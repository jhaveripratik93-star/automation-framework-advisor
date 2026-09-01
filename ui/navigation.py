"""Top navigation bar — single mechanism (Streamlit buttons only)."""
from __future__ import annotations

import streamlit as st

_NAV_ITEMS = [
    ("home",     "🏠", "Home"),
    ("advisor",  "🧭", "Advisor"),
    ("studio",   "🐍", "Code Studio"),
    ("codegen",  "🤖", "Test Generator"),
    ("coverage", "📊", "Coverage"),
]


def render() -> None:
    page = st.session_state.page
    cols = st.columns(len(_NAV_ITEMS))
    for col, (key, icon, label) in zip(cols, _NAV_ITEMS):
        with col:
            if st.button(
                f"{icon} {label}",
                key=f"nav_btn_{key}",
                use_container_width=True,
                type="primary" if page == key else "secondary",
            ):
                st.session_state.page = key
                st.rerun()
