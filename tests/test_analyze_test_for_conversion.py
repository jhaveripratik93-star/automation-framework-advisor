"""Tests for the analyze_test_for_conversion MCP tool (mcp_server/server.py).

Exercises the tool end-to-end against the sibling autotest fixture tree. Each
parser-backed test skips (early return) when robotframework is not installed or
the on-disk fixture is missing, matching the degrade-don't-crash contract used
by tests/test_robot_dependency_analyzer.py. This tool uses no LLM/GROQ.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server.server import analyze_test_for_conversion


def _robot_available() -> bool:
    try:
        import robot.api  # noqa: F401
    except ImportError:
        return False
    return True


_AUTOTEST_ROOT = (
    Path(__file__).parent.parent.parent
    / "autotest_2_extracted" / "autotest"
)
_ANALYTICS_DIR = _AUTOTEST_ROOT / "core-tests" / "component" / "analytics"
_INGEST_ROBOT = _ANALYTICS_DIR / "ingest.robot"

_VALID_READINESS = {"self_contained", "needs_custom_libs", "blocked"}


def test_directory_no_filter_analyzes_multiple_test_cases():
    if not _robot_available() or not _ANALYTICS_DIR.is_dir():
        return
    result = analyze_test_for_conversion(
        path=str(_ANALYTICS_DIR), source_root=str(_AUTOTEST_ROOT)
    )
    assert "error" not in result
    assert result["total_files"] >= 1
    assert result["total_test_cases"] > 1
    # No name filter -> matched equals total.
    assert result["matched_test_cases"] == result["total_test_cases"]
    assert result.get("report")
    for fd in result["files"]:
        for tc in fd["test_cases"]:
            assert tc["conversion_readiness"] in _VALID_READINESS
            assert tc["rationale"]


def test_single_file_with_matching_name_returns_only_that_case():
    if not _robot_available() or not _INGEST_ROBOT.is_file():
        return
    result = analyze_test_for_conversion(
        path=str(_INGEST_ROBOT),
        test_case_name="typecasting",
        source_root=str(_AUTOTEST_ROOT),
    )
    assert "error" not in result
    assert result["test_case_name"] == "typecasting"
    assert result["matched_test_cases"] >= 1
    names = [tc["name"] for fd in result["files"] for tc in fd["test_cases"]]
    assert names, "expected at least one matching test case"
    for name in names:
        assert "typecasting" in name.lower()
    for fd in result["files"]:
        for tc in fd["test_cases"]:
            assert tc["conversion_readiness"] in _VALID_READINESS


def test_non_existent_path_returns_error_dict_without_raising():
    result = analyze_test_for_conversion(path=r"C:\no\such\dir\nope.robot")
    assert isinstance(result, dict)
    assert "error" in result
