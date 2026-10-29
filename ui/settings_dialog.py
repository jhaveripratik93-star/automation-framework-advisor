"""Settings dialog — configure LLM provider API keys (Groq, Gemini, or a
custom provider) from the Streamlit UI instead of hand-editing
config/.env.

Keys are:
  1. written to os.environ immediately — GroqClient/GeminiClient re-read
     their API key fresh on every request, so this takes effect without
     an app restart;
  2. persisted to config/.env (gitignored) via python-dotenv's set_key(),
     so they survive a restart too.

Switching the active provider also clears the @st.cache_resource caches
that were built around the previously-active client (get_advisor_stack,
get_scanner) so Advisor chat / Framework Discovery pick up the new
provider on their next use, not a stale cached instance.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import streamlit as st
from dotenv import set_key

logger = logging.getLogger(__name__)

_ENV_PATH = Path(__file__).resolve().parent.parent / "config" / ".env"

_PROVIDERS = {
    "groq": {
        "label": "Groq",
        "key_env": "GROQ_API_KEY",
        "model_env": "GROQ_MODEL",
        "default_model": "qwen/qwen3.8-27b",
        "key_placeholder": "gsk_...",
    },
    "gemini": {
        "label": "Gemini",
        "key_env": "GEMINI_API_KEY",
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-2.0-flash",
        "key_placeholder": "AIza...",
    },
}


def _mask(key: str) -> str:
    if not key:
        return "— not set —"
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}{'•' * (len(key) - 8)}{key[-4:]}"


def _persist_env_var(env_var: str, value: str) -> None:
    """Set for the current process immediately, and write to config/.env
    so it survives a restart."""
    value = value.strip()
    if not value:
        return
    os.environ[env_var] = value
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _ENV_PATH.exists():
        _ENV_PATH.touch()
    set_key(str(_ENV_PATH), env_var, value)


def _activate_provider(provider_id: str) -> None:
    """Make `provider_id` the active LLM provider and drop any cached
    service objects that were built around the previous provider."""
    st.session_state["llm_provider"] = provider_id
    from services.app_services import get_advisor_stack, get_scanner
    get_advisor_stack.clear()
    get_scanner.clear()


@st.dialog("⚙️ API Key Settings", width="large")
def render_settings_dialog() -> None:
    st.caption(
        "Configure the LLM provider powering Advisor chat, Code Studio, "
        "Test Generator, and Framework Discovery. Keys are saved to "
        "`config/.env` (gitignored — never committed) and take effect "
        "immediately, no restart needed."
    )

    active = st.session_state.get("llm_provider", "groq")
    st.info(
        f"Active provider: **{_PROVIDERS.get(active, {}).get('label', active)}**",
        icon="✅",
    )

    tab_groq, tab_gemini, tab_custom, tab_teams = st.tabs(
        ["Groq", "Gemini", "Other key", "🧩 Team Profiles"]
    )

    # ── Groq ────────────────────────────────────────────────────────
    with tab_groq:
        cfg = _PROVIDERS["groq"]
        st.caption(f"Current key: `{_mask(os.getenv(cfg['key_env'], ''))}`")
        new_key = st.text_input(
            cfg["key_env"], type="password", key="settings_groq_key",
            placeholder=cfg["key_placeholder"],
        )
        new_model = st.text_input(
            "Model (optional)", key="settings_groq_model",
            value=os.getenv(cfg["model_env"], ""),
            placeholder=cfg["default_model"],
        )
        if st.button("Save & use Groq", key="settings_save_groq", use_container_width=True):
            if new_key:
                _persist_env_var(cfg["key_env"], new_key)
            if new_model:
                _persist_env_var(cfg["model_env"], new_model)
            from services.app_services import get_groq_client
            get_groq_client.clear()
            _activate_provider("groq")
            st.success("Groq configured and set as the active provider.")
            st.rerun(scope="fragment")  # refresh the dialog without closing it

    # ── Gemini ──────────────────────────────────────────────────────
    with tab_gemini:
        cfg = _PROVIDERS["gemini"]
        st.caption(f"Current key: `{_mask(os.getenv(cfg['key_env'], ''))}`")
        st.caption(
            "Text generation (chat, code conversion, code generation) is "
            "fully supported — nothing in this app relies on function/"
            "tool calling, so Gemini is a safe drop-in for every feature "
            "here."
        )
        new_key = st.text_input(
            cfg["key_env"], type="password", key="settings_gemini_key",
            placeholder=cfg["key_placeholder"],
        )
        new_model = st.text_input(
            "Model (optional)", key="settings_gemini_model",
            value=os.getenv(cfg["model_env"], ""),
            placeholder=cfg["default_model"],
        )
        if st.button("Save & use Gemini", key="settings_save_gemini", use_container_width=True):
            if new_key:
                _persist_env_var(cfg["key_env"], new_key)
            if new_model:
                _persist_env_var(cfg["model_env"], new_model)
            from services.app_services import get_gemini_client
            get_gemini_client.clear()
            _activate_provider("gemini")
            st.success("Gemini configured and set as the active provider.")
            st.rerun(scope="fragment")  # refresh the dialog without closing it

    # ── Custom / forward-compatible ──────────────────────────────────
    with tab_custom:
        st.caption(
            "Store any other provider's key for later use (e.g. OpenAI, "
            "Anthropic, Azure OpenAI). This tab only SAVES the key — "
            "actually using it for live calls requires a matching client "
            "class in `src/llm/` (the same way Groq and Gemini are wired "
            "in `services/app_services.py::get_llm_client()`); it won't "
            "silently start being used by itself."
        )
        custom_name = st.text_input(
            "Provider name", key="settings_custom_name",
            placeholder="e.g. OPENAI, ANTHROPIC, AZURE_OPENAI",
        )
        custom_key = st.text_input("API key", type="password", key="settings_custom_key")
        if st.button("Save key", key="settings_save_custom", use_container_width=True):
            slug = re.sub(r"[^A-Z0-9_]", "_", custom_name.strip().upper()).strip("_")
            if not slug:
                st.error("Enter a provider name first.")
            elif not custom_key.strip():
                st.error("Enter an API key first.")
            else:
                env_var = f"{slug}_API_KEY"
                _persist_env_var(env_var, custom_key)
                st.success(f"Saved as `{env_var}` in config/.env.")

    # ── Team Profiles ──────────────────────────────────────────────
    with tab_teams:
        _render_team_profiles_tab()

    st.markdown("---")
    if st.button("Close", key="settings_close"):
        st.rerun()  # full-app rerun — this is the one place closing the dialog is intended


# ════════════════════════════════════════════════════════════════════
# Team Profiles — one place to manage per-team conversion prompts and
# codegen style rules, so different teams sharing this deployment don't
# all get identical output. See src/team_profiles.py for the on-disk
# layout and src/conversion/prompts.py / src/codegen/style_profiles.py
# for how a profile is resolved at generation/conversion time.
# ════════════════════════════════════════════════════════════════════

# Frameworks offered for conversion-prompt editing — same list Code
# Studio presents, so editable artifacts always match what's actually
# reachable from the Framework Migration page.
_CONVERSION_FRAMEWORKS = ["Robot Framework", "Playwright", "Selenium", "K6"]


def _render_team_profiles_tab() -> None:
    from src.team_profiles import DEFAULT_PROFILE_ID, list_profiles

    st.caption(
        "Different teams have different coding standards, architecture "
        "conventions, and conversion rules. Create a named profile here "
        "to give one team its own code-conversion prompts and code-"
        "generation style — without changing behaviour for anyone still "
        "on the default profile."
    )

    profiles = list_profiles()
    profile_ids = [p.id for p in profiles]
    by_id = {p.id: p for p in profiles}

    active_id = st.session_state.get("active_team_profile", DEFAULT_PROFILE_ID)
    if active_id not in by_id:
        active_id = DEFAULT_PROFILE_ID
        st.session_state["active_team_profile"] = active_id

    tab_manage, tab_create = st.tabs(["📋 Manage a Profile", "➕ Create New Profile"])

    with tab_manage:
        _render_manage_profile(profiles, profile_ids, by_id, active_id)

    with tab_create:
        _render_create_profile(profile_ids, by_id, active_id)


def _render_manage_profile(profiles, profile_ids, by_id, active_id: str) -> None:
    """Everything here operates on exactly ONE profile — the one chosen
    below. Previously each sub-section (edit content / reference docs /
    delete) had its OWN independent profile picker, so what you selected
    in one didn't match what another showed. Now there's a single
    selection, shared by all three."""
    # Selection persists via its own widget key, seeded from the active
    # profile only the first time this renders. A pending-value handoff
    # (set by "create profile" / "delete profile" elsewhere) is consumed
    # HERE, before the selectbox widget below is instantiated — Streamlit
    # forbids writing a widget's session_state key after that widget has
    # already rendered in the same script run, so any code that wants to
    # change this selection must stage it in "_settings_team_pending_select"
    # instead of writing "settings_team_selected" directly.
    if "_settings_team_pending_select" in st.session_state:
        st.session_state["settings_team_selected"] = st.session_state.pop("_settings_team_pending_select")
    elif "settings_team_selected" not in st.session_state or st.session_state["settings_team_selected"] not in by_id:
        st.session_state["settings_team_selected"] = active_id

    selected = st.selectbox(
        "Profile",
        profile_ids,
        format_func=lambda pid: (
            f"⭐ {by_id[pid].display_name}" if pid == active_id else by_id[pid].display_name
        ),
        key="settings_team_selected",
    )
    profile = by_id[selected]

    if profile.description:
        st.caption(profile.description)

    col_status, col_action, col_export = st.columns([3, 2, 2])
    with col_status:
        if selected == active_id:
            st.success("⭐ Active for Framework Migration & Test Generator", icon="✅")
        else:
            st.info("Not active — viewing/editing only affects this profile's files.", icon="ℹ️")
    with col_action:
        if selected != active_id:
            if st.button("⭐ Set as active", key="settings_team_set_active", use_container_width=True):
                st.session_state["active_team_profile"] = selected
                st.rerun(scope="fragment")
    with col_export:
        if not profile.is_default:
            from src.team_profiles import export_profile_zip
            st.download_button(
                "⬇ Export", data=export_profile_zip(selected),
                file_name=f"{selected}.team-profile.zip", mime="application/zip",
                key="settings_team_export", use_container_width=True,
                help="Download this profile as a .zip to share with another "
                     "person or machine — import it on their end to use it.",
            )

    st.markdown("---")

    tab_edit, tab_docs, tab_delete = st.tabs(
        ["✏️ Edit Content", "📄 Reference Documents", "🗑 Delete"]
    )
    with tab_edit:
        _render_profile_editor(selected)
    with tab_docs:
        _render_reference_docs(selected)
    with tab_delete:
        _render_delete_profile(selected, profile, active_id)


def _render_delete_profile(selected: str, profile, active_id: str) -> None:
    from src.team_profiles import DEFAULT_PROFILE_ID, delete_profile

    if profile.is_default:
        st.info("The default profile is built-in and can't be deleted.", icon="ℹ️")
        return

    st.warning(f"This permanently deletes every file belonging to **{profile.display_name}**.")
    confirm = st.checkbox(
        f"I understand — permanently delete '{profile.display_name}'",
        key=f"settings_team_delete_confirm_{selected}",
    )
    if st.button("🗑 Delete this profile", key=f"settings_team_delete_btn_{selected}",
                 disabled=not confirm, type="primary"):
        try:
            delete_profile(selected)
            if active_id == selected:
                st.session_state["active_team_profile"] = DEFAULT_PROFILE_ID
            st.session_state["_settings_team_pending_select"] = DEFAULT_PROFILE_ID
            st.success(f"Deleted '{profile.display_name}'.")
            st.rerun(scope="fragment")  # refresh the dialog without closing it
        except ValueError as exc:
            st.error(str(exc))


def _render_create_profile(profile_ids, by_id, active_id: str) -> None:
    from src.team_profiles import create_profile, create_profile_from_repo

    tab_clone, tab_scan, tab_import = st.tabs(
        ["📋 Clone from existing profile", "🔍 Learn from an existing repo", "📦 Import from file"]
    )

    with tab_clone:
        st.caption(
            "The new profile starts as a full editable copy of the "
            "profile you clone from — nothing is blank."
        )
        new_name = st.text_input("Team / profile name", key="settings_team_new_name",
                                  placeholder="e.g. Team Alpha")
        new_desc = st.text_area("Description (optional)", key="settings_team_new_desc",
                                 height=68, placeholder="e.g. Alpha squad — API-first, pytest, no page objects")
        clone_from = st.selectbox(
            "Clone starting point from", profile_ids,
            index=profile_ids.index(active_id),
            format_func=lambda pid: by_id[pid].display_name,
            key="settings_team_clone_from",
        )
        if st.button("Create profile", key="settings_team_create", type="primary"):
            if not new_name.strip():
                st.error("Enter a team/profile name first.")
            else:
                try:
                    created = create_profile(
                        new_name, new_name.strip(), new_desc.strip(), clone_from=clone_from,
                    )
                    st.session_state["_settings_team_pending_select"] = created.id
                    st.success(
                        f"Created profile '{created.display_name}'. Switch to "
                        f"'📋 Manage a Profile' to review or edit it — it's "
                        f"already selected there."
                    )
                    st.rerun(scope="fragment")  # refresh the dialog without closing it
                except ValueError as exc:
                    st.error(str(exc))

    with tab_scan:
        st.caption(
            "Scans an existing repo (local folder path or a GitHub "
            "URL) and drafts a profile from what it finds — detected "
            "test framework, base URLs, env var names, and existing "
            "fixture/helper/page-object naming. This is a DRAFT: the "
            "detected facts are added as a clearly-labelled 'Detected "
            "From Your Repo' section you review and edit afterwards, "
            "not a finished, trusted profile."
        )
        scan_name = st.text_input("Team / profile name", key="settings_team_scan_name",
                                   placeholder="e.g. Team Alpha")
        scan_desc = st.text_area("Description (optional)", key="settings_team_scan_desc", height=68)
        scan_source = st.text_input(
            "Repo path or GitHub URL", key="settings_team_scan_source",
            placeholder="C:/projects/my-app  or  https://github.com/org/repo",
        )
        scan_base = st.selectbox(
            "Base everything else on", profile_ids,
            index=profile_ids.index(active_id),
            format_func=lambda pid: by_id[pid].display_name,
            key="settings_team_scan_base",
        )
        if st.button("🔍 Scan & create", key="settings_team_scan_create", type="primary"):
            if not scan_name.strip():
                st.error("Enter a team/profile name first.")
            elif not scan_source.strip():
                st.error("Enter a repo path or GitHub URL first.")
            else:
                try:
                    with st.spinner(f"Scanning {scan_source}…"):
                        created, report = create_profile_from_repo(
                            scan_name, scan_name.strip(), scan_source.strip(),
                            description=scan_desc.strip(), base_profile=scan_base,
                        )
                    st.session_state["_settings_team_pending_select"] = created.id
                    st.success(
                        f"Created profile '{created.display_name}'. Switch to "
                        f"'📋 Manage a Profile' to review or edit it — it's "
                        f"already selected there."
                    )
                    st.text_area("What was detected", value=report, height=180, disabled=True)
                    st.rerun(scope="fragment")  # refresh the dialog without closing it
                except ValueError as exc:
                    st.error(str(exc))

    with tab_import:
        from src.team_profiles import import_profile_zip

        st.caption(
            "Import a profile someone else exported (⬇ Export, in the "
            "'📋 Manage a Profile' tab) — gets this profile working on "
            "your own system with no manual file copying."
        )
        import_file = st.file_uploader(
            "Profile .zip", type=["zip"], key="settings_team_import_file",
        )
        import_name_override = st.text_input(
            "Save as (optional — leave blank to use the name from the file)",
            key="settings_team_import_name",
            placeholder="e.g. team_alpha",
        )
        existing_conflict = (
            import_name_override.strip()
            and _slug_matches_existing(import_name_override.strip(), profile_ids)
        )
        overwrite = False
        if existing_conflict:
            st.warning(f"A profile named '{import_name_override.strip()}' already exists.")
            overwrite = st.checkbox("Overwrite it", key="settings_team_import_overwrite")

        if st.button("📦 Import", key="settings_team_import_btn", type="primary",
                     disabled=import_file is None):
            try:
                created = import_profile_zip(
                    import_file.read(),
                    team_id=import_name_override.strip() or None,
                    overwrite=overwrite,
                )
                st.session_state["_settings_team_pending_select"] = created.id
                st.success(
                    f"Imported profile '{created.display_name}' (id: `{created.id}`). "
                    f"Switch to '📋 Manage a Profile' to review it — it's already selected there."
                )
                st.rerun(scope="fragment")  # refresh the dialog without closing it
            except ValueError as exc:
                st.error(str(exc))


def _slug_matches_existing(name: str, profile_ids: list[str]) -> bool:
    from src.team_profiles import slugify_id
    return slugify_id(name) in profile_ids


def _render_profile_editor(team_id: str) -> None:
    """Edit one text artifact (conversion prompt or codegen style rule)
    belonging to `team_id`, section by section instead of one opaque blob."""
    from src.codegen.agent_config import FRAMEWORK_CONTEXT
    from src.codegen.style_profiles import get_framework_context
    from src.conversion.prompts import slugify
    from src.prompt_sections import migrate_label_lines
    from src.team_profiles import DEFAULT_PROFILE_ID, codegen_style_dir, conversion_prompts_dir

    artifact_kind = st.radio(
        "What to edit",
        ["Conversion — base system prompt", "Conversion — syntax rules (target framework)",
         "Conversion — pair rules (from → to)", "Codegen — framework style rules"],
        key=f"settings_team_artifact_kind_{team_id}",
    )

    prompts_dir = conversion_prompts_dir(team_id)
    path = None
    section_kind: str
    can_write = True
    exists = True

    if artifact_kind == "Conversion — base system prompt":
        path = prompts_dir / "base_system_prompt.md"
        section_kind = "base_prompt"
        exists = path.is_file()

    elif artifact_kind == "Conversion — syntax rules (target framework)":
        fw = st.selectbox(
            "Target framework", _CONVERSION_FRAMEWORKS,
            format_func=lambda f: f if (prompts_dir / "syntax_rules" / f"{slugify(f)}.md").is_file()
            else f"{f}  🆕 not yet created",
            key=f"settings_team_syntax_fw_{team_id}",
        )
        path = prompts_dir / "syntax_rules" / f"{slugify(fw)}.md"
        section_kind = "syntax_rules"
        exists = path.is_file()

    elif artifact_kind == "Conversion — pair rules (from → to)":
        col_from, col_to = st.columns(2)
        with col_from:
            from_fw = st.selectbox("From", _CONVERSION_FRAMEWORKS, key=f"settings_team_pair_from_{team_id}")
        with col_to:
            to_options = [f for f in _CONVERSION_FRAMEWORKS if f != from_fw]
            to_fw = st.selectbox(
                "To", to_options,
                format_func=lambda f: f if (prompts_dir / "pair_rules" / f"{slugify(from_fw)}__to__{slugify(f)}.md").is_file()
                else f"{f}  🆕 not yet created",
                key=f"settings_team_pair_to_{team_id}",
            )
        path = prompts_dir / "pair_rules" / f"{slugify(from_fw)}__to__{slugify(to_fw)}.md"
        section_kind = "pair_rules"
        exists = path.is_file()

    else:  # Codegen — framework style rules
        fw_key = st.selectbox("Framework key", sorted(FRAMEWORK_CONTEXT.keys()),
                               key=f"settings_team_codegen_fw_{team_id}")
        section_kind = "codegen_style"
        if team_id == DEFAULT_PROFILE_ID:
            # No on-disk file backs the default profile's codegen style —
            # it's the hardcoded FRAMEWORK_CONTEXT dict in agent_config.py.
            # Editing that safely means editing Python, not writing a file
            # here — so this is view-only; create a named profile to get
            # an editable, file-backed copy of it.
            can_write = False
        else:
            style_dir = codegen_style_dir(team_id) or (prompts_dir.parent / "codegen_style")
            path = style_dir / f"{fw_key}.md"
            exists = path.is_file()

    if not can_write:
        st.info(
            "The default profile's codegen style lives in "
            "`src/codegen/agent_config.py` (Python), not a file — shown "
            "here read-only, broken into the same sections a named "
            "profile's copy would have. Create a named profile to get an "
            "editable copy of this text.",
            icon="ℹ️",
        )
        current_text = get_framework_context(fw_key, team_id=team_id)
        _render_readonly_sections(migrate_label_lines(current_text), team_id, artifact_kind)
        return

    _render_section_editor(path, section_kind, exists, key_prefix=f"{team_id}_{artifact_kind}")


def _render_readonly_sections(sections, team_id: str, artifact_kind: str) -> None:
    for i, s in enumerate(sections):
        with st.expander(f"## {s.heading}" if s.heading else "(intro)", expanded=(i == 0)):
            st.text_area(
                "Content (read-only)", value=s.body, height=120, disabled=True,
                key=f"settings_team_ro_{team_id}_{artifact_kind}_{i}",
                label_visibility="collapsed",
            )


def _render_section_editor(path, section_kind: str, exists: bool, key_prefix: str) -> None:
    """Section-by-section editor for one prompt file.

    If the file doesn't exist yet, starts from a labelled starter
    template for `section_kind` instead of a blank box — so creating a
    brand-new syntax/pair/style file means filling in clearly-explained
    boxes, not writing free text from nothing.
    """
    from src.prompt_sections import PromptSection, join_sections, section_template, split_sections
    from src.team_profiles import read_text_artifact, write_text_artifact

    state_key = f"settings_team_sections_{key_prefix}_{path.name}"

    # Saving calls st.rerun() immediately after st.success() — on a
    # fragment rerun the success message would be shown and then wiped
    # by that SAME rerun before it's ever painted to the browser, making
    # a working save look like it silently did nothing. Stash the
    # confirmation and show it on the render that actually survives.
    just_saved_key = f"{state_key}_just_saved"
    if st.session_state.pop(just_saved_key, None):
        st.success(
            f"Saved `{path.name}`. Takes effect on the next conversion/generation — no restart needed.",
            icon="💾",
        )

    if state_key not in st.session_state:
        if exists:
            st.session_state[state_key] = split_sections(read_text_artifact(path))
        else:
            st.success(
                "This file doesn't exist yet for this profile — starting "
                "from a template. Fill in each section below and save to "
                "create it.",
                icon="🆕",
            )
            st.session_state[state_key] = section_template(section_kind)

    sections: list[PromptSection] = st.session_state[state_key]

    if not sections:
        st.caption("Empty file. Add a section below to start writing it.")

    for i, s in enumerate(sections):
        label = f"## {s.heading}" if s.heading else "(intro — no heading)"
        with st.expander(label, expanded=(i == 0)):
            new_heading = s.heading
            if s.heading is not None:
                new_heading = st.text_input(
                    "Section heading", value=s.heading,
                    key=f"{state_key}_heading_{i}",
                )
            new_body = st.text_area(
                "Section content", value=s.body, height=160,
                key=f"{state_key}_body_{i}", label_visibility="collapsed",
            )
            sections[i] = PromptSection(heading=new_heading, body=new_body)
            if st.button("🗑 Remove this section", key=f"{state_key}_remove_{i}"):
                sections.pop(i)
                st.rerun(scope="fragment")  # refresh the dialog without closing it

    with st.expander("➕ Add new section", expanded=False):
        new_title = st.text_input("New section heading", key=f"{state_key}_new_title")
        if st.button("Add section", key=f"{state_key}_add"):
            if new_title.strip():
                sections.append(PromptSection(heading=new_title.strip(), body=""))
                st.rerun(scope="fragment")  # refresh the dialog without closing it
            else:
                st.error("Enter a heading for the new section first.")

    st.markdown("---")
    if st.button("💾 Save file", key=f"{state_key}_save", type="primary"):
        write_text_artifact(path, join_sections(sections))
        # Clear the per-section widget state (heading/body text areas) too —
        # not just `state_key` itself. Otherwise, after re-reading fresh
        # sections from disk on the next render, each text_area's `value=`
        # is ignored in favour of its OLD cached session_state entry (this
        # is normal Streamlit widget behaviour once a key already exists),
        # so edits that changed the number of sections could show stale
        # content under the wrong index.
        for i in range(len(sections)):
            st.session_state.pop(f"{state_key}_heading_{i}", None)
            st.session_state.pop(f"{state_key}_body_{i}", None)
        del st.session_state[state_key]
        st.session_state[just_saved_key] = True
        st.rerun(scope="fragment")  # refresh the dialog without closing it


def _render_reference_docs(team_id: str) -> None:
    """Upload/list/delete architecture or design documents for one team
    profile. Extracted text is appended to that team's conversion and
    codegen system prompts (see src/team_profiles.py::reference_docs_context,
    wired into src/conversion/prompts.py and src/codegen/style_profiles.py)."""
    from src.doc_extraction import SUPPORTED_EXTENSIONS
    from src.team_profiles import (
        DEFAULT_PROFILE_ID,
        add_reference_doc,
        delete_reference_doc,
        list_reference_docs,
    )

    if team_id == DEFAULT_PROFILE_ID:
        st.info(
            "The default profile has no reference-documents location — "
            "create a named profile to attach architecture/design docs.",
            icon="ℹ️",
        )
        return

    st.caption(
        "Upload an architecture doc, design doc, or coding-standards doc "
        "for this team. Its extracted text is appended to the system "
        "prompt for every conversion and code-generation call made under "
        "this profile — grounding context, not a strict rule file."
    )

    docs = list_reference_docs(team_id)
    if docs:
        st.markdown(f"**{len(docs)} document(s) attached:**")
        for p in docs:
            col_name, col_del = st.columns([5, 1])
            with col_name:
                st.markdown(f"📄 `{p.name}`")
            with col_del:
                if st.button("🗑", key=f"settings_team_doc_del_{team_id}_{p.name}"):
                    delete_reference_doc(team_id, p.name)
                    st.rerun(scope="fragment")  # refresh the dialog without closing it
    else:
        st.caption("No documents attached yet.")

    uploaded = st.file_uploader(
        f"Upload a document ({', '.join(SUPPORTED_EXTENSIONS)})",
        type=[e.lstrip(".") for e in SUPPORTED_EXTENSIONS],
        key=f"settings_team_doc_upload_{team_id}",
    )
    if uploaded is not None and st.button("➕ Add document", key=f"settings_team_doc_add_{team_id}"):
        try:
            path = add_reference_doc(team_id, uploaded.name, uploaded.read())
            st.success(f"Added `{path.name}` — extracted and ready to use.")
            st.rerun(scope="fragment")  # refresh the dialog without closing it
        except ValueError as exc:
            st.error(str(exc))
