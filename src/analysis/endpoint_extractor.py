"""endpoint_extractor.py — derive a keyword -> HTTP endpoint map from custom
library source so the converter can translate Robot keyword calls into NATIVE
Playwright APIRequestContext calls instead of importing the legacy client.

This is the raw material that lets the Robot->Playwright engine PREFER a
self-contained native API translation (its new default for API/REST tests)
over bundling the whole clamps/common dependency tree. See
config/conversion_prompts/pair_rules/robot_framework__to__playwright.md.

Design:
  - Pure, Streamlit-free, LLM-free. Static text/AST analysis only — the custom
    library source is READ, never imported or executed (it may do
    ``from collections import Iterable`` and crash on modern Python, which is
    exactly the baggage this translation avoids).
  - Best-effort. When a method's body cannot be reduced to a single direct HTTP
    call, or the source is unreadable, it is simply omitted from the map; the
    converter then falls back to bundling (Option A) for that keyword.

Public boundary:
  - extract_endpoints_from_source(class_source) -> ClientEndpoints
  - build_endpoint_context(analysis, source_root="") -> str
      Walks a robot_dependency_analyzer analysis dict, reads each resolved
      CUSTOM library .py file, and returns a distilled markdown block
      (keyword -> METHOD path + base-url pieces) ready to attach to the
      conversion prompt context. Returns "" when nothing is derivable.
"""
from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "Endpoint",
    "ClientEndpoints",
    "extract_endpoints_from_source",
    "build_endpoint_context",
]

# HTTP verbs we recognize as "this method is ultimately a direct REST call".
_HTTP_VERBS = {"get", "post", "put", "delete", "patch", "head", "options"}

# Attribute chains on ``self`` whose <verb>(...) call means an HTTP request,
# e.g. ``self.rest.get(...)`` / ``self.client.post(...)`` / ``self.session.get``.
_REST_ATTR_NAMES = {"rest", "client", "session", "http", "api", "conn", "connection"}


@dataclass
class Endpoint:
    """One keyword method reduced to a single HTTP call."""

    method_name: str          # python method name, e.g. "get_flow_status"
    http_method: str          # "GET" / "POST" / ...
    path: str                 # path literal passed to the verb, e.g. "flow/status"
    via: str                  # the attribute chain used, e.g. "self.rest.get"

    def to_dict(self) -> dict:
        return {
            "method_name": self.method_name,
            "http_method": self.http_method,
            "path": self.path,
            "via": self.via,
        }


@dataclass
class ClientEndpoints:
    """Everything derivable from one custom-library class needed to translate
    its keywords into native HTTP calls."""

    class_name: str = ""
    endpoints: list[Endpoint] = field(default_factory=list)
    default_port: str | None = None     # from __init__ default, e.g. "7777"
    default_base_path: str | None = None  # from __init__ default, e.g. "/nifi-api"
    https_enabled: bool | None = None   # from the REST client construction
    service_hint: str | None = None     # service/host hint, e.g. "nifi"

    def to_dict(self) -> dict:
        return {
            "class_name": self.class_name,
            "endpoints": [e.to_dict() for e in self.endpoints],
            "default_port": self.default_port,
            "default_base_path": self.default_base_path,
            "https_enabled": self.https_enabled,
            "service_hint": self.service_hint,
        }

    @property
    def is_rest_client(self) -> bool:
        return bool(self.endpoints)


