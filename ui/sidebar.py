"""Sidebar — contextual per page, lazy service access."""
from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger(__name__)

_BRAND_HTML = """
<div style="padding:1rem 0 0.75rem;border-bottom:1px solid #e8f0ee;margin-bottom:0.75rem;">
    <div style="display:flex;align-items:center;gap:0.6rem;">
        <div style="width:32px;height:32px;background:linear-gradient(135deg,#2d7d6f,#1f5c52);
             border-radius:8px;display:flex;align-items:center;justify-content:center;
             font-size:1rem;color:#fff;">🧭</div>
        <div>
            <div style="font-weight:800;font-size:0.85rem;color:#1e2d2b;">Framework Advisor</div>
            <div style="font-size:0.65rem;color:#8aaba5;">AI Migration Planner</div>
        </div>
    </div>
</div>
"""


def render() -> None:
    page = st.session_state.page

    with st.sidebar:
        st.markdown(_BRAND_HTML, unsafe_allow_html=True)

        if page == "advisor":
            _advisor_sidebar()
        elif page == "home":
            _home_sidebar()
        elif page == "studio":
            _studio_sidebar()
        elif page == "coverage":
            _coverage_sidebar()
        elif page == "codegen":
            _codegen_sidebar()


# ── Per-page sidebar sections ─────────────────────────────────────────

