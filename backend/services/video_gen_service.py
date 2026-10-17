"""Text-to-video generation via Google Veo.

Veo is long-running: ``generate_videos`` returns an operation that must be
polled until ``done``, after which the produced clip is downloaded from a signed
URI. This module wraps that lifecycle in a job registry so the HTTP layer can
return immediately and the frontend can poll for progress.

Only the Gemini/Veo path is implemented. Veo has no OpenAI-compatible
equivalent, so a Kimi key cannot drive generation; the router rejects that
combination with a clear message rather than failing deep inside the SDK.
"""

import time
import uuid
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from backend.config import EXPORTS_DIR, logger
from backend.utils.registry import TTLCache, register

# Veo model ids, most capable first. The `-fast` variants trade some fidelity
# for latency and cost, so they are offered as a lower-latency fallback.
KNOWN_VEO_MODELS = [
    "veo-3.1-generate-preview",
    "veo-3.1-fast-generate-preview",
    "veo-3.0-generate-001",
    "veo-3.0-fast-generate-001",
]

DEFAULT_VEO_MODEL = "veo-3.1-generate-preview"

# Values the API accepts. Anything else is normalised before the call so a bad
# request fails fast with a clear message instead of an opaque SDK error.
ALLOWED_ASPECT_RATIOS = ("16:9", "9:16")
ALLOWED_RESOLUTIONS = ("720p", "1080p", "4k")
ALLOWED_DURATIONS = (4, 6, 8)
MAX_NUMBER_OF_VIDEOS = 4

GENERATION_POLL_INTERVAL_SECONDS = 5.0
# Veo generations routinely take a few minutes; cap well below the frontend's
# patience so a stuck operation surfaces as a timeout rather than hanging.
GENERATION_TIMEOUT_SECONDS = 900.0


def _terminal(job: Any) -> bool:
    """TTL-cache predicate: finished jobs may be reclaimed once they age out."""
    return isinstance(job, dict) and job.get("status") in ("completed", "failed")


video_generation_jobs: TTLCache = register(TTLCache(
    "video_generation_jobs", is_terminal=_terminal
))


def list_available_veo_models() -> List[str]:
    """Veo model ids offered in the UI.

    The SDK exposes no reliable listing for video models, so this returns the
    curated list rather than probing the API.
    """
    return list(KNOWN_VEO_MODELS)


def validate_generation_request(
    prompt: str,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    duration_seconds: int = 8,
    number_of_videos: int = 1,
) -> Optional[str]:
    """Returns an error message for an invalid request, or ``None`` when valid."""
    if not (prompt or "").strip():
        return "A prompt is required to generate a video."
    if len(prompt) > 4000:
        return "The prompt is too long (limit 4000 characters)."
    if aspect_ratio not in ALLOWED_ASPECT_RATIOS:
        return f"Unsupported aspect ratio '{aspect_ratio}'. Choose one of {', '.join(ALLOWED_ASPECT_RATIOS)}."
    if resolution not in ALLOWED_RESOLUTIONS:
        return f"Unsupported resolution '{resolution}'. Choose one of {', '.join(ALLOWED_RESOLUTIONS)}."
    if duration_seconds not in ALLOWED_DURATIONS:
        return (
            f"Unsupported duration '{duration_seconds}s'. Choose one of "
            + ", ".join(f"{d}s" for d in ALLOWED_DURATIONS)
            + "."
        )
    if not 1 <= number_of_videos <= MAX_NUMBER_OF_VIDEOS:
        return f"Number of videos must be between 1 and {MAX_NUMBER_OF_VIDEOS}."
    return None


def _build_config(
    aspect_ratio: str,
    resolution: str,
    duration_seconds: int,
    number_of_videos: int,
    negative_prompt: str = "",
) -> types.GenerateVideosConfig:
    kwargs: Dict[str, Any] = {
        "aspect_ratio": aspect_ratio,
        "number_of_videos": number_of_videos,
        "duration_seconds": duration_seconds,
    }
    # Older Veo revisions reject `resolution`; only send it when the caller
    # explicitly asked for something other than the default.
    if resolution != "720p":
        kwargs["resolution"] = resolution
    if (negative_prompt or "").strip():
        kwargs["negative_prompt"] = negative_prompt.strip()
    # `generate_audio` is deliberately omitted: the Gemini Developer API (the
    # mode an ordinary API key runs in) rejects it with a 400, whether it is set
    # to True or False. Audio comes back anyway when the model supports it.
    return types.GenerateVideosConfig(**kwargs)


