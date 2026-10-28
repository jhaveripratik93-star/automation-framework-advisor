"""Tests for dependency bundling on the Framework Migration page.

Exercises the Streamlit-free helpers added to _pages/code_studio.py that make
the downloadable converted project bundle the test's RESOLVED dependency source
files verbatim (Option A) instead of NotImplementedError stubs:

  - _collect_dependency_files      (direct + bounded transitive collection)
  - _collect_unresolved_dependencies
  - _scan_dotted_imports           (pure static import scan)
  - _dest_rel_for_resolved         (dotted/path -> in-project dest layout)
  - _build_project_zip(..., dependency_files=..., unresolved=...)

Dependency source is copied VERBATIM, never sent through the LLM converter.
Any conversion here uses a scripted STUB llm_call (the headless LLM is not
reliably reachable). Parser-backed tests skip (early return) when robotframework
or the on-disk fixture is absent, matching the other analyzer tests. No running
Streamlit server is needed; Streamlit is never launched.
"""
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from _pages.code_studio import (
    _analyze_path_for_studio,
    _build_project_zip,
    _collect_dependency_files,
    _collect_unresolved_dependencies,
    _convert_matched_files,
    _dest_rel_for_resolved,
    _scan_dotted_imports,
)


def _robot_available() -> bool:
    try:
        import robot.api  # noqa: F401
    except ImportError:
        return False
    return True


_AUTOTEST_ROOT = Path(__file__).parent.parent.parent / "autotest_2_extracted" / "autotest"
_NIFI_ROBOT = _AUTOTEST_ROOT / "core-tests" / "component" / "nifi.robot"

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


def _dest_map(analysis):
    return {dest: abs_src for dest, abs_src in _collect_dependency_files(analysis)}


# --- (a) real-fixture direct resolution ------------------------------------


def test_collect_includes_direct_resolved_dependencies():
    if not _robot_available() or not _NIFI_ROBOT.is_file():
        return
    analysis = _analyze_path_for_studio(str(_NIFI_ROBOT), "", str(_AUTOTEST_ROOT))
    assert "error" not in analysis
    dest_map = _dest_map(analysis)

    assert "clamps/nifi/nifi.py" in dest_map
    assert "clamps/infrastructure/k8s/application_controller.py" in dest_map

    # mapped to the real absolute source files on disk
    nifi_src = Path(dest_map["clamps/nifi/nifi.py"])
    assert nifi_src.is_file()
    assert nifi_src.resolve() == (_AUTOTEST_ROOT / "clamps" / "nifi" / "nifi.py").resolve()

    # __init__.py package markers along the bundled path
    assert "clamps/__init__.py" in dest_map
    assert "clamps/nifi/__init__.py" in dest_map


# --- (b) bounded transitive scan reaches second-level deps -----------------


def test_collect_includes_transitive_dependencies():
    if not _robot_available() or not _NIFI_ROBOT.is_file():
        return
    analysis = _analyze_path_for_studio(str(_NIFI_ROBOT), "", str(_AUTOTEST_ROOT))
    dest_map = _dest_map(analysis)

    # nifi.py imports these project modules; the static scan must pull them in.
    assert "common/rest.py" in dest_map
    assert "configs/common_config.py" in dest_map
    assert "settings.py" in dest_map
    # application_controller.py imports kubernetes_base (second-level).
    assert "clamps/infrastructure/k8s/kubernetes_base.py" in dest_map

    # external pip packages / stdlib must NOT be bundled
    assert not any(d.startswith("kubernetes") for d in dest_map)
    assert "json.py" not in dest_map
    assert "os.py" not in dest_map