def _advisor_sidebar() -> None:
    from src.scoring.weights import WeightProfile, PRESETS, CRITERIA_IDS

    _CRITERIA_LABELS = {
        "C1_language_compatibility": "Language Compatibility",
        "C2_api_validation":         "API Validation",
        "C3_performance_load":       "Performance Testing",
        "C4_cicd_integration":       "CI/CD Integration",
        "C5_maintainability":        "Maintainability",
        "C6_cloud_readiness":        "Cloud Readiness",
        "C7_license_cost":           "License & Cost",
    }

    # ── Advisor reset ─────────────────────────────────────────────────
    if st.button("🔄 Clear & Reset Advisor", key="sb_clear_advisor", use_container_width=True):
        st.session_state.messages = []
        st.session_state.uploaded_docs_context = ""
        st.session_state.case_study_context = ""
        st.session_state.case_study_urls = []
        st.session_state.weight_profile = WeightProfile.default()
        st.session_state["_weight_preset_value"] = "balanced"
        for _cid in CRITERIA_IDS:
            st.session_state[f"w_{_cid}"] = float(round(PRESETS["balanced"].get(_cid, 0.0), 2))
        st.rerun()

    st.markdown("---")

    # ── Scoring weights ───────────────────────────────────────────────
    st.markdown('<div class="sidebar-section">⚖️ Scoring Weights</div>', unsafe_allow_html=True)

    with st.expander("⚖️ Adjust Weights", expanded=False):
        preset_names = list(PRESETS.keys())
        preset_options = preset_names + ["custom"]

        # ── Handle pending auto-balance BEFORE any widget renders ─────
        if "_auto_balance_pending" in st.session_state:
            normalised = st.session_state.pop("_auto_balance_pending")
            for cid in CRITERIA_IDS:
                st.session_state[f"w_{cid}"] = normalised[cid]
            st.session_state.weight_profile = WeightProfile(weights=normalised, profile_name="custom")
            st.session_state["_weight_preset_value"] = "custom"
            st.session_state["_w_feedback"] = "✓ Auto-balanced & applied"

        # Shadow key drives the selectbox index — never written after widget instantiation
        if "_weight_preset_value" not in st.session_state:
            current = st.session_state.weight_profile.profile_name.replace("_adjusted", "")
            st.session_state["_weight_preset_value"] = current if current in preset_options else "balanced"

        current_index = preset_options.index(
            st.session_state["_weight_preset_value"]
            if st.session_state["_weight_preset_value"] in preset_options else "balanced"
        )

        chosen = st.selectbox(
            "Preset", preset_options,
            index=current_index,
            label_visibility="collapsed",
            key="weight_preset_box",
        )
        # Sync shadow key and apply preset when user picks one
        if chosen != st.session_state["_weight_preset_value"]:
            st.session_state["_weight_preset_value"] = chosen
            if chosen != "custom":
                st.session_state.weight_profile = WeightProfile.from_preset(chosen)
                for cid in CRITERIA_IDS:
                    st.session_state[f"w_{cid}"] = float(round(PRESETS[chosen].get(cid, 0.0), 2))

        # Initialise slider keys on first render
        for cid in CRITERIA_IDS:
            if f"w_{cid}" not in st.session_state:
                st.session_state[f"w_{cid}"] = float(
                    round(st.session_state.weight_profile.weights.get(cid, 0.0), 2)
                )

        for cid in CRITERIA_IDS:
            st.number_input(
                _CRITERIA_LABELS[cid],
                min_value=0.0, max_value=1.0, step=0.05, format="%.2f",
                key=f"w_{cid}",
            )

        # ── Live running total ────────────────────────────────────────
        raw   = {cid: st.session_state[f"w_{cid}"] for cid in CRITERIA_IDS}
        total = round(sum(raw.values()), 4)
        diff  = round(total - 1.0, 4)

        if abs(diff) < 0.001:
            st.success(f"Total: {total:.2f} ✓  — ready to apply")
        elif diff > 0:
            st.error(f"Total: {total:.2f}  (+{diff:.2f} over). Reduce or Auto-balance.")
        else:
            st.warning(f"Total: {total:.2f}  ({diff:.2f} under). Increase or Auto-balance.")

        col_apply, col_auto = st.columns(2)
        with col_apply:
            apply_clicked = st.button(
                "Apply", key="apply_weights",
                use_container_width=True, type="primary",
                disabled=(abs(diff) > 0.001),
            )
        with col_auto:
            auto_clicked = st.button(
                "Auto-balance", key="auto_balance_weights",
                use_container_width=True,
            )

        if apply_clicked:
            st.session_state.weight_profile = WeightProfile(weights=dict(raw), profile_name="custom")
            st.session_state["_weight_preset_value"] = "custom"
            st.session_state["_w_feedback"] = "✓ Weights applied"

        if auto_clicked and total > 0:
            normalised = {k: round(v / total, 4) for k, v in raw.items()}
            remainder  = round(1.0 - sum(normalised.values()), 4)
            normalised[CRITERIA_IDS[0]] = round(normalised[CRITERIA_IDS[0]] + remainder, 4)
            st.session_state["_auto_balance_pending"] = normalised
            st.rerun()

        if st.session_state.get("_w_feedback"):
            st.success(st.session_state["_w_feedback"])
            # Clear after one render so it doesn't persist forever
            st.session_state["_w_feedback"] = ""

    st.markdown("---")
    st.markdown('<div class="sidebar-section">Context Files</div>', unsafe_allow_html=True)

    with st.expander("📁 Upload Test Files", expanded=False):
        files = st.file_uploader(
            "Test files", type=["py", "js", "ts", "java", "yaml", "yml", "json"],
            accept_multiple_files=True, key="test_files",
        )
        if files and st.button("🔍 Analyse", key="analyse_files"):
            parts = []
            for f in files:
                try:
                    text = f.read().decode("utf-8"); f.seek(0)
                    parts.append(f"### {f.name}\n" + "\n".join(text.split("\n")[:300]))
                except Exception:
                    pass
            st.session_state.uploaded_docs_context = "\n\n".join(parts)
            st.success(f"✓ {len(files)} file(s) loaded")

    with st.expander("📚 Upload Case Study", expanded=False):
        cs_tab_file, cs_tab_url = st.tabs(["📄 File", "🔗 URL"])

        with cs_tab_file:
            cs_file = st.file_uploader("Case study (txt/md)", type=["txt", "md"], key="case_study")
            if cs_file and st.button("📖 Parse", key="parse_cs"):
                try:
                    text = cs_file.read().decode("utf-8")
                    st.session_state.case_study_context = text
                    _ingest_case_study(text, source=cs_file.name)
                    st.success(f"✓ {len(text)} chars parsed & added to KB")
                except Exception as exc:
                    st.error(str(exc))

        with cs_tab_url:
            url_input = st.text_input(
                "Paste URL", placeholder="https://example.com/case-study", key="cs_url_input"
            )
            if st.button("➕ Fetch & Add", key="cs_fetch_url"):
                url = url_input.strip()
                if not url:
                    st.warning("Enter a URL first.")
                elif url in st.session_state.case_study_urls:
                    st.warning("URL already added.")
                else:
                    with st.spinner("Fetching…"):
                        content, err = _fetch_url(url)
                    if err:
                        st.error(err)
                    else:
                        chunk = f"\n\n--- Source: {url} ---\n{content}"
                        st.session_state.case_study_context = (
                            st.session_state.get("case_study_context", "") + chunk
                        )
                        st.session_state.case_study_urls.append(url)
                        _ingest_case_study(content, source=url)
                        st.success(f"✓ Fetched {len(content)} chars")
                        st.rerun()

            if st.session_state.case_study_urls:
                st.caption(f"{len(st.session_state.case_study_urls)} URL(s) loaded:")
                for _u in st.session_state.case_study_urls:
                    st.markdown(f"· `{_u}`")
                if st.button("🗑 Clear all URLs", key="cs_clear_urls"):
                    st.session_state.case_study_urls = []
                    st.session_state.case_study_context = ""
                    st.rerun()


