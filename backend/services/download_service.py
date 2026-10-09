import asyncio
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

from backend.config import (
    ACTIVE_ENCODER_ARGS,
    ACTIVE_ENCODER_NAME,
    EXPORTS_DIR,
    download_clip_segment,
    download_full_raw_video,
    is_valid_mp4,
    logger,
)
from backend.utils.registry import TTLCache, register


def _job_finished(job: Any) -> bool:
    """Download jobs are terminal once ready or failed (safe to evict)."""
    try:
        return job.get("status") in ("ready", "failed")
    except Exception:
        return True


raw_download_jobs: Dict[str, dict] = register(
    TTLCache("raw_download_jobs", is_terminal=_job_finished)
)
raw_clip_download_jobs: Dict[str, dict] = register(
    TTLCache("raw_clip_download_jobs", is_terminal=_job_finished)
)


async def run_raw_download_job(
    job_id: str,
    v_url: str,
    out_path: str,
    filename: str,
    download_title: Optional[str] = None,
    cookies_content: Optional[str] = None
):
    def on_progress(p: dict):
        if job_id in raw_download_jobs:
            raw_download_jobs[job_id]["progress_percent"] = p.get("percent", 0.0)
            raw_download_jobs[job_id]["downloaded"] = p.get("downloaded", "")
            raw_download_jobs[job_id]["total"] = p.get("total", "")
            raw_download_jobs[job_id]["speed"] = p.get("speed", "")
            raw_download_jobs[job_id]["eta"] = p.get("eta", "")

    try:
        raw_download_jobs[job_id]["status"] = "downloading"

        # Check if local video exists in UPLOADS_DIR / TEMP_DIR (e.g. Google Drive or Uploaded video)
        from backend.utils.media_paths import find_local_video_source
        local_src_path = find_local_video_source(v_url)
        local_src = str(local_src_path) if local_src_path else None
        if not local_src and v_url and os.path.exists(v_url):
            local_src = os.path.abspath(v_url)

        if local_src and os.path.exists(local_src):
            logger.info(f"Serving local/gdrive video {local_src} directly as full download {out_path}")
            shutil.copy2(local_src, out_path)
            raw_download_jobs[job_id]["status"] = "ready"
            raw_download_jobs[job_id]["progress_percent"] = 100.0
            dl_name = download_title or filename
            raw_download_jobs[job_id]["download_url"] = f"/api/download-rendered/{filename}?title={quote(dl_name)}"
            raw_download_jobs[job_id]["filename"] = f"{dl_name}.mp4" if not dl_name.endswith(".mp4") else dl_name
            return

        await asyncio.to_thread(download_full_raw_video, v_url, out_path, on_progress, cookies_content)
        raw_download_jobs[job_id]["status"] = "ready"
        raw_download_jobs[job_id]["progress_percent"] = 100.0
        dl_name = download_title or filename
        raw_download_jobs[job_id]["download_url"] = f"/api/download-rendered/{filename}?title={quote(dl_name)}"
        raw_download_jobs[job_id]["filename"] = f"{dl_name}.mp4" if not dl_name.endswith(".mp4") else dl_name
    except Exception as e:
        logger.error(f"Raw video download job {job_id} failed: {e}")
        raw_download_jobs[job_id]["status"] = "failed"
        raw_download_jobs[job_id]["error"] = str(e)


