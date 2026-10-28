"""Code Studio page — single-file test conversion.

Rewritten to use src.conversion.service (the shared conversion pipeline
also used by the MCP server's convert_test_code tool), instead of the
old ToolExecutor.execute("convert_test_cases", ...) path.

Scope notes:
  - CI/CD / GitHub Actions content is never generated or shown here —
    this page performs code conversion only (see src/conversion's
    base_system_prompt.md, which explicitly forbids it).
  - Multi-file/project conversion is not yet re-implemented under the new
    architecture — only single-file conversion is available for now.
  - The LLM backend here is always Groq (GROQ_API_KEY in config/.env).
    True MCP sampling is specific to the MCP server transport — see
    mcp_server/sampling.py — Streamlit has no MCP client to sample from.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

import streamlit as st

from ui.components import render_thinking

logger = logging.getLogger(__name__)


_SUPPORTED_FRAMEWORKS = ["Robot Framework", "Playwright", "Selenium", "K6"]

# How to run the CONVERTED test suite locally — not CI/CD config, just a
# plain install+run command the user types in their own terminal.
_RUN_INSTRUCTIONS: dict[str, tuple[str, str, str]] = {
    "robot framework": (
        "robotframework>=7.0\nrobotframework-seleniumlibrary>=6.0\nrobotframework-requests>=0.9\nSelenium>=4.0\nrequests>=2.31",
        "robot tests/",
        "pip install -r requirements.txt",
    ),
    "playwright": (
        "playwright>=1.40.0\npytest-playwright>=0.4.0\npytest>=7.0",
        "pytest tests/ -v",
        "pip install -r requirements.txt\nplaywright install",
    ),
    "selenium": (
        "selenium>=4.0\npytest>=7.0\nwebdriver-manager>=4.0",
        "pytest tests/ -v",
        "pip install -r requirements.txt",
    ),
    "k6": (
        "# K6 is a standalone Go binary — no pip packages required\n# Install via: brew install k6  OR  apt-get install k6",
        "k6 run tests/load_test.js",
        "# Install k6:\nbrew install k6\n# or: apt-get install k6",
    ),
}

_LANG_DISPLAY = {
    "python":     ("python",         ".py",    "text/x-python"),
    "javascript": ("javascript",     ".js",    "text/javascript"),
    "typescript": ("typescript",     ".ts",    "text/typescript"),
    "java":       ("java",           ".java",  "text/x-java"),
    "c#":         ("csharp",         ".cs",    "text/plain"),
    "ruby":       ("ruby",           ".rb",    "text/x-ruby"),
    "go":         ("go",             ".go",    "text/x-go"),
    "robot":      ("robotframework", ".robot", "text/plain"),
}


def _build_llm_call(client):
    """Build the transport-agnostic ``(system_prompt, user_prompt) -> str``
    closure over a Groq client. Single source of the page's LLM wiring —
    reused by paste mode, path mode and the consistency check so the closure
    is never duplicated."""
    def _llm_call(system_prompt: str, user_prompt: str) -> str:
        result = client.chat(
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
            temperature=0.1,
        )
        return result.get("content") or result.get("reasoning") or ""

    return _llm_call


def _analyze_path_for_studio(path: str, test_case_name: str, source_root: str) -> dict:
    """Pure (Streamlit-free) analyze + filter step for the path flow.

    Calls ``analyze_path`` and, when a ``test_case_name`` is given, narrows the
    result with ``filter_result_by_test_case`` (case-insensitive substring).
    Returns the analyzer's ``{"error": ...}`` dict unchanged on a bad path /
    no .robot files / missing robotframework, so the caller can surface it.
    """
    from src.analysis.robot_dependency_analyzer import (
        analyze_path,
        filter_result_by_test_case,
    )

    result = analyze_path(path, source_root=source_root)
    if "error" in result:
        return result
    return filter_result_by_test_case(result, test_case_name)


def _convert_matched_files(analysis: dict, from_fw: str, to_fw: str, llm_call, to_fw_data) -> list[dict]:
    """Pure (Streamlit-free) per-file read + convert loop for the path flow.

    Mirrors the MCP server's path core: collect each unique, non-error
    ``file_path`` from ``analysis["files"]``, read its source text from disk
    (UTF-8; ``errors="replace"`` fallback on a decode error), and run it
    through the SAME ``convert(ConversionRequest(... llm_call=...), to_fw_data=...)``
    pipeline the paste flow uses. Each file is wrapped in its own try/except so
    one failing file does not abort the run.

    Returns an ordered list of dicts, one per file: either
    ``{"file_path": fp, "result": ConversionResult}`` or
    ``{"file_path": fp, "error": "<message>"}``. ``llm_call`` and ``to_fw_data``
    are injected so this is unit-testable with a stub llm_call.
    """
    from src.conversion.service import ConversionRequest, convert

    file_results: list[dict] = []
    seen: set[str] = set()
    for fd in analysis.get("files", []):
        if fd.get("error"):
            continue
        fp = fd.get("file_path")
        if not fp or fp in seen:
            continue
        seen.add(fp)
        try:
            try:
                text = Path(fp).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = Path(fp).read_text(encoding="utf-8", errors="replace")
            result = convert(
                ConversionRequest(
                    source_code=text,
                    from_framework=from_fw,
                    to_framework=to_fw,
                    llm_call=llm_call,
                ),
                to_fw_data=to_fw_data,
            )
            file_results.append({"file_path": fp, "result": result})
        except Exception as exc:  # noqa: BLE001 — one bad file must not abort the run
            file_results.append({"file_path": fp, "error": str(exc)})
            logger.error("Path-mode conversion failed for %s: %s", fp, exc)
    return file_results


# Single source of truth for the generated bundle's root folder / download name.
_PROJECT_ROOT = "converted_project"


def _safe_test_filename(file_path: str, ext: str, used: set[str]) -> str:
    """Pure (Streamlit-free) safe, unique test filename for a converted file.

    Derives ``test_<stem><ext>`` from the SOURCE path's stem, replacing any
    character outside ``[A-Za-z0-9_-]`` with ``_``. On collision with a name
    already in ``used`` (two sources sharing a stem), appends ``_1``, ``_2`` …
    until unique. ``used`` is mutated with the returned name.
    """
    stem = Path(file_path).stem or "test"
    safe_stem = re.sub(r"[^A-Za-z0-9_-]", "_", stem)
    base = f"test_{safe_stem}"
    candidate = f"{base}{ext}"
    i = 1
    while candidate in used:
        candidate = f"{base}_{i}{ext}"
        i += 1
    used.add(candidate)
    return candidate


def _build_project_zip(file_results: list[dict], to_fw: str) -> bytes:
    """Pure (Streamlit-free) builder of a runnable project ZIP (bytes).

    Produces an in-memory ZIP with this layout under a single project root:
      - ``converted_project/tests/<test file per successfully converted entry>``
        — one file per ``file_results`` entry that carries a ``"result"``;
        extension comes from ``_LANG_DISPLAY`` via ``_target_lang_display`` and
        the filename from ``_safe_test_filename`` (de-duplicated on collision).
      - ``converted_project/requirements.txt`` — the requirements string from
        ``_get_run_instructions(to_fw.lower())[0]`` verbatim.
      - ``converted_project/README.md`` — install + run commands from
        ``_get_run_instructions`` plus a one-line code-only note.

    Code conversion only: NO CI/CD, GitHub Actions, or deployment content is
    ever written. Entries without a ``"result"`` (errored files) are skipped.
    Returns the ZIP bytes.
    """
    req, run_cmd, install_cmd = _get_run_instructions((to_fw or "").lower())

    readme = (
        f"# Converted Test Project ({to_fw})\n\n"
        "This bundle contains converted test code only — no pipeline or "
        "deployment configuration is generated.\n\n"
        "## Install\n\n"
        f"```bash\n{install_cmd}\n```\n\n"
        "## Run\n\n"
        f"```bash\n{run_cmd}\n```\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used: set[str] = set()
        for fr in file_results:
            result = fr.get("result")
            if result is None:
                continue
            _, ext, _ = _target_lang_display(result.target_language)
            name = _safe_test_filename(fr.get("file_path", "test"), ext, used)
            zf.writestr(f"{_PROJECT_ROOT}/tests/{name}", result.code)
        zf.writestr(f"{_PROJECT_ROOT}/requirements.txt", req)
        zf.writestr(f"{_PROJECT_ROOT}/README.md", readme)
    return buf.getvalue()


def render() -> None:
    st.markdown("""
    <div class="page-header">
        <div class="page-header-left">
            <div class="breadcrumb"><span>Home</span><span class="breadcrumb-sep">›</span><span>Framework Migration</span></div>
            <div class="page-title">🔄 Framework Migration</div>
            <div class="page-subtitle">Convert test code between frameworks — code conversion only, no CI/CD content</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        from_fw = st.selectbox("From framework", _SUPPORTED_FRAMEWORKS, index=0, key="convert_from")
    with col2:
        to_options = [f for f in _SUPPORTED_FRAMEWORKS if f != from_fw]
        to_fw = st.selectbox("To framework", to_options, index=0, key="convert_to")

    if from_fw and to_fw:
        st.markdown(
            f'<div class="breadcrumb" style="margin:0.25rem 0 0.75rem;">'
            f'<span>{from_fw}</span><span class="breadcrumb-sep">→</span>'
            f'<span style="color:#2d7d6f;font-weight:700;">{to_fw}</span></div>',
            unsafe_allow_html=True,
        )
    st.markdown("---")

    v = st.session_state.studio_widget_key_v
    _single_file_mode(from_fw, to_fw, v)


