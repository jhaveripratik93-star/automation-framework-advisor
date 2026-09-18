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

            cicd_marker = "# === CI/CD INTEGRATION ==="
            gap_marker  = "### ⚠️ Capabilities Requiring"

            cicd_split = full_result.split(cicd_marker, 1)
            pre_cicd   = cicd_split[0]
            cicd_block = (cicd_marker + cicd_split[1]) if len(cicd_split) > 1 else ""

            gap_split  = pre_cicd.split(gap_marker, 1)
            code_part  = gap_split[0]
            gap_block  = (gap_marker + gap_split[1]) if len(gap_split) > 1 else ""

            if gap_marker in cicd_block and not gap_block:
                cicd_gap   = cicd_block.split(gap_marker, 1)
                cicd_block = cicd_gap[0]
                gap_block  = gap_marker + cicd_gap[1]

            code_blocks = _re.findall(r"```(?:python)?\n(.*?)```", code_part, _re.DOTALL)
            runnable = code_blocks[0].strip() if code_blocks else code_part.strip()

            st.session_state.last_converted_code = runnable
            st.session_state.studio_source_code  = source_code
            st.session_state.studio_gap_block    = gap_block
            st.session_state.studio_cicd_block   = cicd_block
            # Render immediately — no rerun needed
            _show_single_result()

        except Exception as exc:
            loading_slot.empty()
            st.error(f"Conversion failed: {exc}")
            logger.error("Conversion error: %s", exc)


def _show_single_result() -> None:
    runnable   = st.session_state.last_converted_code
    gap_block  = st.session_state.get("studio_gap_block", "")
    cicd_block = st.session_state.get("studio_cicd_block", "")

    with st.expander("🐍 Converted Test Code", expanded=True):
        st.code(runnable, language="python")
        st.download_button(
            label="⬇ Download converted file",
            data=runnable,
            file_name="test_converted.py",
            mime="text/x-python",
            key="btn_dl_single",
        )
    if gap_block.strip():
        with st.expander("⚠️ Capability Gaps & Helper Scripts", expanded=False):
            st.markdown(gap_block.strip())
    if cicd_block.strip():
        with st.expander("⚙️ CI/CD Integration", expanded=False):
            st.markdown(cicd_block.strip())


_TEST_EXTS = {"py", "robot", "resource", "feature"}
_SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".venv", "venv", "dist", "build", "target"}


def _extract_files_from_zip(zip_bytes: bytes) -> list[dict]:
    """Extract all test files from a ZIP archive, skipping build/vendor dirs."""
    files = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = _re.split(r"[/\\]", info.filename)
            if any(p in _SKIP_DIRS for p in parts):
                continue
            ext = info.filename.rsplit(".", 1)[-1].lower() if "." in info.filename else ""
            if ext not in _TEST_EXTS:
                continue
            try:
                content = zf.read(info.filename).decode("utf-8", errors="replace")
                # Strip the top-level repo folder prefix (e.g. repo-main/)
                rel_path = "/".join(parts[1:]) if len(parts) > 1 else parts[0]
                files.append({"filename": rel_path, "content": content})
            except Exception:
                pass
    return files


def _multi_file_mode(from_fw, to_fw, v, get_stack) -> None:
    uploaded_files = st.file_uploader(
        "Upload test files or a repo ZIP",
        type=["py", "robot", "resource", "feature", "zip"],
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
                Upload individual test files <strong>or a ZIP of your entire repo</strong>.
                Each file is converted with shared cross-file context.
                Helper scripts are auto-generated for capability gaps.
            </div>
            <span class="empty-state-hint">⬆ Use the file uploader above</span>
        </div>
        """, unsafe_allow_html=True)

    files_payload: list[dict] = []
    zip_count = 0

    if uploaded_files:
        for uf in uploaded_files:
            if uf.name.lower().endswith(".zip"):
                extracted = _extract_files_from_zip(uf.read())
                files_payload.extend(extracted)
                zip_count += len(extracted)
            else:
                try:
                    files_payload.append({
                        "filename": uf.name,
                        "content": uf.read().decode("utf-8", errors="replace"),
                    })
                except Exception as exc:
                    st.warning(f"Could not read {uf.name}: {exc}")

        if zip_count:
            st.caption(f"📦 ZIP extracted {zip_count} test file(s). "
                       f"Total: {len(files_payload)} file(s) ready for conversion.")
        else:
            st.caption(f"{len(files_payload)} file(s) selected: " +
                       ", ".join(f.name for f in uploaded_files))

    do_multi = st.button(
        f"🔄 Convert {len(files_payload)} file(s)",
        key="btn_multi_convert",
        disabled=not files_payload,
        type="primary",
    )

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
    st.markdown("---")
    st.markdown(result["summary"])
    st.markdown("### 📂 Converted Project Files")
    all_files: dict[str, str] = {}

    with st.expander("📄 `conftest.py`", expanded=False):
        st.code(result["conftest"], language="python")
        st.download_button("⬇ conftest.py", result["conftest"],
                           file_name="conftest.py", mime="text/x-python", key="dl_conftest")
    all_files["conftest.py"] = result["conftest"]

    with st.expander("📄 `requirements.txt`", expanded=False):
        st.code(result["requirements"], language="text")
        st.download_button("⬇ requirements.txt", result["requirements"],
                           file_name="requirements.txt", mime="text/plain", key="dl_requirements")
    all_files["requirements.txt"] = result["requirements"]

    if result["converted"]:
        st.markdown("#### 🧪 Converted Test Files")
        for path, code in sorted(result["converted"].items()):
            with st.expander(f"📄 `{path}`", expanded=False):
                st.code(code, language="python")
                st.download_button(f"⬇ {Path(path).name}", code,
                                   file_name=Path(path).name, mime="text/x-python",
                                   key=f"dl_{path.replace('/', '_')}")
            all_files[path] = code

    if result["helpers"]:
        st.markdown("#### 🔧 Gap-Bridging Helper Scripts")
        st.caption(f"Auto-generated to cover capabilities {to_label} cannot handle natively.")
        for path, code in sorted(result["helpers"].items()):
            with st.expander(f"📄 `{path}`", expanded=False):
                st.code(code, language="python")
                st.download_button(f"⬇ {Path(path).name}", code,
                                   file_name=Path(path).name, mime="text/x-python",
                                   key=f"dl_{path.replace('/', '_')}")
            all_files[path] = code

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
