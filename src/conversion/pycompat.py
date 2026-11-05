"""Safe, mechanical Python 3.10/3.11 compatibility modernization for bundled
dependency source.

The test-conversion feature bundles a test's custom-library dependencies into
the downloadable project VERBATIM (Option A — copied byte-for-byte, never sent
through the LLM). That faithfully preserves behaviour, but legacy-but-still-
Python-3 code can carry pre-3.10 constructs that break on modern interpreters,
e.g.::

    from collections import Iterable
    ImportError: cannot import name 'Iterable' from 'collections'

``modernize_python_source`` applies ONLY safe, universally-correct, behaviour-
preserving transforms so bundled deps import cleanly on Python 3.10/3.11+:

  * ``from collections import <ABC>`` -> ``from collections.abc import <ABC>``
    for the ABCs that moved in 3.10 (splitting mixed imports so non-ABC names
    such as ``OrderedDict`` stay on ``collections``);
  * ``collections.<ABC>`` attribute access -> ``collections.abc.<ABC>``
    (adding ``import collections.abc`` when needed);
  * ``inspect.getargspec(`` -> ``inspect.getfullargspec(``;
  * ``time.clock(`` -> ``time.perf_counter(``.

It is NOT an LLM rewrite and NEVER changes program logic. Design guarantees:

  * AST-aware — only genuine import statements / attribute accesses / calls are
    rewritten, never matches inside strings, comments or docstrings;
  * the rest of the file is preserved byte-for-byte (same formatting, comments,
    blank lines) except the transformed lines;
  * idempotent — running it twice yields the same output;
  * best-effort with a safe fallback — on any error (including unparseable
    input) the original text is returned unchanged, so a file that cannot be
    safely transformed is still bundled verbatim.
"""
from __future__ import annotations

import ast
import re

__all__ = ["modernize_python_source", "MOVED_COLLECTIONS_ABCS"]


# The ABCs that moved from ``collections`` to ``collections.abc`` and were
# finally removed from ``collections`` in Python 3.10. Only these names are
# ever rewritten; every other ``collections`` member (OrderedDict, defaultdict,
# namedtuple, deque, Counter, ChainMap, UserDict, ...) is left on ``collections``.
MOVED_COLLECTIONS_ABCS = frozenset(
    {
        "Iterable",
        "Mapping",
        "MutableMapping",
        "Sequence",
        "MutableSequence",
        "Set",
        "MutableSet",
        "Callable",
        "Hashable",
        "Container",
        "Iterator",
        "Reversible",
        "Collection",
        "Awaitable",
        "Coroutine",
        "AsyncIterable",
        "AsyncIterator",
        "AsyncGenerator",
        "Generator",
        "KeysView",
        "ItemsView",
        "ValuesView",
        "ByteString",
    }
)

# Simple, unambiguous call-site renames: dotted-attribute call -> replacement.
_CALL_RENAMES = {
    ("inspect", "getargspec"): "inspect.getfullargspec",
    ("time", "clock"): "time.perf_counter",
}


def modernize_python_source(text: str) -> str:
    """Return ``text`` with safe Python 3.10/3.11 compatibility transforms
    applied. Behaviour-preserving, idempotent, and never raises: on any error
    the original ``text`` is returned unchanged (fallback to verbatim)."""
    try:
        return _modernize(text)
    except Exception:
        # Best-effort only — a file that cannot be safely transformed is still
        # bundled verbatim rather than dropped or corrupted.
        return text


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


def _modernize(text: str) -> str:
    tree = ast.parse(text)

    # Split into lines, preserving the exact original line endings is not
    # required (Python source is normalised on read); we rebuild with "\n"
    # between the recorded physical lines. splitlines(keepends=True) keeps the
    # terminators so untouched lines round-trip byte-for-byte.
    lines = text.splitlines(keepends=True)

    # Map 1-based line number -> replacement text for that physical line (full
    # line content WITHOUT its terminator). Collected from AST node positions so
    # only real statements are ever considered.
    line_replacements: dict[int, str] = {}
    need_collections_abc_import = False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            repl = _rewrite_import_from(node, lines)
            if repl is not None:
                line_replacements[node.lineno] = repl

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if _is_moved_collections_attr(node):
                need_collections_abc_import = (
                    _rewrite_collections_attr(node, lines, line_replacements)
                    or need_collections_abc_import
                )
        elif isinstance(node, ast.Call):
            _rewrite_call(node, lines, line_replacements)

    if not line_replacements and not need_collections_abc_import:
        return text

    # Apply single-line replacements, preserving each line's original terminator.
    for lineno, new_body in line_replacements.items():
        idx = lineno - 1
        original = lines[idx]
        terminator = _line_terminator(original)
        lines[idx] = new_body + terminator

    result = "".join(lines)

    if need_collections_abc_import and not _has_collections_abc_import(tree):
        result = _insert_collections_abc_import(result)

    return result


