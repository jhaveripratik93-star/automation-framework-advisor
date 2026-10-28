# Implementation Plan — Read-by-path/test-case-name + conversion analysis MCP tool

## Context and design decisions (grounded in the codebase)

The requested capability already has 90% of its machinery built. `src/analysis/robot_dependency_analyzer.py::analyze_path(input_path, source_root="")` already:
- reads a single `.robot` file OR walks a directory recursively (skipping `node_modules/.git/__pycache__/.venv/venv/dist/build/.idea/.vscode/target`),
- parses each test case with the real Robot Framework parser (`robot.api.get_model`, never regex),
- returns per-test-case `conversion_readiness` verdicts (`self_contained` / `needs_custom_libs` / `blocked`) with rationale, a `dependencies` bundle (custom_libraries, resolved/missing custom libs, custom_resource_keywords, resources, variables_referenced/assigned, blockers), de-duped `keywords_used`, and a markdown `report`.

That function is already exposed as the MCP tool `analyze_test_dependencies(input_path, source_root="")` in `mcp_server/server.py` (it just calls `analyze_path`). What is MISSING relative to the user's request ("read files by given the path name OR testcase name") is the ability to drive the same analysis by a **test-case name**, returning only the matching test case(s).

**Decision 1 — new tool, not a change to the existing one.** Add a NEW tool rather than adding a `test_case_name` param to `analyze_test_dependencies`, so the existing tool's contract and its LLM-facing docstring stay stable and the two intents (whole-suite scoping vs. "analyze this one test case for conversion") are discoverable as distinct tools. Name: **`analyze_test_for_conversion`** — matches the verb-first naming of neighboring tools (`analyze_test_dependencies`, `analyze_test_case_coverage`, `analyze_prerequisites`) and reads as the user's phrasing ("analysis for testcase that are needed for the conversion").

**Decision 2 — reuse `analyze_path`, filter its output.** The new tool calls `analyze_path(path, source_root=source_root)` to get the full per-file/per-test-case analysis, then, when `test_case_name` is given, filters the per-test-case results down to the matches. This reuses the exact return schema and the real-parser analysis rather than inventing a new one.

**Decision 3 — case-insensitive substring match.** Robot test-case names are long, multi-word, and space-sensitive (e.g. `Verify The TypeCasting Error Messages In Levski Analytics Log For Ingesting Empty Values`). Exact match would be brittle for a human-typed filter. Use case-insensitive substring match against the test case `name`. This lets `"typecasting"` or `"Flow Status"` match, and still allows an exact name to match. Document this clearly in the docstring. (No RF-style space/underscore normalization here — test-case names, unlike keyword names, are matched literally by RF, so substring on the literal name is the least-surprising behavior.)

**Decision 4 — filtering helper lives in the analyzer module.** Put the match logic in a small, unit-testable helper `filter_result_by_test_case(result, test_case_name)` in `src/analysis/robot_dependency_analyzer.py` (exported via `__all__`), rather than inline in `server.py`. It is non-trivial (walk `files[].test_cases[]`, drop non-matching test cases, drop files left with zero matches, recompute `total_test_cases` and `readiness_counts`, and re-render the markdown via the existing `_render_markdown`). Keeping it beside `analyze_path` means it can be tested without the MCP layer and can reuse `_render_markdown`.

**Decision 5 — surface stays minimal.** Inputs: `path: str`, `test_case_name: str = ""`, `source_root: str = ""`. No target-framework arg — the analysis is framework-agnostic readiness/dependency scoping (it reports what any conversion must port), and `convert_test_code` is the tool that takes a target framework. Adding one here would imply this tool converts, which it does not.

**Decision 6 — error handling mirrors the existing tool.** Return `analyze_path`'s `{"error": ...}` dict unchanged for a missing path / non-.robot file / empty directory / robotframework-not-installed (never raise). When `path` is valid and parses but `test_case_name` matches nothing, return a non-error dict that names what was searched (`path`, `test_case_name`) with `matched_test_cases: 0` and an empty `files` list, plus a short `report` string saying no test case matched — so the caller gets a clear, actionable empty result rather than an error.

**Return shape** (JSON-serializable dict): the resolved `input_path` and `source_root` (as returned by `analyze_path`), the `test_case_name` filter echoed back, `total_files`, `total_test_cases`, `matched_test_cases` (count after filtering; equals `total_test_cases` when no filter), `readiness_counts`, the filtered `files` list (each retaining the analyzer's existing per-test-case fields: documentation, tags, setup/teardown, steps, keywords_used, dependencies with blockers, conversion_readiness + rationale), and the markdown `report`.

**Scope guard:** touch only test-analysis code. Do NOT add or modify CI/CD, GitHub Actions, or deployment config.

**Environment:** venv at `c:\Users\znadsal\pratik_new_code\automation-framework-advisor\.venv` (confirmed `robot.api`, robotframework 7.5, is installed). Tests live under `automation-framework-advisor\tests\` and are plain `pytest` functions that skip parser assertions when robotframework is absent (see `tests/test_robot_dependency_analyzer.py`). Run commands from the repo root `c:\Users\znadsal\pratik_new_code\automation-framework-advisor`.

---

# Implementation Plan

- [x] 1. Add the `filter_result_by_test_case` helper to the analyzer.
      In `src/analysis/robot_dependency_analyzer.py`, add `filter_result_by_test_case(result: dict, test_case_name: str) -> dict`. If `test_case_name` is empty/whitespace, return `result` unchanged. Otherwise: if `result` has an `"error"` key, return it unchanged; else build a shallow-copied result where each entry in `files` keeps only `test_cases` whose `name` contains `test_case_name` case-insensitively (substring), drop files whose `test_cases` becomes empty, then recompute `total_files`, `total_test_cases`, and `readiness_counts` from the surviving test cases, set `test_case_name` on the dict, and regenerate `report` via the existing `_render_markdown(filtered_result)`. Add `"filter_result_by_test_case"` to `__all__`. Do not mutate the input dict (copy files/test_cases lists before pruning).
      Files: `src/analysis/robot_dependency_analyzer.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/test_robot_dependency_analyzer.py -q` — existing suite still passes (no regressions from the new helper/`__all__` change).

- [x] 2. Add unit tests for the filtering helper.
      In `tests/test_robot_dependency_analyzer.py`, import `filter_result_by_test_case` and add tests that build a tiny two-test-case `.robot` file under `tmp_path`, run `analyze_file`/`analyze_path`, then assert: (a) empty `test_case_name` returns the result unchanged (same total_test_cases); (b) a case-insensitive substring of one test case name returns exactly that one test case and prunes the other, with `total_test_cases == 1` and `matched`/report reflecting it; (c) a non-matching name yields zero surviving test cases / empty `files`; (d) passing an `{"error": ...}` dict through the helper returns it unchanged. Guard parser-backed assertions with the existing `_robot_available()` pattern.
      Files: `tests/test_robot_dependency_analyzer.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/test_robot_dependency_analyzer.py -q` — all tests, including the 4 new ones, pass.

- [x] 3. Register the `analyze_test_for_conversion` MCP tool in the server.
      In `mcp_server/server.py`, directly after `analyze_test_dependencies` (section 3), add a sync `@mcp.tool()` / `@log_tool_call` function `analyze_test_for_conversion(path: str, test_case_name: str = "", source_root: str = "") -> dict[str, Any]`. Body: `from src.analysis.robot_dependency_analyzer import analyze_path, filter_result_by_test_case`; call `result = analyze_path(path, source_root=source_root)`; if `result` contains `"error"`, return it as-is; otherwise `filtered = filter_result_by_test_case(result, test_case_name)`; compute `matched = filtered.get("total_test_cases", 0)`; return `filtered` augmented with `"test_case_name": test_case_name` and `"matched_test_cases": matched`; when `test_case_name` is set and `matched == 0`, ensure `report` states that no test case matched the given name under the given path (set a clear one-line `report` string in that case). Write a rich LLM-facing docstring that: states it reads a LOCAL-MACHINE `.robot` file or directory by `path` and optionally filters to the test case(s) whose name contains `test_case_name` (case-insensitive substring); explains the returned readiness/dependency fields; and disambiguates vs. neighbors — use `analyze_test_dependencies` to scope a whole suite/directory with no name filter, `analyze_test_case_coverage` to map manual test-case dicts onto framework capabilities (not disk files), `analyze_prerequisites` for manual setup steps, and `convert_test_code` to actually generate converted code (this tool only analyzes). Note it returns `{"error": ...}` (never raises) on a missing path / non-.robot file / empty dir / robotframework-not-installed.
      Files: `mcp_server/server.py`
      Verify: `.\.venv\Scripts\python.exe -c "import mcp_server.server"` (run from repo root) exits 0 — tool imports and registers with no error.

- [x] 4. Add a real pytest exercising the new tool end-to-end against the sibling autotest tree.
      Create `tests/test_analyze_test_for_conversion.py` following the `_robot_available()` + real-fixture pattern in `tests/test_robot_dependency_analyzer.py`. Import the tool function from `mcp_server.server` (`from mcp_server.server import analyze_test_for_conversion`; note it is the `@log_tool_call`-wrapped callable — call it directly with keyword args). Define the analytics dir constant `AUTOTEST_ROOT / "core-tests" / "component" / "analytics"` relative to the repo (two parents up from the test file, then into `autotest_2_extracted/autotest`). Tests, each skipping (early `return`) when robotframework is absent or the fixture path is missing: (a) **directory, no filter** — call with `path=<analytics dir>`, `source_root=<AUTOTEST_ROOT>`; assert no `error`, `total_files >= 1`, `total_test_cases > 1`, every surviving test case has a `conversion_readiness` in {self_contained, needs_custom_libs, blocked} and a non-empty `rationale`, and `report` is present; (b) **single file + matching name** — call with `path=<analytics/ingest.robot>`, `test_case_name="typecasting"` (substring of `Verify The TypeCasting Error Messages In Levski Analytics Log For Ingesting Empty Values`); assert exactly the matching case(s) are returned (`matched_test_cases >= 1`, and every returned test case's `name` lower-cased contains `"typecasting"`), and `test_case_name == "typecasting"` is echoed back; (c) **non-existent path** — call with a path like `C:\no\such\dir\nope.robot`; assert the result is a dict containing `"error"` and that calling the tool did not raise. Keep the test independent of GROQ/LLM — this tool uses none.
      Files: `tests/test_analyze_test_for_conversion.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest tests/test_analyze_test_for_conversion.py -q` — all three tests pass (or skip cleanly if robotframework/fixtures are unavailable, which in this env they are not).

- [x] 5. Run the full analysis test group and the import check together as the final gate.
      No code changes; this item confirms the feature is buildable and registered.
      Files: (none)
      Verify: from repo root `c:\Users\znadsal\pratik_new_code\automation-framework-advisor`, run `.\.venv\Scripts\python.exe -m pytest tests/test_robot_dependency_analyzer.py tests/test_analyze_test_for_conversion.py -q` AND `.\.venv\Scripts\python.exe -c "import mcp_server.server"` — both exit 0 with all tests passing.

## Verification record (iteration 1 — implemented)

All plan items implemented. Commands run from repo root
`c:\Users\znadsal\pratik_new_code\automation-framework-advisor` under `.venv`:

1. `.\.venv\Scripts\python.exe -c "import robot.api, robot; print(robot.version.VERSION)"`
   → `robotframework 7.5` (analyzer parser available).
2. `.\.venv\Scripts\python.exe -c "import mcp_server.server"` → exit 0 (no import/registration error).
3. Tool-registration check via `mcp.list_tools()` →
   `analyze_test_for_conversion` present in exported tools alongside
   `analyze_test_dependencies`, `analyze_test_case_coverage`, `analyze_prerequisites`.
4. `.\.venv\Scripts\python.exe -m pytest tests/test_robot_dependency_analyzer.py tests/test_analyze_test_for_conversion.py -q`
   → `27 passed in 3.51s`. Includes:
   - 4 new helper tests (empty-name passthrough, case-insensitive substring match,
     non-matching→empty, error-dict passthrough) in `test_robot_dependency_analyzer.py`.
   - 3 new end-to-end tool tests in `test_analyze_test_for_conversion.py`:
     (a) directory `.../component/analytics` no filter → multiple test cases, each with a
     valid `conversion_readiness` + rationale; (b) single file `ingest.robot` +
     `test_case_name="typecasting"` → only the matching case returned, filter echoed back;
     (c) non-existent path → `{"error": ...}` dict, no raise.

Both new tests are kept as real pytest files under `tests/` (they fit the existing
`_robot_available()` + real-fixture style), so no throwaway cleanup was needed.

What changed:
- `src/analysis/robot_dependency_analyzer.py`: added `filter_result_by_test_case`
  (case-insensitive substring filter, non-mutating, recomputes counts, re-renders
  markdown via `_render_markdown`), added it to `__all__`.
- `mcp_server/server.py`: added `analyze_test_for_conversion(path, test_case_name="",
  source_root="")` after `analyze_test_dependencies`, reusing `analyze_path` +
  `filter_result_by_test_case`; rich docstring cross-referencing the related tools.
- `tests/test_robot_dependency_analyzer.py`, `tests/test_analyze_test_for_conversion.py`:
  new tests.

## Notes / assumptions
- The sibling fixture tree `autotest_2_extracted\autotest\core-tests\component\analytics` was confirmed present with 9 `.robot` suites; `ingest.robot` contains the test case used for the name-filter test. If a future run moves these fixtures, tests (a)/(b) skip via the `is_dir()`/`is_file()` guards rather than failing.
- No target-framework argument is added (Decision 5). If the reviewer wants conversion output, that is `convert_test_code`'s job, not this analysis tool's.
- Scope strictly excludes CI/CD, GitHub Actions, and deployment config.

