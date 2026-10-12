"""Pure, stdlib-only library/helper ingestion for auth/transport contract derivation.

Given the exact ``files`` list the converter already receives
(``[{"filename", "content"}, ...]``), this module:

1. parses Robot ``*** Settings ***`` imports (``parse_robot_settings``),
2. resolves each import against entries *in that same list* (``resolve_imports``) —
   zero filesystem/network access,
3. derives a structured, framework-neutral auth/transport contract from the
   resolved helper content (``derive_contract``),
4. renders a budgeted prompt fragment plus an assumption-flagged shared-auth-helper
   spec (``build_auth_helper`` / ``build_library_context``).

Everything here is a pure function of its arguments: no I/O, no network, no code
execution. The design (CENX-neutral) keys on Robot syntax and generic transport
*shapes* only — never on hostnames, realms, or class names from any one project.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RobotImport:
    """A single ``Library`` / ``Resource`` row from a Robot Settings section."""

    kind: str  # "Library" | "Resource"
    target: str  # raw token as written, e.g. "clamps.CenxQueryApi" or "../lib/x.robot"
    alias: Optional[str] = None  # from WITH NAME / AS; None if absent


@dataclass(frozen=True)
class ResolvedImport:
    """An import paired with the uploaded file it resolved to (if any)."""

    imp: RobotImport
    filename: Optional[str] = None  # the matched entry's "filename", or None if unresolved
    content: Optional[str] = None  # the matched entry's "content", or None if unresolved

    @property
    def resolved(self) -> bool:
        return self.filename is not None


@dataclass(frozen=True)
class SecondaryAuthSystem:
    """A distinct, OIDC/Keycloak-shaped token system guarding some service."""

    name: str  # neutral label, e.g. "oidc"
    token_endpoint: Optional[str] = None
    token_request_shape: Optional[str] = None  # "form_body: grant_type=password&client_id=..."
    token_response_field: Optional[str] = None  # "access_token"
    attaches_as: Optional[str] = None  # "bearer_header"
    applies_to: Optional[str] = None  # hint of which service/base-path it guards


@dataclass(frozen=True)
class AuthContract:
    """Structured, framework-neutral auth/transport contract."""

    login_endpoint: Optional[str] = None
    login_method: Optional[str] = None  # "POST" | "GET" | ...
    credential_location: Optional[str] = None  # "query_param" | "json_body" | "form_body" | "empty_body"
    body_shape: Optional[str] = None  # "empty" | "json" | "form" | None
    token_response_field: Optional[str] = None  # e.g. "token", "access_token"
    later_call_auth: Optional[str] = None  # "query_param:token" | "bearer_header" | ...
    base_paths: tuple[str, ...] = ()
    secondary_systems: tuple[SecondaryAuthSystem, ...] = ()
    assumptions: tuple[str, ...] = ()  # labelled placeholder descriptions (never values)

    @property
    def is_empty(self) -> bool:
        return (
            self.login_endpoint is None
            and not self.secondary_systems
            and self.later_call_auth is None
        )


@dataclass(frozen=True)
class AuthHelperSpec:
    """A renderable shared-auth-helper artifact."""

    path: str  # e.g. "lib/auth.js"
    content: str  # rendered module source
    exports: tuple[str, ...] = ()  # e.g. ("login", "keycloakToken")


@dataclass(frozen=True)
class LibraryContext:
    """The budgeted, ready-to-inject result of ingesting a file set."""

    resolved: tuple[ResolvedImport, ...] = ()
    unresolved: tuple[RobotImport, ...] = ()
    aliases: dict[str, str] = field(default_factory=dict)
    contract: AuthContract = field(default_factory=AuthContract)
    prompt_block: str = ""
    helper_spec: Optional[AuthHelperSpec] = None
    notes: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.prompt_block == "" and self.helper_spec is None


# ──────────────────────────────────────────────────────────────────────────
# Robot *** Settings *** parser
# ──────────────────────────────────────────────────────────────────────────

_SECTION_RE = re.compile(r"\*{3}\s*([^*]+?)\s*\*{3}")
_SETTINGS_HEADER_RE = re.compile(r"\*{3}\s*settings\s*\*{3}", re.I)
_CELL_SPLIT_RE = re.compile(r"\s{2,}|\t+")


def parse_robot_settings(content: str) -> list[RobotImport]:
    """Parse ``Library`` / ``Resource`` imports from a Robot file's Settings section.

    Pure function of ``content``. Returns ``[]`` for non-Robot files (no
    ``*** Settings ***`` header). Continuation (``...``) lines are joined to the
    previous logical row; comment and blank lines are skipped. Alias is captured
    from ``WITH NAME`` (case-insensitive) with ``AS`` tolerated as a synonym.
    """
    if not isinstance(content, str) or not content:
        return []

    lines = content.splitlines()
    in_settings = False
    logical_rows: list[str] = []
    pending: Optional[str] = None

    def _flush() -> None:
        nonlocal pending
        if pending is not None:
            logical_rows.append(pending)
            pending = None

    for raw in lines:
        stripped = raw.strip()

        # Section header handling (works both in and out of settings mode).
        if _SECTION_RE.match(stripped):
            _flush()
            in_settings = bool(_SETTINGS_HEADER_RE.match(stripped))
            continue

        if not in_settings:
            continue

        if not stripped or stripped.startswith("#"):
            continue

        # Continuation line: fold onto the previous logical row.
        if stripped.startswith("..."):
            cont = stripped[3:].strip()
            if pending is None:
                pending = cont
            elif cont:
                pending = pending + "    " + cont
            continue

        # New logical row begins: flush the previous one.
        _flush()
        pending = stripped

    _flush()

    imports: list[RobotImport] = []
    for row in logical_rows:
        cells = [c for c in _CELL_SPLIT_RE.split(row) if c != ""]
        if len(cells) < 2:
            continue
        kw = cells[0].lower()
        if kw not in ("library", "resource"):
            continue
        kind = "Library" if kw == "library" else "Resource"
        target = cells[1]

        alias = None
        rest = cells[2:]
        i = 0
        while i < len(rest):
            tok = rest[i].lower()
            # "WITH NAME" is two cells; "AS" is one. Both mark the next cell as alias.
            if tok == "with name" and i + 1 < len(rest):
                alias = rest[i + 1]
                break
            if tok == "with" and i + 1 < len(rest) and rest[i + 1].lower() == "name" and i + 2 < len(rest):
                alias = rest[i + 2]
                break
            if tok == "as" and i + 1 < len(rest):
                alias = rest[i + 1]
                break
            i += 1

        imports.append(RobotImport(kind=kind, target=target, alias=alias))

    return imports


# ──────────────────────────────────────────────────────────────────────────
# Resolver (uploaded set only — zero I/O)
# ──────────────────────────────────────────────────────────────────────────

_MAX_RESOLVED_FILES = 25
_CLASS_DEF_RE = re.compile(r"^\s*class\s+(\w+)\b", re.M)


def _norm(path: str) -> str:
    """Normalize a filename/target to a POSIX path (handles Windows separators)."""
    return PurePosixPath((path or "").replace("\\", "/")).as_posix()


def _match_by_path_or_basename(target: str, files: list[dict]) -> Optional[dict]:
    """Match when a candidate path ends with ``target`` or their basenames are equal."""
    nt = _norm(target)
    nt_base = PurePosixPath(nt).name
    for f in files:
        fn = f.get("filename")
        if not isinstance(fn, str) or not fn:
            continue
        nf = _norm(fn)
        if nf.endswith(nt) or PurePosixPath(nf).name == nt_base:
            return f
    return None


def _match_dotted_library(target: str, files: list[dict]) -> Optional[dict]:
    """Resolve a dotted ``Library`` token to a .py file by path fragment or class."""
    segments = target.split(".")
    # Path fragments: lower-cased join and verbatim join of the dotted parts.
    frag_lower = "/".join(s.lower() for s in segments)
    frag_verbatim = "/".join(segments)
    fragments = {frag_lower, frag_verbatim}

    for f in files:
        fn = f.get("filename")
        if not isinstance(fn, str) or not fn:
            continue
        nf = _norm(fn)
        if not nf.endswith(".py"):
            continue
        stem = nf[:-3]  # strip ".py"
        # Match either `.../clamps/cenxqueryapi.py` (stem ends with the fragment)
        # or `.../clamps/cenxqueryapi/<module>.py` (fragment is the parent dir).
        parent = stem.rsplit("/", 1)[0] if "/" in stem else stem
        if any(stem.endswith(frag) or parent.endswith(frag) for frag in fragments):
            return f

    # Fall back to: a .py file that defines a class named like the last segment.
    class_name = segments[-1]
    for f in files:
        fn = f.get("filename")
        if not isinstance(fn, str) or not fn:
            continue
        if not _norm(fn).endswith(".py"):
            continue
        content = f.get("content")
        if not isinstance(content, str):
            continue
        for m in _CLASS_DEF_RE.finditer(content):
            if m.group(1) == class_name:
                return f
    return None


def _resolve_one(imp: RobotImport, files: list[dict]) -> Optional[dict]:
    """Resolve a single import to an uploaded file entry (keyed on imp.target)."""
    target = imp.target
    is_dotted_module = (
        imp.kind == "Library"
        and "." in target
        and "/" not in target
        and "\\" not in target
        and not target.lower().endswith(".py")
    )
    if is_dotted_module:
        hit = _match_dotted_library(target, files)
        if hit is not None:
            return hit
        # Dotted but no fragment/class match -> fall through to path matching.
    return _match_by_path_or_basename(target, files)


def resolve_imports(
    imports: list[RobotImport], files: list[dict]
) -> tuple[list[ResolvedImport], list[RobotImport]]:
    """Resolve imports against ``files`` only. Pure; performs ZERO I/O.

    Resolution is bounded-transitive: resolved ``.robot`` resources' own Settings
    imports are followed to a fixpoint, capped at ``_MAX_RESOLVED_FILES``; a
    resolved filename is never processed twice; Python ``import`` statements are
    never followed.
    """
    resolved: list[ResolvedImport] = []
    unresolved: list[RobotImport] = []
    seen_filenames: set[str] = set()
    seen_targets: set[str] = set()

    queue: list[RobotImport] = list(imports)
    while queue:
        if len(resolved) >= _MAX_RESOLVED_FILES:
            logger.info(
                "library ingest: resolution cap reached (%d files)", len(resolved)
            )
            break

        imp = queue.pop(0)
        key = f"{imp.kind}:{imp.target}"
        if key in seen_targets:
            continue
        seen_targets.add(key)

        hit = _resolve_one(imp, files)
        if hit is None:
            unresolved.append(imp)
            continue

        fn = hit.get("filename")
        content = hit.get("content")
        content = content if isinstance(content, str) else ""
        resolved.append(ResolvedImport(imp=imp, filename=fn, content=content))

        if isinstance(fn, str) and fn in seen_filenames:
            continue
        if isinstance(fn, str):
            seen_filenames.add(fn)

        # Bounded-transitive: follow resolved .robot resources' own imports.
        if isinstance(fn, str) and _norm(fn).endswith(".robot"):
            for child in parse_robot_settings(content):
                child_key = f"{child.kind}:{child.target}"
                if child_key not in seen_targets:
                    queue.append(child)

    return resolved, unresolved


# ──────────────────────────────────────────────────────────────────────────
# Auth/transport contract reasoning
# ──────────────────────────────────────────────────────────────────────────

# URL literals inside quotes, keeping only ones that look like request paths/urls.
_URL_LITERAL_RE = re.compile(r"""['"]([^'"\s]*/[^'"\s]*)['"]""")
_VERB_RE = re.compile(r"\b(post|get|put|delete|patch)\b", re.I)
_LOGIN_KEYWORDS = ("login", "session", "authenticate", "token")

_FUNC_DEF_RE = re.compile(r"^\s*(?:def|function|export function|export default function)\b", re.M)


def _is_secondary_shaped(content: str) -> bool:
    """True when a helper carries the OIDC/Keycloak secondary shape."""
    low = content.lower()
    return (
        "grant_type=password" in low
        and "client_id" in low
        and bool(re.search(r"/token\b", low) or "openid-connect/token" in low)
    )


def _find_token_field(content: str) -> Optional[str]:
    """Detect the JSON field read off an auth response, e.g. token / access_token."""
    for pat in (
        r"""\[\s*['"](\w+)['"]\s*\]""",
        r"""\.get\(\s*['"](\w+)['"]""",
        r"""['"](\w+)['"]\s*:""",  # last-resort: field referenced in a dict/obj literal
    ):
        for m in re.finditer(pat, content):
            name = m.group(1)
            if name in ("token", "access_token", "id_token", "accessToken"):
                return name
    return None


def _capture_method_for_url(content: str, url_pos: int) -> Optional[str]:
    """Find the HTTP verb for a URL literal: same statement, else enclosing body."""
    line_start = content.rfind("\n", 0, url_pos) + 1
    line_end = content.find("\n", url_pos)
    if line_end == -1:
        line_end = len(content)
    statement = content[line_start:line_end]
    m = _VERB_RE.search(statement)
    if m:
        return m.group(1).upper()

    # Nearest verb within the enclosing function body (scan backwards for a def).
    body_start = 0
    for fm in _FUNC_DEF_RE.finditer(content[:url_pos]):
        body_start = fm.start()
    window = content[body_start : min(len(content), url_pos + 400)]
    m = _VERB_RE.search(window)
    if m:
        return m.group(1).upper()
    return None


def _path_prefix(url: str) -> str:
    """Strip a scheme/host prefix, keeping the path portion for base-path collection."""
    m = re.match(r"[a-z]+://[^/]+(/.*)", url, re.I)
    return m.group(1) if m else url


def derive_contract(resolved: list[ResolvedImport]) -> AuthContract:
    """Derive a framework-neutral auth/transport contract from resolved helper content.

    Reads resolved helper CONTENT only; every field stays ``None`` unless positively
    detected. Primary vs. secondary routing is deterministic and keyed on *which
    helper* a signal occurs in (never on the field name itself).
    """
    helpers = [r for r in resolved if isinstance(r.content, str) and r.content]

    login_endpoint = None
    login_method = None
    credential_location = None
    body_shape = None
    primary_token_field = None
    later_call_auth = None
    base_paths: list[str] = []
    secondary_systems: list[SecondaryAuthSystem] = []

    # Candidate primary logins: (path, method, first_seen_index).
    primary_candidates: list[tuple[str, Optional[str], int]] = []

    for idx, r in enumerate(helpers):
        content = r.content
        low = content.lower()
        secondary = _is_secondary_shaped(content)

        # Collect URL literals + login-ish candidates.
        for m in _URL_LITERAL_RE.finditer(content):
            url = m.group(1)
            path = _path_prefix(url)
            prefix = path.rsplit("/", 1)[0] if "/" in path else path
            if prefix and prefix not in base_paths:
                base_paths.append(prefix)
            low_url = url.lower()
            if any(k in low_url for k in _LOGIN_KEYWORDS):
                method = _capture_method_for_url(content, m.start())
                if secondary and ("token" in low_url):
                    # Routed to the secondary system, never to the primary login.
                    continue
                primary_candidates.append((path, method, idx))

        # Secondary OIDC/Keycloak system.
        if secondary:
            tok_ep = None
            for m in _URL_LITERAL_RE.finditer(content):
                if "token" in m.group(1).lower():
                    tok_ep = _path_prefix(m.group(1))
                    break
            sec_field = _find_token_field(content)
            attaches = "bearer_header" if re.search(r"authorization['\"]?\s*:?\s*['\"]?\s*bearer", low) else None
            secondary_systems.append(
                SecondaryAuthSystem(
                    name="oidc",
                    token_endpoint=tok_ep,
                    token_request_shape="form_body: grant_type=password&client_id=...",
                    token_response_field=sec_field,
                    attaches_as=attaches,
                )
            )

        # Primary token field: field read in a NON-secondary helper.
        if not secondary:
            tf = _find_token_field(content)
            if tf and primary_token_field is None:
                primary_token_field = tf

        # Credential location + body shape (query-param wins over JSON body).
        if credential_location is None:
            query_param_cred = bool(
                re.search(r"\bparams\s*=", low)
                or "urlencode(" in low
                or re.search(r"\?[^'\"]*username=", low)
            )
            if query_param_cred:
                credential_location = "query_param"
                body_shape = "empty"
            elif re.search(r"\bjson\s*=", low) or "json.dumps" in low:
                if "username" in low:
                    credential_location = "json_body"
                    body_shape = "json"

        # Later-call auth routing: query-param token in a non-secondary helper.
        if not secondary and later_call_auth is None:
            if re.search(r"""\(\s*['"]token['"]\s*,""", content) or re.search(r"token=", low):
                if "token" in low:
                    later_call_auth = "query_param:token"

    # Primary-login selection: prefer login/session over bare authenticate/token.
    def _rank(cand: tuple[str, Optional[str], int]) -> tuple[int, int]:
        p = cand[0].lower()
        strong = 0 if ("login" in p or "session" in p) else 1
        return (strong, cand[2])

    chosen_prefixes: set[str] = set()
    if primary_candidates:
        primary_candidates.sort(key=_rank)
        login_endpoint, login_method, _ = primary_candidates[0]
        chosen_prefixes.add(login_endpoint)
        # Fold any leftover non-primary candidate prefixes into base_paths.
        for path, _, _ in primary_candidates[1:]:
            prefix = path.rsplit("/", 1)[0] if "/" in path else path
            if prefix and prefix not in base_paths:
                base_paths.append(prefix)

    return AuthContract(
        login_endpoint=login_endpoint,
        login_method=login_method,
        credential_location=credential_location,
        body_shape=body_shape,
        token_response_field=primary_token_field,
        later_call_auth=later_call_auth,
        base_paths=tuple(dict.fromkeys(base_paths)),
        secondary_systems=tuple(secondary_systems),
        assumptions=(),
    )


# ──────────────────────────────────────────────────────────────────────────
# Assumption builder
# ──────────────────────────────────────────────────────────────────────────

# Fixed, framework-neutral assumption set: (label, env-var name).
_ASSUMPTIONS: tuple[tuple[str, str], ...] = (
    ("cluster/base FQDN", "BASE_FQDN"),
    ("OIDC client_id", "OIDC_CLIENT_ID"),
    ("auth realm", "OIDC_REALM"),
    ("service -> auth-system mapping", "AUTH_SYSTEM_MAP"),
    ("username", "AUTH_USERNAME"),
    ("password", "AUTH_PASSWORD"),
)


def _comment_marker(target_lang: str) -> str:
    return "//" if target_lang == "javascript" else "#"


def _env_ref(name: str, k6: bool) -> str:
    return f"__ENV.{name}" if k6 else f'os.environ["{name}"]'


def build_assumptions(target_lang: str, *, k6: bool) -> tuple[str, ...]:
    """Render the fixed assumption set as flagged lines (never bare values/secrets)."""
    marker = _comment_marker(target_lang)
    lines: list[str] = []
    for label, env_name in _ASSUMPTIONS:
        ref = _env_ref(env_name, k6)
        lines.append(
            f"{marker} ASSUMPTION — verify against your API/runtime: {label} -> {ref}"
        )
    return tuple(lines)


# ──────────────────────────────────────────────────────────────────────────
# Shared auth-helper renderer (k6/JS only)
# ──────────────────────────────────────────────────────────────────────────


def build_auth_helper(
    contract: AuthContract, target_lang: str, to_name: str
) -> Optional[AuthHelperSpec]:
    """Render a shared auth helper for k6 targets only; else return None.

    Gates on ``to_name`` (not ``target_lang`` alone): k6, Cypress-JS and
    Playwright-JS all resolve to ``target_lang == "javascript"``, so only
    ``"k6" in to_name.lower()`` emits the k6 ``lib/auth.js``. Every runtime value
    is an ``__ENV.*`` reference preceded by an ASSUMPTION marker — never a secret.
    """
    if contract.is_empty:
        return None
    if target_lang != "javascript" or "k6" not in (to_name or "").lower():
        return None

    assumptions = build_assumptions("javascript", k6=True)
    assumption_block = "\n".join(assumptions)
    token_field = contract.token_response_field or "token"
    has_secondary = bool(contract.secondary_systems)

    exports: list[str] = ["login"]
    parts: list[str] = []
    parts.append("// Shared auth helper generated from the imported Robot library layer.")
    parts.append("// Every runtime/config value is an __ENV.* reference you must verify:")
    parts.append(assumption_block)
    parts.append("")
    parts.append("import http from 'k6/http';")
    parts.append("")
    parts.append("const BASE = __ENV.BASE_FQDN;")
    parts.append("")
    # Primary login: POST with credentials as query params, empty body.
    parts.append("export function login() {")
    parts.append(
        "  const params = "
        "`username=${__ENV.AUTH_USERNAME}&password=${__ENV.AUTH_PASSWORD}`;"
    )
    parts.append("  const url = `${BASE}/session/login?${params}`;")
    parts.append("  const res = http.post(url, null);")
    parts.append(f"  return res.json('{token_field}');")
    parts.append("}")

    if has_secondary:
        sec = contract.secondary_systems[0]
        sec_field = sec.token_response_field or "access_token"
        exports.append("keycloakToken")
        parts.append("")
        parts.append("export function keycloakToken() {")
        parts.append(
            "  const body = "
            "`grant_type=password&client_id=${__ENV.OIDC_CLIENT_ID}"
            "&username=${__ENV.AUTH_USERNAME}&password=${__ENV.AUTH_PASSWORD}`;"
        )
        parts.append("  const url = `${BASE}/auth/realms/${__ENV.OIDC_REALM}/protocol/openid-connect/token`;")
        parts.append(
            "  const res = http.post(url, body, "
            "{ headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });"
        )
        parts.append(f"  return res.json('{sec_field}');")
        parts.append("}")

    content = "\n".join(parts) + "\n"
    return AuthHelperSpec(path="lib/auth.js", content=content, exports=tuple(exports))


# ──────────────────────────────────────────────────────────────────────────
# Prompt-block assembly + orchestrator
# ──────────────────────────────────────────────────────────────────────────


def _render_contract_summary(contract: AuthContract) -> str:
    """Render the always-included, structured contract summary."""
    lines: list[str] = ["Derived auth/transport contract (from imported library helpers):"]
    if contract.login_endpoint:
        lines.append(f"- Login endpoint: {contract.login_endpoint}")
    if contract.login_method:
        lines.append(f"- Login method: {contract.login_method}")
    if contract.credential_location:
        lines.append(f"- Credential location: {contract.credential_location}")
    if contract.body_shape:
        lines.append(f"- Login body shape: {contract.body_shape}")
    if contract.token_response_field:
        lines.append(f"- Token response field: {contract.token_response_field}")
    if contract.later_call_auth:
        lines.append(f"- Later-call auth: {contract.later_call_auth}")
    if contract.base_paths:
        lines.append(f"- Base paths: {', '.join(contract.base_paths)}")
    for sec in contract.secondary_systems:
        lines.append(
            f"- Secondary auth system '{sec.name}': token endpoint="
            f"{sec.token_endpoint}, request={sec.token_request_shape}, "
            f"response field={sec.token_response_field}, attaches as={sec.attaches_as}"
        )
    if contract.assumptions:
        lines.append("Assumptions (verify before running):")
        lines.extend(f"  {a}" for a in contract.assumptions)
    return "\n".join(lines)


def _signal_fields(contract: AuthContract) -> list[str]:
    """Field literals/paths that signal a helper contributed to the contract."""
    out: list[str] = []
    for v in (
        contract.login_endpoint,
        contract.token_response_field,
        contract.later_call_auth,
    ):
        if v:
            out.append(v)
    for sec in contract.secondary_systems:
        for v in (sec.token_endpoint, sec.token_response_field):
            if v:
                out.append(v)
    return out


def build_library_context(
    files: list[dict],
    from_name: str,
    to_name: str,
    target_lang: str,
    char_budget: int,
) -> LibraryContext:
    """Orchestrate parse + resolve + contract derivation into a budgeted LibraryContext.

    Returns an empty ``LibraryContext`` for non-Robot, import-less, or no-auth input,
    so callers inject nothing and behave byte-for-byte as before.
    """
    files = files or []
    # Coerce non-string content to "" so everything downstream is a total function.
    norm_files: list[dict] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        content = f.get("content")
        norm_files.append(
            {
                "filename": f.get("filename"),
                "content": content if isinstance(content, str) else "",
            }
        )

    # Parse all Robot Settings across the input, then resolve within the set.
    all_imports: list[RobotImport] = []
    for f in norm_files:
        all_imports.extend(parse_robot_settings(f["content"]))

    if not all_imports:
        return LibraryContext()

    resolved, unresolved = resolve_imports(all_imports, norm_files)

    aliases: dict[str, str] = {}
    for imp in all_imports:
        if imp.alias:
            aliases[imp.alias] = imp.target

    notes: list[str] = []
    for imp in unresolved:
        note = f"Unresolved import: {imp.kind} {imp.target} (not in uploaded file set)"
        notes.append(note)
        logger.info("library ingest: %s", note)

    contract = derive_contract(resolved)
    if contract.is_empty:
        # Nothing auth-related — behave as a no-op.
        return LibraryContext(
            resolved=tuple(resolved),
            unresolved=tuple(unresolved),
            aliases=aliases,
            contract=contract,
            prompt_block="",
            helper_spec=None,
            notes=tuple(notes),
        )

    # Attach the flagged assumptions to the contract for display.
    k6 = target_lang == "javascript" and "k6" in (to_name or "").lower()
    assumptions = build_assumptions(target_lang, k6=k6)
    contract = AuthContract(
        login_endpoint=contract.login_endpoint,
        login_method=contract.login_method,
        credential_location=contract.credential_location,
        body_shape=contract.body_shape,
        token_response_field=contract.token_response_field,
        later_call_auth=contract.later_call_auth,
        base_paths=contract.base_paths,
        secondary_systems=contract.secondary_systems,
        assumptions=assumptions,
    )

    helper_spec = build_auth_helper(contract, target_lang, to_name)

    # ── Assemble the budgeted prompt block ──
    # The always-included contract summary and the fixed trailing sections
    # (import-resolution line, unresolved notes, helper instruction) are counted
    # first; raw helper excerpts fill only the remaining budget. This keeps
    # len(prompt_block) <= max(char_budget, len(fixed_sections)).
    summary = _render_contract_summary(contract)

    # Build the fixed trailing sections up front so their size is reserved.
    trailing_parts: list[str] = [""]
    trailing_parts.append(
        f"Import resolution: {len(resolved)} resolved, {len(unresolved)} unresolved."
    )
    if notes:
        trailing_parts.extend(notes)
    trailing_parts.append("")
    if helper_spec is not None:
        trailing_parts.append(
            f"A shared auth helper ({helper_spec.path}) has been generated from the "
            f"imported library layer; import its exported functions "
            f"({', '.join(helper_spec.exports)}) instead of writing inline login."
        )
    else:
        trailing_parts.append(
            "The derived auth/transport contract below is advisory; implement login "
            "inline per the contract — no shared helper was generated for this target "
            "language."
        )
    trailing = "\n".join(trailing_parts)

    # Reserve summary + trailing + the excerpt-section header from the budget;
    # raw excerpts take whatever remains. The joins ("\n") around the summary,
    # excerpt header, and each excerpt are all accounted for exactly below so the
    # final prompt_block never exceeds char_budget (at any realistic cap where
    # char_budget >= fixed_len).
    _EXCERPT_HEADER_LINES = ["", "Relevant imported helper excerpts:"]
    # summary + "\n" + "\n".join(header_lines) + "\n" + <excerpts> + "\n" + trailing
    fixed_len = (
        len(summary)
        + 1  # newline after summary before header lines
        + len("\n".join(_EXCERPT_HEADER_LINES))
        + 1  # newline before trailing (trailing itself starts with "")
        + len(trailing)
    )
    remaining = max(char_budget - fixed_len, 0)

    # Rank resolved helper excerpts: auth-signal files first, then other resolved.
    signals = _signal_fields(contract)
    signal_files: list[ResolvedImport] = []
    other_files: list[ResolvedImport] = []
    for r in resolved:
        c = r.content or ""
        if signals and any(s in c for s in signals):
            signal_files.append(r)
        else:
            other_files.append(r)

    excerpt_parts: list[str] = []
    used = 0  # chars consumed by excerpts, counting the "\n" join before each
    for r in signal_files + other_files:
        if used >= remaining:
            break
        header = f"# --- {r.filename} ---\n"
        chunk = header + (r.content or "")
        # Each excerpt is preceded by a "\n" join in the final block.
        avail = remaining - used - 1
        if avail <= 0:
            break
        if len(chunk) > avail:
            chunk = chunk[:avail]
        excerpt_parts.append(chunk)
        used += len(chunk) + 1

    block_parts: list[str] = [summary]
    if excerpt_parts:
        block_parts.append("")
        block_parts.append("Relevant imported helper excerpts:")
        block_parts.extend(excerpt_parts)

    # Fixed trailing sections (already built + size-reserved above).
    block_parts.append("")
    block_parts.append(
        f"Import resolution: {len(resolved)} resolved, {len(unresolved)} unresolved."
    )
    if notes:
        block_parts.extend(notes)

    # Helper-instruction variant, chosen by whether a helper was actually generated.
    block_parts.append("")
    if helper_spec is not None:
        block_parts.append(
            f"A shared auth helper ({helper_spec.path}) has been generated from the "
            f"imported library layer; import its exported functions "
            f"({', '.join(helper_spec.exports)}) instead of writing inline login."
        )
    else:
        block_parts.append(
            "The derived auth/transport contract below is advisory; implement login "
            "inline per the contract — no shared helper was generated for this target "
            "language."
        )

    prompt_block = "\n".join(block_parts)

    return LibraryContext(
        resolved=tuple(resolved),
        unresolved=tuple(unresolved),
        aliases=aliases,
        contract=contract,
        prompt_block=prompt_block,
        helper_spec=helper_spec,
        notes=tuple(notes),
    )
