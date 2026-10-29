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
    _collect_custom_marks,
    _collect_dependency_files,
    _collect_third_party_requirements,
    _collect_unresolved_dependencies,
    _conftest_with_marks,
    _convert_matched_files,
    _dest_rel_for_resolved,
    _merge_requirements,
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


# --- (b2) package-init imports are followed (the __init__.py scan bug) ------


def test_collect_follows_package_init_imports(tmp_path):
    # The regression in isolation: pkg/__init__.py eagerly imports pkg.sub, and
    # the ONLY thing the (stub) test imports directly is pkg/deep/leaf.py.
    # Bundling leaf.py pulls in pkg/__init__.py as a package marker; that init
    # must now be SCANNED so pkg/sub.py (which it imports) is also bundled.
    _write(tmp_path / "pkg" / "__init__.py", "from pkg.sub import Thing\n")
    _write(tmp_path / "pkg" / "sub.py", "class Thing:\n    pass\n")
    _write(tmp_path / "pkg" / "deep" / "__init__.py", "")
    _write(tmp_path / "pkg" / "deep" / "leaf.py", "VALUE = 1\n")

    analysis = _fake_analysis(tmp_path, tmp_path / "pkg" / "deep" / "leaf.py", "pkg.deep.leaf")
    dest = {d for d, _ in _collect_dependency_files(analysis)}

    assert "pkg/deep/leaf.py" in dest
    assert "pkg/__init__.py" in dest
    # the package-init import must now be followed:
    assert "pkg/sub.py" in dest


def test_collect_follows_real_clamps_package_init_chain():
    # Real-fixture proof: clamps/__init__.py imports clamps.cenxrestapi,
    # clamps.cenxfederatedapi, clamps.databases.solr.solr, clamps.cenxqueryapi.
    # All resolve as packages/modules on disk and were previously MISSED
    # because the bundled clamps/__init__.py was never scanned.
    if not _robot_available() or not _NIFI_ROBOT.is_file():
        return
    analysis = _analyze_path_for_studio(str(_NIFI_ROBOT), "", str(_AUTOTEST_ROOT))
    dest_map = _dest_map(analysis)

    # package inits reached only via scanning clamps/__init__.py:
    assert "clamps/cenxrestapi/__init__.py" in dest_map
    assert "clamps/cenxfederatedapi/__init__.py" in dest_map
    assert "clamps/cenxqueryapi/__init__.py" in dest_map
    # the exact submodule from the reported ModuleNotFoundError trace:
    #   from clamps.cenxrestapi import Session  -> clamps/cenxrestapi/session.py
    assert "clamps/cenxrestapi/session.py" in dest_map
    assert "clamps/cenxrestapi/customization.py" in dest_map
    assert "clamps/cenxrestapi/topology.py" in dest_map
    assert "clamps/cenxrestapi/data.py" in dest_map
    assert "clamps/cenxrestapi/cenxens.py" in dest_map
    # recursion continues through the second-level package inits:
    assert "clamps/cenxqueryapi/cenxqueryapi.py" in dest_map
    assert "clamps/cenxfederatedapi/entity.py" in dest_map
    # and the module imported via the dotted submodule path:
    assert "clamps/databases/solr/solr.py" in dest_map

    # every clamps module these inits resolve to maps to a real file on disk
    for rel, abs_src in dest_map.items():
        if rel.startswith("clamps/") and abs_src is not None:
            assert Path(abs_src).is_file()


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


