"""End-to-end importability test for the converted-project ZIP.

This is the real proof that the `ModuleNotFoundError: No module named 'common'`
class of bug (a dependency `.py` imported via `from X import NAME` whose NAME is
a sibling submodule) is fixed: we build a synthetic source_root with a genuine
cross-module import, collect + bundle it through the same pure helpers the page
uses, extract the ZIP, and run `pytest` in a SUBPROCESS from the project root.
The subprocess import of the cross-referenced submodule must succeed.

A synthetic fixture (not the real nifi.robot) is used for the subprocess import
proof on purpose: the real `common.rest` / `configs.common_config` pull heavy
third-party packages that are correctly NOT bundled, so importing them headless
would fail on the third-party deps rather than on this bug. A second,
import-free assertion covers the real nifi fixture's collected-set shape when
robotframework + the fixture are present.
"""
import io
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from _pages.code_studio import (
    _analyze_path_for_studio,
    _build_project_zip,
    _collect_dependency_files,
)


def _robot_available() -> bool:
    try:
        import robot.api  # noqa: F401
    except ImportError:
        return False
    return True


_AUTOTEST_ROOT = Path(__file__).parent.parent.parent / "autotest_2_extracted" / "autotest"
_NIFI_ROBOT = _AUTOTEST_ROOT / "core-tests" / "component" / "nifi.robot"


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _fake_analysis(source_root: Path, resolved_py: Path, raw_module: str) -> dict:
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


class _FakeResult:
    def __init__(self, code: str, target_language: str = "python"):
        self.code = code
        self.target_language = target_language


def test_bundled_cross_import_importable_in_subprocess(tmp_path):
    # Synthetic source_root: pkg_a.mod imports pkg_b.thing via `from pkg_b
    # import thing` and uses it at runtime. Before the fix, pkg_b/thing.py was
    # not bundled -> ModuleNotFoundError at collection.
    source_root = tmp_path / "src"
    _write(source_root / "pkg_a" / "__init__.py", "")
    _write(source_root / "pkg_a" / "mod.py", "from pkg_b import thing\n\nVALUE = thing.VALUE\n")
    _write(source_root / "pkg_b" / "__init__.py", "")
    _write(source_root / "pkg_b" / "thing.py", "VALUE = 42\n")

    analysis = _fake_analysis(source_root, source_root / "pkg_a" / "mod.py", "pkg_a.mod")
    dependency_files = _collect_dependency_files(analysis)
    dest = {d for d, _ in dependency_files}
    assert "pkg_b/thing.py" in dest  # the submodule the bug used to drop

    test_code = (
        "import pkg_a.mod\n\n\n"
        "def test_cross_import():\n"
        "    assert pkg_a.mod.VALUE == 42\n"
    )
    file_results = [{"file_path": "cross.robot", "result": _FakeResult(test_code)}]
    data = _build_project_zip(
        file_results, "Playwright", dependency_files=dependency_files, unresolved=[]
    )

    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "converted_project/conftest.py" in names
    assert "converted_project/requirements.txt" in names
    assert "converted_project/README.md" in names

    # Extract and run pytest in a subprocess from the project root.
    extract_dir = tmp_path / "out"
    extract_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(extract_dir)
    project_root = extract_dir / "converted_project"
    assert (project_root / "pkg_b" / "thing.py").is_file()

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"subprocess pytest failed (rc={proc.returncode})\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )


