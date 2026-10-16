"""Tests for the video -> prompt -> video workflow.

Two layers are exercised with real code paths:

* prompt extraction runs the real ``generate_video_prompt`` against a local
  OpenAI-compatible HTTP server (the same contract the Kimi client speaks), so
  the request building, HTTP call and response parsing all really execute;
* video generation runs the real ``run_video_generation_job`` polling loop
  against a fake ``genai.Client`` that mimics the Veo operation lifecycle. The
  Gemini SDK itself is the only thing replaced — it is a third-party client that
  cannot be run without credentials — and the job/registry/validation logic
  under test is untouched.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from backend.services import ai_service, video_gen_service


# ── Fake OpenAI-compatible provider (for prompt extraction) ─────────────────

class _PromptHandler(BaseHTTPRequestHandler):
    server_version = "FakePrompt/1.0"
    requests_seen = []
    reply_content = "{}"

    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
        type(self).requests_seen.append({
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": parsed,
        })
        self._send(200, {"choices": [{"message": {"content": type(self).reply_content}}]})


@pytest.fixture()
def fake_prompt_provider(monkeypatch):
    _PromptHandler.requests_seen = []
    _PromptHandler.reply_content = "{}"

    server = HTTPServer(("127.0.0.1", 0), _PromptHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setenv("MOONSHOT_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test-key-1234567890")

    yield _PromptHandler

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


# ── Prompt generation ───────────────────────────────────────────────────────

def test_generate_video_prompt_returns_structured_prompt(fake_prompt_provider):
    payload = {
        "prompt": "A chef plates a dish in a sunlit kitchen, slow dolly-in, warm tones.",
        "summary": "Chef plating a dish.",
        "style": "cinematic realism",
        "subject": "chef in white apron",
        "action": "plates and garnishes",
        "setting": "sunlit restaurant kitchen",
        "camera": "35mm, slow dolly-in",
        "lighting": "warm window light",
        "audio": "sizzling pans, quiet jazz",
        "negative_prompt": "no text overlays",
        "shot_list": [
            {"time": "0-3s", "description": "Wide of the kitchen"},
            {"time": "3-6s", "description": "Close-up of plating"},
        ],
    }
    fake_prompt_provider.reply_content = json.dumps(payload)

    result = ai_service.generate_video_prompt(
        api_key="sk-test-key-1234567890",
        transcript_text="[00:00] welcome to the kitchen\n[00:03] today we plate a dish",
        model="kimi-k3",
        title="Cooking Show",
        channel="Chef TV",
        duration=120.0,
    )

    assert result.parsed["prompt"].startswith("A chef plates")
    assert len(result.parsed["shot_list"]) == 2

    # The outgoing request must carry the metadata and the json-mode instruction.
    sent = fake_prompt_provider.requests_seen[0]
    assert sent["auth"] == "Bearer sk-test-key-1234567890"
    content = sent["body"]["messages"][0]["content"]
    assert "Cooking Show" in content
    assert "Chef TV" in content
    assert "welcome to the kitchen" in content
    assert "json" in content.lower()
    assert sent["body"]["response_format"] == {"type": "json_object"}


def test_generate_video_prompt_schema_has_required_fields():
    schema = ai_service.build_video_prompt_schema()
    assert "prompt" in schema["required"]
    assert "shot_list" in schema["properties"]


def test_generate_video_prompt_includes_style_and_extra_instructions(fake_prompt_provider):
    fake_prompt_provider.reply_content = json.dumps({"prompt": "p", "summary": "s"})

    ai_service.generate_video_prompt(
        api_key="sk-test-key-1234567890",
        transcript_text="hello",
        model="kimi-k3",
        style_hint="anime",
        extra_instructions="keep it under 10 seconds",
    )

    content = fake_prompt_provider.requests_seen[0]["body"]["messages"][0]["content"]
    assert "anime" in content
    assert "keep it under 10 seconds" in content


# ── Prompt extraction endpoint ──────────────────────────────────────────────

def test_generate_prompt_endpoint_rejects_missing_key():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.post("/api/generate-prompt", json={
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "api_key": "",
    })
    assert resp.status_code == 400
    assert "API key" in resp.json()["detail"]


def test_generate_prompt_endpoint_rejects_mock_key():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.post("/api/generate-prompt", json={
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "api_key": "mock",
    })
    assert resp.status_code == 400
    assert "mock" in resp.json()["detail"].lower()


def test_generate_prompt_endpoint_rejects_invalid_url():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.post("/api/generate-prompt", json={
        "url": "not-a-youtube-url",
        "api_key": "sk-test-key-1234567890",
        "model": "kimi-k3",
    })
    assert resp.status_code == 400
    assert "Invalid YouTube URL" in resp.json()["detail"]


def test_generate_prompt_endpoint_end_to_end(fake_prompt_provider, monkeypatch):
    """YouTube metadata + transcript are fetched, then the prompt is extracted."""
    from fastapi.testclient import TestClient

    import backend.routers.video_gen as video_gen_router
    from backend.main import app

    fake_prompt_provider.reply_content = json.dumps({
        "prompt": "A documentary scene about heatmaps.",
        "summary": "About heatmaps.",
        "shot_list": [{"time": "0-5s", "description": "Presenter at a desk"}],
    })

    monkeypatch.setattr(video_gen_router, "fetch_video_metadata", lambda *_a, **_k: {
        "title": "Retention Explained", "channel": "DataChannel", "duration": 240.0,
    })
    monkeypatch.setattr(video_gen_router, "fetch_transcript", lambda *_a, **_k: [
        {"text": "Heatmaps show where viewers rewatch.", "start": 5.0, "duration": 4.0},
        {"text": "Use them to pick clip moments.", "start": 9.0, "duration": 4.0},
    ])

    client = TestClient(app)
    resp = client.post("/api/generate-prompt", json={
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "api_key": "sk-test-key-1234567890",
        "model": "kimi-k3",
    })

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["prompt"] == "A documentary scene about heatmaps."
    assert data["title"] == "Retention Explained"
    assert data["video_id"] == "dQw4w9WgXcQ"
    assert data["shot_list"][0]["time"] == "0-5s"
    assert "Heatmaps show where viewers rewatch" in data["transcript"]


# ── Prompt extraction batch ─────────────────────────────────────────────────

def _patch_youtube(monkeypatch, transcript_by_id):
    """Routes YouTube metadata/transcript lookups to canned per-video data.

    ``transcript_by_id`` maps a video id to its transcript lines; an unknown id
    yields no transcript, which is how a real blocked fetch behaves.
    """
    import backend.routers.video_gen as video_gen_router

    monkeypatch.setattr(video_gen_router, "fetch_video_metadata", lambda url, *_a, **_k: {
        "title": f"Video {url.rsplit('=', 1)[-1]}", "channel": "DataChannel", "duration": 120.0,
    })
    monkeypatch.setattr(
        video_gen_router, "fetch_transcript",
        lambda video_id, *_a, **_k: transcript_by_id.get(video_id, []),
    )


def _post_batch(client, urls, **extra):
    return client.post("/api/generate-prompts-batch", json={
        "urls": urls,
        "api_key": "sk-test-key-1234567890",
        "model": "kimi-k3",
        **extra,
    })


def test_generate_prompts_batch_endpoint_rejects_missing_key():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.post("/api/generate-prompts-batch", json={
        "urls": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"], "api_key": "",
    })
    assert resp.status_code == 400
    assert "API key" in resp.json()["detail"]


def test_generate_prompts_batch_endpoint_rejects_empty_urls():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = _post_batch(client, ["   ", ""])
    assert resp.status_code == 400
    assert "At least one" in resp.json()["detail"]


def test_generate_prompts_batch_status_404_for_unknown_batch():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.get("/api/generate-prompts-batch-status/nope")
    assert resp.status_code == 404


def test_generate_prompts_batch_isolates_item_failures(fake_prompt_provider, monkeypatch):
    """One unreachable video must not sink the rest of the batch."""
    from fastapi.testclient import TestClient

    from backend.main import app

    fake_prompt_provider.reply_content = json.dumps({
        "prompt": "A documentary scene about heatmaps.",
        "summary": "About heatmaps.",
        "camera": "35mm static",
        "shot_list": [{"time": "0-5s", "description": "Presenter at a desk"}],
    })
    _patch_youtube(monkeypatch, {
        "aaaaaaaaaaa": [{"text": "Heatmaps show rewatching.", "start": 5.0}],
        # bbbbbbbbbbb intentionally absent → no transcript → item fails
        "ccccccccccc": [{"text": "Clips come from the peaks.", "start": 12.0}],
    })

    client = TestClient(app)
    resp = _post_batch(client, [
        "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "https://www.youtube.com/watch?v=bbbbbbbbbbb",
        "https://www.youtube.com/watch?v=ccccccccccc",
    ])
    assert resp.status_code == 200, resp.text
    batch_id = resp.json()["batch_id"]
    assert resp.json()["total"] == 3

    # TestClient runs background tasks before returning, so the batch is done.
    status = client.get(f"/api/generate-prompts-batch-status/{batch_id}")
    assert status.status_code == 200, status.text
    data = status.json()

    assert data["total"] == 3
    assert data["completed"] == 2
    assert data["failed"] == 1
    assert data["status"] == "completed"

    by_url = {item["url"]: item for item in data["items"]}
    ok = by_url["https://www.youtube.com/watch?v=aaaaaaaaaaa"]
    assert ok["status"] == "completed"
    assert ok["prompt"] == "A documentary scene about heatmaps."
    assert ok["title"] == "Video aaaaaaaaaaa"
    assert ok["camera"] == "35mm static"
    assert ok["shot_list"][0]["time"] == "0-5s"

    bad = by_url["https://www.youtube.com/watch?v=bbbbbbbbbbb"]
    assert bad["status"] == "error"
    assert "transcript" in bad["error"].lower()
    assert bad["prompt"] is None


def test_generate_prompts_batch_reports_error_when_all_fail(fake_prompt_provider, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    _patch_youtube(monkeypatch, {})  # no video has a transcript

    client = TestClient(app)
    resp = _post_batch(client, [
        "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "https://www.youtube.com/watch?v=bbbbbbbbbbb",
    ])
    batch_id = resp.json()["batch_id"]

    data = client.get(f"/api/generate-prompts-batch-status/{batch_id}").json()
    assert data["status"] == "error"
    assert data["failed"] == 2
    assert data["completed"] == 0


def test_generate_prompts_batch_applies_style_hint_to_every_item(fake_prompt_provider, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    fake_prompt_provider.reply_content = json.dumps({"prompt": "p", "summary": "s"})
    _patch_youtube(monkeypatch, {
        "aaaaaaaaaaa": [{"text": "one", "start": 1.0}],
        "bbbbbbbbbbb": [{"text": "two", "start": 2.0}],
    })

    client = TestClient(app)
    resp = _post_batch(client, [
        "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "https://www.youtube.com/watch?v=bbbbbbbbbbb",
    ], style_hint="anime", extra_instructions="keep it under 10 seconds")
    batch_id = resp.json()["batch_id"]
    client.get(f"/api/generate-prompts-batch-status/{batch_id}")

    # Two videos → two provider calls, each carrying the shared style/extra.
    assert len(fake_prompt_provider.requests_seen) == 2
    for sent in fake_prompt_provider.requests_seen:
        content = sent["body"]["messages"][0]["content"]
        assert "anime" in content
        assert "keep it under 10 seconds" in content


# ── Prompt batch bookkeeping ────────────────────────────────────────────────

def test_summarise_prompt_batch_stays_running_while_items_pend():
    from backend.services.prompt_batch_service import create_prompt_batch, summarise_prompt_batch

    batch = create_prompt_batch("b1", ["u1", "u2"])
    assert batch["status"] == "running"
    batch["items"][0]["status"] = "completed"
    summarise_prompt_batch(batch)
    assert batch["status"] == "running"  # u2 is still pending
    assert batch["completed"] == 1

    batch["items"][1]["status"] = "error"
    summarise_prompt_batch(batch)
    assert batch["status"] == "completed"  # partial success is not an error
    assert batch["failed"] == 1


# ── Video generation job lifecycle ──────────────────────────────────────────

class _FakeVideo:
    def __init__(self, uri=None, video_bytes=None):
        self.uri = uri
        self.video_bytes = video_bytes


class _FakeOperation:
    def __init__(self, done=False, response=None, error=None, name="ops/1"):
        self.done = done
        self.response = response
        self.result = response
        self.error = error
        self.name = name


class _FakeModels:
    def __init__(self, operations):
        self._operations = list(operations)
        self.calls = []

    def generate_videos(self, **kwargs):
        self.calls.append(kwargs)
        return self._operations.pop(0)


class _FakeOperations:
    def __init__(self, operations):
        self._operations = list(operations)

    def get(self, _operation):
        return self._operations.pop(0)


class _FakeFiles:
    def __init__(self, record):
        self._record = record

    def download(self, file=None, download_path=None):
        self._record.append((file, download_path))
        with open(download_path, "wb") as fh:
            fh.write(b"fake-mp4-bytes")


class _FakeGenaiClient:
    """Stands in for ``genai.Client``; records calls and replays canned ops."""

    def __init__(self, operations, downloads):
        self.models = _FakeModels(operations)
        self.operations = _FakeOperations(operations)
        self.files = _FakeFiles(downloads)


@pytest.fixture()
def fast_polling(monkeypatch):
    """Removes the real 5s poll delay so the lifecycle test stays fast."""
    monkeypatch.setattr(video_gen_service, "GENERATION_POLL_INTERVAL_SECONDS", 0.0)


def _install_fake_client(monkeypatch, operations, downloads):
    client = _FakeGenaiClient(operations, downloads)
    monkeypatch.setattr(video_gen_service.genai, "Client", lambda **_k: client)
    return client


def test_video_generation_job_completes_and_downloads(monkeypatch, fast_polling, tmp_path):
    downloads = []
    response = SimpleNamespace(generated_videos=[SimpleNamespace(video=_FakeVideo(uri="https://x/v.mp4"))])
    operations = [
        _FakeOperation(done=False),
        _FakeOperation(done=True, response=response),
    ]
    client = _install_fake_client(monkeypatch, operations, downloads)

    monkeypatch.setattr(video_gen_service, "EXPORTS_DIR", tmp_path)

    job = video_gen_service.create_generation_job(
        prompt="a cat surfing", model="veo-3.1-generate-preview",
        aspect_ratio="9:16", duration_seconds=6,
    )
    video_gen_service.run_video_generation_job(
        job["job_id"], "AIzaTest", "a cat surfing", "veo-3.1-generate-preview", "9:16", "720p", 6, 1,
    )

    stored = video_gen_service.video_generation_jobs[job["job_id"]]
    assert stored["status"] == "completed", stored.get("error")
    assert len(stored["videos"]) == 1
    assert stored["videos"][0]["filename"].startswith("generated_")
    assert os.path.exists(stored["videos"][0]["path"])

    # The config actually sent must reflect the requested aspect ratio/duration.
    sent_config = client.models.calls[0]["config"]
    assert sent_config.aspect_ratio == "9:16"
    assert sent_config.duration_seconds == 6
    assert client.models.calls[0]["prompt"] == "a cat surfing"
    assert downloads == [("https://x/v.mp4", stored["videos"][0]["path"])]


def test_video_generation_job_prefers_inline_bytes(monkeypatch, fast_polling, tmp_path):
    downloads = []
    response = SimpleNamespace(generated_videos=[SimpleNamespace(video=_FakeVideo(video_bytes=b"inline-bytes"))])
    _install_fake_client(monkeypatch, [_FakeOperation(done=True, response=response)], downloads)
    monkeypatch.setattr(video_gen_service, "EXPORTS_DIR", tmp_path)

    job = video_gen_service.create_generation_job(prompt="x")
    video_gen_service.run_video_generation_job(job["job_id"], "AIzaTest", "x")

    stored = video_gen_service.video_generation_jobs[job["job_id"]]
    assert stored["status"] == "completed"
    with open(stored["videos"][0]["path"], "rb") as fh:
        assert fh.read() == b"inline-bytes"
    # No remote download should have been attempted.
    assert downloads == []


def test_video_generation_job_reports_operation_error(monkeypatch, fast_polling, tmp_path):
    error = SimpleNamespace(message="Safety filter blocked the prompt", code=400)
    _install_fake_client(monkeypatch, [_FakeOperation(done=True, error=error)], [])
    monkeypatch.setattr(video_gen_service, "EXPORTS_DIR", tmp_path)

    job = video_gen_service.create_generation_job(prompt="x")
    video_gen_service.run_video_generation_job(job["job_id"], "AIzaTest", "x")

    stored = video_gen_service.video_generation_jobs[job["job_id"]]
    assert stored["status"] == "failed"
    assert "Safety filter" in stored["error"]


def test_video_generation_job_reports_no_videos(monkeypatch, fast_polling, tmp_path):
    response = SimpleNamespace(generated_videos=[], rai_media_filtered_count=2, rai_media_filtered_reasons="policy")
    _install_fake_client(monkeypatch, [_FakeOperation(done=True, response=response)], [])
    monkeypatch.setattr(video_gen_service, "EXPORTS_DIR", tmp_path)

    job = video_gen_service.create_generation_job(prompt="x")
    video_gen_service.run_video_generation_job(job["job_id"], "AIzaTest", "x")

    stored = video_gen_service.video_generation_jobs[job["job_id"]]
    assert stored["status"] == "failed"
    assert "no video" in stored["error"].lower()


def test_video_generation_job_times_out(monkeypatch, fast_polling, tmp_path):
    monkeypatch.setattr(video_gen_service, "GENERATION_TIMEOUT_SECONDS", 0.0)
    # An operation that never completes.
    _install_fake_client(monkeypatch, [_FakeOperation(done=False), _FakeOperation(done=False)], [])
    monkeypatch.setattr(video_gen_service, "EXPORTS_DIR", tmp_path)

    job = video_gen_service.create_generation_job(prompt="x")
    video_gen_service.run_video_generation_job(job["job_id"], "AIzaTest", "x")

    stored = video_gen_service.video_generation_jobs[job["job_id"]]
    assert stored["status"] == "failed"
    assert "exceeded" in stored["error"]


# ── Generation endpoint guards ──────────────────────────────────────────────

def test_generate_video_endpoint_rejects_kimi_key():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.post("/api/generate-video", json={"prompt": "a cat", "api_key": "sk-test-key"})
    assert resp.status_code == 400
    assert "Gemini key" in resp.json()["detail"]


def test_generate_video_endpoint_rejects_unknown_model():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.post("/api/generate-video", json={
        "prompt": "a cat", "api_key": "AIzaTest", "model": "not-a-veo-model",
    })
    assert resp.status_code == 400
    assert "Unknown Veo model" in resp.json()["detail"]


def test_generate_video_endpoint_rejects_invalid_params():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.post("/api/generate-video", json={
        "prompt": "a cat", "api_key": "AIzaTest", "aspect_ratio": "3:4",
    })
    assert resp.status_code == 400
    assert "aspect ratio" in resp.json()["detail"]


def test_video_models_endpoint_lists_veo():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.get("/api/video-models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "veo"
    assert any(m.startswith("veo-") for m in data["models"])


def test_generate_video_status_404_for_unknown_job():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.get("/api/generate-video-status/doesnotexist")
    assert resp.status_code == 404


def test_generate_video_endpoint_starts_job(monkeypatch):
    from fastapi.testclient import TestClient

    import backend.routers.video_gen as video_gen_router
    from backend.main import app

    started = {}

    def fake_run(job_id, api_key, prompt, model, *args, **kwargs):
        started.update({"job_id": job_id, "prompt": prompt, "model": model})

    monkeypatch.setattr(video_gen_router, "run_video_generation_job", fake_run)

    client = TestClient(app)
    resp = client.post("/api/generate-video", json={
        "prompt": "a robot watering plants",
        "api_key": "AIzaTest",
        "aspect_ratio": "9:16",
        "duration_seconds": 6,
    })

    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    assert started["job_id"] == job_id
    assert started["prompt"] == "a robot watering plants"

    status = client.get(f"/api/generate-video-status/{job_id}")
    assert status.status_code == 200
    assert status.json()["job_id"] == job_id


def test_download_generated_rejects_bad_name():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.get("/api/download-generated/badname.mp4")
    assert resp.status_code == 400
    assert "Invalid file name" in resp.json()["detail"]


def test_download_generated_blocks_path_traversal():
    """A traversal name must never resolve to a file outside the exports dir.

    Encoded slashes do not match the route at all (they fall through to the SPA
    catch-all), so this drives the handler directly to prove the guard itself
    rejects the path rather than relying on routing.
    """
    import pytest as _pytest
    from fastapi import HTTPException

    from backend.routers.video_gen import download_generated_video

    with _pytest.raises(HTTPException) as excinfo:
        download_generated_video("generated_../../../../etc/passwd")
    assert excinfo.value.status_code in (400, 404)


def test_download_generated_serves_file():
    from fastapi.testclient import TestClient

    from backend.config import EXPORTS_DIR
    from backend.main import app

    # is_safe_path validates against fixed managed roots, so the file must live
    # in the real exports directory rather than a patched one.
    target = EXPORTS_DIR / "generated_pytest_0_123.mp4"
    target.write_bytes(b"mp4data")
    try:
        client = TestClient(app)
        resp = client.get("/api/download-generated/generated_pytest_0_123.mp4")
        assert resp.status_code == 200
        assert resp.content == b"mp4data"
    finally:
        target.unlink(missing_ok=True)
