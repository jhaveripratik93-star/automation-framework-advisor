"""Agent Orchestrator — 5-agent pipeline with synthesis feedback loop.

Pipeline:
  user query
    → DecisionAgent        (tool_call | direct)
    → ToolSelectionAgent   (which tools + args)
    → ToolExecutor         (runs tools, returns raw results)
    → SynthesisAgent       (LLM compares/validates tool output, decides if more tools needed)
      ↳ if needs_more → back to ToolSelectionAgent (max 1 extra round)
    → EvaluationAgent      (LLM synthesises final answer)
    → FormatAgent          (markdown formatting)
    → returns final str    (no HITL — response goes straight to chat)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.graph.graphrag_engine import GraphRAGEngine
    from src.knowledge_base.loader import KnowledgeBase
    from src.graph import KnowledgeGraph
    from src.scoring.models import DecisionMatrix
    from src.models import UserProfile

from src.agents.decision_agent import DecisionAgent
from src.agents.tool_selection_agent import ToolSelectionAgent
from src.agents.evaluation_agent import EvaluationAgent
from src.agents.format_agent import FormatAgent
from src.agents.synthesis_agent import SynthesisAgent
from src.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

_MAX_SYNTHESIS_ROUNDS = 2


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
        self._format_agent = FormatAgent()

        self._profile: UserProfile | None = None
        self._matrix: DecisionMatrix | None = None

    def set_context(self, profile: UserProfile | None, matrix: DecisionMatrix | None) -> None:
        self._profile = profile
        self._matrix = matrix

    # ------------------------------------------------------------------
    # Main pipeline entry point
    # ------------------------------------------------------------------

    def run(
        self,
        user_message: str,
        uploaded_docs: str = "",
        case_study: str = "",
    ) -> str:
        """Execute the full pipeline and return the final response string."""
        logger.info("AgentOrchestrator.run: START query='%.80s'", user_message)

        # ── Step 1: Retrieve graph context ───────────────────────────
        graph_context = self._graphrag.retrieve_context(user_message)

        # ── Step 2: Decision Agent ────────────────────────────────────
        decision = self._decision_agent.decide(user_message, graph_context)
        logger.info("AgentOrchestrator: decision=%s (%s)", decision.action, decision.reasoning)

        tool_results: list[dict[str, Any]] = []

        if decision.action == "tool_call" and self._kg:
            tool_executor = ToolExecutor(
                knowledge_graph=self._kg,
                graphrag_engine=self._graphrag,
                knowledge_base=self._kb,
                uploaded_docs_context=uploaded_docs,
                case_study_context=case_study,
            )
            available_tools = list(tool_executor.tools.keys())

            # ── Step 3: Tool Selection + Execution (with synthesis loop) ──
            for round_num in range(_MAX_SYNTHESIS_ROUNDS):
                selection = self._tool_selection_agent.select(user_message, available_tools)
                logger.info("AgentOrchestrator: round=%d selected %d tool(s)", round_num + 1, len(selection.tool_calls))

                round_results: list[dict[str, Any]] = []
                for tc in selection.tool_calls:
                    result_str = tool_executor.execute(tc.tool_name, tc.arguments)
                    round_results.append({"tool_name": tc.tool_name, "result": result_str})
                    logger.info("AgentOrchestrator: tool='%s' result_len=%d", tc.tool_name, len(result_str))

                tool_results.extend(round_results)

                # ── Step 4: Synthesis Agent — validate & decide if more tools needed ──
                synthesis = self._synthesis_agent.synthesise(
                    user_message=user_message,
                    tool_results=round_results,
                    round_num=round_num,
                )
                logger.info(
                    "AgentOrchestrator: synthesis round=%d needs_more=%s verdict_len=%d",
                    round_num + 1, synthesis.needs_more_tools, len(synthesis.verdict),
                )

                if not synthesis.needs_more_tools or round_num + 1 >= _MAX_SYNTHESIS_ROUNDS:
                    break

                # Feed synthesis verdict back as context hint for next tool selection
                user_message = f"{user_message}\n\n[Synthesis feedback]: {synthesis.verdict}"

        # ── Step 5: Evaluation Agent ──────────────────────────────────
        profile_ctx = self._build_profile_context()
        eval_result = self._evaluation_agent.evaluate(
            user_message=user_message,
            tool_results=tool_results,
            graph_context=graph_context,
            profile_context=profile_ctx,
        )

        # ── Step 6: Format Agent ──────────────────────────────────────
        fmt_result = self._format_agent.format(eval_result.response, user_message)

        logger.info("AgentOrchestrator.run: DONE response_len=%d", len(fmt_result.formatted))
        return fmt_result.formatted

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_profile_context(self) -> str:
        if not self._profile:
            return ""
        p = self._profile
        parts = [
            f"language={p.primary_language}",
            f"team={p.team_size}",
            f"ci_cd={p.ci_cd_tool}",
            f"legacy_scripts={p.legacy_test_count}",
        ]
        if self._matrix and self._matrix.rankings:
            top = self._matrix.rankings[0]
            parts.append(f"top_recommendation={top.framework}({top.overall_score}/100)")
        return ", ".join(parts)
