"""Scenario Planner Agent — analyses the full test case before any code is written."""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from src.codegen.agent_config import (
    ACTION_KEYWORDS,
    FRAMEWORK_CONTEXT,
    PIPELINE_SETTINGS,
    SCENARIO_PLANNER_SYSTEM,
    SCENARIO_PLANNER_USER_TEMPLATE,
)

if TYPE_CHECKING:
    from src.codegen.pipeline import CodeGenState

logger = logging.getLogger(__name__)


def _classify_step(action: str) -> str:
    """Classify a step action using ACTION_KEYWORDS (no LLM needed)."""
    action_lower = action.lower()
    for action_type, keywords in ACTION_KEYWORDS.items():
        if any(kw in action_lower for kw in keywords):
            return action_type
    return "custom"


def run_scenario_planner(state: CodeGenState, llm_client: Any) -> dict:
    """LangGraph node: analyse the full test case and build ScenarioContext."""
    if not PIPELINE_SETTINGS.get("scenario_analysis", True):
        logger.info("ScenarioPlanner: skipped (disabled in PIPELINE_SETTINGS)")
        return {"scenario": {}, "classified_steps": _classify_all_steps(state)}

    tc = state["test_case"]
    framework = state.get("framework", "playwright_ts")
    steps_text = "\n".join(
        f"  {s['step_number']}. {s['action']}"
        + (f" [data: {s['test_data']}]" if s.get("test_data") else "")
        + (f" → {s['expected_result']}" if s.get("expected_result") else "")
        for s in tc.get("steps", [])
    )
    test_cases_text = (
        f"ID: {tc.get('id', '')}\n"
        f"Title: {tc.get('title', '')}\n"
        f"Category: {tc.get('category', '')}\n"
        f"Priority: {tc.get('priority', 'medium')}\n"
        f"Preconditions: {', '.join(tc.get('preconditions', [])) or 'none'}\n"
        f"Steps:\n{steps_text}\n"
        f"Expected results: {', '.join(tc.get('expected_results', [])) or 'see steps'}"
    )

    prompt = SCENARIO_PLANNER_USER_TEMPLATE.format(
        test_cases=test_cases_text,
        framework_context=FRAMEWORK_CONTEXT.get(framework, ""),
        project_context="none",
    )

    scenario: dict = {}
    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            system=SCENARIO_PLANNER_SYSTEM,
        )
        raw = result.get("content", "{}")
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            scenario = json.loads(match.group())
        logger.info(
            "ScenarioPlanner: type=%s complexity=%s auth=%s pages=%s",
            scenario.get("test_type", "?"),
            scenario.get("complexity", "?"),
            scenario.get("auth_required", "?"),
            scenario.get("pages_visited", []),
        )
    except Exception as exc:
        logger.warning("ScenarioPlanner: LLM call failed (%s) — using heuristics", exc)
        scenario = _heuristic_scenario(tc)

    return {
        "scenario": scenario,
        "classified_steps": _classify_all_steps(state),
    }


def _classify_all_steps(state: CodeGenState) -> list[dict]:
    """Classify each step's action type using keyword matching."""
    steps = state["test_case"].get("steps", [])
    return [
        {**s, "action_type": _classify_step(s.get("action", ""))}
        for s in steps
    ]


def _heuristic_scenario(tc: dict) -> dict:
    """Build a basic scenario without LLM when the call fails."""
    steps_text = " ".join(s.get("action", "").lower() for s in tc.get("steps", []))
    return {
        "auth_required": any(kw in steps_text for kw in ("login", "auth", "sign in", "token")),
        "pages_visited": [],
        "shared_variables": {},
        "api_calls": [],
        "fixture_hints": ["authenticated_user"] if "login" in steps_text else [],
        "page_object_hints": [],
        "test_type": "api" if any(kw in steps_text for kw in ("request", "api", "endpoint")) else "e2e",
        "complexity": "complex" if len(tc.get("steps", [])) > 7 else "medium",
        "notes": "",
    }
