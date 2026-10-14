"""ToolRouter — composes the per-feature executors into the single
dispatch surface the LangGraph pipeline and MCP server expect.

This is pipeline-dispatch INFRASTRUCTURE, not feature logic — it belongs
here (src/agents/) rather than in any one feature package, the same way
the old monolithic ``src.tools.executor.ToolExecutor`` used to serve as
the pipeline's single tool-call entry point. The actual tool
implementations now live in the feature-owned packages:

  src/advisor_tools/   search_knowledge_graph, get_framework_details,
                       run_framework_comparison, recommend_frameworks,
                       score_frameworks, list_frameworks_by_category,
                       find_migration_paths
  src/coverage/        analyze_test_case_coverage, analyze_uploaded_content
  src/prerequisites/   analyze_prerequisites

ToolRouter exposes the same ``.tools`` dict and ``.execute(name, args)``
contract the pipeline (langgraph_pipeline.py) and tool_selection_agent.py
were already written against, so neither needed to change — only their
import of ``ToolExecutor`` became an import of ``ToolRouter``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from src.advisor_tools.executor import AdvisorToolsExecutor
from src.coverage.executor import CoverageExecutor
from src.prerequisites.executor import PrerequisitesExecutor
from src.scoring.weights import WeightProfile

if TYPE_CHECKING:
    from src.graph import KnowledgeGraph
    from src.graph.graphrag_engine import GraphRAGEngine
    from src.knowledge_base.loader import KnowledgeBase

logger = logging.getLogger(__name__)


class ToolRouter:
    """Routes a tool-call name to whichever feature executor owns it."""

    # Tools whose output depends on the active scoring weights. Their cache
    # entries must be keyed on the weight signature, otherwise changing the
    # weights and re-asking the same query returns a stale ranking.
    _WEIGHT_SENSITIVE_TOOLS = frozenset({"recommend_frameworks", "score_frameworks"})

    def __init__(
        self,
        knowledge_graph: KnowledgeGraph | None = None,
        graphrag_engine: GraphRAGEngine | None = None,
        knowledge_base: KnowledgeBase | None = None,
        uploaded_docs_context: str = "",
        case_study_context: str = "",
        weight_profile: WeightProfile | None = None,
    ) -> None:
        if knowledge_base is None:
            from src.knowledge_base import KnowledgeBase as _KB
            knowledge_base = _KB(data_dir="data/frameworks")
            knowledge_base.load()
        self.kb = knowledge_base

        if knowledge_graph is None:
            from src.graph import load_or_seed_graph
            knowledge_graph = load_or_seed_graph(self.kb)
        self.kg = knowledge_graph

        self.graphrag = graphrag_engine
        self.uploaded_docs = uploaded_docs_context
        self.case_study = case_study_context
        self.weight_profile = weight_profile or WeightProfile.default()
        self._llm: Any | None = None  # kept for compatibility with callers that set it

        self._advisor = AdvisorToolsExecutor(
            knowledge_graph=self.kg,
            graphrag_engine=self.graphrag,
            knowledge_base=self.kb,
            case_study_context=self.case_study,
            weight_profile=self.weight_profile,
        )
        self._coverage = CoverageExecutor(
            knowledge_base=self.kb,
            uploaded_docs_context=self.uploaded_docs,
            case_study_context=self.case_study,
        )
        self._prerequisites = PrerequisitesExecutor(knowledge_base=self.kb)

        # Merge each feature executor's tool dict into one flat lookup —
        # callers (langgraph_pipeline, tool_selection_agent) only ever see
        # the combined surface, never which executor owns what.
        self.tools: dict[str, Callable] = {
            **self._advisor.tools,
            **self._coverage.tools,
            **self._prerequisites.tools,
        }

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        from src.agents.cache import tool_cache, make_tool_cache_key, weight_signature

        if tool_name not in self.tools:
            logger.error("ToolRouter: unknown tool '%s'", tool_name)
            return f"Error: unknown tool '{tool_name}'"

        weight_sig = (
            weight_signature(self.weight_profile)
            if tool_name in self._WEIGHT_SENSITIVE_TOOLS else ""
        )
        cache_key = make_tool_cache_key(tool_name, arguments, weight_sig)
        cached = tool_cache.get(cache_key)
        if cached is not None:
            logger.info("ToolRouter: CACHE HIT '%s' -> %d chars", tool_name, len(cached))
            return cached

        try:
            result = self.tools[tool_name](**arguments)
            logger.info("ToolRouter: '%s' -> %d chars", tool_name, len(str(result)))
            tool_cache.set(cache_key, result)
            return result
        except Exception as exc:
            logger.error("ToolRouter: '%s' failed - %s: %s", tool_name, type(exc).__name__, exc)
            return f"Error executing {tool_name}: {exc}"
