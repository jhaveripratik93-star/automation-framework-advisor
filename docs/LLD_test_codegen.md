# Low-Level Design: Hybrid Test Code Generation

## 1. Overview

This module converts **manual test cases** (natural language descriptions) into **executable automated test code** using a hybrid approach combining template-based pattern matching with LLM-powered code generation.

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Generation strategy | Hybrid (Template + LLM) | Cost efficiency + flexibility |
| Validation | Multi-layer (AST + lint + structure + dry-run) | Guarantee runnable output |
| Learning | Self-learning template store | Reduces LLM cost over time |
| Multi-test | Optimization pass with page objects | Professional-grade output |
| Integration | New `src/codegen/` module + ToolExecutor registration | Follows existing patterns |

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CodeGen Orchestrator                       │
│  (src/codegen/orchestrator.py)                               │
└──────────┬──────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────┐     ┌──────────────────────┐
│  Template Engine      │     │  LLM Generator       │
│  (confidence-based)   │     │  (fallback + complex)│
│                       │     │                       │
│  - Pattern matching   │     │  - GroqClient.chat() │
│  - Confidence scoring │     │  - System prompts    │
│  - Code assembly      │     │  - Context-aware     │
└──────────┬────────────┘     └──────────┬───────────┘
           │                             │
           ▼                             ▼
┌──────────────────────────────────────────────────────────────┐
│                     Code Validator                            │
│  - Syntax check (AST parsing)                                │
│  - Structure validation (framework rules)                    │
│  - Import completeness check                                 │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│                   Multi-Test Optimizer                        │
│  - Detect shared steps → extract helpers/page objects         │
│  - Detect data-only diffs → parameterize                     │
│  - Extract setup/teardown → fixtures                         │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│                     Code Renderer                            │
│  - Framework-specific output (Playwright, Selenium, etc.)    │
│  - Project scaffold (package.json, config, README)           │
│  - Selector map for user review                             │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│                   Self-Learning Store                         │
│  - Store successful LLM patterns as new templates            │
│  - Track usage count and success rate                        │
│  - Auto-retire failing patterns                              │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Module Structure

```
src/codegen/
├── __init__.py              # Public API
├── models.py               # Pydantic data models
├── template_engine.py      # Pattern matching + confidence scoring
├── llm_generator.py        # LLM-based code generation
├── template_store.py       # Self-learning pattern store (JSON persistence)
├── validator.py            # Syntax + structure validation
├── optimizer.py            # Multi-test optimization (page objects, fixtures)
├── renderer.py             # Framework-specific code rendering
└── orchestrator.py         # Main pipeline coordinator
```

---

## 4. Data Models

### 4.1 Input: ManualTestCase

```python
class ManualTestCase(BaseModel):
    id: str                          # e.g. "TC001"
    title: str                       # e.g. "Login with valid credentials"
    description: str                 # Natural language description
    preconditions: list[str] = []    # Setup requirements
    steps: list[TestStep]            # Ordered test steps
    expected_results: list[str] = [] # What should happen
    priority: str = "medium"         # low/medium/high/critical
    category: str = ""               # e.g. "authentication", "checkout"
    tags: list[str] = []

class TestStep(BaseModel):
    step_number: int
    action: str                      # Natural language action
    test_data: str = ""              # Input data for this step
    expected_result: str = ""        # Per-step expected outcome
```

### 4.2 Output: GeneratedTestSuite

```python
class GeneratedTestSuite(BaseModel):
    framework: str                    # Target framework
    language: str                     # Target language
    files: list[GeneratedFile]        # All generated files
    install_instructions: str         # How to set up
    run_command: str                  # How to execute
    selector_map: dict[str, str]      # Elements needing user verification
    confidence_score: float           # Overall generation confidence

class GeneratedFile(BaseModel):
    path: str                         # Relative file path
    content: str                      # File content
    file_type: str                    # "test" | "page_object" | "fixture" | "config" | "data"
```

### 4.3 Internal: Template Pattern

```python
class TemplatePattern(BaseModel):
    id: str
    pattern: str                      # Regex pattern with named groups
    action_type: str                  # "click" | "fill" | "navigate" | "assert" | ...
    code_template: dict[str, str]     # framework → code template string
    confidence_threshold: float = 0.8
    source: str = "static"            # "static" | "learned"
    usage_count: int = 0
    success_count: int = 0
    created_at: str = ""
```

---

## 5. Pipeline Flow

### 5.1 Single Test Case Generation

