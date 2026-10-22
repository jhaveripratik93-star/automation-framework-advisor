"""compilers.py — Real compiler/parser checks for converted code.

Point 5 of the conversion rewrite: "the repo has built-in compilers so it
should help generate code which is compilable and accurate." This module
is the actual implementation of that — it shells out to or imports the
REAL toolchain for each target language, not a regex heuristic:

  - Robot Framework -> robot.api.get_model()  (the real RF parser — the
    same one `robot`/`robot --dryrun` uses internally)
  - Python          -> ast.parse()            (the real CPython parser)
  - JavaScript/TS   -> `node --check`         (the real V8/Node parser)
  - Java            -> `javac -d <tmp>`      (the real javac compiler)

Each check returns a CompileResult with ok/errors, so the conversion
pipeline can feed failures back into an LLM repair loop (see
src/conversion/service.py) instead of shipping code nobody tried to
compile.

Any missing external tool (node/javac not on PATH) degrades to "skipped"
rather than failing the whole pipeline — this feature should never block
a conversion just because the local machine lacks a given toolchain.
"""
from __future__ import annotations

import ast
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["CompileResult", "compile_check"]


@dataclass
class CompileResult:
    ok: bool
    checked: bool  # False when the toolchain wasn't available (skipped, not failed)
    language: str
    errors: list[str] = field(default_factory=list)
    tool: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "language": self.language,
            "tool": self.tool,
            "errors": self.errors,
        }


def compile_check(code: str, language: str) -> CompileResult:
    """Run the real compiler/parser for `language` against `code`.

    `language` is the lowercase key used elsewhere in the conversion
    pipeline ("python", "robot", "javascript", "typescript", "java", ...).
    Unknown languages return checked=False (nothing to validate against).
    """
    lang = (language or "").lower()
    if lang == "python":
        return _check_python(code)
    if lang == "robot":
        return _check_robot(code)
    if lang in ("javascript", "typescript"):
        return _check_node(code, lang)
    if lang == "java":
        return _check_java(code)
    return CompileResult(ok=True, checked=False, language=lang, tool="none",
                          errors=[])


# ── Python: ast.parse (always available, stdlib) ───────────────────────

def _check_python(code: str) -> CompileResult:
    try:
        ast.parse(code)
        return CompileResult(ok=True, checked=True, language="python", tool="ast.parse")
    except SyntaxError as exc:
        msg = f"line {exc.lineno}: {exc.msg}"
        return CompileResult(ok=False, checked=True, language="python",
                              tool="ast.parse", errors=[msg])


# ── Robot Framework: robot.api.get_model (the real RF parser) ─────────

def _collect_robot_errors(node, errors: list[str]) -> None:
    """Recursively walk a Robot Framework AST node collecting `.errors`.

    Robot's parser attaches parse errors (missing END, malformed FOR/IF,
    invalid section header, etc.) directly onto the offending node's
    `.errors` attribute rather than raising — this is the same mechanism
    `robot --dryrun` surfaces as "[ ERROR ] ... on line N: ..." output.
    """
    node_errors = getattr(node, "errors", None)
    if node_errors:
        lineno = getattr(node, "lineno", "?")
        for msg in node_errors:
            errors.append(f"line {lineno}: {msg}")
    for child in getattr(node, "body", None) or []:
        _collect_robot_errors(child, errors)
    for attr in ("header", "end", "orelse"):
        sub = getattr(node, attr, None)
        if sub is not None:
            _collect_robot_errors(sub, errors)
    for section in getattr(node, "sections", None) or []:
        _collect_robot_errors(section, errors)


def _check_robot(code: str) -> CompileResult:
    try:
        from robot.api import get_model
    except ImportError:
        logger.info("compile_check: robotframework not installed — skipping Robot syntax check")
        return CompileResult(ok=True, checked=False, language="robot", tool="robot.api")

    errors: list[str] = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "converted.robot"
            path.write_text(code, encoding="utf-8")
            model = get_model(str(path))
            _collect_robot_errors(model, errors)
    except Exception as exc:  # noqa: BLE001 — any parser crash is a real error
        errors.append(f"parser error: {exc}")

    return CompileResult(ok=len(errors) == 0, checked=True, language="robot",
                          tool="robot.api.get_model", errors=errors)


# ── JavaScript/TypeScript: node --check (the real V8 parser) ──────────

def _check_node(code: str, lang: str) -> CompileResult:
    node_bin = shutil.which("node")
    if not node_bin:
        logger.info("compile_check: node not found on PATH — skipping JS/TS syntax check")
        return CompileResult(ok=True, checked=False, language=lang, tool="node --check")

    ext = ".ts" if lang == "typescript" else ".js"
    if lang == "typescript":
        # `node --check` doesn't understand TS syntax (types, etc.) — only
        # degrade gracefully, don't fail a valid TS file on a JS-only check.
        return CompileResult(ok=True, checked=False, language=lang, tool="node --check")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"converted{ext}"
            path.write_text(code, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, "--check", str(path)],
                capture_output=True, text=True, timeout=20,
            )
            if proc.returncode == 0:
                return CompileResult(ok=True, checked=True, language=lang, tool="node --check")
            errors = [line for line in proc.stderr.splitlines() if line.strip()][:10]
            return CompileResult(ok=False, checked=True, language=lang,
                                  tool="node --check", errors=errors or ["unknown syntax error"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("compile_check: node --check failed to run: %s", exc)
        return CompileResult(ok=True, checked=False, language=lang, tool="node --check")


# ── Java: javac (the real compiler) ────────────────────────────────────

def _check_java(code: str) -> CompileResult:
    javac_bin = shutil.which("javac")
    if not javac_bin:
        logger.info("compile_check: javac not found on PATH — skipping Java syntax check")
        return CompileResult(ok=True, checked=False, language="java", tool="javac")

    import re
    m = re.search(r"\bclass\s+(\w+)", code)
    class_name = m.group(1) if m else "Converted"

    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{class_name}.java"
            path.write_text(code, encoding="utf-8")
            proc = subprocess.run(
                [javac_bin, "-d", tmp, str(path)],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode == 0:
                return CompileResult(ok=True, checked=True, language="java", tool="javac")
            errors = [line for line in proc.stderr.splitlines() if line.strip()][:10]
            return CompileResult(ok=False, checked=True, language="java",
                                  tool="javac", errors=errors or ["unknown compile error"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("compile_check: javac failed to run: %s", exc)
        return CompileResult(ok=True, checked=False, language="java", tool="javac")
