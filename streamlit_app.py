"""Automation Framework Migration & Coverage Advisor — Streamlit UI.

Tabs:
  🧭 Advisor      — LLM agent chat (existing)
  🐍 Code Studio  — Python interpreter + test-case converter
  📊 Coverage     — Excel test-case upload, coverage matrix, CI/CD export
"""
from __future__ import annotations

import logging
import sys
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
    except Exception as exc:
        logger.debug("Pipeline diagram export skipped: %s", exc)

    return client, graph, graphrag, advisor


kb = load_kb()
_client, _graph, _graphrag, advisor = init_stack(kb)

# ── Constants ─────────────────────────────────────────────────────────
_CRITERIA_LABELS = {
    "C1_language_compatibility": "Language Compatibility",
    "C2_api_validation":         "API Validation",
    "C3_performance_load":       "Performance Testing",
    "C4_cicd_integration":       "CI/CD Integration",
    "C5_maintainability":        "Maintainability",
    "C6_cloud_readiness":        "Cloud Readiness",
    "C7_license_cost":           "License & Cost",
}

_KNOWN_FRAMEWORKS = [
    "Playwright", "Cypress", "Selenium WebDriver", "Robot Framework",
    "WebdriverIO", "Puppeteer", "TestCafe", "Appium", "Karate",
    "K6", "Locust", "REST Assured",
]

_PLAYGROUND_TEMPLATE = """\
# Converted test will appear here after using the Convert button above.
# You can also write or paste any Python code and click ▶ Run.

print("Hello from the Framework Advisor sandbox!")
"""

# ── Session state defaults ────────────────────────────────────────────
_DEFAULTS: dict = {
    "messages": [],
    "uploaded_docs_context": "",
    "case_study_context": "",
    "weight_profile": WeightProfile.default(),
    # Code Studio
    "playground_code": _PLAYGROUND_TEMPLATE,
    "playground_output": "",
    "playground_install_log": "",
    "playground_exit_code": 0,
    "playground_timed_out": False,
    "playground_packages": [],
    "last_converted_code": "",
    "auto_run_pending": False,
    # Coverage
    "excel_test_cases": [],
    "excel_summary": "",
    "coverage_result": "",
    "cicd_yaml": "",
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
                normalised = (
                    {k: v / total for k, v in raw_weights.items()} if total > 0
                    else {k: 1 / len(raw_weights) for k in raw_weights}
                )
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
                "Case study (txt/md)", type=["txt", "md"], key="case_study",
            )
            if cs_file and st.button("📖 Parse", key="parse_cs"):
                try:
                    text = cs_file.read().decode("utf-8")
                    st.session_state.case_study_context = text
                    st.success(f"✓ {len(text)} chars parsed")
                except Exception as exc:
                    st.error(str(exc))


