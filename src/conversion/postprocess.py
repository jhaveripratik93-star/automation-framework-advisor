"""postprocess.py — Deterministic, LLM-independent guards applied to converted code.

This module is a safety net for the non-deterministic behaviour of the LLM
backends (Gemini on Streamlit, MCP sampling on the server). The prompt rules
in config/conversion_prompts/ are the PRIMARY fix for the smells below; these
guards guarantee the result even when a backend ignores those rules on a given
run. Both run (independently, in any order — they touch disjoint constructs)
for python Playwright conversions.

Two guards live here:

- ``strip_unused_browser_fixture`` — removes a provably UNUSED
  pytest-playwright browser fixture from converted Python/pytest code.
- ``rewrite_importerror_stub_fallback`` — rewrites a ``try: from <mod> import
  <Name> / except ImportError: class <Name>: ...NotImplementedError...``
  TODO-stub fallback into a direct top-level import, so a bundled dependency is
  imported for real instead of being silently shadowed by a stub.

Smell it targets
----------------
A Robot Framework REST/API/infrastructure test (no UI) is sometimes converted
to pytest with every test function given a ``page: Page`` parameter plus
``from playwright.sync_api import Page``. The unused ``page`` makes
pytest-playwright launch Chromium at setup, so the test fails with
``BrowserType.launch: Executable doesn't exist`` even though it never touches a
browser.

Heuristic (deliberately conservative — favours leaving code untouched over any
risk of breaking a genuine UI test)
-----------------------------------
1. Parse the code with ``ast.parse``. On ``SyntaxError`` the code is returned
   UNCHANGED (never corrupt un-parseable output).
2. Decide whether the FILE shows any genuine browser/UI usage by scanning for
   browser call/attribute patterns (``.goto(``, ``.click(``, ``.fill(``,
   ``.locator(``, ``.get_by_*(``, ``.wait_for_*(``, ``.screenshot(``,
   ``.set_input_files(``, ``.type(``, ``.press(``, ``.select_option(``,
   ``.check(``, ``.hover(``, ``.focus(``, ``.tap(``, any ``page.`` member
   access, and ``expect(`` used on a page/locator). If ANY genuine browser
   usage exists anywhere in the module, make NO changes at all.
3. Otherwise, for each top-level or nested ``def test_*`` function whose
   signature declares a parameter literally named ``page`` and whose body NEVER
   references the name ``page``, remove ONLY that parameter from the signature.
4. After the removals, if the module no longer references the names ``Page``
   and/or ``expect`` anywhere, prune those names from any
   ``from playwright.sync_api import ...`` statement; drop the import line
   entirely if it becomes empty.

The transform edits only the specific signature lines and the one import line,
leaving every other line (including comments, blank lines, and all assertions)
byte-for-byte intact. It is idempotent: running it twice yields the same output
as running it once, and input with nothing provably-unused to remove is
returned unchanged.
"""
from __future__ import annotations

import ast
import re

__all__ = ["strip_unused_browser_fixture", "rewrite_importerror_stub_fallback"]

# Attribute/call patterns that indicate genuine browser/UI interaction. If any
# of these appears anywhere in the file, the guard makes no changes.
_BROWSER_USAGE_RE = re.compile(
    r"\.goto\(|\.click\(|\.fill\(|\.locator\(|\.get_by_\w+\(|\.wait_for_\w+\(|"
    r"\.screenshot\(|\.set_input_files\(|\.type\(|\.press\(|\.select_option\(|"
    r"\.check\(|\.uncheck\(|\.hover\(|\.focus\(|\.tap\(|\.dblclick\(|"
    r"\.inner_text\(|\.text_content\(|\.input_value\(|\.is_visible\(|"
    r"\bpage\."
)
# ``expect(<something>)`` applied to a page/locator chain — a web-first
# assertion. Backend value checks never look like this.
_EXPECT_ON_PAGE_RE = re.compile(r"\bexpect\(\s*(?:page\b|[\w.]*\.(?:locator|get_by_)\w*)")


def _file_has_browser_usage(code: str) -> bool:
    return bool(_BROWSER_USAGE_RE.search(code) or _EXPECT_ON_PAGE_RE.search(code))


