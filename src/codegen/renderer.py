"""Code Renderer — produces framework-specific output files.

Renders optimized test results into a complete project structure:
  - Test spec files
  - Page object files
  - Fixture files
  - Config files (playwright.config.ts, pytest.ini, etc.)
  - Package files (package.json, requirements.txt, etc.)
  - README with run instructions
"""
from __future__ import annotations

import logging
import re
from typing import Any

from src.codegen.models import (
    CodeGenOptions,
    FileType,
    FixtureSpec,
    GeneratedFile,
    GeneratedTestSuite,
    GenerationSource,
    HelperSpec,
    ManualTestCase,
    OptimizationResult,
    PageObjectSpec,
    ParameterizedGroup,
    StepGenerationResult,
    TargetFramework,
    TestCaseGenerationResult,
    ValidationResult,
)

logger = logging.getLogger(__name__)


# ── Framework Configs ─────────────────────────────────────────────────

_FRAMEWORK_CONFIG: dict[str, dict[str, Any]] = {
    "playwright_ts": {
        "language": "TypeScript",
        "extension": ".spec.ts",
        "page_ext": ".page.ts",
        "fixture_ext": ".fixture.ts",
        "config_file": "playwright.config.ts",
        "package_file": "package.json",
        "run_command": "npx playwright test",
        "install_command": "npm install && npx playwright install",
    },
    "playwright_py": {
        "language": "Python",
        "extension": "_test.py",
        "page_ext": "_page.py",
        "fixture_ext": "_fixture.py",
        "config_file": "pytest.ini",
        "package_file": "requirements.txt",
        "run_command": "pytest --headed",
        "install_command": "pip install -r requirements.txt && playwright install",
    },
    "selenium_py": {
        "language": "Python",
        "extension": "_test.py",
        "page_ext": "_page.py",
        "fixture_ext": "_fixture.py",
        "config_file": "pytest.ini",
        "package_file": "requirements.txt",
        "run_command": "pytest -v",
        "install_command": "pip install -r requirements.txt",
    },
    "selenium_java": {
        "language": "Java",
        "extension": "Test.java",
        "page_ext": "Page.java",
        "fixture_ext": "Base.java",
        "config_file": "pom.xml",
        "package_file": "pom.xml",
        "run_command": "mvn test",
        "install_command": "mvn install -DskipTests",
    },
    "cypress_js": {
        "language": "JavaScript",
        "extension": ".cy.js",
        "page_ext": ".page.js",
        "fixture_ext": ".fixture.js",
        "config_file": "cypress.config.js",
        "package_file": "package.json",
        "run_command": "npx cypress run",
        "install_command": "npm install",
    },
    "rest_assured": {
        "language": "Java",
        "extension": "Test.java",
        "page_ext": "Client.java",
        "fixture_ext": "Base.java",
        "config_file": "pom.xml",
        "package_file": "pom.xml",
        "run_command": "mvn test",
        "install_command": "mvn install -DskipTests",
    },
    "robot_framework": {
        "language": "Robot Framework",
        "extension": ".robot",
        "page_ext": ".resource",
        "fixture_ext": ".resource",
        "config_file": "robot.yaml",
        "package_file": "requirements.txt",
        "run_command": "robot tests/",
        "install_command": "pip install -r requirements.txt",
    },
}


# ── Code Renderer ─────────────────────────────────────────────────────


