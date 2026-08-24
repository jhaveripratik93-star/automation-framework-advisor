"""Retry decorators for LLM and tool calls with exponential backoff."""
from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


def llm_retry(max_retries: int = 2, initial_wait: float = 0.5) -> Callable:
    """Decorator: retry LLM calls on transient errors with exponential backoff.

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
                        logger.warning(
                            "llm_retry: %s attempt %d/%d failed (%s), retrying in %.1fs",
                            func.__name__, attempt + 1, max_retries + 1, exc, wait,
                        )
                        time.sleep(wait)
                        wait *= 2  # exponential backoff
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
