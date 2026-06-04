"""Simple in-process TTL cache for upstream proxy responses.

This is not a Redis replacement — it's a per-worker memory cache with a
hard size cap. Good enough to take the 3.8MB staging-inquiries blob off
the hot path so the browser doesn't wait on Heroku cold-starts every
time the user opens the InpharmD Inquiries tab.

Each entry stores:
- value          — whatever was cached
- stored_at      — monotonic timestamp (seconds)
- ttl_seconds    — how long the entry is considered "fresh"

Beyond `ttl_seconds` an entry is *stale*; callers can still retrieve it
(via `get_stale_ok`) and fall back to it when the upstream call fails.

For production we'd swap this for Redis with the same interface.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional, Tuple

log = logging.getLogger("inquiry.cache")

DEFAULT_TTL_SECONDS = 300  # 5 minutes
MAX_ENTRIES = 256          # crude LRU-ish cap so a runaway cache key set can't OOM us


class _CacheEntry:
    __slots__ = ("value", "stored_at", "ttl_seconds")

    def __init__(self, value: Any, ttl_seconds: int):
        self.value = value
        self.stored_at = time.monotonic()
        self.ttl_seconds = ttl_seconds

    def age(self) -> float:
        return time.monotonic() - self.stored_at

    def is_fresh(self) -> bool:
        return self.age() < self.ttl_seconds


_store: dict[str, _CacheEntry] = {}
_lock = threading.Lock()


def get(key: str) -> Optional[Tuple[Any, float]]:
    """Return (value, age_seconds) if a FRESH entry exists; else None."""
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        if not entry.is_fresh():
            return None
        return entry.value, entry.age()


def get_stale_ok(key: str) -> Optional[Tuple[Any, float]]:
    """Return (value, age_seconds) for any entry, fresh OR stale."""
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        return entry.value, entry.age()


def set(key: str, value: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
    """Store a value. Evicts the oldest entry if we'd exceed MAX_ENTRIES."""
    with _lock:
        if len(_store) >= MAX_ENTRIES and key not in _store:
            # evict the entry with the oldest stored_at
            oldest_key = min(_store, key=lambda k: _store[k].stored_at)
            log.info("cache evict (cap reached) key=%s", oldest_key)
            _store.pop(oldest_key, None)
        _store[key] = _CacheEntry(value, ttl_seconds)
        log.info("cache set key=%s ttl=%ds entries=%d", key, ttl_seconds, len(_store))


def invalidate(key: str) -> None:
    with _lock:
        if _store.pop(key, None):
            log.info("cache invalidate key=%s", key)


def invalidate_prefix(prefix: str) -> int:
    """Bust every key starting with `prefix`. Returns number dropped."""
    with _lock:
        keys = [k for k in _store if k.startswith(prefix)]
        for k in keys:
            _store.pop(k, None)
        if keys:
            log.info("cache invalidate_prefix=%s dropped=%d", prefix, len(keys))
        return len(keys)


def stats() -> dict:
    with _lock:
        return {
            "entries": len(_store),
            "keys": [
                {"key": k, "age": round(e.age(), 1), "ttl": e.ttl_seconds, "fresh": e.is_fresh()}
                for k, e in _store.items()
            ],
        }
