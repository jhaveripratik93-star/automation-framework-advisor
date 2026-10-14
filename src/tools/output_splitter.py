"""output_splitter.py — Post-conversion file splitter (no LLM calls).

Takes a single block of converted code and splits it into:
  - tests/test_<name>.py  (one per test function / Robot test case)
  - pages/<Name>Page.py   (page-object classes)
  - helpers/helpers.py    (utility / helper functions)
  - conftest.py           (pytest fixtures)
  - resources/keywords.robot   (Robot Framework shared keywords)
  - resources/variables.robot  (Robot Framework variables)

Key guarantee: every test file gets the correct imports so nothing breaks.
"""
from __future__ import annotations

import ast
import re
import logging
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)


# ── Public entry point ────────────────────────────────────────────────

def split(converted_code: str, framework: str, source_stem: str = "tests") -> dict[str, str]:
    """Split *converted_code* into a {path: content} dict.

    Args:
        converted_code: The full converted source code string.
        framework:      Target framework name (case-insensitive).
        source_stem:    Stem of the original source file (used for naming).

    Returns a dict of {relative_path: file_content}.
    Falls back to {f"tests/{source_stem}.ext": converted_code} on any error
    so the caller always gets something usable.
    """
    converted_code = strip_trailing_cicd_block(converted_code)
    fw = framework.lower()
    try:
        if "robot" in fw:
            return _split_robot(converted_code, source_stem)
        elif "k6" in fw:
            return _split_k6(converted_code, source_stem)
        else:
            return _split_python(converted_code, source_stem)
    except Exception as exc:
        logger.warning("output_splitter: split failed (%s), returning single file", exc)
        ext = ".robot" if "robot" in fw else (".js" if "k6" in fw else ".py")
        return {f"tests/{source_stem}{ext}": converted_code}


# ── Leaked CI/CD trailer guard ─────────────────────────────────────────
#
# Despite being instructed not to, LLMs sometimes still append a trailing
# "CI/CD integration" block (a GitHub Actions snippet, raw YAML, or both)
# to converted test code — a learned habit from training data, not
# something the prompt can fully suppress. Rather than relying solely on
# prompt wording (see executor.py's _build_system_prompt), this function
# is a second, independent guard: it runs on every piece of converted code
# before any framework-specific parsing happens, so even if the model
# ignores instructions, the leaked trailer never reaches _split_robot()/
# _parse_robot_test_cases() and can't explode into dozens of garbage
# "test case" files (one per leaked YAML key).
#
# _YAML_CI_KEYS / _YAML_LIST_ITEM / _LEADING_KEY are also reused by
# _looks_like_test_case_name() below — single source of truth for "what
# counts as leaked YAML" shared by both guards.

_CICD_TRAILER_MARKER = re.compile(
    r"(ci/cd\s*integration|github\s*actions?\s*(step|workflow)|\.github/workflows)",
    re.IGNORECASE,
)
_LEADING_KEY = re.compile(r"^([A-Za-z][\w\-]*)\s*:(\s|$)")
_YAML_CI_KEYS = {
    "on", "push", "pull_request", "pull_request_target", "workflow_dispatch",
    "workflow_call", "schedule", "release", "branches", "branches-ignore",
    "tags", "tags-ignore", "paths", "paths-ignore", "jobs", "runs-on", "steps",
    "uses", "with", "env", "if", "needs", "strategy", "matrix", "fail-fast",
    "timeout-minutes", "continue-on-error", "working-directory", "shell",
    "container", "services", "permissions", "concurrency", "defaults",
    "outputs", "secrets", "inputs", "python-version", "node-version",
    "java-version", "go-version", "cache", "token", "ref", "repository",
    "cron",
}
_YAML_LIST_ITEM = re.compile(r"^-\s+\S")


# Top-level GitHub Actions workflow keys. These two are distinctive enough
# that a bare, unindented "on:" or "jobs:" line essentially never appears
# in legitimate Robot Framework / Python / K6 test code — if either shows
# up, everything from that line to the end of the block is the YAML tree
# (job names, step names, nested keys) and gets dropped wholesale, rather
# than relying on recognizing every individual nested key by name (which
# breaks on arbitrary job/step names like "test:" or "build:" that aren't
# themselves CI vocabulary).
_TOP_LEVEL_CICD_ANCHORS = {"on", "jobs"}