# --- (c) bound / cycle guard (synthetic temp package) ----------------------


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _fake_analysis(source_root: Path, resolved_py: Path, raw_module: str) -> dict:
    """Minimal analysis dict with one resolved custom library, mirroring the
    analyzer's to_dict() shape, so _collect_dependency_files can seed from it."""
    return {
        "source_root": str(source_root),
        "files": [
            {
                "file_path": str(source_root / "suite.robot"),
                "error": None,
                "settings": {
                    "libraries": [
                        {
                            "raw_module": raw_module,
                            "alias": None,
                            "args": [],
                            "resolved_path": str(resolved_py),
                            "exists": True,
                            "kind": "custom",
                        }
                    ],
                    "resources": [],
                    "variables": [],
                },
            }
        ],
    }


def test_transitive_cycle_guard_terminates(tmp_path):
    # pkg/a.py imports pkg.b, pkg/b.py imports pkg.a -> a cycle.
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "a.py", "from pkg import b\nimport pkg.b\n")
    _write(tmp_path / "pkg" / "b.py", "from pkg import a\nimport pkg.a\n")

    analysis = _fake_analysis(tmp_path, tmp_path / "pkg" / "a.py", "pkg.a")
    result = _collect_dependency_files(analysis)  # must not hang
    dest = {d for d, _ in result}
    assert "pkg/a.py" in dest
    assert "pkg/b.py" in dest
    # terminates with a finite, small set (cycle did not re-add forever)
    assert len([d for d in dest if d.endswith(".py") and not d.endswith("__init__.py")]) == 2


def test_transitive_respects_max_files_cap(tmp_path):
    # chain m0 -> m1 -> m2 -> ... so depth is deep; cap at a tiny max_files.
    for i in range(6):
        nxt = f"import pkg.m{i + 1}\n" if i < 5 else ""
        _write(tmp_path / "pkg" / f"m{i}.py", nxt)
    _write(tmp_path / "pkg" / "__init__.py", "")

    analysis = _fake_analysis(tmp_path, tmp_path / "pkg" / "m0.py", "pkg.m0")
    result = _collect_dependency_files(analysis, max_depth=5, max_files=3)
    assert len(result) <= 3  # the cap is respected


def test_transitive_respects_max_depth(tmp_path):
    for i in range(6):
        nxt = f"import pkg.m{i + 1}\n" if i < 5 else ""
        _write(tmp_path / "pkg" / f"m{i}.py", nxt)
    _write(tmp_path / "pkg" / "__init__.py", "")

    analysis = _fake_analysis(tmp_path, tmp_path / "pkg" / "m0.py", "pkg.m0")
    # depth 1 from m0 reaches m1 only (m0 is depth 0, scans to depth 1).
    result = _collect_dependency_files(analysis, max_depth=1, max_files=300)
    dest = {d for d, _ in result}
    assert "pkg/m0.py" in dest
    assert "pkg/m1.py" in dest
    assert "pkg/m5.py" not in dest  # deep modules not reached at depth 1


# --- pure sub-helper unit cases --------------------------------------------


def test_scan_dotted_imports_basic():
    text = (
        "import json\n"
        "import clamps.nifi.nifi as n\n"
        "from common.rest import UnexpectedHttpResponse\n"
        "from . import sibling\n"          # relative -> skipped
        "from .pkg import thing\n"         # relative -> skipped
        "    import indented.module\n"
    )
    found = _scan_dotted_imports(text)
    assert "json" in found
    assert "clamps.nifi.nifi" in found
    assert "common.rest" in found
    assert "indented.module" in found
    assert "sibling" not in found
    assert "pkg" not in found


def test_dest_rel_for_resolved_from_source_root(tmp_path):
    src = tmp_path / "clamps" / "nifi" / "nifi.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("", encoding="utf-8")
    dest = _dest_rel_for_resolved(str(src), str(tmp_path), "clamps.nifi.nifi.Nifi")
    assert dest == "clamps/nifi/nifi.py"


def test_dest_rel_for_resolved_fallback_from_module():
    # No source_root -> derive from dotted module, dropping trailing class.
    dest = _dest_rel_for_resolved(r"C:\somewhere\x.py", "", "clamps.nifi.nifi.Nifi")
    assert dest == "clamps/nifi/nifi.py"


