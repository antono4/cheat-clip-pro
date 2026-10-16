#!/usr/bin/env python3
"""Extract a text-to-video prompt from a source video via a running backend.

Talks to the Cheat Clip PRO HTTP API, so the backend must already be running
(`npm run dev-backend`, or `uvicorn backend.main:app`). The script has no
third-party dependencies on purpose — it is meant to be runnable straight from
a fresh checkout to sanity-check the endpoint.

Examples:
    python3 video_to_prompt.py --url https://youtu.be/XXXX --api-key AIza...
    python3 video_to_prompt.py --url https://youtu.be/XXXX --api-key sk-... --json
    python3 video_to_prompt.py --batch urls.txt --api-key sk-... --wait
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://localhost:8000"
BATCH_POLL_SECONDS = 3.0


def _post(base_url: str, path: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    return _read(req, timeout)


def _get(base_url: str, path: str, timeout: float) -> dict:
    req = urllib.request.Request(base_url.rstrip("/") + path, method="GET")
    return _read(req, timeout)


def _read(req: urllib.request.Request, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except Exception:
            pass
        raise SystemExit(f"HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Could not reach the backend at {req.full_url}: {e.reason}")


def _common_fields(args: argparse.Namespace) -> dict:
    fields = {
        "api_key": args.api_key,
        "model": args.model or "",
        "style_hint": args.style or None,
        "extra_instructions": args.extra or None,
        "proxy": args.proxy or None,
    }
    if args.cookies:
        with open(args.cookies, encoding="utf-8") as fh:
            fields["cookies"] = fh.read()
    if args.subtitles:
        with open(args.subtitles, encoding="utf-8") as fh:
            fields["subtitles"] = fh.read()
        fields["subtitles_filename"] = args.subtitles
    return fields


def extract_single(args: argparse.Namespace) -> int:
    payload = {"url": args.url, **_common_fields(args)}
    result = _post(args.base_url, "/api/generate-prompt", payload, args.timeout)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if result.get("title"):
        print(f"Source: {result['title']}")
    if result.get("model"):
        print(f"Model:  {result['model']}")
    if result.get("summary"):
        print(f"\nSummary: {result['summary']}")
    print(f"\nPrompt:\n{result.get('prompt', '')}")
    for label, key in (("Style", "style"), ("Camera", "camera"),
                       ("Lighting", "lighting"), ("Audio", "audio"),
                       ("Setting", "setting"), ("Negative", "negative_prompt")):
        if result.get(key):
            print(f"{label}: {result[key]}")
    for shot in result.get("shot_list") or []:
        print(f"  {shot.get('time', ''):>8}  {shot.get('description', '')}")
    return 0


def extract_batch(args: argparse.Namespace) -> int:
    with open(args.batch, encoding="utf-8") as fh:
        urls = [line.strip() for line in fh if line.strip() and not line.startswith("#")]
    if not urls:
        raise SystemExit(f"No URLs found in {args.batch}")

    payload = {"urls": urls, **_common_fields(args)}
    started = _post(args.base_url, "/api/generate-prompts-batch", payload, args.timeout)
    batch_id = started["batch_id"]
    print(f"Batch {batch_id} started with {len(urls)} video(s).", file=sys.stderr)

    if not args.wait:
        print(json.dumps(started, indent=2))
        print(f"Poll: GET /api/generate-prompts-batch-status/{batch_id}", file=sys.stderr)
        return 0

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        status = _get(args.base_url, f"/api/generate-prompts-batch-status/{batch_id}", args.timeout)
        if status.get("status") != "running":
            if args.json:
                print(json.dumps(status, indent=2, ensure_ascii=False))
            else:
                for item in status.get("items", []):
                    print(f"\n=== {item.get('url')} [{item.get('status')}] ===")
                    if item.get("error"):
                        print(f"error: {item['error']}")
                    elif item.get("prompt"):
                        print(item["prompt"])
            return 1 if status.get("status") == "error" else 0
        time.sleep(BATCH_POLL_SECONDS)
    raise SystemExit("Timed out waiting for the batch to finish.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Single source video (YouTube, Google Drive, or an upload reference)")
    source.add_argument("--batch", help="Path to a file with one source URL per line")

    parser.add_argument("--api-key", required=True, help="Gemini (AIza...) or Moonshot/Kimi (sk-...) key")
    parser.add_argument("--model", default="", help="Preferred model; the provider is inferred from it")
    parser.add_argument("--style", default="", help="Visual style to steer the prompt")
    parser.add_argument("--extra", default="", help="Extra instructions for the model")
    parser.add_argument("--proxy", default="", help="Optional proxy URL for YouTube")
    parser.add_argument("--cookies", default="", help="Optional cookies.txt for YouTube")
    parser.add_argument("--subtitles", default="", help="Optional SRT/TXT transcript to use instead of auto")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Backend base URL (default {DEFAULT_BASE_URL})")
    parser.add_argument("--timeout", type=float, default=600.0, help="Per-request timeout in seconds")
    parser.add_argument("--wait", action="store_true", help="For --batch, poll until every item finishes")
    parser.add_argument("--json", action="store_true", help="Print the raw JSON response")

    args = parser.parse_args()
    return extract_batch(args) if args.batch else extract_single(args)


if __name__ == "__main__":
    raise SystemExit(main())
