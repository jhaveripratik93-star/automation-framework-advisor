K6 → Robot Framework CONVERSION RULES
K6 is an HTTP/load tool. Robot Framework replaces it using RequestsLibrary
for HTTP calls.

MANDATORY library imports under *** Settings ***:
    Library    RequestsLibrary
    Library    Collections

*** CRITICAL — group()/exported-function → Test Case MAPPING (read first) ***
A k6 source file has ONE of two shapes. Identify which shape you are given
and follow its rule EXACTLY — this determines whether the output needs a
*** Test Cases *** section at all:

SHAPE A — the file exports a function whose body wraps its checks in a k6
`group('<Name>', function () { ... check(...) ... })`. This group() IS the
test — it is k6's equivalent of a Robot test case. You MUST produce BOTH:
  1. A `*** Keywords ***` entry with the converted logic (HTTP calls,
     retries, check()→assertion translation), returning a boolean/value
     exactly like the k6 function does.
  2. A `*** Test Cases ***` entry — named after the group()'s string
     argument — that CALLS that keyword and asserts the result, e.g.:
        *** Test Cases ***
        Verify Health Status For All Microservices
            ${result}=    Get Health Status    ${ms_url}    ${ms_name}    ${endpoint}    none
            Should Be True    ${result}
  Producing only the Keyword (step 1) and omitting the Test Case (step 2)
  is an INVALID conversion — do not do this even if the function is also
  imported by another file. Every converted file must be independently
  runnable.

SHAPE B — the file exports ONLY pure helper/utility functions with NO
check()/group() of their own (e.g. a URL builder, a data mapper, a retry
loop wrapper that takes a callback). These are legitimately
`*** Keywords ***`-only — do not invent a fake test case for them. State
this explicitly with a leading `# Library keyword(s) — no standalone
group()/check() in source; consumed by other suites.` comment so the
omission is clearly intentional, not a mistake.

SHAPE C — the file's check()/http calls live directly inside the k6 entry
point `export default function () { ... check(...) ... }`, with NO
group() wrapper anywhere. This is the single most common k6 script shape
(a plain load/smoke test with no explicit group() call) — do NOT force
this into SHAPE A's "extract the name from group()" rule, since there is
no group() name to extract. Instead:
  1. Treat the ENTIRE default function as ONE test case.
  2. Derive a descriptive Test Case name from the HTTP call + check()
     descriptions (e.g. a login POST with checks "status is 200" /
     "token present" becomes `Login Returns 200 With Token`). Never name
     it `Default`, `Main`, or leave it unnamed.
  3. Inline the steps directly under `*** Test Cases ***` (Setup Session,
     HTTP call, one assertion per check() entry — see the keyword mapping
     table below). Only split logic into `*** Keywords ***` first if that
     same logic is reused by another file in the project, per the
     KEYWORD CREATION RULES above — don't add an unnecessary indirection
     for a single-use default function.
  4. Still document `options.vus` / `options.duration` / any
     `options.thresholds` in `*** Variables ***` as usual.

DECIDING WHICH SHAPE APPLIES — check in this order:
  - No check()/group() at all, pure helper exports              -> SHAPE B
  - Has check()/http calls, wrapped in a named group()          -> SHAPE A
  - Has check()/http calls, NOT wrapped in any group()          -> SHAPE C
Never force SHAPE A when no group() name exists — that produces an
unresolvable "name the test case" step and inconsistent output. If a
single file contains BOTH group()-wrapped sections AND top-level
check()s outside any group(), apply SHAPE A per group and SHAPE C for the
remaining ungrouped logic, in the same file.

CONVERSION DETAILS:
- Return statements: use the modern `RETURN    ${variable}` syntax
  (NEVER `[Return]`).
- Exception handling: use `TRY ... EXCEPT    AS    ${error} ... END`
  (the `AS` keyword is mandatory).
- Dictionary lookups: use direct index syntax `${json}[key1][key2]` for
  nested properties instead of multi-arg `Get From Dictionary`.
- List parameters in [Arguments]: pass as a standard scalar `${METRICS}`
  if other positional arguments follow it; use `@{METRICS}` only when it's
  the last argument or there are no further positional arguments after it.

