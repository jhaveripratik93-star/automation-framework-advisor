"""Agent Orchestrator — LangGraph-powered 5-agent pipeline.

Graph (defined in langgraph_pipeline.py):
  decide → [tool_call] → select_tools → execute_tools → synthesise
                                              ↑ (needs_more, max 2 rounds)
         → [direct]   ──────────────────────────────────────────────→ evaluate → format
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.graph.graphrag_engine import GraphRAGEngine
    from src.knowledge_base.loader import KnowledgeBase
    from src.graph import KnowledgeGraph

from src.agents.decision_agent import DecisionAgent
from src.agents.tool_selection_agent import ToolSelectionAgent
from src.agents.evaluation_agent import EvaluationAgent
from src.agents.format_agent import FormatAgent
from src.agents.reflection_agent import ReflectionAgent
from src.agents.synthesis_agent import SynthesisAgent
from src.agents.langgraph_pipeline import build_pipeline
from src.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Runs the full 5-agent pipeline and returns the final response string."""

    def __init__(
        self,
        llm_client,
        graphrag_engine: GraphRAGEngine,
        knowledge_base: KnowledgeBase,
        knowledge_graph: KnowledgeGraph | None = None,
    ) -> None:
        self._client = llm_client
        self._graphrag = graphrag_engine
        self._kb = knowledge_base
        self._kg = knowledge_graph

        self._decision_agent = DecisionAgent(llm_client=llm_client)
        self._tool_selection_agent = ToolSelectionAgent(llm_client=llm_client)
        self._synthesis_agent = SynthesisAgent(llm_client=llm_client)
        self._evaluation_agent = EvaluationAgent(llm_client=llm_client)
        self._reflection_agent = ReflectionAgent(llm_client=llm_client)
        self._format_agent = FormatAgent(llm_client=llm_client)

    # ------------------------------------------------------------------
    # Main pipeline entry point
    # ------------------------------------------------------------------

    def run(
        self,
        user_message: str,
        uploaded_docs: str = "",
        case_study: str = "",
        weight_context: str = "",
        weight_profile=None,
    ) -> str:
        """Execute the LangGraph pipeline and return the final response string."""
        logger.info("AgentOrchestrator.run: START query='%.80s'", user_message)

        graph_context = self._graphrag.retrieve_context(user_message)

        tool_executor = ToolExecutor(
            knowledge_graph=self._kg,
            graphrag_engine=self._graphrag,
            knowledge_base=self._kb,
            uploaded_docs_context=uploaded_docs,
            case_study_context=case_study,
            weight_profile=weight_profile,
        )

        pipeline = build_pipeline(
            decision_agent=self._decision_agent,
            tool_selection_agent=self._tool_selection_agent,
            tool_executor=tool_executor,
            synthesis_agent=self._synthesis_agent,
            evaluation_agent=self._evaluation_agent,
            reflection_agent=self._reflection_agent,
            format_agent=self._format_agent,
        )

        initial_state: dict[str, Any] = {
            "user_message": user_message,
            "graph_context": graph_context,
            "profile_context": weight_context,
            "uploaded_docs": uploaded_docs,
            "case_study": case_study,
            "action": "",
            "tool_results": [],
            "synthesis_verdict": "",
            "needs_more": False,
            "round_num": 0,
            "reflection_critique": "",
            "reflection_count": 0,
            "final_response": "",
        }

        result_state = pipeline.invoke(initial_state)
        response = result_state.get("final_response", "")
        logger.info("AgentOrchestrator.run: DONE response_len=%d", len(response))
        return response

