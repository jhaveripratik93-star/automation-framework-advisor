"""Framework Scanner — discovers new automation frameworks from the internet.

Searches public sources (GitHub trending, PyPI, npm, web search) for emerging
test automation frameworks and generates YAML profiles compatible with the
knowledge base schema.

Usage:
    scanner = FrameworkScanner(kb=knowledge_base, llm_client=groq_client)

    # On-demand scan
    new_frameworks = scanner.scan()

    # Check specific framework
    profile = scanner.research_framework("Detox")
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)

# ── Sources to scan for new frameworks ────────────────────────────────

_GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
_PYPI_SEARCH_URL = "https://pypi.org/pypi/{package}/json"
_NPM_SEARCH_URL = "https://registry.npmjs.org/-/v1/search"

# Keywords used to discover automation frameworks on GitHub
_SEARCH_QUERIES = [
    "test automation framework",
    "e2e testing framework",
    "browser automation",
    "UI testing framework",
    "API testing framework",
    "mobile testing framework",
    "performance testing framework",
    "load testing tool",
    "infrastructure testing",
    "cloud testing framework",
    "unit testing framework",
    "integration testing framework",
    "contract testing",
    "BDD testing framework",
    "test runner framework",
    "security testing framework",
    "chaos engineering testing",
    "visual testing framework",
    "accessibility testing tool",
]

# Minimum GitHub stars to consider a framework relevant
_MIN_STARS = 500

# How often to auto-scan (seconds) — default 24 hours
_SCAN_INTERVAL_SECONDS = 86400

_HTTP_TIMEOUT = 30.0


@dataclass
class DiscoveredFramework:
    """Represents a newly discovered framework before full profiling."""

    name: str
    repo_url: str
    description: str
    stars: int = 0
    language: str = ""
    license: str = ""
    topics: list[str] = field(default_factory=list)
    last_updated: str = ""
    homepage: str = ""
    categories: list[str] = field(default_factory=list)  # e.g. ["web_ui_testing", "api_testing"]


class FrameworkScanner:
    """Discovers new automation frameworks and adds them to the knowledge base."""

    def __init__(
        self,
        kb=None,
        llm_client=None,
        data_dir: str = "data/frameworks",
        github_token: str | None = None,
        min_stars: int = _MIN_STARS,
        scan_interval: int = _SCAN_INTERVAL_SECONDS,
    ):
        self._kb = kb
        self._llm = llm_client
        self._data_dir = Path(data_dir)
        self._github_token = github_token
        self._min_stars = min_stars
        self._scan_interval = scan_interval
        self._last_scan_time: float = 0.0
        self._scan_results: list[DiscoveredFramework] = []
        self._http = httpx.Client(
            timeout=_HTTP_TIMEOUT,
            headers={"User-Agent": "AutomationFrameworkAdvisor/1.0"},
            follow_redirects=True,
        )

    # ── Public API ────────────────────────────────────────────────────

    def scan(self, force: bool = False) -> list[DiscoveredFramework]:
        """Scan the internet for new automation frameworks.

        Args:
            force: If True, ignore scan interval and scan immediately.

        Returns:
            List of newly discovered frameworks not in the knowledge base.
        """
        if not force and not self._should_scan():
            logger.info("Skipping scan — last scan within interval (%ds)", self._scan_interval)
            return self._scan_results

        logger.info("Starting framework discovery scan...")
        discovered: list[DiscoveredFramework] = []

        # Source 1: GitHub search
        github_results = self._search_github()
        discovered.extend(github_results)

        # Source 2: Curated list of known emerging frameworks (fallback)
        curated = self._check_curated_list()
        discovered.extend(curated)

        # Deduplicate
        seen = set()
        unique: list[DiscoveredFramework] = []
        for fw in discovered:
            key = fw.name.lower().replace(" ", "").replace("-", "")
            if key not in seen:
                seen.add(key)
                unique.append(fw)

        # Filter out frameworks already in our KB
        new_frameworks = self._filter_existing(unique)

        self._scan_results = new_frameworks
        self._last_scan_time = time.time()
        logger.info(
            "Discovery scan complete: %d found, %d new (not in KB)",
            len(unique),
            len(new_frameworks),
        )
        return new_frameworks

    def get_categorized_results(
        self, frameworks: list[DiscoveredFramework] | None = None
    ) -> dict[str, list[DiscoveredFramework]]:
        """Group discovered frameworks by category.

        Args:
            frameworks: List of discovered frameworks. If None, uses last scan results.

        Returns:
            Dict mapping category_id -> list of DiscoveredFramework in that category.
            A framework may appear in multiple categories.
        """
        from src.knowledge_base.schema import FRAMEWORK_CATEGORIES

        fws = frameworks if frameworks is not None else self._scan_results
        categorized: dict[str, list[DiscoveredFramework]] = {
            cat_id: [] for cat_id in FRAMEWORK_CATEGORIES
        }

        for fw in fws:
            cats = fw.categories or self._classify_discovered(fw)
            for cat in cats:
                if cat in categorized:
                    categorized[cat].append(fw)

        # Remove empty categories
        return {k: v for k, v in categorized.items() if v}

    def research_framework(self, name: str) -> dict[str, Any] | None:
        """Research a specific framework and generate a full YAML profile.

        Uses LLM to fill in the knowledge base schema based on web search results.

        Args:
            name: Framework name to research.

        Returns:
            Dict matching FrameworkData schema, or None if research fails.
        """
        logger.info("Researching framework: %s", name)

        # Gather raw information
        info = self._gather_framework_info(name)
        if not info:
            logger.warning("Could not gather information for: %s", name)
            return None

        # Use LLM to structure the data into our schema
        profile = self._generate_profile_with_llm(name, info)
        return profile

    def add_framework(self, profile: dict[str, Any]) -> Path | None:
        """Write a framework profile to YAML and reload the knowledge base.

        Validates that the profile has all critical fields populated before
        writing. Incomplete profiles will be logged with warnings but still
        saved (to avoid data loss), with a marker in limitations.

        Args:
            profile: Dict matching FrameworkData schema.

        Returns:
            Path to the created YAML file, or None on failure.
        """
        if not profile or "framework_name" not in profile:
            logger.error("Invalid profile: missing framework_name")
            return None

        # ── Completeness validation before writing ────────────────────
        from src.knowledge_base.schema import FrameworkData

        is_valid, issues = FrameworkData.validate_for_addition(profile)
        if not is_valid:
            logger.warning(
                "Framework '%s' has incomplete data (%d issues): %s",
                profile["framework_name"],
                len(issues),
                "; ".join(issues),
            )
            # Add a marker so reviewers know this needs attention
            if isinstance(profile.get("limitations"), list):
                if "INCOMPLETE PROFILE — manual review needed" not in profile["limitations"]:
                    profile["limitations"].insert(
                        0, "INCOMPLETE PROFILE — manual review needed"
                    )
            else:
                profile["limitations"] = ["INCOMPLETE PROFILE — manual review needed"]

        # Create safe filename
        filename = self._safe_filename(profile["framework_name"])
        filepath = self._data_dir / f"{filename}.yaml"

        if filepath.exists():
            logger.warning("YAML already exists: %s — skipping", filepath)
            return filepath

        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(
                    profile,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                    width=120,
                )
            logger.info("Created framework profile: %s", filepath)

            # Reload knowledge base if available
            if self._kb:
                self._kb.load()
                logger.info("Knowledge base reloaded — now has %d frameworks", len(self._kb.list_all()))

                # Update knowledge graph with the new framework
                try:
                    from src.knowledge_base.schema import FrameworkData as FD
                    from src.graph import update_graph_with_framework

                    fw_data = FD(**profile)
                    update_graph_with_framework(fw_data, self._kb)
                    logger.info("Knowledge graph updated with '%s'", profile["framework_name"])
                except Exception as graph_exc:
                    logger.warning(
                        "Failed to update knowledge graph for '%s': %s",
                        profile["framework_name"], graph_exc,
                    )

            return filepath
        except Exception as exc:
            logger.error("Failed to write %s: %s", filepath, exc)
            return None

    def scan_and_add_all(self, force: bool = False) -> list[str]:
        """Full pipeline: scan → research → add new frameworks.

        Returns:
            List of framework names that were added.
        """
        new_frameworks = self.scan(force=force)
        added: list[str] = []

        for fw in new_frameworks:
            try:
                profile = self.research_framework(fw.name)
                if profile:
                    path = self.add_framework(profile)
                    if path:
                        added.append(fw.name)
                        logger.info("✓ Added: %s → %s", fw.name, path)
            except Exception as exc:
                logger.warning("Failed to add %s: %s", fw.name, exc)

        logger.info("Scan-and-add complete: %d new frameworks added", len(added))
        return added

    def should_auto_scan(self) -> bool:
        """Check if it's time for an automatic scan."""
        return self._should_scan()

    # ── GitHub search ─────────────────────────────────────────────────

    def _search_github(self) -> list[DiscoveredFramework]:
        """Search GitHub for automation framework repositories."""
        results: list[DiscoveredFramework] = []
        headers = {"Accept": "application/vnd.github+json"}
        if self._github_token:
            headers["Authorization"] = f"Bearer {self._github_token}"

        for query in _SEARCH_QUERIES:
            try:
                resp = self._http.get(
                    _GITHUB_SEARCH_URL,
                    params={
                        "q": f"{query} stars:>{self._min_stars}",
                        "sort": "stars",
                        "order": "desc",
                        "per_page": 10,
                    },
                    headers=headers,
                )
                if resp.status_code == 403:
                    logger.warning("GitHub rate limit hit — stopping search")
                    break
                if resp.status_code != 200:
                    logger.debug("GitHub search returned %d for: %s", resp.status_code, query)
                    continue

                data = resp.json()
                for item in data.get("items", []):
                    fw = DiscoveredFramework(
                        name=self._clean_name(item.get("name", "")),
                        repo_url=item.get("html_url", ""),
                        description=item.get("description", "") or "",
                        stars=item.get("stargazers_count", 0),
                        language=item.get("language", "") or "",
                        license=(item.get("license") or {}).get("spdx_id", ""),
                        topics=item.get("topics", []),
                        last_updated=item.get("pushed_at", ""),
                        homepage=item.get("homepage", "") or "",
                    )
                    fw.categories = self._classify_discovered(fw)
                    results.append(fw)

                # Be nice to GitHub API
                time.sleep(1.0)

            except httpx.HTTPError as exc:
                logger.debug("GitHub search error for '%s': %s", query, exc)
            except Exception as exc:
                logger.debug("Unexpected error in GitHub search: %s", exc)

        return results

    # ── Curated emerging frameworks list ──────────────────────────────

    def _check_curated_list(self) -> list[DiscoveredFramework]:
        """Return a curated list of known frameworks to check.

        This serves as a fallback when GitHub search is rate-limited and ensures
        well-known frameworks are always discoverable. Covers: test runners,
        unit testing, BDD, API testing, mobile, performance, security, visual,
        contract testing, and infrastructure.
        """
        curated = [
            # ── Test Runners & Unit Testing Frameworks ────────────────
            DiscoveredFramework(
                name="Pytest",
                repo_url="https://github.com/pytest-dev/pytest",
                description="The pytest framework makes it easy to write small tests, yet scales to support complex functional testing",
                stars=12000,
                language="Python",
                license="MIT",
                topics=["testing", "python", "unit-testing", "test-framework"],
                homepage="https://pytest.org/",
            ),
            DiscoveredFramework(
                name="JUnit 5",
                repo_url="https://github.com/junit-team/junit5",
                description="The 5th major version of the programmer-friendly testing framework for Java",
                stars=6300,
                language="Java",
                license="EPL-2.0",
                topics=["testing", "java", "unit-testing", "test-framework"],
                homepage="https://junit.org/junit5/",
            ),
            DiscoveredFramework(
                name="TestNG",
                repo_url="https://github.com/testng-team/testng",
                description="Testing framework for Java inspired by JUnit and NUnit",
                stars=1900,
                language="Java",
                license="Apache-2.0",
                topics=["testing", "java", "unit-testing", "integration-testing"],
                homepage="https://testng.org/",
            ),
            DiscoveredFramework(
                name="Jest",
                repo_url="https://github.com/jestjs/jest",
                description="Delightful JavaScript Testing Framework with a focus on simplicity",
                stars=44000,
                language="TypeScript",
                license="MIT",
                topics=["testing", "javascript", "unit-testing", "snapshot-testing"],
                homepage="https://jestjs.io/",
            ),
            DiscoveredFramework(
                name="Mocha",
                repo_url="https://github.com/mochajs/mocha",
                description="Simple, flexible, fun JavaScript test framework for Node.js and the browser",
                stars=22700,
                language="JavaScript",
                license="MIT",
                topics=["testing", "javascript", "unit-testing", "node"],
                homepage="https://mochajs.org/",
            ),
            DiscoveredFramework(
                name="Vitest",
                repo_url="https://github.com/vitest-dev/vitest",
                description="Blazing fast Vite-native unit test framework",
                stars=12000,
                language="TypeScript",
                license="MIT",
                topics=["testing", "vite", "unit-testing", "typescript"],
                homepage="https://vitest.dev/",
            ),
            DiscoveredFramework(
                name="NUnit",
                repo_url="https://github.com/nunit/nunit",
                description="NUnit Framework for .NET testing",
                stars=2500,
                language="C#",
                license="MIT",
                topics=["testing", "dotnet", "unit-testing", "csharp"],
                homepage="https://nunit.org/",
            ),
            DiscoveredFramework(
                name="xUnit.net",
                repo_url="https://github.com/xunit/xunit",
                description="Free, open source, community-focused unit testing tool for the .NET Framework",
                stars=4000,
                language="C#",
                license="Apache-2.0",
                topics=["testing", "dotnet", "unit-testing", "csharp"],
                homepage="https://xunit.net/",
            ),
            DiscoveredFramework(
                name="Go Test",
                repo_url="https://github.com/golang/go",
                description="Built-in testing package for Go with benchmarking and fuzzing",
                stars=123000,
                language="Go",
                license="BSD-3-Clause",
                topics=["testing", "golang", "unit-testing", "benchmark"],
                homepage="https://pkg.go.dev/testing",
            ),
            DiscoveredFramework(
                name="RSpec",
                repo_url="https://github.com/rspec/rspec",
                description="BDD for Ruby - Behaviour Driven Development testing framework",
                stars=3000,
                language="Ruby",
                license="MIT",
                topics=["testing", "ruby", "bdd", "unit-testing"],
                homepage="https://rspec.info/",
            ),
            # ── BDD Frameworks ────────────────────────────────────────
            DiscoveredFramework(
                name="Cucumber",
                repo_url="https://github.com/cucumber/cucumber-js",
                description="BDD testing framework - write tests in plain language (Gherkin)",
                stars=5000,
                language="JavaScript",
                license="MIT",
                topics=["bdd", "testing", "gherkin", "acceptance-testing"],
                homepage="https://cucumber.io/",
            ),
            DiscoveredFramework(
                name="Behave",
                repo_url="https://github.com/behave/behave",
                description="BDD testing framework for Python using Gherkin syntax",
                stars=3100,
                language="Python",
                license="BSD-2-Clause",
                topics=["bdd", "testing", "python", "gherkin"],
                homepage="https://behave.readthedocs.io/",
            ),
            DiscoveredFramework(
                name="SpecFlow",
                repo_url="https://github.com/SpecFlowOSS/SpecFlow",
                description="BDD testing framework for .NET using Gherkin specifications",
                stars=2200,
                language="C#",
                license="BSD-3-Clause",
                topics=["bdd", "testing", "dotnet", "gherkin"],
                homepage="https://specflow.org/",
            ),
            # ── Web UI / E2E Testing ──────────────────────────────────
            DiscoveredFramework(
                name="Nightwatch.js",
                repo_url="https://github.com/nightwatchjs/nightwatch",
                description="Integrated testing framework powered by W3C WebDriver",
                stars=11700,
                language="JavaScript",
                license="MIT",
                topics=["selenium", "webdriver", "e2e-testing", "browser-testing"],
                homepage="https://nightwatchjs.org/",
            ),
            DiscoveredFramework(
                name="Taiko",
                repo_url="https://github.com/getgauge/taiko",
                description="A Node.js library for testing modern web applications",
                stars=3500,
                language="JavaScript",
                license="MIT",
                topics=["browser-automation", "testing", "chromium"],
                homepage="https://taiko.dev/",
            ),
            DiscoveredFramework(
                name="Codecept.js",
                repo_url="https://github.com/codeceptjs/CodeceptJS",
                description="Supercharged End 2 End Testing Framework",
                stars=4000,
                language="JavaScript",
                license="MIT",
                topics=["e2e-testing", "acceptance-testing", "webdriver"],
                homepage="https://codecept.io/",
            ),
            DiscoveredFramework(
                name="Gauge",
                repo_url="https://github.com/getgauge/gauge",
                description="Light weight cross-platform test automation",
                stars=3200,
                language="Go",
                license="Apache-2.0",
                topics=["testing", "bdd", "test-automation"],
                homepage="https://gauge.org/",
            ),
            DiscoveredFramework(
                name="Katalon Studio",
                repo_url="https://github.com/nickthecoder/katalon",
                description="All-in-one test automation solution for web, API, mobile, desktop",
                stars=600,
                language="Java",
                license="Commercial",
                topics=["test-automation", "web-testing", "api-testing", "mobile-testing"],
                homepage="https://katalon.com/",
            ),
            # ── Mobile Testing ────────────────────────────────────────
            DiscoveredFramework(
                name="Detox",
                repo_url="https://github.com/wix/Detox",
                description="Gray box end-to-end testing for mobile apps",
                stars=11000,
                language="TypeScript",
                license="MIT",
                topics=["react-native", "mobile-testing", "e2e"],
                homepage="https://wix.github.io/Detox/",
            ),
            DiscoveredFramework(
                name="Maestro",
                repo_url="https://github.com/mobile-dev-inc/maestro",
                description="Painless Mobile UI Automation",
                stars=5500,
                language="Kotlin",
                license="Apache-2.0",
                topics=["mobile-testing", "ui-testing", "ios", "android"],
                homepage="https://maestro.mobile.dev/",
            ),
            DiscoveredFramework(
                name="Espresso",
                repo_url="https://github.com/nickthecoder/nickthecoder",
                description="Android UI testing framework by Google for reliable UI interaction testing",
                stars=1000,
                language="Java",
                license="Apache-2.0",
                topics=["android", "mobile-testing", "ui-testing", "google"],
                homepage="https://developer.android.com/training/testing/espresso",
            ),
            DiscoveredFramework(
                name="XCUITest",
                repo_url="https://github.com/nickthecoder/nickthecoder",
                description="Apple's native iOS/macOS UI testing framework built into Xcode",
                stars=500,
                language="Swift",
                license="Proprietary",
                topics=["ios", "mobile-testing", "ui-testing", "apple", "xcode"],
                homepage="https://developer.apple.com/documentation/xctest",
            ),
            # ── API & Contract Testing ────────────────────────────────
            DiscoveredFramework(
                name="Pactum",
                repo_url="https://github.com/pactumjs/pactum",
                description="REST API Testing Tool for all levels in a Test Pyramid",
                stars=1500,
                language="JavaScript",
                license="MIT",
                topics=["api-testing", "contract-testing", "e2e-testing"],
                homepage="https://pactumjs.github.io/",
            ),
            DiscoveredFramework(
                name="Dredd",
                repo_url="https://github.com/apiaryio/dredd",
                description="Language-agnostic HTTP API Testing Tool",
                stars=4100,
                language="JavaScript",
                license="MIT",
                topics=["api-testing", "openapi", "api-blueprint"],
                homepage="https://dredd.org/",
            ),
            DiscoveredFramework(
                name="Pact",
                repo_url="https://github.com/pact-foundation/pact-js",
                description="Consumer-driven contract testing framework for HTTP APIs and messages",
                stars=2800,
                language="TypeScript",
                license="MIT",
                topics=["contract-testing", "api-testing", "microservices", "consumer-driven"],
                homepage="https://pact.io/",
            ),
            DiscoveredFramework(
                name="Postman/Newman",
                repo_url="https://github.com/postmanlabs/newman",
                description="CLI collection runner for Postman API tests",
                stars=6800,
                language="JavaScript",
                license="Apache-2.0",
                topics=["api-testing", "postman", "rest", "cli"],
                homepage="https://www.postman.com/",
            ),
            DiscoveredFramework(
                name="Hurl",
                repo_url="https://github.com/Orange-OpenSource/hurl",
                description="Run and test HTTP requests with plain text files",
                stars=12500,
                language="Rust",
                license="Apache-2.0",
                topics=["http", "api-testing", "rest", "cli"],
                homepage="https://hurl.dev/",
            ),
            DiscoveredFramework(
                name="Httpx (pytest-httpx)",
                repo_url="https://github.com/encode/httpx",
                description="A fully featured HTTP client for Python with async support, used for API testing",
                stars=12800,
                language="Python",
                license="BSD-3-Clause",
                topics=["http", "api-testing", "python", "async"],
                homepage="https://www.python-httpx.org/",
            ),
            # ── Performance & Load Testing ────────────────────────────
            DiscoveredFramework(
                name="Gatling",
                repo_url="https://github.com/gatling/gatling",
                description="Modern load testing tool for web applications using Scala DSL",
                stars=6400,
                language="Scala",
                license="Apache-2.0",
                topics=["load-testing", "performance", "scala", "http"],
                homepage="https://gatling.io/",
            ),
            DiscoveredFramework(
                name="JMeter",
                repo_url="https://github.com/apache/jmeter",
                description="Apache JMeter - load testing tool for analyzing performance of web services",
                stars=8200,
                language="Java",
                license="Apache-2.0",
                topics=["load-testing", "performance", "java", "http"],
                homepage="https://jmeter.apache.org/",
            ),
            DiscoveredFramework(
                name="Artillery",
                repo_url="https://github.com/artilleryio/artillery",
                description="Cloud-scale load testing framework for modern applications",
                stars=7800,
                language="JavaScript",
                license="MPL-2.0",
                topics=["load-testing", "performance", "http", "websocket"],
                homepage="https://www.artillery.io/",
            ),
            DiscoveredFramework(
                name="Vegeta",
                repo_url="https://github.com/tsenart/vegeta",
                description="HTTP load testing tool and library written in Go",
                stars=23300,
                language="Go",
                license="MIT",
                topics=["load-testing", "performance", "http", "benchmark"],
                homepage="https://github.com/tsenart/vegeta",
            ),
            # ── Security Testing ──────────────────────────────────────
            DiscoveredFramework(
                name="OWASP ZAP",
                repo_url="https://github.com/zaproxy/zaproxy",
                description="The world's most widely used web app security scanner",
                stars=12500,
                language="Java",
                license="Apache-2.0",
                topics=["security", "web-security", "penetration-testing", "dast"],
                homepage="https://www.zaproxy.org/",
            ),
            DiscoveredFramework(
                name="Nuclei",
                repo_url="https://github.com/projectdiscovery/nuclei",
                description="Fast and customizable vulnerability scanner based on YAML templates",
                stars=19000,
                language="Go",
                license="MIT",
                topics=["security", "vulnerability-scanner", "penetration-testing"],
                homepage="https://nuclei.projectdiscovery.io/",
            ),
            # ── Visual & Accessibility Testing ────────────────────────
            DiscoveredFramework(
                name="Storybook",
                repo_url="https://github.com/storybookjs/storybook",
                description="UI component workshop for building, documenting, and testing components in isolation",
                stars=84000,
                language="TypeScript",
                license="MIT",
                topics=["ui", "component-testing", "visual-testing", "react"],
                homepage="https://storybook.js.org/",
            ),
            DiscoveredFramework(
                name="Chromatic",
                repo_url="https://github.com/chromaui/chromatic-cli",
                description="Visual regression testing and review tool for UI components",
                stars=700,
                language="TypeScript",
                license="MIT",
                topics=["visual-testing", "ui", "regression", "storybook"],
                homepage="https://www.chromatic.com/",
            ),
            DiscoveredFramework(
                name="Pa11y",
                repo_url="https://github.com/pa11y/pa11y",
                description="Automated accessibility testing tool for web pages",
                stars=4600,
                language="JavaScript",
                license="LGPL-3.0",
                topics=["accessibility", "a11y", "testing", "wcag"],
                homepage="https://pa11y.org/",
            ),
            # ── Chaos Engineering ─────────────────────────────────────
            DiscoveredFramework(
                name="Chaos Monkey",
                repo_url="https://github.com/Netflix/chaosmonkey",
                description="Netflix's resilience testing tool that randomly terminates instances",
                stars=14500,
                language="Go",
                license="Apache-2.0",
                topics=["chaos-engineering", "resilience", "netflix", "cloud"],
                homepage="https://netflix.github.io/chaosmonkey/",
            ),
            DiscoveredFramework(
                name="Litmus",
                repo_url="https://github.com/litmuschaos/litmus",
                description="Cloud-native chaos engineering framework for Kubernetes",
                stars=4300,
                language="Go",
                license="Apache-2.0",
                topics=["chaos-engineering", "kubernetes", "resilience", "cloud-native"],
                homepage="https://litmuschaos.io/",
            ),
        ]
        # Classify each curated framework
        for fw in curated:
            fw.categories = self._classify_discovered(fw)
        return curated

    # ── Info gathering ────────────────────────────────────────────────

    def _gather_framework_info(self, name: str) -> dict[str, Any]:
        """Gather information about a framework from multiple sources."""
        info: dict[str, Any] = {"name": name, "sources": []}

        # Try GitHub
        github_info = self._fetch_github_readme(name)
        if github_info:
            info["github"] = github_info
            info["sources"].append("github")

        # Try PyPI
        pypi_info = self._fetch_pypi_info(name)
        if pypi_info:
            info["pypi"] = pypi_info
            info["sources"].append("pypi")

        # Try npm
        npm_info = self._fetch_npm_info(name)
        if npm_info:
            info["npm"] = npm_info
            info["sources"].append("npm")

        # Check if we found from the scan results
        for fw in self._scan_results:
            if fw.name.lower() == name.lower():
                info["discovered"] = {
                    "description": fw.description,
                    "stars": fw.stars,
                    "language": fw.language,
                    "license": fw.license,
                    "topics": fw.topics,
                    "homepage": fw.homepage,
                    "repo_url": fw.repo_url,
                }
                info["sources"].append("scan_cache")
                break

        return info if info["sources"] else {}

    def _fetch_github_readme(self, name: str) -> dict[str, Any] | None:
        """Fetch README and repo metadata from GitHub."""
        try:
            headers = {"Accept": "application/vnd.github+json"}
            if self._github_token:
                headers["Authorization"] = f"Bearer {self._github_token}"

            # Search for the repo
            resp = self._http.get(
                _GITHUB_SEARCH_URL,
                params={"q": f"{name} in:name", "sort": "stars", "per_page": 1},
                headers=headers,
            )
            if resp.status_code != 200:
                return None

            items = resp.json().get("items", [])
            if not items:
                return None

            repo = items[0]
            full_name = repo.get("full_name", "")

            # Fetch README
            readme_resp = self._http.get(
                f"https://api.github.com/repos/{full_name}/readme",
                headers={**headers, "Accept": "application/vnd.github.raw+json"},
            )
            readme = readme_resp.text[:5000] if readme_resp.status_code == 200 else ""

            return {
                "full_name": full_name,
                "description": repo.get("description", ""),
                "stars": repo.get("stargazers_count", 0),
                "language": repo.get("language", ""),
                "license": (repo.get("license") or {}).get("spdx_id", ""),
                "topics": repo.get("topics", []),
                "homepage": repo.get("homepage", ""),
                "readme_excerpt": readme,
            }
        except Exception as exc:
            logger.debug("GitHub fetch error for %s: %s", name, exc)
            return None

    def _fetch_pypi_info(self, name: str) -> dict[str, Any] | None:
        """Fetch package info from PyPI."""
        try:
            # Try common name variations
            for pkg_name in [name.lower(), name.lower().replace(" ", "-"), name.lower().replace(" ", "_")]:
                resp = self._http.get(_PYPI_SEARCH_URL.format(package=pkg_name))
                if resp.status_code == 200:
                    data = resp.json()
                    info_data = data.get("info", {})
                    return {
                        "name": info_data.get("name", ""),
                        "version": info_data.get("version", ""),
                        "summary": info_data.get("summary", ""),
                        "license": info_data.get("license", ""),
                        "home_page": info_data.get("home_page", ""),
                        "requires_python": info_data.get("requires_python", ""),
                        "classifiers": info_data.get("classifiers", [])[:10],
                    }
        except Exception as exc:
            logger.debug("PyPI fetch error for %s: %s", name, exc)
        return None

    def _fetch_npm_info(self, name: str) -> dict[str, Any] | None:
        """Fetch package info from npm registry."""
        try:
            resp = self._http.get(
                _NPM_SEARCH_URL,
                params={"text": name, "size": 1},
            )
            if resp.status_code != 200:
                return None

            objects = resp.json().get("objects", [])
            if not objects:
                return None

            pkg = objects[0].get("package", {})
            return {
                "name": pkg.get("name", ""),
                "version": pkg.get("version", ""),
                "description": pkg.get("description", ""),
                "keywords": pkg.get("keywords", []),
                "links": pkg.get("links", {}),
            }
        except Exception as exc:
            logger.debug("npm fetch error for %s: %s", name, exc)
        return None

    # ── LLM-based profile generation ─────────────────────────────────

    def _generate_profile_with_llm(self, name: str, info: dict[str, Any]) -> dict[str, Any] | None:
        """Use LLM to generate a structured YAML profile from gathered info."""
        if not self._llm or not self._llm.is_available:
            logger.warning("LLM not available — using template-based profile generation")
            return self._generate_profile_template(name, info)

        prompt = self._build_profile_prompt(name, info)

        try:
            system = (
                "You are a test automation expert. Generate a YAML-compatible framework profile. "
                "Respond ONLY with valid YAML content, no markdown fences, no explanation."
            )
            response = self._llm.generate(prompt=prompt, system=system)
            # Parse the YAML response
            profile = yaml.safe_load(response)
            if isinstance(profile, dict) and "framework_name" in profile:
                # Validate required fields are present
                profile = self._validate_and_fill_profile(profile, name, info)
                return profile
            else:
                logger.warning("LLM returned invalid profile for %s — falling back to template", name)
                return self._generate_profile_template(name, info)
        except Exception as exc:
            logger.warning("LLM profile generation failed for %s: %s — using template", name, exc)
            return self._generate_profile_template(name, info)

    def _build_profile_prompt(self, name: str, info: dict[str, Any]) -> str:
        """Build the prompt for LLM profile generation."""
        context_parts = [f"Generate a complete knowledge base YAML profile for the automation framework: {name}\n"]

        if "github" in info:
            gh = info["github"]
            context_parts.append(f"GitHub: {gh.get('full_name', '')} ({gh.get('stars', 0)} stars)")
            context_parts.append(f"Description: {gh.get('description', '')}")
            context_parts.append(f"Language: {gh.get('language', '')}")
            context_parts.append(f"License: {gh.get('license', '')}")
            context_parts.append(f"Topics: {', '.join(gh.get('topics', []))}")
            if gh.get("readme_excerpt"):
                context_parts.append(f"\nREADME excerpt:\n{gh['readme_excerpt'][:2000]}")

        if "pypi" in info:
            pp = info["pypi"]
            context_parts.append(f"\nPyPI: {pp.get('name', '')} v{pp.get('version', '')}")
            context_parts.append(f"Summary: {pp.get('summary', '')}")

        if "npm" in info:
            npm = info["npm"]
            context_parts.append(f"\nnpm: {npm.get('name', '')} v{npm.get('version', '')}")
            context_parts.append(f"Description: {npm.get('description', '')}")

        if "discovered" in info:
            d = info["discovered"]
            context_parts.append(f"\nAdditional: {d.get('description', '')}")
            context_parts.append(f"Homepage: {d.get('homepage', '')}")

        context_parts.append(f"""
\nThe YAML profile MUST include exactly these top-level keys with REAL, SPECIFIC data for {name}.
Do NOT use generic placeholder values. Every field must reflect the actual characteristics of {name}.

REQUIRED KEYS:
- framework_name: string
- vendor: string (the company or organization behind it)
- license: string (SPDX identifier)
- website: string (official URL)
- first_release: string (year of first public release)
- latest_version: string (current stable version number)
- category: "test_automation" or "cloud_infrastructure"
- languages_supported: list of strings (all languages the framework supports)
- architecture_fit: dict with keys: web_spa, web_mpa, native_mobile, hybrid_mobile, desktop, api_testing, microservices (boolean or "limited"/"partial" values)
- capabilities: dict with keys: shadow_dom, iframe_cross_origin, multi_tab, multi_domain, file_upload_download, network_interception, parallel_execution, auto_wait, test_recorder, code_generation (boolean or string like "limited", "plugin (name)")
- cicd_integration: dict with keys: docker_support, github_actions, jenkins, gitlab_ci, azure_devops, pre_built_docker_images (boolean or string values)

CRITICAL FIELDS — MUST BE POPULATED WITH SPECIFIC DATA (NOT empty or generic):

- cloud_grids: dict listing cloud/grid providers that support this framework.
  Include browserstack, sauce_labs, lambda_test, and any framework-specific cloud services (e.g. cypress_cloud for Cypress).
  Use true/false/"limited" for each. If the framework is not applicable for cloud grids (e.g., it's an IaC tool), use: not_applicable: true

- performance: dict with REAL performance characteristics:
  - avg_test_execution_speed: "fast" / "moderate" / "slow" (based on actual framework speed)
  - resource_footprint: "low" / "medium" / "high" (memory/CPU usage)
  - startup_time_ms: integer (realistic cold-start time in milliseconds)
  - parallel_overhead: "low" / "medium" / "high" (cost of parallelization)

- maintainability: dict with REAL maintainability features:
  - page_object_support: true/false/"custom pattern" (does it support POM natively?)
  - fixture_support: true/false/string describing how fixtures work
  - reusable_components: true/false/string
  - built_in_reporting: true/false/string (describe the reporting)
  - trace_viewer: true/false
  - debugging_tools: string describing debugging capabilities (e.g. "excellent (time-travel)", "moderate (logs only)")

- limitations: list of 5-10 SPECIFIC, REAL limitations of {name}. These must be genuine constraints that a team would encounter, such as:
  - Language restrictions (e.g., "JavaScript only - no Python/Java support")
  - Platform limitations (e.g., "Cannot test native mobile apps")
  - Architecture constraints (e.g., "Single browser tab execution model")
  - Cost/licensing issues (e.g., "Parallel execution requires paid cloud service")
  - Setup complexity (e.g., "Complex emulator setup required")
  Do NOT use generic limitations like "may be incomplete" or "manual review needed".

- pip_packages: dict of Python package dependencies with version specs (e.g., {{"playwright": ">=1.40", "pytest": ">=7.0"}}).
  Use empty dict {{}} ONLY if the framework is not a Python framework.

- install_commands: list of commands needed AFTER package installation (e.g., ["playwright install --with-deps chromium"]).
  Include any driver installs, browser downloads, SDK setups, or init commands specific to {name}.
  Use empty list [] ONLY if no post-install setup is needed (rare).

- test_command: string — the default CLI command to run tests (e.g., "pytest tests/", "npx cypress run", "mvn test")

- report_paths: list of strings — default output paths for test results/artifacts (e.g., ["playwright-report/", "test-results/"]).
  Include paths for reports, screenshots, videos, or any artifacts {name} generates.

- versions: list of version history entries (at least 2-4 major versions). Each entry must have:
  - version: string (version number)
  - release_date: string (YYYY-MM-DD format)
  - min_runtime: string (e.g., "Node 18+", "Python 3.8+", "Java 11+")
  - notable_features: list of strings (key features in this release)
  - capabilities_added: list of dicts with keys: name, status ("added"/"improved"), description
  - capabilities_deprecated: list of dicts with keys: name, status ("deprecated"/"removed"), description, replacement
  - breaking_changes: list of dicts with keys: description, migration_effort ("low"/"medium"/"high"), affected_apis (list), workaround
  - known_limitations: list of strings
  Include the latest stable version and 2-3 previous major versions. For older versions, include eol_date if known.

Use true/false for booleans, use "limited" or "plugin (name)" for partial support.
""")
        return "\n".join(context_parts)

    def _generate_profile_template(self, name: str, info: dict[str, Any]) -> dict[str, Any]:
        """Generate a profile using template defaults when LLM is unavailable.

        Attempts to produce meaningful, framework-specific data from gathered info
        rather than empty/generic placeholders.
        """
        github = info.get("github", {})
        discovered = info.get("discovered", {})
        pypi = info.get("pypi", {})
        npm = info.get("npm", {})

        # Determine language
        language = (
            github.get("language", "")
            or discovered.get("language", "")
            or "Unknown"
        )
        languages = [language] if language != "Unknown" else ["JavaScript"]

        # Determine category from topics
        topics = github.get("topics", []) or discovered.get("topics", [])
        is_infra = any(t in topics for t in ["infrastructure", "iac", "devops", "cloud"])
        category = "cloud_infrastructure" if is_infra else "test_automation"

        # Determine architecture fit from topics/description
        desc = (github.get("description", "") or discovered.get("description", "")).lower()
        arch_fit = {
            "web_spa": any(w in desc for w in ["web", "browser", "e2e", "ui"]),
            "web_mpa": any(w in desc for w in ["web", "browser", "e2e"]),
            "native_mobile": any(w in desc for w in ["mobile", "ios", "android", "native"]),
            "hybrid_mobile": any(w in desc for w in ["mobile", "hybrid", "react-native"]),
            "desktop": any(w in desc for w in ["desktop", "electron"]),
            "api_testing": any(w in desc for w in ["api", "rest", "http", "graphql"]),
            "microservices": any(w in desc for w in ["microservice", "api", "contract"]),
        }

        # Determine license
        lic = (
            github.get("license", "")
            or discovered.get("license", "")
            or pypi.get("license", "")
            or "MIT"
        )

        # Determine website
        website = (
            github.get("homepage", "")
            or discovered.get("homepage", "")
            or pypi.get("home_page", "")
            or (npm.get("links", {}) or {}).get("homepage", "")
            or discovered.get("repo_url", "")
            or ""
        )

        # Build vendor from GitHub org
        vendor = "Community"
        full_name = github.get("full_name", "")
        if full_name and "/" in full_name:
            vendor = full_name.split("/")[0]

        # ── Derive performance characteristics from language/type ──────
        is_python = language.lower() == "python"
        is_java = language.lower() in ("java", "kotlin")
        is_js = language.lower() in ("javascript", "typescript")
        is_go = language.lower() == "go"
        is_mobile = arch_fit.get("native_mobile") or arch_fit.get("hybrid_mobile")

        if is_go:
            perf_speed, perf_footprint, startup_ms = "fast", "low", 200
        elif is_js and not is_mobile:
            perf_speed, perf_footprint, startup_ms = "fast", "medium", 1000
        elif is_java:
            perf_speed, perf_footprint, startup_ms = "moderate", "medium-high", 3000
        elif is_mobile:
            perf_speed, perf_footprint, startup_ms = "slow", "high", 5000
        else:
            perf_speed, perf_footprint, startup_ms = "moderate", "medium", 1500

        # ── Derive install commands based on ecosystem ─────────────────
        install_commands: list[str] = []
        pip_packages: dict[str, str] = {}
        test_command = ""
        report_paths: list[str] = []

        if is_python:
            pkg_name = pypi.get("name", name.lower().replace(" ", "-"))
            pip_packages = {pkg_name: ">=1.0"}
            test_command = "pytest tests/"
            report_paths = ["test-results/"]
        elif is_java:
            test_command = "mvn test"
            report_paths = ["target/surefire-reports/"]
        elif is_js:
            npm_name = npm.get("name", name.lower().replace(" ", "-"))
            test_command = f"npx {npm_name} run"
            report_paths = ["test-results/"]
            # Check if it's a browser testing tool
            if any(w in desc for w in ["browser", "e2e", "ui", "web"]):
                install_commands = [f"npx {npm_name} install"]
        elif is_go:
            test_command = f"{name.lower()} run tests/"
            report_paths = [f"{name.lower()}-results.json"]

        # ── Derive cloud grids from type ──────────────────────────────
        if is_infra:
            cloud_grids: dict[str, Any] = {"not_applicable": True}
        elif is_mobile:
            cloud_grids = {
                "browserstack": True,
                "sauce_labs": True,
                "aws_device_farm": "limited",
            }
        elif any(arch_fit.get(k) for k in ["web_spa", "web_mpa"]):
            cloud_grids = {
                "browserstack": True,
                "sauce_labs": True,
                "lambda_test": True,
            }
        else:
            cloud_grids = {"standalone": True}

        # ── Derive limitations from known characteristics ─────────────
        limitations = []
        if len(languages) == 1 and languages[0] != "Unknown":
            limitations.append(f"{languages[0]} only — no support for other languages")
        if is_mobile:
            limitations.append("Device/emulator setup is complex and resource-intensive")
            limitations.append("iOS testing requires macOS with Xcode")
        if is_java:
            limitations.append("JVM dependency adds startup overhead")
        if not any(arch_fit.get(k) for k in ["web_spa", "web_mpa"]):
            limitations.append("No web browser testing support")
        if not arch_fit.get("native_mobile"):
            limitations.append("Cannot test native mobile applications")
        limitations.append(f"Community size: {github.get('stars', 0) or discovered.get('stars', 0)} GitHub stars")
        limitations.append("Profile auto-generated from metadata — manual review recommended")
        # Ensure at least 3 limitations
        if len(limitations) < 3:
            limitations.append("Limited documentation compared to mainstream alternatives")

        profile = {
            "framework_name": name,
            "vendor": vendor,
            "license": lic,
            "website": website,
            "first_release": "",
            "latest_version": pypi.get("version", "") or npm.get("version", ""),
            "category": category,
            "languages_supported": languages,
            "architecture_fit": arch_fit,
            "capabilities": {
                "shadow_dom": False,
                "iframe_cross_origin": False,
                "multi_tab": False,
                "multi_domain": False,
                "file_upload_download": False,
                "network_interception": False,
                "parallel_execution": False,
                "auto_wait": False,
                "test_recorder": False,
                "code_generation": False,
            },
            "cicd_integration": {
                "docker_support": True,
                "github_actions": True,
                "jenkins": True,
                "gitlab_ci": True,
                "azure_devops": False,
                "pre_built_docker_images": "community",
            },
            "cloud_grids": cloud_grids,
            "performance": {
                "avg_test_execution_speed": perf_speed,
                "resource_footprint": perf_footprint,
                "startup_time_ms": startup_ms,
                "parallel_overhead": "medium",
            },
            "maintainability": {
                "page_object_support": "custom pattern" if any(
                    arch_fit.get(k) for k in ["web_spa", "web_mpa", "native_mobile"]
                ) else False,
                "fixture_support": "via test framework" if is_python or is_java else False,
                "reusable_components": True,
                "built_in_reporting": False,
                "trace_viewer": False,
                "debugging_tools": "moderate (logs, IDE debugging)" if is_java or is_python else "basic",
            },
            "limitations": limitations,
            "pip_packages": pip_packages,
            "install_commands": install_commands,
            "test_command": test_command,
            "report_paths": report_paths,
            "versions": self._generate_versions_template(name, info, language),
        }
        return profile

    # ── Helpers ───────────────────────────────────────────────────────

    def _generate_versions_template(
        self, name: str, info: dict[str, Any], language: str
    ) -> list[dict[str, Any]]:
        """Generate a versions section from gathered info.

        Derives version history from PyPI/npm data and GitHub info.
        """
        versions: list[dict[str, Any]] = []
        pypi = info.get("pypi", {})
        npm = info.get("npm", {})
        github = info.get("github", {})

        latest_version = pypi.get("version", "") or npm.get("version", "")

        # Determine min_runtime from language
        if language.lower() == "python":
            min_runtime = pypi.get("requires_python", "Python 3.8+") or "Python 3.8+"
        elif language.lower() in ("javascript", "typescript"):
            min_runtime = "Node 16+"
        elif language.lower() in ("java", "kotlin"):
            min_runtime = "Java 11+"
        elif language.lower() == "go":
            min_runtime = "Go 1.21+"
        else:
            min_runtime = f"{language} (version unknown)"

        if latest_version:
            versions.append({
                "version": latest_version,
                "release_date": "unknown",
                "min_runtime": min_runtime,
                "notable_features": [
                    "Latest stable release",
                    "See official changelog for details",
                ],
                "capabilities_added": [],
                "capabilities_deprecated": [],
                "breaking_changes": [],
                "known_limitations": [
                    "Version details auto-generated — verify against official docs",
                ],
            })

        return versions

    def _validate_and_fill_profile(
        self, profile: dict[str, Any], name: str, info: dict[str, Any]
    ) -> dict[str, Any]:
        """Ensure all required fields exist in the LLM-generated profile.

        Validates both structural integrity (correct types) and data completeness
        (critical fields must have meaningful content, not just empty containers).
        """
        required_keys = [
            "framework_name", "vendor", "license", "website", "languages_supported",
            "architecture_fit", "capabilities", "cicd_integration",
        ]
        for key in required_keys:
            if key not in profile:
                # Fill from template
                template = self._generate_profile_template(name, info)
                profile[key] = template[key]

        # Ensure framework_name matches
        if not profile.get("framework_name"):
            profile["framework_name"] = name

        # Ensure lists are actually lists
        if not isinstance(profile.get("languages_supported"), list):
            profile["languages_supported"] = [str(profile.get("languages_supported", "Unknown"))]
        if not isinstance(profile.get("limitations"), list):
            profile["limitations"] = []
        if not isinstance(profile.get("install_commands"), list):
            profile["install_commands"] = []
        if not isinstance(profile.get("report_paths"), list):
            profile["report_paths"] = []

        # Ensure dicts are actually dicts
        for dict_key in ["architecture_fit", "capabilities", "cicd_integration", "cloud_grids",
                         "performance", "maintainability", "pip_packages"]:
            if not isinstance(profile.get(dict_key), dict):
                profile[dict_key] = {}

        # ── Completeness enforcement ─────────────────────────────────
        # Fill critical empty fields with template defaults so profiles
        # are never saved with missing scoring data.
        template = None  # Lazily generated

        if not profile.get("limitations"):
            if template is None:
                template = self._generate_profile_template(name, info)
            profile["limitations"] = template.get("limitations", [
                "Profile auto-generated — manual review recommended",
                "Capabilities may be incomplete",
            ])
            logger.warning(
                "Framework '%s': limitations was empty, filled with defaults", name
            )

        if not profile.get("performance"):
            if template is None:
                template = self._generate_profile_template(name, info)
            profile["performance"] = template.get("performance", {
                "avg_test_execution_speed": "medium",
                "resource_footprint": "medium",
                "startup_time_ms": 1000,
                "parallel_overhead": "medium",
            })
            logger.warning(
                "Framework '%s': performance was empty, filled with defaults", name
            )

        if not profile.get("maintainability"):
            if template is None:
                template = self._generate_profile_template(name, info)
            profile["maintainability"] = template.get("maintainability", {
                "page_object_support": False,
                "fixture_support": False,
                "reusable_components": True,
                "built_in_reporting": False,
                "trace_viewer": False,
                "debugging_tools": "basic",
            })
            logger.warning(
                "Framework '%s': maintainability was empty, filled with defaults", name
            )

        if not profile.get("cloud_grids"):
            profile["cloud_grids"] = {"not_applicable": True}
            logger.warning(
                "Framework '%s': cloud_grids was empty, set to not_applicable", name
            )

        # Ensure versions section exists
        if not isinstance(profile.get("versions"), list) or not profile.get("versions"):
            if template is None:
                template = self._generate_profile_template(name, info)
            profile["versions"] = template.get("versions", [])
            if not profile["versions"]:
                # Generate from available info
                language = ""
                if isinstance(profile.get("languages_supported"), list) and profile["languages_supported"]:
                    language = profile["languages_supported"][0]
                profile["versions"] = self._generate_versions_template(name, info, language)
            logger.warning(
                "Framework '%s': versions was empty, filled with defaults", name
            )

        return profile

    def _classify_discovered(self, fw: DiscoveredFramework) -> list[str]:
        """Classify a discovered framework into categories using available metadata."""
        from src.knowledge_base.schema import classify_framework
        return classify_framework(
            architecture_fit=None,
            description=fw.description,
            topics=fw.topics,
        )

    def _filter_existing(self, frameworks: list[DiscoveredFramework]) -> list[DiscoveredFramework]:
        """Filter out frameworks already in the knowledge base."""
        if not self._kb:
            return frameworks

        existing_names = {name.lower() for name in self._kb.frameworks.keys()}
        # Also check by display name
        existing_display = {fw.framework_name.lower() for fw in self._kb.list_all()}
        all_existing = existing_names | existing_display

        # Build normalized set (no spaces, hyphens, underscores) for fuzzy matching
        all_existing_normalized = {
            name.replace(" ", "").replace("-", "").replace("_", "")
            for name in all_existing
        }

        new: list[DiscoveredFramework] = []
        for fw in frameworks:
            name_lower = fw.name.lower().replace("-", " ").replace("_", " ")
            name_normalized = name_lower.replace(" ", "")

            # Direct match (with spaces)
            if name_lower in all_existing:
                continue

            # Normalized match (no spaces/hyphens/underscores)
            if name_normalized in all_existing_normalized:
                continue

            # Partial match (e.g. "playwright" in "playwright-python")
            is_existing = any(
                existing in name_lower or name_lower in existing
                for existing in all_existing
            )
            # Also check partial with normalized names
            if not is_existing:
                is_existing = any(
                    existing in name_normalized or name_normalized in existing
                    for existing in all_existing_normalized
                )

            if not is_existing:
                new.append(fw)

        return new

    def _should_scan(self) -> bool:
        """Check if enough time has passed since last scan."""
        if self._last_scan_time == 0:
            return True
        return (time.time() - self._last_scan_time) >= self._scan_interval

    def _clean_name(self, raw_name: str) -> str:
        """Clean a repository name into a display name."""
        # Convert kebab/snake case to title case
        name = raw_name.replace("-", " ").replace("_", " ")
        # Capitalize properly
        name = name.title()
        # Fix common casing (JS, IO, etc.)
        name = name.replace(".Js", ".js").replace("Io", "IO")
        return name.strip()

    def _safe_filename(self, name: str) -> str:
        """Convert framework name to a safe filename."""
        safe = re.sub(r"[^a-z0-9]+", "_", name.lower())
        return safe.strip("_")

    def __del__(self):
        """Clean up HTTP client."""
        try:
            self._http.close()
        except Exception:
            pass