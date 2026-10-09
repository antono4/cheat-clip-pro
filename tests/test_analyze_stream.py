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