def test_scan_dotted_imports_emits_from_submodules():
    text = (
        "from common import rest\n"              # -> common AND common.rest
        "from pkg import a, b\n"                 # -> pkg.a, pkg.b
        "import pkg.a, pkg.b as q\n"             # -> pkg.a, pkg.b (both modules)
        "from thing import *\n"                  # -> only thing
        "from pkg2 import (one, two)\n"          # parenthesized -> pkg2.one, pkg2.two
        "from aliased import name as n\n"        # -> aliased.name (alias dropped)
        "from . import sibling\n"                # relative -> skipped
        "from .sub import t\n"                   # relative -> skipped
    )
    found = _scan_dotted_imports(text)
    # from X import NAME now yields BOTH the base and the submodule candidate.
    assert "common" in found
    assert "common.rest" in found
    assert "pkg.a" in found
    assert "pkg.b" in found
    assert "pkg2.one" in found
    assert "pkg2.two" in found
    assert "aliased.name" in found
    # from X import * cannot know the names -> only the base package.
    assert "thing" in found
    assert not any(f.startswith("thing.") for f in found)
    # relative imports are still skipped entirely.
    assert "sibling" not in found
    assert "sub" not in found
    assert "sub.t" not in found


def test_collect_bundles_from_import_submodule(tmp_path):
    # pkg_a/mod.py does `from pkg_b import thing` — the submodule pkg_b/thing.py
    # was previously MISSED (only pkg_b was emitted). It must now be bundled.
    _write(tmp_path / "pkg_a" / "__init__.py", "")
    _write(tmp_path / "pkg_a" / "mod.py", "from pkg_b import thing\n")
    _write(tmp_path / "pkg_b" / "__init__.py", "")
    _write(tmp_path / "pkg_b" / "thing.py", "VALUE = 42\n")

    analysis = _fake_analysis(tmp_path, tmp_path / "pkg_a" / "mod.py", "pkg_a.mod")
    dest = {d for d, _ in _collect_dependency_files(analysis)}

    assert "pkg_a/mod.py" in dest
    assert "pkg_a/__init__.py" in dest
    assert "pkg_b/__init__.py" in dest
    # the previously-missing submodule imported via `from pkg_b import thing`
    assert "pkg_b/thing.py" in dest


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


# --- (f) third-party requirements scanner (synthetic, no LLM, no robot) -----


def test_collect_third_party_requirements_classifies_imports(tmp_path):
    # A bundled file importing a third-party pkg, stdlib, and a bundled project
    # module. source_root makes the project module resolvable -> excluded.
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "project_mod.py", "VALUE = 1\n")
    _write(
        tmp_path / "pkg" / "dep.py",
        "import os\n"                       # stdlib -> skip
        "from kubernetes import client\n"   # third-party -> keep
        "import requests\n"                 # third-party -> keep
        "from pkg import project_mod\n",    # bundled project module -> skip
    )

    dependency_files = [
        ("pkg/dep.py", str(tmp_path / "pkg" / "dep.py")),
        ("pkg/project_mod.py", str(tmp_path / "pkg" / "project_mod.py")),
        ("pkg/__init__.py", str(tmp_path / "pkg" / "__init__.py")),
    ]
    result = _collect_third_party_requirements(dependency_files, str(tmp_path))
    assert result == ["kubernetes", "requests"]


def test_collect_third_party_requirements_maps_distribution_names(tmp_path):
    _write(
        tmp_path / "dep.py",
        "import yaml\n"      # import name yaml -> distribution PyYAML
        "import robot\n"     # import name robot -> distribution robotframework
        "import httpx\n",    # pass-through
    )
    dependency_files = [("dep.py", str(tmp_path / "dep.py"))]
    result = _collect_third_party_requirements(dependency_files, str(tmp_path))
    assert "PyYAML" in result
    assert "robotframework" in result
    assert "httpx" in result
    assert "yaml" not in result  # mapped, not the raw import name


def test_collect_third_party_requirements_skips_synthesized_scans_real_init(tmp_path):
    # Only synthesized __init__ (abs_src None) is skipped. A REAL package
    # __init__.py IS scanned for third-party imports now, because Python runs
    # it on any import into the package and its eager third-party imports must
    # reach requirements.txt.
    _write(tmp_path / "pkg" / "__init__.py", "import kubernetes\n")
    dependency_files = [
        ("pkg/__init__.py", str(tmp_path / "pkg" / "__init__.py")),
        ("pkg/sub/__init__.py", None),  # synthesized -> skipped
    ]
    assert _collect_third_party_requirements(dependency_files, str(tmp_path)) == ["kubernetes"]


