"""Batch prompt extraction.

``POST /api/generate-prompts-batch`` accepts several source videos and writes a
text-to-video prompt for each. The work is long-running (each video needs a
transcript fetch plus a model call), so the router returns a ``batch_id``
immediately and the items are processed here in the background.

Each item succeeds or fails on its own: one unreachable video must not sink the
rest of the batch. The overall batch is only reported as ``error`` when every
item failed, mirroring how the render batch summarises itself.
"""

from typing import Any, Dict, List

from backend.utils.registry import TTLCache, register

PROMPT_BATCHES: TTLCache = register(TTLCache(
    "PROMPT_BATCHES",
    is_terminal=lambda batch: batch.get("status") in ("completed", "error"),
))


def create_prompt_batch(batch_id: str, urls: List[str]) -> Dict[str, Any]:
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
