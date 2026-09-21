"""Repo Scanner — static analysis of a project repository.

Extracts a ProjectContext dict from a local folder or a public GitHub repo URL.
No LLM calls. Pure file parsing.

The context is injected into _batch_generate so the LLM writes code that
references real classes, fixtures, base URLs and env vars from the project
instead of inventing them.

Supported inputs:
  - Local folder path  (e.g. C:/projects/my-app)
  - GitHub HTTPS URL   (e.g. https://github.com/owner/repo)
  - GitHub ZIP URL     (auto-downloaded and extracted)
"""
from __future__ import annotations

import ast
import io
import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan(source: str) -> dict[str, Any]:
    """Scan a repo from a local path or GitHub URL.

    Returns a ProjectContext dict with keys:
      project_name, base_urls, env_vars, dependencies, external_deps,
      existing_fixtures, existing_helpers, existing_page_objects,
      detected_test_framework, config_files, summary
    """
    source = source.strip()
    if _is_github_url(source):
        root = _fetch_github_zip(source)
    else:
        root = Path(source)
        if not root.exists():
            raise FileNotFoundError(f"Path not found: {source}")

    return _scan_root(root, project_name=root.name)


# ---------------------------------------------------------------------------
# GitHub fetch
# ---------------------------------------------------------------------------

def _is_github_url(s: str) -> bool:
    return s.startswith("https://github.com/") or s.startswith("http://github.com/")


def _fetch_github_zip(url: str) -> Path:
    """Download a GitHub repo as ZIP and extract to a temp directory."""
    import tempfile

    # Normalise: strip trailing .git, build archive URL
    url = url.rstrip("/").removesuffix(".git")
    # https://github.com/owner/repo  →  https://github.com/owner/repo/archive/refs/heads/main.zip
    zip_url = f"{url}/archive/refs/heads/main.zip"

    logger.info("RepoScanner: downloading %s", zip_url)
    try:
        resp = httpx.get(zip_url, follow_redirects=True, timeout=30)
        if resp.status_code == 404:
            # Try 'master' branch
            zip_url = f"{url}/archive/refs/heads/master.zip"
            resp = httpx.get(zip_url, follow_redirects=True, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"Failed to download repo from {url}: {exc}") from exc

    tmp = Path(tempfile.mkdtemp(prefix="repo_scan_"))
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        zf.extractall(tmp)

    # The ZIP extracts to a single top-level folder (repo-main/ or repo-master/)
    subdirs = [p for p in tmp.iterdir() if p.is_dir()]
    return subdirs[0] if subdirs else tmp


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------

_SKIP_DIRS = {
    ".git", ".github", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".env", "dist", "build", "target", ".idea", ".vscode",
    "coverage", ".pytest_cache", ".mypy_cache",
}

_MAX_FILE_BYTES = 100_000   # skip files larger than 100 KB
_MAX_FILES      = 500       # stop after scanning this many files


