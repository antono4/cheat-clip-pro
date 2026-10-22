"""
Detached background restart runner for Cheat Clip PRO.

Spawned as an independent process so it can outlive the dev servers it replaces:
it waits for the HTTP response to be delivered, frees ports 8000 and 5173, then
(optionally) pulls the latest code and syncs dependencies, and finally relaunches
``npm run dev`` in a fresh console.

The git pull deliberately lives here instead of in the API handler. The dev
backend runs uvicorn with ``--reload`` watching ``backend/``, so pulling inside a
request rewrote watched files and tore the worker down while the response was
still in flight, which the browser surfaced as "Failed to fetch".
"""

import os
import sys
import time
import shutil
import logging
import argparse
import subprocess
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[restart_runner] %(message)s")
logger = logging.getLogger("restart_runner")

DEFAULT_PORTS = (8000, 5173)


def _pids_listening_on(port: int):
    """Linux fallback: find PIDs owning a listening TCP port via /proc."""
    inodes = set()
    for proc_file in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(proc_file) as fh:
                next(fh, None)
                for line in fh:
                    fields = line.split()
                    if len(fields) > 9 and fields[1].endswith(f":{port:04X}"):
                        inodes.add(fields[9])
        except OSError:
            continue
    if not inodes:
        return set()

    pids = set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        fd_dir = f"/proc/{entry}/fd"
        try:
            for fd in os.listdir(fd_dir):
                try:
                    target = os.readlink(os.path.join(fd_dir, fd))
                except OSError:
                    continue
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    pids.add(int(entry))
                    break
        except OSError:
            continue
    return pids


def free_ports_windows(ports=DEFAULT_PORTS):
    """Frees the given ports on Windows using PowerShell, then netstat/taskkill."""
    current_pid = os.getpid()

    ps_cmd = (
        f"$ports = @({','.join(str(p) for p in ports)}); "
        f"foreach ($p in $ports) {{ "
        f"  try {{ "
        f"    $conns = Get-NetTCPConnection -LocalPort $p -ErrorAction Stop; "
        f"    $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique; "
        f"    foreach ($procId in $pids) {{ "
        f"      if ($procId -ne {current_pid} -and $procId -gt 4) {{ "
        f"        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue "
        f"      }} "
        f"    }} "
        f"  }} catch {{}} "
        f"}}"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.warning(f"PowerShell port freeing encountered error: {e}")

    try:
        out = subprocess.check_output(["netstat", "-ano", "-p", "tcp"], text=True,
                                      encoding="utf-8", errors="replace", timeout=5)
        for line in out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 5 and parts[0].upper() == "TCP":
                local_addr = parts[1]
                pid_str = parts[-1]
                if pid_str.isdigit():
                    pid = int(pid_str)
                    for p in ports:
                        if local_addr.endswith(f":{p}") and pid != current_pid and pid > 4:
                            # /T also kills the reloader's child worker tree.
                            try:
                                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                               shell=False, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL)
                            except Exception:
                                pass
    except Exception:
        pass


