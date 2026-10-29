Robot Framework → Playwright conversion rules.

## Conversion Strategy for API/REST Tests (ORDERED — follow in order)

For a Robot test whose keywords call a custom library, decide per keyword which
strategy to use. Native API translation is the PREFERRED default; bundling is
the fallback.

STEP 1 — NATIVE API TRANSLATION (preferred, the DEFAULT for API/REST tests):
When a Robot keyword maps to a custom-library method that is ultimately a
direct HTTP/REST call — detectable because the method body calls something like
`self.rest.get/post/put/delete(...)`, `self.client.<verb>(...)`, `requests.*`,
`httpx.*`, or another HTTP client — TRANSLATE that keyword call into a NATIVE
Playwright APIRequestContext call. Do NOT import the custom client and do NOT
require bundling for such a keyword. Concretely:
  - Open an API context: `playwright.request.new_context(base_url=<base_url>)`
    and call `.get(...)` / `.post(...)` / `.put(...)` / `.delete(...)` with the
    REAL path, HTTP method, headers/auth and body the library method used.
  - Reproduce the REAL base URL from the library + its config module: scheme
    (http vs https), host/service name, port, and base path. When a
    "Custom-library endpoint map" is provided in the context below, use its
    derived method/path and base-URL pieces directly.
  - Make every connection setting env-overridable with the SAME default the
    source used (e.g. `NIFI_HOSTS`/`NIFI_PORT`/`NIFI_BASE_URL`), via a small
    `resolve_setting`-style helper. Do NOT invent IPs.
  - Assert on the response the SAME way the Robot keyword's assertions did
    (e.g. `Should Contain ${status} controllerStatus` → the response is `ok`
    and its body contains `controllerStatus`).
  - When the backend is unreachable, SKIP with a clear reason (a
    `BackendUnavailable`-style exception / `pytest.skip`) — never fail and never
    produce a false pass.
  - The generated test MUST be self-contained: it MUST NOT import the custom
    `clamps`/`common` client and MUST NOT require the dependency-bundling path.
    This is what avoids the legacy codebase's Python-version baggage.
  - If helpful, generate a small `helpers/backends.py` in the output providing
    `resolve_setting(name, override)` (env override wins, else in-cluster
    service default) and a `BackendUnavailable` exception, mirroring the
    reference port.

STEP 2 — BUNDLE FALLBACK (only when STEP 1 does not apply):
Only if a keyword's behavior CANNOT be reduced to a direct API/HTTP call —
genuinely complex logic such as stateful drivers, DB sessions (Cassandra),
k8s operations, or message-bus clients (Kafka) with no simple HTTP shape —
fall back to the EXISTING behavior: import the bundled custom library directly
(see "IMPORT BUNDLED CUSTOM LIBRARIES DIRECTLY" below) and rely on dependency
bundling (Option A). Keep that path fully working; the native path above simply
avoids needing it when a keyword is a plain HTTP call.

### Worked example — `Nifi.get_flow_status` → native APIRequestContext

