"""Individual criterion scoring functions."""

from src.models import UserProfile, CloudProvider
from src.knowledge_base.schema import FrameworkData


def score_language_compatibility(
    profile: UserProfile, fw: FrameworkData
) -> int:
    """Score C1: Language & Ecosystem Compatibility (0-100)."""
    score = 0
    primary = profile.primary_language.lower()
    fw_langs = [lang.lower() for lang in fw.languages_supported]

    # Primary language match
    if primary in fw_langs or any(primary in lang for lang in fw_langs):
        score += 50
    elif any(lang in primary for lang in fw_langs):
        score += 25

    # Secondary language bonus
    for lang in profile.secondary_languages:
        if lang.lower() in fw_langs:
            score += 15
            break

    # Test framework integration (pytest, jest, junit)
    test_fw_map = {
        "python": ["pytest", "unittest"],
        "javascript": ["jest", "mocha"],
        "typescript": ["jest", "mocha"],
        "java": ["junit", "testng"],
    }
    expected = test_fw_map.get(primary, [])
    if expected:
        score += 15  # Simplified: assume integration exists if language matches

    # Package manager availability
    if primary in ("python", "javascript", "typescript", "java"):
        score += 10

    # Community activity proxy
    if len(fw.limitations) < 6:  # Fewer limitations = more mature
        score += 10

    return min(100, score)


def score_api_validation(
    profile: UserProfile, fw: FrameworkData
) -> int:
    """Score C2: API/Backend Validation Support (0-100)."""
    score = 0

    # Native API testing
    api_fit = fw.architecture_fit.get("api_testing", False)
    if api_fit is True:
        score += 30
    elif isinstance(api_fit, str) and api_fit.lower() not in ("false", ""):
        score += 15

    # Network interception (useful for API mocking)
    if fw.capabilities.get("network_interception") is True:
        score += 20

    # Protocol support
    protocols = fw.capabilities.get("protocol_support", [])
    if isinstance(protocols, list):
        if any("http" in p.lower() for p in protocols):
            score += 15
        if any("graphql" in p.lower() for p in protocols):
            score += 10
        if any("grpc" in p.lower() for p in protocols):
            score += 5

    # Schema validation
    if fw.capabilities.get("schema_validation") is True:
        score += 15

    # Fallback: if it's a web framework with request capabilities
    if score < 30 and fw.architecture_fit.get("web_spa") is True:
        score += 10  # Can typically do basic API calls

    return min(100, score)


def score_performance_load(
    profile: UserProfile, fw: FrameworkData
) -> int:
    """Score C3: Performance & Load Testing (0-100)."""
    score = 0

    # Check if this IS a performance framework
    if fw.architecture_fit.get("performance_testing") is True:
        score += 40
    if fw.architecture_fit.get("load_testing") is True:
        score += 20

    # Parallel execution capability
    parallel = fw.capabilities.get("parallel_execution", "")
    if parallel == "native" or parallel is True:
        score += 15
    elif parallel:
        score += 8

    # Execution speed
    speed = fw.performance.get("avg_test_execution_speed", "")
    speed_scores = {"very fast": 15, "fast": 12, "moderate-fast": 8, "moderate": 5}
    score += speed_scores.get(speed, 0)

    # Metrics/monitoring integration
    if fw.maintainability.get("grafana_integration") is True:
        score += 10

    return min(100, score)


def score_cicd_integration(
    profile: UserProfile, fw: FrameworkData
) -> int:
    """Score C4: CI/CD Integration (0-100)."""
    score = 0

    # Docker support
    docker = fw.cicd_integration.get("docker_support", False)
    if docker is True:
        score += 25
    elif fw.cicd_integration.get("pre_built_docker_images"):
        score += 20

    # User's specific CI/CD tool
    ci_tool = profile.ci_cd_tool.lower().replace(" ", "_")
    tool_key_map = {
        "jenkins": "jenkins",
        "github_actions": "github_actions",
        "github actions": "github_actions",
        "gitlab_ci": "gitlab_ci",
        "gitlab ci": "gitlab_ci",
        "azure_devops": "azure_devops",
        "azure devops": "azure_devops",
    }
    key = tool_key_map.get(ci_tool, ci_tool)
    if fw.cicd_integration.get(key) is True:
        score += 25

    # Headless / auto-wait (CI-friendly)
    if fw.capabilities.get("auto_wait") is True:
        score += 10

    # Parallel execution in CI
    parallel = fw.capabilities.get("parallel_execution", "")
    if parallel == "native" or parallel is True:
        score += 15
    elif parallel:
        score += 8

    # Reporting (JUnit XML / built-in)
    if fw.maintainability.get("built_in_reporting") is True:
        score += 15
    elif fw.maintainability.get("built_in_reporting"):
        score += 10
    else:
        score += 5  # Most can output some format

    # Pre-built Docker images
    if fw.cicd_integration.get("pre_built_docker_images") is True:
        score += 10

    return min(100, score)