def _is_test_function(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")


def _body_references_name(node: ast.FunctionDef, name: str) -> bool:
    """True if `name` is read anywhere in the function body (not its signature)."""
    for child in node.body:
        for sub in ast.walk(child):
            if isinstance(sub, ast.Name) and sub.id == name:
                return True
            # attribute access like `page.foo` lands as Name(page) above; this
            # catches any other Load/Store of the bare name.
    return False


def _name_used_outside_import(tree: ast.AST, name: str) -> bool:
    """True if `name` appears as a referenced Name anywhere (ignores the import
    statement's alias binding itself)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            return True
    return False


def _remove_param_from_def_lines(lines: list[str], node: ast.FunctionDef) -> None:
    """In-place edit `lines` to drop the `page` parameter from `node`'s
    signature. Operates only on the signature span (def line through the line
    containing the closing paren of the argument list)."""
    start = node.lineno - 1  # 0-indexed
    # The body's first statement starts after the signature; the signature
    # header spans from `start` up to (but excluding) the first body line.
    body_lineno = node.body[0].lineno if node.body else node.lineno
    end = body_lineno - 1  # 0-indexed line where body begins
    header = "\n".join(lines[start:end])

    new_header = _drop_page_param_from_header(header)
    if new_header is not None and new_header != header:
        new_lines = new_header.split("\n")
        lines[start:end] = new_lines


# Matches a `page` parameter (optionally `page: Type`) as a standalone argument.
_PAGE_PARAM_RE = re.compile(
    r"""
    (?P<lead>\(|,)      # preceding '(' or ','
    \s*
    page                # the parameter name
    (?:\s*:\s*[^,\)]+)? # optional type annotation up to next , or )
    \s*
    (?=,|\))            # followed by ',' or ')'
    """,
    re.VERBOSE,
)


def _drop_page_param_from_header(header: str) -> str | None:
    """Remove the `page` parameter from a function-signature header string.

    Returns the rewritten header, or None if `page` was not found as a param.
    Handles `page` as the first arg (`(page, x)` -> `(x)`), a middle/last arg
    (`(a, page, b)` -> `(a, b)`, `(a, page)` -> `(a)`), or the sole arg
    (`(page)` -> `()`).
    """
    if not _PAGE_PARAM_RE.search(header):
        return None

    def _repl(m: re.Match) -> str:
        lead = m.group("lead")
        # If `page` followed `(`, keep the `(` and let the trailing comma (if
        # any) be consumed below. If it followed `,`, drop that comma.
        return lead if lead == "(" else ""

    result = _PAGE_PARAM_RE.sub(_repl, header, count=1)
    # If page was the first param and others follow, we now have `(, x)` ->
    # normalise a leading comma right after '('.
    result = re.sub(r"\(\s*,\s*", "(", result)
    return result


def _prune_import(lines: list[str], tree: ast.AST) -> None:
    """Remove now-unused `Page`/`expect` names from any
    `from playwright.sync_api import ...` line; drop the line if it empties."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "playwright.sync_api":
            continue
        line_idx = node.lineno - 1
        if line_idx >= len(lines):
            continue
        keep: list[str] = []
        for alias in node.names:
            bound = alias.asname or alias.name
            # Prune Page/expect only if nothing else in the module references
            # the bound name. Keep any other import (e.g. sync_playwright).
            if alias.name in ("Page", "expect") and alias.asname is None:
                if _name_used_outside_import(tree, bound):
                    keep.append(alias.name)
            else:
                keep.append(alias.asname and f"{alias.name} as {alias.asname}" or alias.name)
        original = lines[line_idx]
        if len(keep) == len(node.names):
            continue  # nothing pruned
        if keep:
            lines[line_idx] = f"from playwright.sync_api import {', '.join(keep)}"
        else:
            lines[line_idx] = None  # mark for removal


