"""Tests for the Kimi (Moonshot) provider integration.

The Kimi client talks to an OpenAI-compatible HTTP endpoint, so these tests
start a real local HTTP server that speaks that contract and point
``MOONSHOT_BASE_URL`` at it. Nothing about the code under test is mocked: the
real ``requests`` call, the real response parsing, and the real analyzer
helpers all run.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend.services import ai_service


class _OpenAICompatibleHandler(BaseHTTPRequestHandler):
    """Serves canned OpenAI-shaped responses and records received requests."""

    server_version = "FakeMoonshot/1.0"
    requests_seen = []
    scripted = {}

    def log_message(self, *args):  # silence stderr chatter from BaseHTTPRequestHandler
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).requests_seen.append({"method": "GET", "path": self.path})
        if self.path.endswith("/models"):
            self._send(200, {"data": [{"id": "kimi-k3"}, {"id": "kimi-k2.5"}, {"id": "moonshot-embed"}]})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
        type(self).requests_seen.append({
            "method": "POST",
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": parsed,
        })

        status, payload = type(self).scripted.get("POST", (200, {
            "choices": [{"message": {"content": "{}"}}]
        }))
        self._send(status, payload)


@pytest.fixture()
def fake_moonshot(monkeypatch):
    """Starts the fake Kimi endpoint and routes the client at it."""
    _OpenAICompatibleHandler.requests_seen = []
    _OpenAICompatibleHandler.scripted = {}

    server = HTTPServer(("127.0.0.1", 0), _OpenAICompatibleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    monkeypatch.setenv("MOONSHOT_BASE_URL", base_url)
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test-key-1234567890")

    yield _OpenAICompatibleHandler

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


# ── Provider detection ──────────────────────────────────────────────────────

def test_detect_provider_from_model_name():
    assert ai_service.detect_provider(model="kimi-k3") == ai_service.PROVIDER_KIMI
    assert ai_service.detect_provider(model="moonshot/something") == ai_service.PROVIDER_KIMI
    assert ai_service.detect_provider(model="gemini-2.5-flash") == ai_service.PROVIDER_GEMINI


def test_detect_provider_from_key_prefix():
    assert ai_service.detect_provider(api_key="sk-abc123") == ai_service.PROVIDER_KIMI
    assert ai_service.detect_provider(api_key="AIzaSyABC123") == ai_service.PROVIDER_GEMINI


def test_explicit_kimi_model_wins_over_gemini_key():
    """A user who picks a Kimi model must never be silently sent to Gemini."""
    assert ai_service.detect_provider(model="kimi-k3", api_key="AIzaSyABC123") == ai_service.PROVIDER_KIMI


def test_kimi_model_chain_puts_requested_model_first():
    chain = ai_service.build_kimi_model_chain("kimi-k2.7")
    assert chain[0] == "kimi-k2.7"
    assert "kimi-k3" in chain
    assert len(chain) == 4


# ── Chat completion against a live endpoint ─────────────────────────────────

def test_kimi_chat_completion_returns_parsed_json(fake_moonshot):
    clip = {
        "summary": "A test summary #test",
        "clips": [{
            "title": "Great moment",
            "start_time": 10.0,
            "end_time": 40.0,
            "hook_time": 12.0,
            "virality_score": 88,
            "key_quotes": ["wow"],
            "transcript": "wow",
            "title_suggestion": "Alternative",
            "caption_suggestion": "Caption",
            "hashtag_suggestion": "#a #b",
        }],
    }
    fake_moonshot.scripted["POST"] = (200, {"choices": [{"message": {"content": json.dumps(clip)}}]})

    result = ai_service.kimi_chat_completion(
        "sk-test-key-1234567890", "kimi-k3", "analyse this",
        json_schema={"type": "object"},
    )

    assert result.parsed is not None
    assert result.parsed["clips"][0]["virality_score"] == 88
    assert result.model == "kimi-k3"

    # The request must carry the bearer token and OpenAI-shaped body.
    post = [r for r in fake_moonshot.requests_seen if r["method"] == "POST"][0]
    assert post["auth"] == "Bearer sk-test-key-1234567890"
    assert post["path"].endswith("/chat/completions")
    assert post["body"]["model"] == "kimi-k3"
    assert post["body"]["response_format"] == {"type": "json_object"}
    assert "json" in post["body"]["messages"][0]["content"].lower()


def test_kimi_chat_completion_tolerates_markdown_fenced_json(fake_moonshot):
    """A fenced/escaped reply must not crash the client; the lenient parser handles it."""
    fenced = "```json\n{\"summary\": \"ok\", \"clips\": []}\n```"
    fake_moonshot.scripted["POST"] = (200, {"choices": [{"message": {"content": fenced}}]})

    result = ai_service.kimi_chat_completion("sk-test-key-1234567890", "kimi-k3", "analyse")

    assert result.text == fenced
    assert result.parsed is None  # caller falls back to parse_lenient_json_analysis


def test_kimi_chat_completion_reports_auth_failure(fake_moonshot):
    fake_moonshot.scripted["POST"] = (401, {"error": {"message": "invalid api key"}})

    with pytest.raises(RuntimeError) as exc:
        ai_service.kimi_chat_completion("sk-bad-key", "kimi-k3", "analyse")

    message = str(exc.value)
    assert "401" in message
    assert "platform.kimi.ai" in message


def test_kimi_chat_completion_reports_rate_limit(fake_moonshot):
    fake_moonshot.scripted["POST"] = (429, {"error": {"message": "rate limited"}})

    with pytest.raises(RuntimeError) as exc:
        ai_service.kimi_chat_completion("sk-test-key-1234567890", "kimi-k3", "analyse")

    assert "429" in str(exc.value)


def test_kimi_chat_completion_requires_a_key(fake_moonshot, monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="Moonshot/Kimi API key is required"):
        ai_service.kimi_chat_completion("", "kimi-k3", "analyse")


# ── Model listing ───────────────────────────────────────────────────────────

def test_list_available_kimi_models_queries_endpoint(fake_moonshot):
    models = ai_service.list_available_kimi_models("sk-test-key-1234567890")

    assert "kimi-k3" in models
    assert "kimi-k2.5" in models
    # Non-chat ids are filtered out of the UI list.
    assert "moonshot-embed" not in models
    assert any(r["method"] == "GET" and r["path"].endswith("/models") for r in fake_moonshot.requests_seen)


def test_list_available_kimi_models_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)

    models = ai_service.list_available_kimi_models("")
    assert "kimi-k3" in models


# ── /api/models endpoint routing ────────────────────────────────────────────

def test_models_endpoint_returns_kimi_provider(fake_moonshot):
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.get("/api/models", params={"api_key": "sk-test-key-1234567890"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "kimi"
    assert "kimi-k3" in data["models"]


def test_models_endpoint_returns_gemini_provider_by_default():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.get("/api/models", params={"api_key": "AIzaSyDummyKeyForListing"})

    assert resp.status_code == 200
    assert resp.json()["provider"] == "gemini"


# ── End-to-end analyze stream through the Kimi provider ─────────────────────

def test_analyze_stream_uses_kimi_and_normalises_clips(fake_moonshot, monkeypatch):
    """Selecting a Kimi model must route /api/analyze through Kimi, not Gemini."""
    import asyncio

    import backend.routers.analyze as analyze_module
    from backend.schemas.analyze import AnalyzeRequest

    clip_payload = {
        "summary": "Kimi found the viral moments. #clips",
        "clips": [{
            "title": "Heatmaps reveal viral hooks",
            "start_time": 10.0,
            "end_time": 40.0,
            "hook_time": 14.0,
            "virality_score": 91,
            "key_quotes": ["heatmaps reveal it"],
            "transcript": "heatmaps reveal it",
            "title_suggestion": "The Heatmap Secret",
            "caption_suggestion": "Find hooks faster.",
            "hashtag_suggestion": "#viral #clips",
        }],
    }
    fake_moonshot.scripted["POST"] = (200, {"choices": [{"message": {"content": json.dumps(clip_payload)}}]})

    monkeypatch.setattr(analyze_module, "is_google_drive_url", lambda *_a, **_k: False)
    monkeypatch.setattr(analyze_module, "fetch_video_metadata", lambda *_a, **_k: {
        "title": "Test Video", "duration": 200.0, "channel": "Test Channel",
        "video_id": "dQw4w9WgXcQ", "heatmap": [],
    })
    monkeypatch.setattr(analyze_module, "fetch_transcript", lambda *_a, **_k: [
        {"text": "heatmaps reveal viral hooks in the video", "start": 10.0, "duration": 30.0},
    ])

    async def scenario():
        response = await analyze_module.analyze_video(AnalyzeRequest(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            api_key="sk-test-key-1234567890",
            model="kimi-k3",
        ))
        events = []
        async for chunk in response.body_iterator:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            for line in text.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        return events

    events = asyncio.run(scenario())

    assert not any(e.get("error") for e in events), f"unexpected error events: {events}"
    done = [e for e in events if e.get("done")]
    assert done, "expected a done event carrying the analysis result"
    assert done[0]["result"]["clips"][0]["virality_score"] == 91
    assert done[0]["result"]["model"] == "kimi-k3"

    # The Kimi endpoint was actually called (proving Gemini was bypassed).
    assert any(r["method"] == "POST" for r in fake_moonshot.requests_seen)
    post = [r for r in fake_moonshot.requests_seen if r["method"] == "POST"][0]
    assert post["body"]["model"] == "kimi-k3"
