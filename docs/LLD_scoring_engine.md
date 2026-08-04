# Low-Level Design (LLD)
# Scoring & Evaluation Engine Module

**Version:** 1.0  
**Date:** July 2026  
**Module:** `src/scoring/`

---

## 1. Module Overview

The Scoring Engine is the analytical core that takes a structured user
profile and evaluates all candidate frameworks from the Knowledge Base,
producing a ranked Decision Matrix with per-criteria breakdowns.

---

## 2. Class Diagram

```
┌─────────────────────────────────┐
│       ScoringEngine             │
├─────────────────────────────────┤
│ - knowledge_base: KnowledgeBase │
│ - weight_profile: WeightProfile │
│ - criteria: List[Criterion]     │
├─────────────────────────────────┤
│ + evaluate(profile) → Matrix    │
│ + rank(scores) → List[Ranked]   │
│ + explain(ranked) → str         │
└───────────────┬─────────────────┘
                │ uses
    ┌───────────┴────────────┐
    │                        │
    ▼                        ▼
┌──────────────────┐  ┌──────────────────────┐
│  Criterion       │  │  KnowledgeBase       │
├──────────────────┤  ├──────────────────────┤
│ - id: str        │  │ - frameworks: dict   │
│ - name: str      │  │ - data_dir: Path     │
│ - weight: float  │  ├──────────────────────┤
│ - scorer: Func   │  │ + load() → None      │
├──────────────────┤  │ + get(name) → dict   │
│ + score() → int  │  │ + list_all() → list  │
│ + explain() → str│  │ + filter(arch) → list│
└──────────────────┘  └──────────────────────┘

┌──────────────────────┐  ┌──────────────────────┐
│  WeightProfile       │  │  DecisionMatrix      │
├──────────────────────┤  ├──────────────────────┤
│ - weights: dict      │  │ - rankings: list     │
│ - profile_name: str  │  │ - metadata: dict     │
├──────────────────────┤  ├──────────────────────┤
│ + normalize() → None │  │ + to_json() → str    │
│ + adjust() → None    │  │ + to_markdown() → str│
│ + get(crit_id) → f   │  │ + top_n(n) → list    │
└──────────────────────┘  └──────────────────────┘

┌──────────────────────┐
│  FrameworkScore      │
├──────────────────────┤
│ - framework: str     │
│ - criteria_scores: d │
│ - bonuses: list      │
│ - penalties: list    │
│ - overall: float     │
│ - confidence: str    │
│ - pros: list         │
│ - cons: list         │
├──────────────────────┤
│ + calculate() → float│
│ + to_dict() → dict   │
└──────────────────────┘
```


---

## 3. File Structure

```
src/scoring/
├── __init__.py
├── engine.py           # ScoringEngine class (orchestrator)
├── criteria.py         # Individual criterion scoring functions
├── weights.py          # WeightProfile and preset profiles
├── penalties.py        # Penalty and bonus logic
├── models.py           # Data models (FrameworkScore, DecisionMatrix)
└── explainer.py        # Natural language explanation generator

src/knowledge_base/
├── __init__.py
├── loader.py           # YAML framework data loader
├── schema.py           # Pydantic models for framework data
└── filters.py          # Architecture-based filtering
```

---

## 4. Data Models (Pydantic)

### 4.1 UserProfile Model

```python
from pydantic import BaseModel
from typing import Optional
from enum import Enum

class ArchitectureType(str, Enum):
    WEB_SPA = "web_spa"
    WEB_MPA = "web_mpa"
    NATIVE_MOBILE = "native_mobile"
    HYBRID_MOBILE = "hybrid_mobile"
    DESKTOP = "desktop"
    API_ONLY = "api_only"
    MICROSERVICES = "microservices"

class ExperienceLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"

class BudgetPreference(str, Enum):
    STRICT_OSS = "strict_oss"
    OSS_PREFERRED = "oss_preferred"
    FLEXIBLE = "flexible"
    COMMERCIAL_OK = "commercial_ok"

class UserProfile(BaseModel):
    project_name: str
    architecture_types: list[ArchitectureType]
    primary_language: str
    secondary_languages: list[str] = []
    team_size: int
    automation_experience: ExperienceLevel
    current_framework: Optional[str] = None
    ci_cd_tool: str
    containerized: bool = False
    cloud_grid: Optional[str] = None
    parallel_required: bool = False
    browsers_required: list[str] = []
    special_ui: list[str] = []  # shadow_dom, iframe, canvas, etc.
    budget: BudgetPreference = BudgetPreference.OSS_PREFERRED
    timeline_weeks: int = 12
    must_support: list[str] = []
    nice_to_have: list[str] = []
    legacy_test_count: int = 0
    legacy_frameworks: list[str] = []
```