async def run_raw_clip_download_job(
    job_id: str,
    v_url: str,
    video_id: str,
    start_time: float,
    end_time: float,
    title: str,
    cookies_content: Optional[str] = None
):
    clean_title = re.sub(r'[\\/*?:"<>|]', "", (title or "clip").strip())
    if not clean_title:
        clean_title = f"clip_{int(start_time)}_{int(end_time)}"
    download_title = f"{clean_title} (raw)"
    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', video_id or "clip")
    seg_filename = f"{safe_id}_clip_{int(start_time)}_{int(end_time)}_{int(time.time())}_raw.mp4"
    out_path = EXPORTS_DIR / seg_filename

    try:
        raw_clip_download_jobs[job_id]["status"] = "downloading"
        raw_clip_download_jobs[job_id]["progress_percent"] = 25.0

        # Optimization: reuse an existing local full raw video as the trim source
        from backend.utils.media_paths import find_local_video_source
        source_video_path = find_local_video_source(v_url, video_id)
        source_video = None
        if source_video_path and source_video_path.name != seg_filename:
            if source_video_path.stat().st_size > 1024 * 1024 and "_clip_" not in source_video_path.name:
                source_video = str(source_video_path)

        duration_sec = max(1.0, end_time - start_time)

        success = False
        if source_video and os.path.exists(source_video):
            try:
                logger.info(f"Trimming local video with {ACTIVE_ENCODER_NAME} for clip {download_title} ({start_time}-{end_time})")
                trim_cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", str(start_time),
                    "-i", source_video,
                    "-t", str(duration_sec),
                    *ACTIVE_ENCODER_ARGS,
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-avoid_negative_ts", "make_zero",
                    "-movflags", "+faststart",
                    str(out_path)
                ]
                trim_timeout = max(180, int(duration_sec * 4) + 60)
                await asyncio.to_thread(subprocess.run, trim_cmd, check=True, timeout=trim_timeout)
                if out_path.exists() and out_path.stat().st_size > 1000:
                    success = True
            except Exception as trim_err:
                logger.warning(f"Hardware trimming failed ({trim_err}), retrying with CPU libx264...")
                try:
                    cpu_trim_cmd = [
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", str(start_time),
                        "-i", source_video,
                        "-t", str(duration_sec),
                        "-c:v", "libx264",
                        "-preset", "veryfast",
                        "-crf", "20",
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-avoid_negative_ts", "make_zero",
                        "-movflags", "+faststart",
                        str(out_path)
                    ]
                    await asyncio.to_thread(subprocess.run, cpu_trim_cmd, check=True, timeout=trim_timeout)
                    if out_path.exists() and out_path.stat().st_size > 1000:
                        success = True
                except Exception as cpu_err:
                    logger.warning(f"CPU trimming also failed ({cpu_err}), falling back to direct stream download...")

        if not success:
            logger.info(f"Downloading clip segment from YouTube for {download_title} ({start_time}-{end_time})")
            temp_name = f"temp_{seg_filename}"
            downloaded_temp = await asyncio.to_thread(
                download_clip_segment,
                v_url,
                start_time,
                end_time,
                temp_name,
                cookies_content
            )
            if downloaded_temp and os.path.exists(downloaded_temp):
                if out_path.exists():
                    try:
                        out_path.unlink()
                    except Exception:
                        pass
                # Fast timestamp and keyframe normalization to eliminate any playback stutter
                fix_cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", downloaded_temp,
                    "-c", "copy",
                    "-avoid_negative_ts", "make_zero",
                    "-movflags", "+faststart",
                    str(out_path)
                ]
                try:
                    await asyncio.to_thread(subprocess.run, fix_cmd, check=True, timeout=45)
                    if out_path.exists() and out_path.stat().st_size > 1000:
                        success = True
                        try:
                            os.unlink(downloaded_temp)
                        except Exception:
                            pass
                except Exception:
                    shutil.move(downloaded_temp, str(out_path))
                    if out_path.exists() and out_path.stat().st_size > 1000:
                        success = True

        if success and out_path.exists() and is_valid_mp4(out_path):
            raw_clip_download_jobs[job_id]["status"] = "ready"
            raw_clip_download_jobs[job_id]["progress_percent"] = 100.0
            raw_clip_download_jobs[job_id]["download_url"] = f"/api/download-rendered/{seg_filename}?title={quote(download_title)}"
            raw_clip_download_jobs[job_id]["filename"] = f"{download_title}.mp4"
            logger.info(f"Raw clip '{download_title}' ready at {out_path}")
        else:
            raise RuntimeError("Generated clip file is missing or invalid.")

    except Exception as e:
        logger.error(f"Raw clip download job {job_id} failed: {e}")
        raw_clip_download_jobs[job_id]["status"] = "failed"
        raw_clip_download_jobs[job_id]["error"] = str(e)
