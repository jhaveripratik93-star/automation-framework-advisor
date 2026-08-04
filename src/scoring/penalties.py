"""Penalty and bonus calculation logic."""

from dataclasses import dataclass

from src.models import UserProfile, BudgetPreference
from src.knowledge_base.schema import FrameworkData


@dataclass
class Adjustment:
    """A score adjustment (penalty or bonus)."""

    description: str
    points: int
    reason: str


def calculate_penalties(
    profile: UserProfile, fw: FrameworkData
) -> list[Adjustment]:
    """Calculate penalties for hard blockers."""
    penalties = []

    # Primary language not supported
    primary = profile.primary_language.lower()
    fw_langs = [lang.lower() for lang in fw.languages_supported]
    lang_match = primary in fw_langs or any(primary in l for l in fw_langs)

    if not lang_match:
        penalties.append(Adjustment(
            description=f"No {profile.primary_language} support",
            points=30,
            reason="Team's primary language not available in framework",
        ))

    # Special UI requirements not met
    ui_capability_map = {
        "shadow dom": "shadow_dom",
        "iframe": "iframe_cross_origin",
        "iframes": "iframe_cross_origin",
        "multi-tab": "multi_tab",
        "multi-domain": "multi_domain",
        "canvas": "canvas_webgl",
        "webgl": "canvas_webgl",
    }
    for ui_req in profile.special_ui:
        req_lower = ui_req.lower()
        for keyword, cap_key in ui_capability_map.items():
            if keyword in req_lower:
                cap_val = fw.capabilities.get(cap_key, False)
                if cap_val is False:
                    penalties.append(Adjustment(
                        description=f"No {ui_req} support",
                        points=20,
                        reason=f"Required UI capability '{ui_req}' not available",
                    ))
                break

    # Docker required but not supported
    if profile.containerized:
        if not fw.cicd_integration.get("docker_support"):
            penalties.append(Adjustment(
                description="No Docker support",
                points=25,
                reason="Containerization required but framework lacks Docker support",
            ))

    # Parallel execution required but missing
    if profile.parallel_required:
        parallel = fw.capabilities.get("parallel_execution", "")
        if not parallel or parallel is False:
            penalties.append(Adjustment(
                description="No parallel execution",
                points=15,
                reason="Parallel execution required but not supported natively",
            ))

    # Budget constraint: paid-only parallelization
    if profile.budget in (BudgetPreference.STRICT_OSS, BudgetPreference.OSS_PREFERRED):
        parallel = str(fw.capabilities.get("parallel_execution", ""))
        if "paid" in parallel.lower() or "cloud" in parallel.lower():
            penalties.append(Adjustment(
                description="Paid parallelization conflicts with budget",
                points=10,
                reason="Parallel execution requires paid service",
            ))

    return penalties


def calculate_bonuses(
    profile: UserProfile, fw: FrameworkData
) -> list[Adjustment]:
    """Calculate bonuses for exceptional fit."""
    bonuses = []

    # Current framework match (no migration needed)
    if profile.current_framework:
        current = profile.current_framework.lower()
        fw_name = fw.framework_name.lower()
        if current in fw_name or fw_name in current:
            bonuses.append(Adjustment(
                description="Current tool match (no migration)",
                points=10,
                reason="Framework matches current tooling",
            ))

    # Combined UI + API capability (reduces tool sprawl)
    has_ui = fw.architecture_fit.get("web_spa") is True
    has_api = fw.architecture_fit.get("api_testing") is True
    if has_ui and has_api:
        bonuses.append(Adjustment(
            description="UI + API in single framework",
            points=5,
            reason="Reduces tool sprawl by covering both UI and API testing",
        ))

    # Accessibility testing when requested
    if "accessibility" in [nh.lower() for nh in profile.nice_to_have]:
        acc = fw.capabilities.get("accessibility_testing")
        if acc and acc is not False:
            bonuses.append(Adjustment(
                description="Accessibility testing available",
                points=5,
                reason="Nice-to-have requirement met",
            ))

    # Visual regression when requested
    if "visual regression" in [nh.lower() for nh in profile.nice_to_have]:
        vis = fw.capabilities.get("visual_regression")
        if vis and vis is not False:
            bonuses.append(Adjustment(
                description="Visual regression available",
                points=3,
                reason="Nice-to-have requirement met",
            ))

    return bonuses
