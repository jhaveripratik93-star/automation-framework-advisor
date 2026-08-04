"""LLM-powered advisor that grounds responses in the knowledge graph.

Orchestrates: GraphRAG context retrieval -> prompt construction ->
Ollama generation -> tool calling -> fallback to rule-based AdvisorChat on error.

Supports agentic tool calling for dynamic context retrieval and analysis.

Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 8.1, 8.2, 8.3
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.chat.advisor import AdvisorChat
    from src.graph.graphrag_engine import GraphRAGEngine
    from src.knowledge_base.loader import KnowledgeBase
    from src.llm.ollama_client import OllamaClient
    from src.scoring.models import DecisionMatrix
    from src.models import UserProfile
    from src.graph import KnowledgeGraph

from src.llm.tools import TOOL_DEFINITIONS, ToolExecutor

logger = logging.getLogger(__name__)

_MAX_HISTORY_MESSAGES = 20
_MAX_TOOL_ITERATIONS = 5  # Prevent infinite tool calling loops


class AdvisorLLM:
    """LLM-powered chat advisor grounded by the knowledge graph.

    Falls back to the rule-based AdvisorChat when Ollama is unavailable
    or returns an error.

    Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5
    """

    def __init__(
        self,
        ollama_client: OllamaClient,
        graphrag_engine: GraphRAGEngine,
        knowledge_base: KnowledgeBase,
        fallback_advisor: AdvisorChat,
        knowledge_graph: KnowledgeGraph | None = None,
    ) -> None:
        self._client = ollama_client
        self._graphrag = graphrag_engine
        self._kb = knowledge_base
        self._fallback = fallback_advisor
        self._kg = knowledge_graph  # Needed for tool executor
        self._history: deque[dict[str, str]] = deque(maxlen=_MAX_HISTORY_MESSAGES)
        self._profile: UserProfile | None = None
        self._matrix: DecisionMatrix | None = None
        self._additional_context: str | None = None  # For uploaded documents
        self._tool_executor: ToolExecutor | None = None
        logger.debug("AdvisorLLM initialized with model=%s", ollama_client.model)

    def set_context(self, profile: UserProfile | None, matrix: DecisionMatrix | None) -> None:
        self._profile = profile
        self._matrix = matrix
        self._fallback.set_context(profile, matrix)
        logger.debug(
            "AdvisorLLM.set_context: profile=%s matrix_rankings=%d",
            profile.project_name if profile else None,
            len(matrix.rankings) if matrix else 0,
        )

    # ------------------------------------------------------------------
    # Main respond orchestration
    # ------------------------------------------------------------------

    def respond(self, user_message: str, uploaded_docs: str = "", case_study: str = "") -> str:
        """Generate a response grounded by GraphRAG context with tool calling support.

        Implements an agentic loop:
        1. LLM generates response or requests tool calls
        2. If tool calls requested, execute them
        3. Feed results back to LLM
        4. Repeat until LLM produces final answer (max 5 iterations)

        Validates: Requirements 2.1, 2.3, 2.4, 8.1, 8.2, 8.3
        """
        logger.info("=" * 80)
        logger.info("ADVISOR_LLM.respond: Starting LLM-powered response generation")
        logger.debug(
            "  Message length: %d chars | History: %d messages",
            len(user_message), len(self._history),
        )
        logger.info(f"  Uploaded docs context: {len(uploaded_docs)} chars")
        logger.info(f"  Case study context: {len(case_study)} chars")
        logger.info("=" * 80)

        # Initialize tool executor with uploaded documents
        if self._kg:
            logger.info("TOOL_EXECUTOR: Initializing with knowledge graph and document contexts")
            self._tool_executor = ToolExecutor(
                knowledge_graph=self._kg,
                graphrag_engine=self._graphrag,
                knowledge_base=self._kb,
                uploaded_docs_context=uploaded_docs,
                case_study_context=case_study,
            )
            logger.info(f"TOOL_EXECUTOR: {len(TOOL_DEFINITIONS)} tools available for LLM")
        else:
            logger.warning("TOOL_EXECUTOR: Knowledge graph not available, tools disabled")

        self._extract_entities_background(user_message)

        if not self._client.is_available:
            logger.warning("=" * 80)
            logger.warning("FALLBACK: Ollama unavailable — using rule-based AdvisorChat")
            logger.warning("=" * 80)
            return self._fallback.respond(user_message)

        try:
            graph_context = self._graphrag.retrieve_context(user_message)
            logger.info(f"GRAPHRAG: Retrieved {len(graph_context)} chars of context from knowledge graph")

            system_prompt = self._build_system_prompt()
            user_prompt = self._build_user_prompt(user_message, graph_context)
            
            logger.info("AGENTIC_LOOP: Starting (max %d iterations)", _MAX_TOOL_ITERATIONS)
            
            # Agentic loop: LLM can call tools multiple times
            iteration = 0
            tool_results_history = []
            
            while iteration < _MAX_TOOL_ITERATIONS:
                iteration += 1
                logger.info("-" * 80)
                logger.info(f"AGENTIC_LOOP: Iteration {iteration}/{_MAX_TOOL_ITERATIONS}")
                logger.info("-" * 80)
                
                # Include tool results in prompt if available
                if tool_results_history:
                    tool_context = "\n\n## Tool Results:\n" + "\n\n".join(tool_results_history)
                    current_prompt = user_prompt + tool_context
                    logger.info(f"AGENTIC_LOOP: Including {len(tool_results_history)} previous tool results in prompt")
                else:
                    current_prompt = user_prompt
                
                # Generate with tools available (if tool executor exists)
                tools = TOOL_DEFINITIONS if self._tool_executor else None
                logger.info(f"LLM_CALL: Sending request to Ollama (tools={'enabled' if tools else 'disabled'})")
                
                response = self._client.generate(
                    prompt=current_prompt,
                    system=system_prompt,
                    tools=tools
                )
                
                # Check if response is a tool call request
                if isinstance(response, dict) and response.get("type") == "tool_calls":
                    if not self._tool_executor:
                        logger.warning("TOOL_CALL: LLM requested tools but no tool executor available")
                        break
                    
                    # Execute all requested tool calls
                    tool_calls = response["tool_calls"]
                    logger.info("=" * 80)
                    logger.info(f"TOOL_CALL: LLM requested {len(tool_calls)} tool(s) - ENTERING TOOL EXECUTION")
                    logger.info("=" * 80)
                    
                    for idx, tool_call in enumerate(tool_calls, 1):
                        tool_name = tool_call.get("function", {}).get("name")
                        tool_args_str = tool_call.get("function", {}).get("arguments", "{}")
                        
                        try:
                            tool_args = json.loads(tool_args_str) if isinstance(tool_args_str, str) else tool_args_str
                        except json.JSONDecodeError:
                            tool_args = {}
                        
                        logger.info(f"TOOL_CALL [{idx}/{len(tool_calls)}]: Executing '{tool_name}'")
                        logger.debug(f"  Arguments: {tool_args}")
                        
                        tool_result = self._tool_executor.execute(tool_name, tool_args)
                        
                        logger.info(f"TOOL_RESULT [{idx}/{len(tool_calls)}]: '{tool_name}' returned {len(tool_result)} chars")
                        tool_results_history.append(f"**Tool: {tool_name}**\n{tool_result}")
                    
                    logger.info("=" * 80)
                    logger.info("TOOL_CALL: All tools executed, feeding results back to LLM")
                    logger.info("=" * 80)
                    
                    # Continue loop to let LLM process tool results
                    continue
                
                # Regular text response - we're done
                if not response:
                    raise ValueError("Empty response from Ollama")
                
                logger.info("=" * 80)
                logger.info(f"AGENTIC_LOOP: FINAL RESPONSE received (length: {len(response)} chars, iterations: {iteration})")
                logger.info("=" * 80)
                
                self._history.append({"role": "user", "content": user_message})
                self._history.append({"role": "assistant", "content": response})
                return response
            
            # Max iterations reached
            logger.warning("=" * 80)
            logger.warning(f"AGENTIC_LOOP: MAX ITERATIONS REACHED ({_MAX_TOOL_ITERATIONS})")
            logger.warning("  LLM continued requesting tools beyond limit")
            logger.warning("=" * 80)
            
            final_response = "I've gathered information from multiple sources but need to conclude. " + (response if isinstance(response, str) else response.get("message", ""))
            self._history.append({"role": "user", "content": user_message})
            self._history.append({"role": "assistant", "content": final_response})
            return final_response

        except Exception as exc:
            logger.warning("=" * 80)
            logger.warning("FALLBACK: Exception in AdvisorLLM, falling back to AdvisorChat")
            logger.warning(f"  Error: {type(exc).__name__}: {exc}")
            logger.warning("=" * 80)
            return self._fallback.respond(user_message)

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        lines = [
            "You are an expert Automation Framework Migration Advisor.",
            "You help engineering teams choose, migrate to, and configure test automation frameworks.",
            "Ground your answers in the provided knowledge graph context and knowledge base data.",
            "Be concise, specific, and cite relationships when relevant.",
        ]

        if self._profile:
            p = self._profile
            lines.append(
                f"\nUser Profile: language={p.primary_language}, "
                f"team_size={p.team_size}, "
                f"ci_cd={p.ci_cd_tool}, "
                f"legacy_scripts={p.legacy_test_count}."
            )

        if self._matrix and self._matrix.rankings:
            top = self._matrix.rankings[0]
            lines.append(
                f"Current top recommendation: {top.framework} "
                f"(score {top.overall_score}/100, confidence {top.confidence})."
            )
        
        # Include uploaded document context (test files, case studies)
        if self._additional_context:
            lines.append(
                "\n## Additional Context from Uploaded Documents:\n"
                "Use the following context from the user's uploaded test files and case studies "
                "to provide more accurate and personalized recommendations.\n"
                + self._additional_context
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # User prompt
    # ------------------------------------------------------------------

    def _build_user_prompt(self, message: str, graph_context: str) -> str:
        parts: list[str] = []

        if graph_context:
            parts.append(graph_context)

        if self._history:
            history_lines = ["Conversation history (most recent last):"]
            for entry in self._history:
                role = entry["role"].capitalize()
                history_lines.append(f"{role}: {entry['content']}")
            parts.append("\n".join(history_lines))

        parts.append(f"User: {message}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Background entity extraction
    # ------------------------------------------------------------------

    def _extract_entities_background(self, message: str) -> None:
        from src.graph.entity_extractor import EntityExtractor

        def _run() -> None:
            try:
                logger.debug("AdvisorLLM._extract_entities_background: starting for message_len=%d", len(message))
                extractor = EntityExtractor(ollama_client=self._client)
                entities, relationships = extractor.extract_from_message(message)
                logger.debug(
                    "AdvisorLLM._extract_entities_background: extracted entities=%d relationships=%d",
                    len(entities), len(relationships),
                )
                graph = self._graphrag._graph
                for entity in entities:
                    graph.add_entity(entity)
                for rel in relationships:
                    graph.add_relationship(rel)
            except Exception as exc:
                logger.debug("AdvisorLLM._extract_entities_background: failed — %s: %s", type(exc).__name__, exc)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