def _scan_root(root: Path, project_name: str) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "project_name":           project_name,
        "base_urls":              [],
        "env_vars":               [],
        "dependencies":           [],
        "external_deps":          [],
        "existing_fixtures":      [],
        "existing_helpers":       [],
        "existing_page_objects":  [],
        "detected_test_framework": "",
        "config_files":           [],
        "summary":                "",
    }

    files_scanned = 0
    for path in _walk(root):
        if files_scanned >= _MAX_FILES:
            break
        files_scanned += 1
        rel = str(path.relative_to(root)).replace("\\", "/")
        name = path.name.lower()

        # ── Dependency manifests ──────────────────────────────────────
        if name == "requirements.txt":
            ctx["dependencies"].extend(_parse_requirements(path))
            ctx["config_files"].append(rel)

        elif name in ("package.json",):
            _parse_package_json(path, ctx)
            ctx["config_files"].append(rel)

        elif name in ("pom.xml",):
            ctx["dependencies"].extend(_parse_pom_xml(path))
            ctx["config_files"].append(rel)

        elif name in ("build.gradle", "build.gradle.kts"):
            ctx["dependencies"].extend(_parse_gradle(path))
            ctx["config_files"].append(rel)

        # ── Config / env files ────────────────────────────────────────
        elif name in (".env", ".env.example", ".env.sample", ".env.test"):
            _parse_dotenv(path, ctx)
            ctx["config_files"].append(rel)

        elif name in ("application.properties", "application.yml",
                      "application.yaml", "config.yml", "config.yaml",
                      "settings.py", "config.py"):
            _parse_config_file(path, ctx)
            ctx["config_files"].append(rel)

        # ── Python source files ───────────────────────────────────────
        elif path.suffix == ".py":
            _parse_python_file(path, rel, ctx)

        # ── Java source files ─────────────────────────────────────────
        elif path.suffix == ".java":
            _parse_java_file(path, rel, ctx)

        # ── JavaScript / TypeScript ───────────────────────────────────
        elif path.suffix in (".js", ".ts", ".jsx", ".tsx"):
            _parse_js_file(path, rel, ctx)

    # Deduplicate all lists
    for key in ("base_urls", "env_vars", "dependencies", "external_deps",
                "existing_fixtures", "existing_helpers", "existing_page_objects"):
        ctx[key] = list(dict.fromkeys(ctx[key]))

    # Detect test framework from dependencies
    ctx["detected_test_framework"] = _detect_test_framework(ctx["dependencies"])

    # Build human-readable summary for LLM injection
    ctx["summary"] = _build_summary(ctx)

    logger.info(
        "RepoScanner: scanned %d files — %d deps, %d fixtures, %d helpers, %d urls",
        files_scanned,
        len(ctx["dependencies"]),
        len(ctx["existing_fixtures"]),
        len(ctx["existing_helpers"]),
        len(ctx["base_urls"]),
    )
    return ctx


def _walk(root: Path):
    """Yield all non-skipped files under root."""
    for path in root.rglob("*"):
        if path.is_file():
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            yield path


# ---------------------------------------------------------------------------
# Dependency parsers
# ---------------------------------------------------------------------------

def _parse_requirements(path: Path) -> list[str]:
    deps = []
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                # Strip version specifiers: requests>=2.0 → requests
                name = re.split(r"[>=<!;\[]", line)[0].strip()
                if name:
                    deps.append(name.lower())
    except Exception:
        pass
    return deps


def _parse_package_json(path: Path, ctx: dict) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        for section in ("dependencies", "devDependencies"):
            for pkg in data.get(section, {}):
                ctx["dependencies"].append(pkg.lower())
    except Exception:
        pass


def _parse_pom_xml(path: Path) -> list[str]:
    deps = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"<artifactId>([^<]+)</artifactId>", text):
            deps.append(m.group(1).lower())
    except Exception:
        pass
    return deps


def _parse_gradle(path: Path) -> list[str]:
    deps = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"['\"]([a-zA-Z0-9._-]+:[a-zA-Z0-9._-]+):[^'\"]+['\"]", text):
            artifact = m.group(1).split(":")[-1].lower()
            deps.append(artifact)
    except Exception:
        pass
    return deps


# ---------------------------------------------------------------------------
# Config / env parsers
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s\"'<>{}|\\^`\[\]]+")

def _parse_dotenv(path: Path, ctx: dict) -> None:
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                ctx["env_vars"].append(key)
                if _URL_RE.match(val):
                    ctx["base_urls"].append(val.rstrip("/"))
    except Exception:
        pass


def _parse_config_file(path: Path, ctx: dict) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Extract URLs
        for m in _URL_RE.finditer(text):
            url = m.group(0).rstrip("/.,;)")
            if url not in ctx["base_urls"]:
                ctx["base_urls"].append(url)
        # Extract env var references
        for m in re.finditer(r"os\.environ(?:\.get)?\(['\"]([A-Z_][A-Z0-9_]*)['\"]", text):
            ctx["env_vars"].append(m.group(1))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Python file parser
# ---------------------------------------------------------------------------

_EXTERNAL_IMPORT_MARKERS = {
    # DB
    "psycopg2": "postgresql", "pymysql": "mysql", "pymongo": "mongodb",
    "sqlalchemy": "sqlalchemy", "cx_oracle": "oracle",
    # Messaging
    "confluent_kafka": "kafka", "kafka": "kafka",
    "pika": "rabbitmq", "boto3": "aws-sqs",
    "aio_pika": "rabbitmq",
    # HTTP clients
    "requests": "http-requests", "httpx": "http-httpx",
    "aiohttp": "http-aiohttp",
    # Test frameworks
    "pytest": "pytest", "unittest": "unittest",
    "selenium": "selenium", "playwright": "playwright",
    "appium": "appium",
    # Auth
    "jwt": "jwt", "authlib": "oauth",
    # Cloud
    "boto3": "aws", "google.cloud": "gcp", "azure": "azure",
}

