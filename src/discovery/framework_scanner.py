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

        Args:
            profile: Dict matching FrameworkData schema.

        Returns:
            Path to the created YAML file, or None on failure.
        """
        if not profile or "framework_name" not in profile:
            logger.error("Invalid profile: missing framework_name")
            return None

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
        """Return a curated list of known emerging frameworks to check.

        This serves as a fallback when GitHub search is rate-limited.
        Updated periodically with frameworks gaining traction.
        """
        curated = [
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
                description="All-in-one test automation solution",
                stars=600,
                language="Java",
                license="Commercial",
                topics=["test-automation", "web-testing", "api-testing", "mobile-testing"],
                homepage="https://katalon.com/",
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
        ]
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

        context_parts.append("""
\nThe YAML profile MUST include exactly these top-level keys:
- framework_name: string
- vendor: string
- license: string (SPDX identifier)
- website: string (URL)
- first_release: string (year)
- latest_version: string
- category: "test_automation" or "cloud_infrastructure"
- languages_supported: list of strings
- architecture_fit: dict with keys: web_spa, web_mpa, native_mobile, hybrid_mobile, desktop, api_testing, microservices (boolean values)
- capabilities: dict with keys like shadow_dom, iframe_cross_origin, multi_tab, multi_domain, file_upload_download, network_interception, parallel_execution, auto_wait, test_recorder, code_generation (boolean or string values)
- cicd_integration: dict with keys: docker_support, github_actions, jenkins, gitlab_ci, azure_devops (boolean values)
- cloud_grids: dict with keys: browserstack, sauce_labs, lambda_test (boolean values)
- performance: dict with keys: avg_test_execution_speed, resource_footprint, startup_time_ms, parallel_overhead
- maintainability: dict with keys: page_object_support, fixture_support, reusable_components, built_in_reporting, debugging_tools
- limitations: list of strings (3-5 items)
- pip_packages: dict of package: version_spec (if Python; empty dict otherwise)
- install_commands: list of strings
- test_command: string
- report_paths: list of strings

Use true/false for booleans, use "limited" or "plugin (name)" for partial support.
""")
        return "\n".join(context_parts)

    def _generate_profile_template(self, name: str, info: dict[str, Any]) -> dict[str, Any]:
        """Generate a profile using template defaults when LLM is unavailable."""
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
            },
            "cloud_grids": {
                "browserstack": False,
                "sauce_labs": False,
                "lambda_test": False,
            },
            "performance": {
                "avg_test_execution_speed": "medium",
                "resource_footprint": "medium",
                "startup_time_ms": 1000,
                "parallel_overhead": "medium",
            },
            "maintainability": {
                "page_object_support": True,
                "fixture_support": False,
                "reusable_components": True,
                "built_in_reporting": False,
                "debugging_tools": "basic",
            },
            "limitations": [
                "Profile auto-generated — manual review recommended",
                "Capabilities may be incomplete",
                f"Stars: {github.get('stars', 0) or discovered.get('stars', 0)}",
            ],
            "pip_packages": {},
            "install_commands": [],
            "test_command": "",
            "report_paths": [],
        }
        return profile

    # ── Helpers ───────────────────────────────────────────────────────

    def _validate_and_fill_profile(
        self, profile: dict[str, Any], name: str, info: dict[str, Any]
    ) -> dict[str, Any]:
        """Ensure all required fields exist in the LLM-generated profile."""
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

        return profile

    def _filter_existing(self, frameworks: list[DiscoveredFramework]) -> list[DiscoveredFramework]:
        """Filter out frameworks already in the knowledge base."""
        if not self._kb:
            return frameworks

        existing_names = {name.lower() for name in self._kb.frameworks.keys()}
        # Also check by display name
        existing_display = {fw.framework_name.lower() for fw in self._kb.list_all()}
        all_existing = existing_names | existing_display

        new: list[DiscoveredFramework] = []
        for fw in frameworks:
            name_lower = fw.name.lower().replace("-", " ").replace("_", " ")
            # Check various normalizations
            if name_lower not in all_existing and name_lower.replace(" ", "") not in all_existing:
                # Also check partial matches (e.g. "playwright" in "playwright-python")
                is_existing = any(
                    existing in name_lower or name_lower in existing
                    for existing in all_existing
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