`Nifi.get_flow_status` in `clamps/nifi/nifi.py` is `self.rest.get("flow/status")`,
and `common/rest.Rest` uses plain HTTP (`https_enabled=False`) against the k8s
service `nifi`, port `7777`, base path `/nifi-api` (from
`configs/common_config.py`). So the keyword is the single HTTP call
`GET http://nifi:7777/nifi-api/flow/status`. It converts to a self-contained,
browser-free pytest test — NO `from clamps...` import, NO bundling:

    # Robot:
    #   Can Get Flow Status
    #       ${status}=    Nifi.get_flow_status
    #       Should Contain    ${status}    controllerStatus

    import os
    import pytest
    from playwright.sync_api import APIRequestContext
    from helpers.backends import BackendUnavailable, resolve_setting


    def _nifi_base_url() -> str:
        full = os.environ.get("NIFI_BASE_URL")
        if full:
            return full.rstrip("/")
        host = resolve_setting("NIFI_HOSTS", os.environ.get("NIFI_HOSTS"))
        port = resolve_setting("NIFI_PORT", os.environ.get("NIFI_PORT"))
        base_path = resolve_setting("NIFI_BASE_PATH", os.environ.get("NIFI_BASE_PATH")) or "/nifi-api"
        if not host or not port:
            raise BackendUnavailable("NiFi host/port not resolvable; set NIFI_HOSTS/NIFI_PORT or NIFI_BASE_URL.")
        host = host.strip().strip('"[]').split(",")[0].strip().strip('"')
        return f"http://{host}:{port}{base_path if base_path.startswith('/') else '/' + base_path}"


    @pytest.mark.nifi
    def test_can_get_flow_status(playwright):
        base_url = _nifi_base_url()
        request: APIRequestContext = playwright.request.new_context(base_url=base_url)
        try:
            try:
                response = request.get("/nifi-api/flow/status")
            except Exception as exc:  # NiFi unreachable -> skip, not a false pass
                raise BackendUnavailable(f"NiFi not reachable at {base_url}: {exc}") from exc
            assert response.ok, f"GET /nifi-api/flow/status returned HTTP {response.status}"
            assert "controllerStatus" in response.text()
        finally:
            request.dispose()

Here `resolve_setting` returns the env override if set, else the in-cluster
service default (`NIFI_HOSTS`="nifi", `NIFI_PORT`="7777", `NIFI_BASE_PATH`="/nifi-api"),
and `BackendUnavailable` is raised to skip when NiFi is unreachable.

## Completeness Principle

GENERIC COMPLETENESS PRINCIPLE (applies to every conversion, not just one file):
Convert everything the source test actually uses — never the test body alone.
Walk the dependency chain and port each part the chosen test(s) reference:
  - Every `Library` import in *** Settings *** (built-in AND custom Python libraries).
  - Every `Resource` import, recursively, and the keywords within them the test calls.
  - Every `Variables` file and *** Variables *** entry the test or its keywords read.
  - `Suite Setup`, `Suite Teardown`, `Test Setup`, `Test Teardown` — convert these to
    pytest fixtures (module/function scope as appropriate); never silently drop them.
  - Every user keyword transitively called by the test, with its real argument
    signature and defaults preserved.
If a referenced keyword/library is TRULY absent from context (no source available and
not bundled), emit a clearly-named helper stub plus a TODO naming exactly what is
missing, rather than omitting the behavior.

IMPORT BUNDLED CUSTOM LIBRARIES DIRECTLY — NEVER WRAP THEM IN A TODO-STUB FALLBACK:
When a test depends on a custom library whose source will be bundled alongside the
converted test (e.g. `clamps.nifi.nifi.Nifi`, `clamps.infrastructure.k8s.application_controller.ApplicationController`),
import it DIRECTLY at the top of the file:

    from clamps.nifi.nifi import Nifi
    from clamps.infrastructure.k8s.application_controller import ApplicationController

Do NOT guard a bundled dependency with a `try: … except ImportError:` that defines a
local stub class raising `NotImplementedError`. A genuinely missing dependency must
surface as an honest `ImportError` (so the real cause — e.g. an uninstalled third-party
package the library needs — is visible), never be silently replaced by a
`NotImplementedError` TODO stub that makes every test fail for the wrong reason. The
TODO-stub-plus-helper guidance above applies ONLY to keywords/libraries that are truly
absent from context — not to a custom library that is bundled with the test.

## Porting Custom Python Libraries

PORT CUSTOM PYTHON LIBRARIES FROM THEIR IMPLEMENTATION, NOT GUESSES:
When the suite imports a custom library (Cassandra/Kafka/DB/HTTP client, etc.), read
that library's Python source if it is in context and replicate its real construction
in the helper:
  - Authentication: auth providers, usernames, password/secret FILE paths, tokens,
    TLS. A client that connects without the source's auth WILL fail against a secured
    backend (e.g. cassandra-driver PlainTextAuthProvider reading a mounted secret
    file; Kafka SASL/SSL; bearer tokens for HTTP).
  - Ports, keyspaces/topics/namespaces, load-balancing/retry policies, timeouts.
  - Expose each as an environment override with the SAME default the source used.

