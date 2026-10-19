"""Command-construction tests for the yt-dlp download helpers.

These tests put a fake ``yt-dlp`` executable first on ``PATH``. The fake records
its own argv and exits non-zero, so the real code path runs (real subprocess
invocation, real argument building) without touching the network.

The fake is a POSIX ``#!/bin/sh`` script, which Windows cannot execute; the
three end-to-end shim tests are therefore skipped on Windows. The
executable-resolution behaviour that matters on Windows is covered separately by
``test_base_cmd_resolves_executable_path``.
"""

import os
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

_posix_only = pytest.mark.skipif(
    os.name == "nt",
    reason="the yt-dlp fake is an extensionless #!/bin/sh script, which Windows cannot execute",
)


@pytest.fixture()
def fake_ytdlp(tmp_path, monkeypatch):
    """Installs a recording fake yt-dlp on PATH and returns a call reader."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
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


@_posix_only
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


@_posix_only
def test_clip_segment_without_cookies_never_passes_cookies(fake_ytdlp):
    with pytest.raises(RuntimeError):
        video_engine.download_clip_segment(URL, 10.0, 20.0, "nocookie_probe_clip.mp4", None)

    calls = fake_ytdlp()
    assert calls
    assert not any("--cookies" in c for c in calls)


@_posix_only
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
    resolved = os.path.join(os.path.dirname(sys.executable), "yt-dlp-shim")

    def fake_which(name):
        return resolved if name == "yt-dlp" else None

    monkeypatch.setattr(video_engine.shutil, "which", fake_which)

    cmd = video_engine.get_yt_dlp_base_cmd(include_cookies=False)

    assert cmd[0] == resolved, "the resolved yt-dlp path must be used as argv[0]"
    assert cmd[1:3] == ["--extractor-args", "youtube:player_client=default,web_embedded,ios"]
    assert "--force-ipv4" in cmd


def test_base_cmd_falls_back_to_python_module(monkeypatch):
    """Without a yt-dlp executable the module invocation must be used."""
    monkeypatch.setattr(video_engine.shutil, "which", lambda name: None)

    cmd = video_engine.get_yt_dlp_base_cmd(include_cookies=False)

    assert cmd[:3] == [sys.executable, "-m", "yt_dlp"]
