"""Unit tests for the safe Python 3.10/3.11 modernization helper.

Pure tests — no network, no LLM, no Streamlit. They assert the transforms are
correct, logic-preserving, docstring-safe, idempotent, and that the resulting
source actually compiles and the moved names resolve on the running (3.11)
interpreter.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.conversion.pycompat import modernize_python_source


def _compiles(source: str) -> None:
    compile(source, "<modernized>", "exec")


def _exec_module(source: str) -> dict:
    ns: dict = {}
    exec(compile(source, "<modernized>", "exec"), ns)
    return ns


# --- from collections import <ABC> -----------------------------------------


def test_simple_abc_import_is_moved_to_collections_abc():
    src = "from collections import Iterable\n"
    out = modernize_python_source(src)
    assert out == "from collections.abc import Iterable\n"
    _compiles(out)
    ns = _exec_module(out)
    assert ns["Iterable"] is __import__("collections.abc", fromlist=["Iterable"]).Iterable


def test_multi_name_import_is_split_keeping_non_abc_on_collections():
    src = "from collections import OrderedDict, Iterable\n"
    out = modernize_python_source(src)
    # OrderedDict stays on collections; Iterable moves to collections.abc.
    assert "from collections import OrderedDict" in out
    assert "from collections.abc import Iterable" in out
    _compiles(out)
    ns = _exec_module(out)
    import collections
    import collections.abc as cabc

    assert ns["OrderedDict"] is collections.OrderedDict
    assert ns["Iterable"] is cabc.Iterable


def test_multi_name_all_abc_moves_all():
    src = "from collections import Iterable, Mapping\n"
    out = modernize_python_source(src)
    assert out == "from collections.abc import Iterable, Mapping\n"
    _compiles(out)


def test_non_abc_only_import_untouched():
    src = "from collections import OrderedDict, defaultdict\n"
    out = modernize_python_source(src)
    assert out == src


def test_indent_and_trailing_comment_preserved():
    src = (
        "def f():\n"
        "    from collections import Iterable  # legacy\n"
        "    return Iterable\n"
    )
    out = modernize_python_source(src)
    assert out == (
        "def f():\n"
        "    from collections.abc import Iterable  # legacy\n"
        "    return Iterable\n"
    )
    _compiles(out)


# --- collections.<ABC> attribute access ------------------------------------


def test_attribute_access_rewritten_and_import_added():
    src = (
        "import collections\n"
        "\n"
        "def check(x):\n"
        "    return isinstance(x, collections.Mapping)\n"
    )
    out = modernize_python_source(src)
    assert "collections.abc.Mapping" in out
    assert "import collections.abc" in out
    _compiles(out)
    ns = _exec_module(out)
    assert ns["check"]({}) is True
    assert ns["check"](5) is False


def test_attribute_access_adds_import_after_docstring():
    src = (
        '"""Module docstring."""\n'
        "import collections\n"
        "V = collections.Iterable\n"
    )
    out = modernize_python_source(src)
    assert "collections.abc.Iterable" in out
    _compiles(out)
    # docstring still first line
    assert out.splitlines()[0] == '"""Module docstring."""'


def test_attribute_access_existing_collections_abc_import_not_duplicated():
    src = (
        "import collections\n"
        "import collections.abc\n"
        "V = collections.Mapping\n"
    )
    out = modernize_python_source(src)
    assert out.count("import collections.abc") == 1
    assert "collections.abc.Mapping" in out
    _compiles(out)


# --- docstring / string / comment safety -----------------------------------


def test_text_inside_docstring_not_modified():
    src = (
        '"""Historically this used basestring and did\n'
        "`from collections import Iterable` in old code.\n"
        '"""\n'
        "VALUE = 1\n"
    )
    out = modernize_python_source(src)
    assert out == src  # nothing outside the docstring to change


def test_string_literal_mentioning_import_not_modified():
    src = 's = "from collections import Iterable"\n'
    out = modernize_python_source(src)
    assert out == src


def test_comment_mentioning_import_not_modified():
    src = "# from collections import Iterable\nx = 1\n"
    out = modernize_python_source(src)
    assert out == src


def test_basestring_in_docstring_left_untouched():
    src = '"""uses basestring here"""\nY = 2\n'
    out = modernize_python_source(src)
    assert out == src
    assert "basestring" in out


# --- idempotency ------------------------------------------------------------


def test_idempotent_import():
    src = "from collections import Iterable, OrderedDict\n"
    once = modernize_python_source(src)
    twice = modernize_python_source(once)
    assert once == twice


def test_idempotent_attribute_access():
    src = "import collections\nV = collections.Mapping\n"
    once = modernize_python_source(src)
    twice = modernize_python_source(once)
    assert once == twice


def test_already_modern_unchanged():
    src = "from collections.abc import Iterable\nimport collections.abc\n"
    out = modernize_python_source(src)
    assert out == src


# --- fallback / robustness --------------------------------------------------


def test_unparseable_input_returned_unchanged():
    src = "def broken(:\n    pass\n"  # syntax error
    out = modernize_python_source(src)
    assert out == src


def test_empty_input():
    assert modernize_python_source("") == ""


# --- inspect.getargspec / time.clock ---------------------------------------


def test_inspect_getargspec_renamed():
    src = "import inspect\nspec = inspect.getargspec(f)\n"
    out = modernize_python_source(src)
    assert "inspect.getfullargspec(" in out
    assert "inspect.getargspec(" not in out


def test_time_clock_renamed():
    src = "import time\nt = time.clock()\n"
    out = modernize_python_source(src)
    assert "time.perf_counter(" in out
    assert "time.clock(" not in out


def test_call_rename_inside_string_not_modified():
    src = 's = "time.clock()"\n'
    out = modernize_python_source(src)
    assert out == src
