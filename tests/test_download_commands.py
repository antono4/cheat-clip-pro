"""Command-construction tests for the yt-dlp download helpers.

These tests put a fake ``yt-dlp`` executable first on ``PATH``. The fake records
its own argv and exits non-zero, so the real code path runs (real subprocess
invocation, real argument building) without touching the network.
"""

import os
import stat

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