# --- (d) ZIP contents: real verbatim dependency source + conftest ----------


def _zip_names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.namelist()


def _zip_read_bytes(data: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read(name)


def test_build_project_zip_bundles_real_dependency_source():
    if not _robot_available() or not _NIFI_ROBOT.is_file():
        return
    analysis = _analyze_path_for_studio(str(_NIFI_ROBOT), "", str(_AUTOTEST_ROOT))
    assert "error" not in analysis

    llm = _ScriptedLLM()
    file_results = _convert_matched_files(
        analysis, "Robot Framework", "Playwright", llm, to_fw_data=None
    )
    assert file_results and all("result" in fr for fr in file_results)

    dependency_files = _collect_dependency_files(analysis)
    unresolved = _collect_unresolved_dependencies(analysis)
    data = _build_project_zip(
        file_results, "Playwright",
        dependency_files=dependency_files, unresolved=unresolved,
    )
    names = _zip_names(data)

    assert any(n.startswith("converted_project/tests/") for n in names)
    assert "converted_project/clamps/nifi/nifi.py" in names
    assert "converted_project/clamps/__init__.py" in names
    assert "converted_project/clamps/nifi/__init__.py" in names
    assert "converted_project/conftest.py" in names
    assert "converted_project/requirements.txt" in names
    assert "converted_project/README.md" in names

    # bundled nifi.py is a verbatim copy of the on-disk source
    on_disk = (_AUTOTEST_ROOT / "clamps" / "nifi" / "nifi.py").read_bytes()
    bundled = _zip_read_bytes(data, "converted_project/clamps/nifi/nifi.py")
    assert bundled == on_disk

    # no CI/CD tokens in requirements / README (substring check, matches the
    # existing project-zip test's contract)
    forbidden = ["github", "ci", "workflow", "actions", ".github", "ci/cd"]
    for fname in ("converted_project/requirements.txt", "converted_project/README.md"):
        text = _zip_read_bytes(data, fname).decode("utf-8").lower()
        for token in forbidden:
            assert token not in text, f"{token!r} unexpectedly present in {fname}"


def test_build_project_zip_default_no_dependencies_unchanged():
    # dependency_files defaults to None -> no dep files, no conftest.py.
    class _FakeResult:
        code = VALID_PLAYWRIGHT_OUTPUT
        target_language = "python"

    data = _build_project_zip(
        [{"file_path": r"C:\x\nifi.robot", "result": _FakeResult()}], "Playwright"
    )
    names = _zip_names(data)
    assert "converted_project/conftest.py" not in names
    assert not [n for n in names if n.startswith("converted_project/clamps/")]


# --- (e) unresolved dependency is reported, not silently dropped -----------


def test_unresolved_dependency_reported_and_not_bundled():
    analysis = {
        "source_root": "",
        "files": [
            {
                "file_path": r"C:\x\suite.robot",
                "error": None,
                "settings": {
                    "libraries": [
                        {
                            "raw_module": "missing.pkg.Thing",
                            "alias": None,
                            "args": [],
                            "resolved_path": None,
                            "exists": False,
                            "kind": "unresolved",
                        }
                    ],
                    "resources": [],
                    "variables": [],
                },
            }
        ],
    }
    dep_files = _collect_dependency_files(analysis)
    assert not any("missing" in d for d, _ in dep_files)

    unresolved = _collect_unresolved_dependencies(analysis)
    assert "missing.pkg.Thing" in unresolved

    # README surfaces it as a remaining stub.
    class _FakeResult:
        code = VALID_PLAYWRIGHT_OUTPUT
        target_language = "python"

    data = _build_project_zip(
        [{"file_path": r"C:\x\suite.robot", "result": _FakeResult()}],
        "Playwright",
        dependency_files=dep_files,
        unresolved=unresolved,
    )
    readme = _zip_read_bytes(data, "converted_project/README.md").decode("utf-8")
    assert "missing.pkg.Thing" in readme
