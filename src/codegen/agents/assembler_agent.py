"""Assembler Agent — combines per-test code into a final project-ready file."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.codegen.agent_config import (
    ASSEMBLER_SYSTEM,
    ASSEMBLER_USER_TEMPLATE,
    PIPELINE_SETTINGS,
)

if TYPE_CHECKING:
    from src.codegen.pipeline import CodeGenState

logger = logging.getLogger(__name__)


def run_assembler(state: CodeGenState, llm_client: Any) -> dict:
    """LangGraph node: assemble all generated test functions into one file."""
    if not PIPELINE_SETTINGS.get("run_assembler", True):
        logger.info("Assembler: skipped (disabled in PIPELINE_SETTINGS)")
        return {"assembled_code": state.get("generated_code", "")}

    # For single test case, assembler just cleans up the code
    code = state.get("generated_code", "")
    if not code.strip():
        return {"assembled_code": "// No code generated"}

    # If there's only one test and it already looks complete, skip LLM assembly
    framework = state.get("framework", "playwright_ts")
    if _looks_complete(code, framework):
        logger.info("Assembler: code already complete, skipping LLM assembly")
        return {"assembled_code": code}

    scenario = state.get("scenario", {})
    tc = state["test_case"]

    prompt = ASSEMBLER_USER_TEMPLATE.format(
        framework=framework,
        count=1,
        test_functions=code[:5000],
        page_objects=", ".join(scenario.get("page_object_hints", [])) or "none",
        fixtures=", ".join(scenario.get("fixture_hints", [])) or "none",
        imports=_infer_imports(framework),
    )

    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            system=ASSEMBLER_SYSTEM,
        )
        assembled = _clean(result.get("content", ""))
        if assembled and len(assembled) > len(code) * 0.5:
            logger.info("Assembler: assembled %d chars for '%s'", len(assembled), tc.get("title", ""))
            return {"assembled_code": assembled}
        # LLM returned something too short — use original
        return {"assembled_code": code}
    except Exception as exc:
        logger.warning("Assembler: LLM call failed (%s) — using raw generated code", exc)
        return {"assembled_code": code}


def _looks_complete(code: str, framework: str) -> bool:
    """Heuristic: check if code already has imports and test structure."""
    has_import = any(kw in code for kw in ("import ", "from ", "require(", "Library    ", "Resource    "))
    structure_markers = {
        "playwright_ts": ("test(", "test.describe("),
        "playwright_py": ("def test_",),
        "selenium_py": ("def test_", "class Test"),
        "selenium_java": ("@Test", "public void test"),
        "cypress_js": ("describe(", "it("),
        "rest_assured": ("@Test", "given()"),
        "robot_framework": ("*** Test Cases ***",),
    }
    markers = structure_markers.get(framework, ("test",))
    has_structure = any(m in code for m in markers)
    return has_import and has_structure


def _infer_imports(framework: str) -> str:
    import_map = {
        "playwright_ts": "import { test, expect } from '@playwright/test'",
        "playwright_py": "from playwright.sync_api import Page, expect",
        "selenium_py": "import pytest; from selenium.webdriver.common.by import By",
        "selenium_java": "import org.openqa.selenium.*; import org.testng.annotations.Test",
        "cypress_js": "(no imports needed)",
        "rest_assured": "import static io.restassured.RestAssured.*",
        "robot_framework": "Library    SeleniumLibrary",
    }
    return import_map.get(framework, "")


def _clean(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()
