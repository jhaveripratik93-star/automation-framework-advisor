"""Manual-mode brief builder for the LLM-backed test-code generation tool.

GROQ_API_KEY is optional for this server. When it's not configured, the
generate_test_code tool doesn't fail — it switches to "manual mode": it
reuses the exact same per-framework rules it would otherwise send to
Groq, and hands them back as a structured brief. The calling assistant
(Kiro's own model, already in the conversation) then produces the code
directly using that brief — no external LLM API call needed.

Note: the equivalent manual-mode briefs for code CONVERSION
(build_conversion_brief / build_multi_file_conversion_brief) were removed
as part of the conversion-feature rewrite — see src/conversion/ and
mcp_server/sampling.py, which replaced the Groq-or-manual-brief pattern
with true MCP sampling (preferred) + an automatic Groq fallback, so
conversion no longer needs a "write the code yourself" brief at all.
"""
from __future__ import annotations

from typing import Any


def build_codegen_brief(
    target_framework: str,
    test_cases: list[dict[str, Any]],
    selector_map: dict[str, str] | None,
    team_id: str = "default",
) -> dict[str, Any]:
    """Build a manual-mode brief for generating test code from manual test
    cases, when no LLM is configured.

    Reuses the same per-framework context and step generation rules
    (`STEP_GENERATOR_SYSTEM`) that `CodeGenOrchestrator` sends to Groq, so
    manual-mode output follows identical conventions — including a named
    team profile's own style overrides (see src/team_profiles.py) when
    `team_id` isn't "default". This is the most-used path (no API key
    required), so team style has to apply here too, not just when Groq
    is configured.
    """
    from src.codegen.agent_config import STEP_GENERATOR_SYSTEM
    from src.codegen.style_profiles import get_framework_context

    system_prompt = STEP_GENERATOR_SYSTEM.get(
        target_framework, STEP_GENERATOR_SYSTEM.get("playwright_ts", "")
    )
    framework_ctx = get_framework_context(target_framework, team_id=team_id)
    if framework_ctx:
        system_prompt = f"{system_prompt}\n\nFramework reference:\n{framework_ctx}"

    return {
        "mode": "manual",
        "target_framework": target_framework,
        "generation_rules": system_prompt,
        "selector_map": selector_map or {},
        "test_cases": test_cases,
        "instructions": (
            f"GROQ_API_KEY is not configured, so this tool did not call an "
            f"external LLM. Using `generation_rules` as the authoritative "
            f"rule set, generate complete {target_framework} test code for "
            f"every entry in `test_cases` yourself. Use entries in "
            f"`selector_map` for the elements they name; for anything not "
            f"in `selector_map`, invent a reasonable selector and flag it "
            f"to the user for verification. Present the generated file(s) "
            f"to the user (and write them to disk if they have an open "
            f"workspace for it)."
        ),
    }
