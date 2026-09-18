"""CodeGen Orchestrator — main pipeline for hybrid test code generation.

Two generation paths:
  1. LangGraph Agent Pipeline (default) — 5 specialised agents:
       plan → resolve → generate → validate → assemble
     Prompts and settings are fully configurable in agent_config.py

  2. Legacy Template+LLM Pipeline (fallback) — step-by-step:
       TemplateEngine → LLMGenerator → CodeValidator → CodeRenderer
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from src.codegen.llm_generator import LLMGenerator
from src.codegen.models import (
    CodeGenOptions,
    CodeGenRequest,
    FileType,
    GeneratedFile,
    GeneratedTestSuite,
    GenerationSource,
    GenerationStats,
    ManualTestCase,
    StepGenerationResult,
    TargetFramework,
    TestCaseGenerationResult,
    ValidationResult,
)
from src.codegen.optimizer import TestOptimizer
from src.codegen.renderer import CodeRenderer
from src.codegen.template_engine import TemplateEngine
from src.codegen.template_store import TemplateStore
from src.codegen.validator import CodeValidator

logger = logging.getLogger(__name__)


class CodeGenOrchestrator:
    """Main orchestrator for the hybrid manual-to-automated test code generation.

    Usage:
        orchestrator = CodeGenOrchestrator(llm_client=groq_client)
        result = orchestrator.generate(request)
    """

    def __init__(
        self,
        llm_client: Any,
        template_store_path: str | None = None,
        auto_learn: bool = True,
        use_agent_pipeline: bool = True,
    ) -> None:
        self._llm_client = llm_client
        self._use_agent_pipeline = use_agent_pipeline and llm_client is not None

        # Legacy components (used as fallback)
        self._template_store = TemplateStore(
            store_path=template_store_path,
            auto_learn=auto_learn,
        )
        self._template_engine = TemplateEngine(
            learned_patterns=self._template_store.get_active_patterns(),
        )
        self._llm_generator = LLMGenerator(llm_client=llm_client)
        self._validator = CodeValidator()
        self._renderer = CodeRenderer()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, request: CodeGenRequest) -> GeneratedTestSuite:
        """Execute the full code generation pipeline (agent pipeline only)."""
        if self._use_agent_pipeline:
            return self._generate_with_agents(request)
        return self._generate_legacy(request)

    @staticmethod
    def _group_by_category(test_cases: list[ManualTestCase]) -> "dict[str, list[ManualTestCase]]":
        """Group manual test cases by feature/category.

        Grouping key precedence: explicit ``category`` → first ``tag`` →
        the test's own title (its own group). Cases sharing a category thus
        land together while uncategorised cases each get their own file.
        Insertion order is preserved for deterministic file naming.
        """
        groups: dict[str, list[ManualTestCase]] = {}
        for tc in test_cases:
            key = (tc.category or "").strip()
            if not key and tc.tags:
                key = (tc.tags[0] or "").strip()
            if not key:
                # No category/tag → keep this case in its own file (by title).
                key = (tc.title or tc.id or "tests").strip()
            groups.setdefault(key, []).append(tc)
        return groups

    @staticmethod
    def _split_groups(
        groups: "dict[str, list[ManualTestCase]]",
        max_per_file: int,
    ) -> "dict[str, list[ManualTestCase]]":
        """Split large groups into numbered sub-groups (max_per_file TCs each).

        e.g. "Happy Path" with 9 TCs and max=3 → "Happy Path_1", "Happy Path_2", "Happy Path_3"
        """
        if max_per_file <= 0:
            return groups
        result: dict[str, list[ManualTestCase]] = {}
        for key, tcs in groups.items():
            if len(tcs) <= max_per_file:
                result[key] = tcs
            else:
                for i, chunk_start in enumerate(range(0, len(tcs), max_per_file), 1):
                    result[f"{key}_{i}"] = tcs[chunk_start:chunk_start + max_per_file]
        return result

    def _bundle_group(self, codes: list[str], framework_value: str) -> str:
        """Combine a category group's per-test code into one file's content.

        For Robot Framework the files are merged into a single suite (shared
        Settings/Variables/Keywords, all test cases under one *** Test Cases ***).
        For other frameworks (no defined multi-test merge yet) the snippets are
        concatenated with a blank line between them.
        """
        codes = [c for c in codes if c and c.strip()]
        if not codes:
            return ""
        if len(codes) == 1:
            return codes[0].strip() + "\n"
        if framework_value == "robot_framework":
            from src.codegen.robot_postprocess import merge_robot_files
            return merge_robot_files(codes)
        return ("\n\n".join(c.strip() for c in codes)) + "\n"

    def _batch_generate(
        self,
        test_cases: list,
        framework_value: str,
        selector_map: dict,
    ) -> list[str]:
        """Generate ALL test cases in a SINGLE LLM call.

        Returns a list of code strings, one per test case, in the same order.
        Falls back to empty strings on failure (caller handles error display).
        """
        from src.codegen.agent_config import (
            STEP_GENERATOR_SYSTEM, FRAMEWORK_CONTEXT, PIPELINE_SETTINGS,
        )
        from src.codegen.agents.scenario_planner import _heuristic_scenario

        system = STEP_GENERATOR_SYSTEM.get(framework_value, STEP_GENERATOR_SYSTEM["playwright_ts"])
        fw_ctx = FRAMEWORK_CONTEXT.get(framework_value, "")
        if fw_ctx:
            system = f"{system}\n\nFramework reference:\n{fw_ctx}"

        # Build one prompt block per TC
        tc_blocks = []
        for i, tc in enumerate(test_cases, 1):
            steps_text = "\n".join(
                f"  {s.get('step_number', j)}. {s.get('action', '')}"
                + (f" | data: {s['test_data']}" if s.get('test_data') else "")
                + (f" | expect: {s['expected_result']}" if s.get('expected_result') else "")
                for j, s in enumerate(tc.get("steps", []), 1)
            )
            scenario = _heuristic_scenario(tc)
            pre = ", ".join(tc.get("preconditions", []))
            exp = ", ".join(tc.get("expected_results", []))
            tc_blocks.append(
                f"=== TEST {i}: {tc.get('title', '')} (ID: {tc.get('id', '')}) ===\n"
                f"Category: {tc.get('category', '')} | Auth: {scenario.get('auth_required', False)}"
                f" | Domain: {scenario.get('domain', 'e2e')}\n"
                + (f"Preconditions: {pre}\n" if pre else "")
                + f"Steps:\n{steps_text}\n"
                + (f"Expected: {exp}\n" if exp else "")
            )

        n = len(test_cases)
        prompt = (
            f"Generate {framework_value} test code for ALL {n} test cases below.\n"
            f"Output EXACTLY {n} code blocks, each preceded by a marker line:\n"
            f"  ### TEST_1 ###\n  <code for test 1>\n"
            f"  ### TEST_2 ###\n  <code for test 2>\n"
            f"  ... and so on up to ### TEST_{n} ###\n"
            f"No other text outside the markers. No markdown fencing.\n\n"
            + "\n\n".join(tc_blocks)
        )

        try:
            result = self._llm_client.chat(
                messages=[{"role": "user", "content": prompt[:PIPELINE_SETTINGS["max_context_chars"] * n]}],
                system=system,
                max_tokens=1200 * n,
            )
            raw = result.get("content", "")
            return self._split_batch_response(raw, n)
        except Exception as exc:
            logger.error("_batch_generate failed: %s", exc)
            raise

    @staticmethod
    def _split_batch_response(raw: str, n: int) -> list[str]:
        """Split the batch LLM response into per-TC code strings."""
        import re as _re
        parts = _re.split(r"###\s*TEST_\d+\s*###", raw)
        # parts[0] is text before first marker (discard), parts[1..n] are the codes
        codes = [p.strip() for p in parts[1:n + 1]]
        # Pad with empty strings if LLM returned fewer blocks than expected
        while len(codes) < n:
            codes.append("")
        return codes

    def _generate_with_agents(self, request: CodeGenRequest) -> GeneratedTestSuite:
        """Generate using the LangGraph 5-agent pipeline.

        Optimisations vs. the naive per-TC approach:
        - Suite architect runs ONCE across all test cases (not per-TC).
        - Selector resolver is skipped for non-UI domains.
        - Validator and assembler are disabled by default (see PIPELINE_SETTINGS).
        - Large category groups are split into multiple files (max_tests_per_file).
        - A real common/resources file is generated from shared symbols.
        """
        from src.codegen.pipeline import run_codegen_pipeline
        from src.codegen.renderer import _FRAMEWORK_CONFIG
        from src.codegen.agent_config import PIPELINE_SETTINGS

        start_time = time.time()
        framework = request.target_framework
        cfg = _FRAMEWORK_CONFIG.get(framework.value, _FRAMEWORK_CONFIG["playwright_ts"])
        files: list[GeneratedFile] = []
        stats = GenerationStats(total_steps=sum(len(tc.steps) for tc in request.test_cases))
        _per_tc_states: list[dict] = []

        # ── Step 1: Run suite architect ONCE for all TCs ──────────────
        suite_arch: dict = {}
        if PIPELINE_SETTINGS.get("suite_architecture", False):
            suite_arch = self._run_suite_architect_once(request, framework.value)

        # ── Step 2: Group and split into files ────────────────────────
        max_per_file = PIPELINE_SETTINGS.get("max_tests_per_file", 3)
        grouped = self._group_by_category(request.test_cases)
        grouped = self._split_groups(grouped, max_per_file)

        # ── Step 3: Generate each TC (1-2 LLM calls each) ─────────────
        for group_key, group_tcs in grouped.items():
            group_codes: list[str] = []
            group_valid = True

            for tc in group_tcs:
                tc_dict = {
                    "id": tc.id,
                    "title": tc.title,
                    "description": tc.description,
                    "category": tc.category,
                    "priority": tc.priority,
                    "preconditions": tc.preconditions,
                    "steps": [
                        {
                            "step_number": s.step_number,
                            "action": s.action,
                            "test_data": s.test_data,
                            "expected_result": s.expected_result,
                        }
                        for s in tc.steps
                    ],
                    "expected_results": tc.expected_results,
                    "tags": tc.tags,
                }

                # Inject pre-computed suite architecture so per-TC pipeline
                # skips its own architect call.
                final_state = run_codegen_pipeline(
                    test_case=tc_dict,
                    framework=framework.value,
                    selector_map=dict(request.selector_map),
                    llm_client=self._llm_client,
                    suite_architecture=suite_arch,
                )
                _per_tc_states.append(final_state)

                code = final_state.get("assembled_code") or final_state.get("generated_code", "")
                group_codes.append(code)
                if not final_state.get("validation_result", {}).get("is_valid", True):
                    group_valid = False
                stats.llm_handled += len(tc.steps)

            # Bundle the group's test cases into a single file.
            group_content = self._bundle_group(group_codes, framework.value)
            group_slug = re.sub(r"[^a-z0-9]+", "_", group_key.lower()).strip("_")[:50] or "tests"
            files.append(GeneratedFile(
                path=f"tests/{group_slug}{cfg['extension']}",
                content=group_content,
                file_type=FileType.TEST,
                source=GenerationSource.LLM,
                confidence=0.9 if group_valid else 0.6,
            ))

        # ── Step 4: Generate common/resources file ────────────────────
        common_file = self._generate_common_resource(
            _per_tc_states, framework.value, cfg, request.test_cases
        )
        if common_file:
            files.append(common_file)

        # ── Step 5: Config + package files ────────────────────────────
        config_content = self._renderer._render_config(framework)
        if config_content:
            files.append(GeneratedFile(
                path=cfg["config_file"],
                content=config_content,
                file_type=FileType.CONFIG,
                source=GenerationSource.TEMPLATE,
            ))

        pkg_content = self._renderer._render_package_file(framework)
        if pkg_content:
            files.append(GeneratedFile(
                path=cfg["package_file"],
                content=pkg_content,
                file_type=FileType.PACKAGE,
                source=GenerationSource.TEMPLATE,
            ))

        elapsed_ms = int((time.time() - start_time) * 1000)
        stats.time_elapsed_ms = elapsed_ms

        all_undefined: list[str] = list(dict.fromkeys(
            sym
            for s in _per_tc_states
            for sym in s.get("validation_result", {}).get("undefined_symbols", [])
        ))

        suite = GeneratedTestSuite(
            framework=framework.value,
            language=cfg["language"],
            files=files,
            install_instructions=cfg["install_command"],
            run_command=cfg["run_command"],
            selector_map=request.selector_map,
            confidence_score=sum(f.confidence for f in files) / max(len(files), 1),
            validation=ValidationResult(
                is_valid=len(all_undefined) == 0,
                undefined_symbols=all_undefined,
            ),
            stats=stats,
        )
        logger.info(
            "CodeGenOrchestrator (agents): %d files in %dms",
            len(files), elapsed_ms,
        )
        return suite

    def _run_suite_architect_once(
        self,
        request: CodeGenRequest,
        framework_value: str,
    ) -> dict:
        """Run the suite architect LLM call once for ALL test cases combined.

        Returns the architecture dict to be injected into every per-TC pipeline
        run so the per-TC architect node is skipped.
        """
        from src.codegen.agents.suite_architect_agent import run_suite_architect
        from src.codegen.renderer import _FRAMEWORK_CONFIG

        # Build a synthetic state that contains all TCs as a combined text
        all_tc_text = "\n\n".join(
            f"ID: {tc.id}\nTitle: {tc.title}\nCategory: {tc.category}\n"
            f"Steps:\n" + "\n".join(
                f"  {s.step_number}. {s.action}"
                + (f" [{s.test_data}]" if s.test_data else "")
                for s in tc.steps
            )
            for tc in request.test_cases
        )
        synthetic_state = {
            "test_case": {
                "id": "SUITE",
                "title": f"Suite of {len(request.test_cases)} test cases",
                "category": "",
                "preconditions": [],
                "steps": [
                    {"step_number": i + 1, "action": line, "test_data": "", "expected_result": ""}
                    for i, line in enumerate(all_tc_text.split("\n"))[:30]  # cap at 30 lines
                ],
                "expected_results": [],
                "tags": [],
            },
            "framework": framework_value,
            "scenario": {},
            "selector_map": dict(request.selector_map),
        }
        try:
            result = run_suite_architect(synthetic_state, self._llm_client)
            arch = result.get("suite_architecture", {})
            logger.info(
                "SuiteArchitect (once): %d files, %d symbols",
                len(arch.get("files", [])),
                len(arch.get("symbols", [])),
            )
            return arch
        except Exception as exc:
            logger.warning("SuiteArchitect (once): failed (%s) — skipping", exc)
            return {}

    def _generate_common_resource(
        self,
        per_tc_states: list[dict],
        framework_value: str,
        cfg: dict,
        test_cases: list[ManualTestCase],
    ) -> GeneratedFile | None:
        """Generate a common/resources file with shared fixtures and helpers.

        Deterministic (no LLM) — extracts patterns that appear in multiple
        generated test files and writes them to a single shared resource.
        """
        if not per_tc_states:
            return None

        is_robot = framework_value == "robot_framework"
        is_py = cfg.get("language") in ("python", "Python")

        # Collect all preconditions and categories to infer shared setup
        all_preconditions: list[str] = []
        categories: set[str] = set()
        for tc in test_cases:
            all_preconditions.extend(tc.preconditions)
            if tc.category:
                categories.add(tc.category)

        # Detect shared setup patterns
        needs_auth = any(
            kw in p.lower()
            for p in all_preconditions
            for kw in ("authenticated", "logged in", "valid credentials", "user is authenticated")
        )
        needs_git = any(
            kw in p.lower()
            for p in all_preconditions
            for kw in ("cloned", "git", "repository", "write access", "write permission")
        )
        needs_browser = any(
            kw in p.lower()
            for p in all_preconditions
            for kw in ("browser", "page", "login page", "github is accessible")
        )

        if is_robot:
            return self._robot_common_resource(needs_auth, needs_git, needs_browser)
        elif is_py:
            return self._python_conftest(framework_value, needs_auth, needs_git, needs_browser)
        return None

    @staticmethod
    def _robot_common_resource(
        needs_auth: bool,
        needs_git: bool,
        needs_browser: bool,
    ) -> GeneratedFile:
        """Generate resources/common.resource for Robot Framework."""
        lines = [
            "*** Settings ***",
            "Library    SeleniumLibrary",
            "Library    OperatingSystem",
            "Library    Process",
            "",
            "*** Variables ***",
            "${GITHUB_URL}       https://github.com",
            "${BROWSER}          chrome",
            "${TIMEOUT}          10s",
        ]
        if needs_auth:
            lines += [
                "${GITHUB_USER}      %{GITHUB_USER}",
                "${GITHUB_PASSWORD}  %{GITHUB_PASSWORD}",
            ]
        lines += [
            "",
            "*** Keywords ***",
        ]
        if needs_browser:
            lines += [
                "Open GitHub",
                "    Open Browser    ${GITHUB_URL}    ${BROWSER}",
                "    Set Selenium Timeout    ${TIMEOUT}",
                "",
            ]
        if needs_auth:
            lines += [
                "Login To GitHub",
                "    [Arguments]    ${username}=${GITHUB_USER}    ${password}=${GITHUB_PASSWORD}",
                "    Go To    ${GITHUB_URL}/login",
                "    Input Text      [data-testid='login-field']    ${username}",
                "    Input Password  [data-testid='password']       ${password}",
                "    Click Button    [data-testid='sign-in-button']",
                "    Wait Until Page Contains Element    [data-testid='user-navigation-header-avatar']",
                "",
            ]
        if needs_git:
            lines += [
                "Run Git Command",
                "    [Arguments]    @{cmd}",
                "    ${result}=    Run Process    git    @{cmd}    shell=True",
                "    RETURN    ${result}",
                "",
                "Clone Repository",
                "    [Arguments]    ${url}    ${target_dir}=.",
                "    ${result}=    Run Git Command    clone    ${url}    ${target_dir}",
                "    Should Be Equal As Integers    ${result.rc}    0",
                "",
            ]
        lines += [
            "Teardown Browser",
            "    Close All Browsers",
        ]
        return GeneratedFile(
            path="resources/common.resource",
            content="\n".join(lines) + "\n",
            file_type=FileType.UTILITY,
            source=GenerationSource.TEMPLATE,
            confidence=0.95,
        )

    @staticmethod
    def _python_conftest(
        framework_value: str,
        needs_auth: bool,
        needs_git: bool,
        needs_browser: bool,
    ) -> GeneratedFile:
        """Generate tests/conftest.py with shared pytest fixtures."""
        lines = [
            '"""Shared pytest fixtures for the GitHub test suite."""',
            "import os",
            "import subprocess",
            "import pytest",
        ]
        if framework_value == "playwright_py":
            lines += [
                "from playwright.sync_api import Page, Browser, BrowserContext",
                "",
                "GITHUB_URL = \"https://github.com\"",
                "GITHUB_USER = os.environ.get(\"GITHUB_USER\", \"\")",
                "GITHUB_PASSWORD = os.environ.get(\"GITHUB_PASSWORD\", \"\")",
                "",
            ]
            if needs_auth:
                lines += [
                    "@pytest.fixture",
                    "def authenticated_page(page: Page) -> Page:",
                    '    \"\"\"Fixture: log in to GitHub and return an authenticated page.\"\"\"",',
                    '    page.goto(f"{GITHUB_URL}/login")',
                    "    page.fill(\"[data-testid='login-field']\", GITHUB_USER)",
                    "    page.fill(\"[data-testid='password']\", GITHUB_PASSWORD)",
                    "    page.click(\"[data-testid='sign-in-button']\")",
                    "    page.wait_for_selector(\"[data-testid='user-navigation-header-avatar']\")",
                    "    return page",
                    "",
                ]
        elif framework_value == "selenium_py":
            lines += [
                "from selenium import webdriver",
                "from selenium.webdriver.common.by import By",
                "from selenium.webdriver.support.ui import WebDriverWait",
                "from selenium.webdriver.support import expected_conditions as EC",
                "",
                "GITHUB_URL = \"https://github.com\"",
                "GITHUB_USER = os.environ.get(\"GITHUB_USER\", \"\")",
                "GITHUB_PASSWORD = os.environ.get(\"GITHUB_PASSWORD\", \"\")",
                "",
                "@pytest.fixture",
                "def driver():",
                "    d = webdriver.Chrome()",
                "    d.implicitly_wait(10)",
                "    yield d",
                "    d.quit()",
                "",
            ]
            if needs_auth:
                lines += [
                    "@pytest.fixture",
                    "def authenticated_driver(driver):",
                    '    \"\"\"Fixture: log in to GitHub and return an authenticated WebDriver.\"\"\"",',
                    '    driver.get(f"{GITHUB_URL}/login")',
                    "    WebDriverWait(driver, 10).until(",
                    "        EC.presence_of_element_located((By.CSS_SELECTOR, \"[data-testid='login-field']\"))",
                    "    )",
                    "    driver.find_element(By.CSS_SELECTOR, \"[data-testid='login-field']\").send_keys(GITHUB_USER)",
                    "    driver.find_element(By.CSS_SELECTOR, \"[data-testid='password']\").send_keys(GITHUB_PASSWORD)",
                    "    driver.find_element(By.CSS_SELECTOR, \"[data-testid='sign-in-button']\").click()",
                    "    WebDriverWait(driver, 10).until(",
                    "        EC.presence_of_element_located((By.CSS_SELECTOR, \"[data-testid='user-navigation-header-avatar']\"))",
                    "    )",
                    "    return driver",
                    "",
                ]
        if needs_git:
            lines += [
                "def run_git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:",
                '    \"\"\"Run a git command and return the CompletedProcess result.\"\"\"",',
                "    return subprocess.run([\"git\", *args], capture_output=True, text=True, cwd=cwd)",
                "",
            ]
        return GeneratedFile(
            path="tests/conftest.py",
            content="\n".join(lines) + "\n",
            file_type=FileType.FIXTURE,
            source=GenerationSource.TEMPLATE,
            confidence=0.95,
        )

    def _generate_common_files(
        self,
        arch: dict,
        framework_value: str,
        cfg: dict,
    ) -> list[GeneratedFile]:
        """Generate page objects, fixtures, and common resource files from suite architecture."""
        files: list[GeneratedFile] = []
        if not arch:
            return files

        # Collect files declared in the architecture that are NOT test files
        for file_entry in arch.get("files", []):
            path: str = file_entry.get("path", "")
            purpose: str = file_entry.get("purpose", "")
            symbols: list[str] = file_entry.get("symbols_defined", [])

            if not path or path.startswith("tests/"):
                continue  # test files are already generated per test case

            # Determine file type from path/purpose
            if any(x in path for x in ("page", "pages")):
                file_type = FileType.PAGE_OBJECT
            elif any(x in path for x in ("fixture", "conftest", "setup")):
                file_type = FileType.FIXTURE
            elif any(x in path for x in ("util", "helper", "common", "resource", "keyword")):
                file_type = FileType.UTILITY
            else:
                file_type = FileType.UTILITY

            # Build a stub with declared symbols listed as comments/placeholders
            content = self._stub_common_file(path, purpose, symbols, framework_value, cfg)
            files.append(GeneratedFile(
                path=path,
                content=content,
                file_type=file_type,
                source=GenerationSource.LLM,
                confidence=0.7,
            ))

        return files

    def _stub_common_file(
        self,
        path: str,
        purpose: str,
        symbols: list[str],
        framework_value: str,
        cfg: dict,
    ) -> str:
        """Generate a stub for a common/shared file declared in the architecture."""
        is_robot = framework_value == "robot_framework"
        is_py = cfg.get("language") == "Python"
        is_ts = cfg.get("language") == "TypeScript"
        is_java = cfg.get("language") == "Java"

        if is_robot:
            sym_lines = "\n".join(f"    # {s}" for s in symbols)
            return (
                f"*** Settings ***\n"
                f"Documentation    {purpose}\n\n"
                f"*** Variables ***\n"
                f"# Declare shared variables here\n\n"
                f"*** Keywords ***\n"
                f"{sym_lines}\n"
            )
        elif is_py:
            sym_lines = "\n\n".join(
                f"def {s}():\n    raise NotImplementedError  # TODO: implement"
                for s in symbols if re.match(r"^[a-zA-Z_]", s)
            )
            return (
                f'"""\n{purpose}\n"""\n\n'
                + (sym_lines or "# TODO: implement shared helpers\n")
            )
        elif is_ts:
            sym_lines = "\n\n".join(
                f"export async function {s}() {{\n  // TODO: implement\n}}"
                for s in symbols if re.match(r"^[a-zA-Z_]", s)
            )
            return (
                f"// {purpose}\n\n"
                + (sym_lines or "// TODO: implement shared helpers\n")
            )
        elif is_java:
            sym_lines = "\n\n".join(
                f"    public static void {s}() {{\n        // TODO: implement\n    }}"
                for s in symbols if re.match(r"^[a-zA-Z_]", s)
            )
            class_name = re.sub(r"[^a-zA-Z0-9]", "", path.split("/")[-1].split(".")[0]) or "CommonHelper"
            return (
                f"// {purpose}\npublic class {class_name} {{\n"
                + (sym_lines or "    // TODO: implement shared helpers\n")
                + "\n}\n"
            )
        return f"# {purpose}\n# Symbols: {', '.join(symbols)}\n"

    def _generate_legacy(self, request: CodeGenRequest) -> GeneratedTestSuite:
        """Original template+LLM pipeline (fallback)."""
        start_time = time.time()
        framework = request.target_framework
        options = request.options
        selector_map = request.selector_map

        logger.info(
            "CodeGenOrchestrator: START — %d test cases, framework=%s",
            len(request.test_cases), framework.value,
        )

        # Update template engine with user's selector map
        if selector_map:
            self._template_engine.update_selector_map(selector_map)

        # Stats tracking
        stats = GenerationStats(total_steps=sum(len(tc.steps) for tc in request.test_cases))

        # Phase 1: Generate code for each test case
        test_results: list[TestCaseGenerationResult] = []
        for tc in request.test_cases:
            result = self._generate_single_test(tc, framework, options, selector_map, stats)
            test_results.append(result)

        # Phase 2: Multi-test optimization
        optimizer = TestOptimizer(framework=framework)
        optimization = optimizer.optimize(test_results)

        # Phase 3: Render final output
        suite = self._renderer.render(
            test_results=test_results,
            optimization=optimization,
            framework=framework,
            options=options,
            selector_map=selector_map,
        )

        # Finalize stats
        elapsed_ms = int((time.time() - start_time) * 1000)
        stats.time_elapsed_ms = elapsed_ms
        suite.stats = stats

        logger.info(
            "CodeGenOrchestrator: DONE — %d files generated in %dms "
            "(template=%d, llm=%d, learned=%d)",
            len(suite.files), elapsed_ms,
            stats.template_handled, stats.llm_handled, stats.patterns_learned,
        )

        return suite

    def generate_single(
        self,
        test_case: ManualTestCase,
        framework: TargetFramework = TargetFramework.PLAYWRIGHT_TS,
        selector_map: dict[str, str] | None = None,
    ) -> GeneratedTestSuite:
        """Convenience method to generate code for a single test case."""
        request = CodeGenRequest(
            test_cases=[test_case],
            target_framework=framework,
            selector_map=selector_map or {},
        )
        return self.generate(request)

    def get_store_stats(self) -> dict[str, Any]:
        """Return statistics about the template store (for monitoring)."""
        return self._template_store.get_stats_summary()

    # ------------------------------------------------------------------
    # Phase 1: Individual Test Case Generation
    # ------------------------------------------------------------------

    def _generate_single_test(
        self,
        test_case: ManualTestCase,
        framework: TargetFramework,
        options: CodeGenOptions,
        selector_map: dict[str, str],
        stats: GenerationStats,
    ) -> TestCaseGenerationResult:
        """Generate code for a single test case (all steps)."""
        step_results: list[StepGenerationResult] = []
        context_lines: list[str] = []

        # Build initial context from test case metadata
        if test_case.description:
            context_lines.append(f"Test: {test_case.description}")
        if test_case.preconditions:
            context_lines.append(f"Preconditions: {', '.join(test_case.preconditions)}")

        for step in test_case.steps:
            # Build context from preceding steps
            context = "\n".join(context_lines[-5:])  # Last 5 lines of context

            # Route: template or LLM
            result = self._generate_step(
                step=step,
                framework=framework,
                options=options,
                context=context,
                selector_map=selector_map,
                stats=stats,
            )

            step_results.append(result)
            # Add to context for next step
            context_lines.append(f"Step {step.step_number}: {step.action} → {result.generated_code[:80]}")

        # Assemble into complete test
        assembled = self._assemble_test(test_case, step_results, framework)

        # Validate the assembled test
        validation = self._validate_and_fix(assembled, framework, options, stats)

        return TestCaseGenerationResult(
            test_case_id=test_case.id,
            title=test_case.title,
            step_results=step_results,
            assembled_code=assembled if validation.is_valid else assembled,
            validation=validation,
            source=self._determine_source(step_results),
        )

    # ------------------------------------------------------------------
    # Step Generation (Template vs LLM Routing)
    # ------------------------------------------------------------------

    def _generate_step(
        self,
        step: Any,
        framework: TargetFramework,
        options: CodeGenOptions,
        context: str,
        selector_map: dict[str, str],
        stats: GenerationStats,
    ) -> StepGenerationResult:
        """Generate code for a single step with confidence-based routing.

        Flow:
          1. Try template engine
          2. If confidence >= threshold → verify with LLM (quick check)
          3. If confidence < threshold → use LLM generator
          4. On LLM success → learn new template
        """
        threshold = options.confidence_threshold

        # Step 1: Try template engine
        match = self._template_engine.match(step, framework)

        if match.matched and match.confidence >= threshold:
            # Template matched with high confidence
            logger.debug(
                "Step %d: template match (pattern=%s, conf=%.2f)",
                step.step_number, match.pattern_id, match.confidence,
            )

            # Optional: LLM verification of template output — disabled to conserve TPM
            verified = True

            if verified:
                stats.template_handled += 1
                self._template_store.record_usage(match.pattern_id, success=True)
                return StepGenerationResult(
                    step_number=step.step_number,
                    original_action=step.action,
                    generated_code=match.generated_code,
                    source=GenerationSource.TEMPLATE,
                    confidence=match.confidence,
                    action_type=match.action_type,
                    verified=True,
                )
            else:
                # Template output rejected by LLM — fall through to LLM generation
                self._template_store.record_usage(match.pattern_id, success=False)
                logger.debug("Step %d: template output rejected by LLM verifier", step.step_number)

        # Step 2: LLM generation (template failed or low confidence)
        result = self._llm_generator.generate_step(
            step=step,
            framework=framework,
            context=context,
            selector_map=selector_map,
        )
        stats.llm_handled += 1

        # Step 3: Validate step code
        step_validation = self._validator.validate_step_code(result.generated_code, framework)
        if not step_validation.is_valid and options.max_llm_retries > 0:
            # Try to fix
            fixed_code = self._llm_generator.fix_code(
                result.generated_code,
                "; ".join(step_validation.errors),
                framework,
            )
            result.generated_code = fixed_code
            result.source = GenerationSource.LLM_FIX

        # Step 4: Learn from successful LLM generation
        if result.confidence > 0.7 and options.learning_mode != "off":
            learned = self._template_store.learn(
                step_action=step.action,
                generated_code=result.generated_code,
                action_type=result.action_type,
                framework=framework,
                verified=(result.confidence > 0.8),
            )
            if learned:
                stats.patterns_learned += 1
                # Also add to the live engine for this session
                self._template_engine.add_learned_pattern(learned)

        return result

    # ------------------------------------------------------------------
    # Code Assembly
    # ------------------------------------------------------------------

    def _assemble_test(
        self,
        test_case: ManualTestCase,
        step_results: list[StepGenerationResult],
        framework: TargetFramework,
    ) -> str:
        """Assemble individual step codes into a complete test function."""
        return self._renderer.assemble_test_function(
            test_case=test_case,
            step_results=step_results,
            framework=framework,
        )

    # ------------------------------------------------------------------
    # Validation & Fix Loop
    # ------------------------------------------------------------------

    def _validate_and_fix(
        self,
        code: str,
        framework: TargetFramework,
        options: CodeGenOptions,
        stats: GenerationStats,
    ) -> ValidationResult:
        """Validate assembled code and attempt fixes if needed."""
        # First validation pass
        result = self._validator.validate(code, framework, strict=options.strict_mode)

        if result.is_valid:
            return result

        # Auto-fix imports
        if not result.imports_ok:
            code = self._validator.auto_fix_imports(code, framework)
            result = self._validator.validate(code, framework, strict=options.strict_mode)
            if result.is_valid:
                return result

        # LLM fix loop
        retries = 0
        while not result.is_valid and retries < options.max_llm_retries:
            error_msg = "; ".join(result.errors[:3])  # Send top 3 errors
            code = self._llm_generator.fix_code(code, error_msg, framework)
            result = self._validator.validate(code, framework, strict=options.strict_mode)
            retries += 1
            stats.validation_retries += 1
            logger.debug("Validation fix attempt %d: valid=%s", retries, result.is_valid)

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _determine_source(step_results: list[StepGenerationResult]) -> GenerationSource:
        """Determine overall generation source from step results."""
        sources = {sr.source for sr in step_results}
        if sources == {GenerationSource.TEMPLATE}:
            return GenerationSource.TEMPLATE
        if sources == {GenerationSource.LLM}:
            return GenerationSource.LLM
        return GenerationSource.HYBRID
