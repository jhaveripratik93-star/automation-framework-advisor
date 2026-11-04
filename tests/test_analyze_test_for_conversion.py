"""Tests for the analyze_test_for_conversion MCP tool (mcp_server/server.py).

Exercises the tool end-to-end against a SELF-CONTAINED synthetic Robot suite
built under pytest's ``tmp_path`` (see tests/conftest.py), so it runs everywhere
without any external fixture. Parser-backed tests use
``pytest.importorskip("robot")`` so they skip cleanly ONLY when robotframework
is genuinely not installed. This tool uses no LLM/GROQ.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server.server import analyze_test_for_conversion


def _robot_available() -> bool:
    try:
        import robot.api  # noqa: F401
    except ImportError:
        return False
    return True


_VALID_READINESS = {"self_contained", "needs_custom_libs", "blocked"}


def test_directory_no_filter_analyzes_multiple_test_cases(make_robot_suite):
    pytest.importorskip("robot")
    suite = make_robot_suite()
    result = analyze_test_for_conversion(
        path=str(suite.suite.parent), source_root=str(suite.source_root)
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


def test_single_file_with_matching_name_returns_only_that_case(make_robot_suite):
    pytest.importorskip("robot")
    suite = make_robot_suite()
    result = analyze_test_for_conversion(
        path=str(suite.suite),
        test_case_name="status",
        source_root=str(suite.source_root),
    )
    assert "error" not in result
    assert result["test_case_name"] == "status"
    assert result["matched_test_cases"] >= 1
    names = [tc["name"] for fd in result["files"] for tc in fd["test_cases"]]
    assert names, "expected at least one matching test case"
    for name in names:
        assert "status" in name.lower()
    for fd in result["files"]:
        for tc in fd["test_cases"]:
            assert tc["conversion_readiness"] in _VALID_READINESS


def test_non_existent_path_returns_error_dict_without_raising():
    result = analyze_test_for_conversion(path=r"C:\no\such\dir\nope.robot")
    assert isinstance(result, dict)
    assert "error" in result