## Remote / Keyword-Server Libraries

REMOTE / KEYWORD-SERVER LIBRARIES (Robot `Remote` library, XML-RPC keyword servers):
The keyword logic lives on the SERVER, not in the suite. The helper is a thin XML-RPC
proxy (Python's stdlib xmlrpc.client is sufficient — no extra dependency).
  - Keyword names on the wire are NORMALIZED: Robot lowercases them and replaces
    spaces with underscores, keeps a trailing `?`, and keeps the `namespace/` prefix.
    So Robot `clj.analytics/combined metrics disabled?` is sent as
    `analytics/combined_metrics_disabled?`. NEVER send the spaced Robot form — the
    server throws a NullPointerException on an unresolved name.
  - If the exact registered names are unknown, the client can call the server's
    `get_keyword_names` once and map to the precise strings (casing matters).
  - Pass arguments with the arity the server expects; a zero-arg call to a keyword
    that needs a host raises an ArityException.

## Runtime-Resolved Config

RUNTIME-RESOLVED CONFIG (service discovery, not env vars):
Robot config modules often compute hosts at runtime (e.g.
resolve_hostname_to_ip("cassandra")) rather than reading env vars. In-cluster,
default the helper to those SAME service names/ports so the test needs zero
configuration, with env vars as optional overrides. Do not invent IPs.

## Stateful Connection Lifecycle

STATEFUL CONNECTION LIFECYCLE (servers that keep a global/persistent session):
  - Make connect idempotent: disconnect any stale session first, then connect, so a
    prior failed run does not leave the server reporting "Already connected".
  - Wrap fixture setup so that if any setup step fails, connections are still released
    before the error propagates (pytest runs post-yield teardown only when setup
    reached `yield`).
  - Faithfully port multi-step connect keywords (e.g. "connect to each host and stay
    on the first that reports healthy"), not just the first call.

BROWSER-FREE API/NON-UI TESTS (do not inject a browser the suite never uses):
A Robot suite whose test cases only call library or custom-keyword methods
(e.g. `Nifi.get_flow_status`), make HTTP/API calls, assert on returned data, or
perform infrastructure/k8s operations — with NO browser navigation, NO element
interaction, and NO visual/DOM assertion — converts to PLAIN pytest functions.
Such tests MUST NOT take a `page: Page` parameter, MUST NOT import the browser
`Page` fixture, and MUST NOT trigger pytest-playwright's browser launch.
  - APPLY THE ORDERED STRATEGY ABOVE: if the keyword is a direct HTTP/REST call
    (STEP 1), translate it to a native Playwright APIRequestContext call and do
    NOT import the custom client. Only when STEP 1 does not apply (STEP 2) keep
    the suite's bundled custom client (e.g. `Nifi()`), importing it directly.
  - Preserve each Robot tag (`Force Tags`, `[Tags]`) as a registered pytest
    mark — never add a browser dependency to satisfy a tag.
  - Only test cases that genuinely drive a UI get `page: Page` and the browser
    imports.

The `Nifi.get_flow_status` example under "Conversion Strategy for API/REST
Tests" above shows the preferred STEP 1 native translation for this exact case
(a self-contained APIRequestContext GET, env-overridable base URL, skip when
NiFi is unreachable). Fall back to importing the bundled `Nifi` client only for
keywords whose behavior cannot be reduced to a direct HTTP call.

ASSERTIONS, STRUCTURE & DATA SEMANTICS:
  - Preserve exact computed values and data shapes (timestamps, week math, numeric
    results). Map Robot assertions to explicit checks: Should Not Be Empty, Should Be
    Equal As Numbers, list containment, etc.
  - Playwright's native expect() targets DOM/HTTP only. For backend/non-DOM values,
    use a small value-assertion helper (not expect()). Keep pytest fixtures and
    pytest-playwright structure.
  - `Pass Execution If` → pytest.skip.
  - When a required backend/driver is unavailable, SKIP with a clear reason rather
    than letting the test falsely pass.
