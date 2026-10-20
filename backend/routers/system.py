import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from backend.config import _base_dir, logger
from backend.services.system_service import (
    auto_cleanup_expired_files,
    cleanup_expired_temp_files,
    clear_temp_files,
    get_current_git_info,
    get_temp_storage_summary,
    is_server_environment,
    is_update_allowed,
    run_git_command,
    trigger_detached_restart,
)

router = APIRouter(tags=["System"])


def verify_admin_access(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None)
) -> bool:
    """
    Verifies administrative authorization for destructive system endpoints.

    - If ADMIN_API_KEY / CHEAT_CLIP_API_KEY is configured, the request MUST present a matching token.
    - If no key is configured, access is allowed ONLY for local desktop installs (loopback client,
      not a server/container environment). In a server environment without a configured key,
      access is denied by default to prevent unauthenticated remote update/restart/cleanup.
    """
    admin_key = (os.environ.get("ADMIN_API_KEY") or os.environ.get("CHEAT_CLIP_API_KEY") or "").strip()

    provided = (x_api_key or "").strip()
    if not provided and authorization and authorization.startswith("Bearer "):
        provided = authorization[7:].strip()

    if admin_key:
        if provided != admin_key:
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing administrator API key")
        return True

    # No admin key configured: only permit verified-local, non-server installs.
    if is_server_environment():
        raise HTTPException(
            status_code=403,
            detail="Administrative API key is not configured. Refusing unauthenticated system operations in server mode."
        )

    client_host = (request.client.host if request.client else "") or ""
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(
            status_code=403,
            detail="Administrative operations are restricted to local requests. Configure ADMIN_API_KEY to allow remote access."
        )
    return True


@router.get("/api/temp-storage-info")
async def get_temp_storage_info():
    """Returns total files, bytes, and formatted size of temp download storage."""
    return get_temp_storage_summary()


@router.post("/api/clear-temp")
async def clear_temp_folder(authorized: bool = Depends(verify_admin_access)):
    """
    Clears all temporary downloaded video clips, audio slices, ASS files, and frames
    from TEMP_DIR and backend/temp. Re-creates empty directories.
    PROTECTED: cookies.txt and any cookie files are strictly PRESERVED and NEVER deleted.
    """
    if not is_update_allowed():
        raise HTTPException(
            status_code=403,
            detail="Manual server-side cache clearing is disabled in server mode for safety. Background auto-cleanup manages server storage automatically."
        )
    return clear_temp_files()


@router.post("/api/cleanup-expired-temp")
async def cleanup_expired_temp(max_age_hours: int = 1, authorized: bool = Depends(verify_admin_access)):
    """Deletes temporary frame images and slices older than max_age_hours."""
    return cleanup_expired_temp_files(max_age_hours=max_age_hours)


@router.post("/api/auto-cleanup")
async def api_trigger_auto_cleanup(
    max_age_seconds: int = 3600,
    max_storage_gb: float = 5.0,
    authorized: bool = Depends(verify_admin_access)
):
    """Manually triggers dual-mode auto-cleanup (Option 1: TTL + Option 2: Quota)."""
    return auto_cleanup_expired_files(
        max_age_seconds=max_age_seconds,
        max_storage_bytes=int(max_storage_gb * 1024 * 1024 * 1024)
    )


@router.get("/api/system/version")
def api_system_version():
    """Returns local git version and environment update permission information."""
    return get_current_git_info()


