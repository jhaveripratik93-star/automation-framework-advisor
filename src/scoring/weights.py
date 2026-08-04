"""Weight profiles for scoring criteria."""

from __future__ import annotations

from dataclasses import dataclass, field
from src.models import UserProfile, BudgetPreference, ExperienceLevel


# Criterion IDs
C1 = "C1_language_compatibility"
C2 = "C2_api_validation"
C3 = "C3_performance_load"
C4 = "C4_cicd_integration"
C5 = "C5_maintainability"
C6 = "C6_cloud_readiness"
C7 = "C7_license_cost"

# Cloud migration criteria
C8 = "C8_cloud_provider_support"
C9 = "C9_iac_capabilities"
C10 = "C10_cloud_migration_readiness"

CRITERIA_IDS = [C1, C2, C3, C4, C5, C6, C7]
CLOUD_CRITERIA_IDS = [C8, C9, C10]


PRESETS = {
    "balanced": {C1: 0.20, C2: 0.20, C3: 0.15, C4: 0.20, C5: 0.15, C6: 0.05, C7: 0.05},
    "api_heavy": {C1: 0.15, C2: 0.30, C3: 0.20, C4: 0.20, C5: 0.10, C6: 0.05, C7: 0.00},
    "enterprise": {C1: 0.15, C2: 0.15, C3: 0.10, C4: 0.20, C5: 0.20, C6: 0.10, C7: 0.10},
    "startup": {C1: 0.25, C2: 0.15, C3: 0.05, C4: 0.15, C5: 0.25, C6: 0.05, C7: 0.10},
    "devops_first": {C1: 0.10, C2: 0.15, C3: 0.15, C4: 0.30, C5: 0.15, C6: 0.10, C7: 0.05},
    # Cloud migration presets (include C8-C10)
    "cloud_migration": {
        C1: 0.10, C2: 0.05, C3: 0.05, C4: 0.15, C5: 0.10, C6: 0.05, C7: 0.05,
        C8: 0.20, C9: 0.15, C10: 0.10,
    },
    "cloud_native": {
        C1: 0.12, C2: 0.05, C3: 0.05, C4: 0.13, C5: 0.10, C6: 0.05, C7: 0.05,
        C8: 0.15, C9: 0.20, C10: 0.10,
    },
    "hybrid_cloud": {
        C1: 0.12, C2: 0.08, C3: 0.05, C4: 0.15, C5: 0.10, C6: 0.05, C7: 0.05,
        C8: 0.15, C9: 0.15, C10: 0.10,
    },
}


@dataclass
class WeightProfile:
    """Manages evaluation criteria weights."""

    weights: dict[str, float] = field(default_factory=dict)
    profile_name: str = "balanced"

    @classmethod
    def default(cls) -> WeightProfile:
        """Return the default balanced weight profile."""
        return cls(weights=PRESETS["balanced"].copy(), profile_name="balanced")

    @classmethod
    def from_preset(cls, name: str) -> WeightProfile:
        """Create a weight profile from a named preset."""
        if name not in PRESETS:
            raise ValueError(
                f"Unknown preset '{name}'. Available: {list(PRESETS.keys())}"
            )
        return cls(weights=PRESETS[name].copy(), profile_name=name)

    @classmethod
    def list_presets(cls) -> dict[str, dict[str, float]]:
        """Return all available preset profiles."""
        return PRESETS.copy()

    def get(self, criterion_id: str) -> float:
        """Get weight for a specific criterion."""
        return self.weights.get(criterion_id, 0.0)

    def normalize(self) -> None:
        """Ensure all weights sum to 1.0."""
        total = sum(self.weights.values())
        if total > 0 and abs(total - 1.0) > 0.001:
            for key in self.weights:
                self.weights[key] /= total

    def adjust(self, profile: UserProfile) -> WeightProfile:
        """Dynamically adjust weights based on user profile."""
        adjusted = self.weights.copy()

        # If cloud migration is enabled, ensure cloud criteria have weights
        if profile.cloud_migration:
            if C8 not in adjusted:
                # Switch to cloud_migration preset base
                cloud_preset = PRESETS["cloud_migration"]
                adjusted = cloud_preset.copy()
            # Boost cloud provider weight if multi-cloud
            from src.models import CloudProvider
            if CloudProvider.MULTI_CLOUD in profile.cloud_providers:
                adjusted[C8] = adjusted.get(C8, 0.15) + 0.05
                adjusted[C4] = adjusted.get(C4, 0.15) - 0.05

        # Parallel execution emphasis
        if profile.parallel_required:
            adjusted[C4] = adjusted.get(C4, 0.20) + 0.05
            adjusted[C5] = adjusted.get(C5, 0.15) - 0.025
            adjusted[C3] = adjusted.get(C3, 0.15) - 0.025

        # Budget sensitivity
        if profile.budget == BudgetPreference.STRICT_OSS:
            adjusted[C7] = adjusted.get(C7, 0.05) + 0.05
            adjusted[C6] = adjusted.get(C6, 0.05) - 0.05

        # Beginner teams need maintainability
        if profile.automation_experience == ExperienceLevel.BEGINNER:
            adjusted[C5] = adjusted.get(C5, 0.15) + 0.05
            adjusted[C3] = adjusted.get(C3, 0.15) - 0.05

        # Large legacy inventory → CI/CD integration critical
        if profile.legacy_test_count > 200:
            adjusted[C4] = adjusted.get(C4, 0.20) + 0.03
            adjusted[C6] = adjusted.get(C6, 0.05) - 0.03

        # Compliance requirements boost cloud migration readiness
        if profile.compliance_requirements:
            if C10 in adjusted:
                adjusted[C10] = adjusted.get(C10, 0.10) + 0.05
                adjusted[C1] = adjusted.get(C1, 0.10) - 0.05

        result = WeightProfile(
            weights=adjusted, profile_name=f"{self.profile_name}_adjusted"
        )
        result.normalize()
        return result

    def to_dict(self) -> dict[str, float]:
        """Return weights as a dictionary."""
        return self.weights.copy()