def _single_file_mode(from_fw: str, to_fw: str, v: int) -> None:
    mode = st.radio(
        "Input mode",
        ["Paste code", "Read from local path"],
        horizontal=True,
        key="studio_input_mode",
    )
    if mode == "Read from local path":
        _path_mode(from_fw, to_fw)
        return

    _paste_mode(from_fw, to_fw, v)


def _paste_mode(from_fw: str, to_fw: str, v: int) -> None:
    source_code = st.text_area(
        "Paste source test code here", height=200,
        placeholder=f"Paste your {from_fw} test code here…",
        key=f"convert_source_{v}",
    )
    do_convert = st.button("🔄 Convert", key="btn_convert", type="primary")

    if st.session_state.get("studio_result") and not do_convert:
        _show_result()
        return

    if do_convert:
        if not source_code.strip():
            st.warning("Paste some source test code first.")
            return

        loading_slot = st.empty()
        with loading_slot.container():
            render_thinking(f"Converting {from_fw} → {to_fw}…")

        try:
            from services.app_services import get_llm_client, get_kb
            from src.conversion.service import ConversionRequest, convert
            from src.tools.formatters import resolve_framework

            client = get_llm_client()
            _llm_call = _build_llm_call(client)

            to_fw_data = resolve_framework(get_kb(), to_fw)
            result = convert(
                ConversionRequest(
                    source_code=source_code,
                    from_framework=from_fw,
                    to_framework=to_fw,
                    llm_call=_llm_call,
                ),
                to_fw_data=to_fw_data,
            )

            loading_slot.empty()
            st.session_state.studio_result = result
            st.session_state.studio_to_fw = to_fw
            st.session_state.studio_source_code = source_code
            st.session_state.studio_from_fw = from_fw
            st.session_state.studio_consistency_report = None  # stale — frameworks/source changed
            _show_result()

        except Exception as exc:
            loading_slot.empty()
            st.error(f"Conversion failed: {exc}")
            logger.error("Conversion error: %s", exc)

    _consistency_check_section()


