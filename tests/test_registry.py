import time

from backend.utils.registry import TTLCache, prune_all_registries, register


def test_get_set_and_contains():
    cache = TTLCache("t", ttl_seconds=60)
    cache["a"] = {"status": "running"}
    assert "a" in cache
    assert cache["a"] == {"status": "running"}
    assert cache.get("missing", "fallback") == "fallback"
    assert len(cache) == 1


def test_ttl_expiry_removes_entry():
    cache = TTLCache("t", ttl_seconds=0)
    cache["a"] = {"status": "running"}
    time.sleep(0.01)
    assert "a" not in cache
    assert cache.get("a") is None


def test_terminal_entries_are_evicted_when_over_capacity():
    cache = TTLCache(
        "t",
        ttl_seconds=3600,
        max_entries=2,
        is_terminal=lambda job: job.get("status") in ("ready", "failed"),
    )
    cache["j1"] = {"status": "ready"}
    cache["j2"] = {"status": "ready"}
    cache["j3"] = {"status": "ready"}
    assert len(cache) == 2
    assert "j1" not in cache
    assert "j3" in cache


def test_running_entries_are_not_force_evicted():
    cache = TTLCache(
        "t",
        ttl_seconds=3600,
        max_entries=1,
        is_terminal=lambda job: job.get("status") in ("ready", "failed"),
    )
    cache["running"] = {"status": "downloading"}
    cache["other"] = {"status": "ready"}
    assert "running" in cache


def test_prune_respects_terminal_state():
    cache = TTLCache(
        "t",
        ttl_seconds=0,
        is_terminal=lambda job: job.get("status") in ("ready", "failed"),
    )
    cache["done"] = {"status": "ready"}
    cache["busy"] = {"status": "downloading"}
    time.sleep(0.01)
    removed = cache.prune()
    assert removed == 1
    assert "busy" in cache


def test_prune_all_registries_reports_per_registry_counts():
    cache = TTLCache("reg-test", ttl_seconds=0)
    register(cache)
    cache["x"] = {"status": "ready"}
    time.sleep(0.01)
    result = prune_all_registries()
    assert result.get("reg-test") == 1
