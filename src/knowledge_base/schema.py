"""Pydantic models for framework knowledge base entries."""

import logging
from pydantic import BaseModel, model_validator
from typing import Any

logger = logging.getLogger(__name__)

# Fields that MUST be populated (non-empty) for a framework to be considered complete.
# These are critical for scoring, recommendations, and migration planning.
REQUIRED_POPULATED_FIELDS = {
    "limitations": "list",          # Must have at least 1 entry
    "performance": "dict",          # Must have at least avg_test_execution_speed
    "maintainability": "dict",      # Must have at least page_object_support
    "cloud_grids": "dict",          # Must have at least 1 entry (or "not_applicable")
}

# Minimum required keys within dict fields
REQUIRED_PERFORMANCE_KEYS = ["avg_test_execution_speed", "resource_footprint"]
REQUIRED_MAINTAINABILITY_KEYS = ["page_object_support", "reusable_components", "built_in_reporting"]


class FrameworkData(BaseModel):
    """Schema for a single framework entry in the knowledge base."""

    framework_name: str
    vendor: str
    license: str
    website: str = ""
    first_release: str = ""
    latest_version: str = ""
    category: str = "test_automation"  # test_automation | cloud_infrastructure
    languages_supported: list[str]
    architecture_fit: dict[str, Any]
    capabilities: dict[str, Any]
    cicd_integration: dict[str, Any]
    cloud_grids: dict[str, Any] = {}
    cloud_providers: dict[str, Any] = {}  # Cloud provider support matrix
    testing_capabilities: dict[str, Any] = {}  # Testing features for IaC
    performance: dict[str, Any] = {}
    maintainability: dict[str, Any] = {}
    cloud_migration_metrics: dict[str, Any] = {}  # Cloud migration KPIs
    limitations: list[str] = []
    # Runtime / CI metadata — used by executor and CI/CD builders
    pip_packages: dict[str, str] = {}       # {"pytest": ">=7.0", ...}
    install_commands: list[str] = []        # extra install steps (e.g. playwright install)
    test_command: str = ""                  # default CLI test command
    report_paths: list[str] = []            # artifact paths for CI upload
    versions: list[dict[str, Any]] = []     # version history with breaking changes

    @model_validator(mode="after")
    def warn_incomplete_fields(self) -> "FrameworkData":
        """Log warnings for frameworks with empty critical fields.

        This does NOT block loading (so existing data still works), but ensures
        visibility into incomplete profiles that need attention.
        """
        issues = self._get_completeness_issues()
        if issues:
            logger.warning(
                "Framework '%s' has incomplete data: %s",
                self.framework_name,
                "; ".join(issues),
            )
        return self

    def _get_completeness_issues(self) -> list[str]:
        """Return list of completeness issues for this framework."""
        issues: list[str] = []

        if not self.limitations:
            issues.append("limitations is empty (should list known constraints)")

        if not self.performance:
            issues.append("performance is empty (should include speed, footprint)")
        else:
            missing_perf = [
                k for k in REQUIRED_PERFORMANCE_KEYS
                if k not in self.performance
            ]
            if missing_perf:
                issues.append(f"performance missing keys: {missing_perf}")

        if not self.maintainability:
            issues.append("maintainability is empty (should include tooling info)")
        else:
            missing_maint = [
                k for k in REQUIRED_MAINTAINABILITY_KEYS
                if k not in self.maintainability
            ]
            if missing_maint:
                issues.append(f"maintainability missing keys: {missing_maint}")

        if not self.cloud_grids:
            issues.append("cloud_grids is empty (use 'not_applicable: true' if N/A)")

        if not self.versions:
            issues.append("versions is empty (should include at least 1 version entry)")

        return issues

    def is_complete(self) -> bool:
        """Check whether this framework profile has all critical fields populated."""
        return len(self._get_completeness_issues()) == 0

    @classmethod
    def validate_for_addition(cls, data: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate a framework profile dict is complete enough to be added.

        This is a stricter check used when *adding* new frameworks (vs loading
        existing ones). Returns (is_valid, list_of_issues).
        """
        issues: list[str] = []

        # Check required top-level fields exist and are non-empty
        required_fields = [
            "framework_name", "vendor", "license", "languages_supported",
            "architecture_fit", "capabilities", "cicd_integration",
        ]
        for field in required_fields:
            if field not in data or not data[field]:
                issues.append(f"Required field '{field}' is missing or empty")

        # Check critical data fields are populated (not just present)
        if not data.get("limitations"):
            issues.append(
                "limitations must not be empty - list at least 3 known constraints"
            )

        perf = data.get("performance", {})
        if not isinstance(perf, dict) or not perf:
            issues.append(
                "performance must be populated with at least: "
                "avg_test_execution_speed, resource_footprint"
            )
        elif isinstance(perf, dict):
            missing = [k for k in REQUIRED_PERFORMANCE_KEYS if k not in perf]
            if missing:
                issues.append(f"performance missing required keys: {missing}")

        maint = data.get("maintainability", {})
        if not isinstance(maint, dict) or not maint:
            issues.append(
                "maintainability must be populated with at least: "
                "page_object_support, reusable_components, built_in_reporting"
            )
        elif isinstance(maint, dict):
            missing = [k for k in REQUIRED_MAINTAINABILITY_KEYS if k not in maint]
            if missing:
                issues.append(f"maintainability missing required keys: {missing}")

        grids = data.get("cloud_grids", {})
        if not isinstance(grids, dict) or not grids:
            issues.append(
                "cloud_grids must be populated (use {'not_applicable': true} "
                "for frameworks that don't use cloud grids)"
            )

        versions = data.get("versions", [])
        if not isinstance(versions, list) or not versions:
            issues.append(
                "versions must include at least 1 version entry with "
                "version, release_date, notable_features, and breaking_changes"
            )

        return (len(issues) == 0, issues)

