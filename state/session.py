"""Session state defaults and initialisation."""
from __future__ import annotations

import streamlit as st


_DEFAULTS: dict = {
    # Navigation
    "page": "home",
    # Advisor
    "messages": [],
    "uploaded_docs_context": "",
    "case_study_context": "",
    "case_study_urls": [],
    # Weights — populated after KB is available; see init()
    "weight_profile": None,
    # Code Studio
    "playground_code": "",
    "last_converted_code": "",
    "studio_source_code": "",
    "studio_from_fw": "",
    "studio_to_fw": "",
    "studio_mode": "Single file (paste)",
    "studio_gap_block": "",
    "studio_cicd_block": "",
    "studio_widget_key_v": 0,
    "multi_convert_result": None,
    # Coverage
    "excel_test_cases": [],
    "excel_summary": "",
    "coverage_result": "",
    "coverage_best_fw": "",
    "coverage_best_pct": "",
    "cicd_yaml": "",
    # Framework discovery
    "scan_results": [],
    "scan_message": "",
    # Loading
    "llm_busy": False,
    "llm_step": 0,
}


def init(weight_profile_default=None) -> None:
    """Apply defaults once per session.  Call after KB is loaded."""
    for k, v in _DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v

    if st.session_state.weight_profile is None and weight_profile_default is not None:
        st.session_state.weight_profile = weight_profile_default
