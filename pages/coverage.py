"""Coverage Analyser page — progressive disclosure UI."""
from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger(__name__)

_ALIASES = {
    "id":                  ["id", "test_id", "testid", "test id", "tc"],
    "description":         ["description", "desc", "title", "name", "test name"],
    "required_capability": ["required_capability", "capability", "type", "category", "test type"],
    "steps":               ["steps", "step", "test steps", "actions"],
    "expected_result":     ["expected_result", "expected", "expected result", "outcome"],
}


def _pick(row: dict, aliases: list[str]) -> str:
    row_lower = {k.strip().lower(): v for k, v in row.items()}
    for alias in aliases:
        if alias in row_lower:
            return str(row_lower[alias] or "")
    return ""


def _normalise(records: list[dict]) -> list[dict]:
    return [
        {
            "id":                  _pick(r, _ALIASES["id"]),
            "description":         _pick(r, _ALIASES["description"]),
            "required_capability": _pick(r, _ALIASES["required_capability"]).lower() or "ui automation",
            "steps":               _pick(r, _ALIASES["steps"]),
            "expected_result":     _pick(r, _ALIASES["expected_result"]),
        }
        for r in records
    ]


def render() -> None:
    # ── Deferred clear — before any widgets ──────────────────────────
    if st.session_state.pop("_cov_clear_pending", False):
        for k in ("excel_test_cases", "excel_summary", "coverage_result",
                  "coverage_best_fw", "coverage_best_pct", "cicd_yaml", "coverage_analysis"):
            st.session_state[k] = [] if k == "excel_test_cases" else ""
        st.session_state["cov_selected_frameworks"] = []
        st.session_state.pop("_cov_upload_type", None)
        st.rerun()

    st.markdown("""
    <div class="page-header">
        <div class="page-header-left">
            <div class="breadcrumb"><span>Home</span><span class="breadcrumb-sep">›</span><span>Coverage Analyser</span></div>
            <div class="page-title">📊 Coverage Analyser</div>
            <div class="page-subtitle">Map test cases to framework capabilities and find the best fit</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    tcs = st.session_state.excel_test_cases

    # Framework selector
    from services.app_services import get_kb
    kb = get_kb()
    all_fw_names = sorted([fw.framework_name for fw in kb.list_all()])
    st.multiselect(
        "🔍 Frameworks to compare (leave empty to evaluate all)",
        options=all_fw_names,
        default=st.session_state.get("cov_selected_frameworks", []),
        key="cov_selected_frameworks",
        help="Select specific frameworks to compare. If none selected, all are evaluated.",
    )

    # Metric cards
    if tcs:
        caps    = list({tc.get("required_capability", "") for tc in tcs if tc.get("required_capability")})
        best_fw = st.session_state.coverage_best_fw  or "—"
        cov_pct = st.session_state.coverage_best_pct or "—"
        st.markdown(f"""
        <div class="metric-row">
            <div class="metric-card">
                <span class="metric-card-icon">🧪</span>
                <div class="metric-card-value">{len(tcs)}</div>
                <div class="metric-card-label">Test Cases</div>
            </div>
            <div class="metric-card">
                <span class="metric-card-icon">🎯</span>
                <div class="metric-card-value">{len(caps)}</div>
                <div class="metric-card-label">Capabilities</div>
            </div>
            <div class="metric-card">
                <span class="metric-card-icon">🏆</span>
                <div class="metric-card-value" style="font-size:1.1rem">{best_fw}</div>
                <div class="metric-card-label">Best Framework</div>
            </div>
            <div class="metric-card">
                <span class="metric-card-icon">📈</span>
                <div class="metric-card-value">{cov_pct}</div>
                <div class="metric-card-label">Top Coverage</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # Upload section
    with st.expander("📥 Upload Test Cases", expanded=not bool(tcs)):
        st.markdown(
            "Expected fields: `Test ID` · `Description` · `Required Capability` · `Steps` · `Expected Result`"
        )
        tab_xl, tab_json, tab_csv = st.tabs(["📊 Excel", "📋 JSON", "📄 CSV"])

        with tab_xl:
            upload_file_xl = st.file_uploader("Excel file (.xlsx)", type=["xlsx"], key="xl_upload")
            if upload_file_xl:
                st.session_state["_cov_upload_type"] = "xlsx"

        with tab_json:
            st.caption("JSON must be an array of objects with the expected fields.")
            upload_file_json = st.file_uploader("JSON file (.json)", type=["json"], key="cov_json_upload")
            if upload_file_json:
                st.session_state["_cov_upload_type"] = "json"
            with st.expander("📖 Expected JSON format", expanded=False):
                st.code("""
[
  {
    "id": "TC001",
    "description": "Login with valid credentials",
    "required_capability": "ui automation",
    "steps": "Navigate to login page, enter credentials, click login",
    "expected_result": "Dashboard is displayed"
  }
]
                """, language="json")

        with tab_csv:
            st.caption("CSV must have a header row. Column names are matched case-insensitively.")
            upload_file_csv = st.file_uploader("CSV file (.csv)", type=["csv"], key="cov_csv_upload")
            if upload_file_csv:
                st.session_state["_cov_upload_type"] = "csv"

        upload_type = st.session_state.get("_cov_upload_type")
        upload_file = (
            upload_file_xl   if upload_type == "xlsx" else
            upload_file_json if upload_type == "json" else
            upload_file_csv  if upload_type == "csv"  else None
        )

        col_parse, col_reset = st.columns(2)
        with col_parse:
            parse_clicked = st.button("📂 Parse & Analyse", key="btn_parse_xl",
                                      use_container_width=True, type="primary")
        with col_reset:
            if st.button("🗑 Clear", key="btn_clear_xl", use_container_width=True):
                st.session_state["_cov_clear_pending"] = True
                st.rerun()

        if parse_clicked:
            if upload_file is None:
                st.warning("Upload a file first (Excel, JSON, or CSV).")
            else:
                _parse_and_analyse(upload_file, upload_type, kb)

    # ── Results — progressive disclosure ─────────────────────────────
    if st.session_state.get("coverage_analysis"):
        _render_results(st.session_state["coverage_analysis"])


def _parse_and_analyse(upload_file, upload_type: str, kb) -> None:
    import json as _json
    from src.tools.excel_parser import parse_excel_test_cases
    from src.tools.coverage_engine import analyze_coverage, render_coverage_report, build_display_coverage

    with st.spinner("Analysing coverage…"):
        try:
            raw = upload_file.read()
            if upload_type == "xlsx":
                test_cases = parse_excel_test_cases(raw)
            elif upload_type == "json":
                records = _json.loads(raw.decode("utf-8"))
                if isinstance(records, dict):
                    records = records.get("test_cases", records.get("data", [records]))
                if not isinstance(records, list):
                    raise ValueError("JSON must be an array of objects")
                test_cases = _normalise(records)
            elif upload_type == "csv":
                import csv, io as _io
                reader = csv.DictReader(_io.StringIO(raw.decode("utf-8")))
                test_cases = _normalise(list(reader))
            else:
                test_cases = []

            if not test_cases:
                st.error("Could not parse any test cases. Check the file format.")
                return

            _fw_filter = st.session_state.get("cov_selected_frameworks") or None
            analysis   = analyze_coverage(test_cases, kb, frameworks=_fw_filter)
            report     = render_coverage_report(analysis, len(test_cases))
            display    = build_display_coverage(analysis)

            if display["fw_scores"]:
                best_fw  = display["fw_names"][0]
                best_pct = display["fw_scores"][best_fw]["pct"]
                st.session_state.coverage_best_fw  = best_fw
                st.session_state.coverage_best_pct = f"{best_pct:.0f}%"

            # Store final display model — rendering does zero computation
            st.session_state.excel_test_cases  = test_cases
            st.session_state.coverage_result   = report
            st.session_state.coverage_analysis = display

        except Exception as exc:
            st.error(f"Analysis failed: {exc}")
            logger.error("Coverage analysis error: %s", exc)


def _render_results(display: dict) -> None:
    """Render pre-computed display model — no computation here."""
    import pandas as pd

    if not display:
        return

    matrix    = display["matrix"]
    fw_names  = display["fw_names"]
    fw_scores = display["fw_scores"]
    tc_order  = display["tc_order"]
    total     = len(tc_order)

    st.markdown("---")

    # ── 1. Recommendation card ────────────────────────────────────────
    if fw_names:
        best     = fw_names[0]
        best_pct = fw_scores[best]["pct"]
        sup      = len(fw_scores[best]["supported"])
        unsup    = fw_scores[best]["unsupported"]

        st.markdown("## 🏆 Recommended Framework")
        pct_bar = int(best_pct)
        st.markdown(f"""
        <div style="background:#f0faf7;border:1px solid #b2dfdb;border-radius:10px;padding:1.2rem 1.5rem;margin-bottom:1rem;">
            <div style="font-size:1.4rem;font-weight:800;color:#1e2d2b;">{best}</div>
            <div style="margin:0.5rem 0 0.3rem;">
                <div style="background:#e0f2ef;border-radius:6px;height:14px;width:100%;">
                    <div style="background:#2d7d6f;border-radius:6px;height:14px;width:{pct_bar}%;"></div>
                </div>
            </div>
            <div style="color:#2d7d6f;font-weight:700;font-size:1.1rem;">{best_pct:.0f}% coverage</div>
            <div style="color:#555;font-size:0.85rem;">{sup} / {total} test cases supported</div>
        </div>
        """, unsafe_allow_html=True)

        if best_pct < 100 and unsup:
            gap_coverage: dict[str, list] = {}
            for tc_id in unsup:
                for fw in fw_names[1:]:
                    if matrix[tc_id]["support"].get(fw, "false") != "false":
                        gap_coverage.setdefault(fw, []).append(tc_id)
                        break
            if gap_coverage:
                alts = list(gap_coverage.keys())[:2]
                st.info(
                    f"Combine **{best}** with **{', '.join(alts)}** "
                    f"to cover the remaining {len(unsup)} test case(s)."
                )

    # ── 2. Top frameworks summary ─────────────────────────────────────
    st.markdown("### Top Frameworks")
    summary_rows = []
    for rank, fw in enumerate(fw_names, 1):
        scores = fw_scores[fw]
        pct    = scores["pct"]
        badge  = "✅" if pct == 100 else ("🟢" if pct >= 80 else ("🟡" if pct >= 50 else "🔴"))
        summary_rows.append({
            "Rank": f"#{rank}",
            "Framework": fw,
            "Coverage %": f"{pct:.0f}%",
            "Supported ✅": len(scores["supported"]),
            "Not Supported ❌": len(scores["unsupported"]),
            "Fit": badge,
        })

    def _colour_pct(val):
        p = int(val.replace("%", ""))
        if p == 100: return "color:#155724;font-weight:700"
        if p >= 80:  return "color:#0c5460;font-weight:600"
        if p >= 50:  return "color:#856404"
        return "color:#721c24"

    df_sum = pd.DataFrame(summary_rows)
    st.dataframe(
        df_sum.style.map(_colour_pct, subset=["Coverage %"]),
        width="stretch", hide_index=True,
    )

    # ── 3. Detail — on demand ─────────────────────────────────────────
    with st.expander("🔍 View full test-case × framework matrix", expanded=False):
        rows = []
        for tc_id in tc_order:
            d   = matrix[tc_id]
            row = {
                "Test ID":     tc_id,
                "Description": (d["description"] or tc_id)[:60],
                "Capability":  d["capability"].title(),
            }
            for fw in fw_names:
                row[fw] = "✅" if d["support"].get(fw, "false") != "false" else "❌"
            rows.append(row)

        df = pd.DataFrame(rows)

        def _colour_cell(val):
            if val == "✅": return "background-color:#d4edda;color:#155724;font-weight:600;text-align:center"
            if val == "❌": return "background-color:#f8d7da;color:#721c24;font-weight:600;text-align:center"
            return ""

        fw_cols = [c for c in df.columns if c not in ("Test ID", "Description", "Capability")]
        st.dataframe(df.style.map(_colour_cell, subset=fw_cols), width="stretch", hide_index=True)

    with st.expander("🔍 Per-framework gap details", expanded=False):
        for fw in fw_names:
            scores    = fw_scores[fw]
            sup_ids   = scores["supported"]
            unsup_ids = scores["unsupported"]
            pct       = scores["pct"]
            badge     = "✅ Full" if pct == 100 else ("⚠️ Partial" if pct >= 50 else "❌ Low")

            with st.expander(
                f"{badge} **{fw}** — {pct:.0f}% ({len(sup_ids)}/{total} tests)", expanded=False
            ):
                col_sup, col_unsup = st.columns(2)
                with col_sup:
                    st.markdown(f"**✅ Supported ({len(sup_ids)})**")
                    for tc_id in sup_ids:
                        desc = matrix[tc_id]["description"] or tc_id
                        st.markdown(f"  - `{tc_id}` — {desc[:55]}")
                with col_unsup:
                    st.markdown(f"**❌ Not Supported ({len(unsup_ids)})**")
                    for tc_id in unsup_ids:
                        desc = matrix[tc_id]["description"] or tc_id
                        alts = [f for f in fw_names if f != fw
                                and matrix[tc_id]["support"].get(f, "false") != "false"][:3]
                        alt_txt = f" → try: *{', '.join(alts)}*" if alts else ""
                        st.markdown(f"  - `{tc_id}` — {desc[:45]}{alt_txt}")
