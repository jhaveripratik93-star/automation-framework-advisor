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

    # Pre-detect domain from step text so we can inject it into the prompt
    # and also use it as a fallback if the LLM misidentifies the test type.
    steps_text_lower = " ".join(s.get("action", "").lower() for s in tc.get("steps", []))
    detected_domain = _detect_domain(steps_text_lower)

    # Always use heuristics — saves one LLM call per TC and is accurate
    # enough for well-structured manual test cases.
    scenario = _heuristic_scenario(tc)
    logger.info(
        "ScenarioPlanner: heuristic — type=%s domain=%s complexity=%s",
        scenario.get("test_type", "?"),
        scenario.get("domain", "?"),
        scenario.get("complexity", "?"),
    )

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


# Domain keyword sets for non-UI test type detection
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "messaging": [
        "kafka", "rabbitmq", "activemq", "sqs", "sns", "pubsub", "nats", "pulsar",
        "topic", "queue", "broker", "producer", "consumer", "publish", "subscribe",
        "message", "event", "offset", "partition", "consume", "produce",
    ],
    "database": [
        "database", "db", "sql", "mysql", "postgres", "postgresql", "oracle",
        "mongodb", "redis", "cassandra", "dynamodb", "query", "insert", "update",
        "delete", "select", "table", "record", "row", "schema",
    ],
    "api": [
        "request", "api", "endpoint", "rest", "graphql", "grpc", "http",
        "post", "get", "put", "patch", "response", "status code", "payload",
        "header", "token", "oauth", "curl",
    ],
    "performance": [
        "load", "stress", "performance", "throughput", "latency", "concurrent",
        "virtual user", "ramp", "spike", "k6", "locust", "jmeter",
    ],
}


def _detect_domain(steps_text: str) -> str:
    """Detect the test domain from step text. Returns 'e2e' for UI tests."""
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if any(kw in steps_text for kw in keywords):
            return domain
    return "e2e"


def _heuristic_scenario(tc: dict) -> dict:
    """Build a basic scenario without LLM when the call fails."""
    steps_text = " ".join(s.get("action", "").lower() for s in tc.get("steps", []))
    domain = _detect_domain(steps_text)
    test_type = "e2e" if domain == "e2e" else domain
    return {
        "auth_required": any(kw in steps_text for kw in ("login", "auth", "sign in", "token")),
        "pages_visited": [],
        "shared_variables": {},
        "api_calls": [],
        "fixture_hints": ["authenticated_user"] if "login" in steps_text else [],
        "page_object_hints": [],
        "test_type": test_type,
        "domain": domain,
        "complexity": "complex" if len(tc.get("steps", [])) > 7 else "medium",
        "notes": f"Detected domain: {domain}",
    }
