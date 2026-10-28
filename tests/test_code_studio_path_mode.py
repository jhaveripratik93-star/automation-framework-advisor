"""Tests for the path-mode pure helpers on the Framework Migration page.

Exercises the Streamlit-free logic factored out of _pages/code_studio.py:
  - _analyze_path_for_studio (analyze_path + filter_result_by_test_case)
  - _convert_matched_files   (per-file disk read + convert loop)

with a scripted stub llm_call — the same (system_prompt, user_prompt) -> str
contract production's llm_call uses — so the analysis chaining, filtering, the
per-file read/convert loop, per-file error isolation, and result assembly are
covered WITHOUT a running Streamlit server or a live LLM backend. Parser-backed
tests skip (early return) when robotframework or the on-disk fixture is absent,
matching the degrade-don't-crash contract of the other analyzer tests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from _pages.code_studio import _analyze_path_for_studio, _convert_matched_files


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
_COMBINED_ROBOT = _ANALYTICS_DIR / "combined.robot"

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


def test_analyze_single_file_returns_populated_dict():
    if not _robot_available() or not _COMBINED_ROBOT.is_file():
        return
    result = _analyze_path_for_studio(str(_COMBINED_ROBOT), "", str(_AUTOTEST_ROOT))
    assert "error" not in result
    assert result["total_test_cases"] >= 1
    assert result["files"], "expected at least one file report"


def test_analyze_with_matching_name_narrows_results():
    if not _robot_available() or not _COMBINED_ROBOT.is_file():
        return
    full = _analyze_path_for_studio(str(_COMBINED_ROBOT), "", str(_AUTOTEST_ROOT))
    assert "error" not in full and full["total_test_cases"] >= 1
    # Pick a real test-case name and filter on a substring of it.
    first_name = full["files"][0]["test_cases"][0]["name"]
    needle = first_name.split()[0] if first_name.split() else first_name
    filtered = _analyze_path_for_studio(str(_COMBINED_ROBOT), needle, str(_AUTOTEST_ROOT))
    assert "error" not in filtered
    assert 1 <= filtered["total_test_cases"] <= full["total_test_cases"]


def test_analyze_name_matching_nothing_yields_zero():
    if not _robot_available() or not _COMBINED_ROBOT.is_file():
        return
    result = _analyze_path_for_studio(
        str(_COMBINED_ROBOT), "this-name-matches-no-test-case-xyz", str(_AUTOTEST_ROOT)
    )
    assert "error" not in result
    assert result["total_test_cases"] == 0


def test_analyze_bad_path_returns_error():
    if not _robot_available():
        return
    result = _analyze_path_for_studio(r"C:\no\such\dir\nope.robot", "", "")
    assert isinstance(result, dict)
    assert "error" in result


def test_convert_matched_files_directory_converts_each_file():
    if not _robot_available() or not _ANALYTICS_DIR.is_dir():
        return
    analysis = _analyze_path_for_studio(str(_ANALYTICS_DIR), "", str(_AUTOTEST_ROOT))
    assert "error" not in analysis
    n_files = len([f for f in analysis["files"] if not f.get("error")])
    assert n_files > 1, "directory should yield multiple file reports"

    llm = _ScriptedLLM()
    results = _convert_matched_files(
        analysis, "Robot Framework", "Playwright", llm, to_fw_data=None
    )
    assert len(results) == n_files
    for fr in results:
        assert "result" in fr, f"unexpected error entry: {fr.get('error')}"
        assert fr["result"].code.strip(), "expected non-empty converted code"
    assert llm.call_count >= n_files


def test_convert_matched_files_per_file_error_isolation(monkeypatch):
    """One file whose read raises yields a {"file_path", "error"} entry while
    the others still convert."""
    fake_analysis = {
        "files": [
            {"file_path": r"C:\fixtures\a.robot", "test_cases": []},
            {"file_path": r"C:\fixtures\b.robot", "test_cases": []},
            {"file_path": r"C:\fixtures\c.robot", "test_cases": []},
        ],
    }

    def fake_read_text(self, *args, **kwargs):
        if self.name == "b.robot":
            raise OSError("simulated read failure")
        return "*** Test Cases ***\nT1\n    Log    hi\n"

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    # Convert must not touch the LLM for the failing file; stub a convert that
    # just echoes a canned result so the test needs no live pipeline.
    import _pages.code_studio as mod

    class _FakeResult:
        code = VALID_PLAYWRIGHT_OUTPUT

    def fake_convert(request, to_fw_data=None):
        assert request.source_code.strip()
        return _FakeResult()

    import src.conversion.service as svc
    monkeypatch.setattr(svc, "convert", fake_convert)

    llm = _ScriptedLLM()
    results = mod._convert_matched_files(
        fake_analysis, "Robot Framework", "Playwright", llm, to_fw_data=None
    )

    assert len(results) == 3
    errored = [f for f in results if "error" in f]
    ok = [f for f in results if "result" in f]
    assert len(errored) == 1
    assert errored[0]["file_path"].endswith("b.robot")
    assert len(ok) == 2


def test_convert_matched_files_skips_error_and_duplicate_entries(monkeypatch):
    """File reports carrying a parse error, or a duplicate file_path, are not
    read or converted twice."""
    fake_analysis = {
        "files": [
            {"file_path": r"C:\fixtures\dup.robot", "test_cases": []},
            {"file_path": r"C:\fixtures\dup.robot", "test_cases": []},  # duplicate
            {"file_path": r"C:\fixtures\bad.robot", "error": "parse error"},
        ],
    }

    monkeypatch.setattr(
        Path, "read_text", lambda self, *a, **k: "*** Test Cases ***\nT1\n    Log    hi\n"
    )

    class _FakeResult:
        code = VALID_PLAYWRIGHT_OUTPUT

    import src.conversion.service as svc
    monkeypatch.setattr(svc, "convert", lambda request, to_fw_data=None: _FakeResult())

    import _pages.code_studio as mod
    llm = _ScriptedLLM()
    results = mod._convert_matched_files(
        fake_analysis, "Robot Framework", "Playwright", llm, to_fw_data=None
    )
    assert len(results) == 1
    assert results[0]["file_path"].endswith("dup.robot")
