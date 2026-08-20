"""CodeGen Orchestrator — main pipeline for hybrid test code generation.

Coordinates the full flow:
  1. Route each step → template engine or LLM (confidence-based)
  2. Validate generated code (syntax + structure)
  3. Fix loop on failure (LLM retry up to N times)
  4. Learn new patterns from successful LLM generations
  5. Multi-test optimization (page objects, fixtures, parameterization)
  6. Render final output via CodeRenderer
"""
from __future__ import annotations

import logging
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
    ) -> None:
        self._llm_client = llm_client

        # Initialize components
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

        Args:
            request: CodeGenRequest with test cases, target framework, and options.

        Returns:
            GeneratedTestSuite with all generated files and metadata.
        """
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

            # Optional: LLM verification of template output
            verified = True
            if match.confidence < 0.95:  # Only verify if not near-perfect
                verified = self._llm_generator.verify_code(
                    step.action, match.generated_code, framework,
                )

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
