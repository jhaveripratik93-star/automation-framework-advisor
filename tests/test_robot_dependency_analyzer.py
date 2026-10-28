"""Tests for src/analysis/robot_dependency_analyzer.py — static RF analysis.

The Robot-dependent tests skip their assertions when robotframework is not
installed in the environment (same degrade-don't-crash contract the analyzer
and tests/test_conversion_compilers.py use). Resolver/helper unit tests that
do not need the parser always run.
"""
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.analysis.robot_dependency_analyzer import (
    _normalize_keyword_name,
    _resolve_library,
    _resolve_relative,
    _split_keyword,
    analyze_file,
    analyze_path,
    filter_result_by_test_case,
)


def _robot_available() -> bool:
    try:
        import robot.api  # noqa: F401
    except ImportError:
        return False
    return True


# Real target fixtures (checked into the sibling autotest tree).
_AUTOTEST_ROOT = (
    Path(__file__).parent.parent.parent
    / "autotest_2_extracted" / "autotest"
)
_NIFI_ROBOT = _AUTOTEST_ROOT / "core-tests" / "component" / "nifi.robot"
_COMPONENT_DIR = _AUTOTEST_ROOT / "core-tests" / "component"


# ── Pure helper unit tests (no parser needed) ───────────────────────────

def test_split_keyword_prefixed():
    assert _split_keyword("Nifi.get_flow_status") == ("Nifi", "get_flow_status")


def test_split_keyword_bare():
    assert _split_keyword("Should Contain") == (None, "Should Contain")


def test_normalize_keyword_name_is_space_case_underscore_insensitive():
    assert _normalize_keyword_name("Should Contain") == "shouldcontain"
    assert _normalize_keyword_name("should_contain") == "shouldcontain"
    assert _normalize_keyword_name("ShouldContain") == "shouldcontain"


def test_resolve_library_finds_module_py_under_source_root(tmp_path):
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    mod = tmp_path / "pkg" / "sub" / "thing.py"
    mod.write_text("class Thing:\n    pass\n", encoding="utf-8")
    robot_file = tmp_path / "suite" / "x.robot"
    robot_file.parent.mkdir(parents=True)
    resolved, exists, kind = _resolve_library(
        "pkg.sub.thing.Thing", robot_file, tmp_path
    )
    assert exists is True
    assert kind == "custom"
    assert Path(resolved) == mod.resolve()


def test_resolve_library_unresolved_is_marked(tmp_path):
    robot_file = tmp_path / "x.robot"
    resolved, exists, kind = _resolve_library(
        "nope.does.not.Exist", robot_file, tmp_path
    )
    assert resolved is None
    assert exists is False
    assert kind == "unresolved"


def test_resolve_library_standard_lib_is_builtin(tmp_path):
    robot_file = tmp_path / "x.robot"
    resolved, exists, kind = _resolve_library("Collections", robot_file, tmp_path)
    assert kind == "builtin"


def test_resolve_relative_resource(tmp_path):
    res = tmp_path / "common.resource"
    res.write_text("*** Keywords ***\nDo It\n    Log    hi\n", encoding="utf-8")
    robot_file = tmp_path / "suite.robot"
    resolved, exists = _resolve_relative("common.resource", robot_file)
    assert exists is True
    assert Path(resolved) == res.resolve()


# ── Error / edge behavior ───────────────────────────────────────────────

def test_missing_path_returns_error_dict():
    result = analyze_path(r"C:\this\does\not\exist\nope.robot")
    assert "error" in result


def test_non_robot_file_returns_error_dict(tmp_path):
    f = tmp_path / "notrobot.txt"
    f.write_text("hello", encoding="utf-8")
    result = analyze_path(str(f))
    assert "error" in result


def test_empty_directory_returns_error_dict(tmp_path):
    result = analyze_path(str(tmp_path))
    assert "error" in result


# ── Real-parser-backed tests (skip assertions if robot not installed) ────

