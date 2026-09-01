"""Shared UI components."""
from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st


# ── CSS ───────────────────────────────────────────────────────────────
@st.cache_data
def _load_css() -> str:
    return Path("static/styles.css").read_text(encoding="utf-8")


def inject_css() -> None:
    st.markdown(f"<style>{_load_css()}</style>", unsafe_allow_html=True)


# ── Simple spinner banner (no fake sleeps) ────────────────────────────
def render_thinking(label: str = "Analysing your request…") -> None:
    """Single honest 'thinking' banner — no simulated progress steps."""
    dots = "".join(
        f'<span class="pulse-dot" style="animation-delay:{i*0.2}s;margin-right:3px"></span>'
        for i in range(3)
    )
    st.markdown(
        f'<div class="llm-loading">'
        f'<div class="llm-loading-header"><div>{dots}</div>'
        f'<div><div class="llm-loading-title">{label}</div>'
        f'<div class="llm-loading-sub">This may take 10–30 seconds</div>'
        f'</div></div></div>',
        unsafe_allow_html=True,
    )


# ── Copy button ───────────────────────────────────────────────────────
_copy_counter: dict[str, int] = {"n": 0}


def render_copy_button(text: str) -> None:
    _copy_counter["n"] += 1
    btn_id = f"copy-btn-{_copy_counter['n']}"
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    js = (
        "(function(){"
        f"var el=document.getElementById('{btn_id}');"
        "var txt=atob(el.dataset.b64);"
        "function mark(){"
        f"el.innerHTML='<svg width=\\'13\\' height=\\'13\\' viewBox=\\'0 0 24 24\\' fill=\\'none\\' stroke=\\'currentColor\\' stroke-width=\\'2.5\\' stroke-linecap=\\'round\\' stroke-linejoin=\\'round\\'><polyline points=\\'20 6 9 17 4 12\\'></polyline></svg> Copied';"
        "el.classList.add('copied');"
        f"setTimeout(function(){{el.innerHTML='<svg width=\\'13\\' height=\\'13\\' viewBox=\\'0 0 24 24\\' fill=\\'none\\' stroke=\\'currentColor\\' stroke-width=\\'2\\' stroke-linecap=\\'round\\' stroke-linejoin=\\'round\\'><rect x=\\'9\\' y=\\'9\\' width=\\'13\\' height=\\'13\\' rx=\\'2\\' ry=\\'2\\'></rect><path d=\\'M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1\\'></path></svg> Copy';el.classList.remove('copied');}},1800);"
        "}"
        "if(navigator.clipboard&&window.isSecureContext){"
        "navigator.clipboard.writeText(txt).then(mark).catch(function(){"
        "var ta=document.createElement('textarea');"
        "ta.value=txt;ta.style.position='fixed';ta.style.opacity='0';"
        "document.body.appendChild(ta);ta.select();document.execCommand('copy');"
        "document.body.removeChild(ta);mark();"
        "});"
        "}else{"
        "var ta=document.createElement('textarea');"
        "ta.value=txt;ta.style.position='fixed';ta.style.opacity='0';"
        "document.body.appendChild(ta);ta.select();document.execCommand('copy');"
        "document.body.removeChild(ta);mark();"
        "}"
        "})()"
    )
    html = (
        f'<div class="copy-btn-row">'
        f'<button class="copy-btn" id="{btn_id}" data-b64="{b64}" title="Copy response" onclick="{js}">'
        f'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;margin-right:4px">'
        f'<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>'
        f'<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>'
        f'</svg> Copy</button></div>'
    )
    st.markdown(html, unsafe_allow_html=True)
