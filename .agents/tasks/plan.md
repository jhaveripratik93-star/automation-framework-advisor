# Implementation Plan — `convert_test_from_path` end-to-end MCP tool

One-call tool that reads Robot Framework test(s) from a LOCAL path, runs the existing
conversion-readiness analysis, then ACTUALLY converts the matched file(s) to a target
framework and returns the converted code. Chains two capabilities that already exist
separately (`analyze_test_for_conversion` = read+analyze, `convert_test_code` = convert raw
source text) into one `read -> analyze -> convert` tool.

## Design decisions (grounded in the code I read)

- **Tool name: `convert_test_from_path`.** Consistent with the existing verb-first names
  (`convert_test_code`, `analyze_test_for_conversion`). It is the "convert, starting from a
  path" sibling of `convert_test_code` ("convert raw source you already have").
- **Factor the analyze+filter logic out of `analyze_test_for_conversion` into a shared
  module-level helper** so the two tools don't duplicate it. `analyze_test_for_conversion`
  (server.py ~line 420) currently calls `analyze_path` + `filter_result_by_test_case` and
  then post-processes (`matched_test_cases`, empty-match report). Extract that exact body
  into `_analyze_for_conversion(path, test_case_name, source_root) -> dict` and have BOTH
  the existing tool and the new tool call it. This is a pure refactor of existing behavior —
  the existing tool's output must stay byte-for-byte identical (its 3 tests in
  `tests/test_analyze_test_for_conversion.py` must still pass unchanged).
- **Testability — split the new tool into an async wrapper + a sync core.** `convert_test_code`
  builds its LLM callable inline via `make_llm_call(ctx)` and `make_llm_call` is imported
  *locally inside the function*, so it cannot be monkeypatched at module scope and there is
  no LLM backend available in tests (confirmed: `config/.env` has only GEMINI keys, no
  `GROQ_API_KEY`; `svc.groq_available()` returns `False`; a plain pytest run has no MCP
  `ctx` for sampling). Therefore put all non-LLM logic (analysis chaining, per-file disk
  read, per-file conversion loop, result assembly, markdown report) in a module-level
  **synchronous** helper `_convert_from_path_core(path, to_framework, test_case_name,
  from_framework, source_root, llm_call, llm_mode) -> dict` that takes an already-built
  `llm_call`. The `@mcp.tool()` async wrapper only calls `make_llm_call(ctx)` then
  `anyio.to_thread.run_sync(_convert_from_path_core, ...)`. Tests call the core directly with
  a scripted stub `llm_call` (the `_ScriptedLLM` pattern from
  `tests/test_conversion_service.py`). This keeps the stub identical to production's
  `llm_call` contract `(system_prompt, user_prompt) -> str`, so no production code path is
  faked away.
  - Note on threading: `_convert_from_path_core` runs inside `anyio.to_thread.run_sync`, so
    it may call `convert(...)` directly (synchronous). The sampling `llm_call` returned by
    `make_llm_call` already bridges back to the event loop via `anyio.from_thread.run` and
    only works from inside a worker thread — which this is. This mirrors exactly how
    `convert_test_code` wraps `convert`.
- **Multi-file shape.** There is no `convert_test_path` in server.py (the comment at
  ~line 662 says multi-file conversion "is not yet re-implemented"). So the per-file list
  shape is defined here, modeled on `ConversionResult.to_dict()`: a top-level `files` list
  where each entry is one source file with its converted code and per-file reports, plus
  per-file error capture so one bad file does not abort the run.
- **Source framework defaults to `"Robot Framework"`** (these local files are Robot) but is
  an overridable `from_framework` argument, passed straight into `ConversionRequest`.
- **Source TEXT comes from re-reading each matched file from disk.** The analyzer is
  per-test-case and does not keep raw file text; each file report carries `file_path`. The
  core reads `Path(file_path).read_text(encoding="utf-8")` per matched file and converts the
  whole file's text (conversion operates on a file, not a single test case). The
  `test_case_name` filter still scopes *which files* are converted (only files that have at
  least one matching test case survive filtering), matching `filter_result_by_test_case`,
  which drops files with no matching test case.

## Verification backend reality (read before writing tests)

- `config/.env` contains only `GEMINI_API_KEY` / `GEMINI_MODEL`; there is **no**
  `GROQ_API_KEY`, and `svc.groq_available()` returns `False`. A plain `pytest` run has no MCP
  `ctx`, so true MCP sampling is unavailable too. **Do NOT rely on a live LLM.** Unit-test
  everything AROUND the LLM by passing a scripted stub `llm_call` into
  `_convert_from_path_core` (canned valid Robot/Playwright snippet).
- Current suite baseline (observed by running it): **106 passed, 1 warning**. The task brief's
  "27 passing" is stale — use 106 as the regression baseline. Nothing may regress; the new
  tests add to this count.