# ── Tab: Advisor ──────────────────────────────────────────────────────
def _render_advisor_tab() -> None:
    if not st.session_state.messages:
        st.session_state.messages.append({"role": "assistant", "content": (
            "👋 Hi! I'm your **Framework Migration Advisor**.\n\n"
            "- *'Compare Robot Framework vs Playwright'*\n"
            "- *'Best Python API testing framework'*\n"
            "- *'Migrate from Selenium to Playwright'*\n"
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


# ── Tab: Code Studio ─────────────────────────────────────────────────
def _render_code_studio_tab() -> None:
    import re as _re
    from src.tools.code_sandbox import (
        run_code, install_and_run, get_venv_info,
        detect_required_packages, uninstall_packages,
    )

    # ── Converter section ─────────────────────────────────────────────
    st.markdown("### 🔄 Test Case Converter")
    st.caption(
        "Convert test cases from any framework to Python. "
        "Converted code loads into the editor and runs automatically."
    )

    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        from_fw = st.selectbox("From framework", _KNOWN_FRAMEWORKS, index=3, key="convert_from")
    with col2:
        to_options = [f for f in _KNOWN_FRAMEWORKS if f != from_fw]
        to_fw = st.selectbox("To framework (Python)", to_options, index=0, key="convert_to")
    with col3:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        do_convert = st.button("🔄 Convert", key="btn_convert", use_container_width=True)

    source_code = st.text_area(
        "Paste source test code here",
        height=160,
        placeholder=f"Paste your {from_fw} test code here…",
        key="convert_source",
    )

    if do_convert:
        if not source_code.strip():
            st.warning("Paste some source test code first.")
        else:
            with st.spinner(f"Converting {from_fw} → {to_fw} (Python)…"):
                try:
                    from src.tools.executor import ToolExecutor
                    executor = ToolExecutor(
                        knowledge_base=kb,
                        knowledge_graph=_graph,
                        graphrag_engine=_graphrag,
                    )
                    executor._llm = _client
                    full_result = executor.execute("convert_test_cases", {
                        "source_code": source_code,
                        "from_framework": from_fw,
                        "to_framework": to_fw,
                        "language": "python",
                    })
                    # Extract first ```python ... ``` block as runnable code
                    code_blocks = _re.findall(r"```(?:python)?\n(.*?)```", full_result, _re.DOTALL)
                    runnable = code_blocks[0].strip() if code_blocks else full_result
                    st.session_state.playground_code = runnable
                    st.session_state.last_converted_code = full_result
                    # Auto-run the converted code
                    st.session_state.auto_run_pending = True
                    st.success(f"✓ Converted — running in interpreter…")
                    with st.expander("📄 Full conversion output (gaps + CI/CD)", expanded=False):
                        st.markdown(full_result)
                except Exception as exc:
                    st.error(f"Conversion failed: {exc}")
                    logger.error("Conversion error: %s", exc)

    # ── Interpreter section ───────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🐍 Python Interpreter")

    # Venv status badge
    venv_info = get_venv_info()
    if venv_info["exists"]:
        st.caption(
            f"🟢 Sandbox venv ready · {venv_info['count']} packages installed · "
            f"`{venv_info['venv_path']}`"
        )
    else:
        st.caption("🟡 Sandbox venv will be created on first run.")

    st.caption(
        "Required packages are detected from your imports and installed automatically. "
        f"Execution timeout: {60}s."
    )

    code = st.text_area(
        "Python code",
        value=st.session_state.playground_code,
        height=300,
        key="editor_code",
        label_visibility="collapsed",
    )


    # Detect packages needed by current code and show as a log
    required_pkgs = detect_required_packages(code) if code.strip() else []
    if required_pkgs:
        pkg_lines = "\n".join(f"  [required] {p}" for p in required_pkgs)
        st.code(f"[packages] The following will be installed on Run:\n{pkg_lines}", language="text")

    run_col, clear_col, end_col, _ = st.columns([1, 1, 1, 4])
    with run_col:
        run_clicked = st.button("▶ Run", key="btn_run", use_container_width=True)
    with clear_col:
        if st.button("🗑 Clear", key="btn_clear", use_container_width=True):
            st.session_state.playground_code = _PLAYGROUND_TEMPLATE
            st.session_state.playground_output = ""
            st.session_state.playground_install_log = ""
            st.session_state.auto_run_pending = False
            st.rerun()
    with end_col:
        end_clicked = st.button("🔴 END", key="btn_end", use_container_width=True)

    # END: uninstall all session-installed packages
    if end_clicked:
        session_pkgs = st.session_state.get("playground_packages", [])
        if session_pkgs:
            with st.spinner("Uninstalling packages…"):
                uninstall_log = uninstall_packages(session_pkgs)
            st.session_state.playground_packages = []
            st.session_state.playground_install_log = uninstall_log
            st.session_state.playground_output = ""
            st.code(uninstall_log, language="text")
        else:
            st.info("No session packages to uninstall.")

    # Trigger: manual Run button OR auto-run after conversion
    should_run = run_clicked or st.session_state.get("auto_run_pending", False)

    if should_run:
        run_code_val = code if run_clicked else st.session_state.playground_code
        if not run_code_val.strip():
            st.warning("Nothing to run.")
        else:
            st.session_state.playground_code = run_code_val
            st.session_state.auto_run_pending = False

            pkgs_to_install = detect_required_packages(run_code_val)

            if pkgs_to_install:
                install_preview = "[packages] Installing required packages:\n" + \
                    "\n".join(f"  [install] {p}" for p in pkgs_to_install)
                st.code(install_preview, language="text")

            status_placeholder = st.empty()
            status_placeholder.info("⏳ Running…")

            sandbox_result = install_and_run(run_code_val, pkgs_to_install)

            # Accumulate session packages (for END cleanup)
            existing = st.session_state.get("playground_packages", [])
            new_pkgs = [p for p in sandbox_result.packages_installed if p not in existing]
            st.session_state.playground_packages = existing + new_pkgs

            st.session_state.playground_install_log = sandbox_result.install_log
            st.session_state.playground_output = (
                sandbox_result.stdout or sandbox_result.stderr or "(no output)"
            )
            st.session_state.playground_exit_code = sandbox_result.exit_code
            st.session_state.playground_timed_out = sandbox_result.timed_out

            if sandbox_result.timed_out:
                status_placeholder.error("⏱ Timed out after 60s")
            elif sandbox_result.success:
                pkg_note = (
                    f" · installed: {', '.join(sandbox_result.packages_installed)}"
                    if sandbox_result.packages_installed else ""
                )
                status_placeholder.success(f"✅ Ran successfully{pkg_note}")
            else:
                status_placeholder.error(f"❌ Exit code {sandbox_result.exit_code}")

    # ── Output display ────────────────────────────────────────────────
    install_log = st.session_state.get("playground_install_log", "")
    output      = st.session_state.get("playground_output", "")

    if install_log:
        with st.expander("📦 Package log", expanded=False):
            st.code(install_log, language="text")

    if output:
        st.markdown("**Output:**")
        exit_code = st.session_state.get("playground_exit_code", 0)
        timed_out = st.session_state.get("playground_timed_out", False)
        if timed_out:
            st.error(f"⏱ Timed out\n\n{output}")
        else:
            st.code(output, language="text")


def _render_coverage_tab() -> None:
    st.markdown("### 📊 Test Case Coverage Analyser")
    st.caption(
        "Upload an Excel file with your test cases. "
        "The tool maps each test to framework capabilities, shows a coverage matrix, "
        "recommends the best framework combination, and generates a CI/CD pipeline you can download."
    )

    # ── Excel upload ──────────────────────────────────────────────────
    with st.expander("📥 Upload Test Cases (Excel)", expanded=True):
        st.markdown(
            "Expected columns *(order & case don't matter)*: "
            "`Test ID` · `Description` · `Capability` · `Steps` · `Expected Result`"
        )
        xl_file = st.file_uploader(
            "Excel file (.xlsx)", type=["xlsx"], key="xl_upload"
        )

        col_parse, col_reset = st.columns([1, 1])
        with col_parse:
            parse_clicked = st.button("📂 Parse Excel", key="btn_parse_xl", use_container_width=True)
        with col_reset:
            if st.button("🗑 Clear", key="btn_clear_xl", use_container_width=True):
                st.session_state.excel_test_cases = []
                st.session_state.excel_summary = ""
                st.session_state.coverage_result = ""
                st.session_state.cicd_yaml = ""
                st.rerun()

        if parse_clicked:
            if xl_file is None:
                st.warning("Upload an Excel file first.")
            else:
                from src.tools.excel_parser import parse_excel_test_cases, summarise_excel
                raw = xl_file.read()
                test_cases = parse_excel_test_cases(raw)
                if not test_cases:
                    st.error("Could not parse any test cases. Check column headers.")
                else:
                    st.session_state.excel_test_cases = test_cases
                    st.session_state.excel_summary = summarise_excel(test_cases)
                    st.session_state.coverage_result = ""
                    st.session_state.cicd_yaml = ""
                    st.success(f"✓ {len(test_cases)} test cases loaded")

        if st.session_state.excel_summary:
            st.markdown(st.session_state.excel_summary)

    # ── Framework filter ──────────────────────────────────────────────
    if st.session_state.excel_test_cases:
        st.markdown("---")
        st.markdown("#### ⚙️ Analysis Options")

        selected_fws = st.multiselect(
            "Frameworks to include (leave empty = all)",
            options=_KNOWN_FRAMEWORKS,
            default=[],
            key="coverage_fw_filter",
        )

        cicd_platform = st.selectbox(
            "CI/CD platform for export",
            ["GitHub Actions", "GitLab CI", "Azure DevOps"],
            key="cicd_platform",
        )

        analyse_clicked = st.button("🔍 Analyse Coverage", key="btn_analyse_cov", use_container_width=False)

        if analyse_clicked:
            with st.spinner("Analysing coverage across frameworks…"):
                try:
                    from src.tools.executor import ToolExecutor
                    executor = ToolExecutor(knowledge_base=kb, knowledge_graph=_graph)
                    result = executor.execute("analyze_test_case_coverage", {
                        "test_cases": st.session_state.excel_test_cases,
                        "frameworks": selected_fws if selected_fws else None,
                    })
                    st.session_state.coverage_result = result

                    # Build CI/CD YAML for the top recommended framework
                    cicd_yaml = _build_cicd_yaml(
                        st.session_state.excel_test_cases,
                        cicd_platform,
                    )
                    st.session_state.cicd_yaml = cicd_yaml
                except Exception as exc:
                    st.error(f"Analysis failed: {exc}")
                    logger.error("Coverage analysis error: %s", exc)

    # ── Coverage results ──────────────────────────────────────────────
    if st.session_state.coverage_result:
        st.markdown("---")
        st.markdown(st.session_state.coverage_result)

    # ── CI/CD export ──────────────────────────────────────────────────
    if st.session_state.cicd_yaml:
        st.markdown("---")
        st.markdown("### ⬇️ CI/CD Pipeline Export")

        platform = st.session_state.get("cicd_platform", "GitHub Actions")
        filename_map = {
            "GitHub Actions": "github-actions.yml",
            "GitLab CI":      ".gitlab-ci.yml",
            "Azure DevOps":   "azure-pipelines.yml",
        }
        filename = filename_map.get(platform, "ci-pipeline.yml")

        st.code(st.session_state.cicd_yaml, language="yaml")
        st.download_button(
            label=f"⬇ Download {filename}",
            data=st.session_state.cicd_yaml,
            file_name=filename,
            mime="text/yaml",
            key="btn_download_cicd",
        )

    # ── Manual test case entry fallback ──────────────────────────────
    if not st.session_state.excel_test_cases:
        st.markdown("---")
        with st.expander("✏️ Or enter test cases manually (JSON)", expanded=False):
            st.caption(
                'Format: `[{"id":"TC001","description":"Login","required_capability":"ui automation"}]`'
            )
            manual_json = st.text_area("Test cases JSON", height=150, key="manual_tc_json")
            if st.button("▶ Analyse Manual Input", key="btn_manual_analyse"):
                import json
                try:
                    tcs = json.loads(manual_json)
                    if not isinstance(tcs, list):
                        raise ValueError("Must be a JSON array")
                    from src.tools.executor import ToolExecutor
                    executor = ToolExecutor(knowledge_base=kb, knowledge_graph=_graph)
                    result = executor.execute("analyze_test_case_coverage", {"test_cases": tcs})
                    st.session_state.coverage_result = result
                    st.session_state.cicd_yaml = _build_cicd_yaml(tcs, "GitHub Actions")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Invalid input: {exc}")


# ── CI/CD YAML builder ────────────────────────────────────────────────
def _build_cicd_yaml(test_cases: list[dict], platform: str) -> str:
    """Generate a CI/CD YAML string for the given platform."""
    caps = list({tc.get("required_capability", "ui automation") for tc in test_cases})
    needs_playwright = any(c in caps for c in ["ui automation", "network mocking", "visual testing"])
    needs_pytest     = True
    needs_locust     = any("performance" in c or "load" in c for c in caps)
    needs_appium     = any("mobile" in c for c in caps)

    test_cmd_parts = []
    install_parts  = ["pip install -r requirements.txt"]

    if needs_playwright:
        install_parts.append("playwright install --with-deps chromium")
        test_cmd_parts.append("pytest tests/ -m ui")
    if needs_locust:
        test_cmd_parts.append("locust -f tests/load_test.py --headless -u 10 -r 2 --run-time 1m")
    if needs_appium:
        test_cmd_parts.append("pytest tests/ -m mobile")
    if not test_cmd_parts:
        test_cmd_parts.append("pytest tests/")

    if platform == "GitHub Actions":
        return _gh_actions_yaml(install_parts, test_cmd_parts)
    elif platform == "GitLab CI":
        return _gitlab_yaml(install_parts, test_cmd_parts)
    else:
        return _azure_yaml(install_parts, test_cmd_parts)


def _gh_actions_yaml(install_parts: list[str], test_cmds: list[str]) -> str:
    install_str = "\n".join(f"          {cmd}" for cmd in install_parts)
    test_str    = "\n".join(f"          {cmd}" for cmd in test_cmds)
    return f"""\
name: Automated Tests

on:
  push:
    branches: [main, develop]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
{install_str}

      - name: Run tests
        run: |
{test_str}

      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: test-results
          path: |
            test-results/
            playwright-report/
"""


def _gitlab_yaml(install_parts: list[str], test_cmds: list[str]) -> str:
    install_str = "\n".join(f"    - {cmd}" for cmd in install_parts)
    test_str    = "\n".join(f"    - {cmd}" for cmd in test_cmds)
    return f"""\
stages:
  - install
  - test

install:
  stage: install
  image: python:3.11
  script:
{install_str}
  cache:
    paths:
      - .venv/

test:
  stage: test
  image: python:3.11
  script:
{test_str}
  artifacts:
    when: always
    paths:
      - test-results/
      - playwright-report/
"""


def _azure_yaml(install_parts: list[str], test_cmds: list[str]) -> str:
    install_str = "\n".join(f"          {cmd}" for cmd in install_parts)
    test_str    = "\n".join(f"          {cmd}" for cmd in test_cmds)
    return f"""\
trigger:
  branches:
    include:
      - main
      - develop

pool:
  vmImage: ubuntu-latest

steps:
  - task: UsePythonVersion@0
    inputs:
      versionSpec: '3.11'

  - script: |
{install_str}
    displayName: Install dependencies

  - script: |
{test_str}
    displayName: Run tests

  - task: PublishTestResults@2
    condition: always()
    inputs:
      testResultsFiles: '**/test-results/*.xml'
"""


# ── App header ────────────────────────────────────────────────────────
def _render_header() -> None:
    st.markdown("""
    <div class="app-header">
        <div class="app-header-left">
            <div class="app-header-logo">&#x1F9ED;</div>
            <div>
                <p class="app-header-title">Automation Framework Advisor</p>
                <p class="app-header-sub">AI-powered migration planning · Python interpreter · Coverage analysis</p>
            </div>
        </div>
        <div class="app-header-badge">POWERED BY GROQ LLM</div>
    </div>
    """, unsafe_allow_html=True)


# ── Main ──────────────────────────────────────────────────────────────
def main() -> None:
    _render_sidebar()
    _render_header()

    tab_advisor, tab_studio, tab_coverage = st.tabs([
        "🧭 Advisor",
        "🐍 Code Studio",
        "📊 Coverage Analyser",
    ])

    with tab_advisor:
        _render_advisor_tab()

    with tab_studio:
        _render_code_studio_tab()

    with tab_coverage:
        _render_coverage_tab()


if __name__ == "__main__":
    main()
