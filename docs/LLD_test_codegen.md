# Low-Level Design: Hybrid Test Code Generation

**Version:** 2.0
**Date:** July 2025
**Module:** `src/codegen/`

## 1. Overview

This module converts **manual test cases** (natural language descriptions) into **executable automated test code** using a hybrid approach:

- **Primary path** — LangGraph 5-agent pipeline (plan → resolve → generate → validate → assemble)
- **Fallback path** — Template+LLM pipeline (TemplateEngine → LLMGenerator → CodeValidator → CodeRenderer)

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------| 
| Primary generation | LangGraph 5-agent pipeline | Analyses full test case before writing code; coherent single-call generation |
| Fallback | Legacy template+LLM pipeline | No regression risk if agent pipeline fails |
| User config | `agent_config.py` single file | All prompts, framework context, action keywords, pipeline settings in one place |
| Validation | Multi-layer (AST + lint + structure + LLM fix) | Guarantee runnable output |
| Learning | Self-learning template store | Reduces LLM cost over time (legacy path) |

---

## 2. Architecture

```
                    ┌─────────────────────┐
                    │   ALL TEST CASES    │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ SUITE ARCHITECT     │
                    │                     │
                    │ • dependencies      │
                    │ • shared variables  │
                    │ • helpers           │
                    │ • fixtures          │
                    │ • page objects      │
                    │ • files             │
                    │ • symbol ownership  │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ SELECTOR RESOLVER  │
                    └──────────┬──────────┘
                               ↓
              ┌────────────────┴────────────────┐
              ↓                                 ↓
     ┌──────────────────┐              ┌──────────────────┐
     │ COMMON CODE GEN  │              │ TEST CODE GEN    │
     │                  │              │                  │
     │ helpers          │              │ TC01             │
     │ fixtures         │              │ TC02             │
     │ page objects     │              │ TC03             │
     │ clients          │              │ ...              │
     └────────┬─────────┘              └────────┬─────────┘
              └────────────────┬────────────────┘
                               ↓
                    ┌─────────────────────┐
                    │ SYMBOL VALIDATOR    │
                    │                     │
                    │ undefined symbols   │
                    │ duplicate symbols   │
                    │ imports             │
                    │ signatures          │
                    │ scope               │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ ASSEMBLER / LINKER  │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ FINAL VALIDATOR     │
                    │                     │
                    │ "Can this actually  │
                    │  run?"              │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ REPAIR IF REQUIRED  │
                    └─────────────────────┘
```

---

## 3. Module Structure

```
src/codegen/
├── __init__.py
├── agent_config.py          # ← Single user-editable file (prompts + settings)
├── pipeline.py              # LangGraph CodeGenState + build_codegen_pipeline()
├── orchestrator.py          # CodeGenOrchestrator (agent + legacy paths)
├── agents/
│   ├── __init__.py
│   ├── scenario_planner.py  # Agent 1: analyse test case, classify steps
│   ├── selector_resolver.py # Agent 2: resolve element names to CSS selectors
│   ├── step_generator.py    # Agent 3: generate complete test code in one LLM call
│   ├── validator_agent.py   # Agent 4: review code, apply fixes
│   └── assembler_agent.py   # Agent 5: combine into final project-ready file
├── models.py                # Pydantic data models
├── template_engine.py       # Pattern matching + confidence scoring (legacy)
├── llm_generator.py         # LLM-based step generation (legacy)
├── template_store.py        # Self-learning pattern store (legacy)
├── validator.py             # Syntax + structure validation
├── optimizer.py             # Multi-test optimization (legacy)
└── renderer.py              # Framework-specific code rendering
```

---

## 4. LangGraph Agent Pipeline

### 4.1 CodeGenState TypedDict (`pipeline.py`)

All keys must be declared — LangGraph silently drops undeclared keys.

```python
class CodeGenState(TypedDict):
    # Inputs
    test_case: dict[str, Any]          # ManualTestCase as dict
    framework: str                     # e.g. "playwright_ts"
    selector_map: dict[str, str]       # user-provided element → selector

    # ScenarioPlanner output
    scenario: dict[str, Any]           # structured scenario analysis
    classified_steps: list[dict]       # steps with action_type added

    # SelectorResolver output
    resolved_selectors: dict[str, str] # merged user + LLM-resolved selectors

    # StepGenerator output
    generated_code: str
    generation_error: str

    # ValidatorAgent output
    validation_result: dict[str, Any]
    validation_attempts: int

    # AssemblerAgent output
    assembled_code: str

    # Injected dependency
    _llm_client: Any
```

