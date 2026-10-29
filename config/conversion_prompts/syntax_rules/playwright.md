OUTPUT RULES (STRICT):
1. Output ONLY valid executable Python code using pytest / pytest-playwright idioms.
2. Do NOT wrap the code in markdown code fences.
3. Do NOT output any explanations or conversational text.

BROWSER FIXTURE — ONLY WHEN THE TEST DRIVES A UI (STRICT):
- A converted test gets the `page: Page` fixture and the browser imports ONLY
  when it performs REAL UI work: browser navigation (`page.goto`), element
  interaction (`click`/`fill`/`type`/`select_option`/`set_input_files`),
  locator lookups (`page.locator`, `page.get_by_*`), or visual/DOM assertions
  (`expect(page...)...`).
- If the source test only performs HTTP/API calls, custom library/keyword
  calls, data assertions, or infrastructure/k8s operations — with NO browser
  navigation, NO element interaction, and NO visual/DOM assertion — then the
  converted pytest test MUST NOT declare a `page` parameter, MUST NOT import
  the browser `Page` fixture, and MUST NOT do anything that triggers
  pytest-playwright's browser launch. Injecting an unused `page` forces a
  Chromium launch at setup and fails the whole test for no reason.
- For an API-only test that needs an HTTP client, use Playwright's
  APIRequestContext (`playwright.request.new_context(base_url=...)` then
  `.get/.post/.put/.delete(...)`) or `requests`/`httpx`.
- Keep custom test tags as registered pytest marks; never pull in a browser
  dependency just to satisfy a tag.

PREFER NATIVE API TRANSLATION OVER IMPORTING A REST CLIENT (STRICT):
- When a Robot keyword maps to a custom-library method that is ultimately a
  direct HTTP/REST call (its body calls `self.rest.get/post/put/delete(...)`,
  `self.client.<verb>(...)`, `requests.*`, `httpx.*`, or another HTTP client),
  TRANSLATE it into a native Playwright APIRequestContext call with the REAL
  method, path, headers/auth and base URL (scheme/host/port/base-path read from
  the library + its config module). Make the connection env-overridable with the
  SAME defaults the source used, and SKIP (not fail, not false-pass) when the
  backend is unreachable. The resulting test MUST be self-contained — it MUST
  NOT import the `clamps`/`common` client and MUST NOT require bundling.
- ONLY fall back to importing the bundled custom client (see the direct-import
  rule below) for a keyword whose behavior genuinely CANNOT be reduced to a
  direct HTTP call (stateful drivers, DB sessions, k8s, message buses).

BUNDLED CUSTOM LIBRARY IMPORTS — DIRECT, NO TODO-STUB FALLBACK (STRICT):
- When a test depends on a custom library whose source will be bundled
  alongside the converted test (e.g. `clamps.nifi.nifi.Nifi`), import it
  DIRECTLY at the top of the file:
      from clamps.nifi.nifi import Nifi
- Do NOT wrap such an import in a `try: … except ImportError:` that defines a
  local class raising `NotImplementedError`. A genuinely missing dependency
  must surface as an honest `ImportError`, never be silently swapped for a
  `NotImplementedError` TODO stub that makes every test fail for the wrong
  reason. Emit a TODO stub ONLY for a keyword/library that is truly absent from
  context, never for a library bundled with the test.

TECHNICAL REQUIREMENTS:
- Imports for genuine UI tests:
    from playwright.sync_api import Page, expect
  Import `Page`/`expect` ONLY in files that actually drive a browser.
- Structure tests as standalone pytest functions. A UI test is
  `def test_<name>(page: Page):`; an API/library-only test is
  `def test_<name>(<custom_client_or_fixture>):` with NO `page` parameter.
- Preferred locator strategy (UI tests only): use user-facing locators
  (`page.get_by_role`, `page.get_by_label`, `page.get_by_test_id`) over raw
  CSS/XPath when possible.
- Assertions (UI tests): use auto-retrying web-first assertions via `expect()`
  (e.g., `expect(page.get_by_role("heading")).to_be_visible()`). For
  backend/non-DOM values, assert directly on the returned data with plain
  `assert`.
- Do NOT explicitly instantiate browsers or drivers inside tests; rely on the
  injected `page` fixture when a browser is genuinely needed.
- Do NOT output Robot Framework, Selenium, or raw JavaScript syntax.
