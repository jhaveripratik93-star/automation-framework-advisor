"""Data models for the hybrid test code generation module."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────


class ActionType(str, Enum):
    """Classified action types for test steps."""
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    ASSERT_VISIBLE = "assert_visible"
    ASSERT_TEXT = "assert_text"
    ASSERT_URL = "assert_url"
    ASSERT_VALUE = "assert_value"
    WAIT = "wait"
    HOVER = "hover"
    SCROLL = "scroll"
    UPLOAD = "upload"
    KEYBOARD = "keyboard"
    API_CALL = "api_call"
    CUSTOM = "custom"


class TargetFramework(str, Enum):
    """Supported target frameworks for code generation."""
    PLAYWRIGHT_TS = "playwright_ts"
    PLAYWRIGHT_PY = "playwright_py"
    SELENIUM_PY = "selenium_py"
    SELENIUM_JAVA = "selenium_java"
    CYPRESS_JS = "cypress_js"
    REST_ASSURED = "rest_assured"
    ROBOT_FRAMEWORK = "robot_framework"


class GenerationSource(str, Enum):
    """Where the generated code came from."""
    TEMPLATE = "template"
    LLM = "llm"
    LLM_FIX = "llm_fix"
    HYBRID = "hybrid"


class FileType(str, Enum):
    """Type of generated file."""
    TEST = "test"
    PAGE_OBJECT = "page_object"
    FIXTURE = "fixture"
    CONFIG = "config"
    DATA = "data"
    UTILITY = "utility"
    PACKAGE = "package"


# ── Input Models ──────────────────────────────────────────────────────


class TestStep(BaseModel):
    """A single step within a manual test case."""
    step_number: int
    action: str                          # Natural language action description
    test_data: str = ""                  # Input data for this step
    expected_result: str = ""            # Per-step expected outcome


class ManualTestCase(BaseModel):
    """Input: a single manual test case to be converted."""
    id: str                              # e.g. "TC001"
    title: str                           # e.g. "Login with valid credentials"
    description: str = ""                # Natural language description
    preconditions: list[str] = []        # Setup requirements
    steps: list[TestStep]                # Ordered test steps
    expected_results: list[str] = []     # Overall expected outcomes
    priority: str = "medium"             # low / medium / high / critical
    category: str = ""                   # e.g. "authentication", "checkout"
    tags: list[str] = []


class CodeGenRequest(BaseModel):
    """Full request for test code generation."""
    test_cases: list[ManualTestCase]
    target_framework: TargetFramework = TargetFramework.PLAYWRIGHT_TS
    options: CodeGenOptions = Field(default_factory=lambda: CodeGenOptions())
    selector_map: dict[str, str] = {}    # User-provided element → selector mapping


class CodeGenOptions(BaseModel):
    """Configuration options for code generation."""
    generate_page_objects: bool = True
    generate_fixtures: bool = True
    generate_data_files: bool = True
    parameterize_similar: bool = True
    include_comments: bool = True
    strict_mode: bool = False            # If True, fail on any validation error
    max_llm_retries: int = 2
    confidence_threshold: float = 0.8
    learning_mode: str = "auto"          # "auto" | "supervised" | "off"


# ── Output Models ─────────────────────────────────────────────────────


class GeneratedFile(BaseModel):
    """A single file produced by the code generator."""
    path: str                            # Relative path in output project
    content: str                         # File content
    file_type: FileType
    source: GenerationSource = GenerationSource.HYBRID
    confidence: float = 1.0


class ValidationResult(BaseModel):
    """Result of validating a piece of generated code."""
    is_valid: bool
    syntax_ok: bool = True
    structure_ok: bool = True
    imports_ok: bool = True
    errors: list[str] = []
    warnings: list[str] = []
    undefined_symbols: list[str] = []


class GeneratedTestSuite(BaseModel):
    """Complete output of the code generation pipeline."""
    framework: str
    language: str
    files: list[GeneratedFile]
    install_instructions: str = ""
    run_command: str = ""
    selector_map: dict[str, str] = {}    # Elements needing user verification
    confidence_score: float = 0.0        # Overall confidence (0-1)
    validation: ValidationResult = Field(default_factory=ValidationResult)
    stats: GenerationStats = Field(default_factory=lambda: GenerationStats())


class GenerationStats(BaseModel):
    """Statistics about the generation process."""
    total_steps: int = 0
    template_handled: int = 0
    llm_handled: int = 0
    validation_retries: int = 0
    patterns_learned: int = 0
    time_elapsed_ms: int = 0


# ── Template Store Models ─────────────────────────────────────────────


class TemplatePattern(BaseModel):
    """A reusable code generation pattern (static or learned)."""
    id: str
    pattern: str                         # Regex pattern with named groups
    action_type: ActionType
    code_templates: dict[str, str]       # framework key → code template string
    confidence_threshold: float = 0.8
    source: str = "static"               # "static" | "learned"
    usage_count: int = 0
    success_count: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    last_used_at: str = ""
    enabled: bool = True


class MatchResult(BaseModel):
    """Result of matching a step against templates."""
    matched: bool
    pattern_id: str = ""
    confidence: float = 0.0
    generated_code: str = ""
    action_type: ActionType = ActionType.CUSTOM
    captured_groups: dict[str, str] = {}


# ── Internal Pipeline Models ──────────────────────────────────────────


class StepGenerationResult(BaseModel):
    """Result of generating code for a single test step."""
    step_number: int
    original_action: str
    generated_code: str
    source: GenerationSource
    confidence: float
    action_type: ActionType = ActionType.CUSTOM
    selector_used: str = ""              # Track which selector was used
    verified: bool = False               # Whether LLM verified the output


class TestCaseGenerationResult(BaseModel):
    """Result of generating code for a complete test case."""
    test_case_id: str
    title: str
    step_results: list[StepGenerationResult]
    assembled_code: str = ""             # Complete test function code
    validation: ValidationResult = Field(default_factory=ValidationResult)
    source: GenerationSource = GenerationSource.HYBRID


class OptimizationResult(BaseModel):
    """Result of the multi-test optimization pass."""
    page_objects: list[PageObjectSpec] = []
    fixtures: list[FixtureSpec] = []
    parameterized_groups: list[ParameterizedGroup] = []
    shared_helpers: list[HelperSpec] = []


class PageObjectSpec(BaseModel):
    """Specification for a generated page object."""
    name: str                            # e.g. "LoginPage"
    file_path: str                       # e.g. "pages/login.page.ts"
    selectors: dict[str, str]            # element name → selector
    methods: list[PageMethod]


class PageMethod(BaseModel):
    """A method within a page object."""
    name: str                            # e.g. "login"
    params: list[str]                    # e.g. ["username", "password"]
    body: str                            # Method implementation code
    doc: str = ""                        # Docstring/comment


class FixtureSpec(BaseModel):
    """Specification for a shared fixture/setup block."""
    name: str                            # e.g. "authenticated_user"
    file_path: str
    setup_code: str
    teardown_code: str = ""
    scope: str = "test"                  # "test" | "suite" | "session"


class ParameterizedGroup(BaseModel):
    """A group of test cases that differ only in data."""
    test_case_ids: list[str]
    base_code: str                       # The common code template
    parameters: list[dict[str, Any]]     # List of data variations
    parameter_names: list[str]           # Names of varying fields


class HelperSpec(BaseModel):
    """Specification for a shared helper function."""
    name: str
    params: list[str]
    body: str
    used_by: list[str] = []              # Test case IDs that use this
