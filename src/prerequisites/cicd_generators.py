"""CI/CD pipeline and framework integration generators."""
from __future__ import annotations
from typing import Any


def generate_cicd_pipeline(plan: list[dict], fw: Any | None, fw_cicd: dict) -> str:
    """Generate CI/CD pipeline YAML with pre-requisite steps."""
    stages = {"setup": [], "pre-test": [], "test": [], "post-test": []}
    for item in plan:
        stages[item["stage"]].append(item)
    
    # Detect CI/CD platform
    platform = "github_actions"
    if fw_cicd:
        if fw_cicd.get("gitlab_ci"):
            platform = "gitlab"
        elif fw_cicd.get("azure_devops"):
            platform = "azure"
    
    if platform == "gitlab":
        return _gitlab_ci_yaml(stages, fw)
    return _github_actions_yaml(stages, fw)


def _github_actions_yaml(stages: dict, fw: Any | None) -> str:
    """Generate GitHub Actions workflow."""
    fw_name = fw.framework_name.lower().replace(" ", "-") if fw else "test"
    lines = [
        "```yaml",
        f"# .github/workflows/{fw_name}-tests.yml",
        "name: Automated Tests",
        "",
        "on: [push, pull_request]",
        "",
        "jobs:",
        "  test:",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "      - uses: actions/setup-python@v5",
        "        with:",
        "          python-version: '3.11'",
        ""
    ]
    
    if stages["setup"]:
        lines.append("      # === SETUP ===")
        for item in stages["setup"]:
            lines.append(f"      - name: {item['name']}")
            lines.append(f"        run: {item['command']}")
        lines.append("")
    
    if stages["pre-test"]:
        lines.append("      # === PRE-REQUISITES ===")
        for item in stages["pre-test"]:
            lines.append(f"      - name: {item['name']}")
            lines.append(f"        run: {item['command']}")
        lines.append("")
    
    lines.append("      # === RUN TESTS ===")
    lines.append("      - name: Run Tests")
    if fw:
        test_cmd = getattr(fw, "test_command", "") or (
            "pytest tests/ --html=report.html" if "Python" in fw.languages_supported else "npm test"
        )
        lines.append(f"        run: {test_cmd}")
    else:
        lines.append("        run: pytest tests/")
    lines.append("")
    
    if stages["post-test"]:
        lines.append("      # === CLEANUP ===")
        for item in stages["post-test"]:
            lines.append(f"      - name: {item['name']}")
            lines.append(f"        run: {item['command']}")
            lines.append("        if: always()")
    
    lines.append("```")
    return "\n".join(lines)


def _gitlab_ci_yaml(stages: dict, fw: Any | None) -> str:
    """Generate GitLab CI pipeline."""
    lines = [
        "```yaml",
        "# .gitlab-ci.yml",
        "stages:",
        "  - setup",
        "  - test",
        "  - cleanup",
        ""
    ]
    
    all_setup = stages["setup"] + stages["pre-test"]
    if all_setup:
        lines.append("setup:")
        lines.append("  stage: setup")
        lines.append("  script:")
        for item in all_setup:
            lines.append(f"    - {item['command']}")
        lines.append("")
    
    lines.append("test:")
    lines.append("  stage: test")
    lines.append("  script:")
    if fw:
        test_cmd = getattr(fw, "test_command", "") or "pytest tests/"
        lines.append(f"    - {test_cmd}")
    else:
        lines.append("    - pytest tests/")
    lines.append("")
    
    if stages["post-test"]:
        lines.append("cleanup:")
        lines.append("  stage: cleanup")
        lines.append("  when: always")
        lines.append("  script:")
        for item in stages["post-test"]:
            lines.append(f"    - {item['command']}")
    
    lines.append("```")
    return "\n".join(lines)


def generate_framework_hooks(plan: list[dict], fw: Any) -> str:
    """Generate framework-specific hooks to run prerequisites.

    Dispatch is driven by the framework's test_command YAML field so new
    frameworks are picked up automatically without code changes.
    """
    cmd = getattr(fw, "test_command", "").lower()
    fw_name = fw.framework_name.lower()

    if "playwright" in cmd or "playwright" in fw_name:
        return _playwright_hooks(plan)
    elif "cypress" in cmd or "cypress" in fw_name:
        return _cypress_hooks(plan)
    elif "robot" in cmd or "robot" in fw_name:
        return _robot_hooks(plan)
    elif "selenium" in fw_name:  # selenium uses pytest runner
        return _selenium_hooks(plan)
    elif "python" in str(fw.languages_supported).lower() or "pytest" in cmd:
        return _pytest_hooks(plan)
    return _generic_hooks(plan)


