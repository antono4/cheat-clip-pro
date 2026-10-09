
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    """Fresh app per test with a clean environment (env vars reloaded per call)."""
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("CHEAT_CLIP_API_KEY", raising=False)
    monkeypatch.delenv("SERVER_MODE", raising=False)
    monkeypatch.delenv("DOOKPLOY", raising=False)
    monkeypatch.delenv("HOST", raising=False)

    import backend.main as main

    return TestClient(main.app)


def test_health_endpoint_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_clear_temp_allowed_for_local_client(client):
    resp = client.post("/api/clear-temp")
    assert resp.status_code == 200


def test_clear_temp_blocked_in_server_mode_without_key(client, monkeypatch):
    monkeypatch.setenv("SERVER_MODE", "1")
    resp = client.post("/api/clear-temp")
    assert resp.status_code == 403


def test_clear_temp_requires_key_when_configured(client, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "super-secret")
    assert client.post("/api/clear-temp").status_code == 401
    assert client.post("/api/clear-temp", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/api/clear-temp", headers={"X-API-Key": "super-secret"}).status_code == 200
