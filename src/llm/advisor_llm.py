"""AdvisorLLM — entry point for the agentic pipeline."""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.graph.graphrag_engine import GraphRAGEngine
    from src.knowledge_base.loader import KnowledgeBase
    from src.graph import KnowledgeGraph

from src.agents.orchestrator import AgentOrchestrator

logger = logging.getLogger(__name__)

_HISTORY_MAXLEN = 20
_SYSTEM_PROMPT = (
    "You are an expert Automation Framework Migration Advisor. "
    "Help engineering teams choose, migrate to, and configure test automation frameworks. "
    "Be concise, specific, and ground answers in retrieved data."
)


class AdvisorLLM:
    """Thin wrapper that routes queries through the 5-agent pipeline."""

    def __init__(
        self,
        graphrag_engine: GraphRAGEngine = None,
        knowledge_base: KnowledgeBase = None,
        knowledge_graph: KnowledgeGraph | None = None,
        llm_client=None,
        # legacy compat params — ignored
        ollama_client=None,
        fallback_advisor=None,
    ) -> None:
        self._client = llm_client
        self._graphrag = graphrag_engine
        self._kb = knowledge_base
        self._kg = knowledge_graph
        self._history: deque[dict[str, Any]] = deque(maxlen=_HISTORY_MAXLEN)

        self._orchestrator: AgentOrchestrator | None = (
            AgentOrchestrator(
                llm_client=self._client,
                graphrag_engine=graphrag_engine,
                knowledge_base=knowledge_base,
                knowledge_graph=knowledge_graph,
            )
            if knowledge_graph else None
        )

    # ── Pipeline entry point ──────────────────────────────────────────

    def run_pipeline(
        self, user_message: str, uploaded_docs: str = "", case_study: str = "",
        weight_context: str = "", weight_profile=None,
    ) -> str:
        """Run the 5-agent pipeline and return the final response string."""
        if not self._orchestrator:
            raise RuntimeError("AgentOrchestrator not available (no knowledge_graph)")
        self._extract_entities_background(user_message)
        response = self._orchestrator.run(
            user_message, uploaded_docs, case_study,
            weight_context=weight_context, weight_profile=weight_profile,
        )
        self._history.append({"role": "user", "content": user_message})
        self._history.append({"role": "assistant", "content": response})
        return response

    # ── Direct fallback (no pipeline) ────────────────────────────────

    def respond(self, user_message: str, uploaded_docs: str = "", case_study: str = "") -> str:
        """Direct LLM respond — used as fallback when pipeline fails."""
        try:
            graph_ctx = self._graphrag.retrieve_context(user_message) if self._graphrag else ""
            system = _SYSTEM_PROMPT
            if graph_ctx:
                system += f"\n\n## Knowledge Graph Context:\n{graph_ctx[:3000]}"
            if uploaded_docs:
                system += f"\n\n## Uploaded Files:\n{uploaded_docs[:2000]}"
            if case_study:
                system += f"\n\n## Case Study:\n{case_study[:4000]}"

            messages = list(self._history) + [{"role": "user", "content": user_message}]
            result = self._client.chat(messages=messages, system=system)
            response = result.get("content", "").strip() or "No response generated."
            self._history.append({"role": "user", "content": user_message})
            self._history.append({"role": "assistant", "content": response})
            return response
        except Exception as exc:
            logger.warning("AdvisorLLM.respond: %s: %s", type(exc).__name__, exc)
            return "I encountered an error processing your request. Please try again."

    # ── Background entity extraction ──────────────────────────────────

    def _extract_entities_background(self, message: str) -> None:
        from src.graph.entity_extractor import EntityExtractor

        def _run() -> None:
            try:
                extractor = EntityExtractor(llm_client=self._client)
                entities, relationships = extractor.extract_from_message(message)
                graph = self._graphrag._graph
                for e in entities:
                    graph.add_entity(e)
                for r in relationships:
                    graph.add_relationship(r)
            except Exception as exc:
                logger.debug("_extract_entities_background: %s", exc)

        threading.Thread(target=_run, daemon=True).start()