def _line_terminator(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    if line.endswith("\r"):
        return "\r"
    return ""


def _strip_terminator(line: str) -> str:
    return line[: len(line) - len(_line_terminator(line))]


# --- from collections import <ABC> -----------------------------------------


def _rewrite_import_from(node: ast.ImportFrom, lines: list[str]) -> str | None:
    """Return the replacement text (single physical line) for a
    ``from collections import ...`` statement that imports moved ABC names, or
    ``None`` if the node needs no change / cannot be safely rewritten.

    Only plain, single-physical-line, absolute ``from collections import a, b``
    statements are handled. Multi-line (parenthesised / backslash-continued) or
    aliased (``as``) forms are conservatively left unchanged by returning None.
    """
    if node.module != "collections" or node.level != 0:
        return None

    names = list(node.names)
    if any(alias.asname is not None for alias in names):
        # Aliased ABC imports are rare; skip to stay safe.
        return None

    abc_names = [a.name for a in names if a.name in MOVED_COLLECTIONS_ABCS]
    if not abc_names:
        return None

    # Only rewrite a statement that occupies a single physical line, so we can
    # replace it precisely without disturbing surrounding formatting.
    end_lineno = getattr(node, "end_lineno", node.lineno)
    if end_lineno != node.lineno:
        return None

    original_line = _strip_terminator(lines[node.lineno - 1])
    indent = original_line[: len(original_line) - len(original_line.lstrip())]

    other_names = [a.name for a in names if a.name not in MOVED_COLLECTIONS_ABCS]

    if not other_names:
        # Pure ABC import: swap only the module on the existing line so leading
        # indentation and any trailing comment are preserved byte-for-byte.
        new = re.sub(
            r"\bfrom\s+collections\s+import\b",
            "from collections.abc import",
            original_line,
            count=1,
        )
        return new

    # Mixed import: keep non-ABC names on ``collections`` and move the ABC names
    # to ``collections.abc`` on the same physical line (via a semicolon) so the
    # line count is preserved. Trailing comments are dropped for this rarer
    # split form — the resulting statement is semantically identical.
    keep_import = f"from collections import {', '.join(other_names)}"
    return f"{indent}{keep_import}; from collections.abc import {', '.join(abc_names)}"


# --- collections.<ABC> attribute access ------------------------------------


def _is_moved_collections_attr(node: ast.Attribute) -> bool:
    return (
        isinstance(node.value, ast.Name)
        and node.value.id == "collections"
        and node.attr in MOVED_COLLECTIONS_ABCS
    )


def _rewrite_collections_attr(
    node: ast.Attribute, lines: list[str], line_replacements: dict[int, str]
) -> bool:
    """Rewrite a ``collections.<ABC>`` access to ``collections.abc.<ABC>`` in
    place on its physical line. Returns True if a rewrite was applied (so the
    caller knows an ``import collections.abc`` may be required)."""
    # Only handle single-line occurrences we can locate precisely.
    if getattr(node, "end_lineno", node.lineno) != node.lineno:
        return False

    lineno = node.lineno
    current = line_replacements.get(lineno)
    if current is None:
        current = _strip_terminator(lines[lineno - 1])

    # Replace exactly ``collections.<ABC>`` not already followed by ``.abc`` and
    # not part of ``collections.abc.<ABC>``. A word-boundary + negative
    # look-behind keeps it from matching ``collections.abc.Iterable`` again
    # (idempotency) or an unrelated longer identifier.
    attr = node.attr
    pattern = re.compile(r"(?<![\w.])collections\.(?!abc\b)" + re.escape(attr) + r"\b")
    new = pattern.sub(f"collections.abc.{attr}", current)
    if new == current:
        return False
    line_replacements[lineno] = new
    return True


# --- inspect.getargspec / time.clock ---------------------------------------


def _rewrite_call(
    node: ast.Call, lines: list[str], line_replacements: dict[int, str]
) -> None:
    func = node.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return
    key = (func.value.id, func.attr)
    replacement = _CALL_RENAMES.get(key)
    if replacement is None:
        return
    if getattr(func, "end_lineno", func.lineno) != func.lineno:
        return

    lineno = func.lineno
    current = line_replacements.get(lineno)
    if current is None:
        current = _strip_terminator(lines[lineno - 1])

    old = f"{func.value.id}.{func.attr}"
    pattern = re.compile(r"(?<![\w.])" + re.escape(old) + r"\b")
    new = pattern.sub(replacement, current, count=1)
    if new != current:
        line_replacements[lineno] = new


# --- import collections.abc insertion --------------------------------------


def _has_collections_abc_import(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "collections.abc":
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "collections" and node.level == 0:
                if any(a.name == "abc" for a in node.names):
                    return True
    return False


def _insert_collections_abc_import(source: str) -> str:
    """Insert ``import collections.abc`` so rewritten ``collections.abc.<ABC>``
    accesses resolve. Placed right after an existing ``import collections`` line
    when present (keeping related imports together); otherwise before the first
    non-comment, non-docstring, non-blank line."""
    lines = source.splitlines(keepends=True)

    # Prefer placing it next to an existing bare ``import collections``.
    for idx, line in enumerate(lines):
        if re.match(r"^\s*import\s+collections\s*(#.*)?$", line):
            indent = line[: len(line) - len(line.lstrip())]
            terminator = _line_terminator(line) or "\n"
            lines.insert(idx + 1, f"{indent}import collections.abc{terminator}")
            return "".join(lines)

    insert_at = _first_statement_index(source)
    terminator = "\n"
    if lines and _line_terminator(lines[0]):
        terminator = _line_terminator(lines[0])
    lines.insert(insert_at, f"import collections.abc{terminator}")
    return "".join(lines)


def _first_statement_index(source: str) -> int:
    """Index (0-based) of the first real statement line, skipping a leading
    module docstring, comments and blank lines, so an inserted import lands
    after the docstring rather than before it."""
    tree = ast.parse(source)
    body = tree.body
    start = 0
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        # Skip past the module docstring.
        start = getattr(body[0], "end_lineno", body[0].lineno)
    if len(body) > 1 or (body and start):
        # Place before the first statement after the docstring (or the first
        # statement if there is no docstring).
        if start:
            return start  # 0-based index == line number after the docstring
        return body[0].lineno - 1
    # Fallback: before everything.
    return start
