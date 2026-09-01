"""Test Generator page — stepped UX: Input → Progress → Result."""
from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger(__name__)

_TARGET_FRAMEWORKS = {
    "Playwright (Python)": "playwright_py",
    "Selenium (Python)":   "selenium_py",
    "Robot Framework":     "robot_framework",
}

_LANG_MAP = {
    "playwright_py":  "python",
    "selenium_py":    "python",
    "selenium_java":  "java",
    "cypress_js":     "javascript",
    "rest_assured":   "java",
    "robot_framework":"robot",
}

_FILE_ICONS = {
    "test":        "🧪",
    "page_object": "📄",
    "fixture":     "🔧",
    "utility":     "🛠️",
    "config":      "⚙️",
    "package":     "📦",
    "data":        "📊",
}


def render() -> None:
    st.markdown("""
    <div class="page-header">
        <div class="page-header-left">
            <div class="breadcrumb"><span>Home</span><span class="breadcrumb-sep">›</span><span>Test Generator</span></div>
            <div class="page-title">🤖 Test Code Generator</div>
            <div class="page-subtitle">Convert manual test cases into executable automated test code</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        fw_label = st.selectbox("Target Framework", list(_TARGET_FRAMEWORKS.keys()),
                                index=0, key="codegen_framework")
    with col2:
        input_mode = st.radio("Input method", ["Manual Entry", "Upload JSON/Excel"],
                              horizontal=True, key="codegen_input_mode")

    st.markdown("---")

    # ── Step 1: Input ─────────────────────────────────────────────────
    st.markdown("### Step 1 — Add Test Cases")
    if input_mode == "Manual Entry":
        _manual_entry()
    else:
        _upload_mode()

    _selector_map_section()
    _options_section()

    # ── Step 2: Generate ──────────────────────────────────────────────
    st.markdown("---")
    test_cases = st.session_state.get("codegen_test_cases", [])
    if not test_cases:
        st.info("Add at least one test case above to generate automated code.")

    if st.button("🚀 Generate Automated Tests", key="btn_generate_code",
                 type="primary", disabled=not test_cases):
        _generate(fw_label)

    # ── Step 3: Result ────────────────────────────────────────────────
    if st.session_state.get("codegen_result"):
        _show_result()


def _manual_entry() -> None:
    if "codegen_test_cases" not in st.session_state:
        st.session_state.codegen_test_cases = []

    with st.expander("➕ Add New Test Case",
                     expanded=not st.session_state.codegen_test_cases):
        tc_id    = st.text_input("Test Case ID",
                                 value=f"TC{len(st.session_state.codegen_test_cases)+1:03d}",
                                 key="tc_id")
        tc_title = st.text_input("Title",
                                 placeholder="e.g. Login with valid credentials",
                                 key="tc_title")
        tc_cat   = st.text_input("Category",
                                 placeholder="e.g. authentication, checkout",
                                 key="tc_category")
        tc_pri   = st.selectbox("Priority", ["low", "medium", "high", "critical"],
                                index=2, key="tc_priority")
        tc_pre   = st.text_input("Preconditions (comma-separated)",
                                 placeholder="e.g. User is logged in, Cart is empty",
                                 key="tc_preconditions")
        st.markdown("**Test Steps** (one per line, format: `action | test_data | expected_result`)")
        tc_steps = st.text_area(
            "Steps", height=200,
            placeholder=(
                "Navigate to the login page\n"
                "Enter 'admin' in the username field | admin | Username is entered\n"
                "Click the Login button\n"
                "Verify the dashboard is visible | | Dashboard page loads"
            ),
            key="tc_steps_raw",
        )

        if st.button("✅ Add Test Case", key="btn_add_tc", type="primary"):
            if not tc_title.strip():
                st.warning("Please provide a test case title.")
            elif not tc_steps.strip():
                st.warning("Please add at least one test step.")
            else:
                steps = []
                for i, line in enumerate(tc_steps.strip().split("\n"), 1):
                    parts = [p.strip() for p in line.split("|")]
                    steps.append({
                        "step_number": i,
                        "action": parts[0],
                        "test_data": parts[1] if len(parts) > 1 else "",
                        "expected_result": parts[2] if len(parts) > 2 else "",
                    })
                st.session_state.codegen_test_cases.append({
                    "id": tc_id.strip(),
                    "title": tc_title.strip(),
                    "category": tc_cat.strip(),
                    "priority": tc_pri,
                    "preconditions": [p.strip() for p in tc_pre.split(",") if p.strip()],
                    "steps": steps,
                    "expected_results": [],
                    "tags": [],
                })
                st.success(f"Added: {tc_title} ({len(steps)} steps)")
                st.rerun()

    tcs = st.session_state.codegen_test_cases
    if tcs:
        st.markdown(f"**{len(tcs)} test case(s) ready:**")
        for i, tc in enumerate(tcs):
            col_a, col_b = st.columns([5, 1])
            with col_a:
                st.markdown(f"**{tc['id']}** — {tc['title']} ({len(tc['steps'])} steps, {tc['priority']})")
            with col_b:
                if st.button("🗑️", key=f"rm_tc_{i}"):
                    st.session_state.codegen_test_cases.pop(i)
                    st.rerun()


def _upload_mode() -> None:
    import json as _json

    uploaded = st.file_uploader(
        "Upload JSON or Excel file with manual test cases",
        type=["json", "xlsx", "xls"],
        key="codegen_upload",
    )
    if uploaded:
        if uploaded.name.endswith(".json"):
            try:
                data = _json.loads(uploaded.read().decode("utf-8"))
                if "test_cases" in data:
                    st.session_state.codegen_test_cases = data["test_cases"]
                    st.session_state.codegen_selector_map = data.get("selector_map", {})
                    st.success(f"Loaded {len(data['test_cases'])} test cases from JSON")
                else:
                    st.error("JSON must contain a 'test_cases' array.")
            except Exception as e:
                st.error(f"Failed to parse JSON: {e}")
        elif uploaded.name.endswith((".xlsx", ".xls")):
            try:
                from src.tools.excel_parser import parse_test_cases_from_excel
                tcs = parse_test_cases_from_excel(uploaded)
                st.session_state.codegen_test_cases = tcs
                st.success(f"Loaded {len(tcs)} test cases from Excel")
            except Exception as e:
                st.error(f"Failed to parse Excel: {e}")

    with st.expander("📖 Expected JSON format"):
        st.code('''
{
  "test_cases": [
    {
      "id": "TC001",
      "title": "Login with valid credentials",
      "category": "authentication",
      "priority": "high",
      "preconditions": ["User exists"],
      "steps": [
        {"step_number": 1, "action": "Navigate to login page", "test_data": "", "expected_result": ""}
      ]
    }
  ],
  "selector_map": {
    "username field": "[data-testid='username']",
    "login button": "#login-btn"
  }
}
        ''', language="json")


def _selector_map_section() -> None:
    st.markdown("---")
    if "codegen_selector_map" not in st.session_state:
        st.session_state.codegen_selector_map = {}

    with st.expander("🎯 Selector Map (optional — improves accuracy)", expanded=False):
        st.caption("Map element descriptions to CSS selectors. One per line: `element name = selector`")
        selector_raw = st.text_area(
            "Selectors", height=120,
            placeholder="username field = [data-testid='username']\nlogin button = #login-btn",
            key="codegen_selectors_raw",
        )
        if st.button("💾 Apply Selectors", key="btn_apply_selectors"):
            parsed: dict[str, str] = {}
            for line in selector_raw.strip().split("\n"):
                if "=" in line:
                    k, _, v = line.partition("=")
                    k, v = k.strip().lower(), v.strip()
                    if k and v:
                        parsed[k] = v
            st.session_state.codegen_selector_map = parsed
            st.success(f"✓ {len(parsed)} selector(s) saved" if parsed else "⚠️ No valid selectors found")

    saved = st.session_state.codegen_selector_map
    if saved:
        st.caption(
            f"🎯 **{len(saved)} selector(s) active:** "
            + ", ".join(f"`{k}`" for k in list(saved.keys())[:5])
            + (f" +{len(saved)-5} more" if len(saved) > 5 else "")
        )


def _options_section() -> None:
    with st.expander("⚙️ Generation Options"):
        c1, c2 = st.columns(2)
        with c1:
            st.checkbox("Generate Page Objects",       value=True, key="cg_po")
            st.checkbox("Generate Fixtures",           value=True, key="cg_fix")
        with c2:
            st.checkbox("Parameterize Similar Tests",  value=True, key="cg_param")
            st.checkbox("Include Comments",            value=True, key="cg_comments")


def _generate(fw_label: str) -> None:
    from src.codegen import (
        CodeGenOrchestrator, CodeGenRequest, ManualTestCase,
        TestStep, TargetFramework, CodeGenOptions,
    )
    from services.app_services import get_groq_client

    fw_value  = _TARGET_FRAMEWORKS[fw_label]
    framework = TargetFramework(fw_value)
    test_cases = st.session_state.get("codegen_test_cases", [])

    manual_tests = []
    for tc in test_cases:
        steps = [
            TestStep(
                step_number=s.get("step_number", i + 1),
                action=s["action"],
                test_data=s.get("test_data", ""),
                expected_result=s.get("expected_result", ""),
            )
            for i, s in enumerate(tc.get("steps", []))
        ]
        manual_tests.append(ManualTestCase(
            id=tc.get("id", f"TC{len(manual_tests)+1:03d}"),
            title=tc.get("title", "Untitled"),
            description=tc.get("description", ""),
            preconditions=tc.get("preconditions", []),
            steps=steps,
            expected_results=tc.get("expected_results", []),
            priority=tc.get("priority", "medium"),
            category=tc.get("category", ""),
            tags=tc.get("tags", []),
        ))

    options = CodeGenOptions(
        generate_page_objects=st.session_state.get("cg_po", True),
        generate_fixtures=st.session_state.get("cg_fix", True),
        parameterize_similar=st.session_state.get("cg_param", True),
        include_comments=st.session_state.get("cg_comments", True),
    )

    with st.spinner("🤖 Generating automated test code…"):
        try:
            client = get_groq_client()
            selector_map = st.session_state.get("codegen_selector_map", {})
            request = CodeGenRequest(
                test_cases=manual_tests,
                target_framework=framework,
                options=options,
                selector_map=selector_map,
            )
            orchestrator = CodeGenOrchestrator(llm_client=client)
            st.session_state.codegen_result = orchestrator.generate(request)
        except Exception as e:
            st.error(f"Generation failed: {e}")
            import traceback; st.code(traceback.format_exc())


def _show_result() -> None:
    import io, zipfile
    result = st.session_state.codegen_result
    st.markdown("---")

    # ── Step 3 summary card ───────────────────────────────────────────
    stats = result.stats
    st.success(
        f"🎉 **Test suite generated** — "
        f"{stats.total_steps} steps · "
        f"{len(result.files)} files · "
        f"confidence {result.confidence_score:.0%}"
    )

    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("Total Steps",      stats.total_steps)
    s2.metric("Template",         stats.template_handled)
    s3.metric("LLM",              stats.llm_handled)
    s4.metric("Patterns Learned", stats.patterns_learned)
    s5.metric("Confidence",       f"{result.confidence_score:.0%}")

    st.markdown(f"**Framework:** {result.framework} · **Run:** `{result.run_command}`")
    st.markdown(f"**Install:** `{result.install_instructions}`")

    # Validation warnings
    vr = result.validation
    _undefined = getattr(vr, "undefined_symbols", [])
    if _undefined:
        st.warning(
            f"⚠️ **{len(_undefined)} undeclared symbol(s)** detected — verify the output:\n"
            + "\n".join(f"  - `{s}`" for s in _undefined)
        )
    if getattr(vr, "errors", None):
        st.error("Validation issues: " + "; ".join(vr.errors))

    # Quick download
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for gf in result.files:
            zf.writestr(gf.path, gf.content)
    zip_buffer.seek(0)
    st.download_button(
        "⬇️ Download All Files (ZIP)",
        data=zip_buffer,
        file_name="generated_tests.zip",
        mime="application/zip",
        key="btn_download_codegen",
    )

    # ── Project files — all collapsed by default ──────────────────────
    st.markdown("### 📁 Generated Project Files")
    for gf in result.files:
        icon  = _FILE_ICONS.get(gf.file_type.value, "📄")
        label = f"{icon} `{gf.path}` ({gf.file_type.value})"
        with st.expander(label, expanded=False):   # all collapsed
            st.code(gf.content, language=_LANG_MAP.get(result.framework, "text"))

    if result.selector_map:
        with st.expander("🎯 Selectors to verify"):
            st.caption("Auto-generated selectors — verify against your app's DOM:")
            for name, sel in result.selector_map.items():
                st.text(f"  {name}  →  {sel}")