### 4.2 FrameworkData Model

```python
class FrameworkData(BaseModel):
    framework_name: str
    vendor: str
    license: str
    languages_supported: list[str]
    architecture_fit: dict[str, bool | str]
    capabilities: dict[str, bool | str]
    cicd_integration: dict[str, bool | str]
    cloud_grids: dict[str, bool | str]
    performance: dict[str, str | int]
    maintainability: dict[str, bool | str]
    limitations: list[str]
```

### 4.3 Scoring Output Models

```python
class CriteriaScores(BaseModel):
    C1_language_compatibility: int  # 0-100
    C2_api_validation: int
    C3_performance_load: int
    C4_cicd_integration: int
    C5_maintainability: int
    C6_cloud_readiness: int
    C7_license_cost: int

class FrameworkScore(BaseModel):
    rank: int
    framework: str
    overall_score: float
    confidence: str  # HIGH, MEDIUM, LOW
    criteria_scores: CriteriaScores
    bonuses_applied: list[str]
    penalties_applied: list[str]
    pros: list[str]
    cons: list[str]
    explanation: str

class DecisionMatrix(BaseModel):
    evaluation_date: str
    profile_completeness: float
    frameworks_evaluated: int
    criteria_weights: dict[str, float]
    rankings: list[FrameworkScore]
```

---

## 5. Core Algorithm Implementation

### 5.1 ScoringEngine.evaluate()

```python
class ScoringEngine:
    def __init__(self, knowledge_base: KnowledgeBase,
                 weight_profile: WeightProfile = None):
        self.kb = knowledge_base
        self.weights = weight_profile or WeightProfile.default()
        self.criteria = self._init_criteria()

    def evaluate(self, profile: UserProfile) -> DecisionMatrix:
        # Step 1: Filter applicable frameworks
        candidates = self.kb.filter_by_architecture(
            profile.architecture_types
        )

        # Step 2: Adjust weights based on profile
        adjusted_weights = self.weights.adjust(profile)

        # Step 3: Score each candidate
        scores = []
        for framework_data in candidates:
            score = self._score_framework(
                framework_data, profile, adjusted_weights
            )
            scores.append(score)

        # Step 4: Rank by overall score
        scores.sort(key=lambda s: s.overall_score, reverse=True)
        for i, score in enumerate(scores):
            score.rank = i + 1

        # Step 5: Generate explanations
        for score in scores:
            score.explanation = self._generate_explanation(score)
            score.pros, score.cons = self._derive_pros_cons(score)

        # Step 6: Build output matrix
        return DecisionMatrix(
            evaluation_date=datetime.now().isoformat(),
            profile_completeness=self._calc_completeness(profile),
            frameworks_evaluated=len(candidates),
            criteria_weights=adjusted_weights.to_dict(),
            rankings=scores
        )

    def _score_framework(self, fw_data, profile, weights):
        criteria_scores = {}
        for criterion in self.criteria:
            criteria_scores[criterion.id] = criterion.score(
                profile, fw_data
            )

        # Calculate weighted sum
        weighted_sum = sum(
            weights.get(c_id) * score
            for c_id, score in criteria_scores.items()
        )

        # Apply penalties
        penalties = calculate_penalties(profile, fw_data)
        penalty_total = sum(p.points for p in penalties)

        # Apply bonuses
        bonuses = calculate_bonuses(profile, fw_data)
        bonus_total = sum(b.points for b in bonuses)

        overall = max(0, min(100,
            weighted_sum + bonus_total - penalty_total
        ))

        confidence = self._calc_confidence(profile, fw_data)

        return FrameworkScore(
            rank=0,
            framework=fw_data.framework_name,
            overall_score=round(overall, 1),
            confidence=confidence,
            criteria_scores=CriteriaScores(**criteria_scores),
            bonuses_applied=[b.description for b in bonuses],
            penalties_applied=[p.description for p in penalties],
            pros=[], cons=[], explanation=""
        )
```


