"""Coverage Gap Analysis engine."""

from dataclasses import dataclass, field
from src.models import UserProfile
from src.knowledge_base.schema import FrameworkData


@dataclass
class CoverageGap:
    """A single identified coverage gap."""

    category: str
    description: str
    reason: str
    mitigation: str
    severity: str = "medium"  # low, medium, high


@dataclass
class CoverageReport:
    """Complete coverage analysis report."""

    legacy_total_tests: int
    migrated_tests: int
    coverage_parity_percentage: float
    gaps: list[CoverageGap] = field(default_factory=list)
    new_coverage_opportunities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "coverage_analysis": {
                "legacy_total_tests": self.legacy_total_tests,
                "migrated_tests": self.migrated_tests,
                "coverage_parity_percentage": self.coverage_parity_percentage,
                "gaps": [
                    {
                        "category": g.category,
                        "description": g.description,
                        "reason": g.reason,
                        "mitigation": g.mitigation,
                        "severity": g.severity,
                    }
                    for g in self.gaps
                ],
                "new_coverage_opportunities": self.new_coverage_opportunities,
            }
        }


class CoverageAnalyzer:
    """Analyzes coverage gaps between legacy and target frameworks."""

    # Known limitation patterns per framework type
    COMMON_GAPS = {
        "load_testing": CoverageGap(
            category="Performance",
            description="Load/stress testing scripts",
            reason="UI/functional frameworks are not designed for load testing",
            mitigation="Retain dedicated load testing tool (K6/Locust) alongside",
            severity="medium",
        ),
        "soap_services": CoverageGap(
            category="Integration",
            description="Legacy SOAP/XML service tests",
            reason="Modern frameworks focus on REST/GraphQL",
            mitigation="Keep as standalone pytest + zeep/suds tests",
            severity="low",
        ),
        "desktop_automation": CoverageGap(
            category="UI",
            description="Desktop application interaction tests",
            reason="Web frameworks cannot automate native desktop apps",
            mitigation="Use dedicated desktop automation (WinAppDriver/PyAutoGUI)",
            severity="high",
        ),
        "canvas_webgl": CoverageGap(
            category="UI",
            description="Canvas/WebGL visual validation tests",
            reason="Limited pixel-level canvas interaction in most frameworks",
            mitigation="Use visual comparison tool (Percy/Applitools) for canvas",
            severity="medium",
        ),
        "native_dialogs": CoverageGap(
            category="UI",
            description="OS-level native dialog interactions",
            reason="Browser automation cannot interact with OS dialogs directly",
            mitigation="Use file chooser API or download event listeners",
            severity="low",
        ),
    }

    def analyze(
        self, profile: UserProfile, target_fw: FrameworkData
    ) -> CoverageReport:
        """Perform coverage gap analysis."""
        gaps = self._identify_gaps(profile, target_fw)
        opportunities = self._identify_opportunities(target_fw)

        # Calculate estimated coverage
        gap_impact = len(gaps) * 0.5  # Each gap ~ 0.5% coverage loss
        parity = max(90.0, 100.0 - gap_impact)

        migrated = int(profile.legacy_test_count * (parity / 100))

        return CoverageReport(
            legacy_total_tests=profile.legacy_test_count,
            migrated_tests=migrated,
            coverage_parity_percentage=round(parity, 1),
            gaps=gaps,
            new_coverage_opportunities=opportunities,
        )

    def _identify_gaps(
        self, profile: UserProfile, target_fw: FrameworkData
    ) -> list[CoverageGap]:
        """Identify coverage gaps based on limitations."""
        gaps = []

        # Check framework limitations against requirements
        limitations = [l.lower() for l in target_fw.limitations]

        # Load testing gap
        if "performance" in str(profile.must_support).lower():
            if any("load" in lim or "performance" in lim for lim in limitations):
                gaps.append(self.COMMON_GAPS["load_testing"])

        # Canvas/WebGL gap
        if any("canvas" in ui.lower() or "webgl" in ui.lower() for ui in profile.special_ui):
            canvas_cap = target_fw.capabilities.get("canvas_webgl", "")
            if canvas_cap != True:
                gaps.append(self.COMMON_GAPS["canvas_webgl"])

        # Mobile gap for web-only frameworks
        if any(
            "mobile" in ms.lower() for ms in profile.must_support
        ):
            if target_fw.architecture_fit.get("native_mobile") is False:
                gaps.append(CoverageGap(
                    category="Mobile",
                    description="Native mobile app testing",
                    reason=f"{target_fw.framework_name} does not support native mobile",
                    mitigation="Use Appium alongside for mobile-specific tests",
                    severity="high",
                ))

        # Check for native dialog limitations
        if any("native" in lim or "os-level" in lim for lim in limitations):
            gaps.append(self.COMMON_GAPS["native_dialogs"])

        return gaps

    def _identify_opportunities(self, target_fw: FrameworkData) -> list[str]:
        """Identify new testing capabilities gained by migration."""
        opportunities = []

        if target_fw.capabilities.get("accessibility_testing"):
            opportunities.append(
                "Accessibility testing via built-in/plugin support"
            )

        if target_fw.capabilities.get("network_interception") is True:
            opportunities.append(
                "Network mocking for offline/degraded-mode scenarios"
            )

        if target_fw.capabilities.get("visual_regression"):
            opportunities.append("Visual regression testing capability")

        parallel = target_fw.capabilities.get("parallel_execution", "")
        if parallel == "native" or parallel is True:
            opportunities.append(
                "Native parallel execution (faster feedback loops)"
            )

        if target_fw.capabilities.get("component_testing") is True:
            opportunities.append("Component-level testing support")

        if target_fw.maintainability.get("trace_viewer") is True:
            opportunities.append("Trace viewer for debugging test failures")

        return opportunities
