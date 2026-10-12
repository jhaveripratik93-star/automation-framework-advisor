"""Offline tests for the Robot *** Settings *** parser + uploaded-set resolver.

No LLM, no disk, no network. Covers Group 1 of the ingestion requirements:
parsing Library/Resource/WITH NAME, resolution within the uploaded set only,
non-fatal unresolved notes, zero-I/O purity, and the non-Robot no-op.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools.library_ingest import (
    build_library_context,
    parse_robot_settings,
    resolve_imports,
)


def test_parses_library_resource_and_with_name_alias():
    content = (
        "*** Settings ***\n"
        "Library    ../lib/Session.py\n"
        "Resource    keywords.robot\n"
        "Library    Foo    WITH NAME    Bar\n"
        "\n"
        "*** Test Cases ***\n"
        "Example\n"
        "    Log    hi\n"
    )
    imports = parse_robot_settings(content)
    targets = {(i.kind, i.target) for i in imports}
    assert ("Library", "../lib/Session.py") in targets
    assert ("Resource", "keywords.robot") in targets
    assert ("Library", "Foo") in targets
    alias = next(i for i in imports if i.target == "Foo")
    assert alias.alias == "Bar"


def test_with_name_case_insensitive():
    for kw in ("WITH NAME", "With Name", "with name"):
        content = f"*** Settings ***\nLibrary    Foo    {kw}    Bar\n"
        imports = parse_robot_settings(content)
        assert imports[0].alias == "Bar"


def test_as_alias_tolerated():
    content = "*** Settings ***\nLibrary    Foo    AS    Baz\n"
    imports = parse_robot_settings(content)
    assert imports[0].alias == "Baz"


def test_continuation_line_folds():
    content = (
        "*** Settings ***\n"
        "Library    Foo\n"
        "...    WITH NAME    Bar\n"
    )
    imports = parse_robot_settings(content)
    assert imports[0].target == "Foo"
    assert imports[0].alias == "Bar"


def test_resource_resolves_by_relative_path_and_basename():
    imports = [i for i in parse_robot_settings(
        "*** Settings ***\nResource    keywords.robot\n")]
    files = [{"filename": "tests/keywords.robot", "content": "*** Keywords ***\n"}]
    resolved, unresolved = resolve_imports(imports, files)
    assert not unresolved
    assert resolved[0].resolved
    assert resolved[0].filename == "tests/keywords.robot"


def test_dotted_library_resolves_by_path_fragment():
    imports = parse_robot_settings(
        "*** Settings ***\nLibrary    clamps.CenxQueryApi\n")
    files = [{
        "filename": "clamps/cenxqueryapi/cenxqueryapi.py",
        "content": "class CenxQueryApi:\n    pass\n",
    }]
    resolved, unresolved = resolve_imports(imports, files)
    assert not unresolved
    assert resolved[0].filename == "clamps/cenxqueryapi/cenxqueryapi.py"


def test_dotted_library_resolves_by_defined_class():
    imports = parse_robot_settings(
        "*** Settings ***\nLibrary    clamps.CenxQueryApi\n")
    files = [{
        "filename": "some/other/path/qapi.py",
        "content": "import os\n\nclass CenxQueryApi:\n    pass\n",
    }]
    resolved, unresolved = resolve_imports(imports, files)
    assert not unresolved
    assert resolved[0].filename == "some/other/path/qapi.py"


def test_unresolved_import_is_nonfatal_note():
    imports = parse_robot_settings(
        "*** Settings ***\nLibrary    clamps.Missing\n")
    files = [{"filename": "unrelated.py", "content": "x = 1\n"}]
    resolved, unresolved = resolve_imports(imports, files)
    assert not resolved
    assert len(unresolved) == 1
    assert unresolved[0].target == "clamps.Missing"


def test_parser_is_pure_no_filesystem(monkeypatch):
    """Resolution must be a pure function of (imports, files): zero I/O."""
    def _boom(*_a, **_k):
        raise AssertionError("filesystem access is forbidden in the resolver")

    monkeypatch.setattr("builtins.open", _boom)
    imports = parse_robot_settings(
        "*** Settings ***\nResource    keywords.robot\n")
    files = [{"filename": "x/keywords.robot", "content": "*** Keywords ***\n"}]
    resolved, unresolved = resolve_imports(imports, files)
    assert resolved and not unresolved


def test_non_robot_files_are_noop():
    files = [
        {"filename": "http.js", "content": "import http from 'k6/http';\n"},
        {"filename": "test_api.py", "content": "def test_x():\n    assert True\n"},
    ]
    assert parse_robot_settings(files[0]["content"]) == []
    lib_ctx = build_library_context(
        files, "K6", "pytest", "python", char_budget=6000)
    assert lib_ctx.is_empty
    assert lib_ctx.prompt_block == ""