### 5.2 Individual Criterion Scorer Example

```python
# src/scoring/criteria.py

def score_language_compatibility(
    profile: UserProfile, fw: FrameworkData
) -> int:
    """Score C1: Language & Ecosystem Compatibility (0-100)."""
    score = 0

    # Primary language check
    primary = profile.primary_language.lower()
    fw_langs = [l.lower() for l in fw.languages_supported]

    if primary in fw_langs:
        score += 50
    elif any(primary in l for l in fw_langs):  # partial match
        score += 25

    # Secondary language bonus
    for lang in profile.secondary_languages:
        if lang.lower() in fw_langs:
            score += 15
            break  # max one secondary bonus

    # Test framework integration
    test_fw_map = {
        "python": "pytest",
        "javascript": "jest",
        "typescript": "jest",
        "java": "junit",
    }
    expected_test_fw = test_fw_map.get(primary, "")
    if expected_test_fw and _has_integration(fw, expected_test_fw):
        score += 15

    # Package manager compatibility
    pkg_map = {"python": "pip", "javascript": "npm", "typescript": "npm"}
    if pkg_map.get(primary):
        score += 10

    # Community activity (GitHub stars proxy)
    if _is_active_community(fw):
        score += 10

    return min(100, score)


def score_cicd_integration(
    profile: UserProfile, fw: FrameworkData
) -> int:
    """Score C4: CI/CD Integration (0-100)."""
    score = 0

    # Docker support
    if fw.cicd_integration.get("docker_support") is True:
        score += 25
    elif fw.cicd_integration.get("pre_built_docker_images"):
        score += 20

    # User's specific CI/CD tool
    ci_tool = profile.ci_cd_tool.lower()
    tool_key_map = {
        "jenkins": "jenkins",
        "github actions": "github_actions",
        "gitlab ci": "gitlab_ci",
        "azure devops": "azure_devops",
    }
    key = tool_key_map.get(ci_tool, ci_tool)
    if fw.cicd_integration.get(key) is True:
        score += 25

    # Headless execution
    if fw.capabilities.get("auto_wait") or "headless" in str(fw.capabilities):
        score += 20

    # Parallel in CI
    parallel = fw.capabilities.get("parallel_execution", "")
    if parallel == "native" or parallel is True:
        score += 15
    elif parallel:  # any non-empty value means some support
        score += 8

    # Test result output format
    if fw.maintainability.get("built_in_reporting"):
        score += 15
    else:
        score += 5  # most frameworks can output JUnit XML

    return min(100, score)
```

---

## 6. Penalty & Bonus Logic

```python
# src/scoring/penalties.py

from dataclasses import dataclass

@dataclass
class Adjustment:
    description: str
    points: int
    reason: str

def calculate_penalties(
    profile: UserProfile, fw: FrameworkData
) -> list[Adjustment]:
    penalties = []

    # Primary language not supported
    primary = profile.primary_language.lower()
    fw_langs = [l.lower() for l in fw.languages_supported]
    if primary not in fw_langs and not any(primary in l for l in fw_langs):
        penalties.append(Adjustment(
            description=f"No {profile.primary_language} support",
            points=30,
            reason="Team's primary language not available"
        ))

    # Required UI capability missing
    for ui_req in profile.special_ui:
        cap_key = ui_req.lower().replace(" ", "_")
        cap_val = fw.capabilities.get(cap_key, False)
        if cap_val is False:
            penalties.append(Adjustment(
                description=f"No {ui_req} support",
                points=20,
                reason=f"Required UI capability '{ui_req}' not available"
            ))

    # Docker required but not supported
    if profile.containerized:
        if not fw.cicd_integration.get("docker_support"):
            penalties.append(Adjustment(
                description="No Docker support",
                points=25,
                reason="Containerization required but not supported"
            ))

    # Parallel execution required but not available
    if profile.parallel_required:
        parallel = fw.capabilities.get("parallel_execution", "")
        if not parallel:
            penalties.append(Adjustment(
                description="No parallel execution",
                points=15,
                reason="Parallel execution required but not supported"
            ))

    return penalties


def calculate_bonuses(
    profile: UserProfile, fw: FrameworkData
) -> list[Adjustment]:
    bonuses = []

    # Current framework match (no migration needed)
    if profile.current_framework:
        if profile.current_framework.lower() in fw.framework_name.lower():
            bonuses.append(Adjustment(
                description="Current tool match",
                points=10,
                reason="No migration needed"
            ))

    # Covers both UI and API (reduces tool sprawl)
    has_ui = fw.architecture_fit.get("web_spa") is True
    has_api = fw.architecture_fit.get("api_testing") is True
    if has_ui and has_api:
        bonuses.append(Adjustment(
            description="UI + API combined",
            points=5,
            reason="Single framework for both UI and API testing"
        ))

    # Built-in accessibility testing
    if fw.capabilities.get("accessibility_testing") and \
       "accessibility" in profile.nice_to_have:
        bonuses.append(Adjustment(
            description="Accessibility built-in",
            points=5,
            reason="Accessibility testing available (nice-to-have met)"
        ))

    return bonuses
```

