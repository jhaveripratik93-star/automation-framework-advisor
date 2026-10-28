"""Shared factory functions for building the core service objects.

Every entry point (Streamlit, the MCP server, the FastAPI app, and the
LangGraph pipeline's agents) needs the same handful of objects: a loaded
``KnowledgeBase``, the persistent ``KnowledgeGraph``, a ``GraphRAGEngine``,
LLM clients, and the framework discovery scanner. Before this module
existed, each entry point reimplemented "how do I build a KB?" from
scratch with its own caching strategy (``@st.cache_resource`` in
``services/app_services.py``, a hand-rolled ``threading.RLock()``
singleton in ``mcp_server/services.py``, a bare module global in
``src/main.py``, and — worst of all — no caching at all in
``src/agents/format_agent.py``, which re-parsed every YAML file on every
single formatted response).

This module owns only *how to construct* each object. It intentionally
does NOT own *how long to keep it alive* — each caller wraps these plain
functions with whatever caching mechanism fits its runtime (Streamlit's
cache_resource, a lock-guarded singleton, a FastAPI lifespan global,
etc.), so caching strategy and construction logic no longer have to be
duplicated together.

For callers that have no caching layer of their own and just want "the
one shared instance for this process, built once" (e.g. a quick script,
or a fallback when no KB was threaded through), use the
``functools.lru_cache``-wrapped ``get_default_*`` helpers below instead
of calling the ``build_*`` functions directly.
"""
from __future__ import annotations

import functools
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.discovery.framework_scanner import FrameworkScanner
    from src.graph import KnowledgeGraph
    from src.graph.graphrag_engine import GraphRAGEngine
    from src.knowledge_base.loader import KnowledgeBase
    from src.llm.gemini_client import GeminiClient
    from src.llm.groq_client import GroqClient

DEFAULT_DATA_DIR = "data/frameworks"
DEFAULT_GRAPH_PATH = "data/knowledge_graph.json"


# ── Construction (no caching — callers decide how to cache) ───────────

def build_kb(data_dir: str = DEFAULT_DATA_DIR) -> KnowledgeBase:
    """Construct and load a fresh KnowledgeBase from the YAML profiles."""
    from src.knowledge_base import KnowledgeBase

    kb = KnowledgeBase(data_dir=data_dir)
    kb.load()
    return kb


def build_graph(kb: KnowledgeBase, store_path: str = DEFAULT_GRAPH_PATH) -> KnowledgeGraph:
    """Load the persistent knowledge graph, seeding it from `kb` if absent/stale."""
    from src.graph import load_or_seed_graph

    return load_or_seed_graph(kb, store_path=store_path)


def build_graphrag(graph: KnowledgeGraph, kb: KnowledgeBase) -> GraphRAGEngine:
    """Build the GraphRAG retrieval engine over an existing graph + KB."""
    from src.graph.graphrag_engine import GraphRAGEngine

    return GraphRAGEngine(graph=graph, knowledge_base=kb)


def build_groq_client() -> GroqClient:
    """Construct the Groq LLM client. GROQ_API_KEY is optional — callers
    should check `.is_available` before relying on it for LLM calls."""
    from src.llm.groq_client import GroqClient

    return GroqClient()


def build_gemini_client() -> GeminiClient:
    """Construct the Gemini LLM client. GEMINI_API_KEY is optional — callers
    should check `.is_available` before relying on it for LLM calls."""
    from src.llm.gemini_client import GeminiClient

    return GeminiClient()


def build_scanner(
    kb: KnowledgeBase,
    llm_client: object | None = None,
    data_dir: str = DEFAULT_DATA_DIR,
) -> FrameworkScanner:
    """Build the framework discovery scanner. `llm_client` is used only if
    it reports `.is_available` — otherwise the scanner runs without LLM
    assistance (template-based profiles instead of LLM-written ones)."""
    from src.discovery.framework_scanner import FrameworkScanner

    usable_client = llm_client if (llm_client is not None and getattr(llm_client, "is_available", False)) else None
    return FrameworkScanner(kb=kb, llm_client=usable_client, data_dir=data_dir)


# ── Process-wide memoized defaults ─────────────────────────────────────
# For callers with no better caching layer of their own that just need
# "the one shared instance for this process, built once." Not used by
# the Streamlit/MCP/FastAPI entry points, which each have their own
# cache lifetimes — this is specifically for fallback paths like
# FormatAgent's weight-summary builder when no KB was threaded through.

@functools.lru_cache(maxsize=1)
def get_default_kb() -> KnowledgeBase:
    return build_kb()


@functools.lru_cache(maxsize=1)
def get_default_graph() -> KnowledgeGraph:
    return build_graph(get_default_kb())
