"""Pydantic models for framework knowledge base entries."""

import logging
from typing import Any, get_origin

from pydantic import BaseModel, model_validator

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
    "unit_testing": {
        "label": "Unit & Integration Testing",
        "icon": "🧪",
        "description": "Unit test runners and integration testing frameworks",
        "architecture_keys": ["unit_testing"],
        "topic_keywords": ["unit-testing", "unit", "test-runner", "test-framework", "integration-testing", "pytest", "junit", "jest", "mocha", "vitest", "nunit", "xunit", "rspec"],
    },
    "bdd_testing": {
        "label": "BDD & Acceptance Testing",
        "icon": "📋",
        "description": "Behaviour-driven development and acceptance testing frameworks",
        "architecture_keys": ["bdd"],
        "topic_keywords": ["bdd", "gherkin", "cucumber", "behave", "specflow", "acceptance-testing"],
    },
    "security_testing": {
        "label": "Security Testing",
        "icon": "🔒",
        "description": "Security scanning, penetration testing, and vulnerability detection",
        "architecture_keys": ["security_testing"],
        "topic_keywords": ["security", "penetration-testing", "vulnerability", "dast", "sast", "owasp", "web-security"],
    },
    "visual_testing": {
        "label": "Visual & Accessibility Testing",
        "icon": "👁️",
        "description": "Visual regression, component testing, and accessibility validation",
        "architecture_keys": ["visual_testing", "accessibility_testing"],
        "topic_keywords": ["visual-testing", "visual", "regression", "accessibility", "a11y", "wcag", "component-testing", "storybook"],
    },
    "chaos_engineering": {
        "label": "Chaos Engineering",
        "icon": "💥",
        "description": "Resilience testing and chaos engineering tools",
        "architecture_keys": ["chaos_engineering", "resilience_testing"],
        "topic_keywords": ["chaos", "chaos-engineering", "resilience", "fault-injection", "chaos-monkey"],
    },
    "contract_testing": {
        "label": "Contract Testing",
        "icon": "📝",
        "description": "Consumer-driven contract testing for microservices",
        "architecture_keys": ["contract_testing"],
        "topic_keywords": ["contract-testing", "consumer-driven", "pact", "openapi", "api-blueprint"],
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
#
# This is the SINGLE SOURCE OF TRUTH for completeness — both
# `FrameworkData._get_completeness_issues` (checked after a profile is
# already loaded) and `FrameworkData.validate_for_addition` (checked on a
# raw dict before a new profile is written) call `_completeness_issues()`
# below instead of each hardcoding their own copy of this list. Add a new
# required field here once and both call sites pick it up automatically.
REQUIRED_POPULATED_FIELDS = {
    "limitations": "list",          # Must have at least 1 entry
    "performance": "dict",          # Must have at least avg_test_execution_speed
    "maintainability": "dict",      # Must have at least page_object_support
    "cloud_grids": "dict",          # Must have at least 1 entry (or "not_applicable")
    "versions": "list",             # Must have at least 1 version entry
}

# Minimum required keys within specific dict fields (checked only for the
# fields listed here — a field in REQUIRED_POPULATED_FIELDS with no entry
# here is only checked for non-emptiness, not for specific sub-keys).
REQUIRED_SUBKEYS = {
    "performance": ["avg_test_execution_speed", "resource_footprint"],
    "maintainability": ["page_object_support", "reusable_components", "built_in_reporting"],
}
# Back-compat aliases (previously separate constants of the same data).
REQUIRED_PERFORMANCE_KEYS = REQUIRED_SUBKEYS["performance"]
REQUIRED_MAINTAINABILITY_KEYS = REQUIRED_SUBKEYS["maintainability"]


def _completeness_issues(source: "FrameworkData | dict[str, Any]") -> list[str]:
    """Shared completeness check, used by both `_get_completeness_issues`
    (instance, post-construction) and `validate_for_addition` (raw dict,
    pre-construction) — accepts either so there is exactly one place that
    knows which fields are required and which sub-keys they need.
    """
    def _get(field: str) -> Any:
        if isinstance(source, dict):
            return source.get(field)
        return getattr(source, field, None)

    issues: list[str] = []
    for field, kind in REQUIRED_POPULATED_FIELDS.items():
        value = _get(field)
        if not value:
            issues.append(f"{field} is empty (expected a populated {kind})")
            continue
        required_subkeys = REQUIRED_SUBKEYS.get(field)
        if required_subkeys and isinstance(value, dict):
            missing = [k for k in required_subkeys if k not in value]
            if missing:
                issues.append(f"{field} missing keys: {missing}")
    return issues


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
    conversion_notes: str = ""              # framework-specific guidance for code conversion

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
        """Return list of completeness issues for this framework.

        Delegates to the module-level `_completeness_issues()` — see
        REQUIRED_POPULATED_FIELDS for the single source of truth on what
        "complete" means. `validate_for_addition` below checks the same
        thing on a raw dict before construction.
        """
        return _completeness_issues(self)

    def is_complete(self) -> bool:
        """Check whether this framework profile has all critical fields populated."""
        return len(self._get_completeness_issues()) == 0

    @classmethod
    def validate_for_addition(cls, data: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate a framework profile dict is complete enough to be added.

        This is a stricter check used when *adding* new frameworks (vs loading
        existing ones): it additionally requires the bare-minimum fields a
        FrameworkData instance can't even be constructed without, on top of
        the same completeness checks `_get_completeness_issues` runs after
        construction. Returns (is_valid, list_of_issues).
        """
        issues: list[str] = []

        # Check required top-level fields exist and are non-empty — these
        # have no default in the schema, so a missing value here would
        # fail Pydantic construction entirely, not just completeness.
        required_fields = [
            "framework_name", "vendor", "license", "languages_supported",
            "architecture_fit", "capabilities", "cicd_integration",
        ]
        for field in required_fields:
            if field not in data or not data[field]:
                issues.append(f"Required field '{field}' is missing or empty")

        # Everything else is the same "is this profile rich enough to be
        # useful" check as an already-loaded framework — single shared
        # implementation, see REQUIRED_POPULATED_FIELDS.
        issues.extend(_completeness_issues(data))

        return (len(issues) == 0, issues)

    @classmethod
    def empty_value_for_field(cls, key: str) -> Any:
        """Type-safe placeholder for a field missing from a raw profile dict.

        Dict/list-typed fields get an empty container — NEVER a bare
        string. Writing the string "Not Applicable" into a dict/list field
        (e.g. architecture_fit, capabilities, versions) produces YAML that
        fails this model's validation on the next `KnowledgeBase.load()`,
        which handles that by silently dropping the whole framework. Only
        genuinely str-typed fields get the human-readable "Not Applicable"
        placeholder; emptiness in dict/list fields is already surfaced via
        the completeness checks above instead of needing a fake value.
        """
        field = cls.model_fields.get(key)
        if field is not None:
            origin = get_origin(field.annotation) or field.annotation
            if origin is dict:
                return {}
            if origin is list:
                return []
        return "Not Applicable"

