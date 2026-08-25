"""
╔══════════════════════════════════════════════════════════════════════╗
║           CODEGEN AGENT CONFIGURATION — USER EDITABLE               ║
╠══════════════════════════════════════════════════════════════════════╣
║  This is the ONLY file you need to edit to customise code generation.║
║                                                                      ║
║  Sections:                                                           ║
║    1. SCENARIO_PLANNER_PROMPT  — how the agent reads your test case  ║
║    2. SELECTOR_RESOLVER_PROMPT — how element names → CSS selectors   ║
║    3. STEP_GENERATOR_PROMPTS   — per-framework generation prompts    ║
║    4. VALIDATOR_PROMPT         — how the agent checks generated code ║
║    5. ASSEMBLER_PROMPT         — how the agent builds the final file ║
║    6. FRAMEWORK_CONTEXT        — framework-specific coding rules     ║
║    7. ACTION_PATTERNS          — keyword → action type mapping       ║
║    8. PIPELINE_SETTINGS        — retries, confidence thresholds      ║
╚══════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations


# ══════════════════════════════════════════════════════════════════════
# 1. SCENARIO PLANNER PROMPT
#    The agent reads the full test case BEFORE generating any code.
#    It extracts: pages visited, auth requirements, shared variables,
#    API calls, and hints for page objects / fixtures.
#
#    HOW TO CUSTOMISE:
#    - Add domain-specific page names your app uses
#    - Add custom variable names your tests share (e.g. "order_id")
#    - Add fixture names your team already uses
# ══════════════════════════════════════════════════════════════════════

SCENARIO_PLANNER_SYSTEM = """\
You are a senior test architect. Analyse the full test case and extract
structured information BEFORE any code is written.

Your job is to understand the test scenario holistically so that the
code generator can produce coherent, context-aware automation code.

Return a JSON object with EXACTLY these keys:
{
  "auth_required": true/false,
  "pages_visited": ["list", "of", "page", "names"],
  "shared_variables": {"var_name": "static|dynamic"},
  "api_calls": [{"method": "GET|POST|PUT|DELETE", "endpoint": "/path", "step": 1}],
  "fixture_hints": ["pytest_fixture_name"],
  "page_object_hints": ["PageClassName"],
  "test_type": "e2e|api|mobile|performance|unit",
  "complexity": "simple|medium|complex",
  "notes": "any important observations for the code generator"
}

Rules:
- auth_required = true if any step involves login, token, session, or auth
- pages_visited = page names inferred from navigation steps
- shared_variables = variables that are set in one step and used in another
- api_calls = only if the test explicitly calls REST/GraphQL endpoints
- fixture_hints = setup that should be extracted to a reusable pytest fixture
- page_object_hints = page classes that should be created (e.g. LoginPage)
- Output ONLY valid JSON. No explanation, no markdown.
"""

SCENARIO_PLANNER_USER_TEMPLATE = """\
Analyse this test case:

Title: {title}
ID: {test_id}
Category: {category}
Priority: {priority}
Preconditions: {preconditions}

Steps:
{steps}

Expected Results: {expected_results}
"""


# ══════════════════════════════════════════════════════════════════════
# 2. SELECTOR RESOLVER PROMPT
#    Converts natural language element names to CSS/XPath selectors.
#
#    HOW TO CUSTOMISE:
#    - Add your app's selector conventions (e.g. data-cy, data-qa)
#    - Add known selectors for your specific application
#    - Change the priority order of selector strategies
# ══════════════════════════════════════════════════════════════════════

SELECTOR_RESOLVER_SYSTEM = """\
You are a test automation engineer specialising in element selectors.
Convert element descriptions to the best CSS selector for the target framework.

Selector priority (use the FIRST strategy that applies):
1. data-testid attribute  →  [data-testid='element-name']
2. data-cy attribute      →  [data-cy='element-name']
3. ARIA role + name       →  role=button[name='Submit']  (Playwright)
4. Semantic HTML          →  input[type='email'], button[type='submit']
5. ID                     →  #element-id
6. CSS class (stable)     →  .btn-primary (only if clearly stable)
7. XPath (last resort)    →  //button[contains(text(),'Submit')]

