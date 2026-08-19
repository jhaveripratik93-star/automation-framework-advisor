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

_AMBIGUOUS_QUERIES = {
    "microservice ui": (
        "Are you asking about (a) UI automation for microservice apps, "
        "or (b) micro-frontend architecture?"
    ),
    "micro frontend": (
        "Do you want framework recommendations for testing micro-frontends, "
        "or building them?"
    ),
    "best framework": (
        "Best for what? (API testing / UI automation / performance / mobile)"
    ),
}


@dataclass
class DecisionResult:
    action: str       # "tool_call" | "direct" | "clarify"
    reasoning: str
    clarification: str | None = None


class DecisionAgent:
    def __init__(self, llm_client=None) -> None:
        self._client = llm_client

    def _get_ambiguity(self, user_message: str, graph_context: str = "") -> str | None:
        try:
            message = user_message.lower().strip()

            for phrase, clarrification in _AMBIGUOUS_QUERIES.items():
                if phrase in message:
                    return DecisionResult(
                        action="clarify",
                        reasoning="Query matches ambiguous topic",
                        clarification=clarification,
                    )
            return None
        except Exception as exc:
            logger.warning("DecisionAgent: LLM call failed to clarify (%s) — defaulting to tool_call", exc)
            action = "tool_call"

    def decide(self, user_message: str, graph_context: str = "") -> DecisionResult:
        try:
            if clarification := self._get_ambiguity(user_message):
                logger.info("DecisionAgent: ambiguity detected in query='%s'", user_message)
                return DecisionResult(action="clarify", reasoning="Ambiguous query detected", clarification=clarification)
            context = (
            f"\nConversation context:\n{graph_context}"
            if graph_context
            else ""
            )
            result = self._client.chat(
                messages=[{"role": "user", "content": f"{user_message}{context}"}],
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