EXAMPLE CONVERSION PATTERN:
*** Keywords ***
Get PM Server Response
    [Arguments]    ${metric_name}
    [Documentation]    Calls the PM server endpoint for a single metric.
    ${endpoint}=    Set Variable    /metrics/${metric_name}
    ${response}=    GET On Session    api    ${endpoint}
    RETURN    ${response}

K6 → Robot Framework keyword mapping (MUST follow exactly):
http.get(url)                        → GET On Session    alias    endpoint
http.post(url, body)                 → POST On Session   alias    endpoint    json=${body}
http.put(url, body)                  → PUT On Session    alias    endpoint    json=${body}
http.del(url)                        → DELETE On Session alias    endpoint
check(res, {'status is 200': ...})   → Status Should Be    200    ${response}
check(res, {'body contains X': ...}) → Should Contain    ${response.text}    X
check(res, {'duration < N': ...})    → Should Be True    ${response.elapsed.total_seconds()} < N
JSON field assertion                 → ${json}=    Set Variable    ${response.json()}
                                        Dictionary Should Contain Key    ${json}    field_name
                                        Should Be Equal As Strings    ${json}[field]    expected
sleep(N)                             → Sleep    Ns
options.vus / options.duration       → Document in *** Variables *** as ${VUS} and ${DURATION}
group('<Name>', fn)                  → *** Test Cases ***  entry named '<Name>' that calls the
                                        converted keyword and asserts its result (see SHAPE A above)

Session setup pattern (REQUIRED for every test suite):
*** Keywords ***
Setup Session
    Create Session    alias    ${BASE_URL}

Each test MUST:
1. Call Setup Session (or use `Suite Setup    Setup Session` under
   *** Settings ***)
2. Make the HTTP call and store the result in ${response}
3. Assert EVERY k6 check() entry with the matching RF keyword above

EXAMPLE (SHAPE A) — converting this k6 code:
export function getUsers() {
    let result = true;
    group('Get Users Returns 200 With Users Key', function () {
        const res = http.get(`${BASE_URL}/users`);
        result = check(res, {
            'status is 200': (r) => r.status === 200,
            'has users key': (r) => r.json().users !== undefined,
        });
    });
    return result;
}

MUST produce (keyword that does the work, PLUS a test case that calls it
and asserts — never the keyword alone):
*** Settings ***
Library    RequestsLibrary
Library    Collections

*** Variables ***
${BASE_URL}    https://example.com

*** Keywords ***
Get Users
    Create Session    api    ${BASE_URL}
    ${response}=    GET On Session    api    /users
    Status Should Be    200    ${response}
    ${json}=    Set Variable    ${response.json()}
    Dictionary Should Contain Key    ${json}    users
    RETURN    ${True}

*** Test Cases ***
Get Users Returns 200 With Users Key
    ${result}=    Get Users
    Should Be True    ${result}

EXAMPLE (SHAPE C — no group(), checks live directly in the default
function) — converting this k6 code:
export const options = {
    vus: 10,
    duration: '30s',
    thresholds: { http_req_duration: ['p(95)<500'], http_req_failed: ['rate<0.01'] },
};

export default function () {
    const res = http.post(
        'https://api.example.com/auth/login',
        JSON.stringify({ username: 'admin', password: 'password123' }),
        { headers: { 'Content-Type': 'application/json' } }
    );
    check(res, {
        'status is 200': (r) => r.status === 200,
        'token present': (r) => JSON.parse(r.body).token !== undefined,
    });
    sleep(1);
}

MUST produce (ONE test case, inlined — no group() name to extract, no
Keyword indirection needed for a single-use default function):
*** Settings ***
Library    RequestsLibrary
Library    Collections
Library    OperatingSystem

*** Variables ***
${BASE_URL}    https://api.example.com
${VUS}         10
${DURATION}    30s

*** Test Cases ***
Login Returns 200 With Token
    Create Session    api    ${BASE_URL}
    ${response}=    POST On Session    api    /auth/login
    ...    json={"username": "admin", "password": "password123"}
    Status Should Be    200    ${response}
    ${json}=    Set Variable    ${response.json()}
    Dictionary Should Contain Key    ${json}    token
    Sleep    1s
