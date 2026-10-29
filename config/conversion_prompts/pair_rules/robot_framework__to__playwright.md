Robot Framework → Playwright conversion rules.

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

RUNTIME-RESOLVED CONFIG (service discovery, not env vars):
Robot config modules often compute hosts at runtime (e.g.
resolve_hostname_to_ip("cassandra")) rather than reading env vars. In-cluster,
default the helper to those SAME service names/ports so the test needs zero
configuration, with env vars as optional overrides. Do not invent IPs.

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
  - Keep the suite's existing custom client (e.g. `Nifi()`); only reach for
    `requests`/`httpx` or Playwright's APIRequestContext if an HTTP client is
    genuinely required and the source has none.
  - Preserve each Robot tag (`Force Tags`, `[Tags]`) as a registered pytest
    mark — never add a browser dependency to satisfy a tag.
  - Only test cases that genuinely drive a UI get `page: Page` and the browser
    imports.

Example — an API-only Robot test case maps to a browser-free pytest test:

    # Robot (library-only, no UI):
    *** Settings ***
    Library    clamps.nifi.nifi.Nifi    WITH NAME    Nifi
    Force Tags    nifi

    *** Test Cases ***
    Can Get Flow Status
        ${status}=    Nifi.get_flow_status
        Should Contain    ${status}    controllerStatus

    # Playwright/pytest — NO page, NO browser import:
    import pytest
    from clamps.nifi.nifi import Nifi


    @pytest.fixture
    def nifi() -> Nifi:
        return Nifi()


    @pytest.mark.nifi
    def test_can_get_flow_status(nifi: Nifi):
        status = nifi.get_flow_status()
        assert "controllerStatus" in status

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
