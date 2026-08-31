"""Reflection Agent — critiques the draft response from EvaluationAgent and
either approves it or requests a revision with specific feedback.

Sits between evaluate → format in the pipeline:
  evaluate → reflect → [approve] → format
                     → [revise]  → evaluate (with critique injected)

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
    "You are a quality reviewer for an automation framework advisor. "
    "You will receive the user's original question and a draft response. "
    "Critique the draft and decide if it is ready to send.\n\n"
    "A response PASSES if it:\n"
    "  - Directly answers the user's question\n"
    "  - Names specific frameworks relevant to what was asked\n"
    "  - Is complete — no obvious missing sections\n\n"
    "A response NEEDS REVISION if it:\n"
    "  - Says it cannot answer or cannot find relevant data\n"
    "  - Lists frameworks that are NOT relevant to the question "
    "(e.g. lists web UI frameworks when user asked about performance testing)\n"
    "  - Is vague, generic, or hallucinates scores\n"
    "  - Misses a key part of the question\n\n"
    "If NEEDS REVISION: in your critique, explicitly state what the correct answer "
    "should cover so the evaluation agent can fix it.\n\n"
    "Respond in this exact format:\n"
    "VERDICT: <pass|revise>\n"
    "CRITIQUE: <one or two sentences of specific feedback, or 'none' if passing>"
)

_MAX_REFLECTIONS = 2


@dataclass
class ReflectionResult:
    approved: bool
    critique: str


class ReflectionAgent:
    """Critiques the draft response and returns approve/revise + feedback.

    Maintains memory of reflection cycles for context across revision rounds.
    """

    def __init__(self, llm_client=None) -> None:
        self._client = llm_client
        self._memory = AgentMemory(agent_name="reflection_agent", max_entries=10)

    @llm_retry(max_retries=2, initial_wait=0.5)
    def _call_llm(self, prompt: str) -> str:
        """LLM call with retry policy for transient failures."""
        result = self._client.chat(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM_PROMPT,
            max_tokens=50,
        )
        return result.get("content", "").strip()

    def reflect(
        self,
        user_message: str,
        draft_response: str,
        reflection_count: int = 0,
    ) -> ReflectionResult:
        """Critique the draft response.

        Args:
            user_message: Original user query.
            draft_response: Response produced by EvaluationAgent.
            reflection_count: How many reflection cycles have run (prevents loops).
        """
        if reflection_count >= _MAX_REFLECTIONS:
            logger.info("ReflectionAgent: max reflections reached — approving")
            return ReflectionResult(approved=True, critique="Max reflections reached.")

        # Include memory context for multi-round reflections
        memory_ctx = self._memory.get_context_string(last_n=3)

        prompt = (
            f"User question: {user_message}\n\n"
            f"Draft response:\n{draft_response}\n\n"
        )
        if memory_ctx:
            prompt += f"\n{memory_ctx}\n\n"
        prompt += "Review the draft and respond in the required format."

        try:
            raw = self._call_llm(prompt)
            result = self._parse(raw)
        except Exception as exc:
            logger.warning("ReflectionAgent: LLM call failed after retries (%s) — approving draft", exc)
            result = ReflectionResult(approved=True, critique="Reflection unavailable.")

        # Store in memory for context in subsequent reflection rounds
        self._memory.add(
            "reflection",
            f"round={reflection_count} approved={result.approved} critique='{result.critique[:80]}'",
            {"approved": result.approved, "round": reflection_count},
        )

        return result

    def run_node(self, state: AgentState) -> dict[str, Any]:
        """LangGraph node entry point — reads/writes from shared state."""
        result = self.reflect(
            user_message=state["user_message"],
            draft_response=state.get("final_response", ""),
            reflection_count=state.get("reflection_count", 0),
        )
        return {
            "reflection_critique": result.critique,
            "reflection_approved": result.approved,
            "reflection_count": state.get("reflection_count", 0) + 1,
        }

    @property
    def memory(self) -> AgentMemory:
        return self._memory

    # ------------------------------------------------------------------

    @staticmethod
    def _parse(raw: str) -> ReflectionResult:
        verdict, critique = "pass", "none"
        for line in raw.splitlines():
            line = line.strip()
            if line.upper().startswith("VERDICT:"):
                verdict = line.split(":", 1)[1].strip().lower()
            elif line.upper().startswith("CRITIQUE:"):
                critique = line.split(":", 1)[1].strip()

        approved = verdict != "revise"
        logger.info("ReflectionAgent: verdict=%s critique='%.100s'", verdict, critique)
        return ReflectionResult(approved=approved, critique=critique)