### 4.2 Graph Topology

```
plan ──→ resolve ──→ generate ──→ validate ──→ assemble ──→ END
                                     │
                         (is_valid=False AND attempts < max_retries)
                                     │
                                     └──→ generate (retry)
```

### 4.3 Agent Responsibilities

**ScenarioPlanner** (`agents/scenario_planner.py`)
- Analyses the full test case before any code is written
- Classifies each step by action type (navigate, click, fill, assert, etc.)
- Returns `scenario` dict and `classified_steps` list
- Heuristic fallback when LLM fails

**SelectorResolver** (`agents/selector_resolver.py`)
- Extracts element names from step text
- Resolves to CSS selectors via LLM
- Merges user-provided selectors with LLM-resolved ones
- Returns `resolved_selectors` dict

**StepGenerator** (`agents/step_generator.py`)
- Generates complete test code in **one LLM call** using full scenario context + resolved selectors
- Uses `STEP_GENERATOR_SYSTEM` from `agent_config.py` (dict per framework)
- Returns `generated_code` string

**ValidatorAgent** (`agents/validator_agent.py`)
- Reviews generated code for syntax and structure issues
- Applies LLM fixes if invalid
- `should_retry_validation()` is the conditional edge function:
  - Returns `"retry"` if `is_valid=False` AND `validation_attempts < max_retries`
  - Returns `"assemble"` otherwise

**AssemblerAgent** (`agents/assembler_agent.py`)
- Combines test functions into final project-ready file
- Skips LLM if code already looks complete
- Returns `assembled_code` string

### 4.4 User-Editable Config (`agent_config.py`)

Single file containing all 8 configurable sections:

| Section | Purpose |
|---------|---------|
| `SCENARIO_PLANNER_SYSTEM` | System prompt for ScenarioPlanner |
| `SCENARIO_PLANNER_USER_TEMPLATE` | User message template |
| `SELECTOR_RESOLVER_SYSTEM` | System prompt for SelectorResolver |
| `SELECTOR_RESOLVER_USER_TEMPLATE` | User message template |
| `STEP_GENERATOR_SYSTEM` | Dict per framework (playwright_ts, selenium_python, etc.) |
| `STEP_GENERATOR_USER_TEMPLATE` | User message template |
| `VALIDATOR_SYSTEM` | System prompt for ValidatorAgent |
| `VALIDATOR_USER_TEMPLATE` | User message template |
| `ASSEMBLER_SYSTEM` | System prompt for AssemblerAgent |
| `ASSEMBLER_USER_TEMPLATE` | User message template |
| `FRAMEWORK_CONTEXT` | Dict per framework (imports, patterns, examples) |
| `ACTION_KEYWORDS` | Keyword → action_type mapping |
| `PIPELINE_SETTINGS` | `max_retries`, `confidence_threshold`, etc. |

---

## 5. Data Models (`models.py`)

### 5.1 Input: ManualTestCase

```python
class ManualTestCase(BaseModel):
    id: str
    title: str
    description: str
    preconditions: list[str] = []
    steps: list[TestStep]
    expected_results: list[str] = []
    priority: str = "medium"
    category: str = ""
    tags: list[str] = []

class TestStep(BaseModel):
    step_number: int
    action: str
    test_data: str = ""
    expected_result: str = ""
```

### 5.2 Output: GeneratedTestSuite

```python
class GeneratedTestSuite(BaseModel):
    framework: str
    language: str
    files: list[GeneratedFile]
    install_instructions: str
    run_command: str
    selector_map: dict[str, str]
    confidence_score: float
    validation: ValidationResult
    stats: GenerationStats

class GeneratedFile(BaseModel):
    path: str
    content: str
    file_type: FileType          # TEST | PAGE_OBJECT | FIXTURE | CONFIG | DATA
    source: GenerationSource     # TEMPLATE | LLM | LLM_FIX | HYBRID
    confidence: float
```

---

## 6. CodeGenOrchestrator (`orchestrator.py`)

