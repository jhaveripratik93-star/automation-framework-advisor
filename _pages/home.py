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
        <div class="home-hero-title">Automation Framework Advisor</div>
        <div class="home-hero-sub">
            AI-powered tool to evaluate, migrate, and verify test automation frameworks.
            Compare {fw_count} frameworks, convert test suites, and analyse coverage gaps — all in one place.
        </div>
    </div>
    """, unsafe_allow_html=True)

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
            <div class="home-stat-value">6</div>
            <div class="home-stat-label">Core Features</div>
        </div>
        <div style="text-align:center">
            <div class="home-stat-value">Live</div>
            <div class="home-stat-label">Continuous KB</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

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
            st.session_state.page = "advisor"; st.rerun()

    with c2:
        st.markdown("""
        <div class="feature-card">
            <span class="feature-card-icon">🐍</span>
            <div class="feature-card-title">Framework Migration</div>
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
            st.session_state.page = "studio"; st.rerun()

    with c3:
        st.markdown("""
        <div class="feature-card">
            <span class="feature-card-icon">🔍</span>
            <div class="feature-card-title">Repo Comparator</div>
            <div class="feature-card-desc">
                Compare two test repos across different frameworks.
                Matches test cases by name and reports assertion-count
                parity so nothing gets silently dropped during migration.
            </div>
            <div class="feature-card-tags">
                <span class="feature-tag">Cross-Repo</span>
                <span class="feature-tag">Assertion Parity</span>
                <span class="feature-tag">ZIP Upload</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Open Repo Comparator →", key="home_go_repo_compare", use_container_width=True):
            st.session_state.page = "repo_compare"; st.rerun()

    c4, c5, c6 = st.columns(3)
    with c4:
        st.markdown("""
        <div class="feature-card">
            <span class="feature-card-icon">📊</span>
            <div class="feature-card-title">Framework Fit</div>
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
            st.session_state.page = "coverage"; st.rerun()

    with c5:
        st.markdown("""
        <div class="feature-card">
            <span class="feature-card-icon">🤖</span>
            <div class="feature-card-title">Test Generator</div>
            <div class="feature-card-desc">
                Convert manual test cases into executable automated test code.
                Supports Playwright, Selenium, Cypress, and Robot Framework
                with page objects, fixtures, and CI/CD config.
            </div>
            <div class="feature-card-tags">
                <span class="feature-tag">Manual → Auto</span>
                <span class="feature-tag">Page Objects</span>
                <span class="feature-tag">Multi-framework</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Open Test Generator →", key="home_go_codegen", use_container_width=True):
            st.session_state.page = "codegen"; st.rerun()

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
        **Verifying a migration?**
        1. Go to **Repo Comparator**
        2. Upload the old and new repos (or .zip files)
        3. Review the assertion-parity report for gaps
        """)
    qd, qe, _qf = st.columns(3)
    with qd:
        st.markdown("""
        **Verifying coverage?**
        1. Go to **Framework Fit**
        2. Upload your test case Excel sheet
        3. See which frameworks cover 100% of your needs
        """)
    with qe:
        st.markdown("""
        **Automating manual tests?**
        1. Go to **Test Generator**
        2. Enter or upload manual test cases
        3. Download generated test code as a ZIP
        """)