def _playwright_hooks(plan: list[dict]) -> str:
    """Generate Playwright globalSetup/globalTeardown with instructions."""
    setup_cmds = [item for item in plan if item["stage"] in ["setup", "pre-test"]]
    cleanup_cmds = [item for item in plan if item["stage"] == "post-test"]
    
    lines = [
        "## Playwright Framework Hooks\n",
        "**How to configure:**",
        "1. Create `global-setup.ts` in your project root",
        "2. Create `global-teardown.ts` in your project root", 
        "3. Update `playwright.config.ts` to reference these files\n",
        "---\n",
        "**Step 1: Create `global-setup.ts`**",
        "```typescript",
        "// global-setup.ts",
        "import { execSync } from 'child_process';",
        "",
        "async function globalSetup() {",
        "  console.log('[Setup] Running prerequisites...');"
    ]
    for item in setup_cmds:
        lines.append(f"  console.log('[Setup] {item['name']}...');")
        lines.append(f"  execSync('{item['command']}', {{ stdio: 'inherit' }});")
    lines.extend([
        "  console.log('[Setup] Complete!');",
        "}",
        "",
        "export default globalSetup;",
        "```\n",
        "---\n",
        "**Step 2: Create `global-teardown.ts`**",
        "```typescript",
        "// global-teardown.ts",
        "import { execSync } from 'child_process';",
        "",
        "async function globalTeardown() {",
        "  console.log('[Teardown] Cleaning up...');"
    ])
    for item in cleanup_cmds:
        lines.append(f"  console.log('[Teardown] {item['name']}...');")
        lines.append(f"  execSync('{item['command']}', {{ stdio: 'inherit' }});")
    lines.extend([
        "  console.log('[Teardown] Complete!');",
        "}",
        "",
        "export default globalTeardown;",
        "```\n",
        "---\n",
        "**Step 3: Update `playwright.config.ts`**",
        "```typescript",
        "// playwright.config.ts",
        "import { defineConfig } from '@playwright/test';",
        "",
        "export default defineConfig({",
        "  globalSetup: require.resolve('./global-setup'),",
        "  globalTeardown: require.resolve('./global-teardown'),",
        "  // ... rest of your config",
        "});",
        "```\n",
        "**Usage:** Run `npx playwright test` - prerequisites run automatically!"
    ])
    return "\n".join(lines)


def _pytest_hooks(plan: list[dict]) -> str:
    """Generate pytest conftest.py hooks with instructions."""
    setup_cmds = [item for item in plan if item["stage"] in ["setup", "pre-test"]]
    cleanup_cmds = [item for item in plan if item["stage"] == "post-test"]
    
    lines = [
        "## Pytest Framework Hooks\n",
        "**How to configure:**",
        "1. Create or update `conftest.py` in your `tests/` directory",
        "2. The hooks run automatically when you execute `pytest`\n",
        "**File location:** `tests/conftest.py` (or project root)\n",
        "---\n",
        "**conftest.py:**",
        "```python",
        "# tests/conftest.py",
        "import subprocess",
        "import pytest",
        "",
        "",
        "def pytest_configure(config):",
        "    '''Run prerequisites ONCE before entire test session'''",
        "    print('\\n[Setup] Running prerequisites...')"
    ]
    for item in setup_cmds:
        lines.append(f"    print('[Setup] {item['name']}...')")
        lines.append(f"    subprocess.run('{item['command']}', shell=True, check=True)")
    lines.extend([
        "    print('[Setup] Complete!')\n",
        "",
        "def pytest_unconfigure(config):",
        "    '''Cleanup ONCE after entire test session'''",
        "    print('\\n[Teardown] Cleaning up...')"
    ])
    for item in cleanup_cmds:
        lines.append(f"    print('[Teardown] {item['name']}...')")
        lines.append(f"    subprocess.run('{item['command']}', shell=True)")
    lines.extend([
        "    print('[Teardown] Complete!')",
        "",
        "",
        "# Optional: Fixture for per-test setup (if needed)",
        "@pytest.fixture(scope='session', autouse=True)",
        "def setup_environment():",
        "    '''Alternative: Use fixture for more control'''",
        "    # Setup runs here",
        "    yield",
        "    # Teardown runs here",
        "```\n",
        "---\n",
        "**Usage:** Run `pytest tests/` - prerequisites run automatically!",
        "",
        "**Tip:** Use `pytest -v` to see setup/teardown messages."
    ])
    return "\n".join(lines)