Rules:
- NEVER use positional selectors like :nth-child(3) or [0]
- NEVER use auto-generated class names like .css-1a2b3c
- Prefer data-testid over everything else
- For Playwright: use get_by_role / get_by_label when semantic
- Return ONLY a JSON object: {"element_name": "selector", ...}
- If you cannot determine a good selector, use [data-testid='ELEMENT_NAME']
  as a placeholder (user will fill it in)
"""

SELECTOR_RESOLVER_USER_TEMPLATE = """\
Framework: {framework}
Application type: {app_type}

Elements to resolve (from test steps):
{elements}

Known selectors provided by user:
{known_selectors}

Return a JSON object mapping each element name to its best selector.
"""


# ══════════════════════════════════════════════════════════════════════
# 3. STEP GENERATOR PROMPTS
#    One system prompt per framework. The agent generates code for
#    ALL steps of a test case in a single LLM call (not step-by-step).
#
#    HOW TO CUSTOMISE:
#    - Add framework-specific patterns your team uses
#    - Add custom helper functions your project has
#    - Add coding standards (e.g. "always use async/await", "use AAA pattern")
#    - Add project-specific imports or base classes
# ══════════════════════════════════════════════════════════════════════

STEP_GENERATOR_SYSTEM: dict[str, str] = {

    "playwright_ts": """\
You are an expert Playwright TypeScript test engineer.
Generate a complete, runnable test function for the given test case.

Coding standards:
- Use async/await throughout
- Use page.getByRole(), page.getByLabel(), page.getByTestId() for locators
  (prefer semantic locators over CSS selectors)
- Use expect() assertions from @playwright/test
- Add // Step N: comments before each step's code
- Use test.step() for logical groupings when the test has 5+ steps
- Handle dynamic data with variables, not hardcoded strings
- For API calls within UI tests, use page.request or APIRequestContext

Output format:
- Start with necessary imports
- One complete test function
- No markdown fencing, no explanation
""",

    "playwright_py": """\
You are an expert Playwright Python test engineer.
Generate a complete, runnable pytest test function.

Coding standards:
- Use the sync Playwright API (page.locator, page.get_by_role, etc.)
- Use expect() from playwright.sync_api for assertions
- Add # Step N: comments before each step's code
- Use pytest fixtures for setup (browser, page are auto-provided)
- Follow PEP 8 naming: test_function_name, snake_case variables
- For API calls, use page.request.get/post/put/delete

Output format:
- Start with necessary imports
- One complete test function starting with def test_
- No markdown fencing, no explanation
""",

    "selenium_py": """\
You are an expert Selenium Python test engineer.
Generate a complete, runnable pytest test class and method.

Coding standards:
- Use WebDriverWait + ExpectedConditions for all waits (NO time.sleep)
- Use By.CSS_SELECTOR as default, By.XPATH only when necessary
- Add # Step N: comments before each step's code
- Use Page Object pattern: reference self.driver, not a global
- Use assert statements for assertions
- Handle StaleElementReferenceException with retry logic when needed

Output format:
- Start with necessary imports
- One test class inheriting from unittest.TestCase or standalone pytest class
- No markdown fencing, no explanation
""",

    "selenium_java": """\
You are an expert Selenium Java test engineer.
Generate a complete, runnable TestNG test class.

Coding standards:
- Use WebDriverWait + ExpectedConditions for all waits (NO Thread.sleep)
- Use By.cssSelector() as default, By.xpath() only when necessary
- Add // Step N: comments before each step's code
- Use @Test annotation from TestNG
- Use Assert.assertEquals / Assert.assertTrue from TestNG
- Extend BaseTest class (assume it provides WebDriver driver field)

Output format:
- Start with necessary imports
- One complete TestNG test class
- No markdown fencing, no explanation
""",

    "cypress_js": """\
