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
    """Build a deterministic cache key from tool name and arguments.

    Normalizes framework names to lowercase and sorts lists so that
    ("Playwright", "Cypress") and ("Cypress", "Playwright") hit the same key.
    """
    normalized = _normalize_arguments(arguments)
    sorted_args = json.dumps(normalized, sort_keys=True, default=str)
    return f"tool:{tool_name}:{sorted_args}"


def make_tool_call_cache_key(tool_calls: list[Any], weight_signature: str = "") -> str:
    """Build a response cache key from the resolved tool calls.

    This keys the cache on WHAT the pipeline decided to do (the tool selection
    agent's output) rather than the raw query text. Two differently-phrased
    queries that resolve to the same tool call(s) share a cache entry — no
    stop words, synonyms, or keyword lists needed.

    Args:
        tool_calls: list of objects with .tool_name and .arguments (ToolCall),
                    or dicts with "tool_name"/"arguments" keys.
        weight_signature: scoring-weight fingerprint (ranking depends on it).
    """
    parts = []
    for tc in tool_calls:
        if hasattr(tc, "tool_name"):
            name, args = tc.tool_name, getattr(tc, "arguments", {})
        else:
            name, args = tc.get("tool_name", ""), tc.get("arguments", {})
        normalized = _normalize_arguments(args or {})
        parts.append(f"{name}:{json.dumps(normalized, sort_keys=True, default=str)}")
    # Sort so tool order doesn't matter
    parts.sort()
    raw = "|".join(parts) + "|" + weight_signature
    return f"toolcall:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def make_query_cache_key(
    user_message: str,
    uploaded_docs: str = "",
    case_study: str = "",
    weight_signature: str = "",
) -> str:
    """Build a semantic cache key from the query and context inputs.

    Normalizes the query so that semantically equivalent questions hash
    to the same key:
      - "Compare Playwright and Cypress" == "compare cypress vs playwright"
      - "What is Selenium?" == "what is selenium"
      - "Recommend a framework for API testing" == "recommend framework for api testing"

    The weight_signature is included so that changing scoring weights
    invalidates the cache — the appended ranking table depends on it.
    """
    normalized = _normalize_query(user_message)
    raw = (
        f"{normalized}|{uploaded_docs[:100].strip().lower()}"
        f"|{case_study[:100].strip().lower()}|{weight_signature}"
    )
    return f"query:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


# ── Query normalization helpers ───────────────────────────────────────

# Filler words that don't change query semantics
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "can", "could", "would", "should", "will",
    "i", "me", "my", "we", "our", "you", "your",
    "it", "its", "that", "this", "these", "those",
    "of", "in", "on", "at", "to", "for", "with", "by", "from", "about",
    "and", "or", "but", "not", "no", "so", "if", "than",
    "what", "which", "how", "please", "help",
    "just", "also", "very", "really", "quite",
    "vs", "between", "using", "use", "need", "want",
})

# Synonym pairs — map to a canonical form
_SYNONYMS = {
    "versus": "vs",
    "compare": "compare",
    "comparison": "compare",
    "comparing": "compare",
    "recommend": "recommend",
    "recommendation": "recommend",
    "recommendations": "recommend",
    "suggesting": "recommend",
    "suggest": "recommend",
    "migrate": "migrate",
    "migration": "migrate",
    "migrating": "migrate",
    "move": "migrate",
    "moving": "migrate",
    "switch": "migrate",
    "switching": "migrate",
    "transition": "migrate",
    "suitable": "recommend",
    "capabilities": "capability",
    "limitations": "limitation",
    "details": "detail",
    "framework": "framework",
    "frameworks": "framework",
    "framwork": "framework",       # common typo
    "framworks": "framework",      # common typo
    "tool": "framework",
    "tools": "framework",
    "testing": "test",
    "tests": "test",
    "apis": "api",
    "microservices": "microservice",
    "graphql": "graphql",
}


def _normalize_query(query: str) -> str:
    """Normalize a query for semantic cache matching.

    Steps:
      1. Lowercase
      2. Remove punctuation
      3. Replace synonyms with canonical forms
      4. Remove stop words
      5. Sort remaining tokens (so word order doesn't matter)
    """
    import re

    text = query.lower().strip()
    # Remove punctuation except hyphens in compound words
    text = re.sub(r"[^\w\s-]", " ", text)
    # Split into tokens
    tokens = text.split()
    # Replace synonyms
    tokens = [_SYNONYMS.get(t, t) for t in tokens]
    # Remove stop words
    tokens = [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]

    _ACTION_VERBS = {"compare", "recommend", "migrate", "detail", "capability", "limitation", "analyze"}
    _KNOWN_FW_TOKENS = {
        "playwright", "cypress", "selenium", "webdriverio", "robot", "testcafe",
        "puppeteer", "appium", "karate", "k6", "locust", "terraform", "ansible",
        "chef", "pulumi", "cloudformation",
    }

    # Collapse intent to a single canonical verb so different phrasings of the
    # same request match. "which framework should I use for X" and "suitable
    # framework for X" both become a "recommend" query about subject X.
    present_actions = [t for t in tokens if t in _ACTION_VERBS]
    fw_count = sum(1 for t in tokens if t in _KNOWN_FW_TOKENS)
    if present_actions:
        intent = present_actions[0]
    elif fw_count >= 2:
        intent = "compare"        # "Robot Framework vs Playwright"
    else:
        intent = "recommend"      # "framework for API testing"

    # Drop all action verbs; represent intent with the single canonical token.
    subject_tokens = [t for t in tokens if t not in _ACTION_VERBS]

    # "graphql" already implies the API domain — drop the redundant "api" token
    # so "GraphQL APIs" and "GraphQL" hash the same.
    if "graphql" in subject_tokens:
        subject_tokens = [t for t in subject_tokens if t != "api"]

    subject_tokens.append(intent)

    # Sort for order independence
    subject_tokens.sort()
    return " ".join(subject_tokens)


def _normalize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool arguments for cache key consistency.

    - Lowercase all string values
    - Sort lists (so ["Playwright", "Cypress"] == ["Cypress", "Playwright"])
    """
    normalized = {}
    for k, v in arguments.items():
        if isinstance(v, str):
            normalized[k] = v.strip().lower()
        elif isinstance(v, list):
            normalized[k] = sorted(
                item.strip().lower() if isinstance(item, str) else item
                for item in v
            )
        else:
            normalized[k] = v
    return normalized


# ── Shared singleton instances ────────────────────────────────────────

# Tool results cache: 5-minute TTL, max 200 entries
tool_cache = TTLCache(ttl_seconds=300.0, max_entries=200)

# Full response cache: 2-minute TTL, max 50 entries
response_cache = TTLCache(ttl_seconds=120.0, max_entries=50)
