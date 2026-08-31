"""Step Generator Agent — generates test code for the full test case in one LLM call."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from src.codegen.agent_config import (
    FRAMEWORK_CONTEXT,
    PIPELINE_SETTINGS,
    STEP_GENERATOR_SYSTEM,
    STEP_GENERATOR_USER_TEMPLATE,
)

if TYPE_CHECKING:
    from src.codegen.pipeline import CodeGenState

logger = logging.getLogger(__name__)


def run_step_generator(state: CodeGenState, llm_client: Any) -> dict:
    """LangGraph node: generate complete test code for the test case."""
    tc = state["test_case"]
    framework = state.get("framework", "playwright_ts")
    scenario = state.get("scenario", {})
    selectors = state.get("resolved_selectors", {})
    arch = state.get("suite_architecture", {})

    system = STEP_GENERATOR_SYSTEM.get(framework, STEP_GENERATOR_SYSTEM["playwright_ts"])
    fw_context = FRAMEWORK_CONTEXT.get(framework, "")
    if fw_context:
        system = f"{system}\n\nFramework reference:\n{fw_context}"

    steps_text = _format_steps(state.get("classified_steps") or tc.get("steps", []))
    selectors_text = _format_selectors(selectors)

    notes_section = ""
    if PIPELINE_SETTINGS.get("include_scenario_notes") and scenario.get("notes"):
        notes_section = f"- Notes: {scenario['notes']}"

    preconditions = tc.get("preconditions", [])
    preconditions_section = (
        f"Preconditions: {', '.join(preconditions)}" if preconditions else ""
    )
    expected = tc.get("expected_results", [])
    expected_section = (
        f"Expected results: {', '.join(expected)}" if expected else ""
    )

    # Build symbol contract: symbols this test is allowed to use
    test_id = tc.get("id", "")
    symbol_contract = _build_symbol_contract(arch, test_id)
    test_dependencies = _build_test_dependencies(arch, test_id)
    common_code = _build_common_code(arch)

    prompt = STEP_GENERATOR_USER_TEMPLATE.format(
        framework=framework,
        title=tc.get("title", ""),
        test_id=test_id,
        category=tc.get("category", ""),
        test_type=scenario.get("test_type", "e2e"),
        auth_required=scenario.get("auth_required", False),
        pages_visited=", ".join(scenario.get("pages_visited", [])) or "not specified",
        complexity=scenario.get("complexity", "medium"),
        notes_section=notes_section,
        steps=steps_text,
        selectors=selectors_text,
        preconditions_section=preconditions_section,
        expected_results_section=expected_section,
        suite_architecture=json.dumps(arch, indent=2)[:3000] if arch else "not available",
        symbol_contract=symbol_contract,
        test_dependencies=test_dependencies,
        common_code=common_code,
    )

    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": prompt[:PIPELINE_SETTINGS["max_context_chars"]]}],
            system=system,
        )
        code = _clean(result.get("content", ""))
        logger.info("StepGenerator: generated %d chars for '%s'", len(code), tc.get("title", ""))
        return {"generated_code": code, "generation_error": ""}
    except Exception as exc:
        logger.error("StepGenerator: failed — %s", exc)
        return {
            "generated_code": f"// TODO: Generation failed — {exc}\n// Test: {tc.get('title', '')}",
            "generation_error": str(exc),
        }


def _build_symbol_contract(arch: dict, test_id: str) -> str:
    """Extract symbols available to this test from the architecture."""
    if not arch:
        return "not available — do not invent symbols"
    symbols = arch.get("symbols", [])
    test_entry = next((t for t in arch.get("tests", []) if t.get("test_id") == test_id), {})
    allowed_calls = set(test_entry.get("calls", []))
    lines = []
    for s in symbols:
        name = s.get("name", "")
        if not name:
            continue
        scope = s.get("scope", "global")
        if scope in ("global", "module") or name in allowed_calls:
            lines.append(
                f"  {s.get('kind', 'symbol')} {name}{s.get('signature', '')} "
                f"[defined in: {s.get('defined_in', '?')}]"
            )
    return "\n".join(lines) if lines else "none declared — use only framework built-ins"


def _build_test_dependencies(arch: dict, test_id: str) -> str:
    test_entry = next((t for t in arch.get("tests", []) if t.get("test_id") == test_id), {})
    deps = test_entry.get("depends_on_tests", [])
    reads = test_entry.get("reads", [])
    writes = test_entry.get("writes", [])
    if not deps and not reads and not writes:
        return "none"
    parts = []
    if deps:
        parts.append(f"depends on: {', '.join(deps)}")
    if reads:
        parts.append(f"reads: {', '.join(reads)}")
    if writes:
        parts.append(f"writes: {', '.join(writes)}")
    return " | ".join(parts)


def _build_common_code(arch: dict) -> str:
    shared = arch.get("shared_code", [])
    if not shared:
        return "none"
    return "\n".join(
        f"  {s.get('symbol', '')} → {s.get('defined_in', '?')} (used by: {', '.join(s.get('used_by', []))})"
        for s in shared
    )


def _format_steps(steps: list[dict]) -> str:
    lines = []
    for s in steps:
        action_type = s.get("action_type", "")
        type_hint = f" [{action_type}]" if action_type and action_type != "custom" else ""
        line = f"  {s.get('step_number', '?')}. {s.get('action', '')}{type_hint}"
        if s.get("test_data"):
            line += f"\n     Data: {s['test_data']}"
        if s.get("expected_result"):
            line += f"\n     Expected: {s['expected_result']}"
        lines.append(line)
    return "\n".join(lines)


def _format_selectors(selectors: dict[str, str]) -> str:
    if not selectors:
        return "  (none provided — use data-testid placeholders)"
    return "\n".join(f"  {k} → {v}" for k, v in list(selectors.items())[:30])


def _clean(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()
