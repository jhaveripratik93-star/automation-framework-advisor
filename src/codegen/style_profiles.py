"""Team-aware resolution of FRAMEWORK_CONTEXT (per-framework coding
style/standards used by the codegen pipeline).

FRAMEWORK_CONTEXT (src/codegen/agent_config.py) is read from five
different files deep in the generation call chain (llm_generator.py,
scenario_planner.py, suite_architect_agent.py, step_generator.py,
orchestrator.py) via `FRAMEWORK_CONTEXT.get(framework, "")`. Threading an
explicit `team_id` parameter through every function signature on that
path would be an invasive, five-file change for what is fundamentally
"which text block do I substitute for this key" — so instead this module
holds the active team as a `contextvars.ContextVar`, set once at the top
of a generation request (`CodeGenOrchestrator.generate()`) and read
wherever `get_framework_context()` replaces the old direct dict lookup.

ContextVar (not a plain module global) specifically because Streamlit
serves concurrent user sessions on different threads — a plain global
would leak one user's active team profile into another's concurrently-
running request. ContextVar values are isolated per thread/async task,
which matches how each generation request already runs synchronously
within one thread end-to-end (confirmed: no internal thread pool in the
codegen pipeline).
"""
from __future__ import annotations

import contextvars
import logging

from src.codegen.agent_config import FRAMEWORK_CONTEXT

logger = logging.getLogger(__name__)

__all__ = ["set_active_team", "get_active_team", "get_framework_context"]

_active_team: contextvars.ContextVar[str] = contextvars.ContextVar(
    "codegen_active_team", default="default"
)


def set_active_team(team_id: str) -> None:
    """Call once at the top of a generation request, before any agent
    runs — see CodeGenOrchestrator.generate()."""
    _active_team.set(team_id or "default")


def get_active_team() -> str:
    return _active_team.get()


def get_framework_context(framework_key: str, team_id: str | None = None) -> str:
    """Team-aware replacement for `FRAMEWORK_CONTEXT.get(framework_key, "")`.

    If the active (or explicitly passed) team has an on-disk override at
    config/team_profiles/<team_id>/codegen_style/<framework_key>.md, that
    text is returned instead of the built-in default. Falls back to
    FRAMEWORK_CONTEXT exactly as before for "default" or any team with no
    override for this specific framework key.

    Any reference/architecture documents attached to the team (see
    src/team_profiles.py's reference_docs_*) are appended to the result —
    this is the one place `FRAMEWORK_CONTEXT.get(...)` used to be called
    from in all 5 generation call sites, so appending here means the
    team's uploaded docs reach every one of them without threading a new
    parameter through each.
    """
    resolved_team = team_id if team_id is not None else get_active_team()
    text = FRAMEWORK_CONTEXT.get(framework_key, "")

    if resolved_team and resolved_team != "default":
        from src.team_profiles import codegen_style_dir, read_text_artifact, reference_docs_context

        style_dir = codegen_style_dir(resolved_team)
        if style_dir is not None:
            override = read_text_artifact(style_dir / f"{framework_key}.md")
            if override.strip():
                text = override

        docs = reference_docs_context(resolved_team)
        if docs:
            text = f"{text}\n\n## Team Reference Documents\n\n{docs}" if text else docs

    return text