def test_package_init_sideeffect_import_importable_in_subprocess(tmp_path):
    # Mirrors the real bug: pkg/__init__.py eagerly imports a sibling submodule
    # (pkg.sub), but the directly-imported module is a DIFFERENT file (pkg/deep/
    # leaf.py). Python runs pkg/__init__.py on `import pkg.deep.leaf`, so
    # pkg/sub.py must be bundled or the subprocess import raises
    # ModuleNotFoundError: No module named 'pkg.sub' — the exact class of the
    # reported clamps.cenxrestapi failure.
    source_root = tmp_path / "src"
    _write(source_root / "pkg" / "__init__.py", "from pkg.sub import Thing\n")
    _write(source_root / "pkg" / "sub.py", "class Thing:\n    VALUE = 7\n")
    _write(source_root / "pkg" / "deep" / "__init__.py", "")
    _write(source_root / "pkg" / "deep" / "leaf.py", "LEAF = 1\n")

    analysis = _fake_analysis(source_root, source_root / "pkg" / "deep" / "leaf.py", "pkg.deep.leaf")
    dependency_files = _collect_dependency_files(analysis)
    dest = {d for d, _ in dependency_files}
    assert "pkg/deep/leaf.py" in dest
    assert "pkg/__init__.py" in dest
    assert "pkg/sub.py" in dest  # followed only via the __init__.py scan

    # The (stub) test imports ONLY the leaf — importing it triggers pkg/__init__.
    test_code = (
        "import pkg.deep.leaf\n"
        "from pkg.sub import Thing\n\n\n"
        "def test_init_side_effect():\n"
        "    assert pkg.deep.leaf.LEAF == 1\n"
        "    assert Thing.VALUE == 7\n"
    )
    file_results = [{"file_path": "init.robot", "result": _FakeResult(test_code)}]
    data = _build_project_zip(
        file_results, "Playwright", dependency_files=dependency_files, unresolved=[]
    )

    extract_dir = tmp_path / "out"
    extract_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(extract_dir)
    project_root = extract_dir / "converted_project"
    assert (project_root / "pkg" / "sub.py").is_file()

    # Direct subprocess import proof (no pytest noise): `import pkg.deep.leaf`
    # must succeed from the project root — i.e. pkg/__init__.py's side-effect
    # import resolves.
    proc = subprocess.run(
        [sys.executable, "-c", "import pkg.deep.leaf; import pkg.sub; print('OK')"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"subprocess import failed (rc={proc.returncode})\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert "OK" in proc.stdout


def test_real_nifi_zip_no_missing_clamps_submodule_in_subprocess(tmp_path):
    # The reported regression: `import clamps` failed with
    # `ModuleNotFoundError: No module named 'clamps.cenxrestapi'` because the
    # bundled clamps/__init__.py imports submodules that were never bundled.
    # Build the real ZIP, extract, and `import clamps` in a subprocess. A
    # ModuleNotFoundError for an uninstalled THIRD-PARTY package (jsonref,
    # kubernetes, ...) is acceptable noise; a missing clamps.* PROJECT submodule
    # that resolves on disk under source_root is NOT.
    if not _robot_available() or not _NIFI_ROBOT.is_file():
        return
    from _pages.code_studio import (
        _collect_third_party_requirements,
        _collect_unresolved_dependencies,
    )

    analysis = _analyze_path_for_studio(str(_NIFI_ROBOT), "", str(_AUTOTEST_ROOT))
    assert "error" not in analysis
    dependency_files = _collect_dependency_files(analysis)
    unresolved = _collect_unresolved_dependencies(analysis)
    third_party = _collect_third_party_requirements(
        dependency_files, analysis.get("source_root") or ""
    )

    data = _build_project_zip(
        [{"file_path": str(_NIFI_ROBOT), "result": _FakeResult("def test_x():\n    assert True\n")}],
        "Playwright",
        dependency_files=dependency_files,
        unresolved=unresolved,
        third_party=third_party,
    )
    extract_dir = tmp_path / "out"
    extract_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(extract_dir)
    project_root = extract_dir / "converted_project"

    # every clamps.* submodule the init chain resolved to is present in the ZIP
    assert (project_root / "clamps" / "cenxrestapi" / "__init__.py").is_file()
    assert (project_root / "clamps" / "cenxrestapi" / "session.py").is_file()

    proc = subprocess.run(
        [sys.executable, "-c", "import clamps"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        err = proc.stderr
        # the specific regression string must be GONE
        assert "No module named 'clamps.cenxrestapi'" not in err, (
            "the original bundling bug is back:\n" + err
        )
        # any residual failure must be a THIRD-PARTY module, never a bundled
        # clamps.* project submodule that resolves on disk.
        import re as _re

        m = _re.search(r"No module named '([^']+)'", err)
        if m is not None:
            missing = m.group(1)
            assert not missing.startswith("clamps."), (
                f"a bundled clamps.* project submodule is missing: {missing}\n{err}"
            )


def test_real_nifi_fixture_collects_top_level_siblings():
    if not _robot_available() or not _NIFI_ROBOT.is_file():
        return
    analysis = _analyze_path_for_studio(str(_NIFI_ROBOT), "", str(_AUTOTEST_ROOT))
    assert "error" not in analysis
    dest = {d for d, _ in _collect_dependency_files(analysis)}

    # The user's failing imports: top-level sibling packages/modules under
    # source_root that nifi.py imports must all be bundled.
    for expected in (
        "clamps/nifi/nifi.py",
        "common/rest.py",
        "common/constants.py",
        "common/utils.py",
        "configs/common_config.py",
        "settings.py",
    ):
        assert expected in dest, f"{expected} missing from bundled set"

    # package markers for the bundled packages
    for init in ("clamps/__init__.py", "clamps/nifi/__init__.py", "common/__init__.py", "configs/__init__.py"):
        assert init in dest, f"{init} missing from bundled set"

    # stdlib / third-party must NOT be bundled
    assert not any(d.startswith("kubernetes") for d in dest)
    assert not any(d.startswith("requests") for d in dest)
