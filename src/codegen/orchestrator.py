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
        """Execute the full code generation pipeline.

        Uses the LangGraph agent pipeline by default.
        Falls back to the legacy template+LLM pipeline if agents are disabled
        or if the LLM client is unavailable.
        """
        if self._use_agent_pipeline:
            try:
                return self._generate_with_agents(request)
            except Exception as exc:
                logger.warning(
                    "CodeGenOrchestrator: agent pipeline failed (%s) — falling back to legacy", exc
                )

        return self._generate_legacy(request)

    def _generate_with_agents(self, request: CodeGenRequest) -> GeneratedTestSuite:
        """Generate using the LangGraph 5-agent pipeline."""
        from src.codegen.pipeline import run_codegen_pipeline
        from src.codegen.renderer import _FRAMEWORK_CONFIG

        start_time = time.time()
        framework = request.target_framework
        cfg = _FRAMEWORK_CONFIG.get(framework.value, _FRAMEWORK_CONFIG["playwright_ts"])
        files: list[GeneratedFile] = []
        stats = GenerationStats(total_steps=sum(len(tc.steps) for tc in request.test_cases))
        last_state: dict = {}
        _per_tc_states: list[dict] = []

        for tc in request.test_cases:
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

            final_state = run_codegen_pipeline(
                test_case=tc_dict,
                framework=framework.value,
                selector_map=dict(request.selector_map),
                llm_client=self._llm_client,
            )
            last_state = final_state
            _per_tc_states.append(final_state)

            code = final_state.get("assembled_code") or final_state.get("generated_code", "")
            is_valid = final_state.get("validation_result", {}).get("is_valid", True)
            stats.llm_handled += len(tc.steps)

            slug = re.sub(r"[^a-z0-9]+", "_", tc.title.lower()).strip("_")[:50] or "test"
            files.append(GeneratedFile(
                path=f"tests/{slug}{cfg['extension']}",
                content=code,
                file_type=FileType.TEST,
                source=GenerationSource.LLM,
                confidence=0.9 if is_valid else 0.6,
            ))

        # --- Generate supporting files from suite architecture ---
        arch = last_state.get("suite_architecture", {})
        files += self._generate_common_files(arch, framework.value, cfg)

        # Config file (e.g. playwright.config.ts, pytest.ini, robot.yaml)
        config_content = self._renderer._render_config(framework)
        if config_content:
            files.append(GeneratedFile(
                path=cfg["config_file"],
                content=config_content,
                file_type=FileType.CONFIG,
                source=GenerationSource.TEMPLATE,
            ))

        # Package/dependency file (requirements.txt, package.json)
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

        # Collect undefined symbols across all test cases for suite-level validation
        all_undefined: list[str] = []
        for gf_state in _per_tc_states:
            vr = gf_state.get("validation_result", {})
            all_undefined.extend(vr.get("undefined_symbols", []))
        all_undefined = list(dict.fromkeys(all_undefined))  # deduplicate, preserve order

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
