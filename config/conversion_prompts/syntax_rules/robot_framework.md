OUTPUT RULES (STRICT):
1. Output ONLY valid Robot Framework code.
2. Do NOT wrap the code in markdown code fences.
3. Do NOT include introductory text, explanations, or conclusions.

SYNTAX & FORMATTING:
- Maintain EXACTLY these sections in order (omit empty sections):
    *** Settings ***
    *** Variables ***
    *** Keywords ***
    *** Test Cases ***
- CRITICAL SPACING RULE: Separate all keywords, arguments, and variable
  assignments with AT LEAST TWO SPACES (or a TAB). Single spaces are
  invalid cell separators — this is the single most common syntax error,
  check every line before finishing.
- Test Case titles must start at the beginning of the line (0 indentation).
- Steps inside test cases and keywords MUST be indented with 4 spaces.

MANDATORY — NO ORPHAN KEYWORDS:
Every keyword you define under *** Keywords *** MUST be called from at
least one entry under *** Test Cases ***/*** Tasks *** in the SAME file.
A file containing only keyword definitions with nothing invoking them is
an INVALID, incomplete conversion — never ship this. If the source
performs any check()/expect()/assert, the output MUST contain a runnable
test case that calls the converted logic and asserts its result (e.g.
with `Should Be True`).

KEYWORD CREATION RULES:
- Write steps inline inside *** Test Cases *** when there is only one
  logical scenario.
- Define custom keywords under *** Keywords *** when they encapsulate 2+
  sequential steps and are reused, or when the source maps one exported
  function to one keyword (see pair-specific rules for the mapping).

SYNTAX & PARSING RULES (RF 6+/7+, use the MODERN syntax exclusively):
- NO PYTHON DOCSTRINGS: never use triple quotes (""") for keyword
  documentation or comments. Use [Documentation] or '#' comments.
- RETURN VALUES: ALWAYS use the modern block statement `RETURN    ${value}`.
  NEVER use the deprecated `[Return]` setting.
- CONTROL FLOW: ALWAYS use native `IF ... END`, `FOR ... END`,
  `WHILE ... END`, `TRY ... EXCEPT AS ${error} ... END`, `BREAK`,
  `CONTINUE`. NEVER use `Run Keyword If`, `Run Keyword Unless`,
  `Exit For Loop`, `Exit For Loop If`, `:FOR`/`\` continuation syntax, or
  `Return From Keyword` — these are legacy constructs and must not appear
  anywhere in the output.
- EXCEPT SYNTAX: `EXCEPT    AS    ${error}` — the `AS` keyword is
  mandatory before the variable name. `EXCEPT    ${error}` (missing `AS`)
  is invalid.
- NESTED DICTIONARIES: Do NOT pass multiple key arguments to
  'Get From Dictionary'. Access nested keys directly using RF variable
  syntax (e.g., '${json}[data][result]').
- ARGUMENT ORDER: list varargs (@{LIST}) must come before any keyword
  arguments but after required positional scalar arguments. Never place a
  list vararg before a required scalar argument in [Arguments].
- Every `Evaluate` call that needs a Python module (json, urllib, base64,
  etc.) MUST pass it as `modules=<name>` and must NOT need multi-line
  statement separation — Evaluate takes a single Python expression, not a
  multi-statement script. If logic needs more than one statement, define
  it as its own Keyword with normal Robot statements instead of cramming
  it into one Evaluate call.
- MULTI-LINE: Robot Framework does not support Python-style multi-line
  expressions in Evaluate via `;`. If a value needs multiple computation
  steps, break it into intermediate `${var}=    Set Variable    ...`
  lines, not into one giant Evaluate.

CORRECT EXAMPLE (keyword DEFINED under *** Keywords *** AND CALLED from
*** Test Cases *** — never ship one section without the other):
*** Settings ***
Library    SeleniumLibrary
Test Teardown    Close Browser

*** Variables ***
${URL}    https://example.com
${BROWSER}    chrome

*** Keywords ***
Login With Credentials
    [Arguments]    ${username}    ${password}
    Open Browser    ${URL}    ${BROWSER}
    Input Text    css=[data-testid='username']    ${username}
    Input Text    css=[data-testid='password']    ${password}
    Click Button    css=[data-testid='login-btn']

*** Test Cases ***
Login With Valid Credentials
    Login With Credentials    admin    secret
    Location Should Contain    /dashboard
    Element Text Should Be    css=h1    Dashboard
