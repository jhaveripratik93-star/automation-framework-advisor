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


def _normalise_tc(raw: dict, index: int) -> dict:
    """Normalise alternate field names to the internal test case format.

    Handles:
      - test_case_title / title
      - pre_condition / preconditions
      - steps with missing step_number
    """
    steps_raw = raw.get("steps", [])
    steps = []
    for i, s in enumerate(steps_raw):
        steps.append({
            "step_number": s.get("step_number", i + 1),
            "action": s.get("action", ""),
            "test_data": s.get("test_data") or "",
            "expected_result": s.get("expected_result") or "",
        })
    title = raw.get("title") or raw.get("test_case_title") or f"TC{index + 1:03d}"
    preconditions = raw.get("preconditions") or raw.get("pre_condition") or []
    return {
        "id": raw.get("id") or f"TC{index + 1:03d}",
        "title": title,
        "description": raw.get("description", ""),
        "category": raw.get("category", ""),
        "priority": raw.get("priority", "medium"),
        "preconditions": preconditions if isinstance(preconditions, list) else [preconditions],
        "steps": steps,
        "expected_results": raw.get("expected_results", []),
        "tags": raw.get("tags", []),
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
    _repo_context_section()
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
                # Accept three formats:
                # 1. {"test_cases": [...]}  — standard format
                # 2. [...]                  — raw array of test case objects
                # 3. [{"test_case_title": ..., "steps": [...]}]  — alternate field names
                if isinstance(data, dict) and "test_cases" in data:
                    raw_tcs = data["test_cases"]
                    selector_map = data.get("selector_map", {})
                elif isinstance(data, list):
                    raw_tcs = data
                    selector_map = {}
                else:
                    st.error("JSON must be an array or an object with a 'test_cases' array.")
                    raw_tcs = None
                if raw_tcs is not None:
                    tcs = [
                        _normalise_tc(tc, i) for i, tc in enumerate(raw_tcs)
                        if tc.get("enabled", True)
                    ]
                    st.session_state.codegen_test_cases = tcs
                    st.session_state.codegen_selector_map = selector_map
                    skipped = len(raw_tcs) - len(tcs)
                    msg = f"Loaded {len(tcs)} test cases from JSON"
                    if skipped:
                        msg += f" ({skipped} disabled skipped)"
                    st.success(msg)
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


def _repo_context_section() -> None:
    st.markdown("---")
    if "codegen_project_context" not in st.session_state:
        st.session_state.codegen_project_context = None

    with st.expander("🔍 Project Repository Context (optional — improves code accuracy)", expanded=False):
        st.caption(
            "Provide your project's repo so the generator uses your real classes, "
            "fixtures, base URLs and env vars instead of invented placeholders."
        )
        repo_source = st.text_input(
            "Local folder path or GitHub URL",
            placeholder="e.g. C:/projects/my-app  or  https://github.com/owner/repo",
            key="codegen_repo_source",
        )
        col_scan, col_clear = st.columns([2, 1])
        with col_scan:
            if st.button("🔎 Scan Repository", key="btn_scan_repo"):
                if not repo_source.strip():
                    st.warning("Enter a folder path or GitHub URL first.")
                else:
                    with st.spinner("Scanning repository…"):
                        try:
                            from src.codegen.repo_scanner import scan
                            ctx = scan(repo_source.strip())
                            st.session_state.codegen_project_context = ctx
                            st.success(f"✅ Scanned **{ctx['project_name']}** — "
                                       f"{len(ctx['dependencies'])} deps, "
                                       f"{len(ctx['existing_fixtures'])} fixtures, "
                                       f"{len(ctx['existing_helpers'])} helpers, "
                                       f"{len(ctx['base_urls'])} URLs found")
                        except Exception as e:
                            st.error(f"Scan failed: {e}")
        with col_clear:
            if st.button("🗑️ Clear", key="btn_clear_repo"):
                st.session_state.codegen_project_context = None
                st.rerun()

    ctx = st.session_state.codegen_project_context
    if ctx:
        with st.expander("📋 Scanned Project Context", expanded=False):
            st.code(ctx["summary"], language="text")
            c1, c2, c3 = st.columns(3)
            c1.metric("Dependencies", len(ctx["dependencies"]))
            c2.metric("Fixtures", len(ctx["existing_fixtures"]))
            c3.metric("Helpers", len(ctx["existing_helpers"]))


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

    progress = st.progress(0, text="Starting generation…")
    status   = st.empty()
    try:
        client = get_groq_client()
        selector_map = st.session_state.get("codegen_selector_map", {})
        request = CodeGenRequest(
            test_cases=manual_tests,
            target_framework=framework,
            options=options,
            selector_map=selector_map,
        )
        total = len(manual_tests)

        # Patch orchestrator to emit progress after each test case
        orchestrator = CodeGenOrchestrator(llm_client=client)

        completed = [0]
        project_context = st.session_state.get("codegen_project_context")
        def _tracked(req):
            from src.codegen.renderer import _FRAMEWORK_CONFIG
            from src.codegen.agent_config import PIPELINE_SETTINGS
            import time, re
            from src.codegen.models import (
                FileType, GeneratedFile, GenerationSource, GenerationStats,
                GeneratedTestSuite, ValidationResult,
            )
            fw   = req.target_framework
            cfg  = _FRAMEWORK_CONFIG.get(fw.value, _FRAMEWORK_CONFIG["playwright_ts"])
            files, _per_tc_states = [], []
            stats = GenerationStats(total_steps=sum(len(tc.steps) for tc in req.test_cases))
            start = time.time()

            grouped = orchestrator._group_by_category(req.test_cases)
            max_per_file = PIPELINE_SETTINGS.get("max_tests_per_file", 3)
            grouped = orchestrator._split_groups(grouped, max_per_file)

            for group_key, group_tcs in grouped.items():
                # Convert ManualTestCase objects to dicts for batch generator
                tc_dicts = [
                    {
                        "id": tc.id, "title": tc.title, "description": tc.description,
                        "category": tc.category, "priority": tc.priority,
                        "preconditions": tc.preconditions,
                        "steps": [{"step_number": s.step_number, "action": s.action,
                                   "test_data": s.test_data, "expected_result": s.expected_result}
                                  for s in tc.steps],
                        "expected_results": tc.expected_results, "tags": tc.tags,
                    }
                    for tc in group_tcs
                ]

                n = len(group_tcs)
                status.markdown(f"⚙️ Generating **{group_key}** ({n} test case{'s' if n > 1 else ''})…")
                pct = int(len(files) / max(len(grouped), 1) * 90) + 5
                progress.progress(pct, text=f"Generating group: {group_key}")

                try:
                    codes = orchestrator._batch_generate(
                        test_cases=tc_dicts,
                        framework_value=fw.value,
                        selector_map=dict(req.selector_map),
                        project_context=project_context,
                    )
                    group_valid = all(bool(c.strip()) for c in codes)
                except Exception as exc:
                    logger.warning("Batch generate failed for group '%s': %s", group_key, exc)
                    codes = [
                        f"# Generation failed for: {tc.title}\n# Error: {exc}"
                        for tc in group_tcs
                    ]
                    group_valid = False

                for tc, code in zip(group_tcs, codes):
                    completed[0] += 1
                    _per_tc_states.append({
                        "assembled_code": code, "generated_code": code,
                        "validation_result": {"is_valid": group_valid, "undefined_symbols": []},
                        "suite_architecture": {},
                    })
                    stats.llm_handled += len(tc.steps)

                group_content = orchestrator._bundle_group(codes, fw.value)
                group_slug = re.sub(r"[^a-z0-9]+", "_", group_key.lower()).strip("_")[:50] or "tests"
                files.append(GeneratedFile(
                    path=f"tests/{group_slug}{cfg['extension']}",
                    content=group_content, file_type=FileType.TEST,
                    source=GenerationSource.LLM, confidence=0.9 if group_valid else 0.6,
                ))

            # Generate common/resources file deterministically (no LLM)
            common_file = orchestrator._generate_common_resource(
                _per_tc_states, fw.value, cfg, req.test_cases
            )
            if common_file:
                files.append(common_file)
            config_content = orchestrator._renderer._render_config(fw)
            if config_content:
                files.append(GeneratedFile(path=cfg["config_file"], content=config_content,
                    file_type=FileType.CONFIG, source=GenerationSource.TEMPLATE))
            pkg_content = orchestrator._renderer._render_package_file(fw)
            if pkg_content:
                files.append(GeneratedFile(path=cfg["package_file"], content=pkg_content,
                    file_type=FileType.PACKAGE, source=GenerationSource.TEMPLATE))
            stats.time_elapsed_ms = int((time.time() - start) * 1000)
            all_undefined = list(dict.fromkeys(
                sym for s in _per_tc_states
                for sym in s.get("validation_result", {}).get("undefined_symbols", [])
            ))
            return GeneratedTestSuite(
                framework=fw.value, language=cfg["language"], files=files,
                install_instructions=cfg["install_command"], run_command=cfg["run_command"],
                selector_map=req.selector_map,
                confidence_score=sum(f.confidence for f in files) / max(len(files), 1),
                validation=ValidationResult(is_valid=len(all_undefined) == 0,
                                            undefined_symbols=all_undefined),
                stats=stats,
            )

        orchestrator._generate_with_agents = _tracked
        progress.progress(5, text="Initialising pipeline…")
        st.session_state.codegen_result = _tracked(request)
        progress.progress(100, text="Done!")
        status.empty()
    except Exception as e:
        st.error(f"Generation failed: {e}")
        import traceback; st.code(traceback.format_exc())
    finally:
        progress.empty()


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
