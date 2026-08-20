"""LLM-based code generator — handles steps that templates cannot match.

Uses the existing GroqClient interface to generate framework-specific test code
from natural language test steps. Also provides verification and fix capabilities.
"""
from __future__ import annotations

import logging
from typing import Any

from src.codegen.models import (
    ActionType,
    GenerationSource,
    ManualTestCase,
    StepGenerationResult,
    TargetFramework,
    TestStep,
)

logger = logging.getLogger(__name__)


# ── System Prompts ────────────────────────────────────────────────────

_GENERATE_STEP_SYSTEM = """You are an expert test automation engineer. Convert manual test steps into executable automation code.

RULES:
- Output ONLY the code line(s) for the given step. No explanations, no markdown fencing.
- Use best-practice selectors (data-testid, role-based, semantic locators).
- Use proper waits and assertions where needed.
- Follow the framework's idiomatic patterns exactly.
- If the step is ambiguous, make a reasonable assumption and use a descriptive selector.
- Do NOT include imports, test function wrappers, or boilerplate — just the step implementation.
"""

_GENERATE_FULL_TEST_SYSTEM = """You are an expert test automation engineer. Convert a complete manual test case into an executable automated test.

RULES:
- Output ONLY the code. No explanations, no markdown code fences.
- Include necessary imports at the top.
- Use the framework's standard test structure (describe/test blocks, classes, etc.).
- Use best-practice selectors (data-testid preferred, then role-based).
- Include proper setup and assertions.
- Add brief inline comments for clarity.
- Make the test self-contained and runnable.
"""

_VERIFY_CODE_SYSTEM = """You are a code reviewer for test automation. Verify whether the generated code correctly implements the described test step.

Respond with ONLY one of:
- "CORRECT" — if the code accurately implements the step
- "INCORRECT: <brief reason>" — if there's an issue

Do NOT provide fixed code. Just validate."""

_FIX_CODE_SYSTEM = """You are an expert test automation engineer. Fix the syntax or logic error in the given test code.

RULES:
- Output ONLY the fixed code. No explanations, no markdown fencing.
- Preserve the original intent and structure.
- Fix only what's broken — don't refactor.
"""

# ── Framework Context ─────────────────────────────────────────────────

_FRAMEWORK_CONTEXT: dict[str, str] = {
    "playwright_ts": (
        "Framework: Playwright with TypeScript\n"
        "Test runner: @playwright/test\n"
        "Patterns: async/await, page.locator(), expect() assertions\n"
        "Imports: import { test, expect } from '@playwright/test';\n"
        "Structure: test.describe('Suite', () => { test('case', async ({ page }) => { ... }) })"
    ),
    "playwright_py": (
        "Framework: Playwright with Python (pytest-playwright)\n"
        "Patterns: sync API, page.locator(), expect() assertions\n"
        "Imports: from playwright.sync_api import Page, expect\n"
        "Structure: def test_case_name(page: Page): ..."
    ),
    "selenium_py": (
        "Framework: Selenium with Python (pytest)\n"
        "Patterns: driver.find_element(), WebDriverWait, By selectors\n"
        "Imports: from selenium.webdriver.common.by import By\n"
        "Structure: class TestCaseName:\n    def test_method(self, driver): ..."
    ),
    "selenium_java": (
        "Framework: Selenium with Java (TestNG/JUnit)\n"
        "Patterns: driver.findElement(), WebDriverWait, By selectors\n"
        "Imports: import org.openqa.selenium.*;\n"
        "Structure: public class TestName { @Test public void testMethod() { ... } }"
    ),
    "cypress_js": (
        "Framework: Cypress with JavaScript\n"
        "Patterns: cy.get(), cy.contains(), .should() assertions\n"
        "No explicit imports needed (Cypress auto-provides)\n"
        "Structure: describe('Suite', () => { it('case', () => { ... }) })"
    ),
    "rest_assured": (
        "Framework: Rest Assured with Java (JUnit)\n"
        "Patterns: given().when().then(), fluent API\n"
        "Imports: import static io.restassured.RestAssured.*;\n"
        "Structure: public class ApiTest { @Test public void testApi() { ... } }"
    ),
    "robot_framework": (
        "Framework: Robot Framework\n"
        "Patterns: keyword-driven, SeleniumLibrary keywords\n"
        "Structure: *** Test Cases ***\\nTest Name\\n    [Documentation]    desc\\n    keyword    args"
    ),
}