def _cypress_hooks(plan: list[dict]) -> str:
    """Generate Cypress setupNodeEvents hooks with instructions."""
    setup_cmds = [item for item in plan if item["stage"] in ["setup", "pre-test"]]
    cleanup_cmds = [item for item in plan if item["stage"] == "post-test"]
    
    lines = [
        "## Cypress Framework Hooks\n",
        "**How to configure:**",
        "1. Update `cypress.config.js` (or `cypress.config.ts`) in project root",
        "2. Hooks run when you execute `npx cypress run`\n",
        "**File location:** `cypress.config.js`\n",
        "---\n",
        "**cypress.config.js:**",
        "```javascript",
        "// cypress.config.js",
        "const { defineConfig } = require('cypress');",
        "const { execSync } = require('child_process');",
        "",
        "module.exports = defineConfig({",
        "  e2e: {",
        "    setupNodeEvents(on, config) {",
        "      // Run prerequisites BEFORE test run",
        "      on('before:run', (details) => {",
        "        console.log('[Setup] Running prerequisites...');"
    ]
    for item in setup_cmds:
        lines.append(f"        console.log('[Setup] {item['name']}...');")
        lines.append(f"        execSync('{item['command']}', {{ stdio: 'inherit' }});")
    lines.extend([
        "        console.log('[Setup] Complete!');",
        "      });\n",
        "      // Cleanup AFTER test run",
        "      on('after:run', (results) => {",
        "        console.log('[Teardown] Cleaning up...');"
    ])
    for item in cleanup_cmds:
        lines.append(f"        console.log('[Teardown] {item['name']}...');")
        lines.append(f"        execSync('{item['command']}', {{ stdio: 'inherit' }});")
    lines.extend([
        "        console.log('[Teardown] Complete!');",
        "      });",
        "",
        "      return config;",
        "    },",
        "    // ... rest of your e2e config",
        "  },",
        "});",
        "```\n",
        "---\n",
        "**Usage:** Run `npx cypress run` - prerequisites run automatically!",
        "",
        "**Note:** `before:run` only fires in `cypress run` mode, not `cypress open`.",
        "For interactive mode, use `cypress/support/e2e.js` with `before()` hook."
    ])
    return "\n".join(lines)


def _robot_hooks(plan: list[dict]) -> str:
    """Generate Robot Framework listener hooks with instructions."""
    setup_cmds = [item for item in plan if item["stage"] in ["setup", "pre-test"]]
    cleanup_cmds = [item for item in plan if item["stage"] == "post-test"]
    
    lines = [
        "## Robot Framework Hooks\n",
        "**How to configure:**",
        "1. Create `PrereqListener.py` in your project",
        "2. Run robot with `--listener PrereqListener.py`\n",
        "**File location:** `listeners/PrereqListener.py`\n",
        "---\n",
        "**PrereqListener.py:**",
        "```python",
        "# listeners/PrereqListener.py",
        "import subprocess",
        "",
        "class PrereqListener:",
        "    ROBOT_LISTENER_API_VERSION = 3",
        "",
        "    def start_suite(self, suite, result):",
        "        '''Run prerequisites before suite starts'''",
        "        if suite.parent is None:  # Only for root suite",
        "            print('[Setup] Running prerequisites...')"
    ]
    for item in setup_cmds:
        lines.append(f"            print('[Setup] {item['name']}...')")
        lines.append(f"            subprocess.run('{item['command']}', shell=True, check=True)")
    lines.extend([
        "            print('[Setup] Complete!')\n",
        "    def end_suite(self, suite, result):",
        "        '''Cleanup after suite ends'''",
        "        if suite.parent is None:  # Only for root suite",
        "            print('[Teardown] Cleaning up...')"
    ])
    for item in cleanup_cmds:
        lines.append(f"            print('[Teardown] {item['name']}...')")
        lines.append(f"            subprocess.run('{item['command']}', shell=True)")
    lines.extend([
        "            print('[Teardown] Complete!')",
        "```\n",
        "---\n",
        "**Usage:**",
        "```bash",
        "robot --listener listeners/PrereqListener.py tests/",
        "```\n",
        "**Alternative:** Use Suite Setup/Teardown in `.robot` file:",
        "```robot",
        "*** Settings ***",
        "Suite Setup       Run Prerequisites",
        "Suite Teardown    Run Cleanup",
        "",
        "*** Keywords ***",
        "Run Prerequisites"
    ])
    for item in setup_cmds:
        lines.append(f"    Run Process    {item['command']}    shell=True")
    lines.append("\nRun Cleanup")
    for item in cleanup_cmds:
        lines.append(f"    Run Process    {item['command']}    shell=True")
    lines.append("```")
    return "\n".join(lines)


