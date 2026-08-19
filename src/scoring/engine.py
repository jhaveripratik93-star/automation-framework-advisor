"""Core Scoring Engine - orchestrates framework evaluation."""

import logging
from datetime import datetime

from src.models import UserProfile
from src.knowledge_base import KnowledgeBase, FrameworkData
from .criteria import (
    score_language_compatibility,
    score_api_validation,
    score_performance_load,
    score_cicd_integration,
    score_maintainability,
    score_cloud_readiness,
    score_license_cost,
    score_cloud_provider_support,
    score_iac_capabilities,
    score_cloud_migration_readiness,
)
from .penalties import calculate_penalties, calculate_bonuses
from .weights import WeightProfile, CRITERIA_IDS
from .models import DecisionMatrix, FrameworkScore, CriteriaScores

logger = logging.getLogger(__name__)

# Map criterion IDs to their scorer functions
CRITERION_SCORERS = {
    "C1_language_compatibility": score_language_compatibility,
    "C2_api_validation": score_api_validation,
    "C3_performance_load": score_performance_load,
    "C4_cicd_integration": score_cicd_integration,
    "C5_maintainability": score_maintainability,
    "C6_cloud_readiness": score_cloud_readiness,
    "C7_license_cost": score_license_cost,
}

# Cloud migration specific criteria (activated when cloud_migration=True)
CLOUD_CRITERION_SCORERS = {
    "C8_cloud_provider_support": score_cloud_provider_support,
    "C9_iac_capabilities": score_iac_capabilities,
    "C10_cloud_migration_readiness": score_cloud_migration_readiness,
}


