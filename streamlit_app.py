"""Automation Framework Migration & Coverage Advisor — Streamlit UI.

Flow:
  user query → LLM agent pipeline → chat history
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent / "config" / ".env")

from src.logging_config import configure_logging
configure_logging()

logger = logging.getLogger(__name__)

from src.knowledge_base import KnowledgeBase
from src.graph import load_or_seed_graph
from src.graph.graphrag_engine import GraphRAGEngine
from src.llm.groq_client import GroqClient
from src.llm.advisor_llm import AdvisorLLM
from src.scoring.weights import WeightProfile, PRESETS, CRITERIA_IDS

# ── Page config ───────────────────────────────────────────────────────
st.set_page_config(
    page_title="Framework Advisor",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"Get help": None, "Report a bug": None, "About": None},
)

st.markdown(
    f"<style>{Path('static/styles.css').read_text(encoding='utf-8')}</style>",
    unsafe_allow_html=True,
)


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

    # Export pipeline flow diagram on startup
    try:
        from src.agents.orchestrator import AgentOrchestrator
        from src.agents.langgraph_pipeline import build_pipeline
        from src.tools.executor import ToolExecutor
        _orch = AgentOrchestrator(llm_client=client, graphrag_engine=graphrag,
                                  knowledge_base=_kb, knowledge_graph=graph)
        _executor = ToolExecutor(knowledge_graph=graph, graphrag_engine=graphrag,
                                 knowledge_base=_kb)
        _pipeline = build_pipeline(
            decision_agent=_orch._decision_agent,
            tool_selection_agent=_orch._tool_selection_agent,
            tool_executor=_executor,
            synthesis_agent=_orch._synthesis_agent,
            evaluation_agent=_orch._evaluation_agent,
            reflection_agent=_orch._reflection_agent,
            format_agent=_orch._format_agent,
        )
        _pipeline.get_graph().draw_mermaid_png(output_file_path="static/pipeline_flow.png")
        logger.info("Pipeline flow diagram saved to static/pipeline_flow.png")
    except Exception as exc:
        logger.debug("Pipeline diagram export skipped: %s", exc)

    return client, graph, graphrag, advisor


kb = load_kb()
_, _, _, advisor = init_stack(kb)


# ── Session state ─────────────────────────────────────────────────────
_CRITERIA_LABELS = {
    "C1_language_compatibility": "Language Compatibility",
    "C2_api_validation": "API Validation",
    "C3_performance_load": "Performance Testing",
    "C4_cicd_integration": "CI/CD Integration",
    "C5_maintainability": "Maintainability",
    "C6_cloud_readiness": "Cloud Readiness",
    "C7_license_cost": "License & Cost",
}

_DEFAULTS = {
    "messages": [],
    "uploaded_docs_context": "",
    "case_study_context": "",
    "weight_profile": WeightProfile.default(),
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ── Sidebar ───────────────────────────────────────────────────────────
def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown("""
        <div style="
             background: linear-gradient(135deg, #eef6f2, #faf5ed);
             margin: -1rem -1rem 1rem -1rem;
             padding: 1.1rem 1.25rem 1rem;
             border-bottom: 1px solid #e5ebe7;
             border-radius: 0 0 12px 12px;
        ">
            <div style="display:flex;align-items:center;gap:0.6rem;">
                <div style="
                    width:34px;height:34px;
                    background:#e2efe9;
                    border:1px solid #cbded7;
                    border-radius:8px;
                    display:flex;align-items:center;justify-content:center;
                    font-size:1.1rem;
                ">🧭</div>
                <div>
                    <div style="color:#49635f;font-weight:800;font-size:0.88rem;line-height:1.2;">Framework Advisor</div>
                    <div style="color:#788984;font-size:0.68rem;">AI Migration Planner</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Scoring Weights ───────────────────────────────────────────
        with st.expander("⚖️ Scoring Weights", expanded=False):
            preset_names = list(PRESETS.keys())
            current_preset = st.session_state.weight_profile.profile_name.replace("_adjusted", "")
            if current_preset not in preset_names:
                current_preset = "balanced"
            selected_preset = st.selectbox(
                "Preset", preset_names,
                index=preset_names.index(current_preset),
                key="weight_preset",
            )
            if selected_preset != current_preset:
                st.session_state.weight_profile = WeightProfile.from_preset(selected_preset)
                st.rerun()

            st.markdown("**Fine-tune weights** (auto-normalised)")
            raw_weights: dict[str, float] = {}
            for cid in CRITERIA_IDS:
                label = _CRITERIA_LABELS[cid]
                current_val = st.session_state.weight_profile.weights.get(cid, 0.0)
                raw_weights[cid] = st.slider(
                    label, 0.0, 1.0, float(round(current_val, 2)),
                    step=0.05, key=f"w_{cid}",
                )

            if st.button("Apply Weights", key="apply_weights"):
                total = sum(raw_weights.values())
                if total > 0:
                    normalised = {k: v / total for k, v in raw_weights.items()}
                else:
                    normalised = {k: 1 / len(raw_weights) for k in raw_weights}
                st.session_state.weight_profile = WeightProfile(
                    weights=normalised, profile_name="custom"
                )
                st.success("Weights applied ✓")

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

        # flow_img = Path("static/pipeline_flow.png")
        # if flow_img.exists():
        #     st.markdown("---")
        #     with st.expander("🗺️ Pipeline Flow", expanded=False):
        #         st.image(str(flow_img))


# ── Main ──────────────────────────────────────────────────────────────
def main() -> None:
    _render_sidebar()

    st.markdown("""
    <div class="app-header">
        <div class="app-header-left">
            <div class="app-header-logo">&#x1F9ED;</div>
            <div>
                <p class="app-header-title">Automation Framework Advisor</p>
                <p class="app-header-sub">AI-powered migration planning &amp; coverage analysis</p>
            </div>
        </div>
        <div class="app-header-badge">POWERED BY GROQ LLM</div>
    </div>
    """, unsafe_allow_html=True)

    if not st.session_state.messages:
        st.session_state.messages.append({"role": "assistant", "content": (
            "👋 Hi! I'm your **Framework Migration Advisor**.\n\n"
            "- Ask directly: *'Compare Playwright vs Cypress'*, *'Best Python API testing framework'*\n"
            "- Upload test files in the sidebar for context-aware recommendations"
        )})

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="🧭" if msg["role"] == "assistant" else "👤"):
            st.markdown(msg["content"])

    if user_input := st.chat_input("Ask about frameworks, migrations, or coverage..."):
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user", avatar="👤"):
            st.markdown(user_input)

        wp = st.session_state.weight_profile
        weight_context = "Active scoring weights: " + ", ".join(
            f"{_CRITERIA_LABELS.get(k, k)}={v:.0%}"
            for k, v in wp.weights.items() if k in CRITERIA_IDS
        )

        try:
            with st.spinner("🤖 Agents processing…"):
                response = advisor.run_pipeline(
                    user_input,
                    st.session_state.uploaded_docs_context,
                    st.session_state.case_study_context,
                    weight_context=weight_context,
                    weight_profile=st.session_state.weight_profile,
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


if __name__ == "__main__":
    main()
