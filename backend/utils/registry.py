"""Bounded, self-expiring in-memory registries for download/render job state.

The API stores job progress in module-level mappings that live for the process
lifetime. Without eviction they grow indefinitely on a long-running server, so
this module provides a small dict-like cache with:

* a time-to-live per entry (finished entries are reclaimed automatically), and
* a maximum entry count (oldest evictable entries are dropped first).

Only the mapping operations actually used by the routers/services are
implemented (get, __getitem__, __setitem__, __contains__, __len__, keys,
values, items, pop, clear). Entries that are still running are never evicted.
"""

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger("cheat-clip-pro.registry")

DEFAULT_JOB_TTL_SECONDS = 3600
DEFAULT_JOB_MAX_ENTRIES = 500


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


class TTLCache:
    """Thread-safe, size- and TTL-bounded mapping used for job/batch state."""

    def __init__(
        self,
        name: str,
        ttl_seconds: Optional[int] = None,
        max_entries: Optional[int] = None,
        is_terminal: Optional[Callable[[Any], bool]] = None,
    ):
        self.name = name
        self.ttl_seconds = (
            ttl_seconds if ttl_seconds is not None
            else _env_int("JOB_TTL_SECONDS", DEFAULT_JOB_TTL_SECONDS)
        )
        self.max_entries = (
            max_entries if max_entries is not None
            else _env_int("JOB_MAX_ENTRIES", DEFAULT_JOB_MAX_ENTRIES)
        )
        self._is_terminal = is_terminal
        self._data: "Dict[str, Tuple[float, float, Any]]" = {}
        self._order: List[str] = []
        self._lock = threading.RLock()

    # ── internal helpers ────────────────────────────────────────────────
    def _expired(self, created: float, ttl: float) -> bool:
        return (time.time() - created) > ttl

    def _evictable(self, entry: Any) -> bool:
        if self._is_terminal is None:
            return True
        try:
            return bool(self._is_terminal(entry))
        except Exception:
            return True

    def _should_remove(self, created: float, ttl: float, value: Any) -> bool:
        """An entry is reclaimable only when expired AND safe to evict.

        Non-terminal (still running) entries never expire, so an in-flight job
        is not dropped from lookups just because it outlived the TTL.
        """
        return self._expired(created, ttl) and self._evictable(value)

    def _drop(self, key: str) -> None:
        self._data.pop(key, None)
        try:
            self._order.remove(key)
        except ValueError:
            pass

    def _enforce_max_entries(self) -> None:
        if len(self._data) <= self.max_entries:
            return
        for key in list(self._order):
            if len(self._data) <= self.max_entries:
                break
            entry = self._data.get(key)
            if entry and self._evictable(entry[2]):
                self._drop(key)

    # ── mapping protocol ────────────────────────────────────────────────
    def __setitem__(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.time(), self.ttl_seconds, value)
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            self._enforce_max_entries()

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            if key not in self._data:
                raise KeyError(key)
            created, ttl, value = self._data[key]
            if self._should_remove(created, ttl, value):
                self._drop(key)
                raise KeyError(key)
            return value

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: str) -> bool:
        with self._lock:
            if key not in self._data:
                return False
            created, ttl, value = self._data[key]
            if self._should_remove(created, ttl, value):
                self._drop(key)
                return False
            return True

    def __len__(self) -> int:
        with self._lock:
            return len(self.keys())

    def keys(self) -> List[str]:
        with self._lock:
            return [k for k in self._order if k in self._data and k in self]

    def values(self) -> List[Any]:
        return [self[k] for k in self.keys()]

    def items(self) -> List[Tuple[str, Any]]:
        return [(k, self[k]) for k in self.keys()]

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    def pop(self, key: str, default: Any = None) -> Any:
        with self._lock:
            value = self.get(key, None)
            self._drop(key)
            return default if value is None else value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._order.clear()

    # ── maintenance ─────────────────────────────────────────────────────
    def prune(self) -> int:
        """Removes expired entries that are safe to evict. Returns count removed."""
        with self._lock:
            removed = 0
            for key in list(self._order):
                entry = self._data.get(key)
                if not entry:
                    continue
                created, ttl, value = entry
                if self._expired(created, ttl) and self._evictable(value):
                    self._drop(key)
                    removed += 1
            return removed


# Registry of all caches so the background worker can prune them in one pass.
_REGISTRIES: List[TTLCache] = []


def register(cache: TTLCache) -> TTLCache:
    _REGISTRIES.append(cache)
    return cache


def prune_all_registries() -> Dict[str, int]:
    """Prunes every registered cache; returns {registry_name: removed_count}."""
    return {cache.name: cache.prune() for cache in _REGISTRIES}