def _parse_python_file(path: Path, rel: str, ctx: dict) -> None:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return

    # Extract URLs and env vars from raw text (fast, no AST needed)
    for m in _URL_RE.finditer(source):
        url = m.group(0).rstrip("/.,;)")
        if url not in ctx["base_urls"]:
            ctx["base_urls"].append(url)

    for m in re.finditer(r"os\.environ(?:\.get)?\(['\"]([A-Z_][A-Z0-9_]*)['\"]", source):
        ctx["env_vars"].append(m.group(1))

    # AST parse for imports, classes, functions
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return

    name_lower = path.name.lower()
    is_fixture  = "conftest" in name_lower or "fixture" in name_lower
    is_helper   = any(k in name_lower for k in ("util", "helper", "common", "base", "shared"))
    is_page_obj = any(k in name_lower for k in ("page", "screen", "component"))
    is_test     = name_lower.startswith("test_") or name_lower.endswith("_test.py")

    for node in ast.walk(tree):
        # Imports → external deps
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = ""
            if isinstance(node, ast.Import):
                mod = node.names[0].name.split(".")[0].lower() if node.names else ""
            else:
                mod = (node.module or "").split(".")[0].lower()
            marker = _EXTERNAL_IMPORT_MARKERS.get(mod)
            if marker and marker not in ctx["external_deps"]:
                ctx["external_deps"].append(marker)

        # Classes and functions → fixtures / helpers / page objects
        elif isinstance(node, ast.ClassDef) and not is_test:
            entry = f"{rel}::{node.name}"
            if is_page_obj:
                ctx["existing_page_objects"].append(entry)
            elif is_helper:
                ctx["existing_helpers"].append(entry)

        elif isinstance(node, ast.FunctionDef):
            # pytest fixtures
            decorators = [
                (d.id if isinstance(d, ast.Name) else
                 d.attr if isinstance(d, ast.Attribute) else "")
                for d in node.decorator_list
            ]
            if "fixture" in decorators and is_fixture:
                ctx["existing_fixtures"].append(f"{rel}::{node.name}")
            elif not is_test and is_helper:
                ctx["existing_helpers"].append(f"{rel}::{node.name}")


# ---------------------------------------------------------------------------
# Java file parser
# ---------------------------------------------------------------------------

def _parse_java_file(path: Path, rel: str, ctx: dict) -> None:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return

    for m in _URL_RE.finditer(source):
        url = m.group(0).rstrip("/.,;)")
        if url not in ctx["base_urls"]:
            ctx["base_urls"].append(url)

    # env vars via System.getenv / @Value
    for m in re.finditer(r'System\.getenv\("([A-Z_][A-Z0-9_]*)"\)', source):
        ctx["env_vars"].append(m.group(1))
    for m in re.finditer(r'@Value\("\$\{([^}]+)\}"\)', source):
        ctx["env_vars"].append(m.group(1).upper().replace(".", "_"))

    # imports → external deps
    for m in re.finditer(r"^import\s+([\w.]+);", source, re.MULTILINE):
        pkg = m.group(1).lower()
        for marker_key, marker_val in {
            "org.springframework": "spring", "io.restassured": "rest-assured",
            "org.testng": "testng", "org.junit": "junit",
            "org.apache.kafka": "kafka", "com.rabbitmq": "rabbitmq",
            "java.sql": "jdbc", "org.hibernate": "hibernate",
            "software.amazon": "aws", "com.google.cloud": "gcp",
        }.items():
            if pkg.startswith(marker_key) and marker_val not in ctx["external_deps"]:
                ctx["external_deps"].append(marker_val)

    name_lower = path.name.lower()
    # Page objects / helpers
    for m in re.finditer(r"public\s+class\s+(\w+)", source):
        cls = m.group(1)
        if any(k in name_lower for k in ("page", "screen")):
            ctx["existing_page_objects"].append(f"{rel}::{cls}")
        elif any(k in name_lower for k in ("util", "helper", "base", "common")):
            ctx["existing_helpers"].append(f"{rel}::{cls}")


