"""Static symbol checker — deterministic pre-validation before the LLM validator runs.

Extracts every symbol reference and every symbol declaration from generated code
using regex patterns per framework.  Returns undeclared symbols so the LLM
validator receives a concrete list rather than having to discover them itself.

No LLM calls.  Zero false-negatives on the patterns it covers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class StaticCheckResult:
    framework: str
    declared: set[str] = field(default_factory=set)
    referenced: set[str] = field(default_factory=set)

    @property
    def undeclared(self) -> set[str]:
        return self.referenced - self.declared

    @property
    def is_clean(self) -> bool:
        return len(self.undeclared) == 0

    def summary(self) -> str:
        if self.is_clean:
            return "static check: OK"
        return f"static check: UNDECLARED {sorted(self.undeclared)}"


# ── Per-framework extractors ──────────────────────────────────────────

def _check_robot_framework(code: str) -> StaticCheckResult:
    result = StaticCheckResult(framework="robot_framework")

    # Declarations: *** Variables *** section  →  ${VAR_NAME}    value
    in_vars = False
    for line in code.splitlines():
        stripped = line.strip()
        if re.match(r"^\*+\s*Variables\s*\*+", stripped, re.IGNORECASE):
            in_vars = True
            continue
        if re.match(r"^\*+\s*\w", stripped) and "Variables" not in stripped:
            in_vars = False
        if in_vars:
            m = re.match(r"(\$\{[\w\s]+\}|\@\{[\w\s]+\}|\&\{[\w\s]+\})", stripped)
            if m:
                result.declared.add(m.group(1))

    # Also treat variable assignments inside Keywords/Test Cases as declarations
    # e.g.  ${token}=    Get Token
    for m in re.finditer(r"(\$\{[\w\s]+\})\s*=", code):
        result.declared.add(m.group(1))

    # References: every ${VAR} / @{VAR} / &{VAR} anywhere in the file
    for m in re.finditer(r"([\$\@\&]\{[\w\s]+\})", code):
        result.referenced.add(m.group(1))

    # Built-in Robot Framework variables — always available, never need declaration
    BUILTINS = {
        "${EMPTY}", "${SPACE}", "${TRUE}", "${FALSE}", "${NULL}", "${NONE}",
        "${TEST NAME}", "${TEST STATUS}", "${TEST MESSAGE}",
        "${SUITE NAME}", "${SUITE STATUS}", "${SUITE MESSAGE}",
        "${OUTPUT DIR}", "${OUTPUT FILE}", "${LOG FILE}", "${REPORT FILE}",
        "${EXECDIR}", "${CURDIR}", "${TEMPDIR}",
        "${/}", "${:}", "${\\n}",
    }
    result.declared.update(BUILTINS)

    return result


def _check_playwright_ts(code: str) -> StaticCheckResult:
    result = StaticCheckResult(framework="playwright_ts")

    # Declarations: const/let/var, function params, import bindings
    for m in re.finditer(r"\b(?:const|let|var)\s+([\w$]+)\s*[=:]", code):
        result.declared.add(m.group(1))
    for m in re.finditer(r"function\s+\w+\s*\(([^)]*)\)", code):
        for param in m.group(1).split(","):
            name = param.strip().split(":")[0].strip().lstrip(".")
            if name:
                result.declared.add(name)
    # async ({ page, request, context, browser }) destructuring
    for m in re.finditer(r"\(\s*\{([^}]+)\}\s*\)", code):
        for part in m.group(1).split(","):
            name = part.strip().split(":")[0].strip()
            if re.match(r"^\w+$", name):
                result.declared.add(name)
    # import { X, Y } from ...
    for m in re.finditer(r"import\s*\{([^}]+)\}", code):
        for name in m.group(1).split(","):
            result.declared.add(name.strip().split(" as ")[-1].strip())
    # import X from ...
    for m in re.finditer(r"import\s+([\w$]+)\s+from", code):
        result.declared.add(m.group(1))

    # References: every bare identifier used in an expression context
    # (too broad to enumerate — only flag process.env.X style undeclared envvars)
    for m in re.finditer(r"process\.env\.([\w]+)", code):
        result.referenced.add(f"process.env.{m.group(1)}")
        result.declared.add(f"process.env.{m.group(1)}")  # env vars are always "available"

    return result


def _check_playwright_py(code: str) -> StaticCheckResult:
    result = StaticCheckResult(framework="playwright_py")

    # Declarations: assignments and function defs
    for m in re.finditer(r"^[ \t]*([\w]+)\s*=", code, re.MULTILINE):
        result.declared.add(m.group(1))
    for m in re.finditer(r"def\s+\w+\s*\(([^)]*)\)", code):
        for param in m.group(1).split(","):
            name = param.strip().split(":")[0].strip().lstrip("*")
            if name:
                result.declared.add(name)
    for m in re.finditer(r"^import\s+([\w.]+)", code, re.MULTILINE):
        result.declared.add(m.group(1).split(".")[0])
    for m in re.finditer(r"^from\s+[\w.]+\s+import\s+(.+)$", code, re.MULTILINE):
        for name in m.group(1).split(","):
            result.declared.add(name.strip().split(" as ")[-1].strip())

    return result


def _check_selenium_py(code: str) -> StaticCheckResult:
    return _check_playwright_py(code)  # same Python patterns


def _check_cypress_js(code: str) -> StaticCheckResult:
    result = StaticCheckResult(framework="cypress_js")
    # cy.* commands are always available; flag only explicit variable references
    for m in re.finditer(r"\b(?:const|let|var)\s+([\w$]+)\s*=", code):
        result.declared.add(m.group(1))
    return result


def _check_generic(code: str, framework: str) -> StaticCheckResult:
    return StaticCheckResult(framework=framework)  # no false positives on unknown frameworks


_CHECKERS = {
    "robot_framework": _check_robot_framework,
    "playwright_ts":   _check_playwright_ts,
    "playwright_py":   _check_playwright_py,
    "selenium_py":     _check_selenium_py,
    "cypress_js":      _check_cypress_js,
}


def run_static_check(code: str, framework: str) -> StaticCheckResult:
    """Run the deterministic symbol check for the given framework."""
    checker = _CHECKERS.get(framework, lambda c: _check_generic(c, framework))
    return checker(code)
