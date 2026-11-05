"""Tests for the project-ZIP pure helpers on the Framework Migration page.

Exercises the Streamlit-free logic added to _pages/code_studio.py:
  - _safe_test_filename  (safe, de-duplicated test filenames)
  - _build_project_zip   (in-memory runnable project bundle)

plus a multi-file sanity check that analyze_path on a SELF-CONTAINED synthetic
suite directory (see tests/conftest.py) yields >1 file and _convert_matched_files
with a scripted stub llm_call returns one result per file. No running Streamlit
server or live LLM backend is needed, and nothing depends on any folder outside
the repo. The parser-backed test uses ``pytest.importorskip("robot")`` so it
skips cleanly ONLY when robotframework is absent.
"""
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from _pages.code_studio import (
    _analyze_path_for_studio,
    _build_project_zip,
    _collect_dependency_files,
    _convert_matched_files,
    _get_run_instructions,
    _safe_test_filename,
)

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
    name = _safe_test_filename(r"C:\x\widgets.robot", ".py", used)
    assert name == "test_widgets.py"
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
        {"file_path": r"C:\x\widgets.robot", "result": _FakeResult(VALID_PLAYWRIGHT_OUTPUT)},
    ]
    data = _build_project_zip(file_results, "Playwright")
    names = _zip_names(data)

    assert "converted_project/requirements.txt" in names
    assert "converted_project/README.md" in names
    test_files = [n for n in names if n.startswith("converted_project/tests/")]
    assert len(test_files) == 1
    assert test_files[0] == "converted_project/tests/test_widgets.py"

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
        {"file_path": r"C:\x\widgets.robot", "result": _FakeResult(VALID_PLAYWRIGHT_OUTPUT)},
    ]
    data = _build_project_zip(file_results, "Playwright")
    forbidden = ["github", "ci", "workflow", "actions", ".github", "ci/cd"]
    for fname in ("converted_project/requirements.txt", "converted_project/README.md"):
        text = _zip_read(data, fname).lower()
        for token in forbidden:
            assert token not in text, f"{token!r} unexpectedly present in {fname}"


# --- multi-file end-to-end sanity (stub llm_call) --------------------------


def test_multi_file_analyze_and_convert_yields_all_files(make_robot_suite):
    pytest.importorskip("robot")
    suite = make_robot_suite()
    suite_dir = suite.suite.parent  # the directory holding >1 synthetic suite
    analysis = _analyze_path_for_studio(str(suite_dir), "", str(suite.source_root))
    assert "error" not in analysis
    n_files = len([f for f in analysis["files"] if not f.get("error")])
    assert n_files > 1, "the synthetic suite dir should yield multiple file reports"

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


# --- dependency modernization (Python 3.10/3.11 compat) --------------------


def _zip_read_bytes(data: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read(name)


def test_bundled_dependency_legacy_collections_import_is_modernized(tmp_path):
    """End-to-end (synthetic, framework-neutral): a bundled dependency whose
    source carries a pre-3.10 ``from collections import Iterable`` is written
    into the ZIP MODERNIZED to ``from collections.abc import Iterable`` and
    imports cleanly on this (3.11) interpreter; a clean dependency with no
    legacy pattern is bundled byte-identical (verbatim preserved)."""
    import subprocess

    # A legacy dependency: valid Python 3, but uses the pre-3.10 import that
    # breaks on 3.11. (No reference to any real project/path.)
    legacy_src = (
        '"""A synthetic legacy dependency (framework-neutral)."""\n'
        "from collections import Iterable, OrderedDict\n"
        "\n"
        "\n"
        "def flatten(x):\n"
        "    return isinstance(x, Iterable)\n"
    )
    legacy_file = tmp_path / "legacy_dep.py"
    legacy_file.write_text(legacy_src, encoding="utf-8")

    # A clean dependency: no legacy pattern -> must stay byte-identical.
    clean_src = (
        '"""A clean modern dependency."""\n'
        "from collections import OrderedDict\n"
        "\n"
        "VALUE = 1\n"
    )
    clean_file = tmp_path / "clean_dep.py"
    clean_file.write_text(clean_src, encoding="utf-8")

    dependency_files = [
        ("pkg/legacy_dep.py", str(legacy_file)),
        ("pkg/clean_dep.py", str(clean_file)),
    ]

    file_results = [
        {"file_path": r"C:\x\widgets.robot", "result": _FakeResult(VALID_PLAYWRIGHT_OUTPUT)},
    ]
    data = _build_project_zip(
        file_results, "Playwright", dependency_files=dependency_files
    )

    legacy_out = _zip_read_bytes(data, "converted_project/pkg/legacy_dep.py").decode("utf-8")
    assert "from collections.abc import Iterable" in legacy_out
    # OrderedDict stays on collections; program logic unchanged (flatten kept).
    assert "from collections import OrderedDict" in legacy_out
    assert "def flatten(x):" in legacy_out
    assert "from collections import Iterable" not in legacy_out

    # The clean file is bundled byte-identical to its source on disk.
    clean_out = _zip_read_bytes(data, "converted_project/pkg/clean_dep.py")
    assert clean_out == clean_file.read_bytes()

    # The modernized dependency imports cleanly under the running 3.11 venv.
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(extracted)
    pkg_dir = extracted / "converted_project" / "pkg"
    proc = subprocess.run(
        [sys.executable, "-c", "import legacy_dep; assert legacy_dep.flatten([1]) is True"],
        cwd=str(pkg_dir),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"import failed: {proc.stderr}"


def test_collect_dependency_files_then_modernize_round_trip(make_robot_suite):
    """The REAL _collect_dependency_files path still works and its bundled deps
    survive the modernization write path: an existing clean synthetic dep stays
    byte-identical in the ZIP (verbatim preserved for files with no legacy
    pattern)."""
    pytest.importorskip("robot")
    suite = make_robot_suite()
    analysis = _analyze_path_for_studio(
        str(suite.suite), "", str(suite.source_root)
    )
    assert "error" not in analysis

    dependency_files = _collect_dependency_files(analysis)
    assert dependency_files, "synthetic suite should resolve custom deps"

    file_results = [
        {"file_path": str(suite.suite), "result": _FakeResult(VALID_PLAYWRIGHT_OUTPUT)},
    ]
    data = _build_project_zip(
        file_results, "Playwright", dependency_files=dependency_files
    )

    # client.py in the synthetic tree has no legacy collections pattern, so it
    # must be bundled byte-identical to the on-disk source.
    client_bytes = _zip_read_bytes(
        data, f"converted_project/{suite.client_dest}"
    )
    assert client_bytes == suite.client_py.read_bytes()
