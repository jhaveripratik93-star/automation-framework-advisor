"""Automation Framework Migration & Coverage Advisor — Streamlit UI."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent / "config" / ".env")

from src.logging_config import configure_logging
configure_logging()

logger = logging.getLogger(__name__)

from src.knowledge_base import KnowledgeBase
from src.graph import load_or_seed_graph
from src.graph.graphrag_engine import GraphRAGEngine
from src.llm.groq_client import GroqClient
from src.llm.advisor_llm import AdvisorLLM
from src.scoring.weights import WeightProfile, PRESETS, CRITERIA_IDS
from src.discovery.framework_scanner import FrameworkScanner

# ── Page config ───────────────────────────────────────────────────────
st.set_page_config(
    page_title="Framework Advisor",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"Get help": None, "Report a bug": None, "About": None},
)

st.markdown(
    f"<style>{Path('static/styles.css').read_text(encoding='utf-8')}</style>",
    unsafe_allow_html=True,
)


# ── Cached resources ──────────────────────────────────────────────────
@st.cache_resource
def load_kb() -> KnowledgeBase:
    kb = KnowledgeBase(data_dir="data/frameworks")
    kb.load()
    return kb


@st.cache_resource
def init_stack(_kb: KnowledgeBase):
    client = GroqClient()
    if not client.is_available:
        logger.warning("Groq: GROQ_API_KEY not set")
    else:
        logger.info("Groq: model=%s ready", client.model)

    graph = load_or_seed_graph(_kb)
    graphrag = GraphRAGEngine(graph=graph, knowledge_base=_kb)
    advisor = AdvisorLLM(
        llm_client=client,
        graphrag_engine=graphrag,
        knowledge_base=_kb,
        knowledge_graph=graph,
    )

    try:
        from src.agents.orchestrator import AgentOrchestrator
        from src.agents.langgraph_pipeline import build_pipeline
        from src.tools.executor import ToolExecutor
        _orch = AgentOrchestrator(llm_client=client, graphrag_engine=graphrag,
                                  knowledge_base=_kb, knowledge_graph=graph)
        _executor = ToolExecutor(knowledge_graph=graph, graphrag_engine=graphrag,
                                 knowledge_base=_kb)
        _pipeline = build_pipeline(
            decision_agent=_orch._decision_agent,
            tool_selection_agent=_orch._tool_selection_agent,
            tool_executor=_executor,
            synthesis_agent=_orch._synthesis_agent,
            evaluation_agent=_orch._evaluation_agent,
            reflection_agent=_orch._reflection_agent,
            format_agent=_orch._format_agent,
        )
        _pipeline.get_graph().draw_mermaid_png(output_file_path="static/pipeline_flow.png")
    except Exception as exc:
        logger.debug("Pipeline diagram export skipped: %s", exc)

    return client, graph, graphrag, advisor


kb = load_kb()
_client, _graph, _graphrag, advisor = init_stack(kb)


# ── Framework Scanner (auto-discovery on load) ────────────────────────
@st.cache_resource
def init_scanner(_kb: KnowledgeBase) -> FrameworkScanner:
    """Initialize the framework scanner with KB and LLM client."""
    client = GroqClient()
    scanner = FrameworkScanner(
        kb=_kb,
        llm_client=client if client.is_available else None,
        data_dir="data/frameworks",
    )
    return scanner


_scanner = init_scanner(kb)


# Auto-scan on first load (non-blocking — just discover, don't auto-add)
if "auto_scan_done" not in st.session_state:
    st.session_state.auto_scan_done = False
    st.session_state.scan_results = []
    st.session_state.scan_message = ""

if not st.session_state.auto_scan_done:
    try:
        new_fws = _scanner.scan(force=False)
        st.session_state.scan_results = new_fws
        if new_fws:
            st.session_state.scan_message = (
                f"🔔 {len(new_fws)} new framework(s) detected! "
                "Open sidebar → Framework Discovery to review and add."
            )
        st.session_state.auto_scan_done = True
    except Exception as exc:
        logger.debug("Auto-scan skipped: %s", exc)
        st.session_state.auto_scan_done = True


def _run_framework_scan():
    """Run an on-demand framework discovery scan."""
    with st.spinner("Scanning GitHub, PyPI, npm for new frameworks..."):
        try:
            new_fws = _scanner.scan(force=True)
            st.session_state.scan_results = new_fws
            if not new_fws:
                st.session_state.scan_message = "✓ No new frameworks found — KB is up to date."
            else:
                st.session_state.scan_message = ""
        except Exception as exc:
            st.session_state.scan_message = f"⚠️ Scan error: {exc}"
    st.rerun()


def _add_single_framework(name: str):
    """Research and add a single framework to the KB."""
    with st.spinner(f"Researching {name}..."):
        try:
            profile = _scanner.research_framework(name)
            if profile:
                path = _scanner.add_framework(profile)
                if path:
                    # Remove from scan results
                    st.session_state.scan_results = [
                        fw for fw in st.session_state.scan_results
                        if fw.name != name
                    ]
                    st.session_state.scan_message = f"✓ Added {name} to knowledge base!"
                    # Clear cached KB to reload
                    load_kb.clear()
                else:
                    st.session_state.scan_message = f"⚠️ Could not write {name} profile."
            else:
                st.session_state.scan_message = f"⚠️ Could not research {name}."
        except Exception as exc:
            st.session_state.scan_message = f"⚠️ Error adding {name}: {exc}"
    st.rerun()


def _add_all_discovered_frameworks():
    """Add all discovered frameworks to the KB."""
    results = st.session_state.get("scan_results", [])
    if not results:
        return
    added = []
    with st.spinner(f"Adding {len(results)} framework(s)..."):
        for fw in results:
            try:
                profile = _scanner.research_framework(fw.name)
                if profile:
                    path = _scanner.add_framework(profile)
                    if path:
                        added.append(fw.name)
            except Exception as exc:
                logger.warning("Failed to add %s: %s", fw.name, exc)
    st.session_state.scan_results = [
        fw for fw in results if fw.name not in added
    ]
    st.session_state.scan_message = f"✓ Added {len(added)} framework(s): {', '.join(added)}"
    if added:
        load_kb.clear()
    st.rerun()

# ── Constants ─────────────────────────────────────────────────────────
_CRITERIA_LABELS = {
    "C1_language_compatibility": "Language Compatibility",
    "C2_api_validation":         "API Validation",
    "C3_performance_load":       "Performance Testing",
    "C4_cicd_integration":       "CI/CD Integration",
    "C5_maintainability":        "Maintainability",
    "C6_cloud_readiness":        "Cloud Readiness",
    "C7_license_cost":           "License & Cost",
}

_KNOWN_FRAMEWORKS = kb.list_names()

_PAGES = ["home", "advisor", "studio", "coverage"]

# ── Session state defaults ────────────────────────────────────────────
_DEFAULTS: dict = {
    "page": "home",
    "messages": [],
    "uploaded_docs_context": "",
    "case_study_context": "",
    "weight_profile": WeightProfile.default(),
    # Code Studio
    "playground_code": "",
    "last_converted_code": "",
    "multi_convert_result": None,
    # Coverage
    "excel_test_cases": [],
    "excel_summary": "",
    "coverage_result": "",
    "coverage_best_fw": "",
    "coverage_best_pct": "",
    "cicd_yaml": "",
    # Loading state
    "llm_busy": False,
    "llm_step": 0,
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ── Loading indicator ─────────────────────────────────────────────────
def _render_loading(steps: list[str], current: int, title: str = "AI is thinking…") -> None:
    """Render a step-by-step loading card. current = 0-based index of active step."""
    dots = "".join(
        f'<span class="pulse-dot" style="animation-delay:{i*0.2}s;margin-right:3px"></span>'
        for i in range(3)
    )
    step_html = ""
    for i, s in enumerate(steps):
        if i < current:
            cls = "done"
            icon = "✓"
        elif i == current:
            cls = "active"
            icon = "›"
        else:
            cls = ""
            icon = "·"
        step_html += (
            f'<div class="step-item {cls}">'
            f'<div class="step-dot"></div>{icon} {s}</div>'
        )
    pct = int((current / max(len(steps) - 1, 1)) * 100)
    st.markdown(f"""
    <div class="llm-loading">
        <div class="llm-loading-header">
            <div>{dots}</div>
            <div>
                <div class="llm-loading-title">{title}</div>
                <div class="llm-loading-sub">This may take 10–30 seconds</div>
            </div>
        </div>
        <div class="step-list">{step_html}</div>
        <div class="progress-bar-wrap">
            <div class="progress-bar-fill" style="width:{pct}%"></div>
        </div>
    </div>
    """, unsafe_allow_html=True)


# ── Top navigation bar ────────────────────────────────────────────────
def _render_topnav() -> None:
    page = st.session_state.page
    nav_items = [
        ("home",     "🏠", "Home"),
        ("advisor",  "🧭", "Advisor"),
        ("studio",   "🐍", "Code Studio"),
        ("codegen",  "🤖", "Test Generator"),
        ("coverage", "📊", "Coverage"),
    ]
    # Build nav HTML
    links_html = ""
    for key, icon, label in nav_items:
        active_cls = "active" if page == key else ""
        links_html += (
            f'<div class="nav-link {active_cls}" '
            f'onclick="void(0)" id="nav_{key}">'
            f'<span class="nav-icon">{icon}</span>{label}</div>'
        )

    st.markdown(f"""
    <div class="top-nav">
        <div class="top-nav-brand">
            <div class="top-nav-logo">🧭</div>
            <div>
                <div class="top-nav-title">Framework Advisor</div>
                <div class="top-nav-sub">AI-powered migration &amp; coverage</div>
            </div>
        </div>
        <div class="top-nav-links">{links_html}</div>
        <div class="top-nav-badge">GROQ LLM</div>
    </div>
    """, unsafe_allow_html=True)

    # Streamlit buttons for actual navigation (hidden visually via columns trick)
    cols = st.columns(len(nav_items))
    for col, (key, icon, label) in zip(cols, nav_items):
        with col:
            if st.button(f"{icon} {label}", key=f"nav_btn_{key}",
                         use_container_width=True,
                         type="primary" if page == key else "secondary"):
                st.session_state.page = key
                st.rerun()


# ── Sidebar — contextual per page ────────────────────────────────────
def _render_sidebar() -> None:
    page = st.session_state.page
    with st.sidebar:
        # Brand
        st.markdown("""
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
        """, unsafe_allow_html=True)

        if page in ("home", "advisor"):
            # ── Scoring Weights ───────────────────────────────────────────
            st.markdown('<div class="sidebar-section">⚖️ Scoring Weights</div>',
                        unsafe_allow_html=True)

            with st.expander("⚖️ Adjust Weights", expanded=True):
                preset_names = list(PRESETS.keys())

                def _on_preset_change():
                    chosen = st.session_state["weight_preset"]
                    if chosen == "custom":
                        return
                    st.session_state.weight_profile = WeightProfile.from_preset(chosen)
                    for cid in CRITERIA_IDS:
                        st.session_state[f"w_{cid}"] = float(
                            round(PRESETS[chosen].get(cid, 0.0), 2)
                        )

                current_preset = st.session_state.weight_profile.profile_name.replace("_adjusted", "")
                if current_preset not in preset_names:
                    current_preset = "balanced"
                if "weight_preset" not in st.session_state:
                    st.session_state["weight_preset"] = current_preset

                preset_options = preset_names + ["custom"]
                if st.session_state.get("weight_preset", current_preset) not in preset_options:
                    st.session_state["weight_preset"] = current_preset

                st.selectbox(
                    "Preset", preset_options,
                    key="weight_preset",
                    on_change=_on_preset_change,
                    label_visibility="collapsed",
                )

                st.caption("Set weights (0.0–1.0). Click Apply to normalise & save.")
                # Initialise widget keys on first load (no conflict with session state)
                for cid in CRITERIA_IDS:
                    if f"w_{cid}" not in st.session_state:
                        st.session_state[f"w_{cid}"] = float(
                            round(st.session_state.weight_profile.weights.get(cid, 0.0), 2)
                        )

                raw_weights: dict[str, float] = {}
                for cid in CRITERIA_IDS:
                    raw_weights[cid] = st.number_input(
                        _CRITERIA_LABELS[cid],
                        min_value=0.0,
                        max_value=1.0,
                        step=0.05,
                        format="%.2f",
                        key=f"w_{cid}",
                    )

                if st.button("Apply Weights", key="apply_weights", use_container_width=True):
                    raw_weights = {cid: st.session_state[f"w_{cid}"] for cid in CRITERIA_IDS}
                    total = sum(raw_weights.values())
                    normalised = (
                        {k: v / total for k, v in raw_weights.items()} if total > 0
                        else {k: 1 / len(raw_weights) for k in raw_weights}
                    )
                    st.session_state.weight_profile = WeightProfile(
                        weights=normalised, profile_name="custom"
                    )
                    st.session_state["weight_preset"] = "custom"
                    st.success("Weights applied ✓")

            st.markdown("---")

            st.markdown('<div class="sidebar-section">Context Files</div>',
                        unsafe_allow_html=True)
            with st.expander("📁 Upload Test Files", expanded=False):
                files = st.file_uploader(
                    "Test files", type=["py", "js", "ts", "java", "yaml", "yml", "json"],
                    accept_multiple_files=True, key="test_files",
                )
                if files and st.button("🔍 Analyse", key="analyse_files"):
                    content_parts = []
                    for f in files:
                        try:
                            text = f.read().decode("utf-8")
                            f.seek(0)
                            content_parts.append(f"### {f.name}\n" + "\n".join(text.split("\n")[:300]))
                        except Exception:
                            pass
                    st.session_state.uploaded_docs_context = "\n\n".join(content_parts)
                    st.success(f"✓ {len(files)} file(s) loaded")

            with st.expander("📚 Upload Case Study", expanded=False):
                cs_file = st.file_uploader(
                    "Case study (txt/md)", type=["txt", "md"], key="case_study",
                )
                if cs_file and st.button("📖 Parse", key="parse_cs"):
                    try:
                        text = cs_file.read().decode("utf-8")
                        st.session_state.case_study_context = text
                        st.success(f"✓ {len(text)} chars parsed")
                    except Exception as exc:
                        st.error(str(exc))

            # ── Framework Discovery ───────────────────────────────────
            st.markdown("---")
            st.markdown('<div class="sidebar-section">🔍 Framework Discovery</div>',
                        unsafe_allow_html=True)
            with st.expander("Discover New Frameworks", expanded=False):
                st.caption(
                    "Scan the internet for new automation frameworks and "
                    "automatically add them to the knowledge base."
                )
                if st.button("🌐 Scan Now", key="scan_frameworks", use_container_width=True):
                    _run_framework_scan()

                if st.session_state.get("scan_results"):
                    results = st.session_state.scan_results
                    st.success(f"Found {len(results)} new framework(s)")
                    for fw in results:
                        col_name, col_btn = st.columns([3, 1])
                        with col_name:
                            st.markdown(f"**{fw.name}** ⭐{fw.stars}")
                            st.caption(fw.description[:80])
                        with col_btn:
                            if st.button("➕", key=f"add_{fw.name}", help=f"Add {fw.name}"):
                                _add_single_framework(fw.name)

                    if st.button("➕ Add All", key="add_all_frameworks", use_container_width=True):
                        _add_all_discovered_frameworks()

                if st.session_state.get("scan_message"):
                    st.info(st.session_state.scan_message)

        elif page == "studio":
            # ── Code Studio context ───────────────────────────────────
            st.markdown('<div class="sidebar-section">Conversion</div>',
                        unsafe_allow_html=True)
            st.caption("Select frameworks in the main panel. Upload files or paste code to convert.")

            if st.session_state.multi_convert_result:
                result = st.session_state.multi_convert_result
                gaps = result.get("gaps", [])
                st.markdown('<div class="sidebar-section">Last Result</div>',
                            unsafe_allow_html=True)
                st.markdown(f"**Files converted:** {len(result.get('converted', {}))}")
                st.markdown(f"**Helper scripts:** {len(result.get('helpers', {}))}")
                if gaps:
                    st.warning(f"⚠️ {len(gaps)} capability gap(s)")
                else:
                    st.success("✓ Full coverage")
                if st.button("🗑 Clear results", key="sb_clear_multi"):
                    st.session_state.pop("multi_convert_result", None)
                    st.rerun()

        elif page == "coverage":
            # ── Coverage context ──────────────────────────────────────
            st.markdown('<div class="sidebar-section">Analysis</div>',
                        unsafe_allow_html=True)
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
                    for k in ("excel_test_cases", "excel_summary", "coverage_result", "cicd_yaml"):
                        st.session_state[k] = [] if k == "excel_test_cases" else ""
                    st.rerun()
            else:
                st.caption("No test cases loaded yet. Upload an Excel file or enter manually.")

            st.markdown('<div class="sidebar-section">Quick Tips</div>',
                        unsafe_allow_html=True)
            st.caption("• Excel columns: Test ID, Description, Capability, Steps, Expected Result")
            st.caption("• Capability field drives analysis — 'smoke', 'e2e', 'api' all work")
            st.caption("• Leave framework filter empty to compare all 17 frameworks")


# ── Home page ─────────────────────────────────────────────────────────
def _render_home() -> None:
    fw_count = len(kb.list_all())
    st.markdown(f"""
    <div class="home-hero">
        <div class="home-hero-title">Automation Framework Advisor</div>
        <div class="home-hero-sub">
            AI-powered tool to evaluate, migrate, and verify test automation frameworks.
            Compare {fw_count} frameworks, convert test suites, and analyse coverage gaps — all in one place.
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Stats bar
    st.markdown(f"""
    <div class="home-stats">
        <div style="text-align:center">
            <div class="home-stat-value">{fw_count}</div>
            <div class="home-stat-label">Frameworks</div>
        </div>
        <div style="text-align:center">
            <div class="home-stat-value">AI</div>
            <div class="home-stat-label">Powered by Groq LLM</div>
        </div>
        <div style="text-align:center">
            <div class="home-stat-value">3</div>
            <div class="home-stat-label">Core Features</div>
        </div>
        <div style="text-align:center">
            <div class="home-stat-value">100%</div>
            <div class="home-stat-label">Coverage Parity</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Feature cards
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("""
        <div class="feature-card">
            <span class="feature-card-icon">🧭</span>
            <div class="feature-card-title">Framework Advisor</div>
            <div class="feature-card-desc">
                Chat with an AI agent to compare frameworks, get migration plans,
                and receive scored recommendations tailored to your project.
            </div>
            <div class="feature-card-tags">
                <span class="feature-tag">LLM Chat</span>
                <span class="feature-tag">Scoring Matrix</span>
                <span class="feature-tag">Migration Plan</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Open Advisor →", key="home_go_advisor", use_container_width=True):
            st.session_state.page = "advisor"
            st.rerun()

    with c2:
        st.markdown("""
        <div class="feature-card">
            <span class="feature-card-icon">🐍</span>
            <div class="feature-card-title">Code Studio</div>
            <div class="feature-card-desc">
                Convert test files between frameworks automatically.
                Supports single-file paste or full repo upload with
                gap-bridging helper scripts and CI/CD config.
            </div>
            <div class="feature-card-tags">
                <span class="feature-tag">Auto Convert</span>
                <span class="feature-tag">Multi-file</span>
                <span class="feature-tag">ZIP Download</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Open Code Studio →", key="home_go_studio", use_container_width=True):
            st.session_state.page = "studio"
            st.rerun()

    with c3:
        st.markdown("""
        <div class="feature-card">
            <span class="feature-card-icon">📊</span>
            <div class="feature-card-title">Coverage Analyser</div>
            <div class="feature-card-desc">
                Upload your test cases via Excel or JSON. Get a full
                coverage matrix across all frameworks, best-fit recommendations,
                and a ready-to-download CI/CD pipeline.
            </div>
            <div class="feature-card-tags">
                <span class="feature-tag">Excel Upload</span>
                <span class="feature-tag">Coverage Matrix</span>
                <span class="feature-tag">CI/CD Export</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Open Coverage →", key="home_go_coverage", use_container_width=True):
            st.session_state.page = "coverage"
            st.rerun()

    # Quick start guide
    st.markdown("---")
    st.markdown("#### 🚀 Quick Start")
    qa, qb, qc = st.columns(3)
    with qa:
        st.markdown("""
        **New to the tool?**
        1. Start with **Advisor** — ask which framework fits your project
        2. Get a scored comparison and migration roadmap
        3. Export the CI/CD pipeline
        """)
    with qb:
        st.markdown("""
        **Migrating a test suite?**
        1. Go to **Code Studio**
        2. Upload your existing test files
        3. Download the converted project as a ZIP
        """)
    with qc:
        st.markdown("""
        **Verifying coverage?**
        1. Go to **Coverage Analyser**
        2. Upload your test case Excel sheet
        3. See which frameworks cover 100% of your needs
        """)


# ── Input guardrail ─────────────────────────────────────────────────
_TOPIC_KEYWORDS = {
    # frameworks & tools
    "playwright", "cypress", "selenium", "webdriverio", "puppeteer", "testcafe",
    "robot", "appium", "karate", "k6", "locust", "rest assured", "restassured",
    "pytest", "junit", "mocha", "jest", "jasmine", "cucumber", "behave",
    "terraform", "ansible", "pulumi", "chef", "cloudformation",
    # testing concepts
    "test", "testing", "automation", "framework", "migrate", "migration",
    "coverage", "e2e", "end-to-end", "ui", "api", "mobile", "performance",
    "load", "smoke", "regression", "integration", "functional", "unit",
    "assertion", "selector", "locator", "fixture", "mock", "stub", "spy",
    "browser", "headless", "parallel", "flaky", "retry", "report",
    # ci/cd & infra
    "ci", "cd", "cicd", "pipeline", "github actions", "gitlab", "azure devops",
    "jenkins", "docker", "container", "deploy", "build", "artifact",
    # scoring / comparison
    "compare", "comparison", "recommend", "recommendation",
    "framework score", "scoring matrix", "weighted score",
    "best framework", "choose framework", "which framework",
    "pros and cons", "tradeoff", "trade-off",
    "convert", "boilerplate", "template", "setup", "install", "config",
    # general dev
    "python", "javascript", "typescript", "java", "node", "npm", "pip",
    "package", "dependency", "library", "plugin", "extension",
}

_OFF_TOPIC_REPLY = (
    "I'm specialised in **test automation frameworks, migrations, and coverage analysis**. "
    "I can't help with that topic, but I'm happy to assist with:\n"
    "- Comparing or recommending automation frameworks\n"
    "- Planning a migration (e.g. Selenium → Playwright)\n"
    "- Analysing test coverage gaps\n"
    "- CI/CD pipeline setup for test suites\n\n"
    "What would you like to know about test automation?"
)


def _is_off_topic(text: str) -> bool:
    """Return True if the message contains no automation/testing-related keywords."""
    lowered = text.lower()
    return not any(kw in lowered for kw in _TOPIC_KEYWORDS)


# ── Tab: Advisor ──────────────────────────────────────────────────────
def _render_advisor_tab() -> None:
    st.markdown("""
    <div class="page-header">
        <div class="page-header-left">
            <div class="breadcrumb"><span>Home</span><span class="breadcrumb-sep">›</span><span>Advisor</span></div>
            <div class="page-title">🧭 Framework Advisor</div>
            <div class="page-subtitle">Ask anything about frameworks, migrations, or scoring</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if not st.session_state.messages:
        st.session_state.messages.append({"role": "assistant", "content": (
            "👋 Hi! I'm your **Framework Migration Advisor**.\n\n"
            "Try asking me:\n"
            "- *'Compare Robot Framework vs Playwright'*\n"
            "- *'Best Python API testing framework'*\n"
            "- *'Migrate from Selenium to Playwright'*\n\n"
            "Upload test files in the sidebar for context-aware recommendations."
        )})

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="🧭" if msg["role"] == "assistant" else "👤"):
            st.markdown(msg["content"])

    if user_input := st.chat_input("Ask about frameworks, migrations, or coverage..."):
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user", avatar="👤"):
            st.markdown(user_input)

        if _is_off_topic(user_input):
            with st.chat_message("assistant", avatar="🧭"):
                st.markdown(_OFF_TOPIC_REPLY)
            st.session_state.messages.append({"role": "assistant", "content": _OFF_TOPIC_REPLY})
            st.stop()

        wp = st.session_state.weight_profile
        weight_context = "Active scoring weights: " + ", ".join(
            f"{_CRITERIA_LABELS.get(k, k)}={v:.0%}"
            for k, v in wp.weights.items() if k in CRITERIA_IDS
        )

        _ADVISOR_STEPS = [
            "Parsing your question",
            "Searching knowledge graph",
            "Running agent pipeline",
            "Synthesising response",
        ]

        with st.chat_message("assistant", avatar="🧭"):
            loading_slot = st.empty()
            for step_i in range(len(_ADVISOR_STEPS)):
                with loading_slot.container():
                    _render_loading(_ADVISOR_STEPS, step_i, "Advisor is thinking…")
                import time; time.sleep(0.4)

            try:
                response = advisor.run_pipeline(
                    user_input,
                    st.session_state.uploaded_docs_context,
                    st.session_state.case_study_context,
                    weight_context=weight_context,
                    weight_profile=st.session_state.weight_profile,
                )
            except Exception as exc:
                logger.warning("Pipeline failed (%s) — direct respond", exc)
                response = advisor.respond(
                    user_input,
                    st.session_state.uploaded_docs_context,
                    st.session_state.case_study_context,
                )

            loading_slot.empty()
            st.markdown(response)

        st.session_state.messages.append({"role": "assistant", "content": response})


# ── Tab: Code Studio ──────────────────────────────────────────────────
def _render_code_studio_tab() -> None:
    import re as _re
    import io
    import zipfile

    st.markdown("""
    <div class="page-header">
        <div class="page-header-left">
            <div class="breadcrumb"><span>Home</span><span class="breadcrumb-sep">›</span><span>Code Studio</span></div>
            <div class="page-title">🐍 Code Studio</div>
            <div class="page-subtitle">Convert test suites between frameworks automatically</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        from_fw = st.selectbox("From framework", _KNOWN_FRAMEWORKS, index=3, key="convert_from")
    with col2:
        to_options = [f for f in _KNOWN_FRAMEWORKS if f != from_fw]
        to_fw = st.selectbox("To framework", to_options, index=0, key="convert_to")

    # Breadcrumb trail showing current conversion
    if from_fw and to_fw:
        st.markdown(
            f'<div class="breadcrumb" style="margin:0.25rem 0 0.75rem;">'
            f'<span>{from_fw}</span><span class="breadcrumb-sep">→</span>'
            f'<span style="color:#2d7d6f;font-weight:700;">{to_fw}</span></div>',
            unsafe_allow_html=True,
        )

    mode = st.radio(
        "Conversion mode",
        ["Single file (paste)", "Multi-file (upload repo)"],
        horizontal=True,
        key="converter_mode",
    )
    st.markdown("---")

    if mode == "Single file (paste)":
        # Empty state when no code pasted
        if not st.session_state.get("last_converted_code"):
            st.markdown("""
            """, unsafe_allow_html=True)

        source_code = st.text_area(
            "Paste source test code here", height=200,
            placeholder=f"Paste your {from_fw} test code here…",
            key="convert_source",
        )
        do_convert = st.button("🔄 Convert", key="btn_convert", type="primary")

        if do_convert:
            if not source_code.strip():
                st.warning("Paste some source test code first.")
            else:
                _CONVERT_STEPS = [
                    "Parsing source code",
                    "Detecting capability gaps",
                    "Generating converted code",
                    "Building CI/CD snippet",
                ]
                loading_slot = st.empty()
                try:
                    from src.tools.executor import ToolExecutor
                    executor = ToolExecutor(knowledge_base=kb, knowledge_graph=_graph,
                                           graphrag_engine=_graphrag)
                    executor._llm = _client

                    for step_i in range(len(_CONVERT_STEPS) - 1):
                        with loading_slot.container():
                            _render_loading(_CONVERT_STEPS, step_i,
                                            f"Converting {from_fw} → {to_fw}…")
                        import time; time.sleep(0.3)

                    full_result = executor.execute("convert_test_cases", {
                        "source_code": source_code,
                        "from_framework": from_fw,
                        "to_framework": to_fw,
                        "language": "python",
                    })

                    with loading_slot.container():
                        _render_loading(_CONVERT_STEPS, len(_CONVERT_STEPS) - 1,
                                        "Finalising…")
                    import time; time.sleep(0.2)
                    loading_slot.empty()

                    # ── Split output into 3 sections ──────────────────
                    cicd_marker   = "# === CI/CD INTEGRATION ==="
                    gap_marker    = "### ⚠️ Capabilities Requiring"

                    cicd_split  = full_result.split(cicd_marker, 1)
                    pre_cicd    = cicd_split[0]
                    cicd_block  = (cicd_marker + cicd_split[1]) if len(cicd_split) > 1 else ""

                    gap_split   = pre_cicd.split(gap_marker, 1)
                    code_part   = gap_split[0]
                    gap_block   = (gap_marker + gap_split[1]) if len(gap_split) > 1 else ""

                    # Also strip gap table from cicd_block if LLM put it there
                    if gap_marker in cicd_block and not gap_block:
                        cicd_gap = cicd_block.split(gap_marker, 1)
                        cicd_block = cicd_gap[0]
                        gap_block  = gap_marker + cicd_gap[1]

                    code_blocks = _re.findall(r"```(?:python)?\n(.*?)```", code_part, _re.DOTALL)
                    runnable = code_blocks[0].strip() if code_blocks else code_part.strip()

                    st.session_state.last_converted_code = runnable
                    st.success("✓ Converted successfully")

                    with st.expander("🐍 Converted Test Code", expanded=True):
                        st.code(runnable, language="python")
                        st.download_button(
                            label="⬇ Download converted file",
                            data=runnable,
                            file_name="test_converted.py",
                            mime="text/x-python",
                            key="btn_dl_single",
                        )

                    if gap_block.strip():
                        with st.expander("⚠️ Capability Gaps & Helper Scripts", expanded=True):
                            st.markdown(gap_block.strip())

                    if cicd_block.strip():
                        with st.expander("⚙️ CI/CD Integration", expanded=False):
                            st.markdown(cicd_block.strip())
                except Exception as exc:
                    loading_slot.empty()
                    st.error(f"Conversion failed: {exc}")
                    logger.error("Conversion error: %s", exc)
    else:
        # Multi-file mode
        uploaded_files = st.file_uploader(
            "Upload test files",
            type=["py", "js", "ts", "java", "robot", "feature", "yml", "yaml"],
            accept_multiple_files=True,
            key="multi_convert_files",
        )

        if not uploaded_files and not st.session_state.get("multi_convert_result"):
            st.markdown("""
            <div class="empty-state">
                <span class="empty-state-icon">📂</span>
                <div class="empty-state-title">Upload your test repository</div>
                <div class="empty-state-desc">
                    Upload multiple test files. Each is converted with shared cross-file context.
                    Helper scripts are auto-generated for capability gaps.
                </div>
                <span class="empty-state-hint">⬆ Use the file uploader above</span>
            </div>
            """, unsafe_allow_html=True)

        if uploaded_files:
            st.caption(f"{len(uploaded_files)} file(s) selected: " +
                       ", ".join(f.name for f in uploaded_files))

        do_multi = st.button(
            f"🔄 Convert {len(uploaded_files) if uploaded_files else 0} file(s)",
            key="btn_multi_convert",
            disabled=not uploaded_files,
            type="primary",
        )

        if do_multi and uploaded_files:
            files_payload = []
            for uf in uploaded_files:
                try:
                    files_payload.append({
                        "filename": uf.name,
                        "content": uf.read().decode("utf-8", errors="replace"),
                    })
                except Exception as exc:
                    st.warning(f"Could not read {uf.name}: {exc}")

            if files_payload:
                _MULTI_STEPS = [
                    "Reading source files",
                    "Analysing capability gaps",
                    f"Converting {len(files_payload)} file(s)",
                    "Generating helper scripts",
                    "Building conftest & requirements",
                ]
                loading_slot = st.empty()
                try:
                    from src.tools.executor import ToolExecutor
                    executor = ToolExecutor(knowledge_base=kb, knowledge_graph=_graph,
                                           graphrag_engine=_graphrag)
                    executor._llm = _client

                    for step_i in range(len(_MULTI_STEPS) - 1):
                        with loading_slot.container():
                            _render_loading(_MULTI_STEPS, step_i,
                                            f"Converting {from_fw} → {to_fw}…")
                        import time; time.sleep(0.35)

                    result = executor.convert_multi_file(
                        files=files_payload,
                        from_framework=from_fw,
                        to_framework=to_fw,
                    )

                    with loading_slot.container():
                        _render_loading(_MULTI_STEPS, len(_MULTI_STEPS) - 1, "Done!")
                    import time; time.sleep(0.2)
                    loading_slot.empty()

                    st.session_state["multi_convert_result"] = result
                    st.session_state["multi_convert_from"] = from_fw
                    st.session_state["multi_convert_to"] = to_fw
                    st.rerun()
                except Exception as exc:
                    loading_slot.empty()
                    st.error(f"Multi-file conversion failed: {exc}")
                    logger.error("Multi-file conversion error: %s", exc)

        result = st.session_state.get("multi_convert_result")
        if result:
            to_label = st.session_state.get("multi_convert_to", "")
            st.markdown("---")
            st.markdown(result["summary"])
            st.markdown("### 📂 Converted Project Files")
            all_files: dict[str, str] = {}

            with st.expander("📄 `conftest.py`", expanded=False):
                st.code(result["conftest"], language="python")
                st.download_button("⬇ conftest.py", result["conftest"],
                                   file_name="conftest.py", mime="text/x-python",
                                   key="dl_conftest")
            all_files["conftest.py"] = result["conftest"]

            with st.expander("📄 `requirements.txt`", expanded=False):
                st.code(result["requirements"], language="text")
                st.download_button("⬇ requirements.txt", result["requirements"],
                                   file_name="requirements.txt", mime="text/plain",
                                   key="dl_requirements")
            all_files["requirements.txt"] = result["requirements"]

            if result["converted"]:
                st.markdown("#### 🧪 Converted Test Files")
                for path, code in sorted(result["converted"].items()):
                    with st.expander(f"📄 `{path}`", expanded=False):
                        st.code(code, language="python")
                        st.download_button(f"⬇ {Path(path).name}", code,
                                           file_name=Path(path).name, mime="text/x-python",
                                           key=f"dl_{path.replace('/', '_')}")
                    all_files[path] = code

            if result["helpers"]:
                st.markdown("#### 🔧 Gap-Bridging Helper Scripts")
                st.caption(f"Auto-generated to cover capabilities {to_label} cannot handle natively.")
                for path, code in sorted(result["helpers"].items()):
                    with st.expander(f"📄 `{path}`", expanded=False):
                        st.code(code, language="python")
                        st.download_button(f"⬇ {Path(path).name}", code,
                                           file_name=Path(path).name, mime="text/x-python",
                                           key=f"dl_{path.replace('/', '_')}")
                    all_files[path] = code

            st.markdown("---")
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for path, code in all_files.items():
                    zf.writestr(path, code)
            zip_buf.seek(0)
            folder_name = f"converted_{to_label.lower().replace(' ', '_')}"
            st.download_button(
                label=f"⬇ Download all as {folder_name}.zip",
                data=zip_buf.getvalue(),
                file_name=f"{folder_name}.zip",
                mime="application/zip",
                key="btn_dl_zip",
            )


# ── Tab: Test Code Generator ──────────────────────────────────────────
def _render_codegen_tab() -> None:
    """Render the manual → automated test code generation UI."""
    import json as _json

    st.markdown("""
    <div class="page-header">
        <div class="page-header-left">
            <div class="breadcrumb"><span>Home</span><span class="breadcrumb-sep">›</span><span>Test Generator</span></div>
            <div class="page-title">🤖 Test Code Generator</div>
            <div class="page-subtitle">Convert manual test cases into executable automated test code</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Framework selection ───────────────────────────────────────────
    _TARGET_FRAMEWORKS = {
        "Playwright (TypeScript)": "playwright_ts",
        "Playwright (Python)": "playwright_py",
        "Selenium (Python)": "selenium_py",
        "Selenium (Java)": "selenium_java",
        "Cypress (JavaScript)": "cypress_js",
        "Robot Framework": "robot_framework",
    }

    col1, col2 = st.columns(2)
    with col1:
        fw_label = st.selectbox(
            "Target Framework",
            list(_TARGET_FRAMEWORKS.keys()),
            index=0,
            key="codegen_framework",
        )
    with col2:
        input_mode = st.radio(
            "Input method",
            ["Manual Entry", "Upload JSON/Excel"],
            horizontal=True,
            key="codegen_input_mode",
        )

    st.markdown("---")

    # ── Manual Entry Mode ─────────────────────────────────────────────
    if input_mode == "Manual Entry":
        st.markdown("### 📝 Add Test Cases")

        # Initialize session state for test cases
        if "codegen_test_cases" not in st.session_state:
            st.session_state.codegen_test_cases = []

        # Form to add a new test case
        with st.expander("➕ Add New Test Case", expanded=not st.session_state.codegen_test_cases):
            tc_id = st.text_input("Test Case ID", value=f"TC{len(st.session_state.codegen_test_cases)+1:03d}", key="tc_id")
            tc_title = st.text_input("Title", placeholder="e.g. Login with valid credentials", key="tc_title")
            tc_category = st.text_input("Category", placeholder="e.g. authentication, checkout", key="tc_category")
            tc_priority = st.selectbox("Priority", ["low", "medium", "high", "critical"], index=2, key="tc_priority")
            tc_preconditions = st.text_input("Preconditions (comma-separated)", placeholder="e.g. User is logged in, Cart is empty", key="tc_preconditions")

            st.markdown("**Test Steps** (one per line, format: `action | test_data | expected_result`)")
            tc_steps_raw = st.text_area(
                "Steps",
                height=200,
                placeholder="Navigate to the login page\n"
                            "Enter 'admin' in the username field | admin | Username is entered\n"
                            "Enter 'password123' in the password field | password123 | Password is masked\n"
                            "Click the Login button\n"
                            "Verify the dashboard is visible | | Dashboard page loads",
                key="tc_steps_raw",
            )

            if st.button("✅ Add Test Case", key="btn_add_tc", type="primary"):
                if not tc_title.strip():
                    st.warning("Please provide a test case title.")
                elif not tc_steps_raw.strip():
                    st.warning("Please add at least one test step.")
                else:
                    # Parse steps
                    steps = []
                    for i, line in enumerate(tc_steps_raw.strip().split("\n"), 1):
                        parts = [p.strip() for p in line.split("|")]
                        steps.append({
                            "step_number": i,
                            "action": parts[0],
                            "test_data": parts[1] if len(parts) > 1 else "",
                            "expected_result": parts[2] if len(parts) > 2 else "",
                        })

                    tc = {
                        "id": tc_id.strip(),
                        "title": tc_title.strip(),
                        "category": tc_category.strip(),
                        "priority": tc_priority,
                        "preconditions": [p.strip() for p in tc_preconditions.split(",") if p.strip()],
                        "steps": steps,
                        "expected_results": [],
                        "tags": [],
                    }
                    st.session_state.codegen_test_cases.append(tc)
                    st.success(f"Added: {tc_title} ({len(steps)} steps)")
                    st.rerun()

        # Show existing test cases
        if st.session_state.codegen_test_cases:
            st.markdown(f"### 📋 Test Cases ({len(st.session_state.codegen_test_cases)})")
            for i, tc in enumerate(st.session_state.codegen_test_cases):
                col_a, col_b = st.columns([5, 1])
                with col_a:
                    st.markdown(f"**{tc['id']}** — {tc['title']} ({len(tc['steps'])} steps, {tc['priority']})")
                with col_b:
                    if st.button("🗑️", key=f"rm_tc_{i}"):
                        st.session_state.codegen_test_cases.pop(i)
                        st.rerun()

    # ── Upload JSON/Excel Mode ────────────────────────────────────────
    else:
        st.markdown("### 📁 Upload Test Cases")
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
                        st.error("JSON must contain a 'test_cases' array. See sample format.")
                except Exception as e:
                    st.error(f"Failed to parse JSON: {e}")
            elif uploaded.name.endswith((".xlsx", ".xls")):
                try:
                    from src.tools.excel_parser import parse_test_cases_from_excel
                    test_cases = parse_test_cases_from_excel(uploaded)
                    st.session_state.codegen_test_cases = test_cases
                    st.success(f"Loaded {len(test_cases)} test cases from Excel")
                except Exception as e:
                    st.error(f"Failed to parse Excel: {e}")

        # Show sample JSON format
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
        {"step_number": 1, "action": "Navigate to login page", "test_data": "", "expected_result": ""},
        {"step_number": 2, "action": "Enter 'admin' in username field", "test_data": "admin", "expected_result": ""}
      ]
    }
  ],
  "selector_map": {
    "username field": "[data-testid='username']",
    "login button": "#login-btn"
  }
}
            ''', language="json")

    # ── Selector Map (optional) ───────────────────────────────────────
    st.markdown("---")
    with st.expander("🎯 Selector Map (optional — improves accuracy)"):
        st.caption("Map element descriptions to actual CSS selectors. One per line: `element name = selector`")
        selector_raw = st.text_area(
            "Selectors",
            height=120,
            placeholder="username field = [data-testid='username']\nlogin button = #login-btn\nerror message = .alert-error",
            value="\n".join(
                f"{k} = {v}" for k, v in st.session_state.get("codegen_selector_map", {}).items()
            ),
            key="codegen_selectors_raw",
        )
        # Parse selector map
        selector_map = {}
        for line in selector_raw.strip().split("\n"):
            if "=" in line:
                parts = line.split("=", 1)
                selector_map[parts[0].strip().lower()] = parts[1].strip()
        st.session_state.codegen_selector_map = selector_map

    # ── Options ───────────────────────────────────────────────────────
    with st.expander("⚙️ Generation Options"):
        opt_col1, opt_col2 = st.columns(2)
        with opt_col1:
            gen_page_objects = st.checkbox("Generate Page Objects", value=True, key="cg_po")
            gen_fixtures = st.checkbox("Generate Fixtures", value=True, key="cg_fix")
        with opt_col2:
            gen_parameterize = st.checkbox("Parameterize Similar Tests", value=True, key="cg_param")
            gen_comments = st.checkbox("Include Comments", value=True, key="cg_comments")

    # ── Generate Button ───────────────────────────────────────────────
    st.markdown("---")
    test_cases = st.session_state.get("codegen_test_cases", [])
    can_generate = len(test_cases) > 0

    if not can_generate:
        st.info("Add at least one test case above to generate automated code.")

    if st.button("🚀 Generate Automated Tests", key="btn_generate_code", type="primary", disabled=not can_generate):
        from src.codegen import CodeGenOrchestrator, CodeGenRequest, ManualTestCase, TestStep, TargetFramework, CodeGenOptions

        fw_value = _TARGET_FRAMEWORKS[fw_label]
        framework = TargetFramework(fw_value)

        # Convert session state test cases to Pydantic models
        manual_tests = []
        for tc in test_cases:
            steps = [
                TestStep(
                    step_number=s.get("step_number", i+1),
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
            generate_page_objects=gen_page_objects,
            generate_fixtures=gen_fixtures,
            parameterize_similar=gen_parameterize,
            include_comments=gen_comments,
        )

        request = CodeGenRequest(
            test_cases=manual_tests,
            target_framework=framework,
            options=options,
            selector_map=st.session_state.get("codegen_selector_map", {}),
        )

        with st.spinner("🤖 Generating automated test code..."):
            try:
                orchestrator = CodeGenOrchestrator(llm_client=_client)
                result = orchestrator.generate(request)
                st.session_state.codegen_result = result
            except Exception as e:
                st.error(f"Generation failed: {e}")
                import traceback
                st.code(traceback.format_exc())

    # ── Display Results ───────────────────────────────────────────────
    if "codegen_result" in st.session_state and st.session_state.codegen_result:
        result = st.session_state.codegen_result
        st.markdown("---")
        st.markdown("## 📦 Generated Test Suite")

        # Stats bar
        stats = result.stats
        stat_cols = st.columns(5)
        with stat_cols[0]:
            st.metric("Total Steps", stats.total_steps)
        with stat_cols[1]:
            st.metric("Template", stats.template_handled)
        with stat_cols[2]:
            st.metric("LLM", stats.llm_handled)
        with stat_cols[3]:
            st.metric("Patterns Learned", stats.patterns_learned)
        with stat_cols[4]:
            st.metric("Confidence", f"{result.confidence_score:.0%}")

        st.markdown(f"**Framework:** {result.framework} | **Run:** `{result.run_command}`")
        st.markdown(f"**Install:** `{result.install_instructions}`")

        # Display each generated file
        for gf in result.files:
            with st.expander(f"📄 {gf.path} ({gf.file_type.value})", expanded=(gf.file_type.value == "test")):
                st.code(gf.content, language=_code_language(result.framework))

        # Download as ZIP
        import io
        import zipfile
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

        # Selector map reminder
        if result.selector_map:
            with st.expander("🎯 Selectors to verify"):
                st.caption("These selectors were auto-generated. Verify them against your app's DOM:")
                for name, sel in result.selector_map.items():
                    st.text(f"  {name}  →  {sel}")


def _code_language(framework: str) -> str:
    """Map framework name to Streamlit code language."""
    lang_map = {
        "playwright_ts": "typescript",
        "playwright_py": "python",
        "selenium_py": "python",
        "selenium_java": "java",
        "cypress_js": "javascript",
        "rest_assured": "java",
        "robot_framework": "robot",
    }
    return lang_map.get(framework, "text")


# ── Tab: Coverage Analyser ────────────────────────────────────────────
def _render_coverage_tab() -> None:
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

    # ── Metric cards ──────────────────────────────────────────────────
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

    # ── Upload + auto-analyse ─────────────────────────────────────────
    with st.expander("📥 Upload Test Cases (Excel)", expanded=not bool(tcs)):
        st.markdown(
            "Expected columns *(order & case don't matter)*: "
            "`Test ID` · `Description` · `Required Capability` · `Steps` · `Expected Result`"
        )
        xl_file = st.file_uploader("Excel file (.xlsx)", type=["xlsx"], key="xl_upload")
        cicd_platform = st.selectbox(
            "CI/CD platform for export",
            ["GitHub Actions", "GitLab CI", "Azure DevOps"],
            key="cicd_platform",
        )
        col_parse, col_reset = st.columns([1, 1])
        with col_parse:
            parse_clicked = st.button("📂 Parse & Analyse", key="btn_parse_xl",
                                      use_container_width=True, type="primary")
        with col_reset:
            if st.button("🗑 Clear", key="btn_clear_xl", use_container_width=True):
                for k in ("excel_test_cases", "excel_summary", "coverage_result",
                          "coverage_best_fw", "coverage_best_pct", "cicd_yaml"):
                    st.session_state[k] = [] if k == "excel_test_cases" else ""
                st.rerun()

        if parse_clicked:
            if xl_file is None:
                st.warning("Upload an Excel file first.")
            else:
                from src.tools.excel_parser import parse_excel_test_cases, summarise_excel
                from src.tools.coverage_engine import analyze_coverage, render_coverage_report

                raw        = xl_file.read()
                test_cases = parse_excel_test_cases(raw)
                if not test_cases:
                    st.error("Could not parse any test cases. Check column headers.")
                else:
                    _COV_STEPS = [
                        "Parsing Excel",
                        "Building capability map",
                        "Scoring frameworks",
                        "Finding best combinations",
                        "Generating report",
                    ]
                    loading_slot = st.empty()
                    try:
                        for step_i in range(len(_COV_STEPS) - 1):
                            with loading_slot.container():
                                _render_loading(_COV_STEPS, step_i, "Analysing coverage…")
                            import time; time.sleep(0.25)

                        analysis = analyze_coverage(test_cases, kb)
                        report   = render_coverage_report(analysis, len(test_cases))

                        cov_data = analysis["coverage"]
                        if cov_data:
                            best = max(cov_data.items(), key=lambda x: x[1]["pct_with_partial"])
                            st.session_state.coverage_best_fw  = best[0]
                            st.session_state.coverage_best_pct = f"{best[1]['pct_with_partial']:.0f}%"

                        with loading_slot.container():
                            _render_loading(_COV_STEPS, len(_COV_STEPS) - 1, "Done!")
                        import time; time.sleep(0.2)
                        loading_slot.empty()

                        st.session_state.excel_test_cases = test_cases
                        st.session_state.excel_summary    = summarise_excel(test_cases)
                        st.session_state.coverage_result  = report
                        st.session_state.cicd_yaml        = _build_cicd_yaml(test_cases, cicd_platform)
                        st.rerun()
                    except Exception as exc:
                        loading_slot.empty()
                        st.error(f"Analysis failed: {exc}")
                        logger.error("Coverage analysis error: %s", exc)

        if st.session_state.excel_summary:
            st.markdown(st.session_state.excel_summary)

    # ── Results ───────────────────────────────────────────────────────
    if st.session_state.coverage_result:
        st.markdown("---")
        st.markdown(st.session_state.coverage_result)

    if st.session_state.cicd_yaml:
        st.markdown("---")
        st.markdown("### ⬇️ CI/CD Pipeline Export")
        platform = st.session_state.get("cicd_platform", "GitHub Actions")
        filename_map = {
            "GitHub Actions": "github-actions.yml",
            "GitLab CI":      ".gitlab-ci.yml",
            "Azure DevOps":   "azure-pipelines.yml",
        }
        filename = filename_map.get(platform, "ci-pipeline.yml")
        st.code(st.session_state.cicd_yaml, language="yaml")
        st.download_button(
            label=f"⬇ Download {filename}",
            data=st.session_state.cicd_yaml,
            file_name=filename,
            mime="text/yaml",
            key="btn_download_cicd",
        )

    # ── Manual entry (only when no Excel loaded) ──────────────────────
    if not tcs:
        st.markdown("---")
        with st.expander("✏️ Or enter test cases manually", expanded=False):
            st.caption(
                "Same fields as Excel: `id`, `description`, `capability`, "
                "`steps`, `expected_result`. Capability drives analysis — "
                "`smoke`, `regression`, `e2e` all work via semantic matching."
            )
            st.markdown("**Quick template:**")
            tcol1, tcol2, tcol3 = st.columns([1, 2, 1])
            with tcol1:
                tmpl_count = st.number_input(
                    "# test cases", min_value=1, max_value=20, value=3, key="tmpl_count"
                )
            with tcol2:
                tmpl_cap = st.selectbox(
                    "Capability",
                    ["ui automation", "api testing", "mobile testing",
                     "performance testing", "visual testing",
                     "accessibility testing", "network mocking",
                     "cross browser", "parallel execution", "e2e testing"],
                    key="tmpl_cap",
                )
            with tcol3:
                st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                if st.button("📝 Generate", key="btn_gen_tmpl"):
                    import json as _json
                    st.session_state["manual_tc_prefill"] = _json.dumps([
                        {
                            "id": f"TC{i+1:03d}",
                            "description": f"Test case {i+1} description",
                            "capability": tmpl_cap,
                            "steps": "Step 1\nStep 2",
                            "expected_result": "Expected outcome",
                        }
                        for i in range(int(tmpl_count))
                    ], indent=2)

            manual_json = st.text_area(
                "Test cases JSON",
                value=st.session_state.get("manual_tc_prefill", ""),
                height=220,
                key="manual_tc_json",
                placeholder='[{"id":"TC001","description":"Login test","capability":"ui automation"}]',
            )

            if st.button("▶ Analyse Manual Input", key="btn_manual_analyse", type="primary"):
                import json
                try:
                    from src.tools.excel_parser import summarise_excel
                    from src.tools.coverage_engine import analyze_coverage, render_coverage_report
                    raw_tcs = json.loads(manual_json)
                    if not isinstance(raw_tcs, list):
                        raise ValueError("Must be a JSON array")
                    normalised = [{
                        "id": tc.get("id") or tc.get("test_id") or "",
                        "description": tc.get("description") or tc.get("desc") or "",
                        "required_capability": (
                            tc.get("required_capability") or tc.get("capability")
                            or tc.get("type") or "ui automation"
                        ).lower(),
                        "steps": tc.get("steps") or "",
                        "expected_result": tc.get("expected_result") or "",
                    } for tc in raw_tcs]

                    analysis = analyze_coverage(normalised, kb)
                    report   = render_coverage_report(analysis, len(normalised))
                    cov_data = analysis["coverage"]
                    if cov_data:
                        best = max(cov_data.items(), key=lambda x: x[1]["pct_with_partial"])
                        st.session_state.coverage_best_fw  = best[0]
                        st.session_state.coverage_best_pct = f"{best[1]['pct_with_partial']:.0f}%"

                    st.session_state.coverage_result  = report
                    st.session_state.cicd_yaml        = _build_cicd_yaml(normalised, "GitHub Actions")
                    st.session_state.excel_summary    = summarise_excel(normalised)
                    st.session_state.excel_test_cases = normalised
                    st.rerun()
                except Exception as exc:
                    st.error(f"Invalid input: {exc}")


def _build_cicd_yaml(test_cases: list[dict], platform: str) -> str:
    caps = list({tc.get("required_capability", "ui automation") for tc in test_cases})
    install_parts: list[str] = ["pip install -r requirements.txt"]
    test_cmd_parts: list[str] = []
    report_paths: list[str] = ["test-results/"]

    for fw in kb.list_all():
        fw_caps = set(fw.capabilities.keys())
        if any(c.replace(" ", "_") in fw_caps or c in fw_caps for c in caps):
            install_parts.extend(fw.install_commands)
            if fw.test_command and fw.test_command not in test_cmd_parts:
                test_cmd_parts.append(fw.test_command)
            report_paths.extend(fw.report_paths)

    if not test_cmd_parts:
        test_cmd_parts.append("pytest tests/")
    report_paths = list(dict.fromkeys(report_paths))

    if platform == "GitHub Actions":
        return _gh_actions_yaml(install_parts, test_cmd_parts, report_paths)
    elif platform == "GitLab CI":
        return _gitlab_yaml(install_parts, test_cmd_parts, report_paths)
    else:
        return _azure_yaml(install_parts, test_cmd_parts, report_paths)


def _gh_actions_yaml(install_parts: list[str], test_cmds: list[str], report_paths: list[str] | None = None) -> str:
    install_str = "\n".join(f"          {cmd}" for cmd in install_parts)
    test_str    = "\n".join(f"          {cmd}" for cmd in test_cmds)
    artifact_str = "\n".join(f"            {p}" for p in (report_paths or ["test-results/"]))
    return f"""\
name: Automated Tests

on:
  push:
    branches: [main, develop]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
{install_str}

      - name: Run tests
        run: |
{test_str}

      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: test-results
          path: |
{artifact_str}
"""


def _gitlab_yaml(install_parts: list[str], test_cmds: list[str], report_paths: list[str] | None = None) -> str:
    install_str  = "\n".join(f"    - {cmd}" for cmd in install_parts)
    test_str     = "\n".join(f"    - {cmd}" for cmd in test_cmds)
    artifact_str = "\n".join(f"      - {p}" for p in (report_paths or ["test-results/"]))
    return f"""\
stages:
  - install
  - test

install:
  stage: install
  image: python:3.11
  script:
{install_str}
  cache:
    paths:
      - .venv/

test:
  stage: test
  image: python:3.11
  script:
{test_str}
  artifacts:
    when: always
    paths:
{artifact_str}
"""


def _azure_yaml(install_parts: list[str], test_cmds: list[str], report_paths: list[str] | None = None) -> str:
    install_str = "\n".join(f"          {cmd}" for cmd in install_parts)
    test_str    = "\n".join(f"          {cmd}" for cmd in test_cmds)
    return f"""\
trigger:
  branches:
    include:
      - main
      - develop

pool:
  vmImage: ubuntu-latest

steps:
  - task: UsePythonVersion@0
    inputs:
      versionSpec: '3.11'

  - script: |
{install_str}
    displayName: Install dependencies

  - script: |
{test_str}
    displayName: Run tests

  - task: PublishTestResults@2
    condition: always()
    inputs:
      testResultsFiles: '**/test-results/*.xml'
"""


# ── Main ──────────────────────────────────────────────────────────────
def main() -> None:
    _render_sidebar()
    _render_topnav()

    page = st.session_state.page

    if page == "home":
        _render_home()
    elif page == "advisor":
        _render_advisor_tab()
    elif page == "studio":
        _render_code_studio_tab()
    elif page == "codegen":
        _render_codegen_tab()
    elif page == "coverage":
        _render_coverage_tab()


if __name__ == "__main__":
    main()
