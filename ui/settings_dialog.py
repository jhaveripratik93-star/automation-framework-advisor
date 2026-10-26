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

    tab_groq, tab_gemini, tab_custom = st.tabs(["Groq", "Gemini", "Other key"])

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
            st.rerun()

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
            st.rerun()

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

    st.markdown("---")
    if st.button("Close", key="settings_close"):
        st.rerun()
