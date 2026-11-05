"""robot_dependency_analyzer.py — offline Robot Framework dependency analyzer.

A self-contained STATIC-ANALYSIS tool. It reads a Robot Framework `.robot`
file (or a directory of them) from disk, parses each test case with the REAL
Robot parser (``robot.api.get_model`` — the same parser ``robot`` uses, never
hand-rolled regex), resolves every ``Library``/``Resource``/``Variables``
import to a file on disk, and reports — per test case — the dependencies a
conversion would need (custom libraries, resources, keywords, variables) plus
a conversion-readiness verdict.

No LLM, no network, no MCP sampling, no ``ctx``. Pure stdlib + ``robot.api``.

The robotframework import is guarded exactly like
``src/conversion/compilers.py::_check_robot`` — if robotframework is not
installed the public functions return a clear error dict instead of crashing.

Public boundary:
  - analyze_file(file_path, source_root="") -> dict
  - analyze_path(input_path, source_root="") -> dict   (file OR directory)

Both return plain JSON-serializable dicts. Internal result types are
``@dataclass`` with ``.to_dict()`` (same pattern as
``compilers.py::CompileResult``).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["analyze_path", "analyze_file", "filter_result_by_test_case"]

# Directory names skipped during the recursive walk — the exact literals used
# by repo_comparator.extract_from_uploaded / server.py::_CONVERT_SKIP_DIRS,
# copied here per the task's explicit allowance to keep this module decoupled
# from the Streamlit/ZIP-bound helper.
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", "target",
}

# Robot Framework standard libraries shipped with RF itself — a bare keyword
# with no library prefix that belongs to one of these is classified "builtin".
_ROBOT_STANDARD_LIBS = {
    "builtin", "collections", "datetime", "dialogs", "easter",
    "operatingsystem", "process", "remote", "screenshot", "string",
    "telnet", "xml",
}

# Well-known external libraries that are NOT project files to port — treated
# as "builtin" (i.e. available off-the-shelf) rather than custom code.
_WELL_KNOWN_EXTERNAL_LIBS = {
    "seleniumlibrary", "requestslibrary", "browser", "appiumlibrary",
    "databaselibrary", "sshlibrary", "restinstance", "jsonlibrary",
    "robotframework-requests",
}

# ``${var}`` reference pattern for best-effort variable dependency extraction.
_VAR_REF_RE = re.compile(r"[$@&]\{([^}{]+)\}")


def _import_get_model():
    """Return robot.api.get_model, or None if robotframework is unavailable.

    Mirrors the ImportError guard in compilers.py::_check_robot so the tool
    degrades to a clear error dict instead of crashing.
    """
    try:
        from robot.api import get_model
    except ImportError:
        logger.info(
            "robot_dependency_analyzer: robotframework not installed — "
            "returning error dict"
        )
        return None
    return get_model


# ──────────────────────────────────────────────────────────────────────
# Result dataclasses (internal) — each exposes .to_dict() for the boundary
# ──────────────────────────────────────────────────────────────────────

@dataclass
class LibraryDep:
    raw_module: str            # e.g. "clamps.nifi.nifi.Nifi"
    alias: str | None          # WITH NAME value, e.g. "Nifi"
    args: list[str]            # positional ctor args, e.g. ["nifi"]
    resolved_path: str | None  # absolute .py path on disk, or None
    exists: bool
    kind: str                  # "builtin" | "custom" | "unresolved"

    def to_dict(self) -> dict:
        return {
            "raw_module": self.raw_module,
            "alias": self.alias,
            "args": list(self.args),
            "resolved_path": self.resolved_path,
            "exists": self.exists,
            "kind": self.kind,
        }


@dataclass
class ResourceDep:
    raw_path: str
    resolved_path: str | None
    exists: bool

    def to_dict(self) -> dict:
        return {
            "raw_path": self.raw_path,
            "resolved_path": self.resolved_path,
            "exists": self.exists,
        }


@dataclass
class VariablesDep:
    raw_path: str
    resolved_path: str | None
    exists: bool

    def to_dict(self) -> dict:
        return {
            "raw_path": self.raw_path,
            "resolved_path": self.resolved_path,
            "exists": self.exists,
        }


@dataclass
class Step:
    keyword: str
    assign: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "assign": list(self.assign),
            "args": list(self.args),
        }


@dataclass
class KeywordUse:
    raw: str                    # e.g. "Nifi.get_flow_status"
    alias: str | None           # library prefix, e.g. "Nifi" (None if bare)
    keyword: str                # e.g. "get_flow_status"
    maps_to_import: str | None  # raw_module of the library it maps to, or None
    classification: str         # "builtin" | "custom" | "unknown"

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "alias": self.alias,
            "keyword": self.keyword,
            "maps_to_import": self.maps_to_import,
            "classification": self.classification,
        }


@dataclass
class TestCaseReport:
    name: str
    documentation: str = ""
    tags: list[str] = field(default_factory=list)
    setup: str | None = None
    teardown: str | None = None
    steps: list[Step] = field(default_factory=list)
    keywords_used: list[KeywordUse] = field(default_factory=list)
    dependencies: dict = field(default_factory=dict)
    conversion_readiness: str = "self_contained"
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "documentation": self.documentation,
            "tags": list(self.tags),
            "setup": self.setup,
            "teardown": self.teardown,
            "steps": [s.to_dict() for s in self.steps],
            "keywords_used": [k.to_dict() for k in self.keywords_used],
            "dependencies": self.dependencies,
            "conversion_readiness": self.conversion_readiness,
            "rationale": self.rationale,
        }


@dataclass
class FileReport:
    file_path: str
    source_root: str
    libraries: list[LibraryDep] = field(default_factory=list)
    resources: list[ResourceDep] = field(default_factory=list)
    variables: list[VariablesDep] = field(default_factory=list)
    suite_setup: str | None = None
    suite_teardown: str | None = None
    force_tags: list[str] = field(default_factory=list)
    default_tags: list[str] = field(default_factory=list)
    test_cases: list[TestCaseReport] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "source_root": self.source_root,
            "settings": {
                "libraries": [lib.to_dict() for lib in self.libraries],
                "resources": [r.to_dict() for r in self.resources],
                "variables": [v.to_dict() for v in self.variables],
                "suite_setup": self.suite_setup,
                "suite_teardown": self.suite_teardown,
                "force_tags": list(self.force_tags),
                "default_tags": list(self.default_tags),
            },
            "test_cases": [tc.to_dict() for tc in self.test_cases],
            "error": self.error,
        }


# ──────────────────────────────────────────────────────────────────────
# Import resolution helpers
# ──────────────────────────────────────────────────────────────────────

def _library_kind_for_name(raw_module: str) -> str | None:
    """Classify a Library import by name alone (before disk resolution).

    Returns "builtin" for a robot standard lib or a well-known external
    library, else None (caller must try disk resolution).
    """
    short = raw_module.split(".")[-1].lower()
    full_lower = raw_module.lower()
    if short in _ROBOT_STANDARD_LIBS or full_lower in _ROBOT_STANDARD_LIBS:
        return "builtin"
    if short in _WELL_KNOWN_EXTERNAL_LIBS or full_lower in _WELL_KNOWN_EXTERNAL_LIBS:
        return "builtin"
    return None


def _candidate_roots(robot_file: Path, source_root: Path | None) -> list[Path]:
    """Directories to try as the root of a dotted module path.

    If source_root is given, it is used exclusively. Otherwise start from the
    .robot file's directory and walk UP the parent chain, so a dotted root
    several levels above the test file (the real autotest layout) resolves.
    """
    if source_root is not None:
        return [source_root]
    roots: list[Path] = []
    cur = robot_file.parent
    while True:
        roots.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    return roots


def _resolve_library(
    raw_module: str, robot_file: Path, source_root: Path | None
) -> tuple[str | None, bool, str]:
    """Resolve a dotted Library module path to a .py file on disk.

    ``clamps.nifi.nifi.Nifi`` splits into module path + optional trailing
    class name. We try two interpretations per candidate root:
      1. last segment is a class  -> a/b/nifi.py  (module "clamps.nifi.nifi")
      2. whole path is a module    -> a/b/c/Nifi.py
    Returns (resolved_abs_path_or_None, exists, kind).
    """
    name_kind = _library_kind_for_name(raw_module)
    if name_kind == "builtin":
        return None, False, "builtin"

    segments = raw_module.split(".")
    # Interpretation 1: drop the last segment (treated as the class name).
    module_as_class = segments[:-1] if len(segments) > 1 else segments
    # Interpretation 2: whole dotted path is the module.
    module_full = segments

    interpretations = []
    if module_as_class:
        interpretations.append(module_as_class)
    interpretations.append(module_full)

    for root in _candidate_roots(robot_file, source_root):
        for mod_parts in interpretations:
            # pkg/.../mod.py
            candidate = root.joinpath(*mod_parts).with_suffix(".py")
            if candidate.is_file():
                return str(candidate.resolve()), True, "custom"
            # pkg/.../mod/__init__.py
            pkg_init = root.joinpath(*mod_parts, "__init__.py")
            if pkg_init.is_file():
                return str(pkg_init.resolve()), True, "custom"

    return None, False, "unresolved"


def _resolve_relative(raw_path: str, robot_file: Path) -> tuple[str | None, bool]:
    """Resolve a Resource/Variables path relative to the .robot file's dir.

    RF resolves resource/variable paths relative to the suite file (unlike
    dotted library module paths). Returns (resolved_abs_path_or_None, exists).
    """
    if not raw_path:
        return None, False
    try:
        candidate = (robot_file.parent / raw_path).resolve()
    except (OSError, ValueError):
        return None, False
    return str(candidate), candidate.is_file()


def _split_keyword(raw: str) -> tuple[str | None, str]:
    """Split ``Nifi.get_flow_status`` into (alias="Nifi", keyword="get_flow_status").

    A bare keyword (no dot, e.g. ``Should Contain``) yields (None, raw).
    """
    if "." in raw:
        alias, _, kw = raw.partition(".")
        if alias and kw:
            return alias, kw
    return None, raw


def _normalize_keyword_name(name: str) -> str:
    """Normalize a keyword name for RF-style case/space/underscore-insensitive
    comparison (``Should Contain`` == ``should_contain`` == ``shouldcontain``).
    """
    return name.replace(" ", "").replace("_", "").lower()


# ──────────────────────────────────────────────────────────────────────
# AST visitor
# ──────────────────────────────────────────────────────────────────────

def _build_visitor():
    """Build the ModelVisitor subclass (lazily, after robot import succeeds)."""
    from robot.api.parsing import ModelVisitor

    class _DependencyVisitor(ModelVisitor):
        def __init__(self) -> None:
            self.libraries: list[LibraryDep] = []
            self.resources: list[ResourceDep] = []
            self.variables: list[VariablesDep] = []
            self.suite_setup: str | None = None
            self.suite_teardown: str | None = None
            self.force_tags: list[str] = []
            self.default_tags: list[str] = []
            self.test_cases: list[TestCaseReport] = []

        # ── Settings ──
        def visit_LibraryImport(self, node) -> None:  # noqa: N802
            self.libraries.append(
                LibraryDep(
                    raw_module=node.name or "",
                    alias=getattr(node, "alias", None),
                    args=[str(a) for a in (getattr(node, "args", None) or ())],
                    resolved_path=None,
                    exists=False,
                    kind="unresolved",
                )
            )

        def visit_ResourceImport(self, node) -> None:  # noqa: N802
            self.resources.append(
                ResourceDep(raw_path=node.name or "", resolved_path=None, exists=False)
            )

        def visit_VariablesImport(self, node) -> None:  # noqa: N802
            self.variables.append(
                VariablesDep(raw_path=node.name or "", resolved_path=None, exists=False)
            )

        def visit_SuiteSetup(self, node) -> None:  # noqa: N802
            self.suite_setup = getattr(node, "name", None)

        def visit_SuiteTeardown(self, node) -> None:  # noqa: N802
            self.suite_teardown = getattr(node, "name", None)

        def visit_TestTags(self, node) -> None:  # noqa: N802
            # RF 7.x: Force Tags surfaces as a TestTags node.
            self.force_tags = [str(v) for v in (getattr(node, "values", None) or ())]

        def visit_ForceTags(self, node) -> None:  # noqa: N802
            # Fallback for older RF versions.
            self.force_tags = [str(v) for v in (getattr(node, "values", None) or ())]

        def visit_DefaultTags(self, node) -> None:  # noqa: N802
            self.default_tags = [str(v) for v in (getattr(node, "values", None) or ())]

        # ── Test cases ──
        def visit_TestCase(self, node) -> None:  # noqa: N802
            tc = TestCaseReport(name=node.header.name)
            for child in node.body:
                cname = type(child).__name__
                if cname == "Documentation":
                    tc.documentation = getattr(child, "value", "") or ""
                elif cname == "Tags":
                    tc.tags = [str(v) for v in (getattr(child, "values", None) or ())]
                elif cname == "Setup":
                    tc.setup = getattr(child, "name", None)
                elif cname == "Teardown":
                    tc.teardown = getattr(child, "name", None)
                elif cname == "KeywordCall":
                    tc.steps.append(
                        Step(
                            keyword=child.keyword or "",
                            assign=[str(a) for a in (getattr(child, "assign", None) or ())],
                            args=[str(a) for a in (getattr(child, "args", None) or ())],
                        )
                    )
            self.test_cases.append(tc)
            # Do not generic_visit — steps already collected above.

    return _DependencyVisitor


def _extract_resource_keywords(
    resources: list[ResourceDep], get_model
) -> dict[str, str]:
    """Collect keyword definitions from resolved Resource files on disk.

    Parses each Resource file that resolved to an existing path with the real
    Robot parser and reads the name of every ``Keyword`` the file defines.
    Returns a map of normalized-keyword-name -> owning resource path, so a
    bare (unprefixed) keyword call in the suite can be matched against a
    keyword actually defined in an imported resource (instead of being
    assumed BuiltIn). Parse failures on a resource are skipped silently — a
    bad resource must not break the suite's analysis.
    """
    from robot.api.parsing import ModelVisitor

    class _KeywordDefVisitor(ModelVisitor):
        def __init__(self) -> None:
            self.names: list[str] = []

        def visit_Keyword(self, node) -> None:  # noqa: N802
            name = getattr(getattr(node, "header", None), "name", None)
            if name:
                self.names.append(name)

    keyword_owner: dict[str, str] = {}
    for res in resources:
        if not (res.exists and res.resolved_path):
            continue
        try:
            model = get_model(res.resolved_path)
            visitor = _KeywordDefVisitor()
            visitor.visit(model)
        except Exception as exc:  # noqa: BLE001 — a bad resource must not abort analysis
            logger.info(
                "robot_dependency_analyzer: could not parse resource %s (%s)",
                res.resolved_path, exc,
            )
            continue
        for name in visitor.names:
            keyword_owner.setdefault(_normalize_keyword_name(name), res.resolved_path)
    return keyword_owner


# ──────────────────────────────────────────────────────────────────────
# Classification + readiness
# ──────────────────────────────────────────────────────────────────────

def _index_libraries(libraries: list[LibraryDep]) -> dict[str, LibraryDep]:
    """Map every reachable name (alias + short class name) to its LibraryDep."""
    index: dict[str, LibraryDep] = {}
    for lib in libraries:
        if lib.alias:
            index[lib.alias] = lib
        short = lib.raw_module.split(".")[-1]
        if short:
            index.setdefault(short, lib)
    return index


def _classify_keyword(
    step_keyword: str,
    lib_index: dict[str, LibraryDep],
    resource_keywords: dict[str, str] | None = None,
) -> KeywordUse:
    resource_keywords = resource_keywords or {}
    alias, keyword = _split_keyword(step_keyword)
    if alias is not None:
        lib = lib_index.get(alias)
        if lib is not None:
            if lib.kind == "custom":
                classification = "custom"
            elif lib.kind == "builtin":
                classification = "builtin"
            else:  # unresolved
                classification = "unknown"
            return KeywordUse(
                raw=step_keyword,
                alias=alias,
                keyword=keyword,
                maps_to_import=lib.raw_module,
                classification=classification,
            )
        # Prefixed, but the prefix matches no known import.
        return KeywordUse(
            raw=step_keyword, alias=alias, keyword=keyword,
            maps_to_import=None, classification="unknown",
        )
    # Bare keyword. Cross-check against keywords actually defined in resolved
    # Resource files first — a resource-defined keyword called without a
    # prefix is a CUSTOM dependency, not a BuiltIn one.
    owner = resource_keywords.get(_normalize_keyword_name(keyword))
    if owner is not None:
        return KeywordUse(
            raw=step_keyword, alias=None, keyword=keyword,
            maps_to_import=owner, classification="custom",
        )
    # Otherwise treat it as a BuiltIn/standard-library keyword (the common
    # case for unprefixed calls like Should Contain / Sleep).
    return KeywordUse(
        raw=step_keyword, alias=None, keyword=keyword,
        maps_to_import=None, classification="builtin",
    )


def _dedupe_keyword_uses(uses: list[KeywordUse]) -> list[KeywordUse]:
    seen: set[str] = set()
    out: list[KeywordUse] = []
    for u in uses:
        if u.raw not in seen:
            seen.add(u.raw)
            out.append(u)
    return out


def _collect_variables(steps: list[Step]) -> tuple[list[str], list[str]]:
    """Return (referenced_vars, assigned_vars) found across a test's steps.

    ``assign`` targets (``${status}=``) are treated as locally-defined; any
    ``${var}`` in args that is not locally assigned is an external reference.
    """
    assigned: list[str] = []
    for step in steps:
        for target in step.assign:
            name = target.strip()
            if name.endswith("="):
                name = name[:-1].strip()
            for m in _VAR_REF_RE.finditer(name):
                if m.group(1) not in assigned:
                    assigned.append(m.group(1))
    referenced: list[str] = []
    for step in steps:
        for arg in step.args:
            for m in _VAR_REF_RE.finditer(arg):
                var = m.group(1)
                if var not in assigned and var not in referenced:
                    referenced.append(var)
    return referenced, assigned


def _test_case_dependencies(
    tc: TestCaseReport,
    libraries: list[LibraryDep],
    resources: list[ResourceDep],
) -> tuple[dict, str, str]:
    """Compute the per-test dependency bundle, readiness verdict + rationale."""
    lib_by_module = {lib.raw_module: lib for lib in libraries}
    resource_by_path = {
        r.resolved_path: r for r in resources if r.resolved_path
    }

    custom_libs: list[str] = []          # library-module customs
    custom_resources: list[str] = []     # resource-file customs (bare kw match)
    blockers: list[str] = []
    touches_unknown = False

    for ku in tc.keywords_used:
        if ku.classification == "custom" and ku.maps_to_import:
            if ku.maps_to_import in lib_by_module:
                if ku.maps_to_import not in custom_libs:
                    custom_libs.append(ku.maps_to_import)
            elif ku.maps_to_import in resource_by_path:
                if ku.maps_to_import not in custom_resources:
                    custom_resources.append(ku.maps_to_import)
            else:
                # custom keyword whose owner is neither a known library nor a
                # resolved resource — still a custom dependency to track.
                if ku.maps_to_import not in custom_libs:
                    custom_libs.append(ku.maps_to_import)
        elif ku.classification == "unknown":
            touches_unknown = True

    # Any custom library this test touches that did not resolve on disk is a
    # blocker; a resolved-but-custom library means "needs custom libs".
    missing_custom: list[str] = []
    resolved_custom: list[str] = []
    for module in custom_libs:
        lib = lib_by_module.get(module)
        if lib is not None and lib.exists:
            resolved_custom.append(module)
        else:
            missing_custom.append(module)

    for ku in tc.keywords_used:
        if ku.classification == "unknown":
            if ku.alias:
                blockers.append(
                    f"keyword '{ku.raw}' uses prefix '{ku.alias}' that maps to "
                    f"no resolvable import"
                )
            else:
                blockers.append(f"keyword '{ku.raw}' could not be classified")
    for module in missing_custom:
        blockers.append(f"custom library '{module}' did not resolve to a file on disk")

    referenced_vars, assigned_vars = _collect_variables(tc.steps)

    # Resource dependencies that failed to resolve are also blockers.
    missing_resources = [r.raw_path for r in resources if not r.exists]
    for r in missing_resources:
        blockers.append(f"resource '{r}' did not resolve to a file on disk")

    dependencies = {
        "custom_libraries": custom_libs,
        "resolved_custom_libraries": resolved_custom,
        "missing_custom_libraries": missing_custom,
        "custom_resource_keywords": custom_resources,
        "resources": [r.raw_path for r in resources],
        "variables_referenced": referenced_vars,
        "variables_assigned": assigned_vars,
        "blockers": blockers,
    }

    # Readiness verdict.
    if blockers or touches_unknown:
        readiness = "blocked"
        rationale = (
            "Blocked: " + "; ".join(blockers)
            if blockers
            else "Blocked: uses keyword(s) that could not be classified."
        )
    elif custom_libs or custom_resources or resources:
        readiness = "needs_custom_libs"
        touched_parts = list(custom_libs) + list(custom_resources)
        touched = ", ".join(touched_parts) if touched_parts else "resource files"
        rationale = (
            f"Needs custom libs: depends on {touched}, all resolved on disk."
        )
    else:
        readiness = "self_contained"
        rationale = "Self-contained: every keyword used is a BuiltIn/standard keyword."

    return dependencies, readiness, rationale


# ──────────────────────────────────────────────────────────────────────
# Public boundary
# ──────────────────────────────────────────────────────────────────────

def analyze_file(file_path: str, source_root: str = "") -> dict:
    """Analyze a single .robot file. Returns a JSON-serializable FileReport dict.

    Parse/IO errors are captured in the returned dict's ``error`` field rather
    than raised, so a bad file never aborts a directory run.
    """
    get_model = _import_get_model()
    if get_model is None:
        return {
            "error": (
                "robotframework is not installed — install it (pip install "
                "robotframework) to analyze .robot dependencies."
            ),
            "file_path": file_path,
        }

    robot_file = Path(file_path)
    root_arg = Path(source_root).resolve() if source_root else None

    report = FileReport(
        file_path=str(robot_file),
        source_root=str(root_arg) if root_arg else str(robot_file.parent.resolve()),
    )

    if not robot_file.is_file():
        report.error = f"file not found: {file_path}"
        return report.to_dict()

    try:
        model = get_model(str(robot_file))
        VisitorCls = _build_visitor()
        visitor = VisitorCls()
        visitor.visit(model)
    except Exception as exc:  # noqa: BLE001 — any parser crash becomes a file error
        report.error = f"parse error: {exc}"
        return report.to_dict()

    report.libraries = visitor.libraries
    report.resources = visitor.resources
    report.variables = visitor.variables
    report.suite_setup = visitor.suite_setup
    report.suite_teardown = visitor.suite_teardown
    report.force_tags = visitor.force_tags
    report.default_tags = visitor.default_tags

    # Resolve imports.
    for lib in report.libraries:
        resolved, exists, kind = _resolve_library(lib.raw_module, robot_file, root_arg)
        lib.resolved_path = resolved
        lib.exists = exists
        lib.kind = kind
    for res in report.resources:
        resolved, exists = _resolve_relative(res.raw_path, robot_file)
        res.resolved_path = resolved
        res.exists = exists
    for var in report.variables:
        resolved, exists = _resolve_relative(var.raw_path, robot_file)
        var.resolved_path = resolved
        var.exists = exists

    lib_index = _index_libraries(report.libraries)
    # Keywords defined in resolved Resource files, so a bare (unprefixed) call
    # to a resource keyword is classified custom rather than assumed BuiltIn.
    resource_keywords = _extract_resource_keywords(report.resources, get_model)

    # Classify keywords + compute readiness per test case.
    for tc in visitor.test_cases:
        uses = [
            _classify_keyword(step.keyword, lib_index, resource_keywords)
            for step in tc.steps
            if step.keyword
        ]
        tc.keywords_used = _dedupe_keyword_uses(uses)
        deps, readiness, rationale = _test_case_dependencies(
            tc, report.libraries, report.resources
        )
        tc.dependencies = deps
        tc.conversion_readiness = readiness
        tc.rationale = rationale

    report.test_cases = visitor.test_cases
    return report.to_dict()


def _iter_robot_files(root: Path) -> list[Path]:
    """Recursively collect .robot files under root, skipping _SKIP_DIRS."""
    return sorted(
        p
        for p in root.rglob("*.robot")
        if p.is_file() and not any(seg in _SKIP_DIRS for seg in p.parts)
    )


def analyze_path(input_path: str, source_root: str = "") -> dict:
    """Analyze a .robot file OR a directory of .robot files (static, offline).

    Returns a top-level JSON-serializable dict:
      input_path, source_root, total_files, total_test_cases,
      readiness_counts, files (list of per-file reports), report (markdown).

    Returns a clear ``{"error": ...}`` dict for a missing path, a directory
    with no .robot files, or when robotframework is unavailable.
    """
    get_model = _import_get_model()
    if get_model is None:
        return {
            "error": (
                "robotframework is not installed — install it (pip install "
                "robotframework) to analyze .robot dependencies."
            ),
            "input_path": input_path,
        }

    path = Path(input_path)
    if not path.exists():
        return {"error": f"path not found: {input_path}", "input_path": input_path}

    if path.is_dir():
        robot_files = _iter_robot_files(path)
        if not robot_files:
            return {
                "error": f"no .robot files found under directory: {input_path}",
                "input_path": input_path,
            }
    elif path.is_file():
        if path.suffix.lower() != ".robot":
            return {
                "error": f"not a .robot file: {input_path}",
                "input_path": input_path,
            }
        robot_files = [path]
    else:
        return {"error": f"unsupported path: {input_path}", "input_path": input_path}

    file_dicts = [analyze_file(str(f), source_root=source_root) for f in robot_files]

    total_test_cases = 0
    readiness_counts = {"self_contained": 0, "needs_custom_libs": 0, "blocked": 0}
    parse_errors = 0
    for fd in file_dicts:
        if fd.get("error"):
            parse_errors += 1
            continue
        for tc in fd.get("test_cases", []):
            total_test_cases += 1
            verdict = tc.get("conversion_readiness", "self_contained")
            readiness_counts[verdict] = readiness_counts.get(verdict, 0) + 1

    resolved_source_root = (
        str(Path(source_root).resolve()) if source_root else ""
    )

    result = {
        "input_path": str(path),
        "source_root": resolved_source_root,
        "total_files": len(file_dicts),
        "total_test_cases": total_test_cases,
        "parse_errors": parse_errors,
        "readiness_counts": readiness_counts,
        "files": file_dicts,
    }
    result["report"] = _render_markdown(result)
    return result


# ──────────────────────────────────────────────────────────────────────
# Markdown report
# ──────────────────────────────────────────────────────────────────────

def _render_markdown(result: dict) -> str:
    lines: list[str] = []
    lines.append("# Robot Framework Dependency Analysis")
    lines.append("")
    lines.append(f"- Input path: `{result['input_path']}`")
    if result.get("source_root"):
        lines.append(f"- Source root: `{result['source_root']}`")
    lines.append(f"- Files analyzed: {result['total_files']}")
    lines.append(f"- Total test cases: {result['total_test_cases']}")
    rc = result["readiness_counts"]
    lines.append(
        f"- Readiness: {rc.get('self_contained', 0)} self-contained, "
        f"{rc.get('needs_custom_libs', 0)} needs custom libs, "
        f"{rc.get('blocked', 0)} blocked"
    )
    if result.get("parse_errors"):
        lines.append(f"- Files with parse errors: {result['parse_errors']}")
    lines.append("")

    for fd in result["files"]:
        lines.append(f"## `{fd['file_path']}`")
        if fd.get("error"):
            lines.append("")
            lines.append(f"**Parse error:** {fd['error']}")
            lines.append("")
            continue

        settings = fd.get("settings", {})
        libs = settings.get("libraries", [])
        if libs:
            lines.append("")
            lines.append("**Library imports:**")
            for lib in libs:
                alias = f" (alias `{lib['alias']}`)" if lib.get("alias") else ""
                args = f" args={lib['args']}" if lib.get("args") else ""
                resolved = lib.get("resolved_path") or "unresolved"
                lines.append(
                    f"- `{lib['raw_module']}`{alias}{args} — kind: "
                    f"**{lib['kind']}**, exists: {lib['exists']} "
                    f"({resolved})"
                )
        resources = settings.get("resources", [])
        if resources:
            lines.append("")
            lines.append("**Resource imports:**")
            for r in resources:
                lines.append(
                    f"- `{r['raw_path']}` — exists: {r['exists']} "
                    f"({r.get('resolved_path') or 'unresolved'})"
                )
        variables = settings.get("variables", [])
        if variables:
            lines.append("")
            lines.append("**Variables imports:**")
            for v in variables:
                lines.append(
                    f"- `{v['raw_path']}` — exists: {v['exists']} "
                    f"({v.get('resolved_path') or 'unresolved'})"
                )

        lines.append("")
        lines.append(f"**Test cases ({len(fd.get('test_cases', []))}):**")
        for tc in fd.get("test_cases", []):
            lines.append("")
            lines.append(f"### {tc['name']}")
            lines.append(f"- Readiness: **{tc['conversion_readiness']}** — {tc['rationale']}")
            if tc.get("tags"):
                lines.append(f"- Tags: {', '.join(tc['tags'])}")
            kws = tc.get("keywords_used", [])
            if kws:
                lines.append("- Keywords used:")
                for ku in kws:
                    mapped = f" → `{ku['maps_to_import']}`" if ku.get("maps_to_import") else ""
                    lines.append(
                        f"  - `{ku['raw']}` ({ku['classification']}){mapped}"
                    )
            deps = tc.get("dependencies", {})
            if deps.get("custom_libraries"):
                lines.append(f"- Custom libraries: {', '.join(deps['custom_libraries'])}")
            if deps.get("custom_resource_keywords"):
                lines.append(
                    f"- Custom resource keywords from: "
                    f"{', '.join(deps['custom_resource_keywords'])}"
                )
            if deps.get("variables_referenced"):
                lines.append(
                    f"- External variables: {', '.join(deps['variables_referenced'])}"
                )
            if deps.get("blockers"):
                lines.append("- Blockers:")
                for b in deps["blockers"]:
                    lines.append(f"  - {b}")
        lines.append("")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# Test-case-name filtering (drives analysis by a test-case name)
# ──────────────────────────────────────────────────────────────────────

def filter_result_by_test_case(result: dict, test_case_name: str) -> dict:
    """Filter an ``analyze_path`` result down to matching test case(s).

    ``test_case_name`` is matched case-insensitively as a SUBSTRING against
    each test case's ``name`` (so ``"typecasting"`` matches
    ``"Verify The TypeCasting Error Messages ..."`` and an exact name also
    matches). Robot test-case names are matched literally by RF, so a literal
    substring on the name is the least-surprising filter here.

    Behavior:
      - If ``test_case_name`` is empty/whitespace, the result is returned
        unchanged (no filtering).
      - If ``result`` carries an ``"error"`` key, it is returned unchanged.
      - Otherwise a shallow copy is produced where each file keeps only the
        test cases whose name contains ``test_case_name``; files left with no
        matching test case are dropped. ``total_files``, ``total_test_cases``
        and ``readiness_counts`` are recomputed from the surviving test cases,
        ``test_case_name`` is echoed onto the dict, and ``report`` is
        regenerated via ``_render_markdown``.

    The input dict is never mutated (file/test-case lists are copied before
    pruning).
    """
    if not test_case_name or not test_case_name.strip():
        return result
    if "error" in result:
        return result

    needle = test_case_name.lower()
    filtered_files: list[dict] = []
    for fd in result.get("files", []):
        # A per-file parse error carries no usable test cases — drop it from a
        # name-filtered view (the unfiltered tool still surfaces parse errors).
        if fd.get("error"):
            continue
        matches = [
            tc
            for tc in fd.get("test_cases", [])
            if needle in (tc.get("name", "") or "").lower()
        ]
        if not matches:
            continue
        new_fd = dict(fd)
        new_fd["test_cases"] = matches
        filtered_files.append(new_fd)

    total_test_cases = 0
    readiness_counts = {"self_contained": 0, "needs_custom_libs": 0, "blocked": 0}
    for fd in filtered_files:
        for tc in fd.get("test_cases", []):
            total_test_cases += 1
            verdict = tc.get("conversion_readiness", "self_contained")
            readiness_counts[verdict] = readiness_counts.get(verdict, 0) + 1

    filtered = dict(result)
    filtered["test_case_name"] = test_case_name
    filtered["total_files"] = len(filtered_files)
    filtered["total_test_cases"] = total_test_cases
    filtered["parse_errors"] = 0
    filtered["readiness_counts"] = readiness_counts
    filtered["files"] = filtered_files
    filtered["report"] = _render_markdown(filtered)
    return filtered