def _path_mode(from_fw: str, to_fw: str) -> None:
    """Read .robot test(s) from a local file/directory, SHOW the conversion
    analysis (readiness, blockers, custom libs), then convert each matched
    file through the same pipeline the paste flow uses. Code conversion only —
    no CI/CD content."""
    st.caption(
        f"Source framework is **{from_fw}** (from the selector above); target is **{to_fw}**."
    )
    path = st.text_input(
        "Local path (a .robot file or a directory of them)",
        key="studio_path_input",
        placeholder=r"e.g. C:\path\to\tests  or  C:\path\to\suite.robot",
    )
    test_case = st.text_input(
        "Test-case name (optional, case-insensitive substring)",
        key="studio_path_testcase",
    )
    st.caption("Leave the test-case name empty to analyze and convert all test cases under the path.")
    source_root = st.text_input(
        "Source root (optional — helps resolve library/resource imports)",
        key="studio_path_root",
    )
    do_run = st.button("🔍 Read & Analyze → Convert", key="btn_path_convert", type="primary")

    # A download button (ZIP / single file) triggers a Streamlit rerun on which
    # do_run is False. Persist the last run's outcome in session_state and
    # re-render it here so the whole result view (analysis + every per-file
    # result + download buttons) survives that rerun instead of vanishing —
    # mirroring how paste mode persists/re-renders studio_result. Only a fresh
    # "Read & Analyze → Convert" click recomputes (see below, where we clear).
    if not do_run:
        if st.session_state.get("studio_path_file_results") is not None:
            _render_path_results(
                st.session_state.get("studio_path_analysis") or {},
                st.session_state.get("studio_path_file_results") or [],
                st.session_state.get("studio_path_from_fw", from_fw),
                st.session_state.get("studio_path_to_fw", to_fw),
            )
        return

    # Fresh run: drop any previously persisted path results so changed inputs
    # recompute cleanly and a stale view is never re-rendered.
    st.session_state.studio_path_file_results = None
    st.session_state.studio_path_analysis = None
    st.session_state.studio_path_to_fw = None
    st.session_state.studio_path_from_fw = None
    st.session_state.studio_consistency_report = None  # stale — new run/inputs

    if not path.strip():
        st.warning("Enter a local file or directory path first.")
        return

    analysis = _analyze_path_for_studio(path.strip(), test_case.strip(), source_root.strip())
    if "error" in analysis:
        st.error(analysis["error"])
        return

    # A test-case name was given but matched nothing — show what was searched,
    # and do NOT convert.
    if test_case.strip() and analysis.get("total_test_cases", 0) == 0:
        st.warning(
            f"No test case matching '{test_case.strip()}' was found under '{path.strip()}'. "
            "Nothing to convert — adjust the name or clear it to convert everything."
        )
        return

    if analysis.get("total_test_cases", 0) == 0:
        # Still render the analysis so the user sees what was searched.
        with st.expander("🔎 Conversion analysis", expanded=True):
            st.markdown(analysis.get("report", "_No analysis report._"))
        st.info("No test cases found to convert under this path.")
        return

    loading_slot = st.empty()
    with loading_slot.container():
        render_thinking(f"Converting matched {from_fw} test(s) → {to_fw}…")

    try:
        from services.app_services import get_llm_client, get_kb
        from src.tools.formatters import resolve_framework

        client = get_llm_client()
        llm_call = _build_llm_call(client)
        to_fw_data = resolve_framework(get_kb(), to_fw)
        file_results = _convert_matched_files(analysis, from_fw, to_fw, llm_call, to_fw_data)
    except Exception as exc:
        loading_slot.empty()
        st.error(f"Conversion failed: {exc}")
        logger.error("Path-mode conversion error: %s", exc)
        return
    finally:
        loading_slot.empty()

    if not file_results:
        st.info("No convertible files were found under this path.")
        return

    # Persist the just-computed outcome so a download-button rerun re-renders it.
    st.session_state.studio_path_file_results = file_results
    st.session_state.studio_path_analysis = analysis
    st.session_state.studio_path_to_fw = to_fw
    st.session_state.studio_path_from_fw = from_fw

    _render_path_results(analysis, file_results, from_fw, to_fw)


