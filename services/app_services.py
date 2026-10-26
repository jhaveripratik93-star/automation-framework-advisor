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
@st.cache_resource
def get_kb():
    from src.knowledge_base import KnowledgeBase
    kb = KnowledgeBase(data_dir="data/frameworks")
    kb.load()
    return kb


# ── Groq client ───────────────────────────────────────────────────────
@st.cache_resource
def get_groq_client():
    from src.llm.groq_client import GroqClient
    client = GroqClient()
    if not client.is_available:
        logger.warning("Groq: GROQ_API_KEY not set")
    else:
        logger.info("Groq: model=%s ready", client.model)
    return client


# ── Gemini client ─────────────────────────────────────────────────────
@st.cache_resource
def get_gemini_client():
    from src.llm.gemini_client import GeminiClient
    client = GeminiClient()
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
    from src.graph import load_or_seed_graph
    from src.graph.graphrag_engine import GraphRAGEngine
    from src.llm.advisor_llm import AdvisorLLM

    kb = get_kb()
    client = get_llm_client()
    graph = load_or_seed_graph(kb)
    graphrag = GraphRAGEngine(graph=graph, knowledge_base=kb)
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
    from src.discovery.framework_scanner import FrameworkScanner
    kb = get_kb()
    client = get_llm_client()           # reuse — no duplicate init
    return FrameworkScanner(
        kb=kb,
        llm_client=client if client.is_available else None,
        data_dir="data/frameworks",
    )
