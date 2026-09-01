"""Synthesis Agent — LLM-powered tool output validator and feedback router.

After tools execute, this agent:
  1. Compares and validates the tool results against the user query
  2. Identifies gaps or contradictions in the data
  3. Returns a verdict + a flag indicating whether additional tool calls are needed

The verdict is fed back to the ToolSelectionAgent if needs_more_tools=True,
allowing the pipeline to fetch supplementary data before final evaluation.

Integrates with LangGraph state, conversation memory, and retry policies.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from src.agents.memory import AgentMemory
from src.agents.retry import llm_retry

if TYPE_CHECKING:
    from src.agents.state import AgentState

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
    "NEEDS_MORE: <yes|no>\n\n"
    "CRITICAL RULE: If the tool results do NOT contain frameworks relevant to the user's "
    "question (e.g. user asked about performance frameworks but results only show web UI "
    "frameworks), you MUST set NEEDS_MORE: yes so a better tool can be tried."
)

_MAX_SYNTHESIS_ROUNDS = 2


@dataclass
class SynthesisResult:
    verdict: str
    gaps: list[str]
    needs_more_tools: bool


class SynthesisAgent:
    """Validates tool output quality and decides if more data is needed.

    Maintains memory of synthesis rounds for context across the feedback loop.
    """

    def __init__(self, llm_client) -> None:
        self._client = llm_client
        self._memory = AgentMemory(agent_name="synthesis_agent", max_entries=10)

    @llm_retry(max_retries=3, initial_wait=1.0)
    def _call_llm(self, prompt: str) -> str:
        """LLM call with retry policy for transient failures."""
        result = self._client.chat(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM_PROMPT,
            max_tokens=100,
            caller="SynthesisAgent",
        )
        return result.get("content", "").strip()

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

        # Include memory context for multi-round synthesis
        memory_ctx = self._memory.get_context_string(last_n=3)

        prompt = (
            f"User question: {user_message}\n\n"
            f"Tool results:\n{tool_block}\n\n"
        )
        if memory_ctx:
            prompt += f"\n{memory_ctx}\n\n"
        prompt += "Analyse the above and respond in the required format."

        try:
            raw = self._call_llm(prompt)
            result = self._parse(raw)
        except Exception as exc:
            logger.warning("SynthesisAgent: LLM call failed after retries (%s) — skipping extra round", exc)
            result = SynthesisResult(verdict="Synthesis unavailable.", gaps=[], needs_more_tools=False)

        # Store in memory for context in subsequent rounds
        self._memory.add(
            "synthesis",
            f"round={round_num} verdict='{result.verdict[:80]}' needs_more={result.needs_more_tools}",
            {"gaps": result.gaps, "round": round_num},
        )

        return result

    def run_node(self, state: AgentState) -> dict[str, Any]:
        """LangGraph node entry point — reads/writes from shared state."""
        user_message = state["user_message"]
        tool_results = state.get("tool_results", [])
        round_num = state.get("synthesis_round", 0)

        result = self.synthesise(user_message, tool_results, round_num)

        return {
            "synthesis_verdict": result.verdict,
            "synthesis_gaps": result.gaps,
            "synthesis_needs_more": result.needs_more_tools,
            "synthesis_round": round_num + 1,
        }

    @property
    def memory(self) -> AgentMemory:
        return self._memory

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
