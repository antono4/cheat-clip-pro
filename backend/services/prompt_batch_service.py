"""Batch prompt extraction.

``POST /api/generate-prompts-batch`` accepts several source videos and writes a
text-to-video prompt for each. The work is long-running (each video needs a
transcript fetch plus a model call), so the router returns a ``batch_id``
immediately and the items are processed here in the background.

Each item succeeds or fails on its own: one unreachable video must not sink the
rest of the batch. The overall batch is only reported as ``error`` when every
item failed, mirroring how the render batch summarises itself.
"""

from typing import Any, Dict, Iterable, List, Tuple

from backend.utils.registry import TTLCache, register

# Each item costs a transcript fetch plus at least one model call, and they run
# one at a time, so an unbounded list would hold a worker for hours and burn the
# caller's quota. 20 keeps a single batch inside a few minutes.
MAX_BATCH_URLS = 20

PROMPT_BATCHES: TTLCache = register(TTLCache(
    "PROMPT_BATCHES",
    is_terminal=lambda batch: batch.get("status") in ("completed", "error"),
))


def normalise_batch_urls(raw_urls: Iterable[str]) -> Tuple[List[str], List[str]]:
    """Cleans a batch of source URLs and enforces the entry rules.

    Returns ``(urls, skipped)`` where ``skipped`` lists duplicate entries that
    were dropped. Raises ``ValueError`` with a user-facing message when the list
    is empty, too long, or holds something that is not an http(s) URL — the
    router turns those into a 400 rather than failing silently mid-batch.
    """
    urls: List[str] = []
    skipped: List[str] = []
    seen: set = set()

    for candidate in raw_urls or []:
        url = (candidate or "").strip()
        if not url:
            continue
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError(f"Not a valid video URL: {url[:80]}")
        if url in seen:
            skipped.append(url)
            continue
        seen.add(url)
        urls.append(url)

    if not urls:
        raise ValueError("At least one video URL is required.")
    if len(urls) > MAX_BATCH_URLS:
        raise ValueError(
            f"Too many videos in one batch: {len(urls)}. The limit is {MAX_BATCH_URLS}."
        )
    return urls, skipped


def create_prompt_batch(
    batch_id: str, urls: List[str], skipped: List[str] | None = None
) -> Dict[str, Any]:
    """Creates a batch record with one pending item per URL."""
    items = [
        {"url": url, "status": "pending", "shot_list": [], "error": None}
        for url in urls
    ]
    return {
        "batch_id": batch_id,
        "status": "running",
        "total": len(items),
        "completed": 0,
        "failed": 0,
        "skipped": list(skipped or []),
        "items": items,
    }


def summarise_prompt_batch(batch: Dict[str, Any]) -> None:
    """Recomputes the batch counters and overall status from its items.

    Called after each item settles so the status endpoint always reflects the
    items without the caller having to keep counters in sync by hand.
    """
    items = batch.get("items") or []
    completed = sum(1 for item in items if item.get("status") == "completed")
    failed = sum(1 for item in items if item.get("status") == "error")
    running = sum(1 for item in items if item.get("status") in ("pending", "processing"))

    batch["completed"] = completed
    batch["failed"] = failed
    batch["total"] = len(items)

    if running:
        batch["status"] = "running"
    elif items and failed == len(items):
        batch["status"] = "error"
    else:
        batch["status"] = "completed"


def apply_prompt_result(item: Dict[str, Any], data: Dict[str, Any]) -> None:
    """Copies a successful ``/api/generate-prompt`` result onto a batch item."""
    item["status"] = "completed"
    item["error"] = None
    for key in ("video_id", "title", "model", "prompt", "summary", "style",
                "camera", "lighting", "audio", "setting", "negative_prompt"):
        item[key] = data.get(key)
    item["shot_list"] = data.get("shot_list") or []


def apply_prompt_error(item: Dict[str, Any], message: str) -> None:
    """Marks a batch item as failed with a user-facing reason."""
    item["status"] = "error"
    item["error"] = message or "Could not extract a prompt from this video."
    item["prompt"] = None
    item["shot_list"] = []