# ---------------------------------------------------------------------------
# JS / TS file parser
# ---------------------------------------------------------------------------

def _parse_js_file(path: Path, rel: str, ctx: dict) -> None:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return

    for m in _URL_RE.finditer(source):
        url = m.group(0).rstrip("/.,;)")
        if url not in ctx["base_urls"]:
            ctx["base_urls"].append(url)

    for m in re.finditer(r"process\.env\.([A-Z_][A-Z0-9_]*)", source):
        ctx["env_vars"].append(m.group(1))

    # imports
    for m in re.finditer(r"""(?:import|require)\s*\(?['"]([^'"]+)['"]\)?""", source):
        pkg = m.group(1).lstrip("@").split("/")[0].lower()
        for marker_key, marker_val in {
            "playwright": "playwright", "cypress": "cypress",
            "selenium-webdriver": "selenium", "axios": "http-axios",
            "node-fetch": "http-fetch", "kafkajs": "kafka",
            "amqplib": "rabbitmq", "pg": "postgresql",
            "mysql": "mysql", "mongoose": "mongodb",
            "jest": "jest", "mocha": "mocha", "jasmine": "jasmine",
        }.items():
            if pkg == marker_key and marker_val not in ctx["external_deps"]:
                ctx["external_deps"].append(marker_val)

    name_lower = path.name.lower()
    if any(k in name_lower for k in ("page", "screen", "component")):
        for m in re.finditer(r"(?:class|const)\s+(\w+)", source):
            ctx["existing_page_objects"].append(f"{rel}::{m.group(1)}")
            break  # just the first one per file


# ---------------------------------------------------------------------------
# Test framework detection
# ---------------------------------------------------------------------------

_FRAMEWORK_SIGNALS: list[tuple[list[str], str]] = [
    (["pytest", "pytest-playwright"],          "pytest-playwright"),
    (["pytest"],                               "pytest"),
    (["playwright"],                           "playwright"),
    (["selenium"],                             "selenium"),
    (["cypress"],                              "cypress"),
    (["testng"],                               "testng"),
    (["junit"],                                "junit"),
    (["jest"],                                 "jest"),
    (["mocha"],                                "mocha"),
    (["robot"],                                "robot-framework"),
    (["rest-assured"],                         "rest-assured"),
    (["appium"],                               "appium"),
]

def _detect_test_framework(deps: list[str]) -> str:
    deps_lower = [d.lower() for d in deps]
    for signals, name in _FRAMEWORK_SIGNALS:
        if all(s in deps_lower for s in signals):
            return name
    return ""


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------

def _build_summary(ctx: dict) -> str:
    parts = [f"Project: {ctx['project_name']}"]

    if ctx["detected_test_framework"]:
        parts.append(f"Existing test framework: {ctx['detected_test_framework']}")

    if ctx["base_urls"]:
        # Only include likely app base URLs (not CDN/docs/badge URLs)
        app_urls = [u for u in ctx["base_urls"][:5]
                    if not any(x in u for x in ("shields.io", "badge", "cdn", "fonts.google",
                                                 "github.com/", "npmjs", "pypi.org"))]
        if app_urls:
            parts.append("Base URLs: " + ", ".join(app_urls[:3]))

    if ctx["env_vars"]:
        parts.append("Env vars: " + ", ".join(ctx["env_vars"][:10]))

    if ctx["external_deps"]:
        parts.append("External dependencies: " + ", ".join(ctx["external_deps"]))

    if ctx["existing_fixtures"]:
        parts.append("Existing fixtures: " + ", ".join(
            f.split("::")[-1] for f in ctx["existing_fixtures"][:8]
        ))

    if ctx["existing_helpers"]:
        parts.append("Existing helpers/utils: " + ", ".join(
            h.split("::")[-1] for h in ctx["existing_helpers"][:8]
        ))

    if ctx["existing_page_objects"]:
        parts.append("Existing page objects: " + ", ".join(
            p.split("::")[-1] for p in ctx["existing_page_objects"][:8]
        ))

    if ctx["dependencies"]:
        parts.append(f"Dependencies ({len(ctx['dependencies'])} total): "
                     + ", ".join(ctx["dependencies"][:15]))

    return "\n".join(parts)