def score_maintainability(
    profile: UserProfile, fw: FrameworkData
) -> int:
    """Score C5: Maintainability & Reusability (0-100)."""
    score = 0

    # Page Object Model support
    pom = fw.maintainability.get("page_object_support", False)
    if pom is True:
        score += 20
    elif pom:
        score += 10

    # Fixture support
    if fw.maintainability.get("fixture_support") is True:
        score += 15
    elif fw.maintainability.get("fixture_support"):
        score += 10

    # Built-in reporting
    if fw.maintainability.get("built_in_reporting") is True:
        score += 15
    elif fw.maintainability.get("built_in_reporting"):
        score += 10

    # Code generation / recorder
    if fw.capabilities.get("code_generation") is True:
        score += 5
    if fw.capabilities.get("test_recorder") is True:
        score += 5

    # Auto-wait / smart selectors
    if fw.capabilities.get("auto_wait") is True:
        score += 15

    # Debugging tools
    debug = fw.maintainability.get("debugging_tools", "")
    if "excellent" in str(debug).lower():
        score += 15
    elif "good" in str(debug).lower():
        score += 10
    elif debug:
        score += 5

    # Trace viewer
    if fw.maintainability.get("trace_viewer") is True:
        score += 5

    return min(100, score)


def score_cloud_readiness(
    profile: UserProfile, fw: FrameworkData
) -> int:
    """Score C6: Cloud-Native/AWS Readiness (0-100)."""
    score = 0

    # Docker support
    if fw.cicd_integration.get("docker_support") is True:
        score += 30

    # Cloud grid integrations
    grids = fw.cloud_grids or {}
    grid_count = sum(
        1 for v in grids.values()
        if v is True or (isinstance(v, str) and v.lower() not in ("false", ""))
    )
    score += min(25, grid_count * 8)

    # Distributed/Kubernetes execution
    perf = fw.performance or {}
    if perf.get("distributed_execution"):
        score += 20

    # Resource footprint
    footprint = perf.get("resource_footprint", "")
    footprint_scores = {"low": 15, "low-medium": 12, "medium": 10, "high": 5}
    score += footprint_scores.get(footprint, 0)

    # AWS-specific (Device Farm)
    if grids.get("aws_device_farm") is True:
        score += 10

    return min(100, score)


def score_license_cost(
    profile: UserProfile, fw: FrameworkData
) -> int:
    """Score C7: License & Cost (0-100)."""
    license_type = fw.license.lower()

    # Fully permissive open source
    if any(lic in license_type for lic in ("mit", "apache", "bsd")):
        return 100

    # Copyleft but free
    if "agpl" in license_type or "gpl" in license_type:
        return 70

    # Freemium (open-source core with paid features)
    if "free" in license_type:
        return 80

    # Commercial
    if "commercial" in license_type or "proprietary" in license_type:
        return 20

    # Unknown - default neutral
    return 60


# ─── Cloud Migration Criteria ─────────────────────────────────────────


