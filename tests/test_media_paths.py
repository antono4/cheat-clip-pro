from pathlib import Path

from backend.config import EXPORTS_DIR, TEMP_DIR, UPLOADS_DIR
from backend.utils.media_paths import (
    find_local_video_source,
    find_video_file_on_disk,
    is_safe_path,
)


def _write(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"\x00" * 2048)
    return path


def test_exact_match_preferred():
    exact = _write(UPLOADS_DIR, "exact-clip.mp4")
    _write(UPLOADS_DIR, "exact-clip-extra.mp4")
    assert find_video_file_on_disk("exact-clip.mp4") == exact
    exact.unlink()


def test_ambiguous_substring_is_rejected():
    _write(UPLOADS_DIR, "data.mp4")
    # "a.mp4" must NOT resolve to "data.mp4" (old loose substring behaviour).
    assert find_video_file_on_disk("a") is None


def test_upload_id_prefix_requires_boundary():
    target = _write(UPLOADS_DIR, "upload_abcd_real.mp4")
    _write(UPLOADS_DIR, "upload_abcdef.mp4")
    assert find_video_file_on_disk("upload_abcd") == target
    target.unlink()


def test_path_traversal_is_blocked():
    assert is_safe_path(UPLOADS_DIR / ".." / ".." / "etc" / "passwd") is False
    assert is_safe_path(UPLOADS_DIR / "ok.mp4") is True


def test_traversal_name_does_not_resolve():
    assert find_video_file_on_disk("../../etc/passwd") is None


def test_find_local_video_source_by_name():
    target = _write(EXPORTS_DIR, "source-video.mp4")
    assert find_local_video_source("http://host/source-video.mp4") == target
    target.unlink()


def test_find_local_video_source_by_id_boundary():
    target = _write(TEMP_DIR, "gdrive_abcdef_original.mp4")
    assert find_local_video_source("", "gdrive_abcdef") == target
    target.unlink()
