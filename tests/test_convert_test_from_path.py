"""Tests for the convert_test_from_path end-to-end MCP tool (mcp_server/server.py).

Exercises the synchronous core (_convert_from_path_core) directly with a
scripted stub llm_call — the same (system_prompt, user_prompt) -> str contract
production's llm_call uses — so the analysis chaining, per-file disk read,
per-file conversion loop, filtering, error passthrough, and return/report shape
are all covered WITHOUT a live LLM backend (no GROQ_API_KEY, no MCP ctx in a
plain pytest run). Parser-backed tests skip (early return) when robotframework
is missing or the on-disk fixture is absent, matching the degrade-don't-crash
contract of the other analyzer tests.

A guarded live-GROQ smoke test (skipped when no backend is configured) exercises
the async tool end-to-end where a real backend exists.

Parser-backed tests run against a SELF-CONTAINED synthetic Robot suite built
under pytest's ``tmp_path`` (see tests/conftest.py) and use
``pytest.importorskip("robot")`` so they skip cleanly ONLY when robotframework
is genuinely not installed.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server.server import _convert_from_path_core


def _robot_available() -> bool:
    try:
        import robot.api  # noqa: F401
    except ImportError:
        return False
    return True


# A valid Playwright-py snippet the stub returns for every conversion call.
VALID_PLAYWRIGHT_OUTPUT = """from playwright.sync_api import sync_playwright


def test_converted():
    assert True
"""


class _ScriptedLLM:
    """Returns the same canned valid snippet for every call; counts calls so
    tests can assert the LLM was (or was not) invoked."""

    def __init__(self, output: str = VALID_PLAYWRIGHT_OUTPUT):
        self._output = output
        self.call_count = 0

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        return self._output


def test_single_file_with_matching_name_converts_that_file(make_robot_suite):
    pytest.importorskip("robot")
    suite = make_robot_suite()
    llm = _ScriptedLLM()
    result = _convert_from_path_core(
        str(suite.suite),
        to_framework="Playwright",
        test_case_name="",  # no filter -> all test cases in the single file
        source_root=str(suite.source_root),
        llm_call=llm,
        llm_mode="stub",
    )
    assert "error" not in result
    assert result["to_framework"] == "Playwright"
    assert result["from_framework"] == "Robot Framework"
    assert result["llm_mode"] == "stub"
    assert result["analysis"]["matched_test_cases"] >= 1
    assert result["files"], "expected at least one converted file"
    for fr in result["files"]:
        assert "error" not in fr
        assert fr["code"].strip(), "expected non-empty converted code"
        assert "is_valid" in fr
    assert llm.call_count >= 1


def test_directory_converts_multiple_files(make_robot_suite):
    pytest.importorskip("robot")
    suite = make_robot_suite()
    llm = _ScriptedLLM()
    result = _convert_from_path_core(
        str(suite.suite.parent),
        to_framework="Playwright",
        source_root=str(suite.source_root),
        llm_call=llm,
        llm_mode="stub",
    )
    assert "error" not in result
    assert len(result["files"]) > 1, "directory should yield multiple file entries"
    assert result["llm_mode"] == "stub"
    for fr in result["files"]:
        if "error" in fr:
            continue
        assert "code" in fr
        assert "is_valid" in fr


def test_name_filter_matching_nothing_returns_empty_without_converting(make_robot_suite):
    pytest.importorskip("robot")
    suite = make_robot_suite()
    llm = _ScriptedLLM()
    result = _convert_from_path_core(
        str(suite.suite),
        to_framework="Playwright",
        test_case_name="this-name-matches-no-test-case-xyz",
        source_root=str(suite.source_root),
        llm_call=llm,
        llm_mode="stub",
    )
    assert "error" not in result
    assert result["files"] == []
    assert "nothing to convert" in result["report"].lower()
    assert llm.call_count == 0, "conversion must not run when nothing matched"


def test_bad_path_returns_error_without_converting():
    llm = _ScriptedLLM()
    result = _convert_from_path_core(
        r"C:\no\such\dir\nope.robot",
        to_framework="Playwright",
        llm_call=llm,
        llm_mode="stub",
    )
    assert isinstance(result, dict)
    assert "error" in result
    assert llm.call_count == 0


def test_result_is_json_serializable(make_robot_suite):
    pytest.importorskip("robot")
    suite = make_robot_suite()
    llm = _ScriptedLLM()
    result = _convert_from_path_core(
        str(suite.suite),
        to_framework="Playwright",
        source_root=str(suite.source_root),
        llm_call=llm,
        llm_mode="stub",
    )
    json.dumps(result)  # must not raise


def test_per_file_error_does_not_abort_run(tmp_path, monkeypatch):
    """One file whose read/convert raises yields a {"file_path", "error"}
    entry while the others still convert."""
    pytest.importorskip("robot")
    import mcp_server.server as server_mod

    base = tmp_path / "suite_dir"
    base.mkdir()
    # Build an analysis stub with three matched files so the loop runs 3x.
    fake_analysis = {
        "input_path": str(base),
        "total_files": 3,
        "matched_test_cases": 3,
        "readiness_counts": {"self_contained": 3, "needs_custom_libs": 0, "blocked": 0},
        "files": [
            {"file_path": str(base / "a.robot"), "test_cases": []},
            {"file_path": str(base / "b.robot"), "test_cases": []},
            {"file_path": str(base / "c.robot"), "test_cases": []},
        ],
        "report": "stub",
    }
    monkeypatch.setattr(
        server_mod, "_analyze_for_conversion",
        lambda *a, **k: fake_analysis,
    )
    # Make the second file's read fail; others succeed with canned text.
    real_read_text = Path.read_text

    def fake_read_text(self, *args, **kwargs):
        if self.name == "b.robot":
            raise OSError("simulated read failure")
        return "*** Test Cases ***\nT1\n    Log    hi\n"

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    llm = _ScriptedLLM()
    result = _convert_from_path_core(
        str(base),
        to_framework="Playwright",
        llm_call=llm,
        llm_mode="stub",
    )
    monkeypatch.setattr(Path, "read_text", real_read_text)

    assert len(result["files"]) == 3
    errored = [f for f in result["files"] if "error" in f]
    ok = [f for f in result["files"] if "error" not in f]
    assert len(errored) == 1
    assert errored[0]["file_path"].endswith("b.robot")
    assert len(ok) == 2


def _groq_available() -> bool:
    try:
        from mcp_server import services as svc
        return svc.groq_available()
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _groq_available(), reason="no GROQ_API_KEY / LLM backend configured")
def test_live_conversion_smoke_via_async_tool(make_robot_suite):
    """End-to-end through the real async tool against a live backend — skipped
    in a keyless environment. Does NOT fabricate an LLM."""
    import asyncio

    from mcp_server.server import convert_test_from_path

    pytest.importorskip("robot")
    suite = make_robot_suite()

    result = asyncio.run(convert_test_from_path(
        path=str(suite.suite),
        to_framework="Playwright",
        source_root=str(suite.source_root),
        ctx=None,
    ))
    assert "error" not in result
    assert result["files"]
    assert any(fr.get("code", "").strip() for fr in result["files"])