```python
class CodeGenOrchestrator:
    def __init__(
        self,
        llm_client: Any,
        template_store_path: str | None = None,
        auto_learn: bool = True,
        use_agent_pipeline: bool = True,   # default: True
    ) -> None: ...

    def generate(self, request: CodeGenRequest) -> GeneratedTestSuite:
        """Tries agent pipeline first, falls back to legacy."""
        if self._use_agent_pipeline:
            try:
                return self._generate_with_agents(request)
            except Exception:
                logger.warning("agent pipeline failed — falling back to legacy")
        return self._generate_legacy(request)
```

`_generate_with_agents()` calls `run_codegen_pipeline()` per test case, then assembles a `GeneratedTestSuite` from the final state's `assembled_code`.

`_generate_legacy()` runs the original TemplateEngine → LLMGenerator → CodeValidator → TestOptimizer → CodeRenderer pipeline.

---

## 7. Supported Frameworks

| Framework | Language | Test Runner | Output Format |
|-----------|----------|-------------|---------------|
| Playwright | TypeScript | @playwright/test | .spec.ts |
| Playwright | Python | pytest-playwright | test_*.py |
| Selenium | Python | pytest | test_*.py |
| Selenium | Java | TestNG/JUnit | *Test.java |
| Cypress | JavaScript | Cypress | .cy.js |
| REST Assured | Java | JUnit | *Test.java |
| Robot Framework | Robot | robot | *.robot |

---

## 8. Legacy Pipeline (Fallback)

### 8.1 Template Engine

Pattern categories: Navigation, Input, Click, Assert visible, Assert text, Wait, Select.

Confidence scoring:
```
confidence = base_score × modifier

base_score:
  Exact structural match: 0.95
  Partial match:          0.75
  Fuzzy match:            0.60

modifier:
  All named groups filled: ×1.0
  Some groups empty:       ×0.85
  Ambiguous references:    ×0.70
```

### 8.2 Self-Learning Loop

When LLM generates code for a step not handled by a template:
1. LLM generates code → passes validation
2. System extracts step structure → creates generalised regex pattern
3. Stores in `data/codegen/learned_templates.json`
4. Quality gate: `success_rate < 0.6` after 5+ uses → auto-disable

### 8.3 Code Validation Layers

| Layer | Check |
|-------|-------|
| 1 | Syntax (AST parse for Python; bracket matching for JS/TS) |
| 2 | Framework structure rules (`must_have`, `must_import`) |
| 3 | Import completeness (auto-add missing imports) |
| 4 | LLM fix loop (max 2 retries on failure) |

---

## 9. CLI Usage

```python
from src.codegen.orchestrator import CodeGenOrchestrator
from src.codegen.models import CodeGenRequest, ManualTestCase, TestStep, TargetFramework

orchestrator = CodeGenOrchestrator(llm_client=groq_client)

request = CodeGenRequest(
    test_cases=[
        ManualTestCase(
            id="TC001",
            title="Login with valid credentials",
            steps=[
                TestStep(step_number=1, action="Navigate to login page"),
                TestStep(step_number=2, action="Enter admin in username field"),
                TestStep(step_number=3, action="Click login button"),
                TestStep(step_number=4, action="Verify dashboard is shown"),
            ]
        )
    ],
    target_framework=TargetFramework.PLAYWRIGHT_TS,
    selector_map={"username field": "#username"},
)

result = orchestrator.generate(request)
for f in result.files:
    print(f.path)
    print(f.content)
```

---

## 10. File Storage

```
data/codegen/
├── learned_templates.json       # Self-learned patterns (legacy)
├── template_stats.json          # Usage/success tracking (legacy)
└── static_templates.json        # Curated base templates (legacy)
```

---

## 11. Error Handling

| Scenario | Action |
|----------|--------|
| Agent pipeline exception | Log warning, fall back to legacy pipeline |
| LLM generation fails | `generation_error` set in state; validator catches |
| Validation fails | LLM fix loop (max `PIPELINE_SETTINGS["max_retries"]`) |
| Fix loop exhausted | Return code with `# FIXME` annotation |
| No framework support | Return error with supported list |
| Empty/invalid test case | Return validation error |

---

*End of LLD Document — Version 2.0*