class ScoringEngine:
    """Evaluates frameworks against user profile using weighted criteria."""

    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        weight_profile: WeightProfile | None = None,
    ):
        self.kb = knowledge_base
        self.weights = weight_profile or WeightProfile.default()

    def evaluate(self, profile: UserProfile) -> DecisionMatrix:
        """Run full evaluation and return ranked Decision Matrix."""
        # Step 1: Filter applicable frameworks by architecture
        arch_types = [arch.value for arch in profile.architecture_types]
        candidates = self.kb.filter_by_architecture(arch_types)

        if not candidates:
            logger.warning("No frameworks match the given architecture types")
            candidates = self.kb.list_all()

        # Step 2: Adjust weights based on profile
        adjusted_weights = self.weights.adjust(profile)

        # Step 3: Determine active criteria
        active_scorers = dict(CRITERION_SCORERS)
        if profile.cloud_migration:
            active_scorers.update(CLOUD_CRITERION_SCORERS)

        # Step 4: Score each candidate
        scores: list[FrameworkScore] = []
        for fw_data in candidates:
            try:
                score = self._score_framework(
                    fw_data, profile, adjusted_weights, active_scorers
                )
                scores.append(score)
            except Exception as e:
                logger.error(f"Error scoring {fw_data.framework_name}: {e}")

        # Step 5: Rank by overall score descending
        scores.sort(key=lambda s: s.overall_score, reverse=True)
        for i, score in enumerate(scores):
            score.rank = i + 1

        # Step 6: Generate pros/cons and explanations
        for score in scores:
            score.pros, score.cons = self._derive_pros_cons(score)
            score.explanation = self._generate_explanation(score, profile)

        # Step 7: Build decision matrix
        return DecisionMatrix(
            evaluation_date=datetime.now().strftime("%Y-%m-%d"),
            profile_completeness=self._calc_completeness(profile),
            frameworks_evaluated=len(candidates),
            criteria_weights=adjusted_weights.to_dict(),
            rankings=scores,
        )

    def _score_framework(
        self,
        fw_data: FrameworkData,
        profile: UserProfile,
        weights: WeightProfile,
        active_scorers: dict | None = None,
    ) -> FrameworkScore:
        """Score a single framework across all active criteria."""
        if active_scorers is None:
            active_scorers = CRITERION_SCORERS

        criteria_scores = {}

        for criterion_id, scorer_fn in active_scorers.items():
            try:
                criteria_scores[criterion_id] = scorer_fn(profile, fw_data)
            except Exception as e:
                logger.warning(
                    f"Scoring {criterion_id} for {fw_data.framework_name} "
                    f"failed: {e}. Defaulting to 50."
                )
                criteria_scores[criterion_id] = 50

        # Weighted aggregation
        weighted_sum = sum(
            weights.get(c_id) * score
            for c_id, score in criteria_scores.items()
            if weights.get(c_id) > 0
        )

        # Normalize if using more criteria than weights account for
        total_weight = sum(
            weights.get(c_id) for c_id in criteria_scores.keys()
            if weights.get(c_id) > 0
        )
        if total_weight > 0 and abs(total_weight - 1.0) > 0.01:
            weighted_sum = weighted_sum / total_weight

        # Penalties
        penalties = calculate_penalties(profile, fw_data)
        penalty_total = sum(p.points for p in penalties)

        # Bonuses
        bonuses = calculate_bonuses(profile, fw_data)
        bonus_total = sum(b.points for b in bonuses)

        # Final score clamped to 0-100
        overall = max(0.0, min(100.0, weighted_sum + bonus_total - penalty_total))

        # Confidence calculation
        confidence = self._calc_confidence(profile, fw_data)

        return FrameworkScore(
            framework=fw_data.framework_name,
            overall_score=round(overall, 1),
            confidence=confidence,
            criteria_scores=CriteriaScores(**{
                k: v for k, v in criteria_scores.items()
                if k in CriteriaScores.model_fields
            }),
            cloud_criteria_scores=criteria_scores if profile.cloud_migration else {},
            bonuses_applied=[b.description for b in bonuses],
            penalties_applied=[p.description for p in penalties],
        )

    def _calc_confidence(
        self, profile: UserProfile, fw_data: FrameworkData
    ) -> str:
        """Calculate confidence level for a recommendation."""
        # Profile completeness factor
        profile_fields = [
            profile.primary_language,
            profile.ci_cd_tool,
            profile.architecture_types,
            profile.team_size,
        ]
        completeness = sum(1 for f in profile_fields if f) / len(profile_fields)

        # Data quality factor (proxy: fewer limitations = better documented)
        data_quality = 1.0 if len(fw_data.limitations) > 0 else 0.7

        confidence_score = completeness * data_quality

        if confidence_score >= 0.85:
            return "HIGH"
        elif confidence_score >= 0.65:
            return "MEDIUM"
        else:
            return "LOW"

    def _calc_completeness(self, profile: UserProfile) -> float:
        """Calculate how complete the user profile is (0-1)."""
        total_fields = 10
        filled = 0
        if profile.primary_language:
            filled += 1
        if profile.architecture_types:
            filled += 1
        if profile.ci_cd_tool:
            filled += 1
        if profile.team_size > 0:
            filled += 1
        if profile.browsers_required:
            filled += 1
        if profile.must_support:
            filled += 1
        if profile.budget:
            filled += 1
        if profile.automation_experience:
            filled += 1
        if profile.legacy_test_count > 0:
            filled += 1
        if profile.timeline_weeks > 0:
            filled += 1
        return round(filled / total_fields, 2)

    def _derive_pros_cons(
        self, score: FrameworkScore
    ) -> tuple[list[str], list[str]]:
        """Derive pros and cons from criteria scores."""
        scores_dict = score.criteria_scores.model_dump()
        if score.cloud_criteria_scores:
            scores_dict.update(score.cloud_criteria_scores)

        pros = []
        cons = []

        criterion_labels = {
            "C1_language_compatibility": "Language Compatibility",
            "C2_api_validation": "API Validation",
            "C3_performance_load": "Performance Testing",
            "C4_cicd_integration": "CI/CD Integration",
            "C5_maintainability": "Maintainability",
            "C6_cloud_readiness": "Cloud Readiness",
            "C7_license_cost": "License & Cost",
            "C8_cloud_provider_support": "Cloud Provider Support",
            "C9_iac_capabilities": "IaC Capabilities",
            "C10_cloud_migration_readiness": "Cloud Migration Readiness",
        }

        for c_id, value in scores_dict.items():
            label = criterion_labels.get(c_id, c_id)
            if value >= 85:
                pros.append(f"Strong {label} ({value}/100)")
            elif value <= 40:
                cons.append(f"Weak {label} ({value}/100)")

        for penalty in score.penalties_applied:
            cons.append(penalty)
        for bonus in score.bonuses_applied:
            pros.append(bonus)

        return pros[:5], cons[:5]

    @staticmethod
    def _score_band(value: int) -> str:
        if value >= 85: return "🟢 Excellent"
        if value >= 65: return "🟡 Good"
        if value >= 40: return "🟠 Fair"
        return "🔴 Poor"

    @staticmethod
    def _criterion_meaning(criterion_id: str, value: int, profile: UserProfile) -> str:
        """Return a plain-English sentence explaining what the score means for the user."""
        band = value  # numeric for threshold checks
        meanings = {
            "C1_language_compatibility": {
                "high":  f"Natively supports {profile.primary_language} — no wrappers or adapters needed.",
                "mid":   f"Partial {profile.primary_language} support — some glue code required.",
                "low":   f"Does NOT support {profile.primary_language} — your team must learn a new language.",
            },
            "C2_api_validation": {
                "high":  "Built-in REST/GraphQL testing — no extra library needed for API tests.",
                "mid":   "Basic HTTP support — you'll need a plugin for full API validation.",
                "low":   "Not designed for API testing — use a dedicated tool alongside this.",
            },
            "C3_performance_load": {
                "high":  "Purpose-built for load/performance testing — handles high concurrency natively.",
                "mid":   "Can run parallel tests but not designed for load simulation.",
                "low":   "Not a performance testing tool — pair with k6 or Locust for load tests.",
            },
            "C4_cicd_integration": {
                "high":  f"Plug-and-play with {profile.ci_cd_tool} — Docker images and pipeline templates available.",
                "mid":   f"Works with {profile.ci_cd_tool} but needs manual pipeline configuration.",
                "low":   f"Limited {profile.ci_cd_tool} support — expect significant pipeline setup effort.",
            },
            "C5_maintainability": {
                "high":  "Excellent debugger, trace viewer, and Page Object support — low long-term maintenance cost.",
                "mid":   "Adequate tooling — test maintenance grows with suite size.",
                "low":   "Weak debugging tools — large suites become hard to maintain over time.",
            },
            "C6_cloud_readiness": {
                "high":  "Docker-native, supports BrowserStack/SauceLabs/LambdaTest — runs anywhere in the cloud.",
                "mid":   "Docker supported but limited cloud grid integrations.",
                "low":   "Minimal cloud support — running at scale requires custom infrastructure work.",
            },
            "C7_license_cost": {
                "high":  "Fully open-source (MIT/Apache) — zero licensing cost, no vendor lock-in.",
                "mid":   "Open-source core with some paid features — budget for optional add-ons.",
                "low":   "Commercial license required — factor in per-seat or usage costs.",
            },
        }
        c = meanings.get(criterion_id, {})
        if not c:
            return ""
        if band >= 75:
            return c["high"]
        if band >= 45:
            return c["mid"]
        return c["low"]

    def _generate_explanation(self, score: FrameworkScore, profile: UserProfile | None = None) -> str:
        """Generate per-criterion plain-English explanation with score bands."""
        scores_dict = score.criteria_scores.model_dump()
        if score.cloud_criteria_scores:
            scores_dict.update(score.cloud_criteria_scores)

        label_map = {
            "C1_language_compatibility": "Language",
            "C2_api_validation":         "API Validation",
            "C3_performance_load":        "Performance",
            "C4_cicd_integration":        "CI/CD",
            "C5_maintainability":         "Maintainability",
            "C6_cloud_readiness":         "Cloud Readiness",
            "C7_license_cost":            "License & Cost",
            "C8_cloud_provider_support":  "Cloud Provider",
            "C9_iac_capabilities":        "IaC",
            "C10_cloud_migration_readiness": "Migration Readiness",
        }

        lines = [f"**{score.framework}** — {score.overall_score}/100 ({score.confidence} confidence)"]
        for c_id, value in scores_dict.items():
            label = label_map.get(c_id, c_id)
            band  = self._score_band(value)
            meaning = self._criterion_meaning(c_id, value, profile) if profile else ""
            suffix = f" — {meaning}" if meaning else ""
            lines.append(f"  - **{label}**: {value}/100 {band}{suffix}")

        return "\n".join(lines)