- The live-GROQ path in the task brief is not reachable in this environment, so the plan's
  verification uses the stub path. If a `GROQ_API_KEY` is later added to `config/.env`, step
  6's conversion-smoke test would also exercise the real backend, but it is written to work
  with the stub regardless.

---

## Plan

- [ ] 1. Factor the shared analyze+filter logic out of `analyze_test_for_conversion` into a
      module-level helper `_analyze_for_conversion(path, test_case_name="", source_root="")
      -> dict[str, Any]` in server.py. Move the current body of `analyze_test_for_conversion`
      (the `analyze_path` call, `error` passthrough, `filter_result_by_test_case`,
      `matched_test_cases`, and the empty-match report block) verbatim into the helper, then
      make `analyze_test_for_conversion` a thin wrapper that `return _analyze_for_conversion(
      path, test_case_name, source_root)`. Keep the tool's rich docstring and decorators
      unchanged. Do NOT change any output values.
      Files: `mcp_server/server.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest -q tests/test_analyze_test_for_conversion.py`
      — all 3 existing tests still pass (proves the refactor preserved behavior).

- [ ] 2. Add the synchronous core helper `_convert_from_path_core(path, to_framework,
      test_case_name="", from_framework="Robot Framework", source_root="", *, llm_call,
      llm_mode) -> dict[str, Any]` in server.py, placed right after `convert_test_code`
      (so it sits with the conversion section). Behavior, in order:
      (a) `analysis = _analyze_for_conversion(path, test_case_name, source_root)`; if
      `"error" in analysis`, return it unchanged (bad-path / not-a-.robot / robot-not-
      installed passthrough).
      (b) If `test_case_name` is set and `analysis.get("matched_test_cases", analysis.get(
      "total_test_cases", 0)) == 0`, return a NON-error dict (`files: []`, a top-level
      `report` stating nothing matched, echoing `path`, `test_case_name`, `from_framework`,
      `to_framework`) — do NOT attempt conversion. Mirror the empty-match wording
      `_analyze_for_conversion` already produces.
      (c) Collect unique source file paths from `analysis["files"]` where the entry has no
      `error` (skip per-file parse errors). For each path: read text via
      `Path(fp).read_text(encoding="utf-8")`; wrap the read + `convert(ConversionRequest(
      source_code=text, from_framework=from_framework, to_framework=to_framework,
      llm_call=llm_call))` in try/except so one failing file yields a per-file
      `{"file_path": fp, "error": str(exc)}` entry instead of aborting. On success build a
      per-file entry: `file_path`, `code` (=result.code), `target_language`, `is_valid`,
      `assertion_report`, `parity_report`, `report` (=result.render_report()).
      (d) Return a JSON-serializable dict: `path`/`input_path`, `test_case_name`,
      `from_framework`, `to_framework`, `llm_mode`, `analysis` (the full readiness block from
      step a, which already includes its markdown under `analysis["report"]`), `files` (the
      per-file list), and a top-level `report` markdown summarizing files analyzed, readiness
      counts + blockers/custom libs from `analysis["readiness_counts"]`, and per-file
      conversion outcome (valid?, parity). Use only stdlib + already-imported helpers.
      Files: `mcp_server/server.py`
      Verify: `.\.venv\Scripts\python.exe -c "import mcp_server.server"` from repo root under
      the venv — imports with no error (catches syntax/NameErrors in the new helper).

