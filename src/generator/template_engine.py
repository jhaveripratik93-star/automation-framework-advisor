"""Boilerplate project generator using Jinja2 templates."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.models import UserProfile

logger = logging.getLogger(__name__)


@dataclass
class GeneratedFile:
    """A single generated file in the boilerplate project."""

    path: str
    content: str


@dataclass
class ProjectTemplate:
    """Complete generated project template."""

    framework: str
    project_name: str
    files: list[GeneratedFile] = field(default_factory=list)

    def file_tree(self) -> list[str]:
        """Return list of all file paths."""
        return [f.path for f in self.files]


class BoilerplateGenerator:
    """Generates project boilerplate based on framework selection."""

    # Template structures per framework
    FRAMEWORK_TEMPLATES = {
        "playwright": {
            "language": "python",
            "files": [
                "pytest.ini",
                "conftest.py",
                "requirements.txt",
                "Dockerfile",
                ".github/workflows/tests.yml",
                "pages/__init__.py",
                "pages/base_page.py",
                "pages/login_page.py",
                "tests/__init__.py",
                "tests/test_login.py",
                "tests/test_api.py",
                "utils/__init__.py",
                "utils/config.py",
                "utils/helpers.py",
                ".env.example",
                "README.md",
            ],
        },
        "cypress": {
            "language": "typescript",
            "files": [
                "package.json",
                "cypress.config.ts",
                "tsconfig.json",
                "Dockerfile",
                ".github/workflows/tests.yml",
                "cypress/e2e/login.cy.ts",
                "cypress/e2e/api.cy.ts",
                "cypress/support/commands.ts",
                "cypress/support/e2e.ts",
                "cypress/fixtures/users.json",
                "README.md",
            ],
        },
        "selenium": {
            "language": "python",
            "files": [
                "pytest.ini",
                "conftest.py",
                "requirements.txt",
                "Dockerfile",
                "Jenkinsfile",
                "pages/__init__.py",
                "pages/base_page.py",
                "pages/login_page.py",
                "tests/__init__.py",
                "tests/test_login.py",
                "utils/__init__.py",
                "utils/driver_factory.py",
                "utils/config.py",
                "README.md",
            ],
        },
    }

    def generate(
        self, framework: str, profile: UserProfile
    ) -> ProjectTemplate:
        """Generate a project template for the selected framework."""
        fw_key = framework.lower().replace(" ", "")
        template_config = self.FRAMEWORK_TEMPLATES.get(fw_key)

        if not template_config:
            logger.warning(f"No template for {framework}, using Playwright default")
            template_config = self.FRAMEWORK_TEMPLATES["playwright"]

        files = []
        for file_path in template_config["files"]:
            content = self._render_file(
                fw_key, file_path, profile, template_config["language"]
            )
            files.append(GeneratedFile(path=file_path, content=content))

        return ProjectTemplate(
            framework=framework,
            project_name=profile.project_name,
            files=files,
        )

    def _render_file(
        self,
        framework: str,
        file_path: str,
        profile: UserProfile,
        language: str,
    ) -> str:
        """Render a single template file (simplified for POC)."""
        # In production, this would use Jinja2 templates from data/templates/
        # For POC, generate common file contents inline

        if file_path == "requirements.txt" and language == "python":
            return self._gen_python_requirements(framework)
        elif file_path == "pytest.ini":
            return self._gen_pytest_ini(framework)
        elif file_path == "conftest.py":
            return self._gen_conftest(framework, profile)
        elif file_path == "Dockerfile":
            return self._gen_dockerfile(framework, language)
        elif file_path.endswith(".yml") and "github" in file_path:
            return self._gen_github_actions(framework)
        elif file_path == "Jenkinsfile":
            return self._gen_jenkinsfile(framework)
        elif file_path == "README.md":
            return self._gen_readme(framework, profile)
        else:
            return f"# {file_path}\n# TODO: Implement {file_path}\n"

    def _gen_python_requirements(self, framework: str) -> str:
        base = "pytest==8.2.2\npytest-html==4.1.1\npython-dotenv==1.0.1\n"
        if framework == "playwright":
            base += "playwright==1.45.0\npytest-playwright==0.5.0\n"
        elif framework == "selenium":
            base += "selenium==4.21.0\nwebdriver-manager==4.0.1\n"
        return base

    def _gen_pytest_ini(self, framework: str) -> str:
        return (
            "[pytest]\n"
            "testpaths = tests\n"
            "addopts = --html=reports/report.html --self-contained-html\n"
            f"markers =\n"
            f"    smoke: Smoke test suite\n"
            f"    regression: Full regression suite\n"
            f"    api: API tests\n"
        )

    def _gen_conftest(self, framework: str, profile: UserProfile) -> str:
        if framework == "playwright":
            return (
                'import pytest\n'
                'from playwright.sync_api import Page\n\n\n'
                '@pytest.fixture(scope="session")\n'
                'def browser_context_args(browser_context_args):\n'
                '    return {\n'
                '        **browser_context_args,\n'
                '        "viewport": {"width": 1920, "height": 1080},\n'
                '    }\n'
            )
        return "import pytest\n\n# Add fixtures here\n"

    def _gen_dockerfile(self, framework: str, language: str) -> str:
        if framework == "playwright":
            return (
                "FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy\n\n"
                "WORKDIR /app\n"
                "COPY requirements.txt .\n"
                "RUN pip install -r requirements.txt\n"
                "COPY . .\n\n"
                'CMD ["pytest", "--headed=false"]\n'
            )
        return (
            f"FROM python:3.11-slim\n\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install -r requirements.txt\n"
            "COPY . .\n\n"
            'CMD ["pytest"]\n'
        )

    def _gen_github_actions(self, framework: str) -> str:
        return (
            "name: Automation Tests\n\n"
            "on:\n  push:\n    branches: [main]\n"
            "  pull_request:\n    branches: [main]\n\n"
            "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - uses: actions/setup-python@v5\n"
            "        with:\n          python-version: '3.11'\n"
            "      - run: pip install -r requirements.txt\n"
            "      - run: pytest --junitxml=results.xml\n"
        )

    def _gen_jenkinsfile(self, framework: str) -> str:
        return (
            "pipeline {\n"
            "    agent { docker { image 'python:3.11' } }\n"
            "    stages {\n"
            "        stage('Install') {\n"
            "            steps { sh 'pip install -r requirements.txt' }\n"
            "        }\n"
            "        stage('Test') {\n"
            "            steps { sh 'pytest --junitxml=results.xml' }\n"
            "        }\n"
            "    }\n"
            "    post {\n"
            "        always { junit 'results.xml' }\n"
            "    }\n"
            "}\n"
        )

    def _gen_readme(self, framework: str, profile: UserProfile) -> str:
        return (
            f"# {profile.project_name} - Automation Tests\n\n"
            f"**Framework:** {framework.title()}\n"
            f"**Language:** Python\n"
            f"**CI/CD:** {profile.ci_cd_tool}\n\n"
            "## Quick Start\n\n"
            "```bash\n"
            "pip install -r requirements.txt\n"
            "pytest\n"
            "```\n\n"
            "## Project Structure\n\n"
            "```\n"
            "├── pages/          # Page Object Models\n"
            "├── tests/          # Test cases\n"
            "├── utils/          # Shared utilities\n"
            "├── conftest.py     # Pytest fixtures\n"
            "└── pytest.ini      # Pytest configuration\n"
            "```\n"
        )
