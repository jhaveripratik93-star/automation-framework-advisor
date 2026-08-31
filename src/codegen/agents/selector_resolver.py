"""Selector Resolver Agent — resolves element names to CSS/XPath selectors."""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from src.codegen.agent_config import (
    PIPELINE_SETTINGS,
    SELECTOR_RESOLVER_SYSTEM,
    SELECTOR_RESOLVER_USER_TEMPLATE,
)

if TYPE_CHECKING:
    from src.codegen.pipeline import CodeGenState

logger = logging.getLogger(__name__)

# Element reference keywords — used to extract element names from step text
_ELEMENT_KEYWORDS = (
    "field", "input", "button", "link", "tab", "icon", "element",
    "menu", "option", "dropdown", "select", "checkbox", "radio",
    "textarea", "text box", "textbox", "label", "header", "modal",
    "dialog", "popup", "toast", "alert", "notification", "banner",
    "table", "row", "column", "cell", "list", "item", "card", "navigate"
)


def run_selector_resolver(state: CodeGenState, llm_client: Any) -> dict:
    """LangGraph node: resolve all element names in the test case to selectors."""
    if not PIPELINE_SETTINGS.get("selector_resolution", True):
        logger.info("SelectorResolver: skipped (disabled in PIPELINE_SETTINGS)")
        return {"resolved_selectors": state.get("selector_map", {})}

    # User-provided selectors always take priority
    known = {k.lower(): v for k, v in state.get("selector_map", {}).items()}
    elements = _extract_element_names(state)

    # Skip elements already covered by user map (exact or substring match)
    known_keys = set(known.keys())
    unresolved = [
        e for e in elements
        if e not in known_keys and not any(e in k or k in e for k in known_keys)
    ]

    if not unresolved:
        logger.info(
            "SelectorResolver: all %d elements covered by user map (%d provided)",
            len(elements), len(known),
        )
        return {"resolved_selectors": known}

    framework = state.get("framework", "playwright_ts")
    scenario = state.get("scenario", {})
    app_type = scenario.get("test_type", "e2e")

    prompt = SELECTOR_RESOLVER_USER_TEMPLATE.format(
        framework=framework,
        app_type=app_type,
        elements="\n".join(f"  - {e}" for e in unresolved),
        known_selectors="\n".join(f"  {k} → {v}" for k, v in known.items()) or "  (none)",
    )

    resolved: dict[str, str] = {}
    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            system=SELECTOR_RESOLVER_SYSTEM,
        )
        raw = result.get("content", "{}")
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            resolved = json.loads(match.group())
        logger.info("SelectorResolver: resolved %d/%d elements via LLM", len(resolved), len(unresolved))
    except Exception as exc:
        logger.warning("SelectorResolver: LLM call failed (%s) — using data-testid fallback", exc)
        resolved = {e: f"[data-testid='{_slugify(e)}']" for e in unresolved}

    # User-provided selectors always win over LLM-resolved ones
    merged = {**resolved, **known}
    logger.info("SelectorResolver: final map has %d selectors (%d user, %d llm)",
                len(merged), len(known), len(resolved))
    return {"resolved_selectors": merged}


def _extract_element_names(state: CodeGenState) -> list[str]:
    """Extract element names from step actions using heuristics."""
    elements: list[str] = []
    steps = state["test_case"].get("steps", [])

    for step in steps:
        action = step.get("action", "")
        # Extract quoted strings (explicit element names)
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", action)
        elements.extend(quoted)

        # Extract "the X field/button/link" patterns
        for kw in _ELEMENT_KEYWORDS:
            pattern = rf"(?:the\s+)?(['\"]?[\w\s\-]+['\"]?)\s+{kw}"
            matches = re.findall(pattern, action, re.IGNORECASE)
            elements.extend(m.strip().strip("'\"") for m in matches if m.strip())

    # Deduplicate, lowercase, filter noise
    seen: set[str] = set()
    result: list[str] = []
    for e in elements:
        e_clean = e.strip().lower()
        if e_clean and len(e_clean) > 1 and e_clean not in seen:
            seen.add(e_clean)
            result.append(e_clean)
    return result


def _slugify(text: str) -> str:
    slug = text.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug or "element"