def _home_sidebar() -> None:
    from services.app_services import get_kb
    from src.knowledge_base.schema import FRAMEWORK_CATEGORIES, classify_framework_data

    kb = get_kb()
    all_fws = kb.list_all()

    st.markdown("---")
    st.markdown('<div class="sidebar-section">📚 Available Frameworks</div>', unsafe_allow_html=True)
    st.caption(f"{len(all_fws)} framework(s) in knowledge base")

    assigned: set = set()
    cat_frameworks: dict = {cat_id: [] for cat_id in FRAMEWORK_CATEGORIES}
    for _fw in all_fws:
        cats = classify_framework_data(_fw)
        if cats:
            cat_frameworks[cats[0]].append(_fw.framework_name)
            assigned.add(_fw.framework_name)

    for _cat_id, _cat_def in FRAMEWORK_CATEGORIES.items():
        names = sorted(cat_frameworks.get(_cat_id, []))
        if not names:
            continue
        with st.expander(f"{_cat_def['icon']} {_cat_def['label']} ({len(names)})", expanded=False):
            for _name in names:
                st.markdown(f"· {_name}")

    st.markdown("---")
    st.markdown('<div class="sidebar-section">🔍 Framework Discovery</div>', unsafe_allow_html=True)
    with st.expander("Discover New Frameworks", expanded=False):
        st.caption(
            "Scan the internet for new automation frameworks and "
            "automatically add them to the knowledge base."
        )

        # Last-updated info instead of auto-scan
        from pathlib import Path as _Path
        import datetime
        fw_dir = _Path("data/frameworks")
        if fw_dir.exists():
            mtimes = [f.stat().st_mtime for f in fw_dir.glob("*.yaml")]
            if mtimes:
                last_updated = datetime.datetime.fromtimestamp(max(mtimes))
                days_ago = (datetime.datetime.now() - last_updated).days
                st.caption(f"Framework database: {len(list(fw_dir.glob('*.yaml')))} frameworks · last updated {days_ago}d ago")

        if st.button("🌐 Check for Updates", key="scan_frameworks", use_container_width=True):
            _run_scan()

        if st.session_state.get("scan_results"):
            results = st.session_state.scan_results
            st.success(f"Found {len(results)} new framework(s)")
            from src.knowledge_base.schema import FRAMEWORK_CATEGORIES as _FC
            scanner = _get_scanner_lazy()
            categorized = scanner.get_categorized_results(results)
            if categorized:
                for cat_id, fws in categorized.items():
                    cat_def = _FC[cat_id]
                    with st.expander(f"{cat_def['icon']} {cat_def['label']} ({len(fws)})", expanded=False):
                        for fw in fws:
                            col_name, col_btn = st.columns([3, 1])
                            with col_name:
                                st.markdown(f"**{fw.name}** ⭐{fw.stars}")
                                st.caption(fw.description[:70])
                            with col_btn:
                                if st.button("➕", key=f"add_{cat_id}_{fw.name}", help=f"Add {fw.name}"):
                                    _add_framework(fw.name)
            else:
                for fw in results:
                    col_name, col_btn = st.columns([3, 1])
                    with col_name:
                        st.markdown(f"**{fw.name}** ⭐{fw.stars}")
                        st.caption(fw.description[:80])
                    with col_btn:
                        if st.button("➕", key=f"add_{fw.name}", help=f"Add {fw.name}"):
                            _add_framework(fw.name)
            if st.button("➕ Add All", key="add_all_frameworks", use_container_width=True):
                _add_all_frameworks()

        if st.session_state.get("scan_message"):
            st.info(st.session_state.scan_message)


