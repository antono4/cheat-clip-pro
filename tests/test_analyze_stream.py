import asyncio
import json

import backend.routers.analyze as analyze_module
from backend.schemas.analyze import AnalyzeRequest


async def _collect_events(response):
    events = []
    async for chunk in response.body_iterator:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        for line in text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_mid_stream_exception_is_reported_as_error_event(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("boom mid-stream")

    monkeypatch.setattr(analyze_module, "is_google_drive_url", boom)

    async def scenario():
        response = await analyze_module.analyze_video(
            AnalyzeRequest(url="https://example.com/v.mp4", api_key="mock")
        )
        return await _collect_events(response)

    events = asyncio.run(scenario())

    assert any(e.get("error") and e.get("status") == 500 for e in events), (
        "a mid-stream exception must surface as an SSE error event, not a silent disconnect"
    )


def test_gemini_404_degrades_to_heatmap_clips(monkeypatch):
    """When every Gemini model is unavailable, the app must still return clips."""

    class FakeModels:
        def list(self):
            return []

        def generate_content(self, **_kwargs):
            raise RuntimeError(
                "404 NOT_FOUND. {'error': {'code': 404, 'message': "
                "'models/gemini-2.5-flash is not found for API version v1beta'}}"
            )

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(analyze_module.genai, "Client", FakeClient)

    async def scenario():
        response = await analyze_module.analyze_video(
            AnalyzeRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", api_key="fake-key")
        )
        return await _collect_events(response)

    events = asyncio.run(scenario())

    assert not any(e.get("error") for e in events), "unavailable models must not hard-fail"
    done = [e for e in events if e.get("done")]
    assert done, "expected a done event with heatmap-derived clips"
    assert len(done[0]["result"]["clips"]) > 0
    assert any(e.get("stage") == "Heatmap Fallback Mode" for e in events)