def _literal_of(node: ast.AST) -> str | None:
    """Return a string literal from an arg node, else None.

    Only handles plain string constants — a templated path like
    ``"process-groups/{id}"`` is still a constant literal and is returned
    verbatim; a path built from f-strings/concatenation is intentionally NOT
    reduced (those keywords are better left to the bundling fallback).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _attr_chain(node: ast.AST) -> list[str]:
    """Flatten an attribute/name chain into its parts.

    ``self.rest.get`` -> ["self", "rest", "get"];  ``requests.get`` ->
    ["requests", "get"]. Returns [] for anything that is not a plain
    Name/Attribute chain.
    """
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return []
    parts.reverse()
    return parts


def _call_is_http(func: ast.AST) -> tuple[str, str] | None:
    """If ``func`` is an HTTP-verb call we recognize, return (HTTP_METHOD, via).

    Recognizes:
      - ``self.<attr>.<verb>(...)`` where <attr> is a known REST-client
        attribute name (self.rest.get, self.client.post, ...).
      - ``requests.<verb>(...)`` / ``httpx.<verb>(...)`` (module-level client).
    Returns None otherwise.
    """
    chain = _attr_chain(func)
    if len(chain) < 2:
        return None
    verb = chain[-1].lower()
    if verb not in _HTTP_VERBS:
        return None
    # self.<attr>.<verb>
    if chain[0] == "self" and len(chain) >= 3 and chain[1] in _REST_ATTR_NAMES:
        return verb.upper(), ".".join(chain)
    # requests.<verb> / httpx.<verb>
    if chain[0] in {"requests", "httpx"} and len(chain) == 2:
        return verb.upper(), ".".join(chain)
    return None


def _first_http_call_in(method: ast.FunctionDef) -> tuple[str, str, str] | None:
    """Find the FIRST direct HTTP call in a method body and return
    (HTTP_METHOD, path_literal, via). Returns None when the method does not
    reduce to a single literal-path HTTP call (so it falls back to bundling).
    """
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        http = _call_is_http(node.func)
        if http is None:
            continue
        http_method, via = http
        # First positional arg (if any) is the path. ``self.rest.get()`` with no
        # positional path means path "" (the client's base path only).
        path = ""
        if node.args:
            lit = _literal_of(node.args[0])
            if lit is None:
                # Non-literal path (f-string / variable) — not safely reducible.
                return None
            path = lit
        else:
            # Maybe a keyword ``path=`` argument.
            for kw in node.keywords:
                if kw.arg == "path":
                    lit = _literal_of(kw.value)
                    if lit is None:
                        return None
                    path = lit
        return http_method, path, via
    return None


def _extract_init_defaults(cls: ast.ClassDef) -> tuple[str | None, str | None, bool | None]:
    """Pull (default_port, default_base_path, https_enabled) hints from the
    class ``__init__`` signature and body.

    - Port / base_path: read from ``__init__`` keyword defaults named like
      ``server_port`` / ``*_port`` and ``base_path``.
    - https_enabled: read from a ``...Rest(..., https_enabled=<bool>)`` call or
      an ``https_enabled=`` default; defaults to None (unknown) if absent.
    """
    default_port: str | None = None
    default_base_path: str | None = None
    https_enabled: bool | None = None

    for item in cls.body:
        if not (isinstance(item, ast.FunctionDef) and item.name == "__init__"):
            continue
        args = item.args
        # defaults align to the TAIL of args.args
        posargs = args.args
        defaults = args.defaults
        if defaults:
            offset = len(posargs) - len(defaults)
            for i, default in enumerate(defaults):
                name = posargs[offset + i].arg
                val = _const_value(default)
                if val is None:
                    continue
                if name == "base_path":
                    default_base_path = str(val)
                elif name.endswith("port") and default_port is None:
                    default_port = str(val)
                elif name == "https_enabled":
                    https_enabled = bool(val)
        # kw-only defaults
        for kwarg, default in zip(args.kwonlyargs, args.kw_defaults):
            if default is None:
                continue
            val = _const_value(default)
            if val is None:
                continue
            if kwarg.arg == "base_path":
                default_base_path = str(val)
            elif kwarg.arg.endswith("port") and default_port is None:
                default_port = str(val)
            elif kwarg.arg == "https_enabled":
                https_enabled = bool(val)

        # Look for https_enabled passed into a Rest(...) construction in body.
        for node in ast.walk(item):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "https_enabled":
                        val = _const_value(kw.value)
                        if val is not None:
                            https_enabled = bool(val)
    return default_port, default_base_path, https_enabled


def _const_value(node: ast.AST):
    if isinstance(node, ast.Constant):
        return node.value
    return None


def extract_endpoints_from_source(class_source: str, service_hint: str | None = None) -> ClientEndpoints:
    """Parse one custom-library .py source TEXT and derive its keyword ->
    HTTP endpoint map (best effort). ``service_hint`` is an optional host/service
    name (e.g. the Robot Library ctor arg, or the config service) used to form a
    base URL in the generated native test.

    Never raises on bad input: a syntax error / non-HTTP class yields an empty
    ClientEndpoints (``is_rest_client`` == False), signalling "fall back to
    bundling for this library".
    """
    result = ClientEndpoints(service_hint=service_hint)
    try:
        tree = ast.parse(class_source)
    except SyntaxError as exc:
        logger.info("endpoint_extractor: could not parse source (%s)", exc)
        return result

    # Pick the first class that has at least one HTTP-reducible method.
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        endpoints: list[Endpoint] = []
        for item in cls.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            if item.name.startswith("_"):
                continue  # private helper, not a Robot keyword
            http = _first_http_call_in(item)
            if http is None:
                continue
            http_method, path, via = http
            endpoints.append(
                Endpoint(
                    method_name=item.name,
                    http_method=http_method,
                    path=path,
                    via=via,
                )
            )
        if endpoints:
            port, base_path, https = _extract_init_defaults(cls)
            result.class_name = cls.name
            result.endpoints = endpoints
            result.default_port = port
            result.default_base_path = base_path
            result.https_enabled = https
            return result
    return result


# ──────────────────────────────────────────────────────────────────────
# Prompt-context builder (consumes a robot_dependency_analyzer analysis)
# ──────────────────────────────────────────────────────────────────────

def _iter_resolved_custom_libraries(analysis: dict) -> list[tuple[str, str, str | None]]:
    """Yield (raw_module, resolved_path, service_hint) for each resolved CUSTOM
    library across the analysis, de-duplicated by resolved_path.

    ``service_hint`` is the library's first ctor arg if present (Robot
    ``Library  clamps.nifi.nifi.Nifi  nifi`` -> "nifi"), else the last module
    segment lowercased (a reasonable k8s-service guess, e.g. "nifi").
    """
    seen: set[str] = set()
    out: list[tuple[str, str, str | None]] = []
    for fd in analysis.get("files", []):
        if fd.get("error"):
            continue
        for lib in fd.get("settings", {}).get("libraries", []):
            if lib.get("kind") != "custom" or not lib.get("exists"):
                continue
            resolved = lib.get("resolved_path")
            if not resolved or resolved in seen:
                continue
            seen.add(resolved)
            args = lib.get("args") or []
            hint = args[0] if args else (lib.get("raw_module", "").split(".")[-1].lower() or None)
            out.append((lib.get("raw_module", ""), resolved, hint))
    return out


def _base_url_pieces(client: ClientEndpoints) -> dict:
    scheme = "https" if client.https_enabled else "http"
    return {
        "scheme": scheme,
        "service": client.service_hint,
        "port": client.default_port,
        "base_path": client.default_base_path,
    }


def build_endpoint_context(analysis: dict, source_root: str = "") -> str:
    """Build a distilled 'keyword -> HTTP endpoint' markdown block from the
    resolved custom-library source referenced by an analysis dict.

    This is attached to the conversion prompt context so the model can TRANSLATE
    custom-library keyword calls into native Playwright APIRequestContext calls
    (the preferred strategy for API/REST Robot tests) instead of importing the
    legacy client. The resolved library SOURCE is also included verbatim (capped)
    so the model can reproduce real paths/headers/assertions.

    Returns "" when no resolved custom library reduces to HTTP calls — the
    converter then falls back to bundling (Option A).
    """
    libs = _iter_resolved_custom_libraries(analysis)
    if not libs:
        return ""

    sections: list[str] = []
    source_sections: list[str] = []
    for raw_module, resolved_path, hint in libs:
        try:
            text = Path(resolved_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.info("endpoint_extractor: could not read %s (%s)", resolved_path, exc)
            continue
        client = extract_endpoints_from_source(text, service_hint=hint)
        if not client.is_rest_client:
            continue
        pieces = _base_url_pieces(client)
        lines = [f"### `{raw_module}` (class `{client.class_name}`)"]
        lines.append(
            "- Base URL pieces (reproduce as env-overridable defaults): "
            f"scheme=`{pieces['scheme']}`, service/host=`{pieces['service']}`, "
            f"port=`{pieces['port']}`, base_path=`{pieces['base_path']}`"
        )
        lines.append("- Keyword → native HTTP call:")
        for ep in client.endpoints:
            lines.append(
                f"  - `{ep.method_name}` → `{ep.http_method} "
                f"{pieces['base_path'] or ''}/{ep.path}`"
                f" (via `{ep.via}`)"
            )
        sections.append("\n".join(lines))

        # Include the resolved source verbatim (capped) so paths/headers/auth
        # and the exact assertions the keyword performs are available.
        capped = text if len(text) <= 8000 else text[:8000] + "\n# … (truncated)\n"
        source_sections.append(
            f"### Source of `{raw_module}` (`{resolved_path}`)\n\n"
            f"```python\n{capped}\n```"
        )

    if not sections:
        return ""

    header = (
        "## Custom-library endpoint map (for NATIVE API translation)\n\n"
        "The Robot suite's custom library keywords below are ultimately direct "
        "HTTP/REST calls. PREFER translating each keyword call into a native "
        "Playwright APIRequestContext call using the derived method + path and "
        "the base-URL pieces (env-overridable, same defaults as the source), "
        "skipping on an unreachable backend — do NOT import the custom client "
        "and do NOT require bundling. Only fall back to importing the bundled "
        "client for a keyword that is NOT in this map (its behavior could not "
        "be reduced to a direct HTTP call).\n"
    )
    parts = [header, "\n\n".join(sections)]
    if source_sections:
        parts.append(
            "## Resolved custom-library source (read-only reference)\n\n"
            + "\n\n".join(source_sections)
        )
    return "\n\n".join(parts)