def strip_trailing_cicd_block(code: str) -> str:
    """Remove a leaked CI/CD block (comment marker + any raw YAML trailer)
    from the end of converted code. No-op if nothing matches.

    Three passes, each truncating further if it finds something:
    1. Explicit marker line ("CI/CD INTEGRATION", "GitHub Actions
       step/workflow", ".github/workflows") — cut from that line onward.
    2. A bare top-level YAML anchor key ("on:" or "jobs:") — cut from that
       line onward wholesale (handles arbitrary nested job/step names that
       aren't themselves recognizable CI vocabulary, e.g. "test:" as a job
       name).
    3. Walk backward from the end, dropping any further trailing lines
       that are recognizable YAML-CI-key lines ("runs-on: ...",
       "python-version: ...") or YAML list items ("- uses: ...") —
       catches a short leftover fragment with no anchor at all.
    """
    lines = code.splitlines()

    for i, line in enumerate(lines):
        if _CICD_TRAILER_MARKER.search(line):
            lines = lines[:i]
            break

    for i, line in enumerate(lines):
        stripped = line.strip()
        m = _LEADING_KEY.match(stripped)
        if m and m.group(1).lower() in _TOP_LEVEL_CICD_ANCHORS:
            lines = lines[:i]
            break

    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        bare = last.lstrip("#/ ").strip()
        if not bare:
            lines.pop()
            continue
        if _YAML_LIST_ITEM.match(bare):
            lines.pop()
            continue
        m = _LEADING_KEY.match(bare)
        if m and m.group(1).lower() in _YAML_CI_KEYS:
            lines.pop()
            continue
        break

    result = "\n".join(lines).rstrip()
    if result != code.rstrip():
        logger.warning("output_splitter: stripped a leaked CI/CD trailer from converted code")
    return result + "\n" if result else result


# ── Robot Framework splitter ──────────────────────────────────────────

_RF_SECTION = re.compile(r"^\*{3}\s*(.+?)\s*\*{3}", re.MULTILINE)


def _parse_robot_sections(code: str) -> dict[str, str]:
    """Return {section_name_lower: body_text} for each *** Section *** block."""
    sections: dict[str, str] = {}
    matches = list(_RF_SECTION.finditer(code))
    for i, m in enumerate(matches):
        name = m.group(1).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(code)
        sections[name] = code[start:end].strip()
    return sections


def _split_robot(code: str, stem: str) -> dict[str, str]:
    sections = _parse_robot_sections(code)

    settings_block  = sections.get("settings", "")
    variables_block = sections.get("variables", "")
    test_cases_block = sections.get("test cases", "")
    keywords_block  = sections.get("keywords", "")

    files: dict[str, str] = {}

    # ── resources/variables.robot ─────────────────────────────────────
    if variables_block.strip():
        files["resources/variables.robot"] = (
            "*** Variables ***\n" + variables_block
        )

    # ── resources/keywords.robot ──────────────────────────────────────
    if keywords_block.strip():
        kw_settings = "*** Settings ***\nLibrary    SeleniumLibrary\n\n" if "seleniumlibrary" in settings_block.lower() else ""
        files["resources/keywords.robot"] = (
            kw_settings
            + "*** Keywords ***\n"
            + keywords_block
        )

    # ── Split test cases into individual .robot files ─────────────────
    test_cases = _parse_robot_test_cases(test_cases_block)

    # Build the shared Settings header for every test file
    shared_settings = _build_robot_settings(settings_block, variables_block, keywords_block)

    if not test_cases:
        # No individual cases found — keep as one file
        files[f"tests/{stem}.robot"] = code
        return files

    for tc_name, tc_body in test_cases.items():
        slug = _slugify(tc_name)
        content = shared_settings + "\n*** Test Cases ***\n" + tc_name + "\n" + tc_body
        files[f"tests/test_{slug}.robot"] = content.strip()

    return files


def _parse_robot_test_cases(block: str) -> dict[str, str]:
    """Parse *** Test Cases *** body into {test_name: indented_body}.

    Guards against non-test-case content (e.g. CI/CD YAML such as
    `on:`, `jobs:`, `runs-on: ubuntu-latest`, `uses: actions/checkout@v3`
    that an LLM occasionally appends despite instructions not to) leaking
    into the parse. A line is only treated as a test case name if it looks
    like one — see `_looks_like_test_case_name`. Lines that fail the check
    are dropped (with a warning) rather than silently becoming a fake test
    case + its own garbage output file.
    """
    cases: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in block.splitlines():
        # A test case name: non-empty line that does NOT start with whitespace
        if line and not line[0].isspace():
            if not _looks_like_test_case_name(line):
                logger.warning(
                    "output_splitter: dropping non-test-case line while parsing "
                    "*** Test Cases *** section (looks like leaked non-Robot "
                    "content, e.g. CI/CD YAML): %r",
                    line.strip()[:80],
                )
                continue
            if current_name is not None:
                cases[current_name] = "\n".join(current_lines)
            current_name = line.rstrip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_name is not None:
        cases[current_name] = "\n".join(current_lines)

    return cases


