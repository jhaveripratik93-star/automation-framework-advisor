"""AgentState — Shared state TypedDict for the LangGraph pipeline."""
from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    """Shared state passed between LangGraph nodes."""

    # Inputs (set once before graph runs)
    user_message: str
    graph_context: str
    profile_context: str
    uploaded_docs: str
    case_study: str

    # Decision agent output
    decision_action: str          # "tool_call" | "direct" | "rejected"
    decision_reasoning: str

    # ── Tool Selection Agent output ───────────────────────────────────
    selected_tools: list[dict[str, Any]]   # [{"tool_name": str, "arguments": dict}]

    # ── Tool Execution results ────────────────────────────────────────
    tool_results: list[dict[str, Any]]     # [{"tool_name": str, "result": str}]

    # ── Synthesis Agent output ────────────────────────────────────────
    synthesis_verdict: str
    synthesis_gaps: list[str]
    synthesis_needs_more: bool
    synthesis_round: int

    # ── Evaluation Agent output ───────────────────────────────────────
    evaluation_response: str

    # ── Format Agent output (final) ───────────────────────────────────
    final_response: str

    # ── Profile context ───────────────────────────────────────────────
    profile_context: str

    # ── Conversation memory ───────────────────────────────────────────
    conversation_history: list[dict[str, str]]


    # Runtime state (mutated by nodes)
    # action: str
 
    # reflection_critique: str
    # reflection_count: int
