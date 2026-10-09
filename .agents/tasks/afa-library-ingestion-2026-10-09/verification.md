# Verification — Library Ingestion + Auth-Contract Conversion + Completeness Report

Repo: `automation-framework-advisor` · Python 3.14.0 · pytest 9.1.1 · all commands run from the repo root.

## Toolchain

```
$ python --version
Python 3.14.0
$ python -m pytest --version
pytest 9.1.1
```

## Byte-compile (all changed modules)

```
$ python -m py_compile src/tools/executor.py src/tools/library_ingest.py src/codegen/agent_config.py mcp_server/manual_mode.py
compile exit=0
```

Clean (exit 0). The three `SyntaxWarning: "\("` lines are pre-existing warnings in unrelated
Robot-string literals in `executor.py` (lines 991/1099/1134); not introduced by this change.

## PIPELINE_SETTINGS key

```
$ python -c "from src.codegen.agent_config import PIPELINE_SETTINGS; assert PIPELINE_SETTINGS['max_library_context_chars']==6000; print(...)"
max_library_context_chars= 6000
```

Key: `max_library_context_chars`, default `6000`, placed next to `max_context_chars` /
`max_suite_context_chars` in the existing comment style.

## New offline tests (3 planned files + completeness-report tests)

```
$ python -m pytest tests/test_library_ingest_parser.py tests/test_library_ingest_contract.py tests/test_library_ingest_integration.py -q
...............................
31 passed in 12.29s
```

Report-specific tests (in `tests/test_library_ingest_integration.py`), all passing:
- `test_report_present_and_structured_groq_path` — asserts `result["report"]` has
  `handled`/`assumed`/`not_done`/`text`; HANDLED records the derived auth flow, resolved imports,
  and the generated shared helper; ASSUMED lists runtime placeholders with `__ENV.` env refs incl.
  `client_id`; the `### What was done / assumed / not done` section is folded into `summary`.
- `test_report_lists_unresolved_imports_in_not_done` — NOT-DONE lists an unresolved import
  (`clamps.Missing`).
- `test_report_present_in_manual_brief` — the manual-mode brief carries the same report structure,
  derived from the shared executor method.
- `test_report_degrades_gracefully_for_non_robot` — non-Robot input: frozen keys intact, HANDLED
  still lists basic facts, NOT-DONE notes "no library layer", ASSUMED empty, no `lib/auth.js`, no
  crash.
- `test_frozen_return_keys_unchanged` — `convert_multi_file`'s keys are exactly the 7 frozen keys
  plus the single additive `report`.
- `test_both_paths_derive_identical_context`, `test_manual_brief_carries_contract_and_helper`,
  `test_non_k6_js_target_yields_no_helper`, `test_k6_auth_helper_emitted_in_helpers`,
  `test_block_variant_matches_helper_presence`.

LLM is never called: `executor._llm = None`, so `_llm_convert` returns a placeholder string. No
network, no Groq. The resolver purity test monkeypatches `builtins.open` to raise.

## Full regression suite

```
$ python -m pytest tests/ --ignore=tests/test_tokens.py -q
.....................................................................
69 passed, 1 warning in 12.10s
```

`tests/test_tokens.py` is excluded because it is a **pre-existing broken test** (unrelated to this
change): it imports `src.llm.token_usage`, a module that does not exist anywhere in the repo
(`git status` shows no local modification to that file; it was already failing collection on the
clean tree). The one warning is a pre-existing `PytestReturnNotNoneWarning` in
`tests/test_groq_connection.py`.

## Additive-only guarantee

- `convert_multi_file` return keys: the 7 frozen keys `{converted, helpers, conftest, requirements,
  gaps, summary, assertion_report}` are unchanged in name and shape; `report` is the only new
  top-level key (asserted by `test_frozen_return_keys_unchanged`).
- Manual-mode brief: existing documented keys unchanged; `report` added additively, plus the
  optional `auth_helper` key when a shared helper was derived.
- The `summary` string now additionally contains a `### What was done / assumed / not done`
  section (its value is enriched; the key and its type stay a string). This is the explicitly
  clarified requirement that every conversion render the report in the human-readable summary.
