"""Regression tests for the auto-cleanup storage engine.

The cleanup worker must never delete the managed storage roots it later writes
into. It used to rmdir an empty ``temp_clips/uploads`` and, because nothing
recreates it after import time, every subsequent upload returned HTTP 500.

These tests redirect the module's directory constants at a tmp dir so they never
touch the real ``backend/temp_clips`` tree shared with other tests.
"""

import os
import time

import pytest

import backend.config as config
import backend.services.system_service as system_service


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Points the cleanup engine at a throwaway directory tree.

    ``auto_cleanup_expired_files`` imports the directory constants from
    ``backend.config`` at call time and also derives ``_base_dir / "temp"``, so
    all three must be redirected to keep the real tree untouched.
    """
    temp_dir = tmp_path / "temp_clips"
    exports_dir = tmp_path / "exports"
    uploads_dir = temp_dir / "uploads"
    for d in (temp_dir, exports_dir, uploads_dir):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "TEMP_DIR", temp_dir)
    monkeypatch.setattr(config, "EXPORTS_DIR", exports_dir)
    monkeypatch.setattr(config, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(system_service, "_base_dir", str(tmp_path))
    return {"temp": temp_dir, "exports": exports_dir, "uploads": uploads_dir}


def _managed(dirs):
    return [dirs["temp"], dirs["uploads"], dirs["exports"]]


def test_cleanup_preserves_managed_storage_roots(sandbox):
    # Nothing is expired here; the empty-subdir pruning pass still runs.
    system_service.auto_cleanup_expired_files(max_age_seconds=3600, max_storage_bytes=5 * 1024**3)

    for d in _managed(sandbox):
        assert d.exists() and d.is_dir(), f"cleanup must not remove managed dir {d}"


def test_cleanup_removes_expired_files_but_keeps_root(sandbox):
    stale = sandbox["uploads"] / "stale_upload.mp4"
    stale.write_bytes(b"\x00" * 32)
    # Age the file explicitly; fresh mtimes are not reliably older than the
    # cutoff on filesystems with coarse timestamp resolution.
    old = time.time() - 3600
    os.utime(stale, (old, old))

    result = system_service.auto_cleanup_expired_files(max_age_seconds=60, max_storage_bytes=5 * 1024**3)

    assert not stale.exists(), "expired upload should be purged"
    assert result["success"] is True
    assert sandbox["uploads"].exists(), "the uploads dir must survive an emptied cleanup pass"


def test_cleanup_prunes_empty_subdir_but_not_root(sandbox):
    nested = sandbox["uploads"] / "empty_nested_dir"
    nested.mkdir(parents=True, exist_ok=True)

    system_service.auto_cleanup_expired_files(max_age_seconds=3600, max_storage_bytes=5 * 1024**3)

    assert not nested.exists(), "empty non-root subdirs may still be pruned"
    assert sandbox["uploads"].exists(), "the root uploads dir must never be pruned"