@router.get("/api/system/check-update")
def api_check_update():
    """Fetches origin and checks if updates are available. Disabled in server environments for security."""
    info = get_current_git_info()
    if not is_update_allowed():
        return {
            **info,
            "update_available": False,
            "behind_count": 0,
            "changelog": [],
            "is_server": True,
            "allow_update": False,
            "message": "Self-update is disabled in server / Docker environment for security. Please update through your orchestrator (e.g. Dokploy / Git)."
        }

    root_dir = Path(_base_dir).parent
    branch = info.get("branch", "master") or "master"

    # Fetch origin
    rc_fetch, _, err_fetch = run_git_command(["fetch", "origin", branch], cwd=root_dir, timeout=20)
    if rc_fetch != 0:
        return {
            **info,
            "update_available": False,
            "behind_count": 0,
            "changelog": [],
            "error": f"Failed to fetch updates from remote: {err_fetch or 'Network or remote error'}"
        }

    # Count commits behind
    rc_count, count_str, _ = run_git_command(["rev-list", "--count", "HEAD..FETCH_HEAD"], cwd=root_dir)
    behind_count = int(count_str) if rc_count == 0 and count_str.isdigit() else 0

    # Get changelog of new commits
    changelog = []
    if behind_count > 0:
        rc_log, log_str, _ = run_git_command(["log", "HEAD..FETCH_HEAD", "--pretty=format:%h|%s|%cd", "--date=short"], cwd=root_dir)
        if rc_log == 0 and log_str:
            for line in log_str.splitlines():
                p = line.strip().split("|")
                if len(p) >= 3:
                    changelog.append({"hash": p[0], "message": p[1], "date": p[2]})

    rc_remote_commit, remote_commit, _ = run_git_command(["rev-parse", "--short", "FETCH_HEAD"], cwd=root_dir)

    return {
        **info,
        "update_available": behind_count > 0,
        "behind_count": behind_count,
        "remote_commit": remote_commit if rc_remote_commit == 0 else info["current_commit"],
        "changelog": changelog
    }


@router.post("/api/system/update")
async def api_perform_update(authorized: bool = Depends(verify_admin_access)):
    """Checks for updates, then hands the pull + restart to the detached runner.

    The pull is deliberately NOT done here. The dev backend runs uvicorn with
    ``--reload`` watching ``backend/``; pulling inside the request rewrote those
    files and restarted the worker before the response was flushed, so the
    browser's fetch() rejected with "Failed to fetch". A read-only ``fetch`` is
    safe (it only writes under ``.git/``), so we use it to report connectivity
    errors, then let the runner pull after this response has been delivered.
    """
    if not is_update_allowed():
        raise HTTPException(
            status_code=403,
            detail="System self-update via Web UI is disabled in server / Docker environment for security. Please update through your server orchestrator (e.g. Dokploy / Git pull)."
        )

    root_dir = Path(_base_dir).parent
    info = get_current_git_info()
    branch = info.get("branch", "master") or "master"

    # 1. Fetch only, so a network/remote failure is reported to the user now.
    rc_fetch, _, err_fetch = run_git_command(["fetch", "origin", branch], cwd=root_dir, timeout=25)
    if rc_fetch != 0:
        raise HTTPException(status_code=500, detail=f"Failed to fetch updates: {err_fetch or 'network or remote error'}")

    rc_count, count_str, _ = run_git_command(["rev-list", "--count", "HEAD..FETCH_HEAD"], cwd=root_dir)
    behind_count = int(count_str) if rc_count == 0 and count_str.isdigit() else 0

    if behind_count == 0:
        return {
            "success": True,
            "status": "up-to-date",
            "previous_commit": info["current_commit"],
            "new_commit": info["current_commit"],
            "message": "Cheat Clip PRO is already up to date.",
        }

    # 2. Hand off the pull + dependency sync + restart to a detached process.
    trigger_detached_restart(delay=2.5, update=True, branch=branch)

    return {
        "success": True,
        "status": "restarting",
        "previous_commit": info["current_commit"],
        "behind_count": behind_count,
        "message": "Cheat Clip PRO is updating and restarting in the background...",
    }


@router.post("/api/system/restart")
async def api_restart_app(authorized: bool = Depends(verify_admin_access)):
    """Triggers an immediate background restart without pulling code. Disabled in server environments for security."""
    if not is_update_allowed():
        raise HTTPException(
            status_code=403,
            detail="Server restarts via Web UI are disabled in server / Docker environment for security."
        )
    trigger_detached_restart(delay=2.5)
    return {
        "success": True,
        "status": "restarting",
        "message": "Cheat Clip PRO is restarting..."
    }