You are an expert Cypress JavaScript test engineer.
Generate a complete, runnable Cypress test spec.

Coding standards:
- Use cy.get(), cy.contains(), cy.findByRole() for element selection
- Use .should() for assertions (NOT expect() unless chained)
- Add // Step N: comments before each step's code
- Use cy.intercept() for API mocking when needed
- Use Cypress custom commands for repeated actions
- Chain commands where possible (cy.get().type().should())

Output format:
- No imports needed (Cypress auto-provides)
- One describe() block with one it() block
- No markdown fencing, no explanation
""",

    "rest_assured": """\
You are an expert REST Assured Java API test engineer.
Generate a complete, runnable JUnit/TestNG test class.

Coding standards:
- Use given().when().then() fluent API
- Use Hamcrest matchers for assertions (equalTo, containsString, hasSize)
- Add // Step N: comments before each step's code
- Use @BeforeClass to set up RestAssured.baseURI
- Extract response to Response object for multi-step assertions
- Use JsonPath for response body extraction

Output format:
- Start with necessary imports including static imports
- One complete test class
- No markdown fencing, no explanation
""",

    "robot_framework": """\
You are an expert Robot Framework test engineer.
Generate a complete, runnable Robot Framework test suite.

Coding standards:
- Use SeleniumLibrary keywords for browser automation
- Use RequestsLibrary keywords for API testing
- Add [Documentation] to each test case
- Use [Tags] for test categorisation
- Create reusable keywords in *** Keywords *** section
- Use ${VARIABLE} syntax for all dynamic values
- Indent with 4 spaces

Output format:
- *** Settings *** section with Library imports
- *** Variables *** section for test data
- *** Test Cases *** section
- *** Keywords *** section for reusable steps
- No markdown fencing, no explanation
""",
}

STEP_GENERATOR_USER_TEMPLATE = """\
Generate {framework} test code for this test case:

Title: {title}
ID: {test_id}
Category: {category}

Scenario analysis:
- Test type: {test_type}
- Auth required: {auth_required}
- Pages: {pages_visited}
- Complexity: {complexity}
{notes_section}

Steps:
{steps}

Resolved selectors:
{selectors}

{preconditions_section}
{expected_results_section}
"""


# ══════════════════════════════════════════════════════════════════════
# 4. VALIDATOR PROMPT
#    The agent reviews generated code and returns structured feedback.
#
#    HOW TO CUSTOMISE:
#    - Add project-specific rules (e.g. "never use XPath", "always use AAA")
#    - Add forbidden patterns for your codebase
#    - Add required patterns your team enforces
# ══════════════════════════════════════════════════════════════════════

VALIDATOR_SYSTEM = """\
You are a senior test automation code reviewer.
Review the generated test code and return structured feedback.

Check for:
1. Syntax correctness (no obvious syntax errors)
2. Framework compliance (correct API usage for the target framework)
3. Selector quality (no fragile positional or auto-generated selectors)
4. Wait strategy (no hardcoded sleeps, proper async/await usage)
5. Assertion completeness (test actually verifies something)
6. Import completeness (all used APIs are imported)

Return a JSON object:
{
  "is_valid": true/false,
  "issues": ["list of specific issues found"],
  "warnings": ["list of non-blocking suggestions"],
  "fixed_code": "corrected code if is_valid=false, else empty string"
}

Rules:
- If is_valid=false, you MUST provide fixed_code with the corrections applied
- fixed_code must be complete runnable code, not a diff
- Output ONLY valid JSON. No explanation outside the JSON.
"""

VALIDATOR_USER_TEMPLATE = """\
Framework: {framework}
Review this generated test code:

```
{code}
```

