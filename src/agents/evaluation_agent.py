"""Evaluation Agent — takes raw tool output(s) and the original user query,
then uses the LLM to produce a coherent, grounded evaluation/answer.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.llm.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an expert Automation Framework Migration Advisor. "
    "You have been given tool results containing framework data. "
    "Use ONLY the provided data to answer the user's question. "
    "Be specific, concise, and complete. Do not hallucinate framework names or scores."
)


@dataclass
class EvaluationResult:
    response: str
    tool_results_used: list[str]
    reasoning: str = ""


class EvaluationAgent:
    """Synthesises tool outputs into a coherent LLM response."""

    def __init__(self, llm_client: OllamaClient) -> None:
        self._client = llm_client

    def evaluate(
        self,
        user_message: str,
        tool_results: list[dict[str, Any]],
        graph_context: str = "",
        profile_context: str = "",
    ) -> EvaluationResult:
        """Combine tool results and ask LLM to produce a final answer.

        Args:
            user_message: Original user query.
            tool_results: List of {"tool_name": str, "result": str} dicts.
            graph_context: Pre-retrieved graph context (may be empty).
            profile_context: User profile summary string (may be empty).
        """
        # Build context block from tool results
        tool_block = ""
        tool_names_used = []
        for tr in tool_results:
            tool_names_used.append(tr["tool_name"])
            tool_block += f"\n\n### Tool: {tr['tool_name']}\n{tr['result']}"

        system = _SYSTEM_PROMPT
        if profile_context:
            system += f"\n\nUser profile: {profile_context}"
        if graph_context:
            system += f"\n\n## Knowledge Graph Context:\n{graph_context[:2000]}"

        prompt = (
            f"User question: {user_message}\n\n"
            f"## Tool Results:{tool_block}\n\n"
            "Based on the tool results above, provide a complete and specific answer."
        )

        logger.info(
            "EvaluationAgent: synthesising %d tool result(s) for query='%.60s'",
            len(tool_results), user_message,
        )

        try:
            result = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system=system,
                tools=None,
            )
            response_text = result.get("content", "").strip()
        except Exception as exc:
            logger.warning("EvaluationAgent: LLM call failed (%s) — returning raw tool output", exc)
            response_text = tool_block.strip() or "No data available."

        logger.info("EvaluationAgent: response_len=%d", len(response_text))
        return EvaluationResult(
            response=response_text,
            tool_results_used=tool_names_used,
            reasoning=f"synthesised {len(tool_results)} tool result(s)",
        )
