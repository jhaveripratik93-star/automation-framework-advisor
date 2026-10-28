"""Lazy-loaded, cached service accessors.

Nothing here runs at import time.  Each getter is called only when the
relevant page actually needs the service.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)


# ── KB (lightweight — always needed) ─────────────────────────────────
# Construction logic (which constructor args, which data_dir) lives in
# src/bootstrap.py and is shared with mcp_server/services.py — only the
# caching mechanism (@st.cache_resource here, a lock-guarded singleton
# there) differs between entry points.
@st.cache_resource
def get_kb():
    from src.bootstrap import build_kb
    return build_kb()


# ── Groq client ───────────────────────────────────────────────────────
@st.cache_resource
def get_groq_client():
    from src.bootstrap import build_groq_client
    client = build_groq_client()
    if not client.is_available:
        logger.warning("Groq: GROQ_API_KEY not set")
    else:
        logger.info("Groq: model=%s ready", client.model)
    return client


# ── Gemini client ─────────────────────────────────────────────────────
@st.cache_resource
def get_gemini_client():
    from src.bootstrap import build_gemini_client
    client = build_gemini_client()
    if not client.is_available:
        logger.warning("Gemini: GEMINI_API_KEY not set")
    else:
        logger.info("Gemini: model=%s ready", client.model)
    return client


# ── Active provider dispatch ────────────────────────────────────────
# NOT cache_resource: it takes no args, so caching it would freeze the
# first result forever and ignore later provider switches. The actual
# per-provider clients above stay cached; this just picks between them.
def get_llm_client():
    """Return whichever LLM client is currently selected as the active
    provider (see ui/settings_dialog.py). Defaults to Groq."""
    provider = st.session_state.get("llm_provider", "groq")
    if provider == "gemini":
        return get_gemini_client()
    return get_groq_client()


# ── Advisor stack (Graph + GraphRAG + AdvisorLLM) ─────────────────────
@st.cache_resource
def get_advisor_stack():
    """Initialised only when Advisor page is first opened."""
    from src.bootstrap import build_graph, build_graphrag
    from src.llm.advisor_llm import AdvisorLLM

    kb = get_kb()
    client = get_llm_client()
    graph = build_graph(kb)
    graphrag = build_graphrag(graph, kb)
    advisor = AdvisorLLM(
        llm_client=client,
        graphrag_engine=graphrag,
        knowledge_base=kb,
        knowledge_graph=graph,
    )
    return client, graph, graphrag, advisor


# ── Framework scanner ─────────────────────────────────────────────────
@st.cache_resource
def get_scanner():
    """Initialised only when Framework Discovery is opened."""
    from src.bootstrap import build_scanner
    kb = get_kb()
    client = get_llm_client()           # reuse — no duplicate init
    return build_scanner(kb, llm_client=client)
