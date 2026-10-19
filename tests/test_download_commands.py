"""Command-construction tests for the yt-dlp download helpers.

These tests put a fake ``yt-dlp`` executable first on ``PATH``. The fake records
its own argv and exits non-zero, so the real code path runs (real subprocess
invocation, real argument building) without touching the network.

The fake is a POSIX ``#!/bin/sh`` script on POSIX and a ``yt-dlp.cmd`` shim that
forwards to a Python script on Windows, so the same three tests run on both
platforms. The Windows shim also covers the console-shim resolution that
``get_yt_dlp_base_cmd`` has to handle.
"""

import os
import subprocess
import stat
import sys

import pytest

import backend.video_engine as video_engine

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tabc123\n"
)

_FAKE_YTDLP = """#!/bin/sh
{ printf 'ARGS'; for a in "$@"; do printf '\\t%s' "$a"; done; printf '\\n'; } >> "$FAKE_YTDLP_LOG"
exit 1
"""

_FAKE_YTDLP_PY = """import os
import sys

with open(os.environ["FAKE_YTDLP_LOG"], "a", encoding="utf-8") as fh:
    fh.write("ARGS" + "".join("\\t" + a for a in sys.argv[1:]) + "\\n")
sys.exit(1)
"""


def _install_windows_fake(bin_dir, tmp_path):
    """Build a real ``yt-dlp.exe`` launcher, like the one pip installs.

    A ``.cmd`` shim cannot be used here: ``cmd.exe`` re-parses its arguments, so
    the ``|`` separators in ``--progress-template`` never reach yt-dlp. Using a
    genuine executable also exercises the resolution path production relies on.
    """
    scripts = pytest.importorskip("pip._vendor.distlib.scripts")
    src = tmp_path / "_fake_src"
    src.mkdir()
    (src / "yt-dlp").write_text("#!/usr/bin/env python\n" + _FAKE_YTDLP_PY)
    maker = scripts.ScriptMaker(str(src), str(bin_dir))
    maker.executable = sys.executable
    maker.make("yt-dlp")


@pytest.fixture()
def fake_ytdlp(tmp_path, monkeypatch):
    """Installs a recording fake yt-dlp on PATH and returns a call reader."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if os.name == "nt":
        _install_windows_fake(bin_dir, tmp_path)
    else:
        binary = bin_dir / "yt-dlp"
        binary.write_text(_FAKE_YTDLP)
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    log_path = tmp_path / "ytdlp.log"
    monkeypatch.setenv("FAKE_YTDLP_LOG", str(log_path))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    def read_calls():
        if not log_path.exists():
            return []
        calls = []
        for line in log_path.read_text().splitlines():
            if line.startswith("ARGS"):
                calls.append([part for part in line.split("\t")[1:] if part])
        return calls

    return read_calls


def test_clip_segment_uses_cookies_then_falls_back_to_guest(fake_ytdlp):
    with pytest.raises(RuntimeError):
        video_engine.download_clip_segment(URL, 10.0, 20.0, "cookie_probe_clip.mp4", COOKIES)

    calls = fake_ytdlp()
    assert calls, "the downloader must invoke yt-dlp"

    cookie_calls = [c for c in calls if "--cookies" in c]
    guest_calls = [c for c in calls if "--cookies" not in c]

    assert cookie_calls, "supplied cookies must be forwarded to yt-dlp"
    assert guest_calls, "a failed cookies attempt must be retried in guest mode"
    # Cookies are attempted before the cookie-less guest retry.
    assert "--cookies" in calls[0]
    assert "--cookies" not in calls[-1]


def test_clip_segment_without_cookies_never_passes_cookies(fake_ytdlp):
    with pytest.raises(RuntimeError):
        video_engine.download_clip_segment(URL, 10.0, 20.0, "nocookie_probe_clip.mp4", None)

    calls = fake_ytdlp()
    assert calls
    assert not any("--cookies" in c for c in calls)


def test_full_raw_video_uses_cookies_then_falls_back_to_guest(fake_ytdlp, tmp_path):
    with pytest.raises(RuntimeError):
        video_engine.download_full_raw_video(
            URL, str(tmp_path / "raw_out.mp4"), None, COOKIES
        )

    calls = fake_ytdlp()
    assert calls
    assert "--cookies" in calls[0], "supplied cookies must be forwarded to yt-dlp"
    assert any("--cookies" not in c for c in calls), "guest mode retry must run after cookies fail"


def test_base_cmd_resolves_executable_path(monkeypatch):
    """A discovered yt-dlp must be launched via its resolved path.

    On Windows a bare "yt-dlp" only works when the name maps to a real .exe; a
    pip-installed .cmd/.bat shim raises WinError 2 unless the resolved path is
    handed to subprocess. This asserts the resolved path is used verbatim.
    """
    resolved = os.path.join(os.path.dirname(sys.executable), "yt-dlp.exe")

    def fake_which(name):
        return resolved if name == "yt-dlp" else None

    monkeypatch.setattr(video_engine.shutil, "which", fake_which)

    cmd = video_engine.get_yt_dlp_base_cmd(include_cookies=False)

    assert cmd[0] == resolved, "the resolved yt-dlp path must be used as argv[0]"
    assert cmd[1:3] == ["--extractor-args", "youtube:player_client=default,web_embedded,ios"]
    assert "--force-ipv4" in cmd


def test_base_cmd_avoids_shell_shim_that_mangles_arguments(monkeypatch):
    """A .cmd/.bat shim must not be used as argv[0].

    cmd.exe re-parses the arguments of a batch shim, so the ``|`` separators in
    ``--progress-template`` are swallowed and the download silently produces no
    output. The module invocation passes argv through untouched instead.
    """
    shim = os.path.join(os.path.dirname(sys.executable), "yt-dlp.cmd")
    monkeypatch.setattr(
        video_engine.shutil, "which", lambda name: shim if name == "yt-dlp" else None
    )

    cmd = video_engine.get_yt_dlp_base_cmd(include_cookies=False)

    assert cmd[:2] == [sys.executable, "-m"], "a batch shim must fall back to the module"
    assert cmd[2] == "yt_dlp"


def test_base_cmd_falls_back_to_python_module(monkeypatch):
    """Without a yt-dlp executable the module invocation must be used."""
    monkeypatch.setattr(video_engine.shutil, "which", lambda name: None)

    cmd = video_engine.get_yt_dlp_base_cmd(include_cookies=False)

    assert cmd[:3] == [sys.executable, "-m", "yt_dlp"]


_PROXY_ENV = ("PROXY_URL", "WEBSHARE_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
              "WEBSHARE_USERNAME", "WEBSHARE_PASSWORD")


@pytest.fixture()
def no_proxy_env(monkeypatch):
    for key in _PROXY_ENV:
        monkeypatch.delenv(key, raising=False)


def test_base_cmd_omits_proxy_when_none_configured(no_proxy_env):
    cmd = video_engine.get_yt_dlp_base_cmd(include_cookies=False)

    assert "--proxy" not in cmd
    assert video_engine.get_ffmpeg_proxy_args() == []


def test_base_cmd_routes_through_configured_proxy(no_proxy_env, monkeypatch):
    """Downloads must honour PROXY_URL; YouTube blocks datacenter IPs and the
    transcript/metadata paths already proxy, so a 403 here was inconsistent."""
    monkeypatch.setenv("PROXY_URL", "http://user:pass@proxy.example:8080")

    cmd = video_engine.get_yt_dlp_base_cmd(include_cookies=False)

    assert cmd[cmd.index("--proxy") + 1] == "http://user:pass@proxy.example:8080"


def test_ffmpeg_proxy_args_feed_the_direct_url_trim(no_proxy_env, monkeypatch):
    """The ffmpeg fallback fetches googlevideo URLs directly and needs the proxy
    flag too, otherwise it 403s even when yt-dlp succeeded."""
    monkeypatch.setenv("PROXY_URL", "http://user:pass@proxy.example:8080")

    assert video_engine.get_ffmpeg_proxy_args() == [
        "-http_proxy", "http://user:pass@proxy.example:8080"
    ]


def test_webshare_credentials_synthesize_a_rotating_proxy(no_proxy_env, monkeypatch):
    monkeypatch.setenv("WEBSHARE_USERNAME", "acct-rotate")
    monkeypatch.setenv("WEBSHARE_PASSWORD", "secret")

    assert video_engine.get_download_proxy() == (
        "http://acct-rotate:secret@p.webshare.io:80"
    )


def test_js_runtime_prefers_deno_then_node(monkeypatch):
    monkeypatch.setattr(video_engine.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert video_engine.get_js_runtime() == "deno"


def test_js_runtime_falls_back_to_node(monkeypatch):
    monkeypatch.setattr(
        video_engine.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None
    )

    assert video_engine.get_js_runtime() == "node"


def test_js_runtime_is_none_when_no_runtime_installed(monkeypatch):
    monkeypatch.setattr(video_engine.shutil, "which", lambda name: None)

    assert video_engine.get_js_runtime() is None


def test_base_cmd_passes_the_detected_js_runtime(no_proxy_env, monkeypatch):
    """yt-dlp needs a runtime to solve YouTube's `n` challenge; without the
    flag it silently omits formats and the download later 403s."""
    monkeypatch.setattr(
        video_engine.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None
    )

    cmd = video_engine.get_yt_dlp_base_cmd(include_cookies=False)

    assert cmd[cmd.index("--js-runtimes") + 1] == "node"


def test_base_cmd_omits_js_runtime_flag_when_none_available(no_proxy_env, monkeypatch):
    monkeypatch.setattr(video_engine.shutil, "which", lambda name: None)

    cmd = video_engine.get_yt_dlp_base_cmd(include_cookies=False)

    assert "--js-runtimes" not in cmd


def test_forbidden_error_is_actionable(no_proxy_env, fake_ytdlp, monkeypatch):
    """A 403 must not surface as the generic 'check your internet' message."""
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, "", "[https @ 0x1] HTTP error 403 Forbidden"
        )

    monkeypatch.setattr(video_engine.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as excinfo:
        video_engine.download_clip_segment(URL, 10.0, 20.0, "forbidden_probe_clip.mp4", None)

    message = str(excinfo.value)
    assert "403" in message
    assert "PROXY_URL" in message
    # yt-dlp-ejs ships with requirements.txt, so the actionable lever is a
    # JavaScript runtime; the old wording told users to reinstall the package.
    assert "Node.js" in message
    assert "Deno" in message
