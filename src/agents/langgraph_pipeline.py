"""LangGraph pipeline — replaces the manual for-loop in AgentOrchestrator.

Graph nodes (in order):
  decide        → DecisionAgent: tool_call | direct
  select_tools  → ToolSelectionAgent: pick tool(s) + args
  execute_tools → ToolExecutor: run each tool, collect results
  synthesise    → SynthesisAgent: validate results, flag needs_more
  evaluate      → EvaluationAgent: LLM final answer from tool results
  reflect       → ReflectionAgent: critique draft, approve or revise
  format        → FormatAgent: markdown formatting

Conditional edge after `synthesise`:
  needs_more=True  AND round < MAX_ROUNDS → back to select_tools
  otherwise                               → evaluate

Conditional edge after `reflect`:
  approved=False AND reflection_count < MAX → back to evaluate (with critique)
  otherwise                                 → format
"""
from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 2


# ── Shared graph state ────────────────────────────────────────────────

class PipelineState(TypedDict):
    # Inputs (set once before graph runs)
    user_message: str
    graph_context: str
    profile_context: str
    uploaded_docs: str
    case_study: str

    # Runtime state (mutated by nodes)
    action: str                        # "tool_call" | "direct"
    tool_results: list[dict[str, Any]]
    synthesis_verdict: str
    needs_more: bool
    round_num: int
    final_response: str
    reflection_critique: str
    reflection_count: int

    # Injected dependencies (set once, read-only in nodes)
    _llm_client: Any
    _kb: Any
    _kg: Any
    _graphrag: Any


# ── Node factories ────────────────────────────────────────────────────

def make_decide_node(decision_agent):
    def decide(state: PipelineState) -> dict:
        result = decision_agent.decide(state["user_message"], state["graph_context"])
        logger.info("LangGraph[decide]: action=%s", result.action)
        output: dict[str, Any] = {"action": result.action}
        # Short-circuit: if rejected or clarify, set final_response directly
        if result.action == "rejected" and result.rejection_message:
            output["final_response"] = result.rejection_message
        elif result.action == "clarify" and result.clarification:
            output["final_response"] = result.clarification
        return output
    return decide


def make_select_tools_node(tool_selection_agent, tool_executor):
    def select_tools(state: PipelineState) -> dict:
        available = list(tool_executor.tools.keys())
        # Augment message with synthesis feedback on subsequent rounds
        msg = state["user_message"]
        if state.get("synthesis_verdict") and state.get("round_num", 0) > 0:
            msg = f"{msg}\n\n[Synthesis feedback]: {state['synthesis_verdict']}"
        selection = tool_selection_agent.select(msg, available)
        logger.info("LangGraph[select_tools]: %d tool(s) selected", len(selection.tool_calls))
        # Store selection in state for execute_tools to consume
        return {"_pending_tool_calls": selection.tool_calls}
    return select_tools


def make_execute_tools_node(tool_executor):
    def execute_tools(state: PipelineState) -> dict:
        pending = state.get("_pending_tool_calls", [])
        round_results = []
        for tc in pending:
            result_str = tool_executor.execute(tc.tool_name, tc.arguments)
            round_results.append({"tool_name": tc.tool_name, "result": result_str})
            logger.info("LangGraph[execute_tools]: tool='%s' result_len=%d", tc.tool_name, len(result_str))
        existing = state.get("tool_results") or []
        return {
            "tool_results": existing + round_results,
            "_last_round_results": round_results,
        }
    return execute_tools


def make_synthesise_node(synthesis_agent):
    def synthesise(state: PipelineState) -> dict:
        round_results = state.get("_last_round_results") or state.get("tool_results") or []
        result = synthesis_agent.synthesise(
            user_message=state["user_message"],
            tool_results=round_results,
            round_num=state.get("round_num", 0),
        )
        logger.info(
            "LangGraph[synthesise]: needs_more=%s verdict='%.80s'",
            result.needs_more_tools, result.verdict,
        )
        return {
            "synthesis_verdict": result.verdict,
            "needs_more": result.needs_more_tools,
            "round_num": state.get("round_num", 0) + 1,
        }
    return synthesise


