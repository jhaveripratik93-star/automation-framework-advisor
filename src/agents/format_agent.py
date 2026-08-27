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
    "Reformat the response into clean, compact markdown. Rules:\n"
    "- Use tables for comparisons, numbered phases for migration plans, "
    "and clear headings throughout. "
    "- Output ONLY the reformatted response — no preamble, no repetition of the question"
    "- Use markdown ONLY (no HTML tags whatsoever — no <br>, <b>, <table>, etc.)\n"
    "- Use markdown tables (| col | col |) for comparisons\n"
    "- Use numbered lists for migration phases/steps\n"
    "- Use **bold** for emphasis, not HTML\n"
    "- Keep it compact: no excessive blank lines, no redundant headings\n"
    "- Use ### for main sections, avoid #### or deeper nesting\n"
    "- Do not add or remove factual content — only improve structure\n"
    "- Aim for brevity: collapse verbose sentences, remove filler\n"
    "- Do NOT add TLDR, summaries, or any content not in the original response"
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

        # Strip TL;DR headings early — before any other formatting
        raw_response = self._strip_tldr(raw_response)

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
        formatted = self._strip_tldr(formatted)
        formatted = self._fix_table_cells(formatted)
        formatted = self._compact(formatted)

        logger.info("FormatAgent: fmt_type=%s output_len=%d", fmt_type, len(formatted))
        return FormatResult(formatted=formatted, format_type=fmt_type)

    def run_node(self, state: AgentState) -> dict[str, Any]:
        """LangGraph node entry point — reads/writes from shared state."""
        raw_response = state.get("evaluation_response", state.get("final_response", ""))
        user_message = state["user_message"]
        result = self.format(raw_response, user_message)

        weight_profile = state.get("weight_profile")
        already_has_scoring = any(
            marker in result.formatted
            for marker in ("Weighted Score", "Weight-Based", "Weighted score", "weighted score")
        )
        logger.info(
            "FormatAgent.run_node: weight_profile=%s already_has_scoring=%s response_len=%d",
            weight_profile.profile_name if weight_profile else None,
            already_has_scoring,
            len(result.formatted),
        )
        summary = "" if already_has_scoring else self._build_weight_summary(
            weight_profile,
            llm_response=result.formatted,
            user_profile=state.get("user_profile"),
            profile_context=state.get("profile_context", ""),
        )
        logger.info("FormatAgent.run_node: summary_len=%d", len(summary))
        final = result.formatted + ("\n\n" + summary if summary else "")
        return {"final_response": final}

    # ------------------------------------------------------------------
    # Weight-based summary (appended after LLM response)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_profile_context(profile_context: str):
        """Parse the profile_context string into a UserProfile.

        profile_context format: "language=python, team=5, ci_cd=github_actions, ..."
        Falls back to safe defaults for any missing field.
        """
        from src.models import UserProfile, ArchitectureType, ExperienceLevel

        kv: dict[str, str] = {}
        for part in profile_context.split(","):
            part = part.strip()
            if "=" in part:
                k, _, v = part.partition("=")
                kv[k.strip()] = v.strip()

        # architecture
        _arch_map = {
            "web_spa": ArchitectureType.WEB_SPA,
            "web_mpa": ArchitectureType.WEB_MPA,
            "api_only": ArchitectureType.API_ONLY,
            "microservices": ArchitectureType.MICROSERVICES,
            "native_mobile": ArchitectureType.NATIVE_MOBILE,
            "hybrid_mobile": ArchitectureType.HYBRID_MOBILE,
            "desktop": ArchitectureType.DESKTOP,
            "cloud_infrastructure": ArchitectureType.CLOUD_INFRASTRUCTURE,
        }
        arch_val = kv.get("architecture", "web_spa").lower()
        arch = _arch_map.get(arch_val, ArchitectureType.WEB_SPA)

        # experience
        _exp_map = {
            "beginner": ExperienceLevel.BEGINNER,
            "intermediate": ExperienceLevel.INTERMEDIATE,
            "advanced": ExperienceLevel.ADVANCED,
            "expert": ExperienceLevel.EXPERT,
        }
        exp = _exp_map.get(kv.get("experience", "intermediate").lower(), ExperienceLevel.INTERMEDIATE)

        try:
            team_size = int(kv.get("team", "5"))
        except ValueError:
            team_size = 5

        try:
            legacy_count = int(kv.get("legacy_scripts", "0"))
        except ValueError:
            legacy_count = 0

        return UserProfile(
            project_name="weight_summary",
            architecture_types=[arch],
            primary_language=kv.get("language", "python"),
            team_size=max(1, team_size),
            automation_experience=exp,
            ci_cd_tool=kv.get("ci_cd", "github_actions"),
            legacy_test_count=legacy_count,
        )

    @staticmethod
    def _build_weight_summary(
        weight_profile,
        llm_response: str = "",
        user_profile=None,
        profile_context: str = "",
    ) -> str:
        """Score only the frameworks mentioned in the LLM response.
        Returns empty string if weight_profile is None, no frameworks found, or scoring fails.
        """
        if weight_profile is None:
            logger.warning("FormatAgent._build_weight_summary: weight_profile is None — skipping")
            return ""
        try:
            from src.scoring.engine import ScoringEngine
            from src.models import UserProfile
            from src.knowledge_base import KnowledgeBase

            kb = KnowledgeBase(data_dir="data/frameworks")
            kb.load()

            # Extract only framework names that appear in the LLM response
            response_lower = llm_response.lower()
            mentioned = [
                fw for fw in kb.list_all()
                if fw.framework_name.lower() in response_lower
            ]
            if not mentioned:
                logger.info("FormatAgent._build_weight_summary: no frameworks mentioned in response — skipping")
                return ""

            if user_profile is None:
                user_profile = FormatAgent._parse_profile_context(profile_context)

            engine = ScoringEngine(knowledge_base=kb, weight_profile=weight_profile)
            matrix = engine.evaluate(user_profile)

            # Keep only rankings for mentioned frameworks, sorted by score
            mentioned_names = {fw.framework_name.lower() for fw in mentioned}
            rankings = [
                r for r in matrix.rankings
                if r.framework.lower() in mentioned_names
            ]
            if not rankings:
                return ""

            # Re-rank from 1 within this subset
            for i, r in enumerate(rankings):
                r.rank = i + 1

            _labels = {
                "C1_language_compatibility": "Language",
                "C2_api_validation": "API",
                "C3_performance_load": "Perf",
                "C4_cicd_integration": "CI/CD",
                "C5_maintainability": "Maint.",
                "C6_cloud_readiness": "Cloud",
                "C7_license_cost": "Cost",
            }
            sorted_w = sorted(
                [(lbl, weight_profile.get(cid)) for cid, lbl in _labels.items() if weight_profile.get(cid) > 0],
                key=lambda x: -x[1],
            )
            priority_str = " > ".join(f"{lbl}({w:.0%})" for lbl, w in sorted_w)

            lines = [
                "---",
                f"### ⚖️ Weight-Based Ranking *(preset: {weight_profile.profile_name})*",
                f"**Priorities:** {priority_str}",
                "",
                "| Rank | Framework | Score | Top Strength | Key Weakness |",
                "|---|---|---|---|---|",
            ]
            for r in rankings:
                pro = r.pros[0] if r.pros else "—"
                con = r.cons[0] if r.cons else "—"
                lines.append(f"| {r.rank} | **{r.framework}** | {r.overall_score}/100 | {pro} | {con} |")

            logger.info(
                "FormatAgent._build_weight_summary: scored %d framework(s): %s",
                len(rankings), [r.framework for r in rankings],
            )
            return "\n".join(lines)
        except Exception:
            logger.warning("FormatAgent: weight summary generation failed", exc_info=True)
            return ""

    # ------------------------------------------------------------------
    # LLM-based formatting
    # ------------------------------------------------------------------

    def _format_with_llm(self, raw_response: str, user_message: str) -> FormatResult | None:
        """Use the LLM to format the response. Returns None on failure."""
        prompt = f"Response to format:\n\n{raw_response}"
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
            formatted = self._strip_tldr(formatted)
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
    def _strip_tldr(text: str) -> str:
        """Remove TL;DR / TLDR headings that the LLM sometimes generates."""
        # Remove markdown headings: "## TL;DR", "### TLDR:", etc.
        text = re.sub(r"^#{1,4}\s*(?:TL;?DR|TLDR)\s*:?\s*\n?", "", text, flags=re.MULTILINE | re.IGNORECASE)
        # Remove bold-wrapped: "**TL;DR:**", "**TLDR**:", "**TL;DR**", etc.
        text = re.sub(r"\*{1,2}(?:TL;?DR|TLDR):?\*{1,2}\s*:?\s*", "", text, flags=re.IGNORECASE)
        # Remove plain "TL;DR:" at the start of a line
        text = re.sub(r"^(?:TL;?DR|TLDR)\s*:\s*", "", text, flags=re.MULTILINE | re.IGNORECASE)
        return text

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

        return result

    @staticmethod
    def _ensure_complete_ending(text: str) -> str:
        """No-op — kept for compatibility."""
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
