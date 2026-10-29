"""Uniform tool-call audit logging for the MCP server.

Problem this fixes: only tools that happened to route through
``ToolExecutor.execute()`` logged their name on invocation (via
``src.tools.executor``'s "ToolExecutor: '<name>' -> N chars" line). The
other tools — ``compare_repos``, ``compare_repos_csv``,
``generate_migration_roadmap``, ``analyze_coverage_gaps``,
``generate_boilerplate_project``, ``discover_new_frameworks``,
``add_framework_to_kb``, ``convert_test_project``, ``generate_test_code``,
``analyze_test_case_coverage``, ``list_frameworks`` — called their
implementation directly and left no trace in logs/mcp_server.log, making
it impossible to audit which tools were actually invoked versus merely
described.

``@log_tool_call`` wraps every ``@mcp.tool()`` function with the SAME
log line format regardless of what the function does internally, so
grepping logs/mcp_server.log for "ToolCall:" gives a complete, consistent
record of every automation-framework-advisor tool invocation — name,
argument summary, outcome, size/duration, and errors.

IMPORTANT — "soft failures" vs raised exceptions:

``ToolExecutor.execute()`` (used by 8 of the 20 tools: get_framework_details,
search_knowledge_graph, recommend_frameworks, score_frameworks,
find_migration_paths, analyze_prerequisites, compare_frameworks,
list_frameworks_by_category) catches EVERY exception internally and
returns it as a plain string — ``"Error executing {tool}: {exc}"`` — it
never re-raises. Several other tools (analyze_coverage_gaps,
generate_test_code, add_framework_to_kb) similarly return
``{"error": "..."}`` dicts on bad input instead of raising. From this
decorator's point of view, those calls returned a value successfully —
if we only logged based on whether an exception propagated, every one of
those failures would be mislabeled ``ToolCall OK``, and grepping for
"ToolCall ERROR" would silently miss them.

``_detect_soft_error`` recognizes both known shapes (the ToolExecutor
sentinel-string prefixes, and ``{"error": ...}`` dicts) and logs them as
``ToolCall ERROR (soft) ...`` — same "ToolCall ERROR" prefix as a raised
exception's ``ToolCall ERROR (raised) ...``, so a single
``grep "ToolCall ERROR"`` catches both kinds of failure. Only ``ToolCall
OK`` means the call both returned without raising AND did not match one
of these known failure shapes.
"""
from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("mcp_server.tool_calls")

# Caps to keep the log readable and avoid dumping entire file contents
# (compare_repos/convert_test_project pass full source files as arguments).
_MAX_STR_PREVIEW = 80
_MAX_LIST_PREVIEW = 3


def _summarize_value(value: Any) -> str:
    """Render one argument value as a short, log-safe string.

    Large/structured values (long strings, lists of dicts such as
    files_a/files_b or test_cases) are summarized by shape (length, type)
    rather than dumped in full — keeps the log readable and avoids
    accidentally persisting large source payloads to disk.
    """
    if value is None:
        return "None"
    if isinstance(value, str):
        if len(value) <= _MAX_STR_PREVIEW:
            return repr(value)
        return f"<str len={len(value)}: {value[:_MAX_STR_PREVIEW]!r}...>"
    if isinstance(value, (list, tuple)):
        kind = type(value).__name__
        if not value:
            return f"{kind}[]"
        sample = ", ".join(_summarize_value(v) for v in value[:_MAX_LIST_PREVIEW])
        more = f", ...+{len(value) - _MAX_LIST_PREVIEW} more" if len(value) > _MAX_LIST_PREVIEW else ""
        return f"{kind}[len={len(value)}]({sample}{more})"
    if isinstance(value, dict):
        keys = list(value.keys())[:6]
        more = f", ...+{len(value) - 6} more keys" if len(value) > 6 else ""
        return f"dict[len={len(value)}](keys={keys}{more})"
    return repr(value)


def _summarize_args(kwargs: dict[str, Any]) -> str:
    return ", ".join(f"{k}={_summarize_value(v)}" for k, v in kwargs.items())


def _result_size(result: Any) -> int:
    """Best-effort size metric for the result, mirroring ToolExecutor's
    existing 'N chars' convention so log lines stay comparable."""
    try:
        return len(result) if isinstance(result, (str, list, dict)) else len(str(result))
    except Exception:
        return -1


# String prefixes ToolExecutor.execute() and its tool implementations use
# for a caught-and-swallowed failure instead of raising. Keep in sync with
# src/tools/executor.py's `execute()` (the generic "Error executing ..."
# and "Error: unknown tool ..." sentinels) — individual tool methods also
# return domain-specific "not found" strings, which are intentionally NOT
# treated as errors here since "Framework 'X' not found" from a
# user-mistyped name is a normal, expected result rather than a bug.
_SOFT_ERROR_STR_PREFIXES = ("Error executing ", "Error: unknown tool ")


def _detect_soft_error(result: Any) -> str | None:
    """Return a short reason string if `result` is a known "soft failure"
    shape (an error swallowed internally and returned as a normal value
    instead of raised), else None.

    Recognizes:
      - str results starting with one of _SOFT_ERROR_STR_PREFIXES
        (ToolExecutor.execute()'s catch-all).
      - dict results with a truthy "error" key ({"error": "..."} —
        used by analyze_coverage_gaps, generate_test_code,
        add_framework_to_kb on invalid input).
    """
    if isinstance(result, str) and result.startswith(_SOFT_ERROR_STR_PREFIXES):
        return result[:200]
    if isinstance(result, dict) and result.get("error"):
        return str(result["error"])[:200]
    return None


def _log_success(tool_name: str, start: float, result: Any) -> None:
    elapsed_ms = (time.monotonic() - start) * 1000
    soft_error = _detect_soft_error(result)
    if soft_error is not None:
        logger.error(
            "ToolCall ERROR (soft) '%s' after %.1fms — %s",
            tool_name, elapsed_ms, soft_error,
        )
    else:
        logger.info(
            "ToolCall OK '%s' after %.1fms -> %d chars",
            tool_name, elapsed_ms, _result_size(result),
        )


def _log_exception(tool_name: str, start: float, exc: Exception) -> None:
    elapsed_ms = (time.monotonic() - start) * 1000
    logger.error(
        "ToolCall ERROR (raised) '%s' after %.1fms — %s: %s",
        tool_name, elapsed_ms, type(exc).__name__, exc,
    )


def log_tool_call(fn: Callable) -> Callable:
    """Decorator: log every call to an MCP tool function uniformly.

    Logs one line on entry (tool name + argument summary), one line on
    successful exit (tool name, elapsed ms, result size), and one line on
    exception (tool name, elapsed ms, exception type/message) before
    re-raising — the decorator never swallows or alters tool behavior.

    Supports both sync and async tool functions (FastMCP tools may be
    either) — the wrapper's sync/async-ness matches the wrapped
    function's, so `inspect.iscoroutinefunction` checks FastMCP performs
    on registration still see the right shape.
    """
    import inspect

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            tool_name = fn.__name__
            logger.info("ToolCall START '%s' args=(%s)", tool_name, _summarize_args(kwargs))
            start = time.monotonic()
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                _log_exception(tool_name, start, exc)
                raise
            _log_success(tool_name, start, result)
            return result

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        tool_name = fn.__name__
        logger.info("ToolCall START '%s' args=(%s)", tool_name, _summarize_args(kwargs))
        start = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            _log_exception(tool_name, start, exc)
            raise
        _log_success(tool_name, start, result)
        return result

    return wrapper
