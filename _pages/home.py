"""Home page."""
from __future__ import annotations

import streamlit as st


def render() -> None:
    from services.app_services import get_kb
    kb = get_kb()
    fw_count = len(kb.list_all())

    _top_l, _top_r = st.columns([5, 1])
    with _top_r:
        if st.button("⚙️ Settings", key="home_open_settings", use_container_width=True):
            from ui.settings_dialog import render_settings_dialog
            render_settings_dialog()

    st.markdown(f"""
    <div class="home-hero">
        <div class="home-hero-eyebrow">✨ AI-Powered Testing Advisor</div>
        <div class="home-hero-title">Automation Framework Advisor</div>
        <div class="home-hero-sub">
            AI-powered tool to evaluate, migrate, and verify test automation frameworks.
            Compare {fw_count} frameworks, convert test suites, and analyse coverage gaps — all in one place.
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="home-stats">
        <div class="home-stat-pill">
            <div class="home-stat-icon">🧩</div>
            <div><div class="home-stat-value">{fw_count}</div><div class="home-stat-label">Frameworks</div></div>
        </div>
        <div class="home-stat-pill">
            <div class="home-stat-icon">🤖</div>
            <div><div class="home-stat-value">AI</div><div class="home-stat-label">Powered by Groq / Gemini</div></div>
        </div>
        <div class="home-stat-pill">
            <div class="home-stat-icon">🧭</div>
            <div><div class="home-stat-value">5</div><div class="home-stat-label">Core Features</div></div>
        </div>
        <div class="home-stat-pill">
            <div class="home-stat-icon">📡</div>
            <div><div class="home-stat-value">Live</div><div class="home-stat-label">Continuous KB</div></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="section-header">
        <span class="section-header-title">Explore features</span>
        <span class="section-header-sub">— pick a tool to get started</span>
    </div>
    """, unsafe_allow_html=True)

    _render_feature_grid()

    st.markdown("---")
    _render_quickstart()


def _feature_card(*, icon: str, title: str, desc: str, tags: list[str],
                   button_label: str, button_key: str, target_page: str) -> None:
    """Render one feature card + its action button as a single bordered
    unit (see the stVerticalBlockBorderWrapper rule in static/styles.css)
    instead of a floating HTML card with a visually disconnected button
    underneath it."""
    with st.container(border=True):
        tag_html = "".join(f'<span class="feature-tag">{t}</span>' for t in tags)
        st.markdown(f"""
        <div class="feature-card">
            <div class="feature-icon-badge">{icon}</div>
            <div class="feature-card-title">{title}</div>
            <div class="feature-card-desc">{desc}</div>
            <div class="feature-card-tags">{tag_html}</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button(button_label, key=button_key, use_container_width=True):
            st.session_state.page = target_page
            st.rerun()


def _quickstart_card(col, *, num: int, title: str, steps: list[str]) -> None:
    with col:
        steps_html = "".join(f"<li>{s}</li>" for s in steps)
        st.markdown(f"""
        <div class="quickstart-card">
            <div class="quickstart-head">
                <div class="quickstart-num">{num}</div>
                <div class="quickstart-title">{title}</div>
            </div>
            <ol class="quickstart-steps">{steps_html}</ol>
        </div>
        """, unsafe_allow_html=True)


def _render_feature_grid() -> None:
    c1, c2, c3 = st.columns(3)
    with c1:
        _feature_card(
            icon="🧭", title="Framework Advisor",
            desc=("Chat with an AI agent to compare frameworks, get migration plans, "
                  "and receive scored recommendations tailored to your project."),
            tags=["LLM Chat", "Scoring Matrix", "Migration Plan"],
            button_label="Open Advisor →", button_key="home_go_advisor", target_page="advisor",
        )
    with c2:
        _feature_card(
            icon="🐍", title="Framework Migration",
            desc=("Convert test files between frameworks automatically. "
                  "Supports single-file paste or full repo upload with "
                  "gap-bridging helper scripts and CI/CD config."),
            tags=["Auto Convert", "Multi-file", "ZIP Download"],
            button_label="Open Code Studio →", button_key="home_go_studio", target_page="studio",
        )
    with c3:
        _feature_card(
            icon="🔍", title="Repo Comparator",
            desc=("Compare two test repos across different frameworks. "
                  "Matches test cases by name and reports assertion-count "
                  "parity so nothing gets silently dropped during migration."),
            tags=["Cross-Repo", "Assertion Parity", "ZIP Upload"],
            button_label="Open Repo Comparator →", button_key="home_go_repo_compare", target_page="repo_compare",
        )

    c4, c5, c6 = st.columns(3)
    with c4:
        _feature_card(
            icon="📊", title="Coverage Analyzer",
            desc=("Upload your test cases via Excel, JSON, or CSV. See what "
                  "percentage of them each framework's capabilities actually "
                  "cover, with a full test-case × framework matrix and gap details."),
            tags=["Excel Upload", "Coverage Matrix", "Best-Fit Ranking"],
            button_label="Open Coverage Analyzer →", button_key="home_go_coverage", target_page="coverage",
        )
    with c5:
        _feature_card(
            icon="🤖", title="Test Generator",
            desc=("Convert manual test cases into executable automated test code. "
                  "Supports Playwright, Selenium, Cypress, and Robot Framework "
                  "with page objects, fixtures, and CI/CD config."),
            tags=["Manual → Auto", "Page Objects", "Multi-framework"],
            button_label="Open Test Generator →", button_key="home_go_codegen", target_page="codegen",
        )
    with c6:
        st.empty()


def _render_quickstart() -> None:
    st.markdown("""
    <div class="section-header" style="margin-top:0.5rem;">
        <span class="section-header-title">🚀 Quick Start</span>
        <span class="section-header-sub">— five common journeys through the tool</span>
    </div>
    """, unsafe_allow_html=True)

    qa, qb, qc = st.columns(3)
    _quickstart_card(qa, num=1, title="New to the tool?", steps=[
        "Start with <b>Advisor</b> — ask which framework fits your project",
        "Get a scored comparison and migration roadmap",
        "Export the CI/CD pipeline",
    ])
    _quickstart_card(qb, num=2, title="Migrating a test suite?", steps=[
        "Go to <b>Code Studio</b>",
        "Upload your existing test files",
        "Download the converted project as a ZIP",
    ])
    _quickstart_card(qc, num=3, title="Verifying a migration?", steps=[
        "Go to <b>Repo Comparator</b>",
        "Upload the old and new repos (or .zip files)",
        "Review the assertion-parity report for gaps",
    ])

    st.markdown('<div style="height:0.85rem"></div>', unsafe_allow_html=True)

    qd, qe, _qf = st.columns(3)
    _quickstart_card(qd, num=4, title="Verifying coverage?", steps=[
        "Go to <b>Coverage Analyzer</b>",
        "Upload your test case Excel sheet",
        "See which frameworks cover 100% of your needs",
    ])
    _quickstart_card(qe, num=5, title="Automating manual tests?", steps=[
        "Go to <b>Test Generator</b>",
        "Enter or upload manual test cases",
        "Download generated test code as a ZIP",
    ])
