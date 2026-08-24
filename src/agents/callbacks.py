"""Pipeline callbacks — lightweight event system for streaming and logging."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class StreamEvent(Enum):
    """Events emitted during pipeline execution."""
    NODE_START = "node_start"
    NODE_END = "node_end"
    LLM_CALL = "llm_call"
    LLM_RESPONSE = "llm_response"
    TOOL_EXECUTE = "tool_execute"
    TOOL_RESULT = "tool_result"
    ERROR = "error"


@dataclass
class PipelineCallbackHandler:
    """Collects and dispatches pipeline events to registered listeners."""

    listeners: list[Callable[[StreamEvent, dict[str, Any]], None]] = field(default_factory=list)

    def register(self, listener: Callable[[StreamEvent, dict[str, Any]], None]) -> None:
        self.listeners.append(listener)

    # Alias for backward compat with alternate naming
    add_handler = register

    def emit(self, event: StreamEvent, data: dict[str, Any] | None = None) -> None:
        payload = data or {}
        for listener in self.listeners:
            try:
                listener(event, payload)
            except Exception as exc:
                logger.warning("Callback listener error: %s", exc)

    # ── Convenience methods for common events ─────────────────────────

    def on_node_start(self, node_name: str) -> None:
        self.emit(StreamEvent.NODE_START, {"node": node_name, "message": f"{node_name} started"})

    def on_node_end(self, node_name: str, summary: str = "") -> None:
        self.emit(StreamEvent.NODE_END, {"node": node_name, "message": f"{node_name} done: {summary}"})

    def on_llm_call(self, agent_name: str, prompt_preview: str = "") -> None:
        self.emit(StreamEvent.LLM_CALL, {"agent": agent_name, "message": f"{agent_name} calling LLM: {prompt_preview[:80]}"})

    def on_tool_execution(self, tool_name: str, arguments: dict[str, Any] | None = None) -> None:
        self.emit(StreamEvent.TOOL_EXECUTE, {"tool": tool_name, "arguments": arguments or {}, "message": f"executing {tool_name}"})


def logging_callback(event: StreamEvent, data: dict[str, Any]) -> None:
    """Default callback that logs events at INFO level."""
    logger.info("Pipeline[%s]: %s", event.value, data.get("message", ""))
