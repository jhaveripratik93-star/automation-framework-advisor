"""prompts.py — Loads conversion prompt text from editable config files.

Point 7 of the conversion rewrite: "keep prompt file configurable for the
users, so that if team uses similar tool all would have similar level of
code generated." Nothing in this module hardcodes prompt wording — every
string the LLM sees is read from config/conversion_prompts/ at call time,
so editing a .md file there changes behaviour for every user of the tool
(Streamlit UI, MCP server) without a code change or redeploy.

Layout (see config/conversion_prompts/README.md for the full spec):
  base_system_prompt.md                      always included
  syntax_rules/<to_framework_slug>.md         included when target == that framework
  pair_rules/<from_slug>__to__<to_slug>.md    included for that specific (from, to) pair
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["PromptProfile", "load_prompt_profile", "slugify", "prompts_root"]

_DEFAULT_ROOT = Path(__file__).resolve().parent.parent.parent / "config" / "conversion_prompts"

# Overridable at runtime (e.g. by tests, or by an env-specific deployment
# that keeps prompts in a different directory) without touching code.
_root_override: Path | None = None


def prompts_root() -> Path:
    return _root_override or _DEFAULT_ROOT


def set_prompts_root(path: Path | None) -> None:
    """Override the prompts directory (primarily for tests)."""
    global _root_override
    _root_override = path


def slugify(name: str) -> str:
    """Framework display name -> filename slug (e.g. 'Robot Framework' -> 'robot_framework')."""
    return name.strip().lower().replace(" ", "_").replace("/", "_")


@dataclass
class PromptProfile:
    base_system_prompt: str
    syntax_rules: str          # rules for the TARGET framework (may be empty)
    pair_rules: str            # rules specific to (from, to) (may be empty)
    sources: list[str]         # file paths actually read, for diagnostics/UI display
    reference_docs: str = ""   # team's uploaded architecture/design docs (may be empty)

    def render_system_prompt(self, from_name: str, to_name: str, *, extra: str = "") -> str:
        """Assemble the final system prompt string sent to the LLM."""
        base = self.base_system_prompt.format(from_name=from_name, to_name=to_name)
        parts = [base]
        if self.syntax_rules:
            parts.append(self.syntax_rules)
        if self.pair_rules:
            parts.append(self.pair_rules)
        if self.reference_docs:
            parts.append("## Team Reference Documents\n\n" + self.reference_docs)
        if extra:
            parts.append(extra)
        return "\n\n".join(p.strip() for p in parts if p.strip())


def _read_if_exists(path: Path) -> str:
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("conversion prompts: failed to read %s: %s", path, exc)
    return ""


def load_prompt_profile(
    from_name: str, to_name: str, team_id: str = "default",
) -> PromptProfile:
    """Load the full prompt profile for converting from_name -> to_name.

    Missing files degrade gracefully to an empty string for that section —
    a brand-new framework pair with no pair_rules file yet still gets the
    base prompt + target syntax rules, so conversion isn't blocked while a
    team iterates on wording.

    `team_id` selects a per-team profile (see src/team_profiles.py) so
    different teams sharing one deployment can have different conversion
    rules. "default" (the default) preserves the original behaviour
    exactly — same directory, same `set_prompts_root()` test override —
    so existing callers that never pass `team_id` are unaffected.
    """
    if team_id and team_id != "default":
        from src.team_profiles import conversion_prompts_dir
        root = conversion_prompts_dir(team_id)
    else:
        root = prompts_root()
    sources: list[str] = []

    base_path = root / "base_system_prompt.md"
    base = _read_if_exists(base_path)
    if base:
        sources.append(str(base_path))
    else:
        logger.warning(
            "conversion prompts: base_system_prompt.md not found at %s — "
            "conversion will proceed with a minimal fallback prompt.",
            base_path,
        )
        base = (
            "You are an expert test automation engineer. Convert the given "
            "test code from {from_name} to {to_name}. Output ONLY valid, "
            "runnable {to_name} code. Preserve every assertion. Do not "
            "output CI/CD pipeline configuration of any kind."
        )

    to_slug = slugify(to_name)
    syntax_path = root / "syntax_rules" / f"{to_slug}.md"
    syntax_rules = _read_if_exists(syntax_path)
    if syntax_rules:
        sources.append(str(syntax_path))

    from_slug = slugify(from_name)
    pair_path = root / "pair_rules" / f"{from_slug}__to__{to_slug}.md"
    pair_rules = _read_if_exists(pair_path)
    if pair_rules:
        sources.append(str(pair_path))

    reference_docs = ""
    if team_id and team_id != "default":
        from src.team_profiles import reference_docs_context
        reference_docs = reference_docs_context(team_id)
        if reference_docs:
            sources.append(f"<{team_id}'s reference documents>")

    return PromptProfile(
        base_system_prompt=base,
        syntax_rules=syntax_rules,
        pair_rules=pair_rules,
        sources=sources,
        reference_docs=reference_docs,
    )
