"""Automation Framework Migration & Coverage Advisor — Streamlit bootstrap."""
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

# ── Page config (must be first Streamlit call) ────────────────────────
st.set_page_config(
    page_title="Framework Advisor",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"Get help": None, "Report a bug": None, "About": None},
)

# ── Lightweight startup: KB only ──────────────────────────────────────
from services.app_services import get_kb
from ui.components import inject_css
from ui.navigation import render as render_nav
from ui.sidebar import render as render_sidebar
from state.session import init as init_session

kb = get_kb()                                   # fast — just YAML loads

from src.scoring.weights import WeightProfile
init_session(weight_profile_default=WeightProfile.default())

# ── Lazy case-study load (filenames only on startup) ──────────────────
if not st.session_state.get("_case_studies_loaded"):
    _cs_dir = Path("data/frameworks/case_studies")
    if _cs_dir.exists():
        _parts = []
        for _f in sorted(_cs_dir.glob("*.txt")):
            try:
                _parts.append(f"--- Source: {_f.stem} ---\n{_f.read_text(encoding='utf-8')}")
            except Exception:
                pass
        if _parts:
            st.session_state.case_study_context = "\n\n".join(_parts)
    st.session_state["_case_studies_loaded"] = True

# ── Render ────────────────────────────────────────────────────────────
inject_css()
render_sidebar()
render_nav()

page = st.session_state.page

if page == "home":
    from _pages.home import render
    render()
elif page == "advisor":
    from _pages.advisor import render
    render()
elif page == "studio":
    from _pages.code_studio import render
    render()
elif page == "codegen":
    from _pages.code_generator import render
    render()
elif page == "coverage":
    from _pages.coverage import render
    render()