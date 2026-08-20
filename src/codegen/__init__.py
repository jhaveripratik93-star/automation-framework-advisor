"""Hybrid Test Code Generation Module.

Converts manual test cases into executable automated test code using a
hybrid approach: template-based pattern matching + LLM-powered generation.

Features:
  - Confidence-based routing (template vs LLM)
  - Self-learning template store (reduces LLM cost over time)
  - Multi-layer code validation (syntax, structure, imports)
  - Multi-test optimization (page objects, fixtures, parameterized tests)
  - Framework-specific rendering (Playwright, Selenium, Cypress, etc.)

Usage:
    from src.codegen import CodeGenOrchestrator, CodeGenRequest, ManualTestCase, TestStep

    # Create orchestrator with LLM client
    orchestrator = CodeGenOrchestrator(llm_client=groq_client)

    # Define manual test cases
    test_cases = [
        ManualTestCase(
            id="TC001",
            title="Login with valid credentials",
            steps=[
                TestStep(step_number=1, action="Navigate to the login page"),
                TestStep(step_number=2, action="Enter 'admin' in the username field"),
                TestStep(step_number=3, action="Enter 'password123' in the password field"),
                TestStep(step_number=4, action="Click the login button"),
                TestStep(step_number=5, action="Verify the dashboard is visible"),
            ],
        )
    ]

    # Generate automated test code
    request = CodeGenRequest(
        test_cases=test_cases,
        target_framework=TargetFramework.PLAYWRIGHT_TS,
    )
    result = orchestrator.generate(request)

    # Access generated files
    for f in result.files:
        print(f.path, f.file_type, f.confidence)
"""
from __future__ import annotations

from src.codegen.models import (
    ActionType,
    CodeGenOptions,
    CodeGenRequest,
    FileType,
    GeneratedFile,
    GeneratedTestSuite,
    GenerationSource,
    GenerationStats,
    ManualTestCase,
    MatchResult,
    OptimizationResult,
    StepGenerationResult,
    TargetFramework,
    TemplatePattern,
    TestCaseGenerationResult,
    TestStep,
    ValidationResult,
)
from src.codegen.orchestrator import CodeGenOrchestrator
from src.codegen.template_engine import TemplateEngine
from src.codegen.llm_generator import LLMGenerator
from src.codegen.template_store import TemplateStore
from src.codegen.validator import CodeValidator
from src.codegen.optimizer import TestOptimizer
from src.codegen.renderer import CodeRenderer

__all__ = [
    # Main orchestrator
    "CodeGenOrchestrator",
    # Models
    "ManualTestCase",
    "TestStep",
    "CodeGenRequest",
    "CodeGenOptions",
    "GeneratedTestSuite",
    "GeneratedFile",
    "ValidationResult",
    "GenerationStats",
    "TargetFramework",
    "ActionType",
    "FileType",
    "GenerationSource",
    "TemplatePattern",
    "MatchResult",
    "StepGenerationResult",
    "TestCaseGenerationResult",
    "OptimizationResult",
    # Components (for advanced use)
    "TemplateEngine",
    "LLMGenerator",
    "TemplateStore",
    "CodeValidator",
    "TestOptimizer",
    "CodeRenderer",
]