```
ManualTestCase
    │
    ├─ For each step:
    │   ├─ TemplateEngine.match(step) → (code, confidence)
    │   │   ├─ confidence >= 0.8 → use template output
    │   │   │   └─ LLM quick-verify (yes/no) → accept or regenerate
    │   │   └─ confidence < 0.8 → skip to LLM
    │   │
    │   └─ LLMGenerator.generate(step, context) → code
    │       └─ On success → TemplateStore.learn(step, code)
    │
    ├─ Validator.validate(assembled_code)
    │   ├─ Syntax check (AST)
    │   ├─ Structure check (framework rules)
    │   └─ Import check
    │       └─ On failure: LLM fix loop (max 2 retries)
    │
    └─ Return validated code
```

### 5.2 Multi-Test Case Generation

```
list[ManualTestCase]
    │
    ├─ Phase 1: Generate individually (parallel-ready)
    │   └─ Each test case → single-case pipeline → raw code
    │
    ├─ Phase 2: Optimizer.analyze(all_raw_codes)
    │   ├─ Detect repeated step sequences → helper functions
    │   ├─ Detect shared selectors → page object candidates
    │   ├─ Detect data-only differences → parameterized tests
    │   └─ Detect common setup → beforeEach/fixtures
    │
    ├─ Phase 3: Renderer.render(optimized_structure)
    │   ├─ Generate page object files
    │   ├─ Generate fixture files
    │   ├─ Generate test spec files
    │   ├─ Generate config (playwright.config.ts, etc.)
    │   └─ Generate package.json / requirements.txt
    │
    └─ Return GeneratedTestSuite
```

---

## 6. Template Engine Design

### 6.1 Pattern Categories

| Category | Example Steps | Pattern |
|----------|--------------|---------|
| Navigation | "Go to login page", "Open URL" | `(navigate\|go to\|open\|visit)\s+(.+)` |
| Input | "Enter admin in username" | `(enter\|type\|input\|fill)\s+['"]?(.+?)['"]?\s+(in\|into)\s+(.+)` |
| Click | "Click login button" | `(click\|tap\|press)\s+(on\s+)?(.+)` |
| Assert visible | "Verify error message is shown" | `(verify\|check\|assert\|confirm)\s+(.+)\s+(is\s+)?(visible\|shown\|displayed)` |
| Assert text | "Verify page shows 'Welcome'" | `(verify\|check\|assert)\s+(.+)\s+(shows\|contains\|displays)\s+['"](.+)['"]` |
| Wait | "Wait for page to load" | `(wait\s+for)\s+(.+)` |
| Select | "Select 'Option A' from dropdown" | `(select\|choose)\s+['"]?(.+?)['"]?\s+(from)\s+(.+)` |

### 6.2 Confidence Scoring

```
confidence = base_score × modifier

base_score:
  - Exact structural match: 0.95
  - Partial match (some groups captured): 0.75
  - Fuzzy match (edit distance): 0.60

modifier:
  - All named groups filled: ×1.0
  - Some groups empty: ×0.85
  - Step has ambiguous references: ×0.70
```

---

## 7. Self-Learning Loop

### 7.1 Learning Trigger

When the LLM generates code for a step that was NOT handled by a template:

1. LLM generates code → passes validation
2. System extracts the step's structure → creates a generalized regex pattern
3. Maps the step pattern to the generated code template
4. Stores in `data/codegen/learned_templates.json`

### 7.2 Pattern Generalization

```
Input step:  "Put admin123 into the login email box"
LLM output:  await page.fill('[data-testid="email"]', 'admin123')

Generalized:
  pattern: "(put|place)\s+['"]?(.+?)['"]?\s+(into|in)\s+(.+)"
  template: "await page.fill('[data-testid=\"{field}\"]', '{value}')"
  variables: {value: group(2), field: group(4)}
```

### 7.3 Quality Gates

- Pattern must be distinct from existing patterns (Jaccard similarity < 0.85)
- Must have passed syntax validation
- Track `usage_count` and `success_count`
- If `success_rate < 0.6` after 5+ uses → auto-disable pattern
- Optional: user approval mode for first N patterns

---

## 8. Code Validation Layers

### Layer 1: Syntax Check
- Python: `ast.parse(code)` — catches syntax errors
- JavaScript/TypeScript: `subprocess.run(["node", "--check", file])` or regex-based bracket matching
- Fallback: Balanced bracket/quote checker for environments without Node.js

### Layer 2: Framework Structure Rules

```python
FRAMEWORK_RULES = {
    "playwright": {
        "must_have": ["test(", "expect("],
        "must_import": ["@playwright/test"],
        "structure": "test.describe > test > assertions",
    },
    "selenium_python": {
        "must_have": ["def test_", "assert"],
        "must_import": ["selenium", "webdriver"],
        "structure": "class > def test_ > assertions",
    },
    "cypress": {
        "must_have": ["describe(", "it(", "cy."],
        "must_import": [],  # Cypress auto-imports
        "structure": "describe > it > cy.commands",
    },
}
```

