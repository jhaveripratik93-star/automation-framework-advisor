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
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable

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


def log_tool_call(fn: Callable) -> Callable:
    """Decorator: log every call to an MCP tool function uniformly.

    Logs one line on entry (tool name + argument summary), one line on
    successful exit (tool name, elapsed ms, result size), and one line on
    exception (tool name, elapsed ms, exception type/message) before
    re-raising — the decorator never swallows or alters tool behavior.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        tool_name = fn.__name__
        logger.info("ToolCall START '%s' args=(%s)", tool_name, _summarize_args(kwargs))
        start = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.error(
                "ToolCall ERROR '%s' after %.1fms — %s: %s",
                tool_name, elapsed_ms, type(exc).__name__, exc,
            )
            raise
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "ToolCall OK '%s' after %.1fms -> %d chars",
            tool_name, elapsed_ms, _result_size(result),
        )
        return result

    return wrapper
