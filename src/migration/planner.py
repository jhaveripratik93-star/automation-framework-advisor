"""Migration Roadmap Planner."""

import math
from dataclasses import dataclass, field
from src.models import UserProfile


@dataclass
class MigrationPhase:
    """A single phase in the migration plan."""

    phase_number: int
    name: str
    duration_weeks: int
    scripts_count: int = 0
    tasks: list[str] = field(default_factory=list)


@dataclass
class MigrationRoadmap:
    """Complete migration plan."""

    target_framework: str
    total_scripts: int
    estimated_effort_weeks: int
    phases: list[MigrationPhase] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "target_framework": self.target_framework,
            "total_scripts_to_migrate": self.total_scripts,
            "estimated_effort_weeks": self.estimated_effort_weeks,
            "phases": [
                {
                    "phase": p.phase_number,
                    "name": p.name,
                    "duration_weeks": p.duration_weeks,
                    "scripts_count": p.scripts_count,
                    "tasks": p.tasks,
                }
                for p in self.phases
            ],
        }


class MigrationPlanner:
    """Generates phased migration roadmaps."""

    # Average scripts migrated per developer per week
    SCRIPTS_PER_DEV_PER_WEEK = 8

    def generate_roadmap(
        self, profile: UserProfile, target_framework: str
    ) -> MigrationRoadmap:
        """Generate a phased migration roadmap."""
        total_scripts = profile.legacy_test_count
        team_capacity = max(1, profile.team_size // 2)  # Half team on migration

        # Estimate total effort
        total_weeks = max(
            4,
            math.ceil(total_scripts / (team_capacity * self.SCRIPTS_PER_DEV_PER_WEEK)),
        )

        # Constrain to timeline
        if profile.timeline_weeks > 0:
            total_weeks = min(total_weeks, profile.timeline_weeks)

        phases = self._build_phases(profile, target_framework, total_weeks)

        return MigrationRoadmap(
            target_framework=target_framework,
            total_scripts=total_scripts,
            estimated_effort_weeks=total_weeks,
            phases=phases,
        )

    def _build_phases(
        self, profile: UserProfile, target: str, total_weeks: int
    ) -> list[MigrationPhase]:
        """Build migration phases based on project characteristics."""
        phases = []

        # Phase 1: Foundation (always first)
        phase1_weeks = max(1, total_weeks // 5)
        phases.append(MigrationPhase(
            phase_number=1,
            name="Foundation & Infrastructure",
            duration_weeks=phase1_weeks,
            tasks=[
                f"Set up {target} project with test runner integration",
                f"Configure CI/CD pipeline ({profile.ci_cd_tool})",
                "Establish page-object model / fixture structure",
                "Create shared utilities, helpers, and config",
                "Set up Docker containerization" if profile.containerized else "Configure local execution environment",
            ],
        ))

        # Phase 2: API Tests (if applicable)
        api_scripts = int(profile.legacy_test_count * 0.35)
        if api_scripts > 0 and "api" in str(profile.must_support).lower():
            phase2_weeks = max(1, total_weeks // 4)
            phases.append(MigrationPhase(
                phase_number=2,
                name="API Test Migration",
                duration_weeks=phase2_weeks,
                scripts_count=api_scripts,
                tasks=[
                    f"Migrate {api_scripts} API tests to {target}",
                    "Validate response schemas and data contracts",
                    "Set up API mocking/stubbing utilities",
                    "Configure parallel execution for API suite",
                ],
            ))

        # Phase 3: UI Tests (core flows)
        ui_scripts = int(profile.legacy_test_count * 0.5)
        if ui_scripts > 0:
            phase3_weeks = max(2, total_weeks // 3)
            ui_tasks = [
                f"Migrate {ui_scripts} UI tests (critical paths first)",
                "Implement page objects for key workflows",
                "Cross-browser validation",
            ]
            if profile.special_ui:
                ui_tasks.append(
                    f"Handle special UI: {', '.join(profile.special_ui)}"
                )
            phases.append(MigrationPhase(
                phase_number=len(phases) + 1,
                name="UI Test Migration (Core Flows)",
                duration_weeks=phase3_weeks,
                scripts_count=ui_scripts,
                tasks=ui_tasks,
            ))

        # Phase 4: Validation & Cutover
        remaining = profile.legacy_test_count - api_scripts - ui_scripts
        phase4_weeks = max(1, total_weeks // 5)
        phases.append(MigrationPhase(
            phase_number=len(phases) + 1,
            name="Integration Tests & Validation",
            duration_weeks=phase4_weeks,
            scripts_count=max(0, remaining),
            tasks=[
                "Migrate remaining integration/E2E tests",
                "Run coverage gap analysis (target: 100% parity)",
                "Performance benchmarking vs. legacy suite",
                "Team training and knowledge transfer",
                "Decommission legacy test scripts",
            ],
        ))

        return phases