def free_ports_unix(ports=DEFAULT_PORTS):
    """Frees the given ports on Unix/macOS via lsof, fuser, or a /proc scan."""
    for p in ports:
        p_int = int(p)
        pids = set()

        # lsof is the most precise, but is missing on minimal Linux images.
        if shutil.which("lsof"):
            try:
                out = subprocess.check_output(["lsof", "-ti", f":{p_int}"], text=True,
                                              encoding="utf-8", errors="replace", timeout=5)
                pids.update(int(x) for x in out.split() if x.isdigit())
            except Exception:
                pass

        # fuser ships with psmisc and is present far more often than lsof.
        if not pids and shutil.which("fuser"):
            try:
                subprocess.run(["fuser", "-k", "-n", "tcp", str(p_int)], timeout=5,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

        # Last resort: read the socket inode owner straight from /proc.
        if not pids and sys.platform.startswith("linux"):
            pids.update(_pids_listening_on(p_int))

        for pid in pids:
            if pid > 1 and pid != os.getpid():
                try:
                    subprocess.run(["kill", "-9", str(pid)], shell=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass


def run_git(args, cwd, timeout=60):
    """Runs a git command, returning (returncode, stdout, stderr)."""
    git_bin = shutil.which("git") or "git"
    try:
        proc = subprocess.run([git_bin] + args, cwd=str(cwd),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def _has_tracked_changes(root_dir) -> bool:
    rc, out, _ = run_git(["status", "--porcelain"], root_dir)
    if rc != 0 or not out:
        return False
    return any(not line.startswith("??") for line in out.splitlines())


def update_repository(root_dir, branch):
    """Fetches and fast-forwards the working tree.

    Returns ``(ok, message, changed_files)``. Local (tracked) edits are stashed
    and reapplied so a user's in-progress work is not lost.
    """
    root_dir = Path(root_dir)
    rc, old_head, _ = run_git(["rev-parse", "HEAD"], root_dir)
    old_head = old_head or "HEAD"

    rc_fetch, _, err_fetch = run_git(["fetch", "origin", branch], root_dir, timeout=25)
    if rc_fetch != 0:
        return False, f"Failed to fetch updates: {err_fetch or 'network or remote error'}", []

    stashed = False
    if _has_tracked_changes(root_dir):
        logger.info("Local changes detected. Stashing before update...")
        rc_stash, _, _ = run_git(["stash", "save", "Auto-stash before Cheat Clip PRO update"], root_dir)
        stashed = rc_stash == 0

    rc_pull, pull_out, err_pull = run_git(["pull", "origin", branch], root_dir, timeout=60)
    if rc_pull != 0:
        if stashed:
            run_git(["stash", "pop"], root_dir)
        return False, f"Git pull failed: {err_pull or pull_out}", []

    if stashed:
        logger.info("Reapplying stashed local changes...")
        run_git(["stash", "pop"], root_dir)

    rc_diff, diff_files, _ = run_git(["diff", f"{old_head}..HEAD", "--name-only"], root_dir)
    changed_files = diff_files.splitlines() if rc_diff == 0 and diff_files else []
    return True, "Update pulled successfully.", changed_files


def sync_dependencies(root_dir, changed_files):
    """Installs Node/Python dependencies when the manifests changed."""
    root_dir = Path(root_dir)
    updated = []

    if "package.json" in changed_files:
        logger.info("package.json changed. Running npm install...")
        try:
            npm_bin = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
            subprocess.run([npm_bin, "install"], shell=False, cwd=str(root_dir), timeout=180)
            updated.append("Node modules (npm install)")
        except Exception as e:
            logger.warning(f"npm install warning: {e}")

    if any("requirements.txt" in f for f in changed_files):
        logger.info("requirements.txt changed. Running pip install...")
        try:
            req_file = root_dir / "backend" / "requirements.txt"
            if not req_file.exists():
                req_file = root_dir / "requirements.txt"
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                           shell=False, cwd=str(root_dir), timeout=300)
            updated.append("Python dependencies (pip install)")
        except Exception as e:
            logger.warning(f"pip install warning: {e}")

    return updated


def launch_dev_server(root_dir):
    """Relaunches ``npm run dev`` detached from this runner."""
    if os.name == "nt":
        npm_bin = shutil.which("npm.cmd") or shutil.which("npm") or "npm.cmd"
        subprocess.Popen(["cmd.exe", "/c", f'"{npm_bin}" run dev'], cwd=str(root_dir),
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        subprocess.Popen(["npm", "run", "dev"], cwd=str(root_dir), start_new_session=True)


def main():
    parser = argparse.ArgumentParser(description="Cheat Clip PRO Background Restart Runner")
    parser.add_argument("--delay", type=float, default=2.5, help="Seconds to wait before freeing ports")
    parser.add_argument("--cwd", type=str, default="", help="Workspace root directory")
    parser.add_argument("--update", action="store_true",
                        help="Pull the latest code and sync dependencies before relaunching")
    parser.add_argument("--branch", type=str, default="master", help="Branch to pull when --update is set")
    args = parser.parse_args()

    root_dir = Path(args.cwd).resolve() if args.cwd else Path(__file__).resolve().parent.parent
    logger.info(f"Restart sequence initialized for: {root_dir}")
    logger.info(f"Waiting {args.delay}s to allow client response to complete...")
    time.sleep(args.delay)

    logger.info("Freeing ports 8000 and 5173...")
    if os.name == "nt":
        free_ports_windows(DEFAULT_PORTS)
    else:
        free_ports_unix(DEFAULT_PORTS)

    time.sleep(1.0)

    if args.update:
        logger.info(f"Updating from origin/{args.branch}...")
        ok, message, changed_files = update_repository(root_dir, args.branch)
        if ok:
            logger.info(message)
            updated = sync_dependencies(root_dir, changed_files)
            if updated:
                logger.info("Synced dependencies: " + ", ".join(updated))
        else:
            logger.error(message)

    logger.info("Launching 'npm run dev'...")
    try:
        launch_dev_server(root_dir)
        logger.info("New server process launched successfully.")
    except Exception as e:
        logger.error(f"Failed to launch npm run dev: {e}")


if __name__ == "__main__":
    main()
