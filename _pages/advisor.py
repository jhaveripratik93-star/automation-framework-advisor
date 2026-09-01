"""Advisor page — lazy-loads Graph/GraphRAG/AdvisorLLM on first use."""
from __future__ import annotations

import logging

import streamlit as st

from ui.components import render_copy_button, render_thinking

logger = logging.getLogger(__name__)

_TOPIC_KEYWORDS = {
    "playwright", "cypress", "selenium", "webdriverio", "puppeteer", "testcafe",
    "robot", "appium", "karate", "k6", "locust", "rest assured", "restassured",
    "pytest", "junit", "mocha", "jest", "jasmine", "cucumber", "behave",
    "terraform", "ansible", "pulumi", "chef", "cloudformation",
    "test", "testing", "automation", "framework", "migrate", "migration",
    "coverage", "e2e", "end-to-end", "ui", "api", "mobile", "performance",
    "load", "smoke", "regression", "integration", "functional", "unit",
    "assertion", "selector", "locator", "fixture", "mock", "stub", "spy",
    "browser", "headless", "parallel", "flaky", "retry", "report",
    "ci", "cd", "cicd", "pipeline", "github actions", "gitlab", "azure devops",
    "jenkins", "docker", "container", "deploy", "build", "artifact",
    "compare", "comparison", "recommend", "recommendation",
    "framework score", "scoring matrix", "weighted score",
    "best framework", "choose framework", "which framework",
    "pros and cons", "tradeoff", "trade-off",
    "convert", "boilerplate", "template", "setup", "install", "config",
    "python", "javascript", "typescript", "java", "node", "npm", "pip",
    "package", "dependency", "library", "plugin", "extension",
    "case study", "case", "study", "document", "upload", "uploaded",
    "how", "what", "info", "information", "tell", "give", "show", "explain",
    "help", "weight", "preset", "score", "advisor", "tool", "feature",
    "amazon", "google", "microsoft", "netflix", "uber", "airbnb",
    "company", "project", "team", "enterprise", "startup", "organization",
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

_CRITERIA_LABELS = {
    "C1_language_compatibility": "Language Compatibility",
    "C2_api_validation":         "API Validation",
    "C3_performance_load":       "Performance Testing",
    "C4_cicd_integration":       "CI/CD Integration",
    "C5_maintainability":        "Maintainability",
    "C6_cloud_readiness":        "Cloud Readiness",
    "C7_license_cost":           "License & Cost",
}


def _is_off_topic(text: str) -> bool:
    lowered = text.lower()
    return not any(kw in lowered for kw in _TOPIC_KEYWORDS)


def render() -> None:
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
        ), "is_llm_response": False})

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="🧭" if msg["role"] == "assistant" else "👤"):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and msg.get("is_llm_response", False):
                render_copy_button(msg["content"])

    if user_input := st.chat_input("Ask about frameworks, migrations, or coverage..."):
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user", avatar="👤"):
            st.markdown(user_input)

        if _is_off_topic(user_input):
            with st.chat_message("assistant", avatar="🧭"):
                st.markdown(_OFF_TOPIC_REPLY)
                render_copy_button(_OFF_TOPIC_REPLY)
            st.session_state.messages.append(
                {"role": "assistant", "content": _OFF_TOPIC_REPLY, "is_llm_response": True}
            )
            st.stop()

        from src.scoring.weights import CRITERIA_IDS
        wp = st.session_state.weight_profile
        weight_context = "Active scoring weights: " + ", ".join(
            f"{_CRITERIA_LABELS.get(k, k)}={v:.0%}"
            for k, v in wp.weights.items() if k in CRITERIA_IDS
        )

        with st.chat_message("assistant", avatar="🧭"):
            loading_slot = st.empty()
            with loading_slot.container():
                render_thinking("Advisor is thinking…")

            # Lazy-load the advisor stack only now
            from services.app_services import get_advisor_stack
            _client, _graph, _graphrag, advisor = get_advisor_stack()

            try:
                if hasattr(_client, "reset_session_usage"):
                    _client.reset_session_usage()
                response = advisor.run_pipeline(
                    user_input,
                    st.session_state.uploaded_docs_context,
                    st.session_state.case_study_context,
                    weight_context=weight_context,
                    weight_profile=st.session_state.weight_profile,
                )
            except Exception as exc:
                logger.warning("Pipeline failed (%s) — falling back to direct respond", exc)
                response = advisor.respond(
                    user_input,
                    st.session_state.uploaded_docs_context,
                    st.session_state.case_study_context,
                )

            loading_slot.empty()
            st.markdown(response)
            render_copy_button(response)

            if hasattr(_client, "get_session_usage"):
                usage = _client.get_session_usage()
                logger.info(
                    "TOKEN USAGE | query='%.60s' | prompt=%d | completion=%d | total=%d",
                    user_input,
                    usage["prompt_tokens"],
                    usage["completion_tokens"],
                    usage["total_tokens"],
                )

        st.session_state.messages.append(
            {"role": "assistant", "content": response, "is_llm_response": True}
        )
