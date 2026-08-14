"""Format Agent — takes the evaluation agent's raw response and formats it
for display on the dashboard (markdown, tables, structured sections).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FormatResult:
    formatted: str
    format_type: str   # "markdown" | "table" | "plain"


class FormatAgent:
    """Applies consistent formatting to the evaluation response."""

    def format(self, raw_response: str, user_message: str) -> FormatResult:
        msg_lower = user_message.lower()

        # Detect what kind of output is expected
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

    # ------------------------------------------------------------------
    # Private helpers
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