### Layer 3: Import Completeness
- Scan generated code for used APIs
- Cross-reference with framework's import map
- Auto-add missing imports

### Layer 4: LLM Fix Loop (on failure)
```
Code with error → Send to LLM:
  "Fix this {language} syntax error: {error_message}
   Code: {code}
   Only return the fixed code."
→ Re-validate (max 2 retries)
```

---

## 9. Multi-Test Optimizer

### 9.1 Detection Algorithms

**Repeated Steps (→ helpers/page objects):**
- Hash each step's generated code (ignoring whitespace/variable names)
- If same hash appears in 2+ test cases → extract to shared helper
- If 3+ steps from same "page" context → create page object class

**Data-Only Differences (→ parameterized tests):**
- Compare test cases pairwise
- If steps are identical except for literal values → parameterize
- Extract varying values into a test data array

**Common Setup (→ fixtures/beforeEach):**
- Find longest common prefix across all test case step sequences
- If ≥ 2 common leading steps → extract to beforeEach/setup fixture

### 9.2 Output Structure Decision

```
If page_object_candidates >= 1:
    → Generate: pages/{name}.page.ts
If fixture_candidates >= 1:
    → Generate: fixtures/{name}.fixture.ts
If parameterized_groups >= 1:
    → Generate: test-data/{name}.data.ts
Always:
    → Generate: tests/{name}.spec.ts
    → Generate: config file (playwright.config.ts etc.)
    → Generate: package.json / requirements.txt
```

---

## 10. Supported Frameworks (Initial)

| Framework | Language | Test Runner | Output Format |
|-----------|----------|-------------|---------------|
| Playwright | TypeScript | @playwright/test | .spec.ts |
| Playwright | Python | pytest-playwright | test_*.py |
| Selenium | Python | pytest | test_*.py |
| Selenium | Java | TestNG/JUnit | *Test.java |
| Cypress | JavaScript | Cypress | .cy.js |
| Rest Assured | Java | JUnit | *Test.java |
| Robot Framework | Robot | robot | *.robot |

---

## 11. Integration Points

### 11.1 ToolExecutor Registration

Add `generate_test_code` tool to `src/tools/executor.py`:

```python
self.tools["generate_test_code"] = self._generate_test_code
```

### 11.2 Streamlit UI (Future)

New tab in the app: "Code Generator"
- Upload manual test cases (Excel/JSON)
- Select target framework
- Configure options (page objects, fixtures, etc.)
- Preview generated code
- Download as ZIP

### 11.3 CLI Usage

```python
from src.codegen import CodeGenOrchestrator

orchestrator = CodeGenOrchestrator(llm_client=groq_client)
result = orchestrator.generate(
    test_cases=[...],
    target_framework="playwright",
    language="typescript",
    options={"page_objects": True, "fixtures": True}
)
```

---

## 12. File Storage

```
data/codegen/
├── learned_templates.json       # Self-learned patterns
├── template_stats.json          # Usage/success tracking
└── static_templates.json        # Curated base templates (shipped with tool)
```

---

## 13. Error Handling Strategy

| Scenario | Action |
|----------|--------|
| Template match confidence < 0.8 | Route to LLM |
| LLM generation fails (API error) | Retry once, then return partial with warning |
| Syntax validation fails | LLM fix loop (max 2 retries) |
| Fix loop exhausted | Return code with `# FIXME` annotation |
| No framework support | Return error with supported list |
| Empty/invalid test case input | Return validation error |

---

## 14. Cost Optimization

| Optimization | Savings |
|-------------|---------|
| Template handles common steps | ~70% fewer LLM calls after learning |
| LLM verification (short prompt) vs full generation | ~60% fewer tokens per verified step |
| Batch multiple steps in one LLM call | ~40% fewer API calls |
| Cache identical step lookups | Eliminates duplicate calls |
| Self-learning accumulation | Decreasing LLM dependency over time |

---

## 15. Limitations & Future Work

### Current Limitations
- Element selectors are best-guess (user must verify)
- Cannot execute tests against real app (no browser integration)
- Limited to supported frameworks listed above
- Template learning requires validation pass (won't learn from failed generations)

### Future Enhancements
- Browser extension to capture real selectors from app DOM
- Visual test recording → manual test case extraction
- Test maintenance agent (update selectors when app changes)
- Coverage analysis integration (which manual tests are already automated)
- Parallel generation for large test suites
