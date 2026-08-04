# Evaluation Metrics & Scoring Algorithm Design

**Version:** 1.0  
**Date:** July 2026

---

## 1. Overview

The scoring algorithm evaluates candidate automation frameworks against
a user's specific project profile using a **multi-criteria weighted scoring
model**. The algorithm is transparent, configurable, and produces both
numeric scores and qualitative explanations.

---

## 2. Scoring Model Architecture

```
┌────────────────────┐     ┌──────────────────────┐
│  User Profile      │     │  Framework KB Entry   │
│  (from Discovery)  │     │  (YAML capabilities)  │
└────────┬───────────┘     └──────────┬────────────┘
         │                            │
         ▼                            ▼
┌─────────────────────────────────────────────────┐
│           SCORING ENGINE                         │
│                                                  │
│  1. Criteria Selection (based on profile)        │
│  2. Weight Assignment (default + user override)  │
│  3. Per-Criteria Scoring (0-100)                 │
│  4. Weighted Aggregation                         │
│  5. Penalty/Bonus Adjustments                    │
│  6. Final Ranking + Explanation                  │
└────────────────────────┬────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  OUTPUT: Ranked Decision Matrix                  │
│  - Overall score per framework                   │
│  - Per-criteria breakdown                        │
│  - Pros/Cons derived from scoring gaps           │
│  - Confidence level                              │
└─────────────────────────────────────────────────┘
```


---

## 3. Evaluation Criteria (7 Core + 3 Cloud Dimensions)

### 3.1 Core Criteria Definitions

| # | Criterion | Default Weight | Description |
|---|-----------|---------------|-------------|
| C1 | **Language & Ecosystem Compatibility** | 20% | How well the framework aligns with team's primary language and existing toolchain |
| C2 | **API/Backend Validation** | 20% | REST API testing, data validation, DB verification, schema assertion support |
| C3 | **Performance/Load Testing** | 15% | Integration with K6/Locust or built-in load capabilities |
| C4 | **CI/CD Integration** | 20% | Jenkins, Docker, GitHub Actions compatibility, pipeline ease |
| C5 | **Maintainability & Reusability** | 15% | POM support, fixtures, built-in reporting, debugging tools |
| C6 | **Cloud-Native/AWS Readiness** | 5% | Container support, cloud grid integration, distributed execution |
| C7 | **License & Cost** | 5% | OSS vs commercial, hidden costs (cloud execution, plugins) |

### 3.2 Cloud Migration Criteria (Activated when cloud_migration=True)

| # | Criterion | Cloud Weight | Description |
|---|-----------|-------------|-------------|
| C8 | **Cloud Provider Support** | 20% | Coverage of target cloud providers (AWS, Azure, GCP, multi-cloud) |
| C9 | **IaC Capabilities** | 15% | State management, drift detection, modules, testing, policy-as-code |
| C10 | **Cloud Migration Readiness** | 10% | Compliance automation, rollback safety, multi-account support |

### 3.3 Cloud Migration Metrics (Measured Post-Deployment)

| Metric | Description | Target |
|--------|-------------|--------|
| Resource Coverage | % of cloud resources managed by IaC | ≥95% |
| Drift Detection Rate | % of configuration drift caught before incidents | ≥90% |
| Deployment Frequency | IaC deployments (applies/updates) per week | Increasing trend |
| Mean Time to Recovery | Time to rollback/re-deploy after failure | < 30 min |
| Change Failure Rate | % of IaC applies that cause incidents | < 5% |
| Compliance Score | % of resources passing policy-as-code checks | ≥95% |
| Infrastructure Cost Visibility | Tagged/tracked resources | ≥90% |
| Blast Radius Analysis | Impact scope evaluation before changes | Always performed |

### 3.4 Optional / Conditional Criteria

These activate based on user profile responses:

| Criterion | Activates When | Weight Source |
|-----------|---------------|--------------|
| **Shadow DOM / iFrame Support** | User reports Shadow DOM or iFrames | Borrows from C5 |
| **Mobile Testing** | App architecture includes mobile | New criterion (replaces C6 weight) |
| **Visual Regression** | User requests visual testing | Bonus points |
| **Cross-Browser Coverage** | Multiple browsers required | Part of C1 scoring |
| **Team Ramp-Up Time** | Team has limited automation experience | Penalty modifier |

---

## 4. Scoring Algorithm

### 4.1 Per-Criteria Scoring Function

Each criterion is scored 0-100 using a **capability matching** approach:

