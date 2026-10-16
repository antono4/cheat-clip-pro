"""Endpoints for the video <-> prompt workflow.

Two capabilities:

* ``POST /api/generate-prompt`` — analyse a source video (YouTube, Google Drive
  or an upload), extract its transcript, and turn it into a reusable
  text-to-video generation prompt.
* ``POST /api/generate-video`` + ``GET /api/generate-video-status/{job_id}`` —
  feed a prompt to Google Veo and poll the long-running job to completion.
"""

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from backend.config import (
    EXPORTS_DIR,
    TEMP_DIR,
    UPLOADS_DIR,
    get_video_file_metadata,
    logger,
    transcribe_local_video_file,
)
from backend.routers.media import _find_video_file_on_disk
from backend.schemas.video_gen import (
    GenerateVideoRequest,
    PromptBatchStatusResponse,
    PromptFromVideoBatchRequest,
    PromptFromVideoRequest,
    PromptFromVideoResponse,
    ShotListItem,
    VideoGenerationStatusResponse,
)
from backend.services.ai_service import (
    generate_video_prompt,
    get_kimi_api_key,
    is_kimi_model,
)
from backend.services.gdrive_service import (
    download_google_drive_video,
    is_google_drive_url,
)
from backend.services.prompt_batch_service import (
    PROMPT_BATCHES,
    apply_prompt_error,
    apply_prompt_result,
    create_prompt_batch,
    summarise_prompt_batch,
)
from backend.services.video_gen_service import (
    DEFAULT_VEO_MODEL,
    KNOWN_VEO_MODELS,
    create_generation_job,
    list_available_veo_models,
    run_video_generation_job,
    validate_generation_request,
    video_generation_jobs,
)
from backend.services.youtube_service import fetch_transcript, fetch_video_metadata
from backend.utils.media_paths import is_safe_path as _is_safe_path
from backend.utils.text import extract_video_id, parse_manual_subtitles

router = APIRouter(tags=["Video Generation"])

MAX_TRANSCRIPT_CHARS = 60000


def _resolve_request_key(api_key: str, model: str) -> str:
    """Picks the key for the request, honouring the provider the model implies."""
    explicit = (api_key or "").strip()
    if is_kimi_model(model):
        return get_kimi_api_key(explicit)
    return explicit or (os.environ.get("GEMINI_API_KEY") or "").strip()


def _transcript_to_text(lines: list) -> str:
    """Flattens timestamped transcript lines into plain text for the model."""
    parts = []
    for line in lines or []:
        text = (line.get("text") or "").strip()
        if not text:
            continue
        start = line.get("start")
        if isinstance(start, (int, float)):
            minutes, seconds = divmod(int(start), 60)
            parts.append(f"[{minutes:02d}:{seconds:02d}] {text}")
        else:
            parts.append(text)
    return "\n".join(parts)


async def _load_source_transcript(request: PromptFromVideoRequest) -> dict:
    """Fetches metadata and a transcript for the requested source.

    Returns a dict with ``video_id``, ``title``, ``channel``, ``duration`` and
    ``transcript`` (plain text). Raises ``HTTPException`` on failure.
    """
    source = (request.url or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="A video URL is required.")

    is_gdrive = is_google_drive_url(source)
    uploaded: Optional[Path] = None

    if is_gdrive:
        uploaded = await asyncio.to_thread(download_google_drive_video, source)
    elif source.startswith("upload_") or source.startswith("/api/video/") or source.startswith("file://"):
        candidate = os.path.basename(source.split("?")[0])
        if source.startswith("file://"):
            candidate = os.path.basename(source[7:])
        uploaded = _find_video_file_on_disk(candidate)
        if not uploaded and os.path.exists(source):
            abs_candidate = Path(source)
            if _is_safe_path(abs_candidate):
                uploaded = abs_candidate
        if not uploaded:
            raise HTTPException(status_code=404, detail="Uploaded video file not found on disk. Please upload again.")
    else:
        # A bare filename that exists in a managed directory is an upload too.
        candidate = os.path.basename(source.split("?")[0])
        for base in (UPLOADS_DIR, TEMP_DIR):
            if (base / candidate).exists():
                uploaded = base / candidate
                break

    if uploaded is not None:
        metadata = await asyncio.to_thread(get_video_file_metadata, uploaded)
        duration = float(metadata.get("duration") or 0.0)
        title = metadata.get("title") or uploaded.stem
        channel = "Google Drive" if is_gdrive else "Local Upload"
        video_id = uploaded.stem

        if request.subtitles:
            lines = parse_manual_subtitles(request.subtitles, duration)
        else:
            lines = await asyncio.to_thread(transcribe_local_video_file, uploaded, None)
        if not lines:
            raise HTTPException(
                status_code=400,
                detail="No spoken words were detected in this video. Upload subtitles or use a video with clear speech.",
            )
        return {
            "video_id": video_id,
            "title": title,
            "channel": channel,
            "duration": duration,
            "transcript": _transcript_to_text(lines),
        }

    # ── YouTube path ────────────────────────────────────────────────────────
    video_id = extract_video_id(source)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL. Please check and try again.")
    canonical_url = f"https://www.youtube.com/watch?v={video_id}"

    metadata = await asyncio.to_thread(fetch_video_metadata, canonical_url, request.proxy, request.cookies)
    title = metadata.get("title") or ""
    channel = metadata.get("channel") or ""
    duration = float(metadata.get("duration") or 0.0)

    if request.subtitles:
        lines = parse_manual_subtitles(request.subtitles, duration)
    else:
        lines = await asyncio.to_thread(fetch_transcript, video_id, request.proxy, None, request.cookies)
    if not lines:
        raise HTTPException(
            status_code=400,
            detail="Could not retrieve a transcript for this video. Upload subtitles or try another video.",
        )
    return {
        "video_id": video_id,
        "title": title,
        "channel": channel,
        "duration": duration,
        "transcript": _transcript_to_text(lines),
    }