def test_collect_third_party_requirements_sorted_and_deduped(tmp_path):
    _write(tmp_path / "a.py", "import requests\nimport kubernetes\n")
    _write(tmp_path / "b.py", "import requests\n")  # duplicate across files
    dependency_files = [
        ("a.py", str(tmp_path / "a.py")),
        ("b.py", str(tmp_path / "b.py")),
    ]
    result = _collect_third_party_requirements(dependency_files, str(tmp_path))
    assert result == ["kubernetes", "requests"]  # sorted, de-duped


# --- _merge_requirements -----------------------------------------------------


def test_merge_requirements_appends_new_and_keeps_existing():
    req = "playwright>=1.40.0\npytest-playwright>=0.4.0\npytest>=7.0"
    merged = _merge_requirements(req, ["kubernetes", "requests"])
    lines = merged.splitlines()
    # framework entries kept verbatim (pins preserved)
    assert "playwright>=1.40.0" in lines
    assert "pytest>=7.0" in lines
    # new third-party names appended unpinned
    assert "kubernetes" in lines
    assert "requests" in lines


def test_merge_requirements_dedupes_case_insensitively():
    req = "pytest>=7.0\nRequests==2.31.0"
    merged = _merge_requirements(req, ["requests", "kubernetes"])
    lines = [l for l in merged.splitlines() if l.strip()]
    # 'requests' already present (as Requests==…) -> not re-added
    assert sum(1 for l in lines if l.lower().startswith("requests")) == 1
    assert "kubernetes" in lines


def test_merge_requirements_empty_third_party_noop():
    req = "pytest>=7.0\n"
    assert _merge_requirements(req, []) == req


# --- (g) REAL-fixture end-to-end: honest imports + third-party reqs ---------

# Stub-converted nifi test that (wrongly) wraps its bundled-dependency imports
# in try/except ImportError NotImplementedError stubs. Flowed THROUGH convert()
# so the import-stub postprocess guard rewrites it to direct imports in the ZIP.
STUB_FALLBACK_NIFI_OUTPUT = """import pytest

try:
    from clamps.nifi.nifi import Nifi
except ImportError:
    class Nifi:
        def get_flow_status(self):
            raise NotImplementedError("TODO: Implement clamps.nifi.nifi.Nifi")

try:
    from clamps.infrastructure.k8s.application_controller import ApplicationController
except ImportError:
    class ApplicationController:
        def restart(self):
            raise NotImplementedError("TODO: Implement ApplicationController")


@pytest.fixture
def nifi() -> Nifi:
    return Nifi()


@pytest.fixture
def nifi_controller() -> ApplicationController:
    return ApplicationController()


def test_can_get_flow_status(nifi: Nifi):
    status = nifi.get_flow_status()
    assert "controllerStatus" in status
"""