```python
def score_criterion(criterion_id, user_profile, framework_data):
    """
    Score a single criterion for a framework against user requirements.
    Returns: int (0-100)
    """
    if criterion_id == "C1":  # Language & Ecosystem Compatibility
        return score_language_compatibility(user_profile, framework_data)
    elif criterion_id == "C2":  # API/Backend Validation
        return score_api_support(user_profile, framework_data)
    elif criterion_id == "C3":  # Performance/Load Testing
        return score_performance_integration(user_profile, framework_data)
    elif criterion_id == "C4":  # CI/CD Integration
        return score_cicd_integration(user_profile, framework_data)
    elif criterion_id == "C5":  # Maintainability
        return score_maintainability(user_profile, framework_data)
    elif criterion_id == "C6":  # Cloud-Native Readiness
        return score_cloud_readiness(user_profile, framework_data)
    elif criterion_id == "C7":  # License & Cost
        return score_license_cost(user_profile, framework_data)
```

### 4.2 Scoring Rules per Criterion

#### C1: Language & Ecosystem Compatibility (0-100)

```
Score Calculation:
- Primary language supported natively:       +50 points
- Primary language supported via wrapper:     +25 points
- Primary language NOT supported:              0 points
- Secondary language supported:              +15 points
- Pytest/JUnit/Mocha integration:            +15 points
- Package manager compatibility (pip/npm):   +10 points
- Active community (>10k GitHub stars):      +10 points

Cap at 100.
```

#### C2: API/Backend Validation (0-100)

```
Score Calculation:
- Native API testing support:                +30 points
- JSON/XML schema validation:                +20 points
- Database verification integration:         +15 points
- GraphQL support:                           +10 points
- Mock/stub capabilities:                    +15 points
- Response assertion library:                +10 points

Cap at 100.
```

#### C3: Performance/Load Testing (0-100)

```
Score Calculation:
- Built-in load testing:                     +40 points
- K6 integration documented:                 +20 points
- Locust/JMeter integration:                 +15 points
- Concurrent execution support:              +15 points
- Metrics export (Grafana/Prometheus):       +10 points

Cap at 100. Score 0 if framework is purely functional.
```

#### C4: CI/CD Integration (0-100)

```
Score Calculation:
- Docker official images available:          +25 points
- User's CI/CD tool supported (Jenkins/GHA): +25 points
- Headless execution mode:                   +20 points
- Parallel execution in CI:                  +15 points
- Test result format (JUnit XML/JSON):       +15 points

Cap at 100.
```

#### C5: Maintainability & Reusability (0-100)

```
Score Calculation:
- Page Object Model support:                 +20 points
- Fixture/setup-teardown support:            +15 points
- Built-in reporting:                        +15 points
- Code generation / recorder:                +10 points
- Auto-wait / smart assertions:              +15 points
- Debugging tools (trace viewer, REPL):      +15 points
- Active maintenance (releases < 3 months):  +10 points

Cap at 100.
```

#### C6: Cloud-Native/AWS Readiness (0-100)

```
Score Calculation:
- Docker containerization support:           +30 points
- Cloud grid integration (BS/Sauce):         +25 points
- Kubernetes/distributed execution:          +20 points
- AWS service integration (Device Farm):     +15 points
- Lightweight resource footprint:            +10 points

Cap at 100.
```

#### C7: License & Cost (0-100)

```
Score Calculation:
- Fully open-source (MIT/Apache):           100 points
- Open-source with paid cloud (freemium):    80 points
- AGPL or restrictive OSS:                   70 points
- Commercial with free tier:                 50 points
- Fully commercial / per-seat license:       20 points
```


### 4.3 Weighted Aggregation Formula

```
Final Score = Σ (Weight_i × Score_i) + Bonus - Penalties

Where:
  i = C1 through C7
  Weight_i = configured weight (sums to 1.0)
  Score_i = per-criteria score (0-100)
  Bonus = additional points for exceptional fit
  Penalties = deductions for hard blockers
```

### 4.4 Penalty System (Hard Blockers)

Penalties apply when a framework fundamentally cannot meet a requirement:

| Condition | Penalty |
|-----------|---------|
| Primary language NOT supported at all | -30 points |
| Cannot handle stated UI requirement (Shadow DOM, iFrame) | -20 points |
| No Docker support when containerization is required | -25 points |
| No parallel execution when explicitly required | -15 points |
| Paid-only parallelization when budget is "open-source" | -10 points |

### 4.5 Bonus System (Exceptional Fit)

| Condition | Bonus |
|-----------|-------|
| Framework is team's current tool (no migration needed) | +10 points |
| Framework covers both UI and API (reduces tool sprawl) | +5 points |
| Built-in accessibility testing | +5 points |
| Active migration guides from legacy framework available | +5 points |

---

## 5. Confidence Score

Each recommendation includes a confidence level:

