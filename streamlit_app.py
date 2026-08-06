"""Automation Framework Migration & Coverage Advisor — Streamlit UI.

Flow:
  user query → 4-agent pipeline (Decision → Tool Selection → Evaluation → Format)
             → HITL review panel (approve / edit / discard)
             → chat history

Weighted matrix evaluation is triggered explicitly via the discovery form
or by typing 'evaluate'. RAG context is retrieved automatically for every query.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent / "config" / ".env")

from src.logging_config import configure_logging
configure_logging()

logger = logging.getLogger(__name__)

from src.models import (
    UserProfile, ArchitectureType, ExperienceLevel, BudgetPreference, CloudProvider,
)
from src.knowledge_base import KnowledgeBase
from src.scoring import ScoringEngine, WeightProfile
from src.scoring.weights import PRESETS, CRITERIA_IDS, CLOUD_CRITERIA_IDS
from src.graph import KnowledgeGraph, GraphStore, EntityExtractor, load_or_seed_graph
from src.graph.graphrag_engine import GraphRAGEngine
from src.llm.groq_client import GroqClient
from src.llm.advisor_llm import AdvisorLLM

# ── Page config ───────────────────────────────────────────────────────
st.set_page_config(
    page_title="Framework Advisor",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    MainMenu, footer {visibility: hidden;}
    .block-container {padding-top: 1.5rem; max-width: 1400px;}
    [data-testid="stSidebar"] {background-color: #f8fafc; min-width: 260px !important;}
    .project-bar {
        background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%);
        color: white; padding: 1rem 1.5rem; border-radius: 10px; margin-bottom: 1rem;
    }
    .project-bar h2 {margin: 0; font-size: 1.2rem;}
    .project-bar p  {margin: 0.2rem 0 0; font-size: 0.85rem; opacity: 0.85;}
</style>
""", unsafe_allow_html=True)


# ── Cached resources ──────────────────────────────────────────────────
@st.cache_resource
def load_kb() -> KnowledgeBase:
    kb = KnowledgeBase(data_dir="data/frameworks")
    kb.load()
    return kb


@st.cache_resource
def init_stack(_kb: KnowledgeBase):
    """Initialise GroqClient, KnowledgeGraph, GraphRAG, AdvisorLLM."""
    client = GroqClient()
    if not client.is_available:
        logger.warning("Groq: GROQ_API_KEY not set")
    else:
        logger.info("Groq: model=%s ready", client.model)

    graph = load_or_seed_graph(_kb)
    graphrag = GraphRAGEngine(graph=graph, knowledge_base=_kb)
    advisor = AdvisorLLM(
        llm_client=client,
        graphrag_engine=graphrag,
        knowledge_base=_kb,
        knowledge_graph=graph,
    )
    return client, graph, graphrag, advisor


kb = load_kb()
groq_client, knowledge_graph, graphrag_engine, advisor = init_stack(kb)


