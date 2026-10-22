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

import logging
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
            from services.app_services import get_groq_client, get_kb
            from src.conversion.service import ConversionRequest, convert
            from src.tools.formatters import resolve_framework

            client = get_groq_client()

            def _llm_call(system_prompt: str, user_prompt: str) -> str:
                result = client.chat(
                    messages=[{"role": "user", "content": user_prompt}],
                    system=system_prompt,
                    temperature=0.1,
                )
                return result.get("content") or result.get("reasoning") or ""

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
            from services.app_services import get_groq_client, get_kb
            from src.conversion.consistency import check_consistency
            from src.tools.formatters import resolve_framework

            client = get_groq_client()

            def _llm_call(system_prompt: str, user_prompt: str) -> str:
                r = client.chat(
                    messages=[{"role": "user", "content": user_prompt}],
                    system=system_prompt,
                    temperature=0.1,
                )
                return r.get("content") or r.get("reasoning") or ""

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


def _show_result() -> None:
    result = st.session_state.get("studio_result")
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
                           key="btn_dl_single")

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
