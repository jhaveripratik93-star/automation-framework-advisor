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
# NOTE: intentionally NOT using `from __future__ import annotations`.
# LangGraph resolves the CodeGenState TypedDict annotations via
# typing.get_type_hints() at StateGraph construction time. On Python 3.14,
# stringized (deferred) annotations fail forward-ref evaluation with
# "NameError: name 'CodeGenState'/'Any' is not defined", which crashes
# build_codegen_pipeline and silently drops generation into the LEGACY path
# (where the one-test-case collapse and category grouping do NOT run — hence
# "one test case per step" in the output). Eager evaluation keeps the
# annotations as concrete type objects.

import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from src.codegen.agents.assembler_agent import run_assembler
from src.codegen.agents.scenario_planner import run_scenario_planner
from src.codegen.agents.selector_resolver import run_selector_resolver
from src.codegen.agents.step_generator import run_step_generator
from src.codegen.agents.suite_architect_agent import run_suite_architect
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

    # Populated by SuiteArchitect
    suite_architecture: dict[str, Any] # symbol/dependency contract

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

# NOTE: node/edge functions passed to LangGraph are intentionally left
# UNANNOTATED for their `state` parameter. LangGraph calls
# typing.get_type_hints() on these callables to infer a schema; any annotation
# referencing CodeGenState can fail forward-ref resolution on Python 3.14 and
# crash graph construction (dropping generation to the legacy path). Omitting
# the annotation avoids that entirely.

def _make_plan_node(llm_client: Any):
    def plan(state) -> dict:
        logger.info("CodeGenPipeline[plan]: analysing test case '%s'", state["test_case"].get("title", ""))
        return run_scenario_planner(state, llm_client)
    return plan


def _make_architect_node(llm_client: Any):
    def architect(state) -> dict:
        logger.info("CodeGenPipeline[architect]: building suite architecture")
        return run_suite_architect(state, llm_client)
    return architect


def _make_resolve_node(llm_client: Any):
    def resolve(state) -> dict:
        logger.info("CodeGenPipeline[resolve]: resolving selectors")
        return run_selector_resolver(state, llm_client)
    return resolve


def _make_generate_node(llm_client: Any):
    def generate(state) -> dict:
        logger.info("CodeGenPipeline[generate]: generating code (attempt %d)",
                    state.get("validation_attempts", 0) + 1)
        return run_step_generator(state, llm_client)
    return generate


def _make_validate_node(llm_client: Any):
    def validate(state) -> dict:
        logger.info("CodeGenPipeline[validate]: reviewing generated code")
        return run_validator(state, llm_client)
    return validate


def _make_assemble_node(llm_client: Any):
    def assemble(state) -> dict:
        logger.info("CodeGenPipeline[assemble]: assembling final file")
        return run_assembler(state, llm_client)
    return assemble


# ── Graph builder ─────────────────────────────────────────────────────

def build_codegen_pipeline(llm_client: Any) -> Any:
    """Build and compile the LangGraph CodeGen pipeline.

    Returns a compiled graph whose .invoke(state) runs the full pipeline.
    """
    graph = StateGraph(CodeGenState)

    graph.add_node("plan",      _make_plan_node(llm_client))
    graph.add_node("architect", _make_architect_node(llm_client))
    graph.add_node("resolve",   _make_resolve_node(llm_client))
    graph.add_node("generate",  _make_generate_node(llm_client))
    graph.add_node("validate",  _make_validate_node(llm_client))
    graph.add_node("assemble",  _make_assemble_node(llm_client))

    graph.set_entry_point("plan")
    graph.add_edge("plan",      "architect")
    graph.add_edge("architect", "resolve")
    graph.add_edge("resolve",   "generate")
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
    suite_architecture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full pipeline for one test case. Returns the final state.

    If ``suite_architecture`` is provided it is injected into the initial
    state so the SuiteArchitect node is effectively skipped (it will see a
    non-empty architecture and return it unchanged).
    """
    pipeline = build_codegen_pipeline(llm_client)

    initial_state: CodeGenState = {
        "test_case": test_case,
        "framework": framework,
        "selector_map": selector_map,
        "scenario": {},
        "classified_steps": [],
        "suite_architecture": suite_architecture or {},
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