def _download_generated_video(client: genai.Client, video: Any, out_path: str) -> None:
    """Persists a generated video, preferring inline bytes over the remote URI."""
    inline = getattr(video, "video_bytes", None)
    if inline:
        with open(out_path, "wb") as fh:
            fh.write(inline)
        return

    uri = getattr(video, "uri", None)
    if not uri:
        raise RuntimeError("Veo returned a video with neither inline bytes nor a download URI.")

    client.files.download(file=uri, download_path=out_path)


def _extract_operation_error(operation: Any) -> Optional[str]:
    error = getattr(operation, "error", None)
    if not error:
        return None
    message = getattr(error, "message", None) or str(error)
    code = getattr(error, "code", None)
    return f"{message} (code {code})" if code else message


def run_video_generation_job(
    job_id: str,
    api_key: str,
    prompt: str,
    model: str = DEFAULT_VEO_MODEL,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    duration_seconds: int = 8,
    number_of_videos: int = 1,
    negative_prompt: str = "",
) -> None:
    """Submits a Veo generation and polls it to completion.

    Runs in a background task; progress is published to ``video_generation_jobs``
    under ``job_id`` so ``GET /api/generate-video-status/{job_id}`` can report it.
    """
    job = video_generation_jobs.get(job_id)
    if job is None:
        return

    try:
        job["status"] = "submitting"
        client = genai.Client(api_key=api_key)
        # `source` is the current form; the bare `prompt=` argument is deprecated
        # and slated for removal.
        operation = client.models.generate_videos(
            model=model,
            source=types.GenerateVideosSource(prompt=prompt),
            config=_build_config(
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                duration_seconds=duration_seconds,
                number_of_videos=number_of_videos,
                negative_prompt=negative_prompt,
            ),
        )
        job["operation_name"] = getattr(operation, "name", None)
        job["status"] = "generating"
        job["stage"] = "Generation submitted — waiting for the model"

        started = time.time()
        while not getattr(operation, "done", False):
            elapsed = time.time() - started
            if elapsed >= GENERATION_TIMEOUT_SECONDS:
                raise TimeoutError(
                    f"Video generation exceeded {int(GENERATION_TIMEOUT_SECONDS)}s without completing."
                )
            time.sleep(GENERATION_POLL_INTERVAL_SECONDS)
            operation = client.operations.get(operation)
            job["elapsed"] = int(time.time() - started)
            job["stage"] = f"Rendering video ({job['elapsed']}s)"

        error_message = _extract_operation_error(operation)
        if error_message:
            raise RuntimeError(error_message)

        response = getattr(operation, "response", None) or getattr(operation, "result", None)
        generated = list(getattr(response, "generated_videos", None) or [])
        if not generated:
            filtered = getattr(response, "rai_media_filtered_count", 0)
            reasons = getattr(response, "rai_media_filtered_reasons", None)
            detail = f" ({filtered} filtered: {reasons})" if filtered else ""
            raise RuntimeError(
                "Veo returned no video. The prompt may have been blocked by safety filters." + detail
            )

        job["status"] = "downloading"
        job["stage"] = "Downloading generated video"

        files: List[Dict[str, Any]] = []
        for index, item in enumerate(generated):
            video = getattr(item, "video", None)
            if video is None:
                continue
            filename = f"generated_{job_id}_{index}_{int(time.time())}.mp4"
            out_path = str(EXPORTS_DIR / filename)
            _download_generated_video(client, video, out_path)
            files.append({
                "filename": filename,
                "path": out_path,
                "download_url": f"/api/download-generated/{filename}",
            })

        if not files:
            raise RuntimeError("Veo completed but returned no downloadable video.")

        job["status"] = "completed"
        job["stage"] = "Complete"
        job["progress"] = 100
        job["videos"] = files
        logger.info(f"Video generation job {job_id} produced {len(files)} file(s).")

    except Exception as e:
        logger.error(f"Video generation job {job_id} failed: {e}")
        job["status"] = "failed"
        job["error"] = str(e)
        job["stage"] = "Failed"


def create_generation_job(
    prompt: str,
    model: str = DEFAULT_VEO_MODEL,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    duration_seconds: int = 8,
    number_of_videos: int = 1,
) -> Dict[str, Any]:
    """Registers a pending job and returns it. The caller schedules the work."""
    job_id = str(uuid.uuid4())[:8]
    job: Dict[str, Any] = {
        "job_id": job_id,
        "status": "pending",
        "stage": "Queued",
        "progress": 0,
        "elapsed": 0,
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "duration_seconds": duration_seconds,
        "number_of_videos": number_of_videos,
        "videos": [],
        "error": None,
    }
    video_generation_jobs[job_id] = job
    return job
