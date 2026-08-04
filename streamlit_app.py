"""Streamlit Chat UI - Automation Framework Migration & Coverage Advisor.

A chat-first interactive experience. The user describes their project,
the advisor asks follow-up questions, then delivers recommendations
directly in the conversation.
"""

import sys
import json
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from src.logging_config import configure_logging
configure_logging()

from src.models import (
    UserProfile,
    ArchitectureType,
    ExperienceLevel,
    BudgetPreference,
    CloudProvider,
)
from src.knowledge_base import KnowledgeBase
from src.scoring import ScoringEngine, WeightProfile
from src.migration.planner import MigrationPlanner
from src.migration.coverage import CoverageAnalyzer
from src.generator import BoilerplateGenerator
from src.chat.advisor import AdvisorChat
# Task 13.1 — new imports
from src.llm.ollama_client import OllamaClient
from src.graph import KnowledgeGraph, GraphStore, EntityExtractor, seed_knowledge_graph, load_or_seed_graph
from src.graph.graphrag_engine import GraphRAGEngine
from src.llm.advisor_llm import AdvisorLLM
from src.visualization.knowledge_graph import build_from_persistent_graph, render_graph_html

# ─── Page Config ──────────────────────────────────────────────────────
st.set_page_config(
    page_title="Framework Advisor",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ───────────────────────────────────────────────────────
st.markdown("""
<style>
    /* Clean look */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    .block-container {
        padding-top: 2rem;
        max-width: 1400px;
    }

    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background-color: #f8fafc;
        min-width: 250px !important;
        width: 250px !important;
    }
    
    /* Keep sidebar always visible and expanded */
    [data-testid="stSidebar"][aria-expanded="true"] {
        min-width: 250px !important;
        width: 250px !important;
    }
    
    [data-testid="stSidebar"][aria-expanded="false"] {
        min-width: 250px !important;
        width: 250px !important;
    }
    
    /* Section headers in sidebar */
    .sidebar-section {
        background: white;
        padding: 1rem;
        border-radius: 8px;
        margin-bottom: 1rem;
        border: 1px solid #e2e8f0;
    }
    
    .sidebar-section h3 {
        margin: 0 0 0.5rem 0;
        font-size: 0.9rem;
        font-weight: 600;
        color: #475569;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    /* Project info bar */
    .project-bar {
        background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%);
        color: white;
        padding: 1.2rem 1.5rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
    }
    .project-bar h2 {
        margin: 0;
        font-size: 1.3rem;
        font-weight: 600;
    }
    .project-bar p {
        margin: 0.3rem 0 0 0;
        font-size: 0.9rem;
        opacity: 0.85;
    }

    /* Chat styling */
    .stChatMessage {
        border-radius: 12px;
    }

    /* Metrics in chat */
    [data-testid="stMetric"] {
        background: #f0f9ff;
        border: 1px solid #bfdbfe;
        border-radius: 8px;
        padding: 0.6rem 0.8rem;
    }
    
    /* File uploader styling */
    [data-testid="stFileUploader"] {
        background: white;
        border: 2px dashed #cbd5e1;
        border-radius: 8px;
        padding: 1rem;
    }
</style>

<script>
    // Force sidebar to stay visible
    const sidebar = window.parent.document.querySelector('[data-testid="stSidebar"]');
    if (sidebar) {
        sidebar.style.display = 'block';
        sidebar.style.visibility = 'visible';
        sidebar.style.minWidth = '250px';
    }
</script>
""", unsafe_allow_html=True)


# ─── Initialize ───────────────────────────────────────────────────────
@st.cache_resource
def load_kb():
    kb = KnowledgeBase(data_dir="data/frameworks")
    kb.load()
    return kb


# Task 13.2 — application startup: Ollama + KnowledgeGraph + GraphRAG + AdvisorLLM
@st.cache_resource
def init_llm_stack(_kb):
    """Initialize Ollama client, knowledge graph, GraphRAG engine, and AdvisorLLM."""
    ollama = OllamaClient()
    # Resolve actual model name with tag (e.g. llama3:latest) from Ollama
    health = ollama.health_check()
    if health.connected and health.selected_model:
        ollama.model = health.selected_model
    graph = load_or_seed_graph(_kb)
    graphrag = GraphRAGEngine(graph=graph, knowledge_base=_kb)
    fallback = AdvisorChat(knowledge_base=_kb)
    advisor_llm = AdvisorLLM(
        ollama_client=ollama,
        graphrag_engine=graphrag,
        knowledge_base=_kb,
        fallback_advisor=fallback,
        knowledge_graph=graph,
    )
    return ollama, graph, graphrag, advisor_llm, fallback


kb = load_kb()
migration_planner = MigrationPlanner()
coverage_analyzer = CoverageAnalyzer()
boilerplate_generator = BoilerplateGenerator()
ollama_client, knowledge_graph, graphrag_engine, advisor_llm_instance, fallback_advisor = init_llm_stack(kb)

# Session state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "profile" not in st.session_state:
    st.session_state.profile = None
if "matrix" not in st.session_state:
    st.session_state.matrix = None
if "advisor" not in st.session_state:
    # Task 13.3 — use AdvisorLLM with AdvisorChat as fallback
    st.session_state.advisor = advisor_llm_instance
if "gathering" not in st.session_state:
    st.session_state.gathering = False
if "gathered_info" not in st.session_state:
    st.session_state.gathered_info = {}
if "question_index" not in st.session_state:
    st.session_state.question_index = 0
if "ready_to_evaluate" not in st.session_state:
    st.session_state.ready_to_evaluate = False
if "active_preset" not in st.session_state:
    st.session_state.active_preset = "balanced"
if "uploaded_test_files" not in st.session_state:
    st.session_state.uploaded_test_files = []
if "repo_url" not in st.session_state:
    st.session_state.repo_url = ""
if "case_study_file" not in st.session_state:
    st.session_state.case_study_file = None
if "auto_detected_context" not in st.session_state:
    st.session_state.auto_detected_context = {}
if "weights_modified" not in st.session_state:
    st.session_state.weights_modified = False
if "custom_weights" not in st.session_state:
    st.session_state.custom_weights = {}
if "uploaded_docs_context" not in st.session_state:
    st.session_state.uploaded_docs_context = ""
if "case_study_context" not in st.session_state:
    st.session_state.case_study_context = ""

if "sidebar_weights" not in st.session_state:
    st.session_state.sidebar_weights = None  # None = use preset
if "custom_criteria" not in st.session_state:
    st.session_state.custom_criteria = {}  # name -> {description, weight}


# ─── Criteria metadata for sidebar display ─────────────────────────────────
CRITERIA_META = {
    "C1_language_compatibility":    ("Language Compatibility",    "Primary language & ecosystem fit"),
    "C2_api_validation":            ("API Validation",             "REST/GraphQL testing & schema validation"),
    "C3_performance_load":          ("Performance & Load",         "Load testing integration & execution speed"),
    "C4_cicd_integration":          ("CI/CD Integration",          "Pipeline compatibility, Docker, parallelism"),
    "C5_maintainability":           ("Maintainability",            "Page objects, fixtures, debugging tools"),
    "C6_cloud_readiness":           ("Cloud Readiness",            "Docker images, cloud grids, distributed exec"),
    "C7_license_cost":              ("License & Cost",             "Open-source availability, hidden costs"),
    "C8_cloud_provider_support":    ("Cloud Provider Support",     "Coverage of target cloud platforms"),
    "C9_iac_capabilities":          ("IaC Capabilities",           "State mgmt, drift detection, modules"),
    "C10_cloud_migration_readiness":("Cloud Migration Readiness",  "Compliance, rollback, multi-account"),
}


def _get_active_weights() -> dict[str, float]:
    """Return current sidebar weights, falling back to the active preset."""
    from src.scoring.weights import PRESETS
    if st.session_state.sidebar_weights is not None:
        return dict(st.session_state.sidebar_weights)
    return PRESETS[st.session_state.active_preset].copy()


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Normalize weights so they sum to 1.0. Validates: Requirements 10.1"""
    total = sum(weights.values())
    if total <= 0:
        return weights
    return {k: v / total for k, v in weights.items()}


# ═══════════════════════════════════════════════════════════════════════
# LEGACY FUNCTION - NOT USED (kept for reference, replaced by right-panel config)
# This function was used when criteria were in the sidebar
# Now criteria are in the right config panel, and sidebar shows input options
# ═══════════════════════════════════════════════════════════════════════

# def _render_criteria_sidebar() -> None:
#     """LEGACY - Render the criteria weight sidebar."""
#     # This function is no longer used - see _render_input_sidebar() instead
#     pass


def _render_custom_criteria_section(
    base_weights: dict[str, float],
    normalized_base: dict[str, float],
) -> None:
    """Render Add Custom Criterion expander and list existing custom criteria.
    Validates: Requirements 11.1, 11.2, 11.3, 11.4
    """
    st.markdown("**Custom Criteria**")

    # Show existing custom criteria with remove buttons
    for name, meta in list(st.session_state.custom_criteria.items()):
        col_name, col_remove = st.sidebar.columns([3, 1])
        col_name.caption(f"🔵 **{name}** — weight {int(meta['weight'] * 100)}%")
        if col_remove.button("✕", key=f"remove_custom_{name}"):
            # Task 11.3 — proportional weight restoration
            removed_weight = st.session_state.custom_criteria.pop(name)["weight"]
            current = _get_active_weights()
            # Remove custom key, redistribute removed weight proportionally
            current.pop(name, None)
            total_remaining = sum(current.values())
            if total_remaining > 0:
                for k in current:
                    current[k] += removed_weight * (current[k] / total_remaining)
            st.session_state.sidebar_weights = _normalize_weights(current)
            if st.session_state.profile:
                _rerun_evaluation_with_weights(st.session_state.sidebar_weights)
            st.rerun()

    with st.expander("➕ Add Custom Criterion"):
        with st.form(key="custom_criterion_form", clear_on_submit=True):
            c_name = st.text_input("Name", placeholder="e.g. Mobile Support")
            c_desc = st.text_input("Description", placeholder="e.g. iOS/Android coverage")
            c_weight = st.slider("Weight", min_value=1, max_value=20, value=5, step=1)
            submitted = st.form_submit_button("Add")

        if submitted and c_name.strip():
            new_w = c_weight / 100.0
            # Task 11.2 — proportional redistribution of existing weights
            current = _get_active_weights()
            total_existing = sum(current.values())
            if total_existing > 0:
                scale = max(0.0, 1.0 - new_w)
                current = {k: v * scale for k, v in current.items()}
            current[c_name.strip()] = new_w
            st.session_state.custom_criteria[c_name.strip()] = {
                "description": c_desc.strip(),
                "weight": new_w,
            }
            st.session_state.sidebar_weights = _normalize_weights(current)
            # Task 11.4 — integrate into scoring (default score 50)
            if st.session_state.profile:
                _rerun_evaluation_with_weights(st.session_state.sidebar_weights)
            st.rerun()


def _rerun_evaluation_with_weights(weights: dict[str, float]) -> None:
    """Re-run scoring with updated weights and refresh matrix in session state.
    Custom criteria get a default score of 50 for all frameworks.
    Validates: Requirements 10.2, 11.2, 11.4
    """
    from src.scoring import ScoringEngine, WeightProfile
    profile = st.session_state.profile
    if profile is None:
        return
    # Strip custom criteria keys before passing to ScoringEngine
    # (custom criteria are not in CriteriaScores model)
    custom_keys = set(st.session_state.custom_criteria.keys())
    scoring_weights = {k: v for k, v in weights.items() if k not in custom_keys}
    if not scoring_weights:
        scoring_weights = weights
    wp = WeightProfile(weights=scoring_weights, profile_name="custom")
    engine = ScoringEngine(knowledge_base=kb, weight_profile=wp)
    matrix = engine.evaluate(profile)
    st.session_state.matrix = matrix
    st.session_state.advisor.set_context(profile, matrix)


# ─── Conversational Discovery Questions ───────────────────────────────
QUESTIONS = [
    {
        "key": "arch",
        "question": "What type of application are you testing? (e.g., Web SPA, API/Microservices, Mobile, Desktop, Cloud Infrastructure)",
        "hint": "You can say things like: 'React SPA with REST APIs' or 'AWS cloud infrastructure'",
    },
    {
        "key": "language",
        "question": "What's your team's primary programming language?",
        "hint": "e.g., Python, TypeScript, Java, C#",
    },
    {
        "key": "team",
        "question": "How big is your automation team, and what's their experience level?",
        "hint": "e.g., '5 engineers, intermediate experience' or '8 people, advanced'",
    },
    {
        "key": "cicd",
        "question": "What CI/CD tool and environment do you use? Do you use Docker?",
        "hint": "e.g., 'Jenkins with Docker' or 'GitHub Actions, no containers yet'",
    },
    {
        "key": "requirements",
        "question": "What capabilities are must-haves? (e.g., parallel execution, cross-browser, API testing, visual regression)",
        "hint": "List what's critical vs nice-to-have",
    },
    {
        "key": "legacy",
        "question": "Do you have existing test scripts to migrate? If so, how many and what framework?",
        "hint": "e.g., '300 Selenium + Pytest scripts' or 'Starting fresh, no legacy'",
    },
    {
        "key": "budget",
        "question": "What's your budget preference and timeline?",
        "hint": "e.g., 'Open-source preferred, 12 weeks' or 'Flexible budget, 6 weeks'",
    },
    {
        "key": "cloud",
        "question": "Are you also looking at cloud infrastructure migration? (Terraform, Ansible, etc.) If yes, which cloud providers?",
        "hint": "e.g., 'Yes, AWS multi-account' or 'No, just app testing'",
    },
]


def _build_profile_from_detected_context(ctx: dict, user_msg: str) -> UserProfile:
    """Build a UserProfile from auto-detected context and user message."""
    # Start with detected values
    lang = ctx.get("language", "Python")
    fw = ctx.get("framework", None)
    test_count = ctx.get("test_count", 0)
    
    # Parse user message for additional context
    msg_lower = user_msg.lower()
    
    # Detect architecture
    arch_types = []
    if any(w in msg_lower for w in ["web", "spa", "react", "angular", "vue"]):
        arch_types.append(ArchitectureType.WEB_SPA)
    if any(w in msg_lower for w in ["api", "rest", "microservice", "backend"]):
        arch_types.append(ArchitectureType.API_ONLY)
    if any(w in msg_lower for w in ["mobile", "ios", "android"]):
        arch_types.append(ArchitectureType.NATIVE_MOBILE)
    if any(w in msg_lower for w in ["cloud", "aws", "azure", "terraform"]):
        arch_types.append(ArchitectureType.CLOUD_INFRASTRUCTURE)
    
    if not arch_types:
        arch_types = [ArchitectureType.WEB_SPA]  # Default
    
    return UserProfile(
        project_name="Auto-detected Project",
        architecture_types=arch_types,
        primary_language=lang,
        team_size=5,  # Default
        automation_experience=ExperienceLevel.INTERMEDIATE,
        current_framework=fw,
        ci_cd_tool="GitHub Actions",  # Default
        containerized=False,
        parallel_required=False,
        browsers_required=["Chrome"],
        budget=BudgetPreference.OSS_PREFERRED,
        timeline_weeks=12,
        must_support=[],
        legacy_test_count=test_count,
        legacy_frameworks=[fw] if fw else [],
        cloud_migration=any(w in msg_lower for w in ["cloud", "aws", "azure", "gcp"]),
    )


# ─── Profile Builder (parses conversational answers) ──────────────────
def build_profile_from_answers(answers: dict) -> UserProfile:
    """Parse free-text answers into a structured UserProfile."""
    arch_answer = answers.get("arch", "").lower()
    lang_answer = answers.get("language", "").lower()
    team_answer = answers.get("team", "").lower()
    cicd_answer = answers.get("cicd", "").lower()
    req_answer = answers.get("requirements", "").lower()
    legacy_answer = answers.get("legacy", "").lower()
    budget_answer = answers.get("budget", "").lower()
    cloud_answer = answers.get("cloud", "").lower()

    # Parse architecture
    arch_types = []
    if any(w in arch_answer for w in ["spa", "react", "angular", "vue", "web app"]):
        arch_types.append(ArchitectureType.WEB_SPA)
    if any(w in arch_answer for w in ["mpa", "server-rendered", "traditional"]):
        arch_types.append(ArchitectureType.WEB_MPA)
    if any(w in arch_answer for w in ["mobile", "ios", "android", "native"]):
        arch_types.append(ArchitectureType.NATIVE_MOBILE)
    if any(w in arch_answer for w in ["api", "microservice", "rest", "backend"]):
        arch_types.append(ArchitectureType.API_ONLY)
    if any(w in arch_answer for w in ["cloud", "infrastructure", "iac", "terraform"]):
        arch_types.append(ArchitectureType.CLOUD_INFRASTRUCTURE)
    if not arch_types:
        arch_types = [ArchitectureType.WEB_SPA]

    # Parse language
    lang_map = {"python": "Python", "typescript": "TypeScript", "javascript": "JavaScript",
                "java": "Java", "c#": "C#", "go": "Go", "ruby": "Ruby"}
    primary_lang = "Python"
    for key, val in lang_map.items():
        if key in lang_answer:
            primary_lang = val
            break

    # Parse team
    import re
    team_size = 5
    nums = re.findall(r'\d+', team_answer)
    if nums:
        team_size = int(nums[0])

    exp_level = ExperienceLevel.INTERMEDIATE
    if "beginner" in team_answer or "junior" in team_answer:
        exp_level = ExperienceLevel.BEGINNER
    elif "advanced" in team_answer or "senior" in team_answer:
        exp_level = ExperienceLevel.ADVANCED
    elif "expert" in team_answer:
        exp_level = ExperienceLevel.EXPERT

    # Parse CI/CD
    ci_tool = "Jenkins"
    if "github" in cicd_answer:
        ci_tool = "GitHub Actions"
    elif "gitlab" in cicd_answer:
        ci_tool = "GitLab CI"
    elif "azure" in cicd_answer:
        ci_tool = "Azure DevOps"
    elif "circle" in cicd_answer:
        ci_tool = "CircleCI"

    containerized = any(w in cicd_answer for w in ["docker", "container"])

    # Parse requirements
    must_support = []
    if "parallel" in req_answer:
        must_support.append("Parallel execution")
    if "cross-browser" in req_answer or "browser" in req_answer:
        must_support.append("Cross-browser testing")
    if "api" in req_answer:
        must_support.append("API testing")
    if "visual" in req_answer:
        must_support.append("Visual regression")
    if "accessibility" in req_answer:
        must_support.append("Accessibility testing")
    if "performance" in req_answer or "load" in req_answer:
        must_support.append("Performance/load testing")

    special_ui = []
    if "shadow" in req_answer:
        special_ui.append("Shadow DOM")
    if "iframe" in req_answer:
        special_ui.append("iFrames")

    # Parse legacy
    legacy_count = 0
    legacy_nums = re.findall(r'\d+', legacy_answer)
    if legacy_nums:
        legacy_count = int(legacy_nums[0])

    current_framework = None
    for fw in ["selenium", "cypress", "playwright", "robot", "testcafe"]:
        if fw in legacy_answer:
            current_framework = fw.title()
            break

    # Parse budget
    budget = BudgetPreference.OSS_PREFERRED
    if "strict" in budget_answer or "only open" in budget_answer:
        budget = BudgetPreference.STRICT_OSS
    elif "flexible" in budget_answer or "any" in budget_answer:
        budget = BudgetPreference.FLEXIBLE
    elif "commercial" in budget_answer or "paid" in budget_answer:
        budget = BudgetPreference.COMMERCIAL_OK

    timeline = 12
    timeline_nums = re.findall(r'\d+', budget_answer)
    if timeline_nums:
        timeline = int(timeline_nums[0])

    # Parse cloud
    cloud_migration = any(w in cloud_answer for w in ["yes", "terraform", "ansible", "cloud", "aws", "azure", "gcp", "pulumi"])
    cloud_providers = []
    if "aws" in cloud_answer:
        cloud_providers.append(CloudProvider.AWS)
    if "azure" in cloud_answer:
        cloud_providers.append(CloudProvider.AZURE)
    if "gcp" in cloud_answer or "google" in cloud_answer:
        cloud_providers.append(CloudProvider.GCP)
    if "multi" in cloud_answer:
        cloud_providers.append(CloudProvider.MULTI_CLOUD)

    if cloud_migration:
        arch_types.append(ArchitectureType.CLOUD_INFRASTRUCTURE)

    return UserProfile(
        project_name="Advisory Session",
        architecture_types=list(set(arch_types)),
        primary_language=primary_lang,
        team_size=max(1, team_size),
        automation_experience=exp_level,
        current_framework=current_framework,
        ci_cd_tool=ci_tool,
        containerized=containerized,
        parallel_required="parallel" in req_answer,
        browsers_required=["Chrome", "Firefox"] if "browser" in req_answer else ["Chrome"],
        special_ui=special_ui,
        budget=budget,
        timeline_weeks=max(2, timeline),
        must_support=must_support,
        nice_to_have=[],
        legacy_test_count=legacy_count,
        legacy_frameworks=[current_framework] if current_framework else [],
        cloud_migration=cloud_migration,
        cloud_providers=cloud_providers,
        multi_account="multi-account" in cloud_answer or "multi account" in cloud_answer,
    )


# ─── Evaluation & Result Formatting ──────────────────────────────────
def run_evaluation(profile: UserProfile) -> str:
    """Run evaluation and format results as a rich, detailed chat message."""
    preset = "cloud_migration" if profile.cloud_migration else "balanced"
    weights = WeightProfile.from_preset(preset)
    engine = ScoringEngine(knowledge_base=kb, weight_profile=weights)
    matrix = engine.evaluate(profile)

    st.session_state.profile = profile
    st.session_state.matrix = matrix
    st.session_state.advisor.set_context(profile, matrix)

    # Format rich response
    top_5 = matrix.rankings[:5]
    response = "## 📊 Framework Evaluation Report\n\n"
    response += f"I analyzed **{matrix.frameworks_evaluated} frameworks** across "
    response += f"**{'10 criteria (including cloud)' if profile.cloud_migration else '7 criteria'}** "
    response += f"weighted for your specific profile.\n\n"

    # Profile summary
    response += "### 📋 Your Profile Summary\n"
    response += f"| Dimension | Value |\n|---|---|\n"
    response += f"| Architecture | {', '.join(a.value.replace('_', ' ').title() for a in profile.architecture_types)} |\n"
    response += f"| Primary Language | {profile.primary_language} |\n"
    response += f"| Team | {profile.team_size} engineers, {profile.automation_experience.value} level |\n"
    response += f"| CI/CD | {profile.ci_cd_tool}{' + Docker' if profile.containerized else ''} |\n"
    response += f"| Key Requirements | {', '.join(profile.must_support[:4]) if profile.must_support else 'General'} |\n"
    response += f"| Legacy Scripts | {profile.legacy_test_count} ({profile.current_framework or 'none'}) |\n"
    response += f"| Budget | {profile.budget.value.replace('_', ' ').title()} | Timeline: {profile.timeline_weeks} weeks |\n"
    if profile.cloud_migration:
        response += f"| Cloud | {', '.join(cp.value.upper() for cp in profile.cloud_providers)} |\n"
    response += "\n"

    # Top recommendation - detailed
    response += "---\n\n"
    top = top_5[0]
    response += f"## 🥇 Top Recommendation: {top.framework}\n\n"
    response += f"**Overall Score: {top.overall_score}/100** (Confidence: {top.confidence})\n\n"

    # Detailed criteria breakdown
    response += "### Scoring Breakdown\n\n"
    scores = top.criteria_scores.model_dump()
    labels = {
        "C1_language_compatibility": ("Language & Ecosystem", "How well it fits your team's Python expertise"),
        "C2_api_validation": ("API Testing Support", "REST/GraphQL testing, schema validation, mocking"),
        "C3_performance_load": ("Performance Testing", "Load testing integration and execution speed"),
        "C4_cicd_integration": ("CI/CD Integration", f"Compatibility with {profile.ci_cd_tool}, Docker, parallelization"),
        "C5_maintainability": ("Maintainability", "Page objects, fixtures, debugging, reporting"),
        "C6_cloud_readiness": ("Cloud Readiness", "Docker images, cloud grids, distributed execution"),
        "C7_license_cost": ("License & Cost", "Open-source availability, hidden costs"),
    }

    response += "| Criterion | Score | What This Means For You |\n|---|---|---|\n"
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    for c_id, val in sorted_scores:
        label, description = labels.get(c_id, (c_id, ""))
        bar = "█" * (val // 10) + "░" * (10 - val // 10)
        verdict = "Excellent fit" if val >= 85 else "Good fit" if val >= 70 else "Adequate" if val >= 50 else "Gap identified"
        response += f"| **{label}** | `{bar}` {val}/100 | {verdict}: {description} |\n"

    # Cloud criteria if applicable
    if top.cloud_criteria_scores:
        cloud_labels = {
            "C8_cloud_provider_support": ("Cloud Provider Coverage", "Support for your target cloud platforms"),
            "C9_iac_capabilities": ("IaC Maturity", "State management, drift detection, modules, testing"),
            "C10_cloud_migration_readiness": ("Migration Readiness", "Compliance, rollback, multi-account support"),
        }
        for c_id, val in top.cloud_criteria_scores.items():
            if c_id in cloud_labels:
                label, desc = cloud_labels[c_id]
                bar = "█" * (val // 10) + "░" * (10 - val // 10)
                verdict = "Excellent" if val >= 85 else "Good" if val >= 70 else "Adequate" if val >= 50 else "Gap"
                response += f"| **{label}** | `{bar}` {val}/100 | {verdict}: {desc} |\n"

    response += "\n"

    # Why this framework
    response += "### 💡 Why This Framework Fits Your Project\n\n"
    fw_data = kb.get(top.framework.lower())
    if fw_data:
        response += _generate_detailed_reasoning(profile, fw_data, top)

    # Runners up
    response += "\n---\n\n### 🏆 Full Rankings\n\n"
    response += "| Rank | Framework | Score | Key Strength | Key Limitation |\n|---|---|---|---|---|\n"
    for fw in top_5:
        strength = fw.pros[0] if fw.pros else "-"
        weakness = fw.cons[0] if fw.cons else "-"
        response += f"| {fw.rank} | **{fw.framework}** | {fw.overall_score}/100 | {strength} | {weakness} |\n"

    response += "\n"

    # Actionable next steps
    response += "---\n\n### 🎯 What You Can Ask Next\n\n"
    response += f"- *'Why did {top.framework} beat {top_5[1].framework if len(top_5) > 1 else 'others'}?'* — Deep comparison\n"
    response += f"- *'Compare {top_5[0].framework} vs {top_5[1].framework if len(top_5) > 1 else 'Cypress'}'* — Side-by-side table\n"
    response += f"- *'Show migration plan'* — Phased roadmap with effort estimates\n"
    response += f"- *'What are {top.framework}'s limitations?'* — Full list of trade-offs\n"
    response += f"- *'Generate boilerplate'* — Ready-to-run project template\n"
    response += f"- *'What if we use TypeScript instead?'* — Explore scenarios\n"

    return response


def _generate_detailed_reasoning(
    profile: UserProfile, fw_data, fw_score: FrameworkScore
) -> str:
    """Generate detailed human-readable reasoning for a recommendation."""
    reasons = []

    # Language fit
    primary = profile.primary_language.lower()
    fw_langs = [l.lower() for l in fw_data.languages_supported]
    if primary in fw_langs or any(primary in l for l in fw_langs):
        reasons.append(
            f"**Language alignment:** {fw_data.framework_name} has native "
            f"{profile.primary_language} support, meaning your team can start "
            f"writing tests immediately without learning a new language."
        )
    else:
        reasons.append(
            f"**Language consideration:** {fw_data.framework_name} uses "
            f"{', '.join(fw_data.languages_supported[:2])}. Your team "
            f"({profile.primary_language}) would need some ramp-up time."
        )

    # CI/CD fit
    ci_key = profile.ci_cd_tool.lower().replace(" ", "_")
    if fw_data.cicd_integration.get(ci_key) is True or fw_data.cicd_integration.get("jenkins") is True:
        docker_note = " with pre-built Docker images" if fw_data.cicd_integration.get("docker_support") else ""
        reasons.append(
            f"**CI/CD integration:** Proven compatibility with {profile.ci_cd_tool}"
            f"{docker_note}. Test results can plug directly into your existing pipeline."
        )

    # Parallel execution
    parallel = fw_data.capabilities.get("parallel_execution", "")
    if parallel and profile.parallel_required:
        reasons.append(
            f"**Parallel execution:** {parallel} parallelization support. "
            f"With {profile.legacy_test_count} scripts to run, this significantly "
            f"reduces feedback time in CI."
        )

    # Special UI
    if profile.special_ui:
        supported = []
        for ui in profile.special_ui:
            key = ui.lower().replace(" ", "_").replace("/", "_")
            for cap_key, cap_val in fw_data.capabilities.items():
                if any(word in cap_key for word in ui.lower().split()):
                    if cap_val is True or (isinstance(cap_val, str) and cap_val.lower() not in ("false", "")):
                        supported.append(ui)
                    break
        if supported:
            reasons.append(
                f"**Special UI handling:** Supports {', '.join(supported)}, "
                f"which are critical for your application's testing needs."
            )

    # Migration path
    if profile.legacy_test_count > 0:
        reasons.append(
            f"**Migration feasibility:** With {profile.legacy_test_count} existing "
            f"{'scripts from ' + profile.current_framework if profile.current_framework else 'scripts'}, "
            f"migration to {fw_data.framework_name} is well-documented with "
            f"community guides and similar architecture patterns."
        )

    # Cost
    if "mit" in fw_data.license.lower() or "apache" in fw_data.license.lower():
        reasons.append(
            f"**Cost:** Fully open-source ({fw_data.license}) — no licensing "
            f"fees, no vendor lock-in. Aligns with your "
            f"{profile.budget.value.replace('_', ' ')} budget preference."
        )

    return "\n\n".join(reasons) + "\n"


def format_migration_plan(profile: UserProfile, framework: str) -> str:
    """Format migration plan as a chat message."""
    roadmap = migration_planner.generate_roadmap(profile, framework)

    response = f"## 🗺️ Migration Roadmap → {framework}\n\n"
    response += f"**Total scripts:** {roadmap.total_scripts} | "
    response += f"**Estimated effort:** {roadmap.estimated_effort_weeks} weeks\n\n"

    for phase in roadmap.phases:
        scripts_info = f" ({phase.scripts_count} scripts)" if phase.scripts_count else ""
        response += f"### Phase {phase.phase_number}: {phase.name} — {phase.duration_weeks} weeks{scripts_info}\n"
        for task in phase.tasks:
            response += f"- {task}\n"
        response += "\n"

    # Coverage analysis
    fw_data = kb.get(framework.lower())
    if fw_data:
        report = coverage_analyzer.analyze(profile, fw_data)
        response += f"### Coverage Analysis\n"
        response += f"- Parity: **{report.coverage_parity_percentage}%**\n"
        if report.gaps:
            response += f"- Gaps found: {len(report.gaps)}\n"
            for gap in report.gaps:
                icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}[gap.severity]
                response += f"  - {icon} {gap.description} → *{gap.mitigation}*\n"
        if report.new_coverage_opportunities:
            response += "\n**New capabilities gained:**\n"
            for opp in report.new_coverage_opportunities[:4]:
                response += f"- ✨ {opp}\n"

    return response


def format_boilerplate(profile: UserProfile, framework: str) -> str:
    """Format boilerplate output as a chat message."""
    template = boilerplate_generator.generate(framework, profile)

    response = f"## 📦 Project Boilerplate — {framework}\n\n"
    response += "**Generated files:**\n```\n"
    for f in template.file_tree():
        response += f"{f}\n"
    response += "```\n\n"

    # Show key files
    key_files = ["requirements.txt", "conftest.py", "Dockerfile", "pytest.ini"]
    for gen_file in template.files:
        name = Path(gen_file.path).name
        if name in key_files:
            ext = Path(gen_file.path).suffix
            lang_map = {".py": "python", ".yml": "yaml", ".txt": "text", ".ini": "ini"}
            response += f"**{gen_file.path}:**\n```{lang_map.get(ext, '')}\n{gen_file.content}```\n\n"

    response += "*Ask me to show any other file, or say 'download' to get all files.*"
    return response


# ─── Message Processing ───────────────────────────────────────────────
def process_message(user_input: str, uploaded_docs: str = "", case_study: str = "") -> str:
    """Process user input and return advisor response.
    
    Args:
        user_input: User's message
        uploaded_docs: Context from uploaded test files
        case_study: Context from uploaded case study documents
    """
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 80)
    logger.info("PROCESS_MESSAGE: New request received")
    logger.info(f"  User input length: {len(user_input)} chars")
    logger.info(f"  Uploaded docs context: {len(uploaded_docs)} chars")
    logger.info(f"  Case study context: {len(case_study)} chars")
    logger.info(f"  Gathering mode: {st.session_state.gathering}")
    logger.info(f"  Profile exists: {st.session_state.profile is not None}")
    logger.info("=" * 80)
    
    msg = user_input.lower().strip()

    # If we're in the middle of gathering info
    if st.session_state.gathering:
        logger.info("FLOW: Discovery questionnaire mode (gathering answers)")
        idx = st.session_state.question_index
        key = QUESTIONS[idx]["key"]
        st.session_state.gathered_info[key] = user_input

        # Move to next question
        st.session_state.question_index += 1

        if st.session_state.question_index >= len(QUESTIONS):
            # All questions answered — run evaluation
            logger.info("FLOW: All discovery questions answered, running evaluation")
            st.session_state.gathering = False
            st.session_state.ready_to_evaluate = True
            profile = build_profile_from_answers(st.session_state.gathered_info)
            return run_evaluation(profile)
        else:
            # Ask next question
            logger.info(f"FLOW: Asking next discovery question ({st.session_state.question_index + 1}/{len(QUESTIONS)})")
            next_q = QUESTIONS[st.session_state.question_index]
            return f"**{next_q['question']}**\n\n*{next_q['hint']}*"

    # Auto-trigger evaluation if we have detected context
    auto_eval_needed = (
        st.session_state.auto_detected_context and 
        not st.session_state.profile and
        not st.session_state.gathering
    )
    
    # Check for explicit action triggers
    explicit_eval = any(w in msg for w in ["evaluate", "analyze", "recommend", "suggest frameworks"])
    
    if auto_eval_needed or explicit_eval:
        logger.info("FLOW: Auto-evaluation or explicit evaluation requested")
        # Build profile from auto-detected context or user description
        if st.session_state.auto_detected_context:
            logger.info("FLOW: Building profile from auto-detected context")
            # Use detected context
            ctx = st.session_state.auto_detected_context
            profile = _build_profile_from_detected_context(ctx, msg)
            return run_evaluation(profile)
        elif explicit_eval:
            logger.info("FLOW: Starting discovery flow (explicit evaluation request)")
            # Start discovery flow
            st.session_state.gathering = True
            st.session_state.question_index = 0
            st.session_state.gathered_info = {}
            first_q = QUESTIONS[0]
            return (
                "Great, let's find the right framework for you! "
                "I'll ask a few quick questions about your project.\n\n"
                f"**{first_q['question']}**\n\n*{first_q['hint']}*"
            )

    # Check for knowledge graph request
    # Knowledge graph visualization (disabled until GraphRAG integration is complete)
    if any(w in msg for w in ["knowledge graph", "graph", "visual", "diagram", "map"]):
        logger.info("FLOW: Knowledge graph visualization requested (currently disabled)")
        return "The knowledge graph visualization is currently being upgraded. It will be available once the GraphRAG integration is complete. You can still ask me about frameworks, comparisons, or migration plans."

    # Check for migration plan request
    if any(w in msg for w in ["migration plan", "roadmap", "migration steps", "show.*plan"]):
        logger.info("FLOW: Migration plan requested")
        if st.session_state.matrix and st.session_state.profile:
            fw = st.session_state.matrix.rankings[0].framework
            mentioned = fallback_advisor._extract_framework_name(msg)
            if mentioned:
                for r in st.session_state.matrix.rankings:
                    if mentioned.lower() in r.framework.lower():
                        fw = r.framework
                        break
            logger.info(f"FLOW: Generating migration plan for framework: {fw}")
            return format_migration_plan(st.session_state.profile, fw)
        logger.warning("FLOW: Migration plan requested but no evaluation exists yet")
        return "Run an evaluation first. Say **'start'** to begin."

    # Check for boilerplate request
    if any(w in msg for w in ["boilerplate", "template", "scaffold", "generate project"]):
        logger.info("FLOW: Boilerplate generation requested")
        if st.session_state.matrix and st.session_state.profile:
            fw = st.session_state.matrix.rankings[0].framework
            mentioned = fallback_advisor._extract_framework_name(msg)
            if mentioned:
                for r in st.session_state.matrix.rankings:
                    if mentioned.lower() in r.framework.lower():
                        fw = r.framework
                        break
            logger.info(f"FLOW: Generating boilerplate for framework: {fw}")
            return format_boilerplate(st.session_state.profile, fw)
        logger.warning("FLOW: Boilerplate requested but no evaluation exists yet")
        return "Run an evaluation first. Say **'start'** to begin."

    # Check for re-evaluate with changes
    if any(w in msg for w in ["re-evaluate", "reevaluate", "run again", "evaluate again"]):
        logger.info("FLOW: Re-evaluation requested")
        if st.session_state.profile:
            return run_evaluation(st.session_state.profile)
        logger.warning("FLOW: Re-evaluation requested but no profile exists")
        return "No profile yet. Say **'start'** to begin the discovery."

    # Default: use the advisor (AdvisorLLM with AdvisorChat fallback)
    logger.info("FLOW: Default flow - using AdvisorLLM (tool-based/LLM-powered)")
    logger.info(f"  AdvisorLLM will receive:")
    logger.info(f"    - User message: {len(user_input)} chars")
    logger.info(f"    - Uploaded docs: {len(uploaded_docs)} chars")
    logger.info(f"    - Case study: {len(case_study)} chars")
    advisor = st.session_state.advisor
    advisor.set_context(st.session_state.profile, st.session_state.matrix)
    return advisor.respond(user_input, uploaded_docs, case_study)


def _render_criteria_weights_compact():
    """Compact criteria weights for right sidebar config panel."""
    from src.scoring.weights import PRESETS, CRITERIA_IDS, CLOUD_CRITERIA_IDS
    
    preset_name = st.session_state.active_preset
    preset_weights = PRESETS.get(preset_name, PRESETS["balanced"])
    
    # Show active criteria based on profile
    active_criteria = CRITERIA_IDS.copy()
    if st.session_state.profile and st.session_state.profile.cloud_migration:
        active_criteria.extend(CLOUD_CRITERIA_IDS)
    
    # Compact slider display
    for c_id in active_criteria:
        if c_id in preset_weights:
            weight = st.session_state.custom_weights.get(c_id, preset_weights[c_id])
            label = c_id.replace("_", " ").replace("C", "C").title()[:20]
            new_weight = st.slider(
                label,
                0.0, 0.5, weight, 0.05,
                key=f"weight_{c_id}",
                help=f"Weight for {c_id}"
            )
            if new_weight != weight:
                st.session_state.custom_weights[c_id] = new_weight
                st.session_state.weights_modified = True


# ─── Helper: Render Left Sidebar (Input Options) ─────────────────────
def _render_input_sidebar():
    """Render left sidebar with input options."""
    import logging
    logger = logging.getLogger(__name__)
    logger.info("=" * 80)
    logger.info("_render_input_sidebar: CALLED")
    logger.info("=" * 80)
    
    with st.sidebar:
        logger.info("_render_input_sidebar: Inside st.sidebar context")
        
        # Visible confirmation for debugging
        st.success("✅ Left Sidebar Loaded")
        
        st.markdown("### 📂 Input Sources")
        logger.info("_render_input_sidebar: Rendered header")
        
        # Test Files Upload
        with st.expander("📁 Upload Test Files", expanded=True):
            uploaded_files = st.file_uploader(
                "Select test files or directories",
                type=["py", "js", "ts", "java", "yaml", "yml", "json"],
                accept_multiple_files=True,
                key="test_file_uploader",
                help="Upload test files to auto-detect framework and language"
            )
            if uploaded_files:
                st.session_state.uploaded_test_files = uploaded_files
                st.success(f"✓ {len(uploaded_files)} file(s) uploaded")
                
                # Auto-detect context button
                if st.button("🔍 Analyze Uploaded Files", key="analyze_files"):
                    with st.spinner("Analyzing files..."):
                        context = _analyze_uploaded_files(uploaded_files)
                        st.session_state.auto_detected_context.update(context)
                        st.success(f"Detected: {context.get('language', 'Unknown')} framework")
        
        # Repository URL
        with st.expander("🔗 Connect Repository", expanded=False):
            repo_url = st.text_input(
                "Git Repository URL",
                placeholder="https://github.com/user/repo",
                key="repo_url_input",
                help="Provide a GitHub/GitLab URL to clone and analyze"
            )
            if repo_url and repo_url != st.session_state.repo_url:
                st.session_state.repo_url = repo_url
                if st.button("📥 Clone & Analyze", key="clone_repo"):
                    st.info("Repository cloning and analysis coming soon")
        
        # Case Study Upload
        with st.expander("📚 Upload Case Study", expanded=False):
            case_study = st.file_uploader(
                "Previous migration case study",
                type=["pdf", "md", "txt", "docx"],
                key="case_study_uploader",
                help="Upload a case study to learn from past migrations"
            )
            if case_study:
                st.session_state.case_study_file = case_study
                st.success(f"✓ Case study: {case_study.name}")
                
                # Parse and store case study content for RAG
                if st.button("📖 Parse Case Study", key="parse_case_study"):
                    with st.spinner("Parsing document..."):
                        case_study_text = _parse_case_study(case_study)
                        st.session_state.case_study_context = case_study_text
                        st.success(f"Parsed {len(case_study_text)} characters")
        
        # Display auto-detected context
        if st.session_state.auto_detected_context:
            st.markdown("---")
            st.markdown("#### 🔍 Detected Context")
            ctx = st.session_state.auto_detected_context
            if "language" in ctx:
                st.caption(f"**Language:** {ctx['language']}")
            if "framework" in ctx:
                st.caption(f"**Framework:** {ctx['framework']}")
            if "test_count" in ctx:
                st.caption(f"**Test Files:** {ctx['test_count']}")


def _analyze_uploaded_files(files) -> dict:
    """Analyze uploaded test files to detect language and framework."""
    import re
    
    context = {"test_count": len(files)}
    all_content = []
    
    # Count file types
    extensions = {}
    imports_found = []
    
    for file in files:
        ext = file.name.split(".")[-1]
        extensions[ext] = extensions.get(ext, 0) + 1
        
        # Read first 50 lines to detect imports/frameworks
        try:
            content = file.read().decode("utf-8")
            file.seek(0)  # Reset for potential re-read
            
            lines = content.split("\n")[:50]
            for line in lines:
                imports_found.append(line.lower())
            
            # Store full content for RAG context (limit to first 500 lines per file)
            all_content.append(f"### File: {file.name}\n" + "\n".join(content.split("\n")[:500]))
        except:
            pass
    
    # Store combined content in session state for RAG
    st.session_state.uploaded_docs_context = "\n\n".join(all_content)
    
    # Detect language
    if "py" in extensions:
        context["language"] = "Python"
        # Detect Python frameworks
        imports_str = " ".join(imports_found)
        if "selenium" in imports_str:
            context["framework"] = "Selenium"
        elif "playwright" in imports_str:
            context["framework"] = "Playwright"
        elif "cypress" in imports_str:
            context["framework"] = "Cypress"
        elif "pytest" in imports_str:
            context["framework"] = "Pytest"
    elif "js" in extensions or "ts" in extensions:
        context["language"] = "JavaScript/TypeScript"
        imports_str = " ".join(imports_found)
        if "cypress" in imports_str:
            context["framework"] = "Cypress"
        elif "playwright" in imports_str:
            context["framework"] = "Playwright"
        elif "jest" in imports_str:
            context["framework"] = "Jest"
    elif "java" in extensions:
        context["language"] = "Java"
        imports_str = " ".join(imports_found)
        if "selenium" in imports_str:
            context["framework"] = "Selenium"
        elif "rest.assured" in imports_str or "restassured" in imports_str:
            context["framework"] = "REST Assured"
    
    return context


def _parse_case_study(file) -> str:
    """Parse case study document and extract text content."""
    try:
        file_type = file.name.split(".")[-1].lower()
        
        if file_type == "txt" or file_type == "md":
            content = file.read().decode("utf-8")
            file.seek(0)
            return content
        
        elif file_type == "pdf":
            # PDF parsing (requires PyPDF2 or pdfplumber)
            try:
                import PyPDF2
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page in pdf_reader.pages[:50]:  # Limit to 50 pages
                    text += page.extract_text()
                file.seek(0)
                return text
            except ImportError:
                return "PDF parsing requires PyPDF2. Install with: pip install PyPDF2"
        
        elif file_type == "docx":
            # DOCX parsing (requires python-docx)
            try:
                import docx
                doc = docx.Document(file)
                text = "\n".join([para.text for para in doc.paragraphs])
                file.seek(0)
                return text
            except ImportError:
                return "DOCX parsing requires python-docx. Install with: pip install python-docx"
        
        else:
            return f"Unsupported file type: {file_type}"
    
    except Exception as e:
        return f"Error parsing case study: {str(e)}"


# Duplicate function removed - see first definition above


# ─── Helper: Render Right Sidebar (Fine-tuning Parameters) ───────────
def _render_config_sidebar():
    """Render right sidebar with collapsible fine-tuning parameters."""
    # We'll use a custom right sidebar approach with columns
    # Since Streamlit only has one sidebar, we'll use the main area's right column
    pass  # Implemented inline in main()


# ─── Main App ─────────────────────────────────────────────────────────
def main():
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 80)
    logger.info("MAIN: Starting main() function")
    logger.info("=" * 80)
    
    # Left sidebar - Input options
    try:
        logger.info("MAIN: Calling _render_input_sidebar()...")
        _render_input_sidebar()
        logger.info("MAIN: _render_input_sidebar() completed successfully")
    except Exception as e:
        logger.error(f"MAIN: ERROR in _render_input_sidebar(): {e}")
        import traceback
        logger.error(traceback.format_exc())
        st.error(f"❌ Sidebar Error: {e}")
    
    # Main content area with right config panel
    main_col, config_col = st.columns([3, 1])
    
    with config_col:
        st.markdown("### ⚙️ Configuration")
        
        # Criteria Weights (collapsible)
        with st.expander("📊 Criteria Weights", expanded=False):
            _render_criteria_weights_compact()
        
        # LLM Settings (collapsible)
        with st.expander("🤖 LLM Settings", expanded=False):
            # Model selector
            health = ollama_client.health_check()
            if health.connected and health.available_models:
                model = st.selectbox(
                    "Model",
                    options=health.available_models,
                    index=0 if ollama_client.model not in health.available_models else health.available_models.index(ollama_client.model),
                    key="model_selector"
                )
                if model != ollama_client.model:
                    ollama_client.model = model
            else:
                st.warning("⚠️ Ollama not connected")
            
            # Temperature
            temp = st.slider("Temperature", 0.0, 1.0, 0.7, 0.1, key="temp_slider")
            ollama_client.temperature = temp
            
            # Max tokens
            max_tokens = st.number_input("Max Tokens", 512, 4096, 2048, 128, key="max_tokens_input")
            ollama_client.max_tokens = max_tokens
        
        # Preset selector
        with st.expander("🎯 Presets", expanded=False):
            presets = list(WeightProfile.list_presets().keys())
            preset = st.selectbox(
                "Weight Preset",
                options=presets,
                index=presets.index(st.session_state.active_preset) if st.session_state.active_preset in presets else 0,
                key="preset_selector"
            )
            if st.button("Apply Preset", key="apply_preset"):
                st.session_state.active_preset = preset
                st.session_state.weights_modified = False
                st.session_state.custom_weights = {}
                st.success(f"Applied '{preset}' preset")
    
    with main_col:
        # ── Project Header
        st.markdown("""
        <div class="project-bar">
            <h2>🧭 Automation Framework Migration & Coverage Advisor</h2>
            <p>Analyze your project, compare frameworks, and get migration recommendations</p>
        </div>
        """, unsafe_allow_html=True)

        # ── Context bar (shows current profile if set)
        if st.session_state.profile:
            p = st.session_state.profile
            with st.container():
                cols = st.columns(5)
                cols[0].caption(f"🏗️ {', '.join(a.value for a in p.architecture_types[:2])}")
                cols[1].caption(f"🐍 {p.primary_language}")
                cols[2].caption(f"👥 {p.team_size} engineers")
                cols[3].caption(f"⚙️ {p.ci_cd_tool}")
                cols[4].caption(f"📦 {p.legacy_test_count} scripts")

        # ── Welcome message if empty chat
        if not st.session_state.messages:
            welcome = (
                "👋 Hi! I'm your **Framework Migration Advisor**.\n\n"
                "I can help you:\n"
                "- **Evaluate** 17+ automation & cloud frameworks for your project\n"
                "- **Compare** tools head-to-head with scored criteria\n"
                "- **Plan** a phased migration roadmap\n"
                "- **Generate** ready-to-run project boilerplate\n\n"
                "---\n"
                "**Get started:**\n"
                "- Upload test files in the left sidebar to auto-detect your current setup\n"
                "- Or just ask me: *'What's the best Python testing framework?'*\n"
                "- Or describe your project: *'I need to test a React SPA with REST APIs'*"
            )
            st.session_state.messages.append({"role": "assistant", "content": welcome})

        # ── Render chat history
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"], avatar="🧭" if msg["role"] == "assistant" else "👤"):
                st.markdown(msg["content"])

        # ── Knowledge graph visualization (Task 13.7)
        if not st.session_state.gathering and st.session_state.matrix:
            with st.expander("🕸️ Knowledge Graph", expanded=False):
                import streamlit.components.v1 as components
                graph_data = build_from_persistent_graph(knowledge_graph, max_nodes=150)
                if graph_data.nodes:
                    graph_html = render_graph_html(graph_data)
                    components.html(graph_html, height=540)
                else:
                    st.info("Knowledge graph is empty. Run an evaluation to populate it.")

        # ── Chat input
        if user_input := st.chat_input("Ask me anything about frameworks, or describe your project..."):
            # Show user message
            st.session_state.messages.append({"role": "user", "content": user_input})
            with st.chat_message("user", avatar="👤"):
                st.markdown(user_input)

            # Build augmented context from uploaded documents
            uploaded_docs_ctx = st.session_state.uploaded_docs_context
            case_study_ctx = st.session_state.case_study_context
            
            # Process and show response (with tool-based approach)
            response = process_message(user_input, uploaded_docs_ctx, case_study_ctx)
            
            # Task 13.6 — extract entities from user message and persist graph updates
            try:
                extractor = EntityExtractor(ollama_client=ollama_client)
                new_entities, new_rels = extractor.extract_from_message(user_input)
                for e in new_entities:
                    knowledge_graph.add_entity(e)
                for r in new_rels:
                    knowledge_graph.add_relationship(r)
                if new_entities or new_rels:
                    GraphStore().save(knowledge_graph)
            except Exception:
                pass  # Entity extraction is best-effort
            
            st.session_state.messages.append({"role": "assistant", "content": response})
            with st.chat_message("assistant", avatar="🧭"):
                st.markdown(response)

            # Reset ready_to_evaluate after processing
        if st.session_state.ready_to_evaluate:
            st.session_state.ready_to_evaluate = False


if __name__ == "__main__":
    main()