```
Confidence = (Criteria Coverage × Data Quality × Profile Completeness)

Where:
  Criteria Coverage = % of criteria that could be evaluated (0-1)
  Data Quality = freshness of framework data (1.0 if <6 months, 0.8 if <1yr)
  Profile Completeness = % of discovery questions answered (0-1)

Levels:
  >= 0.85  → HIGH confidence
  0.65-0.84 → MEDIUM confidence
  < 0.65   → LOW confidence (flag for manual review)
```

---

## 6. Output Structure

### 6.1 Decision Matrix Format

```json
{
  "evaluation_metadata": {
    "date": "2026-07-30",
    "profile_completeness": 0.95,
    "frameworks_evaluated": 10,
    "criteria_weights": {
      "C1_language_compatibility": 0.20,
      "C2_api_validation": 0.20,
      "C3_performance_load": 0.15,
      "C4_cicd_integration": 0.20,
      "C5_maintainability": 0.15,
      "C6_cloud_readiness": 0.05,
      "C7_license_cost": 0.05
    }
  },
  "rankings": [
    {
      "rank": 1,
      "framework": "Playwright",
      "overall_score": 92.3,
      "confidence": "HIGH",
      "criteria_scores": {
        "C1": 95, "C2": 85, "C3": 80,
        "C4": 98, "C5": 93, "C6": 90, "C7": 100
      },
      "bonuses_applied": ["+5 UI+API combined"],
      "penalties_applied": [],
      "pros": ["Native Python", "Best Shadow DOM support"],
      "cons": ["No mobile native", "Newer ecosystem"]
    }
  ]
}
```

### 6.2 Explanation Generation

For each ranked framework, the engine generates natural language explanations:

```
Template:
"{framework} scored {score}/100 (Confidence: {confidence}).
It excels in {top_2_criteria} but has limitations in {bottom_2_criteria}.
Key consideration: {primary_tradeoff}."

Example:
"Playwright scored 92/100 (Confidence: HIGH).
It excels in CI/CD Integration (98) and Language Compatibility (95)
but has limitations in Performance Testing (80) and API Validation (85).
Key consideration: No native mobile app testing, but covers all stated
web requirements including Shadow DOM and iFrame handling."
```

---

## 7. Weight Customization Strategy

### 7.1 Default Weight Profiles

| Profile Name | C1 | C2 | C3 | C4 | C5 | C6 | C7 | Best For |
|-------------|----|----|----|----|----|----|-----|----------|
| **Balanced** | 20 | 20 | 15 | 20 | 15 | 5 | 5 | General teams |
| **API-Heavy** | 15 | 30 | 20 | 20 | 10 | 5 | 0 | Backend/microservices |
| **Enterprise** | 15 | 15 | 10 | 20 | 20 | 10 | 10 | Large orgs, compliance |
| **Startup** | 25 | 15 | 5 | 15 | 25 | 5 | 10 | Speed, cost-conscious |
| **DevOps-First** | 10 | 15 | 15 | 30 | 15 | 10 | 5 | Pipeline optimization |

### 7.2 Dynamic Weight Adjustment

The Discovery Module can adjust weights based on answers:

```python
def adjust_weights(base_weights, user_profile):
    """Dynamically adjust weights based on user emphasis."""
    if user_profile.get("parallel_execution_critical"):
        base_weights["C4"] += 0.05
        base_weights["C5"] -= 0.05

    if user_profile.get("budget") == "strict_oss":
        base_weights["C7"] += 0.05
        base_weights["C6"] -= 0.05

    if user_profile.get("team_experience") == "beginner":
        base_weights["C5"] += 0.05  # Maintainability more important
        base_weights["C3"] -= 0.05

    return normalize_weights(base_weights)  # Ensure sum = 1.0
```

---

## 8. Evaluation Pipeline Steps

```
Step 1: Load user profile from Discovery Module
Step 2: Select applicable frameworks from KB (filter by architecture_fit)
Step 3: For each candidate framework:
    a. Score all 7 criteria (0-100 each)
    b. Apply penalties for hard blockers
    c. Apply bonuses for exceptional fit
    d. Calculate weighted aggregate
    e. Determine confidence level
Step 4: Rank frameworks by final score
Step 5: Generate pros/cons from score deltas
Step 6: Generate natural language explanation
Step 7: Output Decision Matrix JSON
```

---

## 9. Validation & Accuracy Metrics

| Metric | Measurement Method | Target |
|--------|-------------------|--------|
| **Recommendation Accuracy** | Expert panel review (3+ QA leads) | ≥80% agreement |
| **Scoring Consistency** | Same input → same output (deterministic) | 100% |
| **Coverage of Edge Cases** | Test with 20 diverse profiles | Handles ≥90% |
| **Weight Sensitivity** | ±5% weight change impact analysis | Score delta <10pts |
| **Explanation Quality** | User clarity survey (1-5 scale) | ≥4.0 average |

---

*End of Evaluation Metrics Document*