Original test case title: {title}
Expected behaviour: {expected_results}
"""


# ══════════════════════════════════════════════════════════════════════
# 5. ASSEMBLER PROMPT
#    Combines multiple test functions into a final project-ready file.
#    Also generates page objects and fixtures when needed.
#
#    HOW TO CUSTOMISE:
#    - Add your project's base class names
#    - Add standard imports your project always needs
#    - Add file header comments your team requires
# ══════════════════════════════════════════════════════════════════════

ASSEMBLER_SYSTEM = """\
You are a test automation architect. Assemble individual test functions
into a complete, production-ready test file.

Your responsibilities:
1. Deduplicate imports (one import block at the top)
2. Extract repeated setup into a shared fixture or beforeEach
3. Add a file-level docstring describing what the suite tests
4. Ensure consistent indentation and formatting
5. Add any missing framework boilerplate (describe blocks, class wrappers)

Rules:
- Output ONLY the final assembled code
- No markdown fencing, no explanation
- Preserve all test logic — do NOT remove or simplify test steps
- Keep all // Step N: or # Step N: comments
"""

ASSEMBLER_USER_TEMPLATE = """\
Framework: {framework}
Assemble these {count} test function(s) into one complete test file:

{test_functions}

Shared context:
- Page objects available: {page_objects}
- Fixtures available: {fixtures}
- Common imports needed: {imports}
"""


# ══════════════════════════════════════════════════════════════════════
# 6. FRAMEWORK CONTEXT
#    Short reference snippets injected into every generation prompt.
#    Helps the LLM stay on-framework without hallucinating APIs.
#
#    HOW TO CUSTOMISE:
#    - Add your project's custom commands / helpers
#    - Add base URLs, environment variables your tests use
#    - Add custom assertion helpers
# ══════════════════════════════════════════════════════════════════════

FRAMEWORK_CONTEXT: dict[str, str] = {
    "playwright_ts": (
        "Playwright TypeScript | @playwright/test | async/await\n"
        "Locators: page.getByRole() > page.getByTestId() > page.locator()\n"
        "Assertions: await expect(locator).toBeVisible() / toHaveText() / toHaveURL()\n"
        "Network: await page.route() / page.waitForResponse()\n"
        "Structure: test.describe('Suite', () => { test('case', async ({ page }) => { ... }) })"
    ),
    "playwright_py": (
        "Playwright Python | pytest-playwright | sync API\n"
        "Locators: page.get_by_role() > page.get_by_test_id() > page.locator()\n"
        "Assertions: expect(locator).to_be_visible() / to_have_text() / to_have_url()\n"
        "Structure: def test_name(page: Page): ..."
    ),
    "selenium_py": (
        "Selenium Python | pytest | WebDriver\n"
        "Locators: By.CSS_SELECTOR > By.ID > By.XPATH\n"
        "Waits: WebDriverWait(driver, 10).until(EC.visibility_of_element_located(...))\n"
        "Structure: class TestSuite: def test_name(self, driver): ..."
    ),
    "selenium_java": (
        "Selenium Java | TestNG | WebDriver\n"
        "Locators: By.cssSelector() > By.id() > By.xpath()\n"
        "Waits: new WebDriverWait(driver, Duration.ofSeconds(10)).until(ExpectedConditions...)\n"
        "Structure: public class TestName extends BaseTest { @Test public void testMethod() {} }"
    ),
    "cypress_js": (
        "Cypress JavaScript | no imports needed\n"
        "Commands: cy.get() / cy.contains() / cy.findByRole()\n"
        "Assertions: .should('be.visible') / .should('have.text', '...') / .should('eq', ...)\n"
        "Network: cy.intercept('POST', '/api/...', { fixture: 'data.json' })\n"
        "Structure: describe('Suite', () => { it('case', () => { ... }) })"
    ),
    "rest_assured": (
        "REST Assured Java | JUnit/TestNG | fluent API\n"
        "Pattern: given().header().body().when().post('/endpoint').then().statusCode(200).body('key', equalTo('val'))\n"
        "Imports: import static io.restassured.RestAssured.*; import static org.hamcrest.Matchers.*;\n"
        "Structure: public class ApiTest { @BeforeClass public static void setup() { baseURI = '...'; } @Test public void test() {} }"
    ),
    "robot_framework": (
        "Robot Framework | SeleniumLibrary / RequestsLibrary\n"
        "Browser: Open Browser / Click Element / Input Text / Element Should Be Visible\n"
        "API: Create Session / GET On Session / POST On Session / Status Should Be\n"
        "Variables: ${VAR_NAME}    value\n"
        "Structure: *** Settings *** / *** Variables *** / *** Test Cases *** / *** Keywords ***"
    ),
}


# ══════════════════════════════════════════════════════════════════════
# 7. ACTION PATTERNS
#    Maps natural language keywords to action types.
#    Used by the scenario planner to classify steps without LLM calls.
#
#    HOW TO CUSTOMISE:
#    - Add domain-specific verbs your test writers use
#    - Add synonyms for actions in your test language
#    - Add new action types for your application's specific interactions
# ══════════════════════════════════════════════════════════════════════

ACTION_KEYWORDS: dict[str, list[str]] = {
    "navigate":       ["navigate", "go to", "open", "visit", "browse", "launch", "load"],
    "click":          ["click", "tap", "press", "hit", "select", "choose", "toggle"],
    "fill":           ["enter", "type", "input", "fill", "write", "set", "put"],
    "select":         ["select from", "choose from", "pick from", "dropdown"],
    "assert_visible": ["verify visible", "check visible", "assert visible", "should be visible",
                       "is displayed", "appears", "shown"],
    "assert_text":    ["verify text", "check text", "assert text", "contains text",
                       "shows", "displays", "has text", "should contain"],
    "assert_url":     ["verify url", "check url", "assert url", "on page", "redirected to"],
    "assert_value":   ["verify value", "check value", "field contains", "input has"],
    "wait":           ["wait", "pause", "sleep", "wait for", "until"],
    "hover":          ["hover", "mouse over", "mouseover"],
    "scroll":         ["scroll", "scroll to", "scroll down", "scroll up"],
    "upload":         ["upload", "attach", "choose file", "select file"],
    "keyboard":       ["press enter", "press tab", "press escape", "press key", "keyboard"],
    "api_call":       ["send request", "call api", "post to", "get from", "api call",
                       "http request", "rest call"],
    "drag_drop":      ["drag", "drop", "drag and drop", "drag to"],
    "screenshot":     ["screenshot", "capture", "take screenshot"],
    "clear":          ["clear", "empty", "reset field", "delete text"],
}


# ══════════════════════════════════════════════════════════════════════
# 8. PIPELINE SETTINGS
#    Controls retry behaviour, confidence thresholds, and agent options.
#
#    HOW TO CUSTOMISE:
#    - Increase max_retries if you want more aggressive error correction
#    - Set skip_validation=True to speed up generation (less safe)
#    - Set scenario_analysis=False to skip the planning step (faster)
#    - Adjust llm_temperature for more/less creative code generation
# ══════════════════════════════════════════════════════════════════════

PIPELINE_SETTINGS: dict = {
    # Maximum LLM retries when validation fails
    "max_validation_retries": 2,

    # Run the scenario planner agent before code generation
    # Set False to skip planning and go straight to generation (faster)
    "scenario_analysis": True,

    # Run the selector resolver agent before code generation
    # Set False to use only user-provided selectors
    "selector_resolution": True,

    # Run the validator agent after code generation
    # Set False to skip validation (faster but less safe)
    "run_validation": True,

    # Run the assembler agent to combine multiple test functions
    # Set False to return raw per-test-case code
    "run_assembler": True,

    # LLM temperature for code generation (0.0 = deterministic, 1.0 = creative)
    # Lower = more consistent code, Higher = more varied approaches
    "llm_temperature": 0.1,

    # Maximum characters of test case context sent to LLM
    "max_context_chars": 8000,

    # Whether to include scenario analysis notes in generation prompt
    "include_scenario_notes": True,

    # Minimum confidence to accept template-generated code without LLM verification
    "template_confidence_threshold": 0.85,
}