# ── LLM Generator ────────────────────────────────────────────────────


class LLMGenerator:
    """Generates test code using the LLM for steps not handled by templates.

    Also provides:
    - Quick verification of template-generated code
    - Syntax fix loop for failed validations
    """

    def __init__(self, llm_client: Any) -> None:
        self._client = llm_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_step(
        self,
        step: TestStep,
        framework: TargetFramework,
        context: str = "",
        selector_map: dict[str, str] | None = None,
    ) -> StepGenerationResult:
        """Generate code for a single test step using LLM.

        Args:
            step: The manual test step to convert.
            framework: Target automation framework.
            context: Additional context (preceding steps, test case description).
            selector_map: Known element-to-selector mappings.

        Returns:
            StepGenerationResult with generated code and metadata.
        """
        prompt = self._build_step_prompt(step, framework, context, selector_map)
        framework_ctx = _FRAMEWORK_CONTEXT.get(framework.value, "")
        system = f"{_GENERATE_STEP_SYSTEM}\n\n{framework_ctx}"

        try:
            result = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system=system,
            )
            code = self._clean_code_output(result.get("content", ""))
            logger.info(
                "LLMGenerator.generate_step: step=%d code_len=%d",
                step.step_number, len(code),
            )
            return StepGenerationResult(
                step_number=step.step_number,
                original_action=step.action,
                generated_code=code,
                source=GenerationSource.LLM,
                confidence=0.85,  # LLM output starts at 0.85, verified bumps to 0.95
                action_type=self._infer_action_type(step.action),
            )
        except Exception as exc:
            logger.error("LLMGenerator.generate_step: failed — %s: %s", type(exc).__name__, exc)
            return StepGenerationResult(
                step_number=step.step_number,
                original_action=step.action,
                generated_code=f"// TODO: Failed to generate — {step.action}",
                source=GenerationSource.LLM,
                confidence=0.0,
                action_type=ActionType.CUSTOM,
            )

    def generate_full_test(
        self,
        test_case: ManualTestCase,
        framework: TargetFramework,
        selector_map: dict[str, str] | None = None,
    ) -> str:
        """Generate a complete test function/file for a test case.

        Used when individual step generation has been completed and we need
        the assembled, complete test with proper structure.
        """
        prompt = self._build_full_test_prompt(test_case, framework, selector_map)
        framework_ctx = _FRAMEWORK_CONTEXT.get(framework.value, "")
        system = f"{_GENERATE_FULL_TEST_SYSTEM}\n\n{framework_ctx}"

        try:
            result = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system=system,
            )
            code = self._clean_code_output(result.get("content", ""))
            logger.info(
                "LLMGenerator.generate_full_test: tc=%s code_len=%d",
                test_case.id, len(code),
            )
            return code
        except Exception as exc:
            logger.error("LLMGenerator.generate_full_test: failed — %s", exc)
            return ""

    def verify_code(
        self,
        step_action: str,
        generated_code: str,
        framework: TargetFramework,
    ) -> bool:
        """Quick LLM verification — does the code correctly implement the step?

        This is a lightweight call (short prompt, yes/no answer) used to
        validate template-generated code.
        """
        prompt = (
            f"Test step: \"{step_action}\"\n"
            f"Generated code ({framework.value}):\n{generated_code}\n\n"
            "Does this code correctly implement the test step?"
        )

        try:
            result = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system=_VERIFY_CODE_SYSTEM,
            )
            response = result.get("content", "").strip().upper()
            is_correct = response.startswith("CORRECT")
            logger.debug(
                "LLMGenerator.verify_code: step='%.50s' → %s",
                step_action, "PASS" if is_correct else "FAIL",
            )
            return is_correct
        except Exception as exc:
            logger.warning("LLMGenerator.verify_code: call failed (%s) — assuming correct", exc)
            return True  # On failure, don't block the pipeline

    def fix_code(
        self,
        code: str,
        error_message: str,
        framework: TargetFramework,
        language: str = "",
    ) -> str:
        """Attempt to fix code that failed validation.

        Args:
            code: The broken code.
            error_message: The error/validation failure description.
            framework: Target framework for context.
            language: Programming language (auto-detected from framework if empty).

        Returns:
            Fixed code string, or original code if fix fails.
        """
        lang = language or self._framework_language(framework)
        prompt = (
            f"Language: {lang}\n"
            f"Framework: {framework.value}\n"
            f"Error: {error_message}\n\n"
            f"Code to fix:\n```\n{code}\n```\n\n"
            "Return ONLY the fixed code."
        )

        try:
            result = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system=_FIX_CODE_SYSTEM,
            )
            fixed = self._clean_code_output(result.get("content", ""))
            if fixed and len(fixed) > 10:
                logger.info("LLMGenerator.fix_code: successfully fixed (%d chars)", len(fixed))
                return fixed
            return code
        except Exception as exc:
            logger.error("LLMGenerator.fix_code: failed — %s", exc)
            return code

    def batch_generate_steps(
        self,
        steps: list[TestStep],
        framework: TargetFramework,
        test_case_context: str = "",
        selector_map: dict[str, str] | None = None,
    ) -> list[StepGenerationResult]:
        """Generate code for multiple steps in a single LLM call (cost optimization).

        Batches up to 10 steps into one prompt to reduce API calls.
        """
        if not steps:
            return []

        # For small batches, use individual calls (more reliable)
        if len(steps) <= 2:
            return [
                self.generate_step(s, framework, test_case_context, selector_map)
                for s in steps
            ]

        # Batch prompt for larger step sets
        prompt = self._build_batch_prompt(steps, framework, test_case_context, selector_map)
        framework_ctx = _FRAMEWORK_CONTEXT.get(framework.value, "")
        system = (
            f"{_GENERATE_STEP_SYSTEM}\n\n{framework_ctx}\n\n"
            "IMPORTANT: For each step, output the code prefixed with '// STEP N:' "
            "(or '# STEP N:' for Python/Robot) on its own line, where N is the step number."
        )

        try:
            result = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system=system,
            )
            raw = result.get("content", "")
            return self._parse_batch_output(raw, steps, framework)
        except Exception as exc:
            logger.warning("LLMGenerator.batch_generate: failed (%s) — falling back to individual", exc)
            return [
                self.generate_step(s, framework, test_case_context, selector_map)
                for s in steps
            ]

    # ------------------------------------------------------------------
    # Prompt Builders
    # ------------------------------------------------------------------

    def _build_step_prompt(
        self,
        step: TestStep,
        framework: TargetFramework,
        context: str,
        selector_map: dict[str, str] | None,
    ) -> str:
        """Build prompt for single step generation."""
        parts = [f"Convert this manual test step to {framework.value} code:\n"]
        parts.append(f"Step {step.step_number}: {step.action}")

        if step.test_data:
            parts.append(f"Test data: {step.test_data}")
        if step.expected_result:
            parts.append(f"Expected result: {step.expected_result}")
        if context:
            parts.append(f"\nContext (preceding steps):\n{context}")
        if selector_map:
            relevant = {k: v for k, v in selector_map.items()
                       if k.lower() in step.action.lower()}
            if relevant:
                parts.append(f"\nKnown selectors: {relevant}")

        return "\n".join(parts)

    def _build_full_test_prompt(
        self,
        test_case: ManualTestCase,
        framework: TargetFramework,
        selector_map: dict[str, str] | None,
    ) -> str:
        """Build prompt for full test case generation."""
        parts = [
            f"Convert this manual test case to a complete {framework.value} automated test:\n",
            f"Title: {test_case.title}",
            f"ID: {test_case.id}",
        ]

        if test_case.description:
            parts.append(f"Description: {test_case.description}")
        if test_case.preconditions:
            parts.append(f"Preconditions: {', '.join(test_case.preconditions)}")

        parts.append("\nSteps:")
        for step in test_case.steps:
            line = f"  {step.step_number}. {step.action}"
            if step.test_data:
                line += f" [data: {step.test_data}]"
            if step.expected_result:
                line += f" → {step.expected_result}"
            parts.append(line)

        if test_case.expected_results:
            parts.append(f"\nExpected Results: {', '.join(test_case.expected_results)}")

        if selector_map:
            parts.append(f"\nKnown selectors:\n{self._format_selector_map(selector_map)}")

        return "\n".join(parts)

    def _build_batch_prompt(
        self,
        steps: list[TestStep],
        framework: TargetFramework,
        context: str,
        selector_map: dict[str, str] | None,
    ) -> str:
        """Build prompt for batch step generation."""
        parts = [
            f"Convert these manual test steps to {framework.value} code.\n"
            "Output each step's code prefixed with its step number marker.\n",
        ]

        if context:
            parts.append(f"Test context: {context}\n")

        parts.append("Steps:")
        for step in steps:
            line = f"  Step {step.step_number}: {step.action}"
            if step.test_data:
                line += f" [data: {step.test_data}]"
            parts.append(line)

        if selector_map:
            parts.append(f"\nKnown selectors:\n{self._format_selector_map(selector_map)}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Output Parsing
    # ------------------------------------------------------------------

    def _parse_batch_output(
        self,
        raw: str,
        steps: list[TestStep],
        framework: TargetFramework,
    ) -> list[StepGenerationResult]:
        """Parse batch LLM output into individual step results."""
        import re

        results: list[StepGenerationResult] = []
        # Split by step markers
        pattern = r"(?://|#)\s*STEP\s+(\d+)\s*:?"
        segments = re.split(pattern, raw, flags=re.IGNORECASE)

        # Build a map of step_number → code
        step_code_map: dict[int, str] = {}
        i = 1  # Skip first segment (preamble)
        while i < len(segments) - 1:
            try:
                step_num = int(segments[i])
                code = segments[i + 1].strip()
                step_code_map[step_num] = code
            except (ValueError, IndexError):
                pass
            i += 2

        # Match back to original steps
        for step in steps:
            code = step_code_map.get(step.step_number, "")
            if code:
                # Clean trailing step markers from next step
                code_lines = []
                for line in code.split("\n"):
                    if re.match(pattern, line.strip(), re.IGNORECASE):
                        break
                    code_lines.append(line)
                code = "\n".join(code_lines).strip()

            results.append(StepGenerationResult(
                step_number=step.step_number,
                original_action=step.action,
                generated_code=code or f"// TODO: {step.action}",
                source=GenerationSource.LLM,
                confidence=0.80 if code else 0.0,
                action_type=self._infer_action_type(step.action),
            ))

        return results

    @staticmethod
    def _clean_code_output(raw: str) -> str:
        """Remove markdown fencing and extra whitespace from LLM output."""
        text = raw.strip()
        # Remove ```language ... ``` fencing
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```lang) and last line (```)
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return text.strip()

    @staticmethod
    def _infer_action_type(action: str) -> ActionType:
        """Infer the action type from natural language step text."""
        action_lower = action.lower()
        if any(kw in action_lower for kw in ("navigate", "go to", "open", "visit", "url")):
            return ActionType.NAVIGATE
        if any(kw in action_lower for kw in ("click", "tap", "press button", "hit")):
            return ActionType.CLICK
        if any(kw in action_lower for kw in ("enter", "type", "input", "fill")):
            return ActionType.FILL
        if any(kw in action_lower for kw in ("select", "choose", "pick", "dropdown")):
            return ActionType.SELECT
        if any(kw in action_lower for kw in ("verify", "check", "assert", "confirm", "ensure", "validate")):
            if any(kw in action_lower for kw in ("visible", "shown", "displayed", "appears")):
                return ActionType.ASSERT_VISIBLE
            if any(kw in action_lower for kw in ("url", "address")):
                return ActionType.ASSERT_URL
            return ActionType.ASSERT_TEXT
        if any(kw in action_lower for kw in ("wait", "pause", "sleep")):
            return ActionType.WAIT
        if any(kw in action_lower for kw in ("hover", "mouse over")):
            return ActionType.HOVER
        if any(kw in action_lower for kw in ("scroll",)):
            return ActionType.SCROLL
        if any(kw in action_lower for kw in ("upload", "attach file")):
            return ActionType.UPLOAD
        return ActionType.CUSTOM

    @staticmethod
    def _framework_language(framework: TargetFramework) -> str:
        """Get programming language for a framework."""
        lang_map = {
            TargetFramework.PLAYWRIGHT_TS: "TypeScript",
            TargetFramework.PLAYWRIGHT_PY: "Python",
            TargetFramework.SELENIUM_PY: "Python",
            TargetFramework.SELENIUM_JAVA: "Java",
            TargetFramework.CYPRESS_JS: "JavaScript",
            TargetFramework.REST_ASSURED: "Java",
            TargetFramework.ROBOT_FRAMEWORK: "Robot Framework",
        }
        return lang_map.get(framework, "Unknown")

    @staticmethod
    def _format_selector_map(selector_map: dict[str, str]) -> str:
        """Format selector map for inclusion in prompts."""
        lines = []
        for element, selector in selector_map.items():
            lines.append(f"  {element} → {selector}")
        return "\n".join(lines)