def strip_unused_browser_fixture(code: str) -> str:
    """Remove a provably-unused pytest-playwright browser fixture from converted
    Python code. See the module docstring for the full heuristic.

    Pure and idempotent. Returns `code` unchanged when it does not parse, when
    the file shows genuine browser/UI usage, or when there is nothing
    provably-unused to remove.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    if _file_has_browser_usage(code):
        return code

    # Collect test functions that declare an unused `page` parameter.
    targets: list[ast.FunctionDef] = []
    for node in ast.walk(tree):
        if not _is_test_function(node):
            continue
        arg_names = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
        if "page" in arg_names and not _body_references_name(node, "page"):
            targets.append(node)

    if not targets:
        return code

    lines = code.split("\n")
    # Edit signature lines. Process in descending start line so earlier edits
    # don't shift later nodes' line indices.
    for node in sorted(targets, key=lambda n: n.lineno, reverse=True):
        _remove_param_from_def_lines(lines, node)

    # Re-parse to prune imports against the post-removal reality (no `page`
    # param left means `Page` may now be unused).
    interim = "\n".join(l for l in lines if l is not None)
    try:
        interim_tree = ast.parse(interim)
    except SyntaxError:
        # Our edit somehow broke parsing — be safe and return the original.
        return code

    pruned_lines: list[str | None] = interim.split("\n")
    _prune_import(pruned_lines, interim_tree)

    return "\n".join(l for l in pruned_lines if l is not None)


# ──────────────────────────────────────────────────────────────────────
# Guard 2: rewrite an ImportError TODO-stub fallback into a direct import.
# ──────────────────────────────────────────────────────────────────────
#
# Smell it targets
# ----------------
# The LLM sometimes wraps an import of a bundled custom dependency in a
# defensive try/except that substitutes a NotImplementedError TODO stub when
# the import fails::
#
#     try:
#         from clamps.nifi.nifi import Nifi
#     except ImportError:
#         class Nifi:
#             def get_flow_status(self):
#                 raise NotImplementedError("TODO: Implement clamps.nifi.nifi.Nifi")
#
# When the real module is bundled alongside the test but raises ImportError at
# runtime (e.g. it transitively imports an uninstalled third-party package),
# the except arm silently swaps in the stub, so every test raises
# NotImplementedError instead of surfacing the honest ImportError. Rewriting to
# the direct ``from clamps.nifi.nifi import Nifi`` makes the real dependency
# load — or fails loudly with the true cause.
#
# Heuristic (deliberately conservative — only an unambiguous stub fallback is
# rewritten; a legitimate optional-dependency try/except is left intact)
# ----------------------------------------------------------------------
# For each module-level ``ast.Try`` node, rewrite it to the bare import ONLY
# when ALL of the following hold:
#   1. The try body is exactly ONE ``from <module> import <n1[, n2, …]>``
#      statement (``import x`` form and multi-statement try bodies are left
#      alone — the direct replacement would be ambiguous).
#   2. There is exactly ONE except handler and it catches ``ImportError``
#      (either ``except ImportError`` or a tuple/name that is exactly
#      ``ImportError`` / ``(ImportError,)``). A bare ``except`` or any other
#      exception type is left intact.
#   3. The except handler has no ``name`` binding other than one that is
#      unused, and no ``orelse``/``finalbody`` on the Try (an ``else``/
#      ``finally`` signals real control flow — leave it alone).
#   4. Every statement in the except body is a ``ClassDef`` whose name is one
#      of the imported names, and every such imported name is defined by
#      exactly one stub class.
#   5. Each stub ClassDef is a pure TODO stub: every statement in its body is
#      either a function/async-function whose body consists solely of a
#      docstring and/or ``raise NotImplementedError(...)``, a bare ``pass``, a
#      ``...`` ellipsis, or a docstring. A class that does anything else (real
#      methods, attributes, a different raise) is NOT a stub and the whole Try
#      is left untouched.
# On a match the entire ``try/except`` block is replaced, in the source lines,
# by the single ``from <module> import <names>`` line (original name order and
# aliases preserved), re-indented to the Try's indentation.
#
# Pure and idempotent: a direct import has no Try to match, so running twice
# equals running once. ``SyntaxError`` input is returned unchanged.

_STUB_RAISE_NAME = "NotImplementedError"


def _is_notimplemented_raise(stmt: ast.stmt) -> bool:
    """True if `stmt` is ``raise NotImplementedError`` or
    ``raise NotImplementedError(...)``."""
    if not isinstance(stmt, ast.Raise) or stmt.exc is None:
        return False
    exc = stmt.exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    return isinstance(exc, ast.Name) and exc.id == _STUB_RAISE_NAME


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _is_ellipsis(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    )


def _is_stub_method(node: ast.stmt) -> bool:
    """A method whose body is only a docstring and/or a NotImplementedError
    raise / pass / ellipsis — i.e. a TODO stub, nothing real."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for stmt in node.body:
        if _is_docstring(stmt) or _is_ellipsis(stmt) or isinstance(stmt, ast.Pass):
            continue
        if _is_notimplemented_raise(stmt):
            continue
        return False
    return True


