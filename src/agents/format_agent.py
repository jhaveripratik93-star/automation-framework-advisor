"""Format Agent — takes the evaluation agent's raw response and formats it
for display on the dashboard (pure markdown, no HTML, compact layout).

Integrates with LangGraph state. Uses LLM formatting when available,
falls back to deterministic formatting logic. Strips all HTML tags and
produces clean, compact markdown output.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents.state import AgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a formatting agent for an automation framework advisor. "
    "Reformat the raw response into clean, compact markdown. Rules:\n"
    "- Use markdown ONLY (no HTML tags whatsoever — no <br>, <b>, <table>, etc.)\n"
    "- Use markdown tables (| col | col |) for comparisons\n"
    "- Use numbered lists for migration phases/steps\n"
    "- Use **bold** for emphasis, not HTML\n"
    "- Keep it compact: no excessive blank lines, no redundant headings\n"
    "- Use ### for main sections, avoid #### or deeper nesting\n"
    "- Do not add or remove factual content — only improve structure\n"
    "- Aim for brevity: collapse verbose sentences, remove filler"
)


@dataclass
class FormatResult:
    formatted: str
    format_type: str   # "markdown" | "table" | "plain"


class FormatAgent:
    """Applies consistent formatting to the evaluation response.

    Guarantees:
    - No HTML tags in output (all stripped/converted)
    - Compact layout with minimal whitespace
    - Clean markdown structure
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

        # Final pass: strip HTML, fix tables, and compact whitespace
        formatted = self._strip_html(formatted)
        formatted = self._fix_table_cells(formatted)
        formatted = self._compact(formatted)

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

            # Always strip HTML, fix tables, and compact — even from LLM output
            formatted = self._strip_html(formatted)
            formatted = self._fix_table_cells(formatted)
            formatted = self._compact(formatted)

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
    # HTML stripping and cleanup
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_html(text: str) -> str:
        """Remove all HTML tags, converting common ones to markdown equivalents."""
        # Convert common HTML to markdown before stripping
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</?p>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<i>(.*?)</i>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<em>(.*?)</em>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<h[1-3][^>]*>(.*?)</h[1-3]>", r"### \1", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<h[4-6][^>]*>(.*?)</h[4-6]>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<li>(.*?)</li>", r"\n- \1", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"</?[uo]l>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<a[^>]*href=[\"'](.*?)[\"'][^>]*>(.*?)</a>", r"[\2](\1)", text, flags=re.IGNORECASE | re.DOTALL)

        # Strip any remaining HTML tags
        text = re.sub(r"<[^>]+>", "", text)

        # Decode common HTML entities (after tag removal so decoded content isn't re-matched)
        text = text.replace("&amp;", "&")
        text = text.replace("&lt;", "<")
        text = text.replace("&gt;", ">")
        text = text.replace("&nbsp;", " ")
        text = text.replace("&quot;", '"')
        text = text.replace("&#39;", "'")
        # Decode numeric entities
        text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)

        return text

    @staticmethod
    def _fix_table_cells(text: str) -> str:
        """Fix malformed markdown tables where cells span multiple lines.

        Ensures each table row is a single line by:
        - Merging continuation lines into the table row above
        - Removing bullet characters (•, -, *) from within cells
        - Collapsing multi-line cell content into comma-separated items
        """
        lines = text.split("\n")
        result_lines = []
        in_table = False

        i = 0
        while i < len(lines):
            stripped = lines[i].strip()

            # Detect table rows
            is_table_row = stripped.startswith("|") and "|" in stripped[1:]
            is_separator = is_table_row and all(c in "|-: " for c in stripped.replace("|", "").strip())

            if is_table_row:
                in_table = True

                if is_separator:
                    result_lines.append(stripped)
                    i += 1
                    continue

                # Collect continuation lines (non-empty, non-table-start, non-heading)
                row_content = stripped
                i += 1
                while i < len(lines):
                    next_stripped = lines[i].strip()
                    # Stop if next line is a table row, separator, heading, or empty
                    if not next_stripped:
                        break
                    if next_stripped.startswith("|") and "|" in next_stripped[1:]:
                        break
                    if next_stripped.startswith("#"):
                        break
                    # Merge continuation into the last cell
                    continuation = next_stripped.lstrip("•·▪*- ").strip()
                    if continuation:
                        # Append to the content before the final |
                        if row_content.rstrip().endswith("|"):
                            row_content = row_content.rstrip()[:-1].rstrip() + ", " + continuation + " |"
                        else:
                            row_content += ", " + continuation
                    i += 1

                # Clean bullet characters from within cells
                row_content = re.sub(r"\s*[•·▪]\s*", " ", row_content)
                # Clean leading bullets at cell boundaries (| • text → | text)
                row_content = re.sub(r"\|\s*[•·▪*-]\s+", "| ", row_content)
                # Collapse multiple spaces
                row_content = re.sub(r"  +", " ", row_content)

                result_lines.append(row_content)
            else:
                in_table = False if not stripped else in_table
                result_lines.append(lines[i])
                i += 1

        return "\n".join(result_lines)

    @staticmethod
    def _compact(text: str) -> str:
        """Remove excessive blank lines and trailing whitespace for a compact layout."""
        # Remove trailing whitespace from each line
        lines = [line.rstrip() for line in text.splitlines()]

        # Collapse 3+ consecutive blank lines into 1
        compacted = []
        blank_count = 0
        for line in lines:
            if line == "":
                blank_count += 1
                if blank_count <= 1:
                    compacted.append(line)
            else:
                blank_count = 0
                compacted.append(line)

        # Strip leading/trailing blank lines
        result = "\n".join(compacted).strip()

        # Ensure response ends with a complete sentence (not truncated mid-word)
        result = FormatAgent._ensure_complete_ending(result)

        return result

    @staticmethod
    def _ensure_complete_ending(text: str) -> str:
        """Ensure the response ends with a complete sentence, not a truncated fragment.

        If the text appears cut off (no terminal punctuation), trims back to the
        last complete sentence. Preserves markdown tables and list items as-is.
        """
        if not text:
            return text

        # Don't touch text that ends with a markdown table row or list
        last_line = text.rstrip().rsplit("\n", 1)[-1].strip()
        if last_line.startswith("|") or last_line.startswith("-") or last_line.startswith("*"):
            return text

        # Check if text ends with sentence-terminal punctuation
        stripped = text.rstrip()
        if stripped and stripped[-1] in ".!?:)\"'`":
            return text

        # Text appears truncated — find the last complete sentence
        # Look for the last sentence-ending punctuation followed by a space or newline
        import re
        # Find last position of sentence-ending punctuation (. ! ?) not inside a table
        matches = list(re.finditer(r'[.!?](?:\s|$)', stripped))
        if matches:
            last_match = matches[-1]
            # Trim to end of last complete sentence
            trimmed = stripped[:last_match.end()].rstrip()
            if len(trimmed) > len(stripped) * 0.5:  # Only trim if we keep >50% of content
                return trimmed

        # Can't find a clean cut point — return as-is rather than losing content
        return text

    # ------------------------------------------------------------------
    # Deterministic formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_markdown(text: str) -> str:
        """Ensure the text starts with a heading if it doesn't already."""
        stripped = text.strip()
        if stripped and not stripped.startswith("#"):
            stripped = "### Response\n" + stripped
        return stripped

    @staticmethod
    def _ensure_table(text: str) -> str:
        """If the response already has a markdown table or heading, return as-is.
        Otherwise wrap in a simple structure."""
        stripped = text.strip()
        if "|" in stripped and "---" in stripped:
            return stripped
        if stripped.startswith("#"):
            return stripped
        return "### Comparison\n" + stripped

    @staticmethod
    def _ensure_phases(text: str) -> str:
        """Ensure migration responses have phase headings."""
        if "Phase" in text or "phase" in text:
            return text.strip()
        return "### Migration Plan\n" + text.strip()
