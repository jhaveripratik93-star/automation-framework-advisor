"""Code Studio page — single and multi-file test conversion."""
from __future__ import annotations

import io
import logging
import re as _re
import zipfile
from pathlib import Path

import streamlit as st

from ui.components import render_thinking

logger = logging.getLogger(__name__)


_SUPPORTED_FRAMEWORKS = ["Robot Framework", "Playwright", "Selenium"]


def render() -> None:
    from services.app_services import get_advisor_stack

    st.markdown("""
    <div class="page-header">
        <div class="page-header-left">
            <div class="breadcrumb"><span>Home</span><span class="breadcrumb-sep">›</span><span>Framework Migration</span></div>
            <div class="page-title">🔄 Framework Migration</div>
            <div class="page-subtitle">Convert test suites between frameworks — Python only</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        from_fw = st.selectbox("From framework", _SUPPORTED_FRAMEWORKS, index=0, key="convert_from")
    with col2:
        to_options = [f for f in _SUPPORTED_FRAMEWORKS if f != from_fw]
        to_fw = st.selectbox("To framework", to_options, index=0, key="convert_to")

    if from_fw and to_fw:
        st.markdown(
            f'<div class="breadcrumb" style="margin:0.25rem 0 0.75rem;">'
            f'<span>{from_fw}</span><span class="breadcrumb-sep">→</span>'
            f'<span style="color:#2d7d6f;font-weight:700;">{to_fw}</span></div>',
            unsafe_allow_html=True,
        )

    mode = st.radio(
        "Conversion mode",
        ["Single file (paste)", "Multi-file (upload repo)"],
        horizontal=True,
        key="converter_mode",
    )
    st.markdown("---")

    v = st.session_state.studio_widget_key_v

    if mode == "Single file (paste)":
        _single_file_mode(from_fw, to_fw, v, get_advisor_stack)
    else:
        _multi_file_mode(from_fw, to_fw, v, get_advisor_stack)


def _single_file_mode(from_fw, to_fw, v, get_stack) -> None:
    source_code = st.text_area(
        "Paste source test code here", height=200,
        placeholder=f"Paste your {from_fw} test code here…",
        key=f"convert_source_{v}",
    )
    do_convert = st.button("🔄 Convert", key="btn_convert", type="primary")

    # Restore persisted result
    if st.session_state.get("last_converted_code") and not do_convert:
        _show_single_result()
        return

    if do_convert:
        if not source_code.strip():
            st.warning("Paste some source test code first.")
            return

        loading_slot = st.empty()
        with loading_slot.container():
            render_thinking(f"Converting {from_fw} → {to_fw}…")

        try:
            from src.tools.executor import ToolExecutor
            from services.app_services import get_kb, get_advisor_stack
            kb = get_kb()
            _client, _graph, _graphrag, _ = get_stack()
            executor = ToolExecutor(knowledge_base=kb, knowledge_graph=_graph, graphrag_engine=_graphrag)
            executor._llm = _client

            full_result = executor.execute("convert_test_cases", {
                "source_code": source_code,
                "from_framework": from_fw,
                "to_framework": to_fw,
                "language": "python",
            })

            loading_slot.empty()

            # Split on any CI/CD marker variant the LLM may produce
            _cicd_re = _re.compile(
                r"(#\s*={0,3}\s*CI/?CD\s*INTEGRATION[^\n]*)",
                _re.IGNORECASE,
            )
            gap_marker = "### \u26a0\ufe0f Capabilities Requiring"

            cicd_match = _cicd_re.search(full_result)
            if cicd_match:
                pre_cicd   = full_result[:cicd_match.start()]
                cicd_block = full_result[cicd_match.start():]
            else:
                pre_cicd   = full_result
                cicd_block = ""

            gap_split = pre_cicd.split(gap_marker, 1)
            code_part = gap_split[0]
            gap_block = (gap_marker + gap_split[1]) if len(gap_split) > 1 else ""

            if gap_marker in cicd_block and not gap_block:
                cicd_gap   = cicd_block.split(gap_marker, 1)
                cicd_block = cicd_gap[0]
                gap_block  = gap_marker + cicd_gap[1]

            # Strip any markdown code fence the LLM may have added despite instructions
            code_blocks = _re.findall(r"```(?:[a-zA-Z]*)\n(.*?)```", code_part, _re.DOTALL)
            runnable = code_blocks[0].strip() if code_blocks else code_part.strip()

            # Strip the leading header line added by _convert_test_cases ("## 🔄 Converted…")
            runnable = _re.sub(r"^##.*?\n", "", runnable).strip()

            st.session_state.last_converted_code = runnable
            st.session_state.studio_source_code  = source_code
            st.session_state.studio_gap_block    = gap_block
            st.session_state.studio_cicd_block   = cicd_block
            st.session_state.studio_to_fw        = to_fw
            # Render immediately — no rerun needed
            _show_single_result()

        except Exception as exc:
            loading_slot.empty()
            st.error(f"Conversion failed: {exc}")
            logger.error("Conversion error: %s", exc)


_RUN_INSTRUCTIONS: dict[str, tuple[str, str, str]] = {
    "robot framework": (
        "robotframework>=7.0\nrobotframework-seleniumlibrary>=6.0\nSelenium>=4.0",
        "robot tests/",
        "pip install -r requirements.txt\nplaywright install  # if using Browser library instead",
    ),
    "playwright": (
        "playwright>=1.40.0\npytest-playwright>=0.4.0\npytest>=7.0",
        "pytest tests/ -v",
        "pip install -r requirements.txt\nplaywright install",
    ),
    "selenium": (
        "selenium>=4.0\npytest>=7.0\nwebdriver-manager>=4.0",
        "pytest tests/ -v",
        "pip install -r requirements.txt",
    ),
}


def _get_run_instructions(to_fw_lower: str) -> tuple[str, str, str]:
    """Return (requirements_txt, run_cmd, install_cmd) for the target framework."""
    for key, val in _RUN_INSTRUCTIONS.items():
        if key in to_fw_lower:
            return val
    return "pytest>=7.0", "pytest tests/ -v", "pip install -r requirements.txt"


def _parse_cicd_block(raw: str, to_fw: str) -> dict:
    """Extract install and run commands from the LLM CI/CD comment block.

    Returns {"install": str, "run": str, "yaml": str} where yaml is the
    full GitHub Actions step block (comment lines stripped of leading #).
    """
    lines = raw.splitlines()
    install, run, yaml_lines = "", "", []

    for line in lines:
        # Strip leading comment chars and whitespace
        clean = _re.sub(r"^\s*#\s?", "", line).strip()
        # Decode HTML entities (&#39; -> ')
        clean = clean.replace("&#39;", "'").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        if not clean:
            continue
        low = clean.lower()
        if low.startswith("pip install") or low.startswith("pip3 install"):
            install = clean
        elif (low.startswith("robot ") or low.startswith("pytest ") or
              low.startswith("python -m pytest") or low.startswith("python -m robot")):
            run = clean
        yaml_lines.append(clean)

    # Fallback run commands per framework
    if not run:
        fw_lower = to_fw.lower()
        if "robot" in fw_lower:
            run = "robot tests/"
        elif "playwright" in fw_lower:
            run = "pytest tests/ --tb=short"
        elif "selenium" in fw_lower:
            run = "pytest tests/ --tb=short"

    return {"install": install, "run": run, "yaml": "\n".join(yaml_lines)}


def _show_single_result() -> None:
    runnable   = st.session_state.last_converted_code
    cicd_block = st.session_state.get("studio_cicd_block", "")
    to_fw      = st.session_state.get("studio_to_fw", "")

    code_lang, ext, mime = _target_lang_display(to_fw)

    with st.expander("📄 Converted Test Code", expanded=True):
        st.code(runnable, language=code_lang)
        st.download_button(
            label="⬇ Download converted file",
            data=runnable,
            file_name=f"test_converted{ext}",
            mime=mime,
            key="btn_dl_single",
        )

    # ── How to run ───────────────────────────────────────────────────
    to_fw_lower = (to_fw or "").lower()
    req, run_cmd, install_cmd = _get_run_instructions(to_fw_lower)

    with st.expander("📋 Requirements & Execution Guide", expanded=True):
        col_req, col_run = st.columns(2)
        with col_req:
            st.markdown("**`requirements.txt`**")
            st.code(req, language="text")
        with col_run:
            st.markdown("**Install & Run**")
            st.code(install_cmd + "\n" + run_cmd, language="bash")


_LANG_DISPLAY = {
    "python":     ("python",      ".py",    "text/x-python"),
    "javascript": ("javascript",  ".js",    "text/javascript"),
    "typescript": ("typescript",  ".ts",    "text/typescript"),
    "java":       ("java",        ".java",  "text/x-java"),
    "c#":         ("csharp",      ".cs",    "text/plain"),
    "ruby":       ("ruby",        ".rb",    "text/x-ruby"),
    "go":         ("go",          ".go",    "text/x-go"),
    "robot":      ("robotframework", ".robot", "text/plain"),
}

# Map framework name → language key (overrides YAML lookup for known frameworks)
_FW_LANG_OVERRIDE = {
    "robot framework": "robot",
    "playwright":      "python",
    "selenium":        "python",
}


def _target_lang_display(to_fw_name: str):
    """Return (streamlit_lang, ext, mime) for the target framework."""
    override = _FW_LANG_OVERRIDE.get((to_fw_name or "").lower())
    if override:
        return _LANG_DISPLAY[override]
    try:
        from services.app_services import get_kb
        from src.tools.executor import ToolExecutor
        kb = get_kb()
        ex = ToolExecutor(knowledge_base=kb)
        fw = ex._resolve_framework(to_fw_name)
        lang, _eco = ex._resolve_target_language(fw, to_fw_name)
        return _LANG_DISPLAY.get(lang, _LANG_DISPLAY["python"])
    except Exception:
        return _LANG_DISPLAY["python"]


# Source file extensions we convert (everything else in a ZIP is ignored)
_SOURCE_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".robot",
                ".feature", ".yml", ".yaml", ".rb", ".cs"}
# Directories to skip when extracting a ZIP repo
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv",
              "dist", "build", ".idea", ".vscode", "target", "bin", "obj"}
_MAX_ZIP_FILES = 60  # safety cap to avoid huge repos overwhelming the LLM


def _extract_source_files(uploaded_files) -> list[dict]:
    """Turn uploaded files (including ZIPs) into a flat list of
    {"filename": relative_path, "content": text} dicts.

    - ZIP archives are expanded, preserving relative paths.
    - Non-source files, skip-dirs, and binary files are ignored.
    - Capped at _MAX_ZIP_FILES to keep conversions manageable.
    """
    import io
    import zipfile
    from pathlib import PurePosixPath

    payload: list[dict] = []

    for uf in uploaded_files:
        name = uf.name
        if name.lower().endswith(".zip"):
            try:
                data = uf.read()
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        path = info.filename
                        parts = PurePosixPath(path).parts
                        # Skip unwanted directories
                        if any(seg in _SKIP_DIRS for seg in parts):
                            continue
                        ext = PurePosixPath(path).suffix.lower()
                        if ext not in _SOURCE_EXTS:
                            continue
                        try:
                            content = zf.read(info).decode("utf-8", errors="replace")
                        except Exception:
                            continue
                        payload.append({"filename": path, "content": content})
                        if len(payload) >= _MAX_ZIP_FILES:
                            st.warning(
                                f"Reached the {_MAX_ZIP_FILES}-file limit; "
                                "remaining files in the ZIP were skipped."
                            )
                            return payload
            except zipfile.BadZipFile:
                st.warning(f"Could not read {name}: not a valid ZIP archive")
        else:
            try:
                payload.append({
                    "filename": name,
                    "content": uf.read().decode("utf-8", errors="replace"),
                })
            except Exception as exc:
                st.warning(f"Could not read {name}: {exc}")

    return payload


def _multi_file_mode(from_fw, to_fw, v, get_stack) -> None:
    uploaded_files = st.file_uploader(
        "Upload test files, or a .zip of your whole framework/repo",
        type=["py", "js", "ts", "java", "robot", "feature", "yml", "yaml", "zip"],
        accept_multiple_files=True,
        key=f"multi_convert_files_{v}",
        help="Upload .py / .robot / .feature files OR a ZIP of your entire repo.",
    )

    if not uploaded_files and not st.session_state.get("multi_convert_result"):
        st.markdown("""
        <div class="empty-state">
            <span class="empty-state-icon">📂</span>
            <div class="empty-state-title">Upload your test repository</div>
            <div class="empty-state-desc">
                Upload individual test files, or a <b>.zip of your entire framework</b> to
                preserve the folder structure. Each file is converted with shared cross-file
                context, and helper scripts are auto-generated for capability gaps.
            </div>
            <span class="empty-state-hint">⬆ Use the file uploader above</span>
        </div>
        """, unsafe_allow_html=True)

    # Expand any uploaded ZIPs into individual source files (preserving paths)
    files_payload: list[dict] = []
    if uploaded_files:
        files_payload = _extract_source_files(uploaded_files)
        st.caption(
            f"{len(files_payload)} source file(s) ready: "
            + ", ".join(f["filename"] for f in files_payload[:8])
            + (" …" if len(files_payload) > 8 else "")
        )

    do_multi = st.button(
        f"🔄 Convert {len(files_payload)} file(s)",
        key="btn_multi_convert",
        disabled=not files_payload,
        type="primary",
    )

    # Rate-limit guard: cap the number of files converted per run
    if files_payload and len(files_payload) > 20:
        st.warning(
            f"⚠️ {len(files_payload)} files detected. Only the first 20 will be converted "
            "to avoid rate limits. Consider uploading a subset of your test files."
        )
        files_payload = files_payload[:20]

    if do_multi and files_payload:
        loading_slot = st.empty()
        with loading_slot.container():
            render_thinking(f"Converting {len(files_payload)} file(s): {from_fw} → {to_fw}…")

        try:
            from src.tools.executor import ToolExecutor
            from services.app_services import get_kb, get_advisor_stack
            kb = get_kb()
            _client, _graph, _graphrag, _ = get_stack()
            executor = ToolExecutor(knowledge_base=kb, knowledge_graph=_graph, graphrag_engine=_graphrag)
            executor._llm = _client

            result = executor.convert_multi_file(
                files=files_payload,
                from_framework=from_fw,
                to_framework=to_fw,
            )

            loading_slot.empty()
            st.session_state["multi_convert_result"] = result
            st.session_state["multi_convert_from"] = from_fw
            st.session_state["multi_convert_to"] = to_fw
            # Render immediately
            _show_multi_result(result, to_fw)

        except Exception as exc:
            loading_slot.empty()
            st.error(f"Multi-file conversion failed: {exc}")
            logger.error("Multi-file conversion error: %s", exc)
        return

    result = st.session_state.get("multi_convert_result")
    if result:
        to_label = st.session_state.get("multi_convert_to", to_fw)
        _show_multi_result(result, to_label)


def _show_multi_result(result: dict, to_label: str) -> None:
    code_lang, ext, mime = _target_lang_display(to_label)
    to_fw_lower = (to_label or "").lower()
    req, run_cmd, install_cmd = _get_run_instructions(to_fw_lower)

    st.markdown("---")
    st.markdown(result["summary"])
    st.markdown("### 📂 Converted Project Files")
    all_files: dict[str, str] = {}

    if result.get("conftest"):
        with st.expander("📄 `conftest.py`", expanded=False):
            st.code(result["conftest"], language="python")
            st.download_button("⬇ conftest.py", result["conftest"],
                               file_name="conftest.py", mime="text/x-python", key="dl_conftest")
        all_files["conftest.py"] = result["conftest"]

    if result.get("requirements"):
        with st.expander("📄 `requirements.txt`", expanded=False):
            st.code(result["requirements"], language="text")
            st.download_button("⬇ requirements.txt", result["requirements"],
                               file_name="requirements.txt", mime="text/plain", key="dl_requirements")
        all_files["requirements.txt"] = result["requirements"]

    if result["converted"]:
        st.markdown("#### 🧪 Converted Test Files")
        for path, code in sorted(result["converted"].items()):
            with st.expander(f"📄 `{path}`", expanded=False):
                st.code(code, language=code_lang)
                st.download_button(f"⬇ {Path(path).name}", code,
                                   file_name=Path(path).name, mime=mime,
                                   key=f"dl_{path.replace('/', '_')}")
            all_files[path] = code

    # ── Requirements & Execution Guide ────────────────────────────────
    with st.expander("📋 Requirements & Execution Guide", expanded=True):
        col_req, col_run = st.columns(2)
        with col_req:
            st.markdown("**`requirements.txt`**")
            st.code(req, language="text")
        with col_run:
            st.markdown("**Install & Run**")
            st.code(install_cmd + "\n" + run_cmd, language="bash")

    st.markdown("---")
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, code in all_files.items():
            zf.writestr(path, code)
    zip_buf.seek(0)
    folder_name = f"converted_{to_label.lower().replace(' ', '_')}"
    st.download_button(
        label=f"⬇ Download all as {folder_name}.zip",
        data=zip_buf.getvalue(),
        file_name=f"{folder_name}.zip",
        mime="application/zip",
        key="btn_dl_zip",
    )
