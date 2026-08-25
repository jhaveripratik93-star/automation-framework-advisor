"""LangGraph-based CodeGen Pipeline.

Graph nodes (in order):
  plan      → ScenarioPlanner:   analyse full test case, classify steps
  resolve   → SelectorResolver:  resolve element names to CSS selectors
  generate  → StepGenerator:     generate complete test code in one LLM call
  validate  → ValidatorAgent:    review code, apply fixes if needed
  assemble  → AssemblerAgent:    combine into final project-ready file

Conditional edge after validate:
  is_valid=False AND attempts < max_retries → generate (retry)
  otherwise                                 → assemble
"""
from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from src.codegen.agents.assembler_agent import run_assembler
from src.codegen.agents.scenario_planner import run_scenario_planner
from src.codegen.agents.selector_resolver import run_selector_resolver
from src.codegen.agents.step_generator import run_step_generator
from src.codegen.agents.validator_agent import run_validator, should_retry_validation

logger = logging.getLogger(__name__)


# ── Shared pipeline state ─────────────────────────────────────────────

class CodeGenState(TypedDict):
    # Inputs (set once before graph runs)
    test_case: dict[str, Any]          # ManualTestCase as dict
    framework: str                     # e.g. "playwright_ts"
    selector_map: dict[str, str]       # user-provided element → selector

    # Populated by ScenarioPlanner
    scenario: dict[str, Any]           # structured scenario analysis
    classified_steps: list[dict]       # steps with action_type added

    # Populated by SelectorResolver
    resolved_selectors: dict[str, str] # merged user + LLM-resolved selectors

    # Populated by StepGenerator
    generated_code: str
    generation_error: str

    # Populated by ValidatorAgent
    validation_result: dict[str, Any]
    validation_attempts: int

    # Populated by AssemblerAgent
    assembled_code: str

    # Injected dependency (read-only in nodes)
    _llm_client: Any


# ── Node factories ────────────────────────────────────────────────────

def _make_plan_node(llm_client: Any):
    def plan(state: CodeGenState) -> dict:
        logger.info("CodeGenPipeline[plan]: analysing test case '%s'", state["test_case"].get("title", ""))
        return run_scenario_planner(state, llm_client)
    return plan


def _make_resolve_node(llm_client: Any):
    def resolve(state: CodeGenState) -> dict:
        logger.info("CodeGenPipeline[resolve]: resolving selectors")
        return run_selector_resolver(state, llm_client)
    return resolve


def _make_generate_node(llm_client: Any):
    def generate(state: CodeGenState) -> dict:
        logger.info("CodeGenPipeline[generate]: generating code (attempt %d)",
                    state.get("validation_attempts", 0) + 1)
        return run_step_generator(state, llm_client)
    return generate


def _make_validate_node(llm_client: Any):
    def validate(state: CodeGenState) -> dict:
        logger.info("CodeGenPipeline[validate]: reviewing generated code")
        return run_validator(state, llm_client)
    return validate


def _make_assemble_node(llm_client: Any):
    def assemble(state: CodeGenState) -> dict:
        logger.info("CodeGenPipeline[assemble]: assembling final file")
        return run_assembler(state, llm_client)
    return assemble


# ── Graph builder ─────────────────────────────────────────────────────

def build_codegen_pipeline(llm_client: Any) -> Any:
    """Build and compile the LangGraph CodeGen pipeline.

    Returns a compiled graph whose .invoke(state) runs the full pipeline.
    """
    graph = StateGraph(CodeGenState)

    graph.add_node("plan",     _make_plan_node(llm_client))
    graph.add_node("resolve",  _make_resolve_node(llm_client))
    graph.add_node("generate", _make_generate_node(llm_client))
    graph.add_node("validate", _make_validate_node(llm_client))
    graph.add_node("assemble", _make_assemble_node(llm_client))

    graph.set_entry_point("plan")
    graph.add_edge("plan",     "resolve")
    graph.add_edge("resolve",  "generate")
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges(
        "validate",
        should_retry_validation,
        {"retry": "generate", "assemble": "assemble"},
    )
    graph.add_edge("assemble", END)

    return graph.compile()


# ── Convenience runner ────────────────────────────────────────────────

def run_codegen_pipeline(
    test_case: dict[str, Any],
    framework: str,
    selector_map: dict[str, str],
    llm_client: Any,
) -> dict[str, Any]:
    """Run the full pipeline for one test case. Returns the final state."""
    pipeline = build_codegen_pipeline(llm_client)

    initial_state: CodeGenState = {
        "test_case": test_case,
        "framework": framework,
        "selector_map": selector_map,
        "scenario": {},
        "classified_steps": [],
        "resolved_selectors": {},
        "generated_code": "",
        "generation_error": "",
        "validation_result": {},
        "validation_attempts": 0,
        "assembled_code": "",
        "_llm_client": llm_client,
    }

    final_state = pipeline.invoke(initial_state)
    logger.info(
        "CodeGenPipeline: DONE — assembled_code=%d chars, valid=%s",
        len(final_state.get("assembled_code", "")),
        final_state.get("validation_result", {}).get("is_valid", "?"),
    )
    return final_state
