"""Shared, safe resolution of media files on disk.

Both the media router and the download service need to map a client-supplied
file name / video id onto a real file inside the managed media directories.
Keeping that logic in one place avoids the loose substring matching that
previously allowed ambiguous or wrong-file resolution.
"""

import os
import urllib.parse
from pathlib import Path
from typing import List, Optional

from backend.config import EXPORTS_DIR, FONTS_DIR, TEMP_DIR, UPLOADS_DIR

# Directories the application is allowed to read media from.
MEDIA_ROOTS = (UPLOADS_DIR, TEMP_DIR, EXPORTS_DIR)
ALLOWED_ROOTS = (UPLOADS_DIR, TEMP_DIR, EXPORTS_DIR, FONTS_DIR)

# Server-issued identifiers embedded in saved file names.
ID_PREFIXES = ("upload_", "gdrive_")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv"}


def is_safe_path(target_path: Path) -> bool:
    """Ensures the resolved path stays strictly within the allowed media directories."""
    try:
        resolved = Path(target_path).resolve()
        allowed = [root.resolve() for root in ALLOWED_ROOTS]
        return any(resolved == root or resolved.is_relative_to(root) for root in allowed)
    except Exception:
        return False


def _list_files() -> List[Path]:
    files: List[Path] = []
    for directory in MEDIA_ROOTS:
        try:
            if directory.exists():
                files.extend(f for f in directory.iterdir() if f.is_file() and is_safe_path(f))
        except OSError:
            continue
    return files


def _most_recent(matches: List[Path]) -> Optional[Path]:
    if not matches:
        return None
    try:
        return max(matches, key=lambda p: p.stat().st_mtime)
    except OSError:
        return matches[0]


def find_video_file_on_disk(file_name: str) -> Optional[Path]:
    """Resolves a request name to a media file using exact matches and strict ID prefixes."""
    clean_name = urllib.parse.unquote(os.path.basename((file_name or "").split("?")[0])).strip()
    if not clean_name:
        return None

    # 1. Direct path (guarded).
    for base in MEDIA_ROOTS:
        candidate = base / clean_name
        try:
            if candidate.exists() and candidate.is_file() and is_safe_path(candidate):
                return candidate
        except OSError:
            continue

    all_files = _list_files()
    clean_lower = clean_name.lower()

    # 2. Exact case-insensitive filename match.
    for f in all_files:
        if f.name.lower() == clean_lower:
            return f

    # 3. Exact stem match (request without extension).
    stem_lower = os.path.splitext(clean_lower)[0]
    for f in all_files:
        if f.stem.lower() == stem_lower:
            return f

    # 4. Server-issued ID: file is stored as "<id>_<name>" (uploads) or
    #    "<id>.<ext>"/"<id>_<name>" (gdrive). Require a strict boundary so that
    #    e.g. "upload_abcd" never matches "upload_abcdef".
    if clean_lower.startswith(ID_PREFIXES):
        prefix_matches = []
        boundary = len(clean_lower)
        for f in all_files:
            name_lower = f.name.lower()
            if name_lower == clean_lower:
                return f
            if name_lower.startswith(clean_lower) and name_lower[boundary:boundary + 1] in ("_", "."):
                prefix_matches.append(f)
        return _most_recent(prefix_matches)

    return None


def find_local_video_source(v_url: str, video_id: str = "") -> Optional[Path]:
    """Returns a local file to use as the source for a download/trim, or None.

    Matches strictly: exact/prefix on the URL basename, or an exact video id
    prefix. Never falls back to ambiguous substring containment.
    """
    clean_vname = urllib.parse.unquote(os.path.basename((v_url or "").split("?")[0])).strip()

    if clean_vname:
        resolved = find_video_file_on_disk(clean_vname)
        if resolved:
            return resolved

    raw_id = (video_id or "").replace("gdrive_", "").replace("upload_", "").strip()
    if raw_id and len(raw_id) >= 6:
        # Only match files whose name contains the id with a clear boundary
        # (start, or preceded by a non-alphanumeric separator like "_" or ".").
        candidates = []
        for f in _list_files():
            if f.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            name_lower = f.name.lower()
            idx = name_lower.find(raw_id.lower())
            if idx == -1:
                continue
            before = name_lower[idx - 1] if idx > 0 else "_"
            if not before.isalnum():
                candidates.append(f)
        return _most_recent(candidates)

    return None