def test_nifi_robot_finds_four_test_cases():
    if not _robot_available() or not _NIFI_ROBOT.is_file():
        return
    result = analyze_file(str(_NIFI_ROBOT), source_root=str(_AUTOTEST_ROOT))
    assert result.get("error") is None
    names = {tc["name"] for tc in result["test_cases"]}
    assert names == {
        "Can Get Flow Status",
        "Can Get Flow About Dialog Information",
        "Can Get Flow Process Groups",
        "Restarting Nifi Pod And Validating Templates And Configuration",
    }


def test_nifi_robot_detects_two_custom_library_imports_with_aliases():
    if not _robot_available() or not _NIFI_ROBOT.is_file():
        return
    result = analyze_file(str(_NIFI_ROBOT), source_root=str(_AUTOTEST_ROOT))
    libs = result["settings"]["libraries"]
    aliases = {lib["alias"] for lib in libs}
    assert aliases == {"Nifi", "NifiController"}
    assert all(lib["kind"] == "custom" for lib in libs)
    # The controller import carries the positional ctor arg "nifi".
    controller = next(lib for lib in libs if lib["alias"] == "NifiController")
    assert "nifi" in controller["args"]


def test_nifi_robot_resolves_clamps_nifi_on_disk():
    if not _robot_available() or not _NIFI_ROBOT.is_file():
        return
    result = analyze_file(str(_NIFI_ROBOT), source_root=str(_AUTOTEST_ROOT))
    nifi = next(
        lib for lib in result["settings"]["libraries"]
        if lib["raw_module"] == "clamps.nifi.nifi.Nifi"
    )
    expected = (_AUTOTEST_ROOT / "clamps" / "nifi" / "nifi.py")
    if expected.is_file():
        assert nifi["exists"] is True
        assert Path(nifi["resolved_path"]) == expected.resolve()
    else:
        # Correct result when the file is absent: blocked downstream.
        assert nifi["exists"] is False


def test_nifi_robot_keyword_classification():
    if not _robot_available() or not _NIFI_ROBOT.is_file():
        return
    result = analyze_file(str(_NIFI_ROBOT), source_root=str(_AUTOTEST_ROOT))
    tc = next(
        t for t in result["test_cases"] if t["name"] == "Can Get Flow Status"
    )
    kws = {ku["raw"]: ku for ku in tc["keywords_used"]}
    # Custom keyword maps to the Nifi import.
    assert kws["Nifi.get_flow_status"]["classification"] == "custom"
    assert kws["Nifi.get_flow_status"]["maps_to_import"] == "clamps.nifi.nifi.Nifi"
    # Bare BuiltIn keyword.
    assert kws["Should Contain"]["classification"] == "builtin"
    # And the custom lib is listed under the test case's dependencies.
    assert "clamps.nifi.nifi.Nifi" in tc["dependencies"]["custom_libraries"]


def test_self_contained_suite(tmp_path):
    if not _robot_available():
        return
    robot = tmp_path / "pure.robot"
    robot.write_text(textwrap.dedent(
        """\
        *** Test Cases ***
        Pure Builtin
            ${x}=    Set Variable    hi
            Should Be Equal    ${x}    hi
        """
    ), encoding="utf-8")
    result = analyze_file(str(robot))
    tc = result["test_cases"][0]
    assert tc["conversion_readiness"] == "self_contained"


def test_unresolved_library_blocks_test_case(tmp_path):
    if not _robot_available():
        return
    robot = tmp_path / "blocked.robot"
    robot.write_text(textwrap.dedent(
        """\
        *** Settings ***
        Library    totally.missing.module.Thing    WITH NAME    Thing

        *** Test Cases ***
        Uses Missing Lib
            Thing.do_something
        """
    ), encoding="utf-8")
    result = analyze_file(str(robot))
    lib = result["settings"]["libraries"][0]
    assert lib["kind"] == "unresolved"
    assert result["test_cases"][0]["conversion_readiness"] == "blocked"


