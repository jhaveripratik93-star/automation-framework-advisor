"""Synthesis Agent — LLM-powered tool output validator and feedback router.

After tools execute, this agent:
  1. Compares and validates the tool results against the user query
  2. Identifies gaps or contradictions in the data
  3. Returns a verdict + a flag indicating whether additional tool calls are needed

The verdict is fed back to the ToolSelectionAgent if needs_more_tools=True,
allowing the pipeline to fetch supplementary data before final evaluation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a data quality analyst for an automation framework advisor. "
    "You receive raw tool results and must determine: "
    "(1) whether the data is sufficient to answer the user's question, "
    "(2) what gaps or contradictions exist, "
    "(3) whether additional tool calls are needed. "
    "Be concise. Respond in this exact format:\n"
    "VERDICT: <one sentence summary of what the data shows>\n"
    "GAPS: <comma-separated list of missing info, or 'none'>\n"
    "NEEDS_MORE: <yes|no>"
)


@dataclass
class SynthesisResult:
    verdict: str
    gaps: list[str]
    needs_more_tools: bool


class SynthesisAgent:
    """Validates tool output quality and decides if more data is needed."""

    def __init__(self, llm_client) -> None:
        self._client = llm_client

    def synthesise(
        self,
        user_message: str,
        tool_results: list[dict[str, Any]],
        round_num: int = 0,
    ) -> SynthesisResult:
        """Analyse tool results and return a synthesis verdict.

        Args:
            user_message: Original (or augmented) user query.
            tool_results: List of {"tool_name": str, "result": str} from this round.
            round_num: Current iteration (0-indexed); prevents infinite loops.
        """
        if not tool_results:
            return SynthesisResult(verdict="No tool results to analyse.", gaps=[], needs_more_tools=False)

        tool_block = "\n\n".join(
            f"[{tr['tool_name']}]\n{tr['result'][:1500]}" for tr in tool_results
        )
        prompt = (
            f"User question: {user_message}\n\n"
            f"Tool results:\n{tool_block}\n\n"
            "Analyse the above and respond in the required format."
        )

        try:
            result = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system=_SYSTEM_PROMPT,
            )
            raw = result.get("content", "").strip()
            return self._parse(raw)
        except Exception as exc:
            logger.warning("SynthesisAgent: LLM call failed (%s) — skipping extra round", exc)
            return SynthesisResult(verdict="Synthesis unavailable.", gaps=[], needs_more_tools=False)

    # ------------------------------------------------------------------

    @staticmethod
    def _parse(raw: str) -> SynthesisResult:
        verdict, gaps_str, needs_more = "", "none", "no"
        for line in raw.splitlines():
            line = line.strip()
            if line.upper().startswith("VERDICT:"):
                verdict = line.split(":", 1)[1].strip()
            elif line.upper().startswith("GAPS:"):
                gaps_str = line.split(":", 1)[1].strip()
            elif line.upper().startswith("NEEDS_MORE:"):
                needs_more = line.split(":", 1)[1].strip().lower()

        gaps = [] if gaps_str.lower() == "none" else [g.strip() for g in gaps_str.split(",") if g.strip()]
        logger.info("SynthesisAgent: verdict='%.80s' gaps=%s needs_more=%s", verdict, gaps, needs_more)
        return SynthesisResult(
            verdict=verdict or raw[:200],
            gaps=gaps,
            needs_more_tools=needs_more == "yes",
        )
