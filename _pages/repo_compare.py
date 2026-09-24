"""repo_compare.py — Cross-repo test case parity page."""
from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger(__name__)

_FRAMEWORKS = ["Robot Framework", "Playwright", "Selenium", "K6"]


def render() -> None:
    st.markdown("""
    <div class="page-header">
        <div class="page-header-left">
            <div class="breadcrumb"><span>Home</span><span class="breadcrumb-sep">›</span><span>Repo Comparator</span></div>
            <div class="page-title">🔍 Cross-Repo Test Parity</div>
            <div class="page-subtitle">Compare test cases across two repos — assertion count parity report</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        fw_a = st.selectbox("Repo A — Framework", _FRAMEWORKS, index=0, key="rc_fw_a")
    with col2:
        fw_b = st.selectbox("Repo B — Framework", _FRAMEWORKS, index=1, key="rc_fw_b")

    st.markdown("---")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"#### 📁 Repo A — *{fw_a}*")
        files_a = st.file_uploader(
            "Upload test files or a .zip",
            type=["py", "js", "ts", "robot", "feature", "zip"],
            accept_multiple_files=True,
            key="rc_upload_a",
        )
        if files_a:
            st.caption(f"{len(files_a)} file(s) uploaded")

    with col_b:
        st.markdown(f"#### 📁 Repo B — *{fw_b}*")
        files_b = st.file_uploader(
            "Upload test files or a .zip",
            type=["py", "js", "ts", "robot", "feature", "zip"],
            accept_multiple_files=True,
            key="rc_upload_b",
        )
        if files_b:
            st.caption(f"{len(files_b)} file(s) uploaded")

    run = st.button("🔍 Compare Repos", type="primary",
                    disabled=not (files_a and files_b))

    if not files_a or not files_b:
        st.markdown("""
        <div class="empty-state">
            <span class="empty-state-icon">🔍</span>
            <div class="empty-state-title">Upload both repos to compare</div>
            <div class="empty-state-desc">
                Upload individual test files or a <b>.zip</b> for each repo.<br>
                The tool extracts every test case, counts its assertions, and
                shows a side-by-side report highlighting any mismatches.
            </div>
        </div>
        """, unsafe_allow_html=True)

    if run and files_a and files_b:
        with st.spinner("Extracting and comparing test cases…"):
            try:
                from src.tools.repo_comparator import compare_repos, extract_from_uploaded
                payload_a = extract_from_uploaded(files_a, fw_a)
                payload_b = extract_from_uploaded(files_b, fw_b)

                if not payload_a:
                    st.error("No supported test files found in Repo A.")
                    return
                if not payload_b:
                    st.error("No supported test files found in Repo B.")
                    return

                report = compare_repos(payload_a, payload_b, fw_a, fw_b)
                st.session_state["rc_report"]   = report
                st.session_state["rc_fw_a_lbl"] = fw_a
                st.session_state["rc_fw_b_lbl"] = fw_b

            except Exception as exc:
                st.error(f"Comparison failed: {exc}")
                logger.error("repo_compare error: %s", exc, exc_info=True)
                return

    report = st.session_state.get("rc_report")
    if report:
        _render_report(
            report,
            st.session_state.get("rc_fw_a_lbl", fw_a),
            st.session_state.get("rc_fw_b_lbl", fw_b),
        )


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _render_report(report: dict, fw_a: str, fw_b: str) -> None:
    s        = report["summary"]
    matched  = report["matched"]
    only_a   = report["only_in_a"]
    only_b   = report["only_in_b"]
    a_mis    = s["assertion_mismatch"]
    a_match  = s["assertion_match"]

    st.markdown("---")

    # ── Health banner ─────────────────────────────────────────────────
    if a_mis == 0 and not only_a and not only_b:
        colour, bg, icon = "#1e7e34", "#d4edda", "✅"
        msg = f"Full parity — all {s['matched_count']} test case(s) matched with identical assertion counts."
    elif a_mis == 0:
        colour, bg, icon = "#856404", "#fff3cd", "⚠️"
        msg = (f"Assertion counts match on all paired tests, but "
               f"{len(only_a) + len(only_b)} test(s) exist in only one repo.")
    else:
        colour, bg, icon = "#721c24", "#f8d7da", "❌"
        msg = f"{a_mis} test case(s) have mismatched assertion counts — review the Mismatch Summary below."

    st.markdown(
        f'<div style="background:{bg};border-left:5px solid {colour};'
        f'padding:0.75rem 1.2rem;border-radius:6px;margin-bottom:1.2rem;">'
        f'<b style="color:{colour};font-size:1.05rem;">{icon} Parity Status</b><br>'
        f'<span style="color:{colour};">{msg}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Metric tiles ──────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    _metric(c1, f"Repo A ({fw_a})", str(s["total_a"]), "test cases")
    _metric(c2, f"Repo B ({fw_b})", str(s["total_b"]), "test cases")
    _metric(c3, "✅ Assertion match",    str(a_match), "pairs")
    _metric(c4, "❌ Assertion mismatch", str(a_mis),   "pairs")

    st.markdown("---")

    # ── Main comparison table ─────────────────────────────────────────
    st.markdown("### 📊 Test Case Comparison")

    if matched or only_a or only_b:
        header = (
            f"| Test Case "
            f"| {fw_a} — Assertions "
            f"| {fw_b} — Assertions "
            f"| {fw_a} — Loop ⟳ "
            f"| {fw_b} — Loop ⟳ "
            f"| Status |\n"
            f"|---|:---:|:---:|:---:|:---:|:---:|"
        )
        lines = [header]

        # Matched pairs
        for m in matched:
            delta = m.assertions_b - m.assertions_a
            if m.status == "match":
                status_cell = "✅ Match"
                b_cell      = str(m.assertions_b)
            else:
                status_cell = f"❌ Mismatch ({delta:+d})"
                b_cell      = f"**{m.assertions_b}**"

            la = f"⟳ {m.loop_assertions_a}" if m.loop_assertions_a else "—"
            lb = f"⟳ {m.loop_assertions_b}" if m.loop_assertions_b else "—"

            # Use whichever name is more descriptive (longer)
            display = m.display_a if len(m.display_a) >= len(m.display_b) else m.display_b
            lines.append(
                f"| {display} | {m.assertions_a} | {b_cell} | {la} | {lb} | {status_cell} |"
            )

        # Only in A
        for u in only_a:
            la = f"\u27f3 {u.loop_assertions}" if u.loop_assertions else "\u2014"
            lines.append(
                f"| {u.display_name} | {u.assertions} | \u2014 | {la} | \u2014 | \u26a0\ufe0f Only in Repo A |"
            )

        # Only in B
        for u in only_b:
            lb = f"\u27f3 {u.loop_assertions}" if u.loop_assertions else "\u2014"
            lines.append(
                f"| {u.display_name} | \u2014 | {u.assertions} | \u2014 | {lb} | \u26a0\ufe0f Only in Repo B |"
            )

        st.markdown("\n".join(lines))
    else:
        st.info("No test cases found in either repo.")

    # ── Mismatch summary ──────────────────────────────────────────────
    mismatches = [m for m in matched if m.status == "mismatch"]
    if mismatches or only_a or only_b:
        st.markdown("---")
        st.markdown("### 🛠️ Mismatch Summary — What Needs to Be Fixed")

        for m in mismatches:
            delta   = m.assertions_b - m.assertions_a
            display = m.display_a if len(m.display_a) >= len(m.display_b) else m.display_b
            if delta > 0:
                problem  = f"Repo A ({fw_a}) is missing {delta} assertion(s) compared to Repo B ({fw_b})"
                fix_file = m.file_a
                fix_repo = f"Repo A ({fw_a})"
                ref_file = m.file_b
                ref_repo = f"Repo B ({fw_b})"
            else:
                problem  = f"Repo B ({fw_b}) is missing {abs(delta)} assertion(s) compared to Repo A ({fw_a})"
                fix_file = m.file_b
                fix_repo = f"Repo B ({fw_b})"
                ref_file = m.file_a
                ref_repo = f"Repo A ({fw_a})"
            st.markdown(
                f'<div style="background:#fff8f0;border-left:4px solid #e07b00;'
                f'padding:0.6rem 1rem;border-radius:4px;margin-bottom:0.6rem;">'
                f'<b>{display}</b><br>'
                f'Repo A ({fw_a}): <b>{m.assertions_a}</b> assertion(s) &nbsp;|&nbsp; '
                f'Repo B ({fw_b}): <b>{m.assertions_b}</b> assertion(s) &nbsp;|&nbsp; '
                f'<span style="color:#c0392b;">Delta: {delta:+d}</span><br>'
                f'<b style="color:#c0392b;">Issue:</b> {problem}<br>'
                f'<span style="color:#555;font-size:0.88rem;">'
                f'Review <code>{fix_file}</code> ({fix_repo}) and add the missing '
                f'assertion(s) found in <code>{ref_file}</code> ({ref_repo}).'
                f'</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        for u in only_a:
            st.markdown(
                f'<div style="background:#fff8f0;border-left:4px solid #e07b00;'
                f'padding:0.6rem 1rem;border-radius:4px;margin-bottom:0.6rem;">'
                f'<b>{u.display_name}</b> — not found in Repo B ({fw_b})<br>'
                f'Repo A has <b>{u.assertions}</b> assertion(s) with no counterpart.<br>'
                f'<span style="color:#555;font-size:0.88rem;">'
                f'➜ Add this test case to Repo B or confirm it is intentionally absent.'
                f'</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        for u in only_b:
            st.markdown(
                f'<div style="background:#fff8f0;border-left:4px solid #e07b00;'
                f'padding:0.6rem 1rem;border-radius:4px;margin-bottom:0.6rem;">'
                f'<b>{u.display_name}</b> — not found in Repo A ({fw_a})<br>'
                f'Repo B has <b>{u.assertions}</b> assertion(s) with no counterpart.<br>'
                f'<span style="color:#555;font-size:0.88rem;">'
                f'➜ Add this test case to Repo A or confirm it is intentionally absent.'
                f'</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── CSV download ──────────────────────────────────────────────────
    st.markdown("---")
    from src.tools.repo_comparator import to_csv
    st.download_button(
        "⬇ Download report (CSV)",
        data=to_csv(report),
        file_name="repo_parity_report.csv",
        mime="text/csv",
        key="rc_dl_csv",
    )


def _metric(col, label: str, value: str, sub: str) -> None:
    with col:
        st.markdown(
            f'<div style="background:#f8f9fa;border-radius:8px;padding:0.6rem 0.8rem;text-align:center;">'
            f'<div style="font-size:0.72rem;color:#666;margin-bottom:2px;">{label}</div>'
            f'<div style="font-size:1.6rem;font-weight:700;color:#2d7d6f;">{value}</div>'
            f'<div style="font-size:0.7rem;color:#999;">{sub}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