def _studio_sidebar() -> None:
    st.markdown('<div class="sidebar-section">Conversion</div>', unsafe_allow_html=True)
    st.caption("Select frameworks in the main panel. Upload files or paste code to convert.")

    _has_single = bool(st.session_state.get("studio_source_code"))
    _has_multi  = bool(st.session_state.get("multi_convert_result"))

    if _has_single:
        st.markdown("**Single file:** converted ✓")
        st.caption(f"{len(st.session_state.last_converted_code.splitlines())} lines")

    if _has_multi:
        result = st.session_state.multi_convert_result
        gaps = result.get("gaps", [])
        st.markdown('<div class="sidebar-section">Last Result</div>', unsafe_allow_html=True)
        st.markdown(f"**Files converted:** {len(result.get('converted', {}))}")
        st.markdown(f"**Helper scripts:** {len(result.get('helpers', {}))}")
        if gaps:
            st.warning(f"⚠️ {len(gaps)} capability gap(s)")
        else:
            st.success("✓ Full coverage")

    if _has_single or _has_multi:
        if st.button("🗑 Clear Studio", key="sb_clear_multi", use_container_width=True):
            for _k in ("last_converted_code", "studio_source_code", "studio_gap_block", "studio_cicd_block"):
                st.session_state[_k] = ""
            st.session_state.multi_convert_result = None
            st.session_state.studio_widget_key_v += 1
            st.session_state.pop("multi_convert_from", None)
            st.session_state.pop("multi_convert_to", None)
            st.rerun()


def _coverage_sidebar() -> None:
    st.markdown('<div class="sidebar-section">Analysis</div>', unsafe_allow_html=True)
    tcs = st.session_state.excel_test_cases
    if tcs:
        caps = list({tc.get("required_capability", "") for tc in tcs if tc.get("required_capability")})
        st.markdown(f"**Test cases loaded:** {len(tcs)}")
        st.markdown(f"**Capabilities:** {len(caps)}")
        st.markdown("**Detected:**")
        for c in caps[:8]:
            st.markdown(f"  · {c}")
        if len(caps) > 8:
            st.caption(f"…and {len(caps)-8} more")
        st.markdown("---")
        if st.button("🗑 Clear all", key="sb_clear_cov"):
            st.session_state["_cov_clear_pending"] = True
            st.rerun()
    else:
        st.caption("No test cases loaded yet. Upload an Excel file or enter manually.")

    st.markdown('<div class="sidebar-section">Quick Tips</div>', unsafe_allow_html=True)
    st.caption("• Excel columns: Test ID, Description, Capability, Steps, Expected Result")
    st.caption("• Capability field drives analysis — 'smoke', 'e2e', 'api' all work")
    st.caption("• Select specific frameworks to compare, or leave empty for all")