@router.post("/api/generate-prompt", response_model=PromptFromVideoResponse)
async def generate_prompt_from_video(request: PromptFromVideoRequest):
    """Extracts a text-to-video prompt from a source video."""
    model = (request.model or "").strip()
    api_key = _resolve_request_key(request.api_key or "", model)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="An API key is required. Enter a Gemini or Moonshot/Kimi key first.",
        )
    if api_key.lower() == "mock":
        raise HTTPException(
            status_code=400,
            detail="Prompt extraction needs a real API key; the 'mock' key is not supported here.",
        )

    return await _extract_prompt(api_key, model, request)


async def _extract_prompt(
    api_key: str,
    model: str,
    request: PromptFromVideoRequest,
) -> PromptFromVideoResponse:
    """Shared extraction path used by the single and batch endpoints.

    Keeping this in one place means the batch endpoint inherits every guard the
    single endpoint has (transcript handling, empty-prompt detection, shot list
    normalisation) instead of duplicating them.
    """
    source = await _load_source_transcript(request)
    transcript_text = source["transcript"][:MAX_TRANSCRIPT_CHARS]

    try:
        result = await asyncio.to_thread(
            generate_video_prompt,
            api_key=api_key,
            transcript_text=transcript_text,
            model=model,
            title=source["title"],
            channel=source["channel"],
            duration=source["duration"],
            style_hint=(request.style_hint or "").strip(),
            extra_instructions=(request.extra_instructions or "").strip(),
        )
    except Exception as e:
        logger.error(f"Prompt generation failed for {source['video_id']}: {e}")
        raise HTTPException(status_code=502, detail=f"Could not generate a prompt: {e}")

    data = result.parsed or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(
            status_code=502,
            detail="The model did not return a usable prompt. Try again or pick a different model.",
        )

    shot_list = []
    for item in data.get("shot_list") or []:
        if isinstance(item, dict):
            shot_list.append(ShotListItem(
                time=str(item.get("time") or ""),
                description=str(item.get("description") or ""),
            ))

    return PromptFromVideoResponse(
        video_id=source["video_id"],
        title=source["title"],
        channel=source["channel"],
        duration=source["duration"],
        model=result.model,
        prompt=prompt,
        summary=data.get("summary"),
        style=data.get("style"),
        subject=data.get("subject"),
        action=data.get("action"),
        setting=data.get("setting"),
        camera=data.get("camera"),
        lighting=data.get("lighting"),
        audio=data.get("audio"),
        negative_prompt=data.get("negative_prompt"),
        shot_list=shot_list,
        transcript=source["transcript"],
    )


async def _run_prompt_batch(
    batch_id: str,
    api_key: str,
    model: str,
    request: PromptFromVideoBatchRequest,
) -> None:
    """Processes each batch item sequentially, isolating per-item failures.

    Sequential rather than concurrent on purpose: every item spends most of its
    time waiting on a transcript fetch and a provider rate limit, and hammering
    the provider in parallel is a reliable way to get throttled.
    """
    batch = PROMPT_BATCHES.get(batch_id)
    if batch is None:
        return

    for item in batch["items"]:
        item["status"] = "processing"
        summarise_prompt_batch(batch)
        PROMPT_BATCHES[batch_id] = batch

        single = PromptFromVideoRequest(
            url=item["url"],
            api_key=api_key,
            model=model,
            style_hint=request.style_hint,
            extra_instructions=request.extra_instructions,
            proxy=request.proxy,
            cookies=request.cookies,
            subtitles=request.subtitles,
            subtitles_filename=request.subtitles_filename,
        )
        try:
            result = await _extract_prompt(api_key, model, single)
            apply_prompt_result(item, result.model_dump())
        except HTTPException as e:
            apply_prompt_error(item, str(e.detail))
        except Exception as e:
            logger.error(f"Prompt batch item failed for {item['url']}: {e}")
            apply_prompt_error(item, f"Could not generate a prompt: {e}")

        summarise_prompt_batch(batch)
        PROMPT_BATCHES[batch_id] = batch


