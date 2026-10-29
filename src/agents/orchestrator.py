"""Agent Orchestrator — LangGraph-powered 5-agent pipeline with memory and streaming.

Graph (defined in langgraph_pipeline.py):
  decide → [tool_call] → select_tools → execute_tools → synthesise
                                              ↑ (needs_more, max 2 rounds)
         → [direct]   ──────────────────────────────────────────────→ evaluate → format
         → [rejected] ──────────────────────────────────────────────→ format (short-circuit)

Features:
  - LangGraph StateGraph orchestration with conditional edges
  - Shared conversation memory across pipeline invocations
  - Per-agent memory (decision, tool_selection, synthesis, evaluation, reflection)
  - Streaming callbacks for real-time progress visibility
  - Retry policies on all LLM calls (exponential backoff)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.graph import KnowledgeGraph
    from src.graph.graphrag_engine import GraphRAGEngine
    from src.knowledge_base.loader import KnowledgeBase

from src.agents.callbacks import PipelineCallbackHandler, logging_callback
from src.agents.decision_agent import DecisionAgent
from src.agents.evaluation_agent import EvaluationAgent
from src.agents.format_agent import FormatAgent
from src.agents.langgraph_pipeline import build_pipeline
from src.agents.memory import ConversationMemory
from src.agents.reflection_agent import ReflectionAgent
from src.agents.synthesis_agent import SynthesisAgent
from src.agents.tool_router import ToolRouter
from src.agents.tool_selection_agent import ToolSelectionAgent
from src.coverage.test_case_extraction import extract_test_cases_from_text

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Runs the full 5-agent pipeline and returns the final response string.

    Maintains conversation memory across invocations and emits streaming
    callbacks for real-time observability.
    """

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

        # ── Initialize agents ─────────────────────────────────────────
        self._decision_agent = DecisionAgent(llm_client=llm_client)
        self._tool_selection_agent = ToolSelectionAgent(llm_client=llm_client)
        self._synthesis_agent = SynthesisAgent(llm_client=llm_client)
        self._evaluation_agent = EvaluationAgent(llm_client=llm_client)
        self._reflection_agent = ReflectionAgent(llm_client=llm_client)
        self._format_agent = FormatAgent(llm_client=llm_client, knowledge_base=knowledge_base)

        # ── Conversation memory (persists across pipeline invocations) ─
        self._conversation_memory = ConversationMemory(max_turns=50)

        # ── Streaming callbacks ───────────────────────────────────────
        self._callback_handler = PipelineCallbackHandler()
        self._callback_handler.register(logging_callback)

        # ── User context (optional, set via set_context()) ────────────
        self._profile: Any = None
        self._matrix: Any = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def conversation_memory(self) -> ConversationMemory:
        """Access the shared conversation memory for external inspection."""
        return self._conversation_memory

    @property
    def callback_handler(self) -> PipelineCallbackHandler:
        """Access callback handler to add/remove stream listeners."""
        return self._callback_handler

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------

    def set_context(self, profile=None, matrix=None) -> None:
        """Set user profile and decision matrix for personalized recommendations.

        Args:
            profile: UserProfile instance (or None to clear).
            matrix: DecisionMatrix instance (or None to clear).
        """
        self._profile = profile
        self._matrix = matrix

    def _build_profile_context(self) -> str:
        """Build a profile context string from stored profile/matrix."""
        if not self._profile:
            return ""
        p = self._profile
        parts = []
        if hasattr(p, "primary_language"):
            parts.append(f"language={p.primary_language}")
        if hasattr(p, "team_size"):
            parts.append(f"team={p.team_size}")
        if hasattr(p, "ci_cd_tool"):
            parts.append(f"ci_cd={p.ci_cd_tool}")
        if hasattr(p, "legacy_test_count"):
            parts.append(f"legacy_scripts={p.legacy_test_count}")
        if self._matrix and hasattr(self._matrix, "rankings") and self._matrix.rankings:
            top = self._matrix.rankings[0]
            parts.append(f"top_recommendation={top.framework}({top.overall_score}/100)")
        return ", ".join(parts)

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
        """Execute the LangGraph pipeline and return the final response string.

        Response caching is handled inside the pipeline (Option 2): the cache is
        keyed on the resolved tool calls, so differently-phrased queries that
        resolve to the same tools share a cached response — no query-text
        keyword normalization needed.
        """
        logger.info("AgentOrchestrator.run: START query='%.80s'", user_message)

        self._callback_handler.on_node_start("orchestrator")

        # Parse any embedded test-case dataset structurally BEFORE any LLM
        # call — large uploads/pastes must never be echoed back by the LLM
        # as a tool-call argument (see src/coverage/test_case_extraction.py
        # for why: a 250-token tool-call response cap silently truncates
        # anything beyond ~1 full test case). The LLM only ever sees
        # `llm_message` (with the bulk JSON replaced by a short
        # placeholder); the real parsed list travels through state and is
        # substituted directly into analyze_test_case_coverage's arguments
        # in execute_tools.
        extracted_test_cases, llm_message = extract_test_cases_from_text(user_message)
        if extracted_test_cases:
            logger.info(
                "AgentOrchestrator.run: structurally extracted %d test case(s) "
                "from user_message — bypassing LLM echo",
                len(extracted_test_cases),
            )

        graph_context = self._graphrag.retrieve_context(llm_message)

        tool_router = ToolRouter(
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
            tool_executor=tool_router,
            synthesis_agent=self._synthesis_agent,
            evaluation_agent=self._evaluation_agent,
            reflection_agent=self._reflection_agent,
            format_agent=self._format_agent,
        )

        # Build profile context — prefer explicit weight_context, fall back to stored profile
        profile_ctx = weight_context or self._build_profile_context()

        initial_state: dict[str, Any] = {
            # Every downstream node (decide/select_tools/evaluate/...) reads
            # this field — it must be the CLEANED message (bulk JSON
            # replaced by a short placeholder), never the raw payload, or
            # every LLM call in the pipeline pays for re-reading it.
            "user_message": llm_message,
            "graph_context": graph_context,
            "profile_context": profile_ctx,
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
            "conversation_history": self._conversation_memory.get_history(last_n=10),
            "weight_profile": weight_profile,
            "user_profile": self._profile,
            "executed_tool_calls": [],
            "_extracted_test_cases": extracted_test_cases,
        }

        result_state = pipeline.invoke(initial_state)
        response = result_state.get("final_response", "")

        # Update conversation memory with the CLEANED message too — history
        # is replayed into future turns' prompts, so storing the raw
        # multi-thousand-char payload here would reintroduce the same
        # token-budget problem on every subsequent query in the session.
        self._conversation_memory.add_turn("user", llm_message)
        self._conversation_memory.add_turn("assistant", response)

        from src.agents.cache import response_cache
        self._callback_handler.on_node_end("orchestrator", f"response_len={len(response)}")
        logger.info("AgentOrchestrator.run: DONE response_len=%d (cache stats: %s)",
                     len(response), response_cache.stats)
        return response

    # ------------------------------------------------------------------
    # Streaming execution
    # ------------------------------------------------------------------

    def run_stream(
        self,
        user_message: str,
        uploaded_docs: str = "",
        case_study: str = "",
        weight_context: str = "",
        weight_profile=None,
    ):
        """Execute pipeline with streaming — yields (node_name, state_update) tuples.

        Usage:
            for node_name, update in orchestrator.run_stream("compare playwright vs cypress"):
                print(f"[{node_name}] completed")
        """
        logger.info("AgentOrchestrator.run_stream: START query='%.80s'", user_message)

        extracted_test_cases, llm_message = extract_test_cases_from_text(user_message)
        if extracted_test_cases:
            logger.info(
                "AgentOrchestrator.run_stream: structurally extracted %d test case(s) "
                "from user_message — bypassing LLM echo",
                len(extracted_test_cases),
            )

        graph_context = self._graphrag.retrieve_context(llm_message)

        tool_router = ToolRouter(
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
            tool_executor=tool_router,
            synthesis_agent=self._synthesis_agent,
            evaluation_agent=self._evaluation_agent,
            reflection_agent=self._reflection_agent,
            format_agent=self._format_agent,
        )

        profile_ctx = weight_context or self._build_profile_context()

        initial_state: dict[str, Any] = {
            "user_message": llm_message,
            "graph_context": graph_context,
            "profile_context": profile_ctx,
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
            "conversation_history": self._conversation_memory.get_history(last_n=10),
            "weight_profile": weight_profile,
            "user_profile": self._profile,
            "executed_tool_calls": [],
            "_extracted_test_cases": extracted_test_cases,
        }

        final_state = None
        for event in pipeline.stream(initial_state):
            for node_name, state_update in event.items():
                self._callback_handler.on_node_end(node_name, str(list(state_update.keys())))
                yield node_name, state_update
                final_state = state_update

        # Update memory after streaming completes
        if final_state and "final_response" in final_state:
            response = final_state["final_response"]
            self._conversation_memory.add_turn("user", llm_message)
            self._conversation_memory.add_turn("assistant", response)
            logger.info("AgentOrchestrator.run_stream: DONE response_len=%d", len(response))
