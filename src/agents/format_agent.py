"""Format Agent — takes the evaluation agent's raw response and formats it
for display on the dashboard (markdown, tables, structured sections).

Integrates with LangGraph state. Uses LLM formatting when available,
falls back to deterministic formatting logic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents.state import AgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a formatting agent for an automation framework advisor. "
    "Take the raw response and reformat it for clean markdown display. "
    "Use tables for comparisons, numbered phases for migration plans, "
    "and clear headings throughout. Do not add or remove factual content — "
    "only improve structure and readability."
)


@dataclass
class FormatResult:
    formatted: str
    format_type: str   # "markdown" | "table" | "plain"


class FormatAgent:
    """Applies consistent formatting to the evaluation response.

    When an LLM client is provided, uses it for rich formatting.
    Otherwise applies deterministic structure-detection formatting.
    """

    def __init__(self, llm_client=None) -> None:
        self._client = llm_client

    def format(self, raw_response: str, user_message: str) -> FormatResult:
        """Format the raw response for display.

        Args:
            raw_response: The evaluation agent's raw output.
            user_message: Original user query (used for intent detection).
        """
        if not raw_response.strip():
            return FormatResult(formatted=raw_response, format_type="markdown")

        # Try LLM-based formatting if client is available
        if self._client:
            llm_result = self._format_with_llm(raw_response, user_message)
            if llm_result is not None:
                return llm_result

        # Deterministic fallback: detect format type from user intent
        msg_lower = user_message.lower()

        if any(w in msg_lower for w in ["compare", "vs", "versus"]):
            formatted = self._ensure_table(raw_response)
            fmt_type = "table"
        elif any(w in msg_lower for w in ["migrate", "migration", "roadmap", "plan"]):
            formatted = self._ensure_phases(raw_response)
            fmt_type = "markdown"
        elif any(w in msg_lower for w in ["coverage", "test case"]):
            formatted = self._ensure_table(raw_response)
            fmt_type = "table"
        else:
            formatted = self._clean_markdown(raw_response)
            fmt_type = "markdown"

        logger.info("FormatAgent: fmt_type=%s output_len=%d", fmt_type, len(formatted))
        return FormatResult(formatted=formatted, format_type=fmt_type)

    def run_node(self, state: AgentState) -> dict[str, Any]:
        """LangGraph node entry point — reads/writes from shared state."""
        raw_response = state.get("evaluation_response", state.get("final_response", ""))
        user_message = state["user_message"]
        result = self.format(raw_response, user_message)
        return {"final_response": result.formatted}

    # ------------------------------------------------------------------
    # LLM-based formatting
    # ------------------------------------------------------------------

    def _format_with_llm(self, raw_response: str, user_message: str) -> FormatResult | None:
        """Use the LLM to format the response. Returns None on failure."""
        prompt = (
            f"Original user question: {user_message}\n\n"
            f"Raw response to format:\n{raw_response}"
        )
        try:
            result = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system=_SYSTEM_PROMPT,
            )
            formatted = result.get("content", "").strip()
            if not formatted:
                return None

            # Detect format type from output
            if "|" in formatted and "---" in formatted:
                fmt_type = "table"
            else:
                fmt_type = "markdown"

            logger.info("FormatAgent (LLM): fmt_type=%s output_len=%d", fmt_type, len(formatted))
            return FormatResult(formatted=formatted, format_type=fmt_type)

        except Exception as exc:
            logger.warning("FormatAgent: LLM call failed (%s) — using deterministic formatting", exc)
            return None

    # ------------------------------------------------------------------
    # Deterministic formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_markdown(text: str) -> str:
        """Ensure the text starts with a heading if it doesn't already."""
        stripped = text.strip()
        if stripped and not stripped.startswith("#"):
            stripped = "### Response\n\n" + stripped
        return stripped

    @staticmethod
    def _ensure_table(text: str) -> str:
        """If the response already has a markdown table, return as-is.
        Otherwise wrap in a simple structure."""
        if "|" in text and "---" in text:
            return text.strip()
        return "### Comparison\n\n" + text.strip()

    @staticmethod
    def _ensure_phases(text: str) -> str:
        """Ensure migration responses have phase headings."""
        if "Phase" in text or "phase" in text:
            return text.strip()
        return "### Migration Plan\n\n" + text.strip()
