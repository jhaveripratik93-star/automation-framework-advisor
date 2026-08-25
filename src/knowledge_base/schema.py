"""Pydantic models for framework knowledge base entries."""

import logging
from pydantic import BaseModel, model_validator
from typing import Any

logger = logging.getLogger(__name__)

# ── Framework Categories ──────────────────────────────────────────────
# Categories used to group frameworks by their primary purpose.
# A framework can belong to multiple categories.

FRAMEWORK_CATEGORIES = {
    "web_ui_testing": {
        "label": "Web UI Testing",
        "icon": "🌐",
        "description": "Browser-based end-to-end and UI testing frameworks",
        "architecture_keys": ["web_spa", "web_mpa"],
        "topic_keywords": ["browser", "e2e", "ui", "web", "selenium", "playwright", "cypress"],
    },
    "api_testing": {
        "label": "API Testing",
        "icon": "🔌",
        "description": "REST, GraphQL, and microservice testing frameworks",
        "architecture_keys": ["api_testing", "microservices"],
        "topic_keywords": ["api", "rest", "http", "graphql", "contract", "soap"],
    },
    "mobile_testing": {
        "label": "Mobile Testing",
        "icon": "📱",
        "description": "Native and hybrid mobile app testing frameworks",
        "architecture_keys": ["native_mobile", "hybrid_mobile"],
        "topic_keywords": ["mobile", "ios", "android", "react-native", "appium", "detox"],
    },
    "performance_testing": {
        "label": "Performance & Load Testing",
        "icon": "⚡",
        "description": "Load, stress, and performance testing frameworks",
        "architecture_keys": ["performance_testing", "load_testing"],
        "topic_keywords": ["load", "performance", "stress", "benchmark", "k6", "locust", "jmeter"],
    },
    "infrastructure_as_code": {
        "label": "Infrastructure as Code",
        "icon": "☁️",
        "description": "Cloud provisioning, configuration management, and IaC tools",
        "architecture_keys": ["cloud_infrastructure", "infrastructure_as_code", "configuration_management"],
        "topic_keywords": ["infrastructure", "iac", "terraform", "ansible", "cloud", "devops", "provisioning"],
    },
    "desktop_testing": {
        "label": "Desktop Testing",
        "icon": "🖥️",
        "description": "Desktop application testing frameworks",
        "architecture_keys": ["desktop"],
        "topic_keywords": ["desktop", "electron", "winappdriver", "gui"],
    },
}


def classify_framework(
    architecture_fit: dict[str, Any] | None = None,
    description: str = "",
    topics: list[str] | None = None,
    category: str = "",
) -> list[str]:
    """Classify a framework into one or more categories.

    Uses architecture_fit keys, description keywords, and topic tags to
    determine which categories a framework belongs to.

    Args:
        architecture_fit: Dict of architecture support flags from YAML.
        description: Framework description text.
        topics: List of topic/tag strings (e.g. from GitHub).
        category: The binary category field ("test_automation" or "cloud_infrastructure").

    Returns:
        List of category IDs (e.g. ["web_ui_testing", "api_testing"]).
    """
    matched: list[str] = []
    desc_lower = description.lower()
    topics_lower = [t.lower() for t in (topics or [])]

    for cat_id, cat_def in FRAMEWORK_CATEGORIES.items():
        # Check architecture_fit keys
        if architecture_fit:
            for arch_key in cat_def["architecture_keys"]:
                value = architecture_fit.get(arch_key, False)
                if value is True or (isinstance(value, str) and value.lower() not in ("false", "", "no")):
                    matched.append(cat_id)
                    break
            else:
                # No arch key matched — try description/topics
                if any(kw in desc_lower for kw in cat_def["topic_keywords"]):
                    matched.append(cat_id)
                elif any(kw in t for t in topics_lower for kw in cat_def["topic_keywords"]):
                    matched.append(cat_id)
        else:
            # No architecture_fit — rely on description and topics
            if any(kw in desc_lower for kw in cat_def["topic_keywords"]):
                matched.append(cat_id)
            elif any(kw in t for t in topics_lower for kw in cat_def["topic_keywords"]):
                matched.append(cat_id)

    # Fallback: use the binary category field
    if not matched:
        if category == "cloud_infrastructure":
            matched.append("infrastructure_as_code")
        else:
            matched.append("web_ui_testing")  # Default for test_automation

    return matched


def classify_framework_data(fw_data) -> list[str]:
    """Classify a loaded FrameworkData object into categories."""
    return classify_framework(
        architecture_fit=fw_data.architecture_fit,
        description="",
        topics=[],
        category=fw_data.category,
    )


# ── Completeness validation constants ─────────────────────────────────

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