def _codegen_sidebar() -> None:
    st.markdown('<div class="sidebar-section">🤖 Test Generator</div>', unsafe_allow_html=True)
    tcs = st.session_state.get("codegen_test_cases", [])
    if tcs:
        cats = list({tc.get("category", "") for tc in tcs if tc.get("category")})
        st.markdown(f"**Test cases:** {len(tcs)}")
        if cats:
            st.markdown(f"**Categories:** {', '.join(cats[:5])}")
        priorities = {tc.get("priority", "") for tc in tcs}
        st.markdown(f"**Priorities:** {', '.join(sorted(priorities))}")
        st.markdown("---")
        if st.button("🗑 Clear test cases", key="sb_clear_codegen"):
            st.session_state.codegen_test_cases = []
            st.session_state.pop("codegen_result", None)
            st.rerun()
    else:
        st.caption("No test cases added yet.")
    st.markdown("---")
    st.markdown('<div class="sidebar-section">Quick Tips</div>', unsafe_allow_html=True)
    st.caption("• Add steps as: `action | test_data | expected`")
    st.caption("• Add a Selector Map to improve accuracy")
    st.caption("• Enable Page Objects for maintainable code")


# ── Helpers ───────────────────────────────────────────────────────────

def _get_scanner_lazy():
    from services.app_services import get_scanner
    return get_scanner()


def _run_scan() -> None:
    scanner = _get_scanner_lazy()
    with st.spinner("Scanning GitHub, PyPI, npm for new frameworks…"):
        try:
            new_fws = scanner.scan(force=True)
            st.session_state.scan_results = new_fws
            st.session_state.scan_message = (
                "" if new_fws else "✓ No new frameworks found — KB is up to date."
            )
        except Exception as exc:
            st.session_state.scan_message = f"⚠️ Scan error: {exc}"
    st.rerun()


def _add_framework(name: str) -> None:
    scanner = _get_scanner_lazy()
    with st.spinner(f"Researching {name}…"):
        try:
            profile = scanner.research_framework(name)
            if profile:
                path = scanner.add_framework(profile)
                if path:
                    st.session_state.scan_results = [
                        fw for fw in st.session_state.scan_results if fw.name != name
                    ]
                    st.session_state.scan_message = f"✓ Added {name} to knowledge base!"
                    from services.app_services import get_kb
                    get_kb.clear()
                else:
                    st.session_state.scan_message = f"⚠️ Could not write {name} profile."
            else:
                st.session_state.scan_message = f"⚠️ Could not research {name}."
        except Exception as exc:
            st.session_state.scan_message = f"⚠️ Error adding {name}: {exc}"
    st.rerun()


def _add_all_frameworks() -> None:
    scanner = _get_scanner_lazy()
    results = st.session_state.get("scan_results", [])
    if not results:
        return
    added = []
    with st.spinner(f"Adding {len(results)} framework(s)…"):
        for fw in results:
            try:
                profile = scanner.research_framework(fw.name)
                if profile:
                    path = scanner.add_framework(profile)
                    if path:
                        added.append(fw.name)
            except Exception as exc:
                logger.warning("Failed to add %s: %s", fw.name, exc)
    st.session_state.scan_results = [fw for fw in results if fw.name not in added]
    st.session_state.scan_message = f"✓ Added {len(added)} framework(s): {', '.join(added)}"
    if added:
        from services.app_services import get_kb
        get_kb.clear()
    st.rerun()


def _fetch_url(url: str) -> tuple[str, str]:
    try:
        import httpx
        from bs4 import BeautifulSoup
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        resp = httpx.get(url, timeout=15, follow_redirects=True, headers=headers)
        if resp.status_code == 403:
            return "", (
                f"⛔ 403 Forbidden — '{url}' blocks automated access. "
                "Try copying the page text manually and uploading as a .txt file."
            )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        if not text.strip():
            return "", "Page fetched but no readable text found (may be JavaScript-rendered)."
        return text[:8000], ""
    except Exception as exc:
        return "", str(exc)


def _ingest_case_study(text: str, source: str = "case_study") -> None:
    try:
        import re, hashlib
        from pathlib import Path
        slug = re.sub(r"[^a-z0-9]+", "_", source.lower())[:40]
        uid  = hashlib.md5(text[:200].encode()).hexdigest()[:6]
        dest = Path("data/frameworks/case_studies")
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{slug}_{uid}.txt").write_text(text, encoding="utf-8")
    except Exception as exc:
        logger.warning("_ingest_case_study failed: %s", exc)