def test_bare_resource_keyword_classified_custom(tmp_path):
    """A bare (unprefixed) keyword defined in an imported Resource file must
    be classified custom, not assumed BuiltIn (review finding #3)."""
    if not _robot_available():
        return
    resource = tmp_path / "common.resource"
    resource.write_text(textwrap.dedent(
        """\
        *** Keywords ***
        Open The Door
            Log    opening
        """
    ), encoding="utf-8")
    robot = tmp_path / "suite.robot"
    robot.write_text(textwrap.dedent(
        """\
        *** Settings ***
        Resource    common.resource

        *** Test Cases ***
        Walk Through
            Open The Door
            Should Be Equal    1    1
        """
    ), encoding="utf-8")
    result = analyze_file(str(robot))
    tc = result["test_cases"][0]
    kws = {ku["raw"]: ku for ku in tc["keywords_used"]}
    assert kws["Open The Door"]["classification"] == "custom"
    assert kws["Open The Door"]["maps_to_import"] == str(resource.resolve())
    # The plain BuiltIn keyword stays builtin.
    assert kws["Should Be Equal"]["classification"] == "builtin"
    assert tc["conversion_readiness"] == "needs_custom_libs"


def test_directory_walk_skips_skip_dirs(tmp_path):
    if not _robot_available():
        return
    real = tmp_path / "real.robot"
    real.write_text(
        "*** Test Cases ***\nT\n    Log    hi\n", encoding="utf-8"
    )
    skipped_dir = tmp_path / ".git"
    skipped_dir.mkdir()
    (skipped_dir / "ignored.robot").write_text(
        "*** Test Cases ***\nSkip\n    Log    no\n", encoding="utf-8"
    )
    result = analyze_path(str(tmp_path))
    assert result["total_files"] == 1
    assert Path(result["files"][0]["file_path"]).name == "real.robot"


def test_broken_file_is_per_file_error_not_fatal(tmp_path):
    if not _robot_available():
        return
    good = tmp_path / "good.robot"
    good.write_text("*** Test Cases ***\nT\n    Log    hi\n", encoding="utf-8")
    # A file whose IF block is never closed is a parse/model error.
    bad = tmp_path / "bad.robot"
    bad.write_text(textwrap.dedent(
        """\
        *** Test Cases ***
        Broken
            IF    True
                Log    hi
        """
    ), encoding="utf-8")
    result = analyze_path(str(tmp_path))
    # Both files are listed; the run did not abort.
    assert result["total_files"] == 2


def test_directory_walk_aggregates_component_dir():
    if not _robot_available() or not _COMPONENT_DIR.is_dir():
        return
    result = analyze_path(str(_COMPONENT_DIR), source_root=str(_AUTOTEST_ROOT))
    assert result.get("error") is None
    assert result["total_files"] >= 1
    assert "report" in result


# ── filter_result_by_test_case helper ───────────────────────────────────

def _two_case_result(tmp_path):
    """Analyze a tiny two-test-case suite and return its analyze_path result."""
    robot = tmp_path / "pair.robot"
    robot.write_text(textwrap.dedent(
        """\
        *** Test Cases ***
        Verify Alpha Behavior
            Should Be Equal    1    1

        Verify Beta Behavior
            Should Be Equal    2    2
        """
    ), encoding="utf-8")
    return analyze_path(str(robot))


def test_filter_empty_name_returns_result_unchanged(tmp_path):
    if not _robot_available():
        return
    result = _two_case_result(tmp_path)
    assert result["total_test_cases"] == 2
    out = filter_result_by_test_case(result, "")
    assert out is result
    assert out["total_test_cases"] == 2


def test_filter_substring_matches_single_case_case_insensitively(tmp_path):
    if not _robot_available():
        return
    result = _two_case_result(tmp_path)
    out = filter_result_by_test_case(result, "alpha")
    assert out["total_test_cases"] == 1
    assert out["test_case_name"] == "alpha"
    names = [tc["name"] for fd in out["files"] for tc in fd["test_cases"]]
    assert names == ["Verify Alpha Behavior"]
    assert "Verify Alpha Behavior" in out["report"]
    # Input was not mutated.
    assert result["total_test_cases"] == 2


def test_filter_non_matching_name_yields_empty(tmp_path):
    if not _robot_available():
        return
    result = _two_case_result(tmp_path)
    out = filter_result_by_test_case(result, "no-such-case")
    assert out["total_test_cases"] == 0
    assert out["total_files"] == 0
    assert out["files"] == []


def test_filter_passes_error_dict_through_unchanged():
    err = {"error": "path not found: nope", "input_path": "nope"}
    out = filter_result_by_test_case(err, "anything")
    assert out is err
