"""Tests for the project-ZIP pure helpers on the Framework Migration page.

Exercises the Streamlit-free logic added to _pages/code_studio.py:
  - _safe_test_filename  (safe, de-duplicated test filenames)
  - _build_project_zip   (in-memory runnable project bundle)

plus a multi-file sanity check that analyze_path on the real component/analytics
directory yields >1 file and _convert_matched_files with a scripted stub
llm_call returns one result per file. No running Streamlit server or live LLM
backend is needed. Parser-backed tests skip (early return) when robotframework
or the on-disk fixture is absent, matching the other analyzer tests.
"""
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from _pages.code_studio import (
    _analyze_path_for_studio,
    _build_project_zip,
    _convert_matched_files,
    _get_run_instructions,
    _safe_test_filename,
)


def _robot_available() -> bool:
    try:
        import robot.api  # noqa: F401
    except ImportError:
        return False
    return True


_AUTOTEST_ROOT = Path(__file__).parent.parent.parent / "autotest_2_extracted" / "autotest"
_ANALYTICS_DIR = _AUTOTEST_ROOT / "core-tests" / "component" / "analytics"

VALID_PLAYWRIGHT_OUTPUT = """from playwright.sync_api import sync_playwright


def test_converted():
    assert True
"""


class _ScriptedLLM:
    def __init__(self, output: str = VALID_PLAYWRIGHT_OUTPUT):
        self._output = output
        self.call_count = 0

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        return self._output


class _FakeResult:
    """Minimal ConversionResult stand-in exposing only what _build_project_zip
    touches: .code and .target_language."""

    def __init__(self, code: str, target_language: str = "python"):
        self.code = code
        self.target_language = target_language


# --- _safe_test_filename ---------------------------------------------------


def test_safe_test_filename_derives_from_stem():
    used: set[str] = set()
    name = _safe_test_filename(r"C:\x\nifi.robot", ".py", used)
    assert name == "test_nifi.py"
    assert name in used


def test_safe_test_filename_sanitizes_unsafe_chars():
    used: set[str] = set()
    name = _safe_test_filename("weird name (v2).robot", ".py", used)
    # spaces and parens become underscores; extension preserved
    assert name.startswith("test_")
    assert name.endswith(".py")
    assert all(c.isalnum() or c in "_-." for c in name)


def test_safe_test_filename_dedupes_on_collision():
    used: set[str] = set()
    a = _safe_test_filename(r"C:\dir_a\combined.robot", ".py", used)
    b = _safe_test_filename(r"C:\dir_b\combined.robot", ".py", used)
    assert a == "test_combined.py"
    assert b == "test_combined_1.py"
    assert a != b


# --- _build_project_zip ----------------------------------------------------


def _zip_names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.namelist()


def _zip_read(data: bytes, name: str) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read(name).decode("utf-8")


def test_build_project_zip_single_file_layout():
    file_results = [
        {"file_path": r"C:\x\nifi.robot", "result": _FakeResult(VALID_PLAYWRIGHT_OUTPUT)},
    ]
    data = _build_project_zip(file_results, "Playwright")
    names = _zip_names(data)

    assert "converted_project/requirements.txt" in names
    assert "converted_project/README.md" in names
    test_files = [n for n in names if n.startswith("converted_project/tests/")]
    assert len(test_files) == 1
    assert test_files[0] == "converted_project/tests/test_nifi.py"

    # tests file content equals the converted code
    assert _zip_read(data, test_files[0]) == VALID_PLAYWRIGHT_OUTPUT

    # requirements.txt equals _get_run_instructions("playwright")[0]
    assert _zip_read(data, "converted_project/requirements.txt") == _get_run_instructions("playwright")[0]
    assert _zip_read(data, "converted_project/requirements.txt").strip()


def test_build_project_zip_multiple_files_distinct_entries():
    file_results = [
        {"file_path": r"C:\a\alarms.robot", "result": _FakeResult(VALID_PLAYWRIGHT_OUTPUT)},
        {"file_path": r"C:\b\combined.robot", "result": _FakeResult(VALID_PLAYWRIGHT_OUTPUT)},
        {"file_path": r"C:\c\ingest.robot", "result": _FakeResult(VALID_PLAYWRIGHT_OUTPUT)},
    ]
    data = _build_project_zip(file_results, "Playwright")
    names = _zip_names(data)
    test_files = sorted(n for n in names if n.startswith("converted_project/tests/"))
    assert len(test_files) == 3
    assert len(set(test_files)) == 3  # no name collisions
    # all under the shared single project root
    assert all(n.startswith("converted_project/") for n in names)


def test_build_project_zip_skips_errored_entries():
    file_results = [
        {"file_path": r"C:\a\ok.robot", "result": _FakeResult(VALID_PLAYWRIGHT_OUTPUT)},
        {"file_path": r"C:\b\bad.robot", "error": "boom"},
    ]
    data = _build_project_zip(file_results, "Playwright")
    names = _zip_names(data)
    test_files = [n for n in names if n.startswith("converted_project/tests/")]
    assert len(test_files) == 1
    # still a valid zip with requirements + readme
    assert "converted_project/requirements.txt" in names
    assert "converted_project/README.md" in names


def test_build_project_zip_no_results_still_valid_zip():
    file_results = [{"file_path": r"C:\b\bad.robot", "error": "boom"}]
    data = _build_project_zip(file_results, "Playwright")
    names = _zip_names(data)
    assert "converted_project/requirements.txt" in names
    assert "converted_project/README.md" in names
    assert not [n for n in names if n.startswith("converted_project/tests/")]


def test_build_project_zip_no_ci_cd_content():
    file_results = [
        {"file_path": r"C:\x\nifi.robot", "result": _FakeResult(VALID_PLAYWRIGHT_OUTPUT)},
    ]
    data = _build_project_zip(file_results, "Playwright")
    forbidden = ["github", "ci", "workflow", "actions", ".github", "ci/cd"]
    for fname in ("converted_project/requirements.txt", "converted_project/README.md"):
        text = _zip_read(data, fname).lower()
        for token in forbidden:
            assert token not in text, f"{token!r} unexpectedly present in {fname}"


# --- multi-file end-to-end sanity (stub llm_call) --------------------------


def test_multi_file_analyze_and_convert_yields_all_files():
    if not _robot_available() or not _ANALYTICS_DIR.is_dir():
        return
    analysis = _analyze_path_for_studio(str(_ANALYTICS_DIR), "", str(_AUTOTEST_ROOT))
    assert "error" not in analysis
    n_files = len([f for f in analysis["files"] if not f.get("error")])
    assert n_files > 1, "component/analytics should yield multiple file reports"

    llm = _ScriptedLLM()
    results = _convert_matched_files(analysis, "Robot Framework", "Playwright", llm, to_fw_data=None)
    assert len(results) == n_files
    assert all("result" in fr for fr in results)

    # every converted file ends up as a distinct tests/ entry in the bundle
    data = _build_project_zip(results, "Playwright")
    names = _zip_names(data)
    test_files = [n for n in names if n.startswith("converted_project/tests/")]
    assert len(test_files) == n_files
    assert len(set(test_files)) == n_files
