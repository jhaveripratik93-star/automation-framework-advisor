"""Evaluation Agent — takes raw tool output(s) and the original user query,
then uses the LLM to produce a coherent, grounded evaluation/answer.

Integrates with LangGraph state, conversation memory, and retry policies.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agents.memory import AgentMemory
from src.agents.retry import llm_retry

if TYPE_CHECKING:
    from src.agents.state import AgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an expert Automation Framework Migration Advisor. "
    "You have been given tool results containing framework data. "
    "Use ONLY the provided data to answer the user's question. "
    "Be specific, concise, and complete. Do not hallucinate framework names or scores."
)


@dataclass
class EvaluationResult:
    response: str
    tool_results_used: list[str]
    reasoning: str = ""


class EvaluationAgent:
    """Synthesises tool outputs into a coherent LLM response.

    Maintains conversation memory to provide context-aware evaluations
    that reference previous interactions when relevant.
    """

    def __init__(self, llm_client) -> None:
        self._client = llm_client
        self._memory = AgentMemory(agent_name="evaluation_agent", max_entries=15)

    @llm_retry(max_retries=3, initial_wait=1.0)
    def _call_llm(self, messages: list[dict[str, str]], system: str) -> str:
        """LLM call with retry policy for transient failures."""
        result = self._client.chat(messages=messages, system=system, tools=None)
        return result.get("content", "").strip()

    def evaluate(
        self,
        user_message: str,
        tool_results: list[dict[str, Any]],
        graph_context: str = "",
        profile_context: str = "",
        reflection_critique: str = "",
        conversation_history: list[dict[str, str]] | None = None,
        uploaded_docs: str = "",
        case_study: str = "",
    ) -> EvaluationResult:
        # Build context block from tool results
        tool_block = ""
        tool_names_used = []
        for tr in tool_results:
            tool_names_used.append(tr["tool_name"])
            tool_block += f"\n\n### Tool: {tr['tool_name']}\n{tr['result']}"

        system = _SYSTEM_PROMPT
        if profile_context:
            system += f"\n\nUser profile: {profile_context}"
        if graph_context:
            system += f"\n\n## Knowledge Graph Context:\n{graph_context[:2000]}"
        if uploaded_docs:
            system += f"\n\n## Uploaded Test Files:\n{uploaded_docs[:2000]}"
        if case_study:
            system += f"\n\n## Case Study / Reference Documents:\n{case_study[:4000]}"

        memory_context = self._memory.get_context_string(last_n=3)
        if memory_context:
            system += f"\n\n{memory_context}"

        prompt = (
            f"User question: {user_message}\n\n"
            f"## Tool Results:{tool_block}\n\n"
            + (f"## Revision Request:\n{reflection_critique}\n\n" if reflection_critique else "")
            + "Based on the tool results above, provide a complete and specific answer."
        )

        logger.info(
            "EvaluationAgent: synthesising %d tool result(s) for query='%.60s'",
            len(tool_results), user_message,
        )

        # Build messages including recent conversation history
        messages = []
        if conversation_history:
            for msg in conversation_history[-6:]:
                messages.append({"role": msg["role"], "content": msg["content"][:500]})
        messages.append({"role": "user", "content": prompt})

        try:
            response_text = self._call_llm(messages=messages, system=system)
            if not response_text:
                response_text = tool_block.strip() or "No data available."
        except Exception as exc:
            logger.warning(
                "EvaluationAgent: LLM call failed after retries (%s) — returning raw tool output", exc
            )
            response_text = tool_block.strip() or "No data available."

        # Store in memory
        self._memory.add("evaluation", f"Q: {user_message[:80]} → A: {response_text[:100]}")

        logger.info("EvaluationAgent: response_len=%d", len(response_text))
        return EvaluationResult(
            response=response_text,
            tool_results_used=tool_names_used,
            reasoning=f"synthesised {len(tool_results)} tool result(s)",
        )

    def run_node(self, state: AgentState) -> dict[str, Any]:
        """LangGraph node entry point — reads/writes from shared state."""
        result = self.evaluate(
            user_message=state["user_message"],
            tool_results=state.get("tool_results", []),
            graph_context=state.get("graph_context", ""),
            profile_context=state.get("profile_context", ""),
            reflection_critique=state.get("reflection_critique", ""),
            conversation_history=state.get("conversation_history", []),
        )
        return {"evaluation_response": result.response}

    @property
    def memory(self) -> AgentMemory:
        return self._memory