def _render_path_results(analysis: dict, file_results: list[dict], from_fw: str, to_fw: str) -> None:
    """Render the path-mode outcome: matched-count caption, the conversion
    analysis, every per-file result, and the project-ZIP download. Called both
    right after a fresh run and on a later rerun (e.g. a download-button click)
    with the persisted values, so the full view survives the rerun."""
    # Render the ANALYSIS first — this is the "understand the conversion needs" view.
    rc = analysis.get("readiness_counts", {})
    st.caption(
        f"Matched {analysis.get('total_test_cases', 0)} test case(s) across "
        f"{analysis.get('total_files', 0)} file(s) — "
        f"{rc.get('self_contained', 0)} self-contained, "
        f"{rc.get('needs_custom_libs', 0)} needs custom libs, "
        f"{rc.get('blocked', 0)} blocked."
    )
    with st.expander("🔎 Conversion analysis", expanded=True):
        st.markdown(analysis.get("report", "_No analysis report._"))

    successes = [fr for fr in file_results if "result" in fr]

    # Exactly one successful file → render through the EXISTING single-result
    # path so output (and the consistency check) look identical to paste mode.
    if len(file_results) == 1 and len(successes) == 1:
        fr = successes[0]
        st.session_state.studio_result = fr["result"]
        st.session_state.studio_to_fw = to_fw
        st.session_state.studio_from_fw = from_fw
        st.session_state.studio_source_code = _safe_read(fr["file_path"])
        st.markdown(f"**Converted:** `{fr['file_path']}`")
        _show_result()
        _render_project_zip_button(file_results, to_fw)
        _consistency_check_section()
        return

    # Multiple files (or a single file that errored) → one expander each, with
    # per-file error isolation. Consistency check is single-result only.
    st.session_state.studio_result = None
    for idx, fr in enumerate(file_results):
        with st.expander(f"📄 {fr['file_path']}", expanded=(len(file_results) == 1)):
            if "error" in fr:
                st.error(f"Conversion failed: {fr['error']}")
            else:
                _show_result(fr["result"], to_fw, key_suffix=f"path_{idx}")
    _render_project_zip_button(file_results, to_fw)


