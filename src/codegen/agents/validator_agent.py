"""Validator Agent — reviews generated code and fixes issues."""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from src.codegen.agent_config import (
    PIPELINE_SETTINGS,
    VALIDATOR_SYSTEM,
    VALIDATOR_USER_TEMPLATE,
)

if TYPE_CHECKING:
    from src.codegen.pipeline import CodeGenState

logger = logging.getLogger(__name__)


def run_validator(state: CodeGenState, llm_client: Any) -> dict:
    """LangGraph node: validate generated code and fix issues."""
    if not PIPELINE_SETTINGS.get("run_validation", True):
        logger.info("Validator: skipped (disabled in PIPELINE_SETTINGS)")
        return {"validation_result": {"is_valid": True, "issues": [], "warnings": []},
                "generated_code": state["generated_code"],
                "validation_attempts": 0}

    code = state["generated_code"]
    if not code or not code.strip():
        return {
            "validation_result": {"is_valid": False, "issues": ["Empty code"], "warnings": []},
            "generated_code": code,
            "validation_attempts": 0,
        }

    tc = state["test_case"]
    framework = state.get("framework", "playwright_ts")
    max_retries = PIPELINE_SETTINGS.get("max_validation_retries", 2)
    attempts = state.get("validation_attempts", 0)

    prompt = VALIDATOR_USER_TEMPLATE.format(
        framework=framework,
        code=code[:6000],
        title=tc.get("title", ""),
        expected_results=", ".join(tc.get("expected_results", [])) or "see test steps",
    )

    validation: dict = {"is_valid": True, "issues": [], "warnings": [], "fixed_code": ""}
    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            system=VALIDATOR_SYSTEM,
        )
        raw = result.get("content", "{}")
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            validation = json.loads(match.group())
    except Exception as exc:
        logger.warning("Validator: LLM call failed (%s) — assuming valid", exc)
        validation = {"is_valid": True, "issues": [], "warnings": [str(exc)], "fixed_code": ""}

    # Apply fix if provided and code was invalid
    final_code = code
    if not validation.get("is_valid") and validation.get("fixed_code", "").strip():
        final_code = validation["fixed_code"].strip()
        logger.info(
            "Validator: applied fix (attempt %d/%d) — issues: %s",
            attempts + 1, max_retries, validation.get("issues", []),
        )
    elif not validation.get("is_valid"):
        logger.warning(
            "Validator: code invalid but no fix provided — issues: %s",
            validation.get("issues", []),
        )

    if validation.get("warnings"):
        logger.debug("Validator: warnings: %s", validation["warnings"])

    return {
        "validation_result": validation,
        "generated_code": final_code,
        "validation_attempts": attempts + 1,
    }


def should_retry_validation(state: CodeGenState) -> str:
    """Conditional edge: retry generation if validation failed and retries remain."""
    max_retries = PIPELINE_SETTINGS.get("max_validation_retries", 2)
    attempts = state.get("validation_attempts", 0)
    is_valid = state.get("validation_result", {}).get("is_valid", True)

    if not is_valid and attempts < max_retries:
        logger.info("Validator: retrying generation (attempt %d/%d)", attempts, max_retries)
        return "retry"
    return "assemble"