# ── Session state ─────────────────────────────────────────────────────
_DEFAULTS = {
    "messages": [],
    "profile": None,
    "matrix": None,
    "active_preset": "balanced",
    "custom_weights": {},
    "uploaded_docs_context": "",
    "case_study_context": "",
    "gathering": False,
    "gathered_info": {},
    "question_index": 0,
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ── Discovery questions ───────────────────────────────────────────────
QUESTIONS = [
    ("arch",         "What type of application are you testing?",
                     "e.g. Web SPA, API/Microservices, Mobile, Cloud Infrastructure"),
    ("language",     "What is your team's primary programming language?",
                     "e.g. Python, TypeScript, Java"),
    ("team",         "How big is your automation team and what is their experience level?",
                     "e.g. 5 engineers, intermediate"),
    ("cicd",         "What CI/CD tool do you use? Do you use Docker?",
                     "e.g. GitHub Actions with Docker"),
    ("requirements", "What capabilities are must-haves?",
                     "e.g. parallel execution, cross-browser, API testing"),
    ("legacy",       "Do you have existing test scripts to migrate? How many?",
                     "e.g. 300 Selenium scripts, or 'starting fresh'"),
    ("budget",       "What is your budget preference and timeline?",
                     "e.g. open-source preferred, 12 weeks"),
    ("cloud",        "Are you also evaluating cloud infrastructure tools?",
                     "e.g. Yes, AWS with Terraform — or No"),
]


def _build_profile(answers: dict) -> UserProfile:
    def _lower(k): return answers.get(k, "").lower()

    arch_map = {
        "spa": ArchitectureType.WEB_SPA, "react": ArchitectureType.WEB_SPA,
        "angular": ArchitectureType.WEB_SPA, "vue": ArchitectureType.WEB_SPA,
        "mpa": ArchitectureType.WEB_MPA,
        "mobile": ArchitectureType.NATIVE_MOBILE, "ios": ArchitectureType.NATIVE_MOBILE,
        "android": ArchitectureType.NATIVE_MOBILE,
        "api": ArchitectureType.API_ONLY, "microservice": ArchitectureType.API_ONLY,
        "cloud": ArchitectureType.CLOUD_INFRASTRUCTURE, "terraform": ArchitectureType.CLOUD_INFRASTRUCTURE,
    }
    arch = list({v for k, v in arch_map.items() if k in _lower("arch")}) or [ArchitectureType.WEB_SPA]

    lang_map = {"python": "Python", "typescript": "TypeScript", "javascript": "JavaScript",
                "java": "Java", "c#": "C#", "go": "Go", "ruby": "Ruby"}
    lang = next((v for k, v in lang_map.items() if k in _lower("language")), "Python")

    nums = re.findall(r"\d+", answers.get("team", ""))
    team_size = int(nums[0]) if nums else 5
    exp = (ExperienceLevel.BEGINNER if any(w in _lower("team") for w in ["beginner", "junior"])
           else ExperienceLevel.ADVANCED if any(w in _lower("team") for w in ["advanced", "senior"])
           else ExperienceLevel.INTERMEDIATE)

    ci_map = {"github": "GitHub Actions", "gitlab": "GitLab CI",
              "azure": "Azure DevOps", "circle": "CircleCI", "jenkins": "Jenkins"}
    ci = next((v for k, v in ci_map.items() if k in _lower("cicd")), "Jenkins")

    must = []
    for kw, label in [("parallel", "Parallel execution"), ("browser", "Cross-browser"),
                      ("api", "API testing"), ("visual", "Visual regression"),
                      ("performance", "Performance testing"), ("load", "Load testing")]:
        if kw in _lower("requirements"):
            must.append(label)

    legacy_nums = re.findall(r"\d+", answers.get("legacy", ""))
    legacy_count = int(legacy_nums[0]) if legacy_nums else 0
    current_fw = next((fw for fw in ["selenium", "cypress", "playwright", "robot"]
                       if fw in _lower("legacy")), None)

    budget_map = {"strict": BudgetPreference.STRICT_OSS, "flexible": BudgetPreference.FLEXIBLE,
                  "commercial": BudgetPreference.COMMERCIAL_OK}
    budget = next((v for k, v in budget_map.items() if k in _lower("budget")), BudgetPreference.OSS_PREFERRED)
    timeline_nums = re.findall(r"\d+", answers.get("budget", ""))
    timeline = int(timeline_nums[0]) if timeline_nums else 12

    cloud = any(w in _lower("cloud") for w in ["yes", "terraform", "ansible", "aws", "azure", "gcp"])
    providers = []
    for kw, cp in [("aws", CloudProvider.AWS), ("azure", CloudProvider.AZURE),
                   ("gcp", CloudProvider.GCP), ("multi", CloudProvider.MULTI_CLOUD)]:
        if kw in _lower("cloud"):
            providers.append(cp)
    if cloud:
        arch.append(ArchitectureType.CLOUD_INFRASTRUCTURE)

    return UserProfile(
        project_name="Advisory Session",
        architecture_types=list(set(arch)),
        primary_language=lang,
        team_size=max(1, team_size),
        automation_experience=exp,
        current_framework=current_fw,
        ci_cd_tool=ci,
        containerized="docker" in _lower("cicd"),
        parallel_required="parallel" in _lower("requirements"),
        browsers_required=["Chrome", "Firefox"] if "browser" in _lower("requirements") else ["Chrome"],
        budget=budget,
        timeline_weeks=max(2, timeline),
        must_support=must,
        legacy_test_count=legacy_count,
        legacy_frameworks=[current_fw] if current_fw else [],
        cloud_migration=cloud,
        cloud_providers=providers,
    )


def _run_evaluation(profile: UserProfile) -> str:
    """Run weighted matrix evaluation and return formatted markdown report."""
    preset = "cloud_migration" if profile.cloud_migration else "balanced"
    weights = WeightProfile.from_preset(preset)
    engine = ScoringEngine(knowledge_base=kb, weight_profile=weights)
    matrix = engine.evaluate(profile)

    st.session_state.profile = profile
    st.session_state.matrix = matrix
    advisor.set_context(profile, matrix)

    top5 = matrix.rankings[:5]
    top = top5[0]

    lines = [
        "## 📊 Framework Evaluation Report\n",
        f"Evaluated **{matrix.frameworks_evaluated} frameworks** across "
        f"{'10 criteria (cloud included)' if profile.cloud_migration else '7 criteria'}.\n",
        "### Your Profile",
        f"| | |",
        f"|---|---|",
        f"| Language | {profile.primary_language} |",
        f"| Team | {profile.team_size} engineers, {profile.automation_experience.value} |",
        f"| CI/CD | {profile.ci_cd_tool} |",
        f"| Legacy scripts | {profile.legacy_test_count} |",
        "",
        f"---\n## 🥇 Top Recommendation: **{top.framework}** — {top.overall_score}/100\n",
        "| Criterion | Score |",
        "|---|---|",
    ]
    labels = {
        "C1_language_compatibility": "Language Compatibility",
        "C2_api_validation": "API Validation",
        "C3_performance_load": "Performance Testing",
        "C4_cicd_integration": "CI/CD Integration",
        "C5_maintainability": "Maintainability",
        "C6_cloud_readiness": "Cloud Readiness",
        "C7_license_cost": "License & Cost",
    }
    for c_id, val in sorted(top.criteria_scores.model_dump().items(), key=lambda x: -x[1]):
        bar = "█" * (val // 10) + "░" * (10 - val // 10)
        lines.append(f"| {labels.get(c_id, c_id)} | `{bar}` {val}/100 |")

    lines += [
        "\n### 🏆 Full Rankings",
        "| Rank | Framework | Score | Confidence |",
        "|---|---|---|---|",
    ]
    for fw in top5:
        lines.append(f"| {fw.rank} | **{fw.framework}** | {fw.overall_score} | {fw.confidence} |")

    lines += [
        "\n---\n### 💬 Ask me anything",
        f"- *Why did {top.framework} score highest?*",
        f"- *Compare {top.framework} vs {top5[1].framework if len(top5) > 1 else 'Cypress'}*",
        "- *Show migration plan*",
        f"- *What are {top.framework}'s limitations?*",
    ]
    return "\n".join(lines)


# ── Sidebar ───────────────────────────────────────────────────────────
def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### 📂 Input Sources")

        with st.expander("📁 Upload Test Files", expanded=True):
            files = st.file_uploader(
                "Test files", type=["py", "js", "ts", "java", "yaml", "yml", "json"],
                accept_multiple_files=True, key="test_files",
            )
            if files and st.button("🔍 Analyse", key="analyse_files"):
                content_parts = []
                for f in files:
                    try:
                        text = f.read().decode("utf-8")
                        f.seek(0)
                        content_parts.append(f"### {f.name}\n" + "\n".join(text.split("\n")[:300]))
                    except Exception:
                        pass
                st.session_state.uploaded_docs_context = "\n\n".join(content_parts)
                st.success(f"✓ {len(files)} file(s) loaded")

        with st.expander("📚 Upload Case Study", expanded=False):
            cs_file = st.file_uploader(
                "Case study (txt/md/pdf)", type=["txt", "md", "pdf"], key="case_study",
            )
            if cs_file and st.button("📖 Parse", key="parse_cs"):
                try:
                    text = cs_file.read().decode("utf-8")
                    st.session_state.case_study_context = text
                    st.success(f"✓ {len(text)} chars parsed")
                except Exception as exc:
                    st.error(str(exc))

        st.markdown("---")
        st.markdown("### ⚙️ Scoring Weights")
        with st.expander("Adjust weights", expanded=False):
            preset = st.selectbox("Preset", list(PRESETS.keys()),
                                  index=list(PRESETS.keys()).index(st.session_state.active_preset),
                                  key="preset_sel")
            if preset != st.session_state.active_preset:
                st.session_state.active_preset = preset
                st.session_state.custom_weights = {}
            base = PRESETS[preset]
            for c_id in CRITERIA_IDS:
                w = st.slider(c_id.replace("_", " ").title()[:22],
                              0.0, 0.5, base.get(c_id, 0.1), 0.05, key=f"w_{c_id}")
                if w != base.get(c_id):
                    st.session_state.custom_weights[c_id] = w


# ── Main ──────────────────────────────────────────────────────────────
def main() -> None:
    _render_sidebar()

    st.markdown("""
    <div class="project-bar">
        <h2>🧭 Automation Framework Advisor</h2>
    </div>
    """, unsafe_allow_html=True)

    # Profile context bar
    if st.session_state.profile:
        p = st.session_state.profile
        cols = st.columns(4)
        cols[0].caption(f"🏗️ {', '.join(a.value for a in p.architecture_types[:2])}")
        cols[1].caption(f"🐍 {p.primary_language}")
        cols[2].caption(f"👥 {p.team_size} engineers")
        cols[3].caption(f"⚙️ {p.ci_cd_tool}")

    # Welcome message
    if not st.session_state.messages:
        st.session_state.messages.append({"role": "assistant", "content": (
            "👋 Hi! I'm your **Framework Migration Advisor**.\n\n"
            "- Type **`evaluate`** to start the guided discovery and get a weighted scorecard\n"
            "- Or ask directly: *'Compare Playwright vs Cypress'*, *'Best Python API testing framework'*\n"
            "- Upload test files in the sidebar for context-aware recommendations"
        )})

    # Chat history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="🧭" if msg["role"] == "assistant" else "👤"):
            st.markdown(msg["content"])

    # ── Chat input ────────────────────────────────────────────────────
    if user_input := st.chat_input("Ask about frameworks, or type 'evaluate' to start..."):
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user", avatar="👤"):
            st.markdown(user_input)

        msg_lower = user_input.lower().strip()

        # ── Discovery questionnaire ───────────────────────────────────
        if st.session_state.gathering:
            idx = st.session_state.question_index
            key = QUESTIONS[idx][0]
            st.session_state.gathered_info[key] = user_input
            st.session_state.question_index += 1

            if st.session_state.question_index >= len(QUESTIONS):
                st.session_state.gathering = False
                profile = _build_profile(st.session_state.gathered_info)
                response = _run_evaluation(profile)
            else:
                nq = QUESTIONS[st.session_state.question_index]
                response = f"**{nq[1]}**\n\n*{nq[2]}*"

            st.session_state.messages.append({"role": "assistant", "content": response})
            with st.chat_message("assistant", avatar="🧭"):
                st.markdown(response)
            st.stop()

        # ── Trigger evaluation ────────────────────────────────────────
        if any(w in msg_lower for w in ["evaluate", "start evaluation", "recommend frameworks"]):
            st.session_state.gathering = True
            st.session_state.question_index = 0
            st.session_state.gathered_info = {}
            q = QUESTIONS[0]
            response = f"Let's find the right framework for you!\n\n**{q[1]}**\n\n*{q[2]}*"
            st.session_state.messages.append({"role": "assistant", "content": response})
            with st.chat_message("assistant", avatar="🧭"):
                st.markdown(response)
            st.stop()

        # ── 5-agent pipeline ──────────────────────────────────────────
        advisor.set_context(st.session_state.profile, st.session_state.matrix)
        try:
            with st.spinner("🤖 Agents processing…"):
                response = advisor.run_pipeline(
                    user_input,
                    st.session_state.uploaded_docs_context,
                    st.session_state.case_study_context,
                )
        except Exception as exc:
            logger.warning("Pipeline failed (%s) — direct respond", exc)
            response = advisor.respond(
                user_input,
                st.session_state.uploaded_docs_context,
                st.session_state.case_study_context,
            )

        st.session_state.messages.append({"role": "assistant", "content": response})
        with st.chat_message("assistant", avatar="🧭"):
            st.markdown(response)

        # Background entity extraction
        try:
            extractor = EntityExtractor(llm_client=groq_client)
            ents, rels = extractor.extract_from_message(user_input)
            for e in ents:
                knowledge_graph.add_entity(e)
            for r in rels:
                knowledge_graph.add_relationship(r)
            if ents or rels:
                GraphStore().save(knowledge_graph)
        except Exception:
            pass


if __name__ == "__main__":
    main()