def _render_project_zip_button(file_results: list[dict], to_fw: str) -> None:
    """Thin st.* wrapper around the pure _build_project_zip helper: offers a
    runnable project folder (tests/ + requirements.txt + README.md) as a ZIP.
    Every successfully converted file is bundled under tests/."""
    if not any("result" in fr for fr in file_results):
        return
    zip_bytes = _build_project_zip(file_results, to_fw)
    st.download_button(
        "⬇ Download project (.zip)",
        data=zip_bytes,
        file_name="converted_project.zip",
        mime="application/zip",
        key="btn_dl_project_zip",
    )


def _safe_read(file_path: str) -> str:
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return Path(file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _consistency_check_section() -> None:
    """Point 6: run the same conversion N times and report how
    reproducible/accurate the result is."""
    result = st.session_state.get("studio_result")
    if result is None:
        return

    st.markdown("---")
    st.markdown("#### 🔁 Consistency check")
    st.caption(
        "Runs this exact conversion multiple times and reports how often the "
        "output compiles, passes structural checks, and agrees on assertion "
        "count — proving the result is reproducible, not a one-shot fluke."
    )
    runs = st.slider("Number of runs", min_value=2, max_value=10, value=5, key="consistency_runs")
    run_check = st.button("▶ Run consistency check", key="btn_consistency")

    if run_check:
        source_code = st.session_state.get("studio_source_code", "")
        from_fw = st.session_state.get("studio_from_fw", "")
        to_fw = st.session_state.get("studio_to_fw", "")
        if not source_code:
            st.warning("Convert a file first, then run the consistency check on it.")
            return

        loading_slot = st.empty()
        with loading_slot.container():
            render_thinking(f"Running {runs} conversion attempts…")
        try:
            from services.app_services import get_llm_client, get_kb
            from src.conversion.consistency import check_consistency
            from src.tools.formatters import resolve_framework

            client = get_llm_client()
            _llm_call = _build_llm_call(client)

            to_fw_data = resolve_framework(get_kb(), to_fw)
            report = check_consistency(
                source_code=source_code,
                from_framework=from_fw,
                to_framework=to_fw,
                llm_call=_llm_call,
                runs=runs,
                to_fw_data=to_fw_data,
            )
            loading_slot.empty()
            st.session_state.studio_consistency_report = report
        except Exception as exc:
            loading_slot.empty()
            st.error(f"Consistency check failed: {exc}")
            logger.error("Consistency check error: %s", exc)
            return

    report = st.session_state.get("studio_consistency_report")
    if report is not None:
        _render_consistency_report(report)


def _render_consistency_report(report) -> None:
    if report.is_consistent:
        st.success(
            f"✅ Consistent across {report.total_runs} run(s): every run compiled, "
            f"passed structural checks, and agreed on assertion count."
        )
    else:
        st.warning(
            f"⚠️ Not fully consistent: {report.valid_count}/{report.total_runs} run(s) valid "
            f"(score {report.consistency_score:.0%}), assertion counts: {report.assertion_counts}"
        )

    st.markdown(
        f"**Cross-repo parity equivalent:** {report.parity_equivalent_count}/{report.total_runs} run(s)"
    )

    with st.expander("Per-run detail", expanded=not report.is_consistent):
        for r in report.records:
            icon = "✅" if r.is_valid and r.assertion_count == report.assertion_count_mode else (
                   "⚠️" if r.is_valid else "❌")
            st.markdown(
                f"{icon} **Run {r.run_number}** — valid: {r.is_valid}, "
                f"assertions: {r.assertion_count}, attempts: {r.attempts_needed}, "
                f"parity equivalent: {r.is_parity_equivalent}"
            )
            if r.errors:
                st.caption("; ".join(r.errors))


def _target_lang_display(target_language: str):
    return _LANG_DISPLAY.get(target_language, _LANG_DISPLAY["python"])


def _get_run_instructions(to_fw_lower: str) -> tuple[str, str, str]:
    for key, val in _RUN_INSTRUCTIONS.items():
        if key in to_fw_lower:
            return val
    return "pytest>=7.0", "pytest tests/ -v", "pip install -r requirements.txt"


def _render_assertion_report(report: dict | None) -> None:
    if not report:
        return
    status = report.get("status", "pass")
    src_n  = report.get("source_count", 0)
    cvt_n  = report.get("converted_count", 0)
    msg    = report.get("message", "")

    colour = {"pass": "#1e7e34", "warn": "#856404", "fail": "#721c24"}.get(status, "#333")
    bg     = {"pass": "#d4edda", "warn": "#fff3cd", "fail": "#f8d7da"}.get(status, "#eee")

    st.markdown(
        f'<div style="background:{bg};border-left:4px solid {colour};'
        f'padding:0.6rem 1rem;border-radius:4px;margin:0.5rem 0;">'
        f'<b style="color:{colour};">Assertion Parity Check</b><br>'
        f'<span style="color:{colour};">{msg}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    with st.expander("📊 Assertion count breakdown", expanded=(status == "fail")):
        st.markdown(
            f"| | Count |\n|---|---|\n"
            f"| Source assertions | **{src_n}** |\n"
            f"| Converted assertions | **{cvt_n}** |\n"
            f"| Delta | **{cvt_n - src_n:+d}** |"
        )


def _render_parity_report(parity: dict | None) -> None:
    """Point 4: cross-repo-comparator parity check — does the converted
    framework have equivalent validation to the source, per test case?"""
    if parity is None:
        return

    if parity.get("is_equivalent"):
        st.success("✅ Cross-repo parity check: every test case matched with equivalent assertions.")
        return

    st.warning("⚠️ Cross-repo parity check found mismatches between source and converted code:")
    with st.expander("Parity mismatch detail", expanded=True):
        for m in parity.get("mismatches", []):
            st.markdown(
                f"- **'{m['display_a']}'** ↔ **'{m['display_b']}'**: "
                f"{m['assertions_a']} assertion(s) in source vs {m['assertions_b']} in converted code"
            )
        for u in parity.get("only_in_a", []):
            st.markdown(f"- **'{u['display_name']}'** exists in the source but has no matching converted test case")
        for u in parity.get("only_in_b", []):
            st.markdown(f"- **'{u['display_name']}'** appears in the converted code with no equivalent in the source")


def _render_validation_badge(result) -> None:
    """Shows compile-check + structural-check outcome with full retry history."""
    n = len(result.attempts)
    last = result.attempts[-1]

    if result.is_valid:
        st.success(
            f"✅ Converted code compiled and passed structural validation "
            f"({n} attempt{'s' if n != 1 else ''}, tool: {last.compile_result.tool or 'n/a'})."
        )
    else:
        st.error("⚠️ Converted code still has unresolved issues after retrying:")
        if last.compile_result.checked and not last.compile_result.ok:
            st.markdown(f"**Compile errors** (`{last.compile_result.tool}`):")
            for e in last.compile_result.errors:
                st.markdown(f"- {e}")
        if not last.validation.is_valid:
            for e in last.validation.errors:
                st.markdown(f"- {e}")

    if n > 1:
        with st.expander(f"🔁 Retry history ({n} attempts)", expanded=False):
            for a in result.attempts:
                ok = (not a.compile_result.checked or a.compile_result.ok) and a.validation.is_valid
                icon = "✅" if ok else "❌"
                st.markdown(f"**{icon} Attempt {a.attempt_number}**")
                if a.compile_result.checked and not a.compile_result.ok:
                    st.markdown("- Compile: " + "; ".join(a.compile_result.errors))
                if not a.validation.is_valid:
                    st.markdown("- Structural: " + "; ".join(a.validation.errors))


def _show_result(result=None, to_fw=None, key_suffix: str = "single") -> None:
    if result is None:
        result = st.session_state.get("studio_result")
    if to_fw is None:
        to_fw = st.session_state.get("studio_to_fw", "")
    if result is None:
        return

    code_lang, ext, mime = _target_lang_display(result.target_language)
    to_fw_lower = (to_fw or "").lower()
    req, run_cmd, install_cmd = _get_run_instructions(to_fw_lower)

    _render_validation_badge(result)
    _render_assertion_report(result.assertion_report)
    _render_parity_report(result.parity_report)

    with st.expander("📄 Converted Test Code", expanded=True):
        st.code(result.code, language=code_lang)
        st.download_button("⬇ Download converted file", result.code,
                           file_name=f"test_converted{ext}", mime=mime,
                           key=f"btn_dl_{key_suffix}")

    with st.expander("📋 Requirements & Execution Guide", expanded=True):
        col_req, col_run = st.columns(2)
        with col_req:
            st.markdown("**`requirements.txt`**")
            st.code(req, language="text")
        with col_run:
            st.markdown("**Install & Run**")
            st.code(install_cmd + "\n" + run_cmd, language="bash")

    with st.expander("🧾 Prompt sources used for this conversion", expanded=False):
        if result.prompt_sources:
            for s in result.prompt_sources:
                st.markdown(f"- `{s}`")
        else:
            st.markdown("_No prompt files found — used built-in fallback prompt._")
        st.caption(
            "Edit these files under config/conversion_prompts/ to change how "
            "conversions are generated for your whole team — no code change "
            "or redeploy needed."
        )