# Genuine Robot Framework test case names are free-text phrases and MAY
# legitimately contain a colon (e.g. "TC1: Login with valid credentials" is
# a common naming convention) — so a generic "anything shaped like key:
# value" check is too broad and produces false positives on real test
# names. Instead, reject only lines whose leading token (before the first
# colon) is a known GitHub Actions / YAML CI vocabulary word, or that are a
# YAML list item ("- uses: ..."). This targets the actual leaked-content
# pattern (CI/CD YAML an LLM appended despite being told not to) without
# rejecting ordinary "ID: description" test names.
# (_YAML_CI_KEYS / _YAML_LIST_ITEM / _LEADING_KEY are defined once, above,
# next to strip_trailing_cicd_block — shared by both guards.)


def _looks_like_test_case_name(line: str) -> bool:
    """Return False for lines that look like leaked YAML/CI content instead
    of a real Robot Framework test case name."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    if _YAML_LIST_ITEM.match(stripped):
        return False
    m = _LEADING_KEY.match(stripped)
    if m and m.group(1).lower() in _YAML_CI_KEYS:
        return False
    return True


def _build_robot_settings(settings_block: str, variables_block: str, keywords_block: str) -> str:
    """Build the *** Settings *** header for a split test file."""
    lines = ["*** Settings ***"]
    # Keep original library imports
    for line in settings_block.splitlines():
        stripped = line.strip()
        if stripped.startswith("Library") or stripped.startswith("Resource"):
            lines.append(line)

    # Reference shared resource files
    if variables_block.strip():
        lines.append("Resource    ../resources/variables.robot")
    if keywords_block.strip():
        lines.append("Resource    ../resources/keywords.robot")

    return "\n".join(lines) + "\n\n"


# ── K6 JavaScript splitter ───────────────────────────────────────────

_K6_EXPORT_DEFAULT = re.compile(r"^export\s+default\s+(?:async\s+)?function", re.MULTILINE)
_K6_OPTIONS = re.compile(r"^export\s+const\s+options\s*=", re.MULTILINE)


def _split_k6(code: str, stem: str) -> dict[str, str]:
    """K6 scripts are self-contained JS files — return as single file.

    Only splits when multiple export default functions are detected
    (rare multi-scenario output), otherwise keeps as one file.
    """
    defaults = list(_K6_EXPORT_DEFAULT.finditer(code))
    if len(defaults) <= 1:
        return {f"tests/{stem}.js": code}

    # Multiple scenarios — split each into its own file
    files: dict[str, str] = {}
    for i, m in enumerate(defaults):
        start = m.start()
        end = defaults[i + 1].start() if i + 1 < len(defaults) else len(code)
        # Include options block if present before this function
        block = code[start:end].strip()
        files[f"tests/{stem}_{i + 1}.js"] = block
    return files


# ── Python splitter ───────────────────────────────────────────────────

def _split_python(code: str, stem: str) -> dict[str, str]:
    """Split converted Python code into test / page-object / helper / conftest files."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Can't parse — return as-is
        return {f"tests/{stem}.py": code}

    # Collect top-level nodes with their source lines
    lines = code.splitlines(keepends=True)

    imports: list[ast.stmt]       = []
    test_funcs: list[ast.stmt]    = []
    fixture_funcs: list[ast.stmt] = []
    page_classes: list[ast.stmt]  = []
    helper_funcs: list[ast.stmt]  = []
    other: list[ast.stmt]         = []

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
        elif isinstance(node, ast.FunctionDef):
            deco_names = _decorator_names(node)
            if node.name.startswith("test_"):
                test_funcs.append(node)
            elif "fixture" in deco_names:
                fixture_funcs.append(node)
            elif _is_helper(node.name):
                helper_funcs.append(node)
            else:
                other.append(node)
        elif isinstance(node, ast.AsyncFunctionDef):
            if node.name.startswith("test_"):
                test_funcs.append(node)
            else:
                other.append(node)
        elif isinstance(node, ast.ClassDef):
            if _is_page_object(node.name):
                page_classes.append(node)
            else:
                other.append(node)
        else:
            other.append(node)

    # If nothing to split (e.g. single test, no helpers/pages) return as-is
    has_split = (
        (page_classes or helper_funcs or fixture_funcs)
        and test_funcs
    )
    if not has_split:
        return {f"tests/{stem}.py": code}

    files: dict[str, str] = {}
    import_src = _nodes_to_source(imports, lines)

    # ── helpers/helpers.py ────────────────────────────────────────────
    helper_names: list[str] = []
    if helper_funcs:
        helper_src = import_src + "\n\n" + _nodes_to_source(helper_funcs, lines)
        files["helpers/helpers.py"] = helper_src.strip()
        helper_names = [n.name for n in helper_funcs]  # type: ignore[attr-defined]

    # ── pages/<Name>Page.py ───────────────────────────────────────────
    page_module_map: dict[str, str] = {}  # class_name -> module path
    for cls in page_classes:
        cls_src = import_src + "\n\n" + _nodes_to_source([cls], lines)
        path = f"pages/{cls.name}.py"  # type: ignore[attr-defined]
        files[path] = cls_src.strip()
        page_module_map[cls.name] = f"pages.{cls.name}"  # type: ignore[attr-defined]

    # ── conftest.py ───────────────────────────────────────────────────
    if fixture_funcs:
        conftest_src = import_src + "\nimport pytest\n\n" + _nodes_to_source(fixture_funcs, lines)
        files["conftest.py"] = conftest_src.strip()

    # ── tests/test_<name>.py — one file per test function ─────────────
    # Build the extra import block needed by each test file
    extra_imports = _build_test_imports(
        page_module_map, helper_names, fixture_funcs, import_src
    )

    # Group tests by name prefix (e.g. test_login_* → test_login.py)
    groups = _group_tests(test_funcs)

    for group_name, group_nodes in groups.items():
        test_src = (
            extra_imports
            + "\n\n"
            + _nodes_to_source(group_nodes, lines)
        )
        files[f"tests/test_{group_name}.py"] = test_src.strip()

    return files


