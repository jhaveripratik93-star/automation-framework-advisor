"""Data models for scoring output."""

from pydantic import BaseModel


class CriteriaScores(BaseModel):
    """Individual criterion scores (0-100 each)."""

    C1_language_compatibility: int = 0
    C2_api_validation: int = 0
    C3_performance_load: int = 0
    C4_cicd_integration: int = 0
    C5_maintainability: int = 0
    C6_cloud_readiness: int = 0
    C7_license_cost: int = 0


class FrameworkScore(BaseModel):
    """Complete score result for a single framework."""

    rank: int = 0
    framework: str
    overall_score: float = 0.0
    confidence: str = "MEDIUM"
    criteria_scores: CriteriaScores = CriteriaScores()
    cloud_criteria_scores: dict[str, int] = {}  # C8, C9, C10 when cloud_migration=True
    bonuses_applied: list[str] = []
    penalties_applied: list[str] = []
    pros: list[str] = []
    cons: list[str] = []
    explanation: str = ""


class DecisionMatrix(BaseModel):
    """Complete evaluation output with all ranked frameworks."""

    evaluation_date: str
    profile_completeness: float
    frameworks_evaluated: int
    criteria_weights: dict[str, float]
    rankings: list[FrameworkScore] = []
