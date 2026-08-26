"""Retry decorators for LLM and tool calls with exponential backoff.

Rate-limit aware: extracts retry-after hints from 429 responses and waits
appropriately instead of using fixed backoff.
"""
from __future__ import annotations

import functools
import logging
import re
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _extract_retry_after(exc: Exception) -> float | None:
    """Try to extract a retry-after duration from a 429 error."""
    exc_str = str(exc)

    # Look for "Please try again in Xs" pattern in error message
    match = re.search(r"try again in (\d+\.?\d*)s", exc_str, re.IGNORECASE)
    if match:
        return float(match.group(1))

    # Check if httpx response has Retry-After header
    if hasattr(exc, "response") and hasattr(exc.response, "headers"):
        retry_after = exc.response.headers.get("retry-after")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass

    return None


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if the exception is a rate limit (429) error."""
    exc_str = str(exc)
    return "429" in exc_str or "rate limit" in exc_str.lower()


def llm_retry(max_retries: int = 2, initial_wait: float = 0.5) -> Callable:
    """Decorator: retry LLM calls on transient errors with exponential backoff.

    Rate-limit aware: if a 429 is detected, uses the server-suggested wait time
    (with a minimum of initial_wait) instead of the standard backoff.

    Usage:
        @llm_retry(max_retries=2, initial_wait=0.5)
        def _call_llm(self, message: str) -> str:
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            wait = initial_wait

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        # For rate limits, use the server-suggested wait time
                        if _is_rate_limit_error(exc):
                            retry_after = _extract_retry_after(exc)
                            actual_wait = max(retry_after or wait, wait) + 0.5  # add buffer
                        else:
                            actual_wait = wait

                        logger.warning(
                            "llm_retry: %s attempt %d/%d failed (%s), retrying in %.1fs",
                            func.__name__, attempt + 1, max_retries + 1, exc, actual_wait,
                        )
                        time.sleep(actual_wait)
                        wait *= 2  # exponential backoff for non-429 errors
                    else:
                        logger.error(
                            "llm_retry: %s exhausted %d retries", func.__name__, max_retries + 1,
                        )

            raise last_exc  # type: ignore[misc]

        return wrapper
    return decorator


def tool_retry(max_retries: int = 1, initial_wait: float = 0.3) -> Callable:
    """Decorator: retry tool executions on transient errors.

    Usage:
        @tool_retry(max_retries=1, initial_wait=0.3)
        def execute(self, tool_name: str, args: dict) -> str:
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            wait = initial_wait

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        logger.warning(
                            "tool_retry: %s attempt %d/%d failed (%s), retrying in %.1fs",
                            func.__name__, attempt + 1, max_retries + 1, exc, wait,
                        )
                        time.sleep(wait)
                        wait *= 2
                    else:
                        logger.error(
                            "tool_retry: %s exhausted %d retries", func.__name__, max_retries + 1,
                        )

            raise last_exc  # type: ignore[misc]

        return wrapper
    return decorator