@router.post("/api/generate-prompts-batch")
async def generate_prompts_batch(
    request: PromptFromVideoBatchRequest,
    background_tasks: BackgroundTasks,
):
    """Starts a background batch that extracts one prompt per source video."""
    urls = [u.strip() for u in (request.urls or []) if (u or "").strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="At least one video URL is required.")

    model = (request.model or "").strip()
    api_key = _resolve_request_key(request.api_key or "", model)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="An API key is required. Enter a Gemini or Moonshot/Kimi key first.",
        )
    if api_key.lower() == "mock":
        raise HTTPException(
            status_code=400,
            detail="Prompt extraction needs a real API key; the 'mock' key is not supported here.",
        )

    batch_id = f"promptbatch_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    PROMPT_BATCHES[batch_id] = create_prompt_batch(batch_id, urls)

    background_tasks.add_task(_run_prompt_batch, batch_id, api_key, model, request)
    return {"batch_id": batch_id, "total": len(urls)}


@router.get("/api/generate-prompts-batch-status/{batch_id}", response_model=PromptBatchStatusResponse)
async def get_prompt_batch_status(batch_id: str):
    """Reports per-item progress for a prompt extraction batch."""
    batch = PROMPT_BATCHES.get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Prompt batch not found or expired.")
    return PromptBatchStatusResponse(**batch)


@router.get("/api/video-models")
def list_video_models():
    """Lists the models available for prompt-based video generation."""
    return {"provider": "veo", "models": list_available_veo_models()}


@router.post("/api/generate-video")
async def generate_video(request: GenerateVideoRequest, background_tasks: BackgroundTasks):
    """Starts a Veo text-to-video job and returns its id immediately."""
    model = (request.model or "").strip() or DEFAULT_VEO_MODEL
    if model not in KNOWN_VEO_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown Veo model '{model}'. Available: {', '.join(KNOWN_VEO_MODELS)}.",
        )

    explicit_key = (request.api_key or "").strip()
    if explicit_key and explicit_key.startswith("sk-"):
        raise HTTPException(
            status_code=400,
            detail="Video generation requires a Google Gemini key. Moonshot/Kimi keys can write prompts but cannot render video.",
        )
    api_key = explicit_key or (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key or api_key.lower() == "mock":
        raise HTTPException(
            status_code=400,
            detail="A Google Gemini API key is required to generate video with Veo.",
        )

    validation_error = validate_generation_request(
        prompt=request.prompt or "",
        aspect_ratio=request.aspect_ratio or "16:9",
        resolution=request.resolution or "720p",
        duration_seconds=request.duration_seconds or 8,
        number_of_videos=request.number_of_videos or 1,
    )
    if validation_error:
        raise HTTPException(status_code=400, detail=validation_error)

    job = create_generation_job(
        prompt=request.prompt.strip(),
        model=model,
        aspect_ratio=request.aspect_ratio or "16:9",
        resolution=request.resolution or "720p",
        duration_seconds=request.duration_seconds or 8,
        number_of_videos=request.number_of_videos or 1,
    )

    background_tasks.add_task(
        run_video_generation_job,
        job["job_id"],
        api_key,
        job["prompt"],
        model,
        job["aspect_ratio"],
        job["resolution"],
        job["duration_seconds"],
        job["number_of_videos"],
        request.negative_prompt or "",
    )
    return {"job_id": job["job_id"], "status": job["status"]}


@router.get("/api/generate-video-status/{job_id}", response_model=VideoGenerationStatusResponse)
async def get_generation_status(job_id: str):
    """Reports the progress of a video generation job."""
    job = video_generation_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found or expired.")
    return VideoGenerationStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        stage=job.get("stage"),
        progress=job.get("progress"),
        elapsed=job.get("elapsed"),
        model=job.get("model"),
        videos=job.get("videos") or [],
        error=job.get("error"),
    )


@router.get("/api/download-generated/{file_name}")
def download_generated_video(file_name: str):
    """Serves a generated video for download."""
    safe_name = os.path.basename(file_name)
    if not safe_name.startswith("generated_") or not safe_name.endswith(".mp4"):
        raise HTTPException(status_code=400, detail="Invalid file name.")
    path = EXPORTS_DIR / safe_name
    if not path.exists() or not _is_safe_path(path):
        raise HTTPException(status_code=404, detail="Generated video not found.")
    return FileResponse(path, media_type="video/mp4", filename=safe_name)
