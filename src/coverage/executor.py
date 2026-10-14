"""CoverageExecutor — small feature-owned dispatcher for coverage tools.

Extracted from the former monolithic ``src.tools.executor.ToolExecutor``.
Owns exactly the tools concerned with mapping test cases onto framework
capabilities: ``analyze_test_case_coverage`` and ``analyze_uploaded_content``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.knowledge_base.loader import KnowledgeBase

logger = logging.getLogger(__name__)


class CoverageExecutor:
    """Executes coverage-related tool calls by name."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase | None = None,
        uploaded_docs_context: str = "",
        case_study_context: str = "",
    ) -> None:
        if knowledge_base is None:
            from src.knowledge_base import KnowledgeBase as _KB
            knowledge_base = _KB(data_dir="data/frameworks")
            knowledge_base.load()
        self.kb = knowledge_base
        self.uploaded_docs = uploaded_docs_context
        self.case_study = case_study_context

        self.tools: dict[str, Callable] = {
            "analyze_test_case_coverage": self._analyze_test_case_coverage,
            "analyze_uploaded_content": self._analyze_uploaded_content,
        }

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name not in self.tools:
            logger.error("CoverageExecutor: unknown tool '%s'", tool_name)
            return f"Error: unknown tool '{tool_name}'"
        try:
            result = self.tools[tool_name](**arguments)
            logger.info("CoverageExecutor: '%s' -> %d chars", tool_name, len(str(result)))
            return result
        except Exception as exc:
            logger.error("CoverageExecutor: '%s' failed - %s: %s", tool_name, type(exc).__name__, exc)
            return f"Error executing {tool_name}: {exc}"

    def _analyze_test_case_coverage(
        self,
        test_cases: list[dict],
        frameworks: list[str] | None = None,
        prerequisites: list[dict] | None = None,
    ) -> str:
        """Delegates to coverage.engine — fully generic, YAML-derived."""
        from src.coverage.engine import analyze_coverage, render_coverage_report
        from src.tools.formatters import find_case_study_relevance
        analysis = analyze_coverage(test_cases, self.kb, frameworks)
        report = render_coverage_report(analysis, len(test_cases))
        note = find_case_study_relevance(
            self.case_study, list({d["capability"] for d in analysis["matrix"].values()})
        )
        return report + ("\n" + note if note else "")

    def _analyze_uploaded_content(self, search_term: str, document_type: str = "all") -> str:
        results = []
        term = search_term.lower()
        if document_type in ("test_files", "all") and self.uploaded_docs:
            matches = [l for l in self.uploaded_docs.split("\n") if term in l.lower()][:10]
            if matches:
                results.append(f"**Test Files ({len(matches)} matches):**")
                results.extend(matches)
        if document_type in ("case_study", "all") and self.case_study:
            matches = [l for l in self.case_study.split("\n") if term in l.lower()][:10]
            if matches:
                results.append(f"\n**Case Study ({len(matches)} matches):**")
                results.extend(matches)
        return "\n".join(results) if results else f"No matches found for '{search_term}'."