def make_evaluate_node(evaluation_agent):
    def evaluate(state: PipelineState) -> dict:
        result = evaluation_agent.evaluate(
            user_message=state["user_message"],
            tool_results=state.get("tool_results") or [],
            graph_context=state.get("graph_context", ""),
            profile_context=state.get("profile_context", ""),
            reflection_critique=state.get("reflection_critique", ""),
        )
        logger.info("LangGraph[evaluate]: response_len=%d", len(result.response))
        return {"final_response": result.response, "reflection_critique": ""}
    return evaluate


def make_reflect_node(reflection_agent):
    def reflect(state: PipelineState) -> dict:
        result = reflection_agent.reflect(
            user_message=state["user_message"],
            draft_response=state["final_response"],
            reflection_count=state.get("reflection_count", 0),
        )
        logger.info("LangGraph[reflect]: approved=%s", result.approved)
        return {
            "reflection_critique": result.critique,
            "reflection_count": state.get("reflection_count", 0) + 1,
        }
    return reflect


def make_format_node(format_agent):
    def format_response(state: PipelineState) -> dict:
        result = format_agent.format(state["final_response"], state["user_message"])
        logger.info("LangGraph[format]: fmt_type=%s", result.format_type)
        return {"final_response": result.formatted}
    return format_response


# ── Conditional routing ───────────────────────────────────────────────

def route_after_synthesis(state: PipelineState) -> str:
    if state.get("needs_more") and state.get("round_num", 0) < _MAX_ROUNDS:
        return "select_tools"
    return "evaluate"


def route_after_reflect(state: PipelineState) -> str:
    from src.agents.reflection_agent import _MAX_REFLECTIONS
    critique = state.get("reflection_critique", "")
    count = state.get("reflection_count", 0)
    approved = not critique or critique.lower() in ("none", "max reflections reached.", "reflection unavailable.")
    if not approved and count < _MAX_REFLECTIONS:
        return "evaluate"
    return "format"


def route_after_decide(state: PipelineState) -> str:
    action = state.get("action")
    if action == "tool_call":
        return "select_tools"
    if action in ("rejected", "clarify"):
        return "format"
    return "evaluate"


# ── Graph builder ─────────────────────────────────────────────────────

def build_pipeline(
    decision_agent,
    tool_selection_agent,
    tool_executor,
    synthesis_agent,
    evaluation_agent,
    reflection_agent,
    format_agent,
) -> Any:
    """Build and compile the LangGraph StateGraph.

    Returns a compiled graph whose .invoke(state) runs the full pipeline.
    """
    graph = StateGraph(PipelineState)

    graph.add_node("decide", make_decide_node(decision_agent))
    graph.add_node("select_tools", make_select_tools_node(tool_selection_agent, tool_executor))
    graph.add_node("execute_tools", make_execute_tools_node(tool_executor))
    graph.add_node("synthesise", make_synthesise_node(synthesis_agent))
    graph.add_node("evaluate", make_evaluate_node(evaluation_agent))
    graph.add_node("reflect", make_reflect_node(reflection_agent))
    graph.add_node("format", make_format_node(format_agent))

    graph.set_entry_point("decide")

    graph.add_conditional_edges("decide", route_after_decide, {
        "select_tools": "select_tools",
        "evaluate": "evaluate",
        "format": "format",
    })
    graph.add_edge("select_tools", "execute_tools")
    graph.add_edge("execute_tools", "synthesise")
    graph.add_conditional_edges("synthesise", route_after_synthesis, {
        "select_tools": "select_tools",
        "evaluate": "evaluate",
    })
    graph.add_edge("evaluate", "reflect")
    graph.add_conditional_edges("reflect", route_after_reflect, {
        "evaluate": "evaluate",
        "format": "format",
    })
    graph.add_edge("format", END)

    return graph.compile()
