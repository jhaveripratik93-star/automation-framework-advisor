"""Agent Memory — lightweight in-memory storage for agent decisions and conversations."""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MemoryEntry:
    """Single memory entry."""
    category: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentMemory:
    """Per-agent short-term memory (bounded deque)."""

    def __init__(self, agent_name: str = "default", max_entries: int = 50) -> None:
        self._agent_name = agent_name
        self._entries: deque[MemoryEntry] = deque(maxlen=max_entries)

    def add(self, category: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        entry = MemoryEntry(category=category, content=content, metadata=metadata or {})
        self._entries.append(entry)
        logger.debug("AgentMemory[%s]: stored '%s' entry", self._agent_name, category)

    def get_recent(self, n: int = 5) -> list[MemoryEntry]:
        return list(self._entries)[-n:]

    def get_by_category(self, category: str) -> list[MemoryEntry]:
        return [e for e in self._entries if e.category == category]

    def get_context_string(self, last_n: int = 5) -> str:
        """Return recent entries as a formatted context string for LLM prompts."""
        recent = self.get_recent(last_n)
        if not recent:
            return ""
        lines = [f"## Previous {self._agent_name} context:"]
        for entry in recent:
            lines.append(f"- [{entry.category}] {entry.content}")
        return "\n".join(lines)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


class ConversationMemory:
    """Multi-turn conversation memory for the full pipeline."""

    def __init__(self, max_turns: int = 20) -> None:
        self._turns: deque[dict[str, str]] = deque(maxlen=max_turns)

    def add_turn(self, role: str, content: str) -> None:
        self._turns.append({"role": role, "content": content})

    def get_history(self, last_n: int | None = None) -> list[dict[str, str]]:
        turns = list(self._turns)
        if last_n is not None:
            return turns[-last_n:]
        return turns

    def clear(self) -> None:
        self._turns.clear()

    def __len__(self) -> int:
        return len(self._turns)