def _selenium_hooks(plan: list[dict]) -> str:
    """Generate Selenium/TestNG hooks with instructions."""
    setup_cmds = [item for item in plan if item["stage"] in ["setup", "pre-test"]]
    cleanup_cmds = [item for item in plan if item["stage"] == "post-test"]
    
    lines = [
        "## Selenium Framework Hooks\n",
        "**For Python (pytest + selenium):** See Pytest hooks above\n",
        "**For Java (TestNG):**\n",
        "**How to configure:**",
        "1. Create `BaseTest.java` with @BeforeSuite/@AfterSuite",
        "2. Extend this class in your test classes\n",
        "**File location:** `src/test/java/base/BaseTest.java`\n",
        "---\n",
        "**BaseTest.java:**",
        "```java",
        "// src/test/java/base/BaseTest.java",
        "package base;",
        "",
        "import org.testng.annotations.BeforeSuite;",
        "import org.testng.annotations.AfterSuite;",
        "import java.io.IOException;",
        "",
        "public class BaseTest {",
        "",
        "    @BeforeSuite",
        "    public void globalSetup() throws IOException, InterruptedException {",
        '        System.out.println("[Setup] Running prerequisites...");'
    ]
    for item in setup_cmds:
        lines.append(f'        System.out.println("[Setup] {item["name"]}...");')
        lines.append(f'        Runtime.getRuntime().exec("{item["command"]}").waitFor();')
    lines.extend([
        '        System.out.println("[Setup] Complete!");',
        "    }\n",
        "    @AfterSuite",
        "    public void globalTeardown() throws IOException, InterruptedException {",
        '        System.out.println("[Teardown] Cleaning up...");'
    ])
    for item in cleanup_cmds:
        lines.append(f'        System.out.println("[Teardown] {item["name"]}...");')
        lines.append(f'        Runtime.getRuntime().exec("{item["command"]}").waitFor();')
    lines.extend([
        '        System.out.println("[Teardown] Complete!");',
        "    }",
        "}",
        "```\n",
        "---\n",
        "**Your test class:**",
        "```java",
        "// src/test/java/tests/LoginTest.java",
        "public class LoginTest extends BaseTest {",
        "    @Test",
        "    public void testLogin() {",
        "        // Your test code - prerequisites already ran!",
        "    }",
        "}",
        "```\n",
        "**Usage:** Run `mvn test` or via TestNG - prerequisites run automatically!"
    ])
    return "\n".join(lines)


def _generic_hooks(plan: list[dict]) -> str:
    """Generate generic package.json scripts with instructions."""
    setup_cmds = [item["command"] for item in plan if item["stage"] in ["setup", "pre-test"]]
    cleanup_cmds = [item["command"] for item in plan if item["stage"] == "post-test"]
    
    lines = [
        "## Generic Framework Hooks (npm scripts)\n",
        "**How to configure:**",
        "1. Update `package.json` in your project root",
        "2. npm automatically runs `pretest` before `test` and `posttest` after\n",
        "**File location:** `package.json`\n",
        "---\n",
        "**package.json:**",
        "```json",
        "{",
        '  "scripts": {',
        f'    "pretest": "{" && ".join(setup_cmds) if setup_cmds else "echo [Setup] Ready"}",',
        '    "test": "your-test-command-here",',
        f'    "posttest": "{" && ".join(cleanup_cmds) if cleanup_cmds else "echo [Teardown] Done"}"',
        "  }",
        "}",
        "```\n",
        "---\n",
        "**Usage:** Run `npm test` - pretest/posttest run automatically!",
        "",
        "**Tip:** For more control, create separate script files:",
        "```json",
        "{",
        '  "scripts": {',
        '    "pretest": "node scripts/setup.js",',
        '    "test": "jest",',
        '    "posttest": "node scripts/cleanup.js"',
        "  }",
        "}",
        "```"
    ]
    return "\n".join(lines)