class CodeRenderer:
    """Renders generated test code into complete project files.

    Handles framework-specific formatting, structure, and project scaffolding.
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(
        self,
        test_results: list[TestCaseGenerationResult],
        optimization: OptimizationResult,
        framework: TargetFramework,
        options: CodeGenOptions,
        selector_map: dict[str, str],
    ) -> GeneratedTestSuite:
        """Render all generated code into a complete test suite.

        Returns a GeneratedTestSuite with all files, instructions, and metadata.
        """
        config = _FRAMEWORK_CONFIG.get(framework.value, _FRAMEWORK_CONFIG["playwright_ts"])
        files: list[GeneratedFile] = []
        all_selectors: dict[str, str] = dict(selector_map)

        # 1. Render page objects (if optimization found any)
        if options.generate_page_objects and optimization.page_objects:
            for po in optimization.page_objects:
                content = self._render_page_object(po, framework, config)
                files.append(GeneratedFile(
                    path=po.file_path,
                    content=content,
                    file_type=FileType.PAGE_OBJECT,
                ))
                all_selectors.update(po.selectors)

        # 2. Render fixtures
        if options.generate_fixtures and optimization.fixtures:
            for fixture in optimization.fixtures:
                content = self._render_fixture(fixture, framework, config)
                files.append(GeneratedFile(
                    path=fixture.file_path,
                    content=content,
                    file_type=FileType.FIXTURE,
                ))

        # 3. Render test spec files
        for tr in test_results:
            content = tr.assembled_code
            if not content:
                content = self._fallback_assemble(tr, framework, config)

            slug = self._slugify(tr.title or tr.test_case_id)
            file_path = f"tests/{slug}{config['extension']}"

            files.append(GeneratedFile(
                path=file_path,
                content=content,
                file_type=FileType.TEST,
                source=tr.source,
                confidence=self._avg_confidence(tr.step_results),
            ))

        # 4. Render parameterized test data files
        if options.generate_data_files and optimization.parameterized_groups:
            for i, group in enumerate(optimization.parameterized_groups):
                content = self._render_test_data(group, framework, config)
                files.append(GeneratedFile(
                    path=f"test-data/data_group_{i+1}{self._data_ext(framework)}",
                    content=content,
                    file_type=FileType.DATA,
                ))

        # 5. Render helpers/utilities
        if optimization.shared_helpers:
            content = self._render_helpers(optimization.shared_helpers, framework, config)
            files.append(GeneratedFile(
                path=f"utils/helpers{self._code_ext(framework)}",
                content=content,
                file_type=FileType.UTILITY,
            ))

        # 6. Render config file
        config_content = self._render_config(framework)
        if config_content:
            files.append(GeneratedFile(
                path=config["config_file"],
                content=config_content,
                file_type=FileType.CONFIG,
            ))

        # 7. Render package/dependency file
        pkg_content = self._render_package_file(framework)
        if pkg_content:
            files.append(GeneratedFile(
                path=config["package_file"],
                content=pkg_content,
                file_type=FileType.PACKAGE,
            ))

        # Calculate overall confidence
        all_confidences = []
        for tr in test_results:
            all_confidences.extend(sr.confidence for sr in tr.step_results)
        overall_confidence = sum(all_confidences) / max(len(all_confidences), 1)

        return GeneratedTestSuite(
            framework=framework.value,
            language=config["language"],
            files=files,
            install_instructions=config["install_command"],
            run_command=config["run_command"],
            selector_map=all_selectors,
            confidence_score=round(overall_confidence, 3),
            validation=ValidationResult(is_valid=True),  # Already validated per-test
        )

    def assemble_test_function(
        self,
        test_case: ManualTestCase,
        step_results: list[StepGenerationResult],
        framework: TargetFramework,
    ) -> str:
        """Assemble step codes into a complete test function/method."""
        config = _FRAMEWORK_CONFIG.get(framework.value, _FRAMEWORK_CONFIG["playwright_ts"])
        fw = framework.value

        # Collect step code lines
        step_lines = []
        for sr in step_results:
            if sr.generated_code.strip():
                # Add comment with original action
                comment = self._make_comment(sr.original_action, fw)
                step_lines.append(comment)
                step_lines.append(sr.generated_code)
                step_lines.append("")  # blank line between steps

        body = "\n".join(step_lines).rstrip()

        # Wrap in framework-specific structure
        if fw == "playwright_ts":
            return self._wrap_playwright_ts(test_case, body)
        elif fw == "playwright_py":
            return self._wrap_playwright_py(test_case, body)
        elif fw == "selenium_py":
            return self._wrap_selenium_py(test_case, body)
        elif fw == "selenium_java":
            return self._wrap_selenium_java(test_case, body)
        elif fw == "cypress_js":
            return self._wrap_cypress_js(test_case, body)
        elif fw == "robot_framework":
            return self._wrap_robot(test_case, body)
        else:
            return body

    # ------------------------------------------------------------------
    # Framework-Specific Test Wrappers
    # ------------------------------------------------------------------

    def _wrap_playwright_ts(self, tc: ManualTestCase, body: str) -> str:
        indented_body = self._indent(body, 4)
        title = self._escape_quotes(tc.title)
        category = tc.category or "tests"
        return (
            f"import {{ test, expect }} from '@playwright/test';\n\n"
            f"test.describe('{category}', () => {{\n"
            f"  test('{title}', async ({{ page }}) => {{\n"
            f"{indented_body}\n"
            f"  }});\n"
            f"}});\n"
        )

    def _wrap_playwright_py(self, tc: ManualTestCase, body: str) -> str:
        indented_body = self._indent(body, 4)
        func_name = self._to_func_name(tc.title)
        return (
            f"from playwright.sync_api import Page, expect\n\n\n"
            f"def {func_name}(page: Page) -> None:\n"
            f'    """Test: {tc.title}"""\n'
            f"{indented_body}\n"
        )

    def _wrap_selenium_py(self, tc: ManualTestCase, body: str) -> str:
        indented_body = self._indent(body, 8)
        func_name = self._to_func_name(tc.title)
        class_name = self._to_class_name(tc.category or tc.title)
        return (
            f"import pytest\n"
            f"from selenium.webdriver.common.by import By\n"
            f"from selenium.webdriver.support.ui import WebDriverWait\n"
            f"from selenium.webdriver.support import expected_conditions as EC\n\n\n"
            f"class {class_name}:\n"
            f'    """Tests for: {tc.category or tc.title}"""\n\n'
            f"    def {func_name}(self, driver):\n"
            f'        """Test: {tc.title}"""\n'
            f"{indented_body}\n"
        )

    def _wrap_selenium_java(self, tc: ManualTestCase, body: str) -> str:
        indented_body = self._indent(body, 8)
        method_name = self._to_method_name(tc.title)
        class_name = self._to_class_name(tc.title) + "Test"
        return (
            f"import org.openqa.selenium.*;\n"
            f"import org.openqa.selenium.support.ui.WebDriverWait;\n"
            f"import org.openqa.selenium.support.ui.ExpectedConditions;\n"
            f"import org.testng.annotations.Test;\n"
            f"import static org.testng.Assert.*;\n"
            f"import java.time.Duration;\n\n"
            f"public class {class_name} extends BaseTest {{\n\n"
            f"    @Test\n"
            f"    public void {method_name}() {{\n"
            f"{indented_body}\n"
            f"    }}\n"
            f"}}\n"
        )

    def _wrap_cypress_js(self, tc: ManualTestCase, body: str) -> str:
        indented_body = self._indent(body, 4)
        title = self._escape_quotes(tc.title)
        category = tc.category or "tests"
        return (
            f"describe('{category}', () => {{\n"
            f"  it('{title}', () => {{\n"
            f"{indented_body}\n"
            f"  }});\n"
            f"}});\n"
        )

    def _wrap_robot(self, tc: ManualTestCase, body: str) -> str:
        indented_body = self._indent(body, 4)
        return (
            f"*** Settings ***\n"
            f"Library    SeleniumLibrary\n\n"
            f"*** Test Cases ***\n"
            f"{tc.title}\n"
            f"    [Documentation]    {tc.description or tc.title}\n"
            f"    [Tags]    {' '.join(tc.tags) if tc.tags else tc.category or 'regression'}\n"
            f"{indented_body}\n"
        )

    # ------------------------------------------------------------------
    # Component Renderers
    # ------------------------------------------------------------------

    def _render_page_object(
        self, po: PageObjectSpec, framework: TargetFramework, config: dict,
    ) -> str:
        """Render a page object class."""
        fw = framework.value

        if fw in ("playwright_ts",):
            return self._render_po_playwright_ts(po)
        elif fw in ("playwright_py", "selenium_py"):
            return self._render_po_python(po, framework)
        elif fw in ("cypress_js",):
            return self._render_po_cypress(po)
        elif fw in ("selenium_java", "rest_assured"):
            return self._render_po_java(po)
        else:
            return self._render_po_generic(po)

    def _render_po_playwright_ts(self, po: PageObjectSpec) -> str:
        """Render Playwright TypeScript page object."""
        lines = [
            "import { Page, Locator } from '@playwright/test';",
            "",
            f"export class {po.name} {{",
            "  readonly page: Page;",
            "",
        ]
        # Selector properties
        for name, selector in po.selectors.items():
            lines.append(f"  readonly {name}: Locator;")
        lines.append("")

        # Constructor
        lines.append("  constructor(page: Page) {")
        lines.append("    this.page = page;")
        for name, selector in po.selectors.items():
            lines.append(f"    this.{name} = page.locator('{selector}');")
        lines.append("  }")
        lines.append("")

        # Methods
        for method in po.methods:
            params_str = ", ".join(f"{p}: string" for p in method.params)
            lines.append(f"  async {method.name}({params_str}) {{")
            if method.doc:
                lines.append(f"    // {method.doc}")
            # Indent method body
            for body_line in method.body.split("\n"):
                lines.append(f"    {body_line}")
            lines.append("  }")
            lines.append("")

        lines.append("}")
        return "\n".join(lines)

    def _render_po_python(self, po: PageObjectSpec, framework: TargetFramework) -> str:
        """Render Python page object (Playwright or Selenium)."""
        lines = []
        if framework == TargetFramework.PLAYWRIGHT_PY:
            lines.append("from playwright.sync_api import Page, Locator")
        else:
            lines.append("from selenium.webdriver.common.by import By")
            lines.append("from selenium.webdriver.remote.webdriver import WebDriver")
        lines.extend(["", "", f"class {po.name}:", f'    """Page object for {po.name}."""', ""])

        # Selectors as class variables
        for name, selector in po.selectors.items():
            lines.append(f"    {name.upper()}_SELECTOR = '{selector}'")
        lines.append("")

        # Constructor
        if framework == TargetFramework.PLAYWRIGHT_PY:
            lines.append("    def __init__(self, page: Page) -> None:")
            lines.append("        self.page = page")
        else:
            lines.append("    def __init__(self, driver: WebDriver) -> None:")
            lines.append("        self.driver = driver")
        lines.append("")

        # Methods
        for method in po.methods:
            params_str = ", ".join(method.params)
            if params_str:
                params_str = ", " + params_str
            lines.append(f"    def {method.name}(self{params_str}):")
            if method.doc:
                lines.append(f'        """{method.doc}"""')
            for body_line in method.body.split("\n"):
                lines.append(f"        {body_line}")
            lines.append("")

        return "\n".join(lines)

    def _render_po_cypress(self, po: PageObjectSpec) -> str:
        """Render Cypress page object."""
        lines = [f"class {po.name} {{", ""]

        # Selectors
        lines.append("  elements = {")
        for name, selector in po.selectors.items():
            lines.append(f"    {name}: () => cy.get('{selector}'),")
        lines.append("  };")
        lines.append("")

        # Methods
        for method in po.methods:
            params_str = ", ".join(method.params)
            lines.append(f"  {method.name}({params_str}) {{")
            for body_line in method.body.split("\n"):
                lines.append(f"    {body_line}")
            lines.append("  }")
            lines.append("")

        lines.append("}")
        lines.append("")
        lines.append(f"export default new {po.name}();")
        return "\n".join(lines)

    def _render_po_java(self, po: PageObjectSpec) -> str:
        """Render Java page object."""
        lines = [
            "import org.openqa.selenium.*;",
            "import org.openqa.selenium.support.FindBy;",
            "import org.openqa.selenium.support.PageFactory;",
            "",
            f"public class {po.name} {{",
            "    private WebDriver driver;",
            "",
        ]
        # Selectors as @FindBy annotations
        for name, selector in po.selectors.items():
            lines.append(f'    @FindBy(css = "{selector}")')
            lines.append(f"    private WebElement {name};")
            lines.append("")

        # Constructor
        lines.append(f"    public {po.name}(WebDriver driver) {{")
        lines.append("        this.driver = driver;")
        lines.append("        PageFactory.initElements(driver, this);")
        lines.append("    }")
        lines.append("")

        # Methods
        for method in po.methods:
            params_str = ", ".join(f"String {p}" for p in method.params)
            lines.append(f"    public void {method.name}({params_str}) {{")
            for body_line in method.body.split("\n"):
                lines.append(f"        {body_line}")
            lines.append("    }")
            lines.append("")

        lines.append("}")
        return "\n".join(lines)

    def _render_po_generic(self, po: PageObjectSpec) -> str:
        """Generic page object rendering."""
        lines = [f"# Page Object: {po.name}", ""]
        lines.append("# Selectors:")
        for name, selector in po.selectors.items():
            lines.append(f"#   {name} = {selector}")
        lines.append("")
        lines.append("# Methods:")
        for method in po.methods:
            lines.append(f"#   {method.name}({', '.join(method.params)})")
        return "\n".join(lines)

    def _render_fixture(
        self, fixture: FixtureSpec, framework: TargetFramework, config: dict,
    ) -> str:
        """Render a fixture/setup file."""
        fw = framework.value

        if fw == "playwright_ts":
            return (
                f"import {{ test as base }} from '@playwright/test';\n\n"
                f"export const test = base.extend({{\n"
                f"  {fixture.name}: async ({{ page }}, use) => {{\n"
                f"    // Setup\n"
                f"    {self._indent(fixture.setup_code, 4)}\n"
                f"    await use(page);\n"
                f"    // Teardown\n"
                f"    {self._indent(fixture.teardown_code, 4) if fixture.teardown_code else '// No teardown needed'}\n"
                f"  }},\n"
                f"}});\n"
            )
        elif fw in ("playwright_py", "selenium_py"):
            return (
                f"import pytest\n\n\n"
                f"@pytest.fixture(scope='{fixture.scope}')\n"
                f"def {fixture.name}(page):\n"
                f'    """Fixture: {fixture.name}"""\n'
                f"    # Setup\n"
                f"    {self._indent(fixture.setup_code, 4)}\n"
                f"    yield page\n"
                f"    # Teardown\n"
                f"    {self._indent(fixture.teardown_code, 4) if fixture.teardown_code else 'pass'}\n"
            )
        elif fw == "cypress_js":
            return (
                f"// Fixture: {fixture.name}\n"
                f"Cypress.Commands.add('{fixture.name}', () => {{\n"
                f"  {self._indent(fixture.setup_code, 2)}\n"
                f"}});\n"
            )
        else:
            return f"// Fixture: {fixture.name}\n{fixture.setup_code}\n"

    def _render_test_data(
        self, group: ParameterizedGroup, framework: TargetFramework, config: dict,
    ) -> str:
        """Render parameterized test data."""
        fw = framework.value

        if fw in ("playwright_ts", "cypress_js"):
            lines = ["export const testData = ["]
            for params in group.parameters:
                entries = ", ".join(f"{k}: '{v}'" for k, v in params.items())
                lines.append(f"  {{ {entries} }},")
            lines.append("];")
            return "\n".join(lines)
        elif fw in ("playwright_py", "selenium_py"):
            lines = ["TEST_DATA = ["]
            for params in group.parameters:
                entries = ", ".join(f'"{k}": "{v}"' for k, v in params.items())
                lines.append(f"    {{{entries}}},")
            lines.append("]")
            return "\n".join(lines)
        else:
            # JSON format as fallback
            import json
            return json.dumps(group.parameters, indent=2)

    def _render_helpers(
        self, helpers: list[HelperSpec], framework: TargetFramework, config: dict,
    ) -> str:
        """Render shared helper functions."""
        fw = framework.value
        lines: list[str] = []

        if fw in ("playwright_ts",):
            lines.append("import { Page } from '@playwright/test';")
            lines.append("")
            for helper in helpers:
                params = ", ".join(f"{p}: string" for p in helper.params)
                lines.append(f"export async function {helper.name}(page: Page, {params}) {{")
                for body_line in helper.body.split("\n"):
                    lines.append(f"  {body_line}")
                lines.append("}")
                lines.append("")
        elif fw in ("playwright_py", "selenium_py"):
            for helper in helpers:
                params = ", ".join(helper.params)
                lines.append(f"def {helper.name}({params}):")
                lines.append(f'    """Helper used by: {", ".join(helper.used_by)}"""')
                for body_line in helper.body.split("\n"):
                    lines.append(f"    {body_line}")
                lines.append("")
                lines.append("")
        elif fw in ("cypress_js",):
            for helper in helpers:
                params = ", ".join(helper.params)
                lines.append(f"function {helper.name}({params}) {{")
                for body_line in helper.body.split("\n"):
                    lines.append(f"  {body_line}")
                lines.append("}")
                lines.append("")
        else:
            for helper in helpers:
                lines.append(f"// Helper: {helper.name}")
                lines.append(helper.body)
                lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Config & Package Renderers
    # ------------------------------------------------------------------

    def _render_config(self, framework: TargetFramework) -> str:
        """Render framework configuration file."""
        fw = framework.value

        if fw == "playwright_ts":
            return (
                "import { defineConfig } from '@playwright/test';\n\n"
                "export default defineConfig({\n"
                "  testDir: './tests',\n"
                "  timeout: 30000,\n"
                "  retries: 1,\n"
                "  use: {\n"
                "    headless: true,\n"
                "    screenshot: 'only-on-failure',\n"
                "    trace: 'retain-on-failure',\n"
                "  },\n"
                "  reporter: [['html', { open: 'never' }]],\n"
                "});\n"
            )
        elif fw in ("playwright_py", "selenium_py"):
            return (
                "[pytest]\n"
                "testpaths = tests\n"
                "addopts = -v --tb=short\n"
            )
        elif fw == "cypress_js":
            return (
                "const { defineConfig } = require('cypress');\n\n"
                "module.exports = defineConfig({\n"
                "  e2e: {\n"
                "    baseUrl: 'http://localhost:3000',\n"
                "    specPattern: 'tests/**/*.cy.js',\n"
                "    supportFile: false,\n"
                "  },\n"
                "});\n"
            )
        return ""

    def _render_package_file(self, framework: TargetFramework) -> str:
        """Render package/dependency file."""
        fw = framework.value

        if fw == "playwright_ts":
            return (
                '{\n'
                '  "name": "generated-tests",\n'
                '  "version": "1.0.0",\n'
                '  "scripts": {\n'
                '    "test": "playwright test",\n'
                '    "test:headed": "playwright test --headed",\n'
                '    "test:report": "playwright show-report"\n'
                '  },\n'
                '  "devDependencies": {\n'
                '    "@playwright/test": "^1.40.0",\n'
                '    "typescript": "^5.3.0"\n'
                '  }\n'
                '}\n'
            )
        elif fw == "playwright_py":
            return (
                "pytest>=7.0\n"
                "pytest-playwright>=0.4.0\n"
                "playwright>=1.40.0\n"
            )
        elif fw == "selenium_py":
            return (
                "pytest>=7.0\n"
                "selenium>=4.15.0\n"
                "webdriver-manager>=4.0.0\n"
            )
        elif fw == "cypress_js":
            return (
                '{\n'
                '  "name": "generated-tests",\n'
                '  "version": "1.0.0",\n'
                '  "scripts": {\n'
                '    "test": "cypress run",\n'
                '    "test:open": "cypress open"\n'
                '  },\n'
                '  "devDependencies": {\n'
                '    "cypress": "^13.0.0"\n'
                '  }\n'
                '}\n'
            )
        return ""

    # ------------------------------------------------------------------
    # Utility Methods
    # ------------------------------------------------------------------

    def _fallback_assemble(
        self, tr: TestCaseGenerationResult, framework: TargetFramework, config: dict,
    ) -> str:
        """Fallback: assemble from step results if assembled_code is empty."""
        lines = []
        for sr in tr.step_results:
            if sr.generated_code.strip():
                lines.append(sr.generated_code)
        return "\n".join(lines)

    @staticmethod
    def _make_comment(text: str, framework: str) -> str:
        """Create a code comment in the appropriate style."""
        if framework in ("robot_framework",):
            return f"    # {text}"
        elif framework in ("playwright_py", "selenium_py"):
            return f"    # {text}"
        else:
            return f"    // {text}"

    @staticmethod
    def _indent(text: str, spaces: int) -> str:
        """Indent a block of text."""
        prefix = " " * spaces
        lines = text.split("\n")
        return "\n".join(f"{prefix}{line}" if line.strip() else line for line in lines)

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text to a file-name-safe slug."""
        slug = text.strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "_", slug)
        slug = slug.strip("_")
        return slug[:50] or "test"

    @staticmethod
    def _to_func_name(title: str) -> str:
        """Convert title to a Python function name."""
        name = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
        return f"test_{name[:50]}"

    @staticmethod
    def _to_class_name(title: str) -> str:
        """Convert title to a PascalCase class name."""
        words = re.sub(r"[^a-z0-9]+", " ", title.lower()).split()
        return "Test" + "".join(w.capitalize() for w in words[:5])

    @staticmethod
    def _to_method_name(title: str) -> str:
        """Convert title to a Java method name."""
        words = re.sub(r"[^a-z0-9]+", " ", title.lower()).split()
        if not words:
            return "testGenerated"
        return "test" + "".join(w.capitalize() for w in words[:5])

    @staticmethod
    def _escape_quotes(text: str) -> str:
        """Escape single quotes for use in string literals."""
        return text.replace("'", "\\'")

    @staticmethod
    def _avg_confidence(step_results: list[StepGenerationResult]) -> float:
        """Calculate average confidence across step results."""
        if not step_results:
            return 0.0
        return sum(sr.confidence for sr in step_results) / len(step_results)

    @staticmethod
    def _code_ext(framework: TargetFramework) -> str:
        """Get code file extension for framework."""
        ext_map = {
            TargetFramework.PLAYWRIGHT_TS: ".ts",
            TargetFramework.PLAYWRIGHT_PY: ".py",
            TargetFramework.SELENIUM_PY: ".py",
            TargetFramework.SELENIUM_JAVA: ".java",
            TargetFramework.CYPRESS_JS: ".js",
            TargetFramework.REST_ASSURED: ".java",
            TargetFramework.ROBOT_FRAMEWORK: ".py",
        }
        return ext_map.get(framework, ".ts")

    @staticmethod
    def _data_ext(framework: TargetFramework) -> str:
        """Get data file extension for framework."""
        ext_map = {
            TargetFramework.PLAYWRIGHT_TS: ".ts",
            TargetFramework.PLAYWRIGHT_PY: ".py",
            TargetFramework.SELENIUM_PY: ".py",
            TargetFramework.SELENIUM_JAVA: ".java",
            TargetFramework.CYPRESS_JS: ".js",
            TargetFramework.REST_ASSURED: ".java",
            TargetFramework.ROBOT_FRAMEWORK: ".py",
        }
        return ext_map.get(framework, ".json")
