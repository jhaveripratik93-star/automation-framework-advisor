"""Format Agent — uses the LLM to format the evaluation response for display."""
from __future__ import annotations

import logging
from dataclasses import dataclass

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
    format_type: str = "markdown"


class FormatAgent:
    def __init__(self, llm_client=None) -> None:
        self._client = llm_client

    def format(self, raw_response: str, user_message: str) -> FormatResult:
        if not self._client or not raw_response.strip():
            return FormatResult(formatted=raw_response, format_type="markdown")

        prompt = (
            f"Original user question: {user_message}\n\n"
            f"Raw response to format:\n{raw_response}"
        )
        try:
            result = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system=_SYSTEM_PROMPT,
            )
            formatted = result.get("content", "").strip() or raw_response
        except Exception as exc:
            logger.warning("FormatAgent: LLM call failed (%s) — returning raw response", exc)
            formatted = raw_response

        logger.info("FormatAgent: output_len=%d", len(formatted))
        return FormatResult(formatted=formatted, format_type="markdown")
