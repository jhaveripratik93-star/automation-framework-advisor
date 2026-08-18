"""Decision Agent — uses the LLM to decide whether a query needs tool calls."""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a routing agent for an automation framework advisor. "
    "Given a user query, decide whether it requires tool execution (data retrieval, "
    "comparison, migration paths, coverage analysis) or can be answered directly "
    "from general knowledge.\n"
    "Respond with exactly one word: tool_call or direct."
)


@dataclass
class DecisionResult:
    action: str       # "tool_call" | "direct"
    reasoning: str


class DecisionAgent:
    def __init__(self, llm_client=None) -> None:
        self._client = llm_client

    def decide(self, user_message: str, graph_context: str = "") -> DecisionResult:
        try:
            result = self._client.chat(
                messages=[{"role": "user", "content": user_message}],
                system=_SYSTEM_PROMPT,
            )
            action = result.get("content", "").strip().lower()
            action = "tool_call" if "tool" in action else "direct"
        except Exception as exc:
            logger.warning("DecisionAgent: LLM call failed (%s) — defaulting to tool_call", exc)
            action = "tool_call"

        result = DecisionResult(action=action, reasoning="llm decision")
        logger.info("DecisionAgent: query='%.60s' → %s", user_message, action)
        return result
