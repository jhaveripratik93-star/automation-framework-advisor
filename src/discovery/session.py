"""Discovery session manager - handles adaptive Q&A flow."""

import logging
from typing import Any

from src.models import (
    UserProfile,
    ArchitectureType,
    ExperienceLevel,
    BudgetPreference,
)
from .questions import DISCOVERY_QUESTIONS, Question

logger = logging.getLogger(__name__)


class DiscoverySession:
    """Manages an interactive discovery session with the user."""

    def __init__(self):
        self.answers: dict[str, Any] = {}
        self.current_index: int = 0
        self.completed: bool = False

    @property
    def progress(self) -> float:
        """Return completion percentage (0-1)."""
        total = len(DISCOVERY_QUESTIONS)
        return self.current_index / total if total > 0 else 0.0

    def get_current_question(self) -> Question | None:
        """Get the next question to ask."""
        if self.current_index >= len(DISCOVERY_QUESTIONS):
            self.completed = True
            return None
        return DISCOVERY_QUESTIONS[self.current_index]

    def submit_answer(self, question_id: str, answer: Any) -> None:
        """Record an answer and advance to next question."""
        self.answers[question_id] = answer
        self.current_index += 1

        if self.current_index >= len(DISCOVERY_QUESTIONS):
            self.completed = True

    def build_profile(self) -> UserProfile:
        """Convert collected answers into a structured UserProfile."""
        # Map architecture type answers
        arch_map = {
            "web spa": ArchitectureType.WEB_SPA,
            "web mpa": ArchitectureType.WEB_MPA,
            "native mobile": ArchitectureType.NATIVE_MOBILE,
            "hybrid mobile": ArchitectureType.HYBRID_MOBILE,
            "desktop": ArchitectureType.DESKTOP,
            "api": ArchitectureType.API_ONLY,
            "microservices": ArchitectureType.MICROSERVICES,
        }

        # Extract architecture types
        arch_answer = self.answers.get("arch_type", "")
        arch_types = []
        if isinstance(arch_answer, list):
            for a in arch_answer:
                for key, val in arch_map.items():
                    if key in a.lower():
                        arch_types.append(val)
                        break
        elif isinstance(arch_answer, str):
            for key, val in arch_map.items():
                if key in arch_answer.lower():
                    arch_types.append(val)

        if not arch_types:
            arch_types = [ArchitectureType.WEB_SPA]

        # Experience level
        exp_map = {
            "beginner": ExperienceLevel.BEGINNER,
            "intermediate": ExperienceLevel.INTERMEDIATE,
            "advanced": ExperienceLevel.ADVANCED,
            "expert": ExperienceLevel.EXPERT,
        }
        exp_answer = str(self.answers.get("team_experience", "intermediate"))
        experience = exp_map.get(exp_answer.lower(), ExperienceLevel.INTERMEDIATE)

        # Budget
        budget_map = {
            "strictly": BudgetPreference.STRICT_OSS,
            "open-source preferred": BudgetPreference.OSS_PREFERRED,
            "flexible": BudgetPreference.FLEXIBLE,
            "commercial": BudgetPreference.COMMERCIAL_OK,
        }
        budget_answer = str(self.answers.get("budget_preference", ""))
        budget = BudgetPreference.OSS_PREFERRED
        for key, val in budget_map.items():
            if key in budget_answer.lower():
                budget = val
                break

        # Build the profile
        return UserProfile(
            project_name=self.answers.get("project_name", "Unnamed Project"),
            architecture_types=arch_types,
            primary_language=self.answers.get("team_language", "Python"),
            secondary_languages=self._parse_list(
                self.answers.get("team_secondary", [])
            ),
            team_size=int(self.answers.get("team_size", 5)),
            automation_experience=experience,
            current_framework=self.answers.get("team_current"),
            ci_cd_tool=self.answers.get("env_cicd", "Jenkins"),
            containerized=self._parse_bool(self.answers.get("env_docker", False)),
            cloud_grid=self.answers.get("env_cloud"),
            parallel_required=self._parse_bool(
                self.answers.get("env_parallel", False)
            ),
            browsers_required=self._parse_list(
                self.answers.get("env_browsers", [])
            ),
            special_ui=self._parse_list(
                self.answers.get("arch_special_ui", [])
            ),
            budget=budget,
            timeline_weeks=int(self.answers.get("budget_timeline", 12)),
            must_support=self._parse_list(
                self.answers.get("req_must_have", [])
            ),
            nice_to_have=[],
            legacy_test_count=int(self.answers.get("legacy_count", 0)),
            legacy_frameworks=self._parse_list(
                self.answers.get("team_current", [])
            ),
        )

    @staticmethod
    def _parse_list(value: Any) -> list[str]:
        """Convert various input formats to a list of strings."""
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return []

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        """Convert various input formats to boolean."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("yes", "true", "1", "yes, critical")
        return False
