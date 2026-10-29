"""Formatting helpers for tool output rendering."""
from __future__ import annotations

from typing import Any


def positive_items(field: dict[str, Any]) -> list[tuple[str, Any]]:
    """Check if dict entry is meaningfully on (True or non-empty string)."""
    if not field:
        return []
    result = []
    for k, v in field.items():
        if v is True:
            result.append((k, v))
        elif isinstance(v, str) and v.strip().lower() not in ("", "false", "no"):
            result.append((k, v))
    return result


def active_capabilities(capabilities: dict[str, Any], limit: int | None = None) -> list[str]:
    """Return title-cased labels for capabilities that are "on" (True or a
    meaningful non-empty string), e.g. {"api_testing": True} -> ["Api Testing"].

    Shared by every advisor-tool handler that lists a framework's active
    capabilities (search, details, comparison) — previously the same
    list-comprehension was copy-pasted in each one.
    """
    labels = [k.replace("_", " ").title() for k, v in positive_items(capabilities)]
    return labels[:limit] if limit is not None else labels


def format_architecture_fit(architecture_fit: dict[str, Any]) -> str:
    """Render architecture_fit with string fit-levels."""
    items = positive_items(architecture_fit)
    if not items:
        return "None"
    parts = []
    for k, v in items:
        label = k.replace("_", " ").title()
        parts.append(label if v is True else f"{label} ({v})")
    return ", ".join(parts)


def format_cicd(cicd_integration: dict[str, Any]) -> str:
    """Format CI/CD integration info."""
    items = positive_items(cicd_integration)
    if not items:
        return ""
    parts = [
        k.replace("_", " ").title() if v is True else f"{k.replace('_', ' ').title()} ({v})"
        for k, v in items
    ]
    return ", ".join(parts)


def format_cloud_grids(cloud_grids: dict[str, Any]) -> str:
    """Format cloud grids info."""
    items = positive_items(cloud_grids)
    if not items:
        return ""
    parts = [
        k.replace("_", " ").title() if v is True else f"{k.replace('_', ' ').title()} ({v})"
        for k, v in items
    ]
    return ", ".join(parts)


def format_performance(performance: dict[str, Any]) -> str:
    """Format performance metrics into readable line."""
    if not performance:
        return ""
    parts = []
    speed = performance.get("avg_test_execution_speed")
    if speed:
        parts.append(f"{str(speed).title()} execution")
    footprint = performance.get("resource_footprint")
    if footprint:
        parts.append(f"{str(footprint).title()} resource footprint")
    startup = performance.get("startup_time_ms")
    if startup:
        parts.append(f"~{startup}ms startup")
    return ", ".join(parts)


def format_maintainability(maintainability: dict[str, Any]) -> str:
    """Format maintainability features."""
    items = positive_items(maintainability)
    if not items:
        return ""
    lines = []
    for k, v in items:
        label = k.replace("_", " ").title()
        lines.append(f"  - {label}" if v is True else f"  - {label}: {v}")
    return "\n".join(lines)


def format_version_info(fw: Any) -> str:
    """Format vendor and version info."""
    parts = []
    if getattr(fw, "vendor", ""):
        parts.append(fw.vendor)
    if getattr(fw, "first_release", "") or getattr(fw, "latest_version", ""):
        parts.append(f"{fw.first_release or '?'}–{fw.latest_version or '?'}")
    return ", ".join(parts) if parts else "N/A"


def format_limitations(field: list[str]) -> str:
    """Format limitations list as bullet lines."""
    if not field:
        return ""
    return "\n".join(f"  - {lim}" for lim in field)


def find_case_study_relevance(case_study: str, subject_terms: list[str], max_excerpts: int = 3) -> str:
    """Scan an uploaded case study for paragraphs mentioning any subject term.

    Returns "" when no case study is loaded or nothing relevant is found,
    so callers can safely do: `if note: lines.append(note)`.

    Shared by every feature executor (advisor_tools, coverage, prerequisites,
    migration) that renders case-study-aware output — single source of
    truth so a case-study loaded via the Streamlit sidebar surfaces
    identically regardless of which tool is answering.
    """
    if not case_study or not subject_terms:
        return ""

    terms = [t.lower() for t in subject_terms if t]
    paragraphs = [p.strip() for p in case_study.split("\n\n") if p.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [line.strip() for line in case_study.split("\n") if line.strip()]

    matches = []
    for para in paragraphs:
        if any(term in para.lower() for term in terms):
            matches.append(para)
        if len(matches) >= max_excerpts:
            break

    if not matches:
        return ""

    lines = ["\n**📄 From Your Case Study:**"]
    for m in matches:
        snippet = m if len(m) <= 300 else m[:300].rstrip() + "…"
        lines.append(f"> {snippet}")
    return "\n".join(lines)


def resolve_framework(kb: Any, name: str) -> Any | None:
    """Resolve a framework name with fuzzy matching (handles 'Selenium' → 'Selenium WebDriver').

    Args:
        kb: KnowledgeBase instance.
        name: Framework name to look up (case-insensitive, partial match OK).

    Returns:
        Matching FrameworkData, or None if not found.
    """
    # Exact match first
    fw = kb.get(name.lower())
    if fw:
        return fw
    # Partial match: input is a substring of a framework name
    name_lower = name.lower()
    for candidate in kb.list_all():
        if name_lower in candidate.framework_name.lower():
            return candidate
    # Reverse: framework name is a substring of the input
    for candidate in kb.list_all():
        if candidate.framework_name.lower() in name_lower:
            return candidate
    return None