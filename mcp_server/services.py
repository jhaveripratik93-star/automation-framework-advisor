"""Lazy singleton service accessors for the MCP server.

Mirrors ``services/app_services.py`` (the Streamlit lazy-loader) but has
no Streamlit dependency — this module is safe to import in a plain
Python process, since the MCP server runs standalone over stdio.

Nothing expensive runs at import time. Each getter initialises its
service on first use and caches it for the lifetime of the process.
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# ── Bootstrap: make `src` importable, load .env ───────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / "config" / ".env")

logger = logging.getLogger(__name__)

_lock = threading.RLock()  # reentrant — getters below call other getters while holding it
_kb: Any = None
_groq_client: Any = None
_graph: Any = None
_graphrag: Any = None
_codegen_orchestrator: Any = None
_scanner: Any = None


# ── Core singletons ────────────────────────────────────────────────────

def get_kb():
    """Lazily load the YAML framework knowledge base."""
    global _kb
    if _kb is None:
        with _lock:
            if _kb is None:
                from src.knowledge_base import KnowledgeBase
                kb = KnowledgeBase(data_dir=str(_ROOT / "data" / "frameworks"))
                kb.load()
                _kb = kb
    return _kb


def get_groq_client():
    """Lazily create the Groq LLM client. LLM-backed tools (convert_test_cases,
    convert_test_files, generate_test_code, discover_new_frameworks,
    add_framework_to_kb) require GROQ_API_KEY to be set in config/.env."""
    global _groq_client
    if _groq_client is None:
        with _lock:
            if _groq_client is None:
                from src.llm.groq_client import GroqClient
                client = GroqClient()
                if not client.is_available:
                    logger.warning(
                        "GROQ_API_KEY is not set — LLM-backed MCP tools will "
                        "raise RuntimeError until it is configured in config/.env"
                    )
                _groq_client = client
    return _groq_client


def get_graph():
    """Lazily load (or seed) the persistent knowledge graph."""
    global _graph
    if _graph is None:
        with _lock:
            if _graph is None:
                from src.graph import load_or_seed_graph
                _graph = load_or_seed_graph(
                    get_kb(), store_path=str(_ROOT / "data" / "knowledge_graph.json")
                )
    return _graph


def get_graphrag():
    """Lazily build the GraphRAG retrieval engine."""
    global _graphrag
    if _graphrag is None:
        with _lock:
            if _graphrag is None:
                from src.graph.graphrag_engine import GraphRAGEngine
                _graphrag = GraphRAGEngine(graph=get_graph(), knowledge_base=get_kb())
    return _graphrag


def get_tool_executor(weight_profile=None, uploaded_docs: str = "", case_study: str = ""):
    """Build a fresh ToolExecutor bound to the shared KB/graph/GraphRAG singletons.

    A new instance is created per call (construction is cheap — it just stores
    references) so that per-call context (uploaded docs, case study text,
    scoring weight profile) never leaks between unrelated tool invocations.
    """
    from src.tools.executor import ToolExecutor
    executor = ToolExecutor(
        knowledge_graph=get_graph(),
        graphrag_engine=get_graphrag(),
        knowledge_base=get_kb(),
        uploaded_docs_context=uploaded_docs,
        case_study_context=case_study,
        weight_profile=weight_profile,
    )
    executor._llm = get_groq_client()
    return executor


def get_codegen_orchestrator():
    """Lazily create the hybrid template+LLM test-code generation orchestrator."""
    global _codegen_orchestrator
    if _codegen_orchestrator is None:
        with _lock:
            if _codegen_orchestrator is None:
                from src.codegen import CodeGenOrchestrator
                _codegen_orchestrator = CodeGenOrchestrator(llm_client=get_groq_client())
    return _codegen_orchestrator


def get_scanner():
    """Lazily create the framework discovery scanner (GitHub + curated sources)."""
    global _scanner
    if _scanner is None:
        with _lock:
            if _scanner is None:
                from src.discovery.framework_scanner import FrameworkScanner
                client = get_groq_client()
                _scanner = FrameworkScanner(
                    kb=get_kb(),
                    llm_client=client if client.is_available else None,
                    data_dir=str(_ROOT / "data" / "frameworks"),
                )
    return _scanner


# ── Builders — turn simple MCP tool arguments into domain objects ─────

def build_weight_profile(preset: str = "balanced", custom_weights: dict[str, float] | None = None):
    """Build a WeightProfile from a named preset, or custom per-criterion weights."""
    from src.scoring.weights import WeightProfile

    if custom_weights:
        wp = WeightProfile(weights=dict(custom_weights), profile_name="custom")
    else:
        try:
            wp = WeightProfile.from_preset(preset)
        except ValueError:
            logger.warning("Unknown weight preset '%s' — falling back to 'balanced'", preset)
            wp = WeightProfile.default()
    wp.normalize()
    return wp


def build_user_profile(
    project_name: str,
    primary_language: str = "python",
    team_size: int = 5,
    architecture_types: list[str] | None = None,
    automation_experience: str = "intermediate",
    ci_cd_tool: str = "github_actions",
    legacy_test_count: int = 0,
    timeline_weeks: int = 12,
    must_support: list[str] | None = None,
    budget: str = "oss_preferred",
    containerized: bool = False,
    browsers_required: list[str] | None = None,
    special_ui: list[str] | None = None,
):
    """Build a UserProfile from simplified MCP tool arguments.

    Unknown enum values (architecture type, experience level, budget) are
    logged and replaced with sensible defaults rather than raising, so a
    loosely-specified MCP call still produces a usable profile.
    """
    from src.models import UserProfile, ArchitectureType, ExperienceLevel, BudgetPreference

    def _enum(enum_cls, value, default):
        try:
            return enum_cls(value)
        except ValueError:
            logger.warning(
                "Invalid %s value '%s' — using default '%s'",
                enum_cls.__name__, value, default,
            )
            return enum_cls(default)

    arch_types = []
    for a in (architecture_types or ["web_spa"]):
        try:
            arch_types.append(ArchitectureType(a))
        except ValueError:
            logger.warning("Unknown architecture_type '%s' — skipping", a)
    if not arch_types:
        arch_types = [ArchitectureType.WEB_SPA]

    return UserProfile(
        project_name=project_name,
        architecture_types=arch_types,
        primary_language=primary_language,
        team_size=max(1, team_size),
        automation_experience=_enum(ExperienceLevel, automation_experience, "intermediate"),
        ci_cd_tool=ci_cd_tool,
        legacy_test_count=max(0, legacy_test_count),
        timeline_weeks=max(1, timeline_weeks),
        must_support=must_support or [],
        budget=_enum(BudgetPreference, budget, "oss_preferred"),
        containerized=containerized,
        browsers_required=browsers_required or [],
        special_ui=special_ui or [],
    )
