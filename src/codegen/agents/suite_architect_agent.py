"""Suite Architect Agent — builds the symbol/dependency architecture before any code is generated."""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from src.codegen.agent_config import (
    FRAMEWORK_CONTEXT,
    PIPELINE_SETTINGS,
    SUITE_ARCHITECT_SYSTEM,
    SUITE_ARCHITECT_USER_TEMPLATE,
)

if TYPE_CHECKING:
    from src.codegen.pipeline import CodeGenState

logger = logging.getLogger(__name__)

_EMPTY_ARCHITECTURE: dict = {
    "files": [],
    "symbols": [],
    "tests": [],
    "shared_code": [],
    "global_state": [],
    "execution_order": [],
    "unresolved_dependencies": [],
}


def run_suite_architect(state: CodeGenState, llm_client: Any) -> dict:
    """LangGraph node: build suite-level symbol/dependency architecture."""
    if not PIPELINE_SETTINGS.get("suite_architecture", True):
        logger.info("SuiteArchitect: skipped (disabled in PIPELINE_SETTINGS)")
        return {"suite_architecture": _EMPTY_ARCHITECTURE}

    tc = state["test_case"]
    framework = state.get("framework", "playwright_ts")
    scenario = state.get("scenario", {})

    test_cases_text = _format_test_case(tc)
    scenario_text = json.dumps(scenario, indent=2) if scenario else "not available"
    fw_context = FRAMEWORK_CONTEXT.get(framework, "")

    prompt = SUITE_ARCHITECT_USER_TEMPLATE.format(
        framework=framework,
        framework_context=fw_context,
        scenario_analysis=scenario_text[:PIPELINE_SETTINGS.get("max_suite_context_chars", 30000)],
        test_cases=test_cases_text,
        project_context="none",
    )

    architecture: dict = _EMPTY_ARCHITECTURE.copy()
    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            system=SUITE_ARCHITECT_SYSTEM,
        )
        raw = result.get("content", "{}")
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            architecture = json.loads(match.group())
        logger.info(
            "SuiteArchitect: %d files, %d symbols, %d unresolved",
            len(architecture.get("files", [])),
            len(architecture.get("symbols", [])),
            len(architecture.get("unresolved_dependencies", [])),
        )
    except Exception as exc:
        logger.warning("SuiteArchitect: LLM call failed (%s) — using empty architecture", exc)

    return {"suite_architecture": architecture}


def _format_test_case(tc: dict) -> str:
    steps = "\n".join(
        f"  {s['step_number']}. {s['action']}"
        + (f" [data: {s['test_data']}]" if s.get("test_data") else "")
        + (f" → {s['expected_result']}" if s.get("expected_result") else "")
        for s in tc.get("steps", [])
    )
    return (
        f"ID: {tc.get('id', '')}\n"
        f"Title: {tc.get('title', '')}\n"
        f"Category: {tc.get('category', '')}\n"
        f"Preconditions: {', '.join(tc.get('preconditions', [])) or 'none'}\n"
        f"Steps:\n{steps}\n"
        f"Expected results: {', '.join(tc.get('expected_results', [])) or 'see steps'}"
    )
