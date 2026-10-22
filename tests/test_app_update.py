"""Regression tests for the in-app update & restart flow.

The "Update & Restart App" button used to report "Failed to fetch". The update
endpoint ran ``git pull`` *inside* the request handler, and the dev backend runs
uvicorn with ``--reload`` watching ``backend/`` — so the pull rewrote watched
files and the reloader restarted the worker before the response was flushed.

The fix keeps the pull out of the request: the endpoint only does a read-only
``git fetch`` (which writes nothing outside ``.git/``), reports connectivity
errors synchronously, and hands the pull + restart to the detached runner.

These tests pin that contract without touching a real remote: they stub the git
commands and assert the endpoint never performs a mutating ``pull`` itself and
always delegates the work to ``trigger_detached_restart``.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("CHEAT_CLIP_API_KEY", raising=False)
    monkeypatch.delenv("SERVER_MODE", raising=False)
    monkeypatch.delenv("DOOKPLOY", raising=False)
    monkeypatch.delenv("HOST", raising=False)

    import backend.main as main

    return TestClient(main.app)


def _patch_update_env(monkeypatch, git_runner, restart_calls):
    import backend.routers.system as system_router

    monkeypatch.setattr(system_router, "is_update_allowed", lambda: True)
    monkeypatch.setattr(system_router, "run_git_command", git_runner)
    monkeypatch.setattr(
        system_router,
        "trigger_detached_restart",
        lambda **kwargs: restart_calls.append(kwargs),
    )
    monkeypatch.setattr(
        system_router,
        "get_current_git_info",
        lambda: {"current_commit": "abc1234", "branch": "master"},
    )


def test_update_does_not_pull_inside_the_request(client, monkeypatch):
    """The endpoint must never run a mutating `git pull` in the request handler.

    A pull here rewrites watched ``backend/`` files, so uvicorn's reloader
    restarts the worker mid-request and the browser sees "Failed to fetch".
    """
    seen = []
    restart_calls = []

    def git_runner(args, cwd=None, timeout=15):
        seen.append(list(args))
        if args[:2] == ["rev-list", "--count"]:
            return 0, "3", ""
        return 0, "", ""

    _patch_update_env(monkeypatch, git_runner, restart_calls)

    resp = client.post("/api/system/update")

    assert resp.status_code == 200
    assert resp.json()["status"] == "restarting"
    assert ["pull", "origin", "master"] not in seen, "the request handler must not pull"
    assert restart_calls == [{"delay": 2.5, "update": True, "branch": "master"}]


def test_update_reports_fetch_failure_before_delegating(client, monkeypatch):
    """A bad remote is reported synchronously, and nothing is restarted."""
    restart_calls = []

    def git_runner(args, cwd=None, timeout=15):
        if args and args[0] == "fetch":
            return 1, "", "could not resolve host"
        return 0, "", ""

    _patch_update_env(monkeypatch, git_runner, restart_calls)

    resp = client.post("/api/system/update")

    assert resp.status_code == 500
    assert "Failed to fetch updates" in resp.json()["detail"]
    assert restart_calls == []


def test_update_is_noop_when_already_current(client, monkeypatch):
    """No restart is scheduled when the working tree is not behind."""
    restart_calls = []

    def git_runner(args, cwd=None, timeout=15):
        if args[:2] == ["rev-list", "--count"]:
            return 0, "0", ""
        return 0, "", ""

    _patch_update_env(monkeypatch, git_runner, restart_calls)

    resp = client.post("/api/system/update")

    assert resp.status_code == 200
    assert resp.json()["status"] == "up-to-date"
    assert restart_calls == []


def test_update_blocked_in_server_environment(client, monkeypatch):
    import backend.routers.system as system_router

    monkeypatch.setattr(system_router, "is_update_allowed", lambda: False)
    resp = client.post("/api/system/update")
    assert resp.status_code == 403


def test_restart_endpoint_delegates_without_updating(client, monkeypatch):
    restart_calls = []

    import backend.routers.system as system_router

    monkeypatch.setattr(system_router, "is_update_allowed", lambda: True)
    monkeypatch.setattr(
        system_router,
        "trigger_detached_restart",
        lambda **kwargs: restart_calls.append(kwargs),
    )

    resp = client.post("/api/system/restart")

    assert resp.status_code == 200
    assert resp.json()["status"] == "restarting"
    assert restart_calls == [{"delay": 2.5}]