# ── Helpers ───────────────────────────────────────────────────────────

def _nodes_to_source(nodes: list[ast.stmt], all_lines: list[str]) -> str:
    """Extract source lines for a list of AST nodes."""
    if not nodes:
        return ""
    parts = []
    for node in nodes:
        start = node.lineno - 1
        end = node.end_lineno  # type: ignore[attr-defined]
        parts.append("".join(all_lines[start:end]))
    return "\n".join(parts)


def _decorator_names(node: ast.FunctionDef) -> list[str]:
    names = []
    for d in node.decorator_list:
        if isinstance(d, ast.Name):
            names.append(d.id)
        elif isinstance(d, ast.Attribute):
            names.append(d.attr)
        elif isinstance(d, ast.Call):
            if isinstance(d.func, ast.Name):
                names.append(d.func.id)
            elif isinstance(d.func, ast.Attribute):
                names.append(d.func.attr)
    return names


def _is_page_object(class_name: str) -> bool:
    low = class_name.lower()
    return any(k in low for k in ("page", "screen", "component", "modal", "dialog"))


def _is_helper(func_name: str) -> bool:
    low = func_name.lower()
    # Only treat as a separate helper module if explicitly named as such
    return any(k in low for k in ("helper", "util", "wait_for", "screenshot"))


def _group_tests(test_funcs: list[ast.stmt]) -> dict[str, list[ast.stmt]]:
    """Group test functions by their name prefix (first meaningful word after test_)."""
    groups: dict[str, list[ast.stmt]] = {}
    for node in test_funcs:
        name: str = node.name  # type: ignore[attr-defined]
        # test_login_valid_credentials → "login"
        parts = name.removeprefix("test_").split("_")
        group = parts[0] if parts else "misc"
        groups.setdefault(group, []).append(node)
    return groups


def _build_test_imports(
    page_module_map: dict[str, str],
    helper_names: list[str],
    fixture_funcs: list[ast.stmt],
    base_imports: str,
) -> str:
    """Build the import block for a test file referencing pages/helpers/conftest."""
    lines = [base_imports.strip()]

    # Import page objects
    for cls_name, module in page_module_map.items():
        lines.append(f"from {module} import {cls_name}")

    # Import helpers
    if helper_names:
        lines.append("from helpers.helpers import " + ", ".join(helper_names))

    # conftest fixtures are auto-discovered by pytest — no explicit import needed
    # but add a comment for clarity
    if fixture_funcs:
        lines.append("# Fixtures from conftest.py are auto-loaded by pytest")

    return "\n".join(lines)


def _slugify(name: str) -> str:
    """Convert a test case name to a safe filename slug."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return slug[:60] or "test"
