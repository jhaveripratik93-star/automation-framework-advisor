"""BaseToolExecutor — shared dispatch boilerplate for feature-owned
tool executors (AdvisorToolsExecutor, CoverageExecutor,
PrerequisitesExecutor).

Every feature executor was independently re-implementing the exact same
three things:
  1. "lazily default-load the knowledge base if none was passed in"
  2. a `.tools: dict[str, Callable]` registry + `execute(name, args)`
     dispatch loop
  3. the identical log/error-string format on unknown tool / exception

That boilerplate now lives here once. Subclasses just set `self.tools`
in their own `__init__` (after calling `super().__init__(knowledge_base)`)
and implement their `_handler` methods — `execute()` and the KB default
are inherited as-is.

The error strings ("Error: unknown tool '...'" / "Error executing
{tool}: {exc}") are a load-bearing format other code depends on —
``mcp_server/tool_logging.py`` pattern-matches these exact prefixes to
detect "soft failures" that were caught and returned as a string instead
of raised. Keep this in sync if that ever changes.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.knowledge_base.loader import KnowledgeBase

logger = logging.getLogger(__name__)


class BaseToolExecutor:
    """Common `.tools` dispatch + KB bootstrap for feature tool executors.

    Subclasses must set ``self.tools`` (a ``dict[str, Callable]``) in
    their own ``__init__`` before ``execute()`` is called.
    """

    def __init__(self, knowledge_base: KnowledgeBase | None = None) -> None:
        if knowledge_base is None:
            from src.knowledge_base import KnowledgeBase as _KB
            knowledge_base = _KB(data_dir="data/frameworks")
            knowledge_base.load()
        self.kb = knowledge_base
        self.tools: dict[str, Callable] = {}

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Dispatch ``tool_name`` to its handler in ``self.tools``.

        Never raises — unknown tools and exceptions both come back as a
        plain error string (see module docstring on why that format is
        fixed) so callers (ToolRouter, MCP tool wrappers) can treat every
        executor uniformly.
        """
        name = type(self).__name__
        if tool_name not in self.tools:
            logger.error("%s: unknown tool '%s'", name, tool_name)
            return f"Error: unknown tool '{tool_name}'"
        try:
            result = self.tools[tool_name](**arguments)
            logger.info("%s: '%s' -> %d chars", name, tool_name, len(str(result)))
            return result
        except Exception as exc:
            logger.error("%s: '%s' failed - %s: %s", name, tool_name, type(exc).__name__, exc)
            return f"Error executing {tool_name}: {exc}"