def _is_stub_class(node: ast.stmt) -> bool:
    """A class that is purely a NotImplementedError TODO stub: every body
    statement is a stub method, a docstring, ``pass`` or ``...``."""
    if not isinstance(node, ast.ClassDef):
        return False
    for stmt in node.body:
        if _is_docstring(stmt) or _is_ellipsis(stmt) or isinstance(stmt, ast.Pass):
            continue
        if _is_stub_method(stmt):
            continue
        return False
    return True


def _handler_catches_importerror(handler: ast.ExceptHandler) -> bool:
    """True if `handler` catches exactly ImportError (name or 1-tuple of it)."""
    typ = handler.type
    if isinstance(typ, ast.Name):
        return typ.id == "ImportError"
    if isinstance(typ, ast.Tuple):
        return (
            len(typ.elts) == 1
            and isinstance(typ.elts[0], ast.Name)
            and typ.elts[0].id == "ImportError"
        )
    return False


def _importfrom_names(node: ast.ImportFrom) -> list[str]:
    """Bound names introduced by a ``from X import …`` (alias when present)."""
    return [alias.asname or alias.name for alias in node.names]


def _render_importfrom(node: ast.ImportFrom) -> str:
    """Reconstruct the ``from <module> import a, b as c`` source line,
    preserving original order and aliases."""
    module = ("." * node.level) + (node.module or "")
    parts = [
        alias.name if alias.asname is None else f"{alias.name} as {alias.asname}"
        for alias in node.names
    ]
    return f"from {module} import {', '.join(parts)}"


def _try_is_stub_fallback(node: ast.Try) -> ast.ImportFrom | None:
    """If `node` is a rewritable ImportError→NotImplementedError-stub fallback,
    return its single ``from … import …`` node; otherwise return None."""
    if node.orelse or node.finalbody:
        return None
    if len(node.body) != 1 or not isinstance(node.body[0], ast.ImportFrom):
        return None
    if len(node.handlers) != 1:
        return None
    handler = node.handlers[0]
    if not _handler_catches_importerror(handler):
        return None

    import_node = node.body[0]
    bound = set(_importfrom_names(import_node))

    stub_class_names: set[str] = set()
    for stmt in handler.body:
        if not _is_stub_class(stmt):
            return None
        stub_class_names.add(stmt.name)  # type: ignore[attr-defined]

    # Every bound import name must be shadowed by a stub class, and every stub
    # class must correspond to a bound name — no extra constructs either way.
    if stub_class_names != bound:
        return None
    return import_node


def rewrite_importerror_stub_fallback(code: str) -> str:
    """Rewrite a ``try: from <mod> import <Name> / except ImportError: <stub
    class(es)>`` fallback into the direct ``from <mod> import <Name>`` import.
    See the heuristic above the function for the exact match conditions.

    Pure and idempotent. Returns `code` unchanged when it does not parse, and
    leaves any try/except that is not an unambiguous NotImplementedError-stub
    fallback byte-for-byte intact (protecting legitimate optional-dependency
    guards).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    # Only module-level Try nodes are rewritten (a stub fallback is always at
    # module scope). Collect (try_node, replacement_import) pairs.
    rewrites: list[tuple[ast.Try, ast.ImportFrom]] = []
    for node in tree.body:
        if isinstance(node, ast.Try):
            import_node = _try_is_stub_fallback(node)
            if import_node is not None:
                rewrites.append((node, import_node))

    if not rewrites:
        return code

    lines = code.split("\n")
    # Replace in descending start line so earlier edits don't shift later spans.
    for try_node, import_node in sorted(rewrites, key=lambda p: p[0].lineno, reverse=True):
        start = try_node.lineno - 1  # 0-indexed, first line of `try:`
        end = _node_end_line(try_node)  # 0-indexed, last line of the block
        indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
        lines[start : end + 1] = [indent + _render_importfrom(import_node)]

    return "\n".join(lines)


def _node_end_line(node: ast.AST) -> int:
    """0-indexed last source line spanned by `node` (uses end_lineno, present
    on 3.8+; falls back to the max lineno of any descendant)."""
    end = getattr(node, "end_lineno", None)
    if end is not None:
        return end - 1
    max_line = getattr(node, "lineno", 1)
    for child in ast.walk(node):
        child_line = getattr(child, "end_lineno", None) or getattr(child, "lineno", None)
        if child_line is not None:
            max_line = max(max_line, child_line)
    return max_line - 1
