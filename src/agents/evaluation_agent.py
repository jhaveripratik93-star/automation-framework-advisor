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
    "Be specific, concise, and complete. Do not hallucinate framework names or scores or version numbers.\n\n"
    "CONVERSATION CONTEXT:\n"
    "- You may receive prior conversation turns. Use them to understand follow-up questions.\n"
    "- If the user's current message is short (e.g., 'Python', 'yes', 'the first one'), "
    "interpret it in the context of the previous exchange.\n\n"
    "If the tool data is relevant, use it as your primary source. "
    "If the tool data does not contain frameworks relevant to the question, "
    "answer from your own expert knowledge — do NOT say you cannot answer.\n\n"
    "FORMATTING RULES:\n"
    "- Do NOT use 'TL;DR', 'TLDR', or 'Summary' as a heading. Start directly with the answer.\n"
    "- For comparisons, use a proper markdown table with ONE row per aspect.\n"
    "- Keep each table cell on a SINGLE line — no bullet points, no line breaks inside cells.\n"
    "- If a capability has multiple items, separate them with commas (not bullets).\n"
    "- Example table format:\n"
    "| Aspect | Framework A | Framework B |\n"
    "|--------|-------------|-------------|\n"
    "| Speed | Fast, parallel execution | Moderate, sequential |\n"
    "| Languages | Python, JS, Java | JS, TS only |\n"
)

_DIRECT_SYSTEM_PROMPT = (
    "You are an expert Automation Framework Migration Advisor. "
    "Answer the user's question conversationally using your general knowledge about "
    "test automation frameworks. Be concise and helpful. "
    "If the question is too vague to give a specific recommendation, ask a clarifying "
    "question (e.g. what language, what type of app, what CI/CD tool)."
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

    # Short follow-up threshold: messages below this length get full
    # conversation context; longer messages get a compact summary.
    _SHORT_FOLLOWUP_CHARS = 60

    def __init__(self, llm_client) -> None:
        self._client = llm_client
        self._memory = AgentMemory(agent_name="evaluation_agent", max_entries=15)

    @llm_retry(max_retries=3, initial_wait=1.0)
    def _call_llm(self, messages: list[dict[str, str]], system: str) -> str:
        """LLM call with retry policy for transient failures."""
        result = self._client.chat(messages=messages, system=system, tools=None, max_tokens=1500, caller="EvaluationAgent")
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
        # ── Direct path: no tool results — answer conversationally ────
        if not tool_results:
            return self._evaluate_direct(user_message, graph_context, conversation_history)

        # ── Tool path: synthesise tool results ────────────────────────
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
            + "Based on the tool results above, provide a complete and specific answer. "
            "Ensure every sentence is finished — do not cut off mid-thought."
        )

        logger.info(
            "EvaluationAgent: synthesising %d tool result(s) for query='%.60s'",
            len(tool_results), user_message,
        )

        messages = []
        if conversation_history:
            is_followup = len(user_message.strip()) < self._SHORT_FOLLOWUP_CHARS
            history_msgs = self._build_history_messages(conversation_history, is_followup)
            messages.extend(history_msgs)
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

        self._memory.add("evaluation", f"Q: {user_message[:80]} → A: {response_text[:100]}")
        logger.info("EvaluationAgent: response_len=%d", len(response_text))
        return EvaluationResult(
            response=response_text,
            tool_results_used=tool_names_used,
            reasoning=f"synthesised {len(tool_results)} tool result(s)",
        )

    def _evaluate_direct(
        self,
        user_message: str,
        graph_context: str = "",
        conversation_history: list[dict[str, str]] | None = None,
    ) -> EvaluationResult:
        """Answer a direct (no-tool) question conversationally."""
        logger.info("EvaluationAgent: direct path for query='%.60s'", user_message)

        system = _DIRECT_SYSTEM_PROMPT
        if graph_context:
            system += f"\n\n## Background context:\n{graph_context[:1500]}"

        messages = []
        if conversation_history:
            is_followup = len(user_message.strip()) < self._SHORT_FOLLOWUP_CHARS
            history_msgs = self._build_history_messages(conversation_history, is_followup)
            messages.extend(history_msgs)
        messages.append({"role": "user", "content": user_message})

        try:
            response_text = self._call_llm(messages=messages, system=system)
            if not response_text:
                response_text = (
                    "Could you give me more details? For example: what type of app are you "
                    "testing, what language does your team use, and what CI/CD tool do you have?"
                )
        except Exception as exc:
            logger.warning("EvaluationAgent: direct LLM call failed (%s)", exc)
            response_text = (
                "I'd be happy to help! Could you tell me more about your project — "
                "what type of app, language, and CI/CD setup you have?"
            )

        self._memory.add("evaluation", f"Q: {user_message[:80]} → A: {response_text[:100]}")
        logger.info("EvaluationAgent (direct): response_len=%d", len(response_text))
        return EvaluationResult(
            response=response_text,
            tool_results_used=[],
            reasoning="direct answer (no tool results)",
        )

    # ──────────────────────────────────────────────────────────────────
    # Conversation history compression
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _summarize_history(history: list[dict[str, str]]) -> str:
        """Compress conversation history into a single compact paragraph (~100 tokens).

        Extracts the key entities and intents from recent turns instead of
        sending the full raw text.
        """
        if not history:
            return ""

        # Take last 6 turns (3 exchanges max)
        recent = history[-6:]

        user_msgs = []
        assistant_topics = []

        for turn in recent:
            content = turn.get("content", "").strip()
            if turn["role"] == "user":
                # Keep user messages short — they carry intent
                user_msgs.append(content[:80])
            elif turn["role"] == "assistant":
                # Extract just the first sentence of assistant responses
                first_sentence = content.split(".")[0].strip()
                if first_sentence:
                    assistant_topics.append(first_sentence[:100])

        parts = []
        if user_msgs:
            parts.append("User asked about: " + "; ".join(user_msgs))
        if assistant_topics:
            parts.append("Previously discussed: " + "; ".join(assistant_topics[-2:]))

        summary = ". ".join(parts)
        # Cap at ~400 chars (~100 tokens)
        if len(summary) > 400:
            summary = summary[:397] + "..."
        return summary

    @staticmethod
    def _build_history_messages(
        history: list[dict[str, str]],
        is_short_followup: bool,
    ) -> list[dict[str, str]]:
        """Build conversation history messages for the LLM.

        For short follow-ups (< 60 chars): inject last 2 exchanges as raw
        messages so the LLM has full context to interpret the follow-up.

        For longer messages: inject a single compact summary as a system-style
        user message (~100 tokens instead of ~3000).
        """
        if not history:
            return []

        if is_short_followup:
            # Short follow-up needs full recent context to interpret
            messages = []
            for turn in history[-4:]:  # last 2 exchanges
                messages.append({
                    "role": turn["role"],
                    "content": turn["content"][:300],
                })
            return messages

        # Longer message — inject compact summary only
        summary = EvaluationAgent._summarize_history(history)
        if summary:
            return [{"role": "user", "content": f"[Prior conversation context] {summary}"}]
        return []

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
