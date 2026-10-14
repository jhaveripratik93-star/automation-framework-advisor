"""conversion_validator.py — Deterministic post-conversion structural checks.

Catches a specific, previously-silent failure mode seen in real output: a
converted file that contains only keyword/function *definitions* and never
actually invokes them in a runnable test entry — e.g. a Robot Framework file
with a ``*** Keywords ***`` section but no ``*** Test Cases ***`` section (or
a `*** Test Cases ***` section with an empty/comment-only body), while the
source file clearly performed assertions (k6 `check()`, `expect()`, etc.).

This module does NOT call an LLM and does NOT evaluate whether the converted
logic is *correct* — only whether something runnable exists. It is used by
src/conversion/service.py as one of the two gates (alongside the real
compiler/parser check in src/conversion/compilers.py) in the conversion
repair loop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.tools.assertion_counter import count as _count_assertions

__all__ = ["ConversionValidation", "validate_single_file_conversion"]


@dataclass
class ConversionValidation:
    is_valid: bool
    has_runnable_test: bool
    source_has_assertions: bool
    source_assertion_count: int
    orphan_keywords: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "has_runnable_test": self.has_runnable_test,
            "source_has_assertions": self.source_has_assertions,
            "source_assertion_count": self.source_assertion_count,
            "orphan_keywords": self.orphan_keywords,
            "errors": self.errors,
            "warnings": self.warnings,
        }

    def render(self) -> str:
        """Render a short markdown block for inclusion in a tool response."""
        if self.is_valid and not self.warnings:
            return ""
        lines: list[str] = []
        if self.errors:
            lines.append("### ❌ Conversion validation failed")
            for e in self.errors:
                lines.append(f"- {e}")
        elif self.warnings:
            lines.append("### ⚠️ Conversion validation warnings")
        for w in self.warnings:
            lines.append(f"- {w}")
        return "\n".join(lines)


# ── Robot Framework structural parsing ─────────────────────────────────

_ROBOT_SECTION_ANY = re.compile(r"^\*{3}\s*([^*]+?)\s*\*{3}", re.MULTILINE)


def _split_robot_sections(code: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    matches = list(_ROBOT_SECTION_ANY.finditer(code))
    for i, m in enumerate(matches):
        name = m.group(1).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(code)
        sections[name] = code[start:end]
    return sections


def _robot_case_bodies(block: str) -> dict[str, list[str]]:
    """Parse a *** Test Cases ***/*** Tasks *** body into {name: [step_lines]}."""
    cases: dict[str, list[str]] = {}
    name: str | None = None
    body: list[str] = []
    for line in block.splitlines():
        if not line.strip():
            if name is not None:
                body.append(line)
            continue
        if line[:1].isspace():
            if name is not None:
                body.append(line)
        else:
            if name is not None:
                cases[name] = body
            name = line.strip()
            body = []
    if name is not None:
        cases[name] = body
    return cases


def _has_runnable_robot_test(code: str) -> tuple[bool, list[str]]:
    """Return (has_runnable_test, orphan_keyword_names).

    has_runnable_test: True if at least one *** Test Cases ***/*** Tasks ***
    entry has a non-blank, non-comment step.
    orphan_keywords: keyword names defined under *** Keywords *** that are
    never referenced (by name) anywhere in the test-case/task bodies — i.e.
    defined but nothing in this file ever calls them.
    """
    sections = _split_robot_sections(code)
    tc_block = sections.get("test cases", "") + "\n" + sections.get("tasks", "")
    cases = _robot_case_bodies(tc_block)

    has_runnable = any(
        any(line.strip() and not line.strip().startswith("#") for line in body)
        for body in cases.values()
    )

    kw_block = sections.get("keywords", "")
    kw_names: list[str] = []
    for line in kw_block.splitlines():
        stripped = line.strip()
        if line[:1].isspace() or not stripped or stripped.startswith("#"):
            continue
        kw_names.append(stripped)

    tc_text_lower = tc_block.lower()
    orphans = [kw for kw in kw_names if kw.lower() not in tc_text_lower]
    return has_runnable, orphans


# ── Other target languages — lighter-weight structural checks ─────────

_PY_TEST_FUNC = re.compile(r"^\s*(?:async\s+)?def\s+test_\w+", re.MULTILINE)
_JS_TEST_CALL = re.compile(r"\b(?:test|it)\s*\(\s*['\"]", re.IGNORECASE)
_JAVA_TEST_ANNOTATION = re.compile(r"@Test\b")


def _has_runnable_python_test(code: str) -> bool:
    return bool(_PY_TEST_FUNC.search(code))


def _has_runnable_js_test(code: str) -> bool:
    return bool(_JS_TEST_CALL.search(code))


def _has_runnable_java_test(code: str) -> bool:
    return bool(_JAVA_TEST_ANNOTATION.search(code))


# ── Public entry point ─────────────────────────────────────────────────

def validate_single_file_conversion(
    source_code: str,
    converted_code: str,
    from_framework: str,
    to_framework: str,
    target_lang: str = "python",
) -> ConversionValidation:
    """Check that a single-file conversion produced a self-contained,
    runnable test — not just keyword/function definitions with nothing
    calling them.

    This is purely structural (no LLM calls, no semantic evaluation of
    correctness). It flags the exact failure mode seen in practice: source
    code with real assertions converted into a target file that only
    *defines* keywords/functions and never invokes any of them.
    """
    source_assertions = _count_assertions(source_code, from_framework)
    source_has_assertions = source_assertions > 0

    lang = (target_lang or "").lower()
    orphan_keywords: list[str] = []

    if "robot" in to_framework.lower() or lang == "robot":
        has_runnable, orphan_keywords = _has_runnable_robot_test(converted_code)
    elif lang == "python":
        has_runnable = _has_runnable_python_test(converted_code)
    elif lang in ("javascript", "typescript"):
        has_runnable = _has_runnable_js_test(converted_code)
    elif lang == "java":
        has_runnable = _has_runnable_java_test(converted_code)
    else:
        # Unknown target language — can't structurally verify; don't block.
        has_runnable = True

    errors: list[str] = []
    warnings: list[str] = []

    if not has_runnable and source_has_assertions:
        errors.append(
            f"Converted {to_framework} code has no runnable test entry (e.g. a "
            f"'*** Test Cases ***' case with steps, a `def test_*`, or a "
            f"`test()`/`it()` call) even though the source has "
            f"{source_assertions} assertion-like check(s). The conversion "
            f"produced definitions only — nothing in this file actually calls "
            f"them. Add a test case/task that invokes the converted "
            f"keyword(s)/function(s), or confirm this file is intentionally a "
            f"shared library consumed elsewhere."
        )
    elif not has_runnable:
        warnings.append(
            f"Converted {to_framework} code has no runnable test entry. Source "
            f"had no detected assertions either, so this may be intentional "
            f"(a pure helper/library file) — verify manually."
        )

    if orphan_keywords:
        warnings.append(
            "Keyword(s) defined but never called within this file: "
            + ", ".join(orphan_keywords)
            + ". If this file should be self-contained, add a test case that "
            "calls them; if it's a shared library used by other suites, this "
            "is expected."
        )

    is_valid = len(errors) == 0

    return ConversionValidation(
        is_valid=is_valid,
        has_runnable_test=has_runnable,
        source_has_assertions=source_has_assertions,
        source_assertion_count=source_assertions,
        orphan_keywords=orphan_keywords,
        errors=errors,
        warnings=warnings,
    )
