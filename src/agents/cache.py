"""TTL cache for tool results and pipeline responses.

Provides two cache layers:
  1. Tool-level cache — keyed by (tool_name, sorted(arguments)), 5-minute TTL
  2. Response-level cache — keyed by query hash, 2-minute TTL

Both are in-memory, bounded, and thread-safe.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class TTLCache:
    """Simple in-memory cache with per-entry TTL and bounded size."""

    def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 100) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        """Return cached value if exists and not expired, else None."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, value = entry
            if time.time() > expires_at:
                del self._store[key]
                self._misses += 1
                return None
            self._hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        """Store a value with TTL."""
        with self._lock:
            # Evict expired entries if at capacity
            if len(self._store) >= self._max:
                self._evict_expired()
            # If still at capacity, drop oldest
            if len(self._store) >= self._max:
                oldest_key = min(self._store, key=lambda k: self._store[k][0])
                del self._store[oldest_key]
            self._store[key] = (time.time() + self._ttl, value)

    def clear(self) -> None:
        """Clear all entries."""
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0

    def _evict_expired(self) -> None:
        """Remove expired entries (must hold lock)."""
        now = time.time()
        expired = [k for k, (exp, _) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]

    @property
    def stats(self) -> dict[str, int]:
        """Return cache hit/miss statistics."""
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._store),
            "hit_rate": round(self._hits / max(self._hits + self._misses, 1) * 100, 1),
        }

    def __len__(self) -> int:
        return len(self._store)


def make_tool_cache_key(tool_name: str, arguments: dict[str, Any]) -> str:
    """Build a deterministic cache key from tool name and arguments."""
    # Sort arguments for consistency regardless of dict order
    sorted_args = json.dumps(arguments, sort_keys=True, default=str)
    return f"tool:{tool_name}:{sorted_args}"


def make_query_cache_key(user_message: str, uploaded_docs: str = "", case_study: str = "") -> str:
    """Build a cache key from the query and context inputs."""
    raw = f"{user_message.strip().lower()}|{uploaded_docs[:100]}|{case_study[:100]}"
    return f"query:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


# ── Shared singleton instances ────────────────────────────────────────

# Tool results cache: 5-minute TTL, max 200 entries
tool_cache = TTLCache(ttl_seconds=300.0, max_entries=200)

# Full response cache: 2-minute TTL, max 50 entries
response_cache = TTLCache(ttl_seconds=120.0, max_entries=50)
