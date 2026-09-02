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
    conversation_history: list[dict[str, str]]

    # Runtime state (mutated by nodes)
    action: str                        # "tool_call" | "direct"
    tool_results: list[dict[str, Any]]
    synthesis_verdict: str
    needs_more: bool
    round_num: int
    final_response: str
    reflection_critique: str
    reflection_count: int

    # Internal hand-off keys between nodes (must be declared or LangGraph drops them)
    _pending_tool_calls: list[Any]
    _last_round_results: list[dict[str, Any]]
    executed_tool_calls: list[dict[str, Any]]
    _cache_hit: bool

    # Injected dependencies (set once, read-only in nodes)
    _llm_client: Any
    _kb: Any
    _kg: Any
    _graphrag: Any
    weight_profile: Any
    user_profile: Any


# ── Node factories ────────────────────────────────────────────────────

def make_decide_node(decision_agent):
    def decide(state: PipelineState) -> dict:
        result = decision_agent.decide(
            state["user_message"],
            state["graph_context"],
            conversation_history=state.get("conversation_history", []),
        )
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
        # Augment with conversation context for short follow-up messages
        conversation_history = state.get("conversation_history", [])
        if conversation_history and len(msg.strip()) < 80:
            # Short message likely a follow-up — prepend recent context
            recent = conversation_history[-4:]  # last 2 exchanges
            context_lines = []
            for turn in recent:
                context_lines.append(f"{turn['role']}: {turn['content'][:150]}")
            if context_lines:
                msg = f"[Conversation context]\n" + "\n".join(context_lines) + f"\n\n[Current message] {msg}"
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
        # Persist the resolved tool calls (name + arguments) so the orchestrator
        # can build a tool-call-based response cache key.
        executed = state.get("executed_tool_calls") or []
        executed = executed + [
            {"tool_name": tc.tool_name, "arguments": dict(tc.arguments or {})}
            for tc in pending
        ]
        return {
            "tool_results": existing + round_results,
            "_last_round_results": round_results,
            "executed_tool_calls": executed,
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
            conversation_history=state.get("conversation_history", []),
            uploaded_docs=state.get("uploaded_docs", ""),
            case_study=state.get("case_study", ""),
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


def _tool_call_cache_key(state: PipelineState) -> str:
    """Build the tool-call-based response cache key for the current state."""
    from src.agents.cache import make_tool_call_cache_key
    executed = state.get("executed_tool_calls") or []
    if not executed:
        return ""
    wp = state.get("weight_profile")
    weight_sig = getattr(wp, "profile_name", "") if wp else ""
    if wp is not None and hasattr(wp, "weights"):
        weight_sig += ":" + ",".join(f"{k}={v:.2f}" for k, v in sorted(wp.weights.items()))
    return make_tool_call_cache_key(executed, weight_sig)


def make_check_cache_node():
    """Node after execute_tools: check the response cache keyed on the
    resolved tool calls. On a hit, emit the cached response and skip the
    rest of the pipeline (synthesise → evaluate → reflect → format)."""
    def check_cache(state: PipelineState) -> dict:
        from src.agents.cache import response_cache
        key = _tool_call_cache_key(state)
        if not key:
            return {"_cache_hit": False}
        cached = response_cache.get(key)
        if cached is not None:
            logger.info("LangGraph[check_cache]: TOOLCALL CACHE HIT response_len=%d", len(cached))
            return {"final_response": cached, "_cache_hit": True}
        return {"_cache_hit": False}
    return check_cache


def make_format_node(format_agent):
    def format_response(state: PipelineState) -> dict:
        result = format_agent.run_node(state)
        # Store the fully-formatted response in the tool-call cache
        from src.agents.cache import response_cache
        key = _tool_call_cache_key(state)
        if key and result.get("final_response"):
            response_cache.set(key, result["final_response"])
        logger.info("LangGraph[format]: response_len=%d", len(result.get("final_response", "")))
        return result
    return format_response


# ── Conditional routing ───────────────────────────────────────────────

def route_after_check_cache(state: PipelineState) -> str:
    """On a tool-call cache hit, skip straight to END; else continue."""
    return "hit" if state.get("_cache_hit") else "miss"


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


def route_after_evaluate(state: PipelineState) -> str:
    """Skip reflection for direct/greeting queries — no point critiquing a simple reply."""
    if state.get("action") == "direct":
        return "format"
    return "reflect"


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
    graph.add_node("check_cache", make_check_cache_node())
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
    # After executing tools, check the tool-call cache before doing expensive work
    graph.add_edge("execute_tools", "check_cache")
    graph.add_conditional_edges("check_cache", route_after_check_cache, {
        "hit": END,        # cached response already set — skip everything
        "miss": "synthesise",
    })
    graph.add_conditional_edges("synthesise", route_after_synthesis, {
        "select_tools": "select_tools",
        "evaluate": "evaluate",
    })
    graph.add_conditional_edges("evaluate", route_after_evaluate, {
        "reflect": "reflect",
        "format": "format",
    })
    graph.add_conditional_edges("reflect", route_after_reflect, {
        "evaluate": "evaluate",
        "format": "format",
    })
    graph.add_edge("format", END)

    return graph.compile()