def test_real_nifi_fixture_honest_imports_and_third_party_requirements():
    if not _robot_available() or not _NIFI_ROBOT.is_file():
        return
    analysis = _analyze_path_for_studio(str(_NIFI_ROBOT), "", str(_AUTOTEST_ROOT))
    assert "error" not in analysis

    # Convert the real fixture with a stub LLM that returns the stub-fallback
    # test code; the pipeline's import-stub guard must rewrite it.
    llm = _ScriptedLLM(STUB_FALLBACK_NIFI_OUTPUT)
    file_results = _convert_matched_files(
        analysis, "Robot Framework", "Playwright", llm, to_fw_data=None
    )
    assert file_results and all("result" in fr for fr in file_results)

    dependency_files = _collect_dependency_files(analysis)
    unresolved = _collect_unresolved_dependencies(analysis)
    third_party = _collect_third_party_requirements(
        dependency_files, analysis.get("source_root") or ""
    )
    data = _build_project_zip(
        file_results, "Playwright",
        dependency_files=dependency_files, unresolved=unresolved,
        third_party=third_party,
    )
    names = _zip_names(data)

    # (a) converted test imports both symbols DIRECTLY — no stub, no try/except.
    test_name = next(n for n in names if n.startswith("converted_project/tests/"))
    test_src = _zip_read_bytes(data, test_name).decode("utf-8")
    assert "from clamps.nifi.nifi import Nifi" in test_src
    assert (
        "from clamps.infrastructure.k8s.application_controller import ApplicationController"
        in test_src
    )
    assert "except ImportError" not in test_src
    assert "NotImplementedError" not in test_src

    # (b) requirements.txt contains kubernetes (guaranteed in the dep chain),
    #     plus whatever other real third-party the scan found.
    req_text = _zip_read_bytes(data, "converted_project/requirements.txt").decode("utf-8")
    req_names = {l.strip().lower() for l in req_text.splitlines() if l.strip()}
    assert "kubernetes" in third_party
    assert "kubernetes" in req_names
    # the scan picked up real third-party deps of the bundled chain
    assert third_party, "expected at least one third-party requirement"

    # (c) README lists the detected third-party deps.
    readme = _zip_read_bytes(data, "converted_project/README.md").decode("utf-8")
    assert "Third-party packages detected from bundled code" in readme
    assert "kubernetes" in readme

    # (d) no CI/CD tokens in requirements / README.
    forbidden = ["github", "ci", "workflow", "actions"]
    for fname in ("converted_project/requirements.txt", "converted_project/README.md"):
        text = _zip_read_bytes(data, fname).decode("utf-8").lower()
        for token in forbidden:
            assert token not in text, f"{token!r} unexpectedly present in {fname}"


# --- (h) custom pytest-mark registration in the generated conftest ---------


def test_collect_custom_marks_excludes_builtins():
    code = (
        "import pytest\n"
        "pytestmark = [pytest.mark.nifi]\n"
        "@pytest.mark.cenx_platform\n"
        "@pytest.mark.Implements_TEST_13556\n"
        "@pytest.mark.dont_run\n"
        "@pytest.mark.skip\n"              # built-in -> excluded
        "@pytest.mark.parametrize('x', [1])\n"  # built-in -> excluded
        "def test_x():\n"
        "    pass\n"
    )
    marks = _collect_custom_marks([code])
    assert marks == [
        "Implements_TEST_13556",
        "cenx_platform",
        "dont_run",
        "nifi",
    ]


def test_conftest_with_marks_registers_each():
    body = _conftest_with_marks(["nifi", "dont_run"])
    assert "def pytest_configure(config):" in body
    assert 'config.addinivalue_line("markers", "nifi:' in body
    assert 'config.addinivalue_line("markers", "dont_run:' in body


def test_conftest_with_no_marks_is_base_body():
    from _pages.code_studio import _CONFTEST_BODY

    assert _conftest_with_marks([]) == _CONFTEST_BODY


def test_build_project_zip_registers_marks_in_conftest(tmp_path):
    # A converted test uses a custom mark; the bundled conftest must register it.
    marked_code = (
        "import pytest\n"
        "from clamps.nifi.nifi import Nifi\n"
        "\n"
        "@pytest.mark.nifi\n"
        "def test_x():\n"
        "    assert Nifi()\n"
    )

    class _FakeResult:
        code = marked_code
        target_language = "python"

    # dependency_files non-empty so conftest.py is written.
    _write(tmp_path / "clamps" / "nifi" / "nifi.py", "class Nifi: pass\n")
    dep_files = [("clamps/nifi/nifi.py", str(tmp_path / "clamps" / "nifi" / "nifi.py"))]
    data = _build_project_zip(
        [{"file_path": r"C:\x\nifi.robot", "result": _FakeResult()}],
        "Playwright",
        dependency_files=dep_files,
    )
    conftest = _zip_read_bytes(data, "converted_project/conftest.py").decode("utf-8")
    assert "def pytest_configure(config):" in conftest
    assert 'config.addinivalue_line("markers", "nifi:' in conftest
