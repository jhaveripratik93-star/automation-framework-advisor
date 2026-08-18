"""Reflection Agent — critiques the draft response from EvaluationAgent and
either approves it or requests a revision with specific feedback.

Sits between evaluate → format in the pipeline:
  evaluate → reflect → [approve] → format
                     → [revise]  → evaluate (with critique injected)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a quality reviewer for an automation framework advisor. "
    "You will receive the user's original question and a draft response. "
    "Critique the draft and decide if it is ready to send.\n\n"
    "A response PASSES if it:\n"
    "  - Directly answers the user's question\n"
    "  - Uses specific framework names, versions, or data (not vague generalities)\n"
    "  - Is complete — no obvious missing sections\n\n"
    "A response NEEDS REVISION if it:\n"
    "  - Is vague, generic, or hallucinates framework data\n"
    "  - Misses a key part of the question\n"
    "  - Contains contradictions\n\n"
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
    """Critiques the draft response and returns approve/revise + feedback."""

    def __init__(self, llm_client=None) -> None:
        self._client = llm_client

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

        prompt = (
            f"User question: {user_message}\n\n"
            f"Draft response:\n{draft_response}\n\n"
            "Review the draft and respond in the required format."
        )
        try:
            result = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system=_SYSTEM_PROMPT,
            )
            raw = result.get("content", "").strip()
            return self._parse(raw)
        except Exception as exc:
            logger.warning("ReflectionAgent: LLM call failed (%s) — approving draft", exc)
            return ReflectionResult(approved=True, critique="Reflection unavailable.")

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