def score_cloud_provider_support(
    profile: UserProfile, fw: FrameworkData
) -> int:
    """Score C8: Cloud Provider Coverage (0-100).
    How well the framework supports the user's target cloud providers.
    """
    score = 0
    cloud_providers_data = getattr(fw, "cloud_providers", None)
    if not cloud_providers_data:
        # Fall back to checking architecture_fit for cloud support
        if fw.architecture_fit.get("cloud_infrastructure") is True:
            return 50
        return 0

    # Check if it's a dict (YAML cloud_providers section)
    if isinstance(cloud_providers_data, dict):
        # Score based on user's cloud providers
        if not profile.cloud_providers:
            # No specific providers requested, score on breadth
            supported = sum(
                1 for v in cloud_providers_data.values()
                if v is True or (isinstance(v, str) and v.lower() not in ("false", ""))
            )
            score = min(100, supported * 15)
        else:
            # Check each requested provider
            provider_map = {
                CloudProvider.AWS: ["aws"],
                CloudProvider.AZURE: ["azure"],
                CloudProvider.GCP: ["gcp", "google"],
                CloudProvider.MULTI_CLOUD: ["aws", "azure", "gcp"],
                CloudProvider.HYBRID: ["aws", "azure", "gcp", "kubernetes", "openstack"],
            }
            total_needed = 0
            total_matched = 0
            for provider in profile.cloud_providers:
                keys_to_check = provider_map.get(provider, [])
                for key in keys_to_check:
                    total_needed += 1
                    for cp_key, cp_val in cloud_providers_data.items():
                        if key in cp_key.lower():
                            if cp_val is True or (isinstance(cp_val, str) and cp_val):
                                total_matched += 1
                                break

            if total_needed > 0:
                score = int((total_matched / total_needed) * 100)
            else:
                score = 50

    # Multi-cloud bonus
    if fw.architecture_fit.get("multi_cloud") is True:
        if any(p == CloudProvider.MULTI_CLOUD for p in profile.cloud_providers):
            score = min(100, score + 15)

    return min(100, score)


def score_iac_capabilities(
    profile: UserProfile, fw: FrameworkData
) -> int:
    """Score C9: Infrastructure-as-Code Capabilities (0-100).
    Evaluates IaC maturity: state mgmt, drift detection, modules, testing.
    """
    score = 0

    # Is this an IaC framework at all?
    if not fw.architecture_fit.get("infrastructure_as_code"):
        return 0

    # State management
    capabilities = fw.capabilities or {}
    if capabilities.get("state_management") is True:
        score += 20
    elif fw.architecture_fit.get("configuration_management") is True:
        score += 10  # Config mgmt tools have different state model

    # Drift detection
    if capabilities.get("drift_detection") or capabilities.get("drift_detection") == "native":
        score += 15

    # Module/reuse ecosystem
    maintainability = fw.maintainability or {}
    if maintainability.get("reusable_components"):
        score += 15

    # Policy as code
    if capabilities.get("policy_as_code"):
        score += 10

    # Testing capabilities
    testing_caps = getattr(fw, "testing_capabilities", None)
    if isinstance(testing_caps, dict):
        test_types = ["unit_testing", "integration_testing", "compliance_testing"]
        for tt in test_types:
            if testing_caps.get(tt):
                score += 8
    elif capabilities.get("component_testing"):
        score += 10

    # Secrets management
    if capabilities.get("secrets_management") or capabilities.get("vault_integration"):
        score += 7

    # Real programming language support (vs DSL-only)
    if capabilities.get("real_programming_languages") is True:
        score += 5

    return min(100, score)


def score_cloud_migration_readiness(
    profile: UserProfile, fw: FrameworkData
) -> int:
    """Score C10: Cloud Migration & Compliance Readiness (0-100).
    Evaluates how well the framework supports migration workflows,
    compliance automation, and enterprise cloud patterns.
    """
    score = 0

    # Check if framework has cloud migration metrics defined
    migration_metrics = getattr(fw, "cloud_migration_metrics", None)
    if isinstance(migration_metrics, dict) and len(migration_metrics) > 0:
        score += 25  # Has defined migration metrics = cloud-aware

    # Multi-account / multi-region support
    capabilities = fw.capabilities or {}
    if capabilities.get("stacksets") or profile.multi_account:
        cloud_providers_data = getattr(fw, "cloud_providers", {})
        if isinstance(cloud_providers_data, dict) and len(cloud_providers_data) >= 3:
            score += 15

    # Compliance testing capability
    testing_caps = getattr(fw, "testing_capabilities", None)
    if isinstance(testing_caps, dict):
        if testing_caps.get("compliance_testing"):
            score += 20
        if testing_caps.get("drift_detection") or testing_caps.get("dry_run"):
            score += 10

    # Rollback / safety mechanisms
    if capabilities.get("rollback") or capabilities.get("plan_and_apply"):
        score += 10

    # CI/CD pipeline integration (critical for cloud migration)
    cicd = fw.cicd_integration or {}
    ci_tools_supported = sum(
        1 for v in cicd.values() if v is True
    )
    score += min(15, ci_tools_supported * 3)

    # Immutable infrastructure alignment
    if profile.immutable_infrastructure:
        if capabilities.get("application_packaging") or fw.architecture_fit.get("cloud_infrastructure"):
            score += 5

    return min(100, score)