---

## 7. Knowledge Base Loader

```python
# src/knowledge_base/loader.py

import yaml
from pathlib import Path
from .schema import FrameworkData

class KnowledgeBase:
    def __init__(self, data_dir: str = "data/frameworks"):
        self.data_dir = Path(data_dir)
        self.frameworks: dict[str, FrameworkData] = {}

    def load(self) -> None:
        """Load all YAML framework profiles."""
        for yaml_file in self.data_dir.glob("*.yaml"):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            fw = FrameworkData(**data)
            self.frameworks[fw.framework_name.lower()] = fw

    def get(self, name: str) -> FrameworkData | None:
        return self.frameworks.get(name.lower())

    def list_all(self) -> list[FrameworkData]:
        return list(self.frameworks.values())

    def filter_by_architecture(
        self, arch_types: list[str]
    ) -> list[FrameworkData]:
        """Return frameworks that fit at least one architecture type."""
        results = []
        for fw in self.frameworks.values():
            for arch in arch_types:
                fit = fw.architecture_fit.get(arch, False)
                if fit is True or (isinstance(fit, str) and fit != "false"):
                    results.append(fw)
                    break
        return results
```

---

## 8. API Interface

```python
# src/scoring/api.py (FastAPI endpoint)

from fastapi import APIRouter
from .engine import ScoringEngine
from .models import UserProfile, DecisionMatrix
from ..knowledge_base.loader import KnowledgeBase

router = APIRouter(prefix="/api/v1", tags=["scoring"])

# Initialize on startup
kb = KnowledgeBase()
kb.load()
engine = ScoringEngine(knowledge_base=kb)

@router.post("/evaluate", response_model=DecisionMatrix)
async def evaluate_frameworks(profile: UserProfile) -> DecisionMatrix:
    """Evaluate frameworks against user profile and return ranked matrix."""
    return engine.evaluate(profile)

@router.get("/frameworks")
async def list_frameworks():
    """List all frameworks in knowledge base."""
    return [fw.framework_name for fw in kb.list_all()]

@router.get("/weights/presets")
async def get_weight_presets():
    """Return available weight preset profiles."""
    return WeightProfile.list_presets()
```

---

## 9. Sequence Diagram (Evaluation Flow)

```
Client          API           ScoringEngine    KnowledgeBase    Criteria
  │              │                │                │               │
  │─POST /eval──▶│                │                │               │
  │              │─evaluate()────▶│                │               │
  │              │                │─filter(arch)──▶│               │
  │              │                │◀──candidates───│               │
  │              │                │                │               │
  │              │                │──[for each candidate]──────────│
  │              │                │                │               │
  │              │                │─score_criterion()─────────────▶│
  │              │                │◀──────score (0-100)────────────│
  │              │                │                │               │
  │              │                │──[end loop]────────────────────│
  │              │                │                │               │
  │              │                │─apply penalties/bonuses        │
  │              │                │─rank by overall score          │
  │              │                │─generate explanations          │
  │              │                │                │               │
  │              │◀──Matrix───────│                │               │
  │◀──JSON───────│                │                │               │
```

---

## 10. Error Handling

| Scenario | Handling |
|----------|----------|
| Empty user profile | Return error with list of required fields |
| No frameworks match architecture | Return empty matrix with explanation |
| YAML parse error in KB | Log warning, skip framework, continue |
| Scoring exception for one criterion | Default to 50 (neutral), flag in output |
| Weight sum != 1.0 | Auto-normalize and log warning |

---

*End of LLD Document*
