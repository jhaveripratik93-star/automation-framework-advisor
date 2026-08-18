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