- [ ] 3. Add the `@mcp.tool()` / `@log_tool_call` async wrapper
      `async def convert_test_from_path(path: str, to_framework: str, test_case_name: str = "",
      from_framework: str = "Robot Framework", source_root: str = "", ctx: Context = ...) ->
      dict[str, Any]` immediately after the core helper. Body: `from mcp_server.sampling import
      make_llm_call` + `import anyio` (local imports, matching `convert_test_code`'s style);
      `llm_call, llm_mode = make_llm_call(ctx)`; then
      `return await anyio.to_thread.run_sync(functools.partial(_convert_from_path_core, path,
      to_framework, test_case_name, from_framework, source_root, llm_call=llm_call,
      llm_mode=llm_mode))` (add `import functools` locally, or wrap in a nested `def`
      exactly as `convert_test_code` does with `_run_conversion`). Write a rich LLM-facing
      docstring: state it is the one-call read-from-local-path -> analyze -> convert tool;
      list args (`path` local abs path to .robot file or dir; `to_framework` required target
      e.g. "Playwright"; `test_case_name` optional case-insensitive substring filter, same
      semantics as `analyze_test_for_conversion`; `from_framework` overridable source default
      "Robot Framework"; `source_root` passed to analyzer); describe the return dict; and
      cross-reference `analyze_test_for_conversion` (analysis only) and `convert_test_code`
      (convert raw source text you already hold). Keep the "never emits CI/CD" note consistent
      with `convert_test_code`.
      Files: `mcp_server/server.py`
      Verify: `.\.venv\Scripts\python.exe -c "import asyncio, mcp_server.server as s;
      print('convert_test_from_path' in [t.name for t in asyncio.run(s.mcp.list_tools())])"`
      — prints `True` (tool is registered and discoverable via `mcp.list_tools()`).

- [ ] 4. Create `tests/test_convert_test_from_path.py` following the existing test style
      (`sys.path.insert` bootstrap like `tests/test_analyze_test_for_conversion.py`; a
      `_robot_available()` early-return guard; a `_ScriptedLLM` stub copied/adapted from
      `tests/test_conversion_service.py` returning a canned VALID Robot/Playwright snippet).
      Import `_convert_from_path_core` from `mcp_server.server`. Cover, calling the CORE
      directly with the stub `llm_call` (no live LLM):
      - single file + matching `test_case_name`: result has non-empty top-level `files`, each
        entry has non-empty `code`, and `analysis` block present with `matched_test_cases>=1`.
      - directory (multi-file) branch: `files` has more than one entry; `llm_mode` echoed.
      - test-case filtering: a `test_case_name` that matches nothing returns the NON-error
        empty dict (`files == []`, `report` mentions nothing matched), and `llm_call` is never
        invoked (assert `_ScriptedLLM.call_count == 0`).
      - error passthrough on bad path: `_convert_from_path_core(r"C:\no\such\nope.robot", ...)`
        returns a dict with `"error"` and never calls the stub.
      - per-file error isolation: point at a dir and monkeypatch/supply a path list where one
        file read fails (or assert the per-file try/except shape by converting a dir that
        includes a parse-error file) — one bad file produces a `{"file_path", "error"}` entry
        while others still convert. (If hard to trigger a read failure deterministically,
        assert instead that every non-error file entry carries `code` and `is_valid` keys.)
      - JSON-serializable: `json.dumps(result)` on a successful result must not raise.
      Use the real fixture tree under
      `..\autotest_2_extracted\autotest\core-tests\component\analytics` (dir) and
      `...\analytics\combined.robot` (single file), guarded by existence checks like the
      existing analyzer tests.
      Files: `tests/test_convert_test_from_path.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest -q tests/test_convert_test_from_path.py`
      — all new tests pass.

- [ ] 5. (If `GROQ_API_KEY` is configured — check `config/.env` and `svc.groq_available()`;
      currently it is NOT, so this step is a documented no-op) add one guarded live-conversion
      smoke test that converts a single test case from
      `...\analytics\combined.robot` to "Playwright" through the async
      `convert_test_from_path` tool and asserts the result has a non-empty `files` list with
      non-empty `code`. Guard it with `pytest.mark.skipif(not groq_available())` so it is
      skipped (not failed) in the current keyless environment and only runs where a backend
      exists. Do NOT fabricate an LLM for this path.
      Files: `tests/test_convert_test_from_path.py`
      Verify: `.\.venv\Scripts\python.exe -m pytest -q tests/test_convert_test_from_path.py`
      — the live test is skipped (reported as skipped), stub tests still pass.

- [ ] 6. Full regression + registration gate. Run the entire suite and confirm no regression
      against the observed baseline, and re-confirm import + tool registration.
      Files: none (verification only)
      Verify: (a) `.\.venv\Scripts\python.exe -c "import mcp_server.server"` — no error.
      (b) `.\.venv\Scripts\python.exe -c "import asyncio, mcp_server.server as s;
      print('convert_test_from_path' in [t.name for t in asyncio.run(s.mcp.list_tools())])"`
      — `True`. (c) `.\.venv\Scripts\python.exe -m pytest -q` — at least **106** prior tests
      still pass plus the new ones (0 failures; the pre-existing `test_groq_connection`
      return-value warning is expected and not a regression).

- [ ] 7. Clean up. Confirm no throwaway/scratch files were left in the repo (no
      `_throwaway_*` / `_tmp_*`); the only added test file is the proper
      `tests/test_convert_test_from_path.py` matching existing style. Remove any ad-hoc
      scripts created during development.
      Files: repo tree (remove scratch files if any)
      Verify: `.\.venv\Scripts\python.exe -m pytest -q` still green, and
      `git status --porcelain` shows only the intended changes
      (`mcp_server/server.py`, `tests/test_convert_test_from_path.py`).

## Notes / open assumptions

- Scope guard: this tool must never emit CI/CD / GitHub Actions / deployment config — the
  underlying `convert`/`service.py` already strips any CI/CD trailer, and the docstring
  restates the ban. No CI/deploy files are touched by this task.
- If reading a file as UTF-8 fails for a real fixture, fall back to
  `read_text(encoding="utf-8", errors="replace")` inside the per-file try/except rather than
  aborting the run.
- The per-file list is the authoritative conversion output; the `analysis` block and the
  top-level `report` are the "understood the conversion needs" phase surfaced alongside it,
  satisfying the user's "read -> understand needs -> convert" ask in a single call.
