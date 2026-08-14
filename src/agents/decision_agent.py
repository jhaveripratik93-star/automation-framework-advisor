"""Decision Agent — decides whether a user query needs a tool call or can be
answered directly from context.

Returns one of:
  - "tool_call"   : query needs data retrieval / computation
  - "direct"      : query can be answered from system prompt + history alone
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.llm.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

# Keywords that strongly suggest a tool call is needed
_TOOL_SIGNAL_TERMS = [
    "compare", "comparison", "vs", "versus",
    "recommend", "best", "which framework",
    "migrate", "migration", "roadmap",
    "coverage", "analyze", "analyse",
    "details", "capabilities", "limitations",
    "score", "evaluate", "evaluation",
    "search", "find", "look up",
]

# Keywords that suggest a direct conversational answer is fine
_DIRECT_SIGNAL_TERMS = [
    "hello", "hi", "hey", "thanks", "thank you",
    "what is", "explain", "tell me about",
    "how does", "why is",
]


@dataclass
class DecisionResult:
    action: str          # "tool_call" | "direct"
    reasoning: str       # short explanation for logging


class DecisionAgent:
    """Decides whether the query requires tool execution.

    Uses a fast heuristic first; falls back to LLM judgement for ambiguous
    queries when a client is available.
    """

    def __init__(self, llm_client: OllamaClient | None = None) -> None:
        self._client = llm_client

    def decide(self, user_message: str, graph_context: str = "") -> DecisionResult:
        msg_lower = user_message.lower()

        # Fast heuristic — tool signals
        if any(t in msg_lower for t in _TOOL_SIGNAL_TERMS):
            result = DecisionResult(action="tool_call", reasoning="heuristic: tool-signal keyword matched")
            logger.info("DecisionAgent: %s → %s (%s)", user_message[:60], result.action, result.reasoning)
            return result

        # Fast heuristic — direct signals
        if any(t in msg_lower for t in _DIRECT_SIGNAL_TERMS) and len(user_message) < 80:
            result = DecisionResult(action="direct", reasoning="heuristic: direct-signal keyword matched")
            logger.info("DecisionAgent: %s → %s (%s)", user_message[:60], result.action, result.reasoning)
            return result

        # If graph context already contains relevant data, prefer direct
        if graph_context and len(graph_context) > 200:
            result = DecisionResult(action="direct", reasoning="graph context sufficient for direct answer")
            logger.info("DecisionAgent: %s → %s (%s)", user_message[:60], result.action, result.reasoning)
            return result

        # Default: use tool call to be safe
        result = DecisionResult(action="tool_call", reasoning="default: no strong direct signal")
        logger.info("DecisionAgent: %s → %s (%s)", user_message[:60], result.action, result.reasoning)
        return result
