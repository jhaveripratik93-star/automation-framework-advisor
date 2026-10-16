"""Tests for src/conversion/compilers.py — real compiler/parser checks."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.conversion.compilers import compile_check


# ── Python (ast.parse — always available) ──────────────────────────────

def test_python_valid_code_passes():
    result = compile_check("def test_x():\n    assert 1 == 1\n", "python")
    assert result.checked is True
    assert result.ok is True
    assert result.errors == []


def test_python_invalid_code_fails():
    result = compile_check("def test_x(:\n    pass\n", "python")
    assert result.checked is True
    assert result.ok is False
    assert result.errors


# ── Robot Framework (robot.api.get_model) ───────────────────────────────

def test_robot_valid_code_passes():
    code = (
        "*** Test Cases ***\n"
        "Verify Thing\n"
        "    Log    hello\n"
    )
    result = compile_check(code, "robot")
    if not result.checked:
        return  # robotframework not installed in this environment — skip assertions
    assert result.ok is True
    assert result.errors == []


def test_robot_missing_end_fails():
    code = (
        "*** Test Cases ***\n"
        "Verify Thing\n"
        "    IF    True\n"
        "        Log    hello\n"
    )
    result = compile_check(code, "robot")
    if not result.checked:
        return
    assert result.ok is False
    assert any("END" in e for e in result.errors)


def test_robot_orphan_keyword_is_not_a_syntax_error():
    """compile_check is purely syntactic — the 'no runnable test' semantic
    rule lives in the conversion validator, not here."""
    code = (
        "*** Keywords ***\n"
        "Do Something\n"
        "    Log    hi\n"
    )
    result = compile_check(code, "robot")
    if not result.checked:
        return
    assert result.ok is True


# ── JavaScript (node --check) ───────────────────────────────────────────

def test_js_valid_code_passes():
    result = compile_check("console.log('hi');\n", "javascript")
    if not result.checked:
        return  # node not on PATH in this environment — skip assertions
    assert result.ok is True


def test_js_invalid_code_fails():
    result = compile_check("function broken( {\n", "javascript")
    if not result.checked:
        return
    assert result.ok is False
    assert result.errors


# ── TypeScript degrades gracefully (node --check can't parse TS types) ─

def test_typescript_always_skipped():
    result = compile_check("const x: number = 1;\n", "typescript")
    assert result.checked is False
    assert result.ok is True


# ── Unknown language ────────────────────────────────────────────────────

def test_unknown_language_skips():
    result = compile_check("whatever", "cobol")
    assert result.checked is False
    assert result.ok is True
