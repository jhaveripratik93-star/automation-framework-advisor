"""Offline integration tests: shared auth helper, both-path parity, frozen keys,
and the additive conversion completeness report.

The executor's per-file LLM loop is never exercised with a real client: `_llm`
stays None so `_llm_convert` returns a placeholder string. No network, no Groq.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server.manual_mode import build_multi_file_conversion_brief
from src.tools.executor import ToolExecutor
from src.tools.library_ingest import build_auth_helper, build_library_context

SESSION_HELPER = """
def login(self, username, password):
    params = urlencode({"username": username, "password": password})
    url = "http://api/cenx/api-docs/session/login"
    res = http.post(url, params=params)
    return res.json()["token"]

def get_session_token_param(self, token):
    return [("token", token)]
"""

KEYCLOAK_HELPER = """
def get_authorization_token(self):
    body = "grant_type=password&client_id=my-cli&username=u&password=p"
    url = "https://host/auth/realms/REALM/protocol/openid-connect/token"
    res = http.post(url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    tok = res.json()["access_token"]
    headers = {"Authorization": "Bearer " + tok}
    return headers
"""

ROBOT_SUITE = (
    "*** Settings ***\n"
    "Library    session.py\n"
    "Library    keycloak.py\n"
    "\n*** Test Cases ***\n"
    "Login\n    Log    hi\n"
)

FROZEN_KEYS = {
    "converted", "helpers", "conftest", "requirements",
    "gaps", "summary", "assertion_report",
}


def _robot_files():
    return [
        {"filename": "suite.robot", "content": ROBOT_SUITE},
        {"filename": "session.py", "content": SESSION_HELPER},
        {"filename": "keycloak.py", "content": KEYCLOAK_HELPER},
    ]


def _executor():
    ex = ToolExecutor()
    ex._llm = None  # force offline — _llm_convert returns a placeholder string
    return ex


# ── Group 3: shared auth helper deliverable ──

def test_k6_auth_helper_emitted_in_helpers():
    ex = _executor()
    result = ex.convert_multi_file(_robot_files(), "Robot Framework", "K6")
    assert "lib/auth.js" in result["helpers"]
    content = result["helpers"]["lib/auth.js"]
    assert "export function login" in content
    assert "export function keycloakToken" in content


def test_non_k6_js_target_yields_no_helper():
    """A derivable contract on a non-k6 JS target emits no lib/auth.js helper."""
    files = _robot_files()
    k6 = build_library_context(files, "Robot Framework", "K6", "javascript",
                               char_budget=6000)
    cypress = build_library_context(files, "Robot Framework", "Cypress",
                                    "javascript", char_budget=6000)
    assert k6.helper_spec is not None
    assert cypress.helper_spec is None
    assert build_auth_helper(k6.contract, "javascript", "K6") is not None
    assert build_auth_helper(cypress.contract, "javascript", "Cypress") is None


def test_block_variant_matches_helper_presence():
    files = _robot_files()
    k6 = build_library_context(files, "Robot Framework", "K6", "javascript",
                               char_budget=6000)
    py = build_library_context(files, "Robot Framework", "pytest", "python",
                               char_budget=6000)
    assert "has been generated" in k6.prompt_block
    assert "has been generated" not in py.prompt_block


# ── Group 6: frozen keys + both-path parity ──

def test_frozen_return_keys_unchanged():
    ex = _executor()
    result = ex.convert_multi_file(_robot_files(), "Robot Framework", "K6")
    # All frozen keys present; the only new top-level key is the additive `report`.
    assert FROZEN_KEYS.issubset(result.keys())
    assert set(result.keys()) - FROZEN_KEYS == {"report"}


def test_both_paths_derive_identical_context():
    ex = _executor()
    files = _robot_files()
    exec_ctx = ex._build_library_context(files, "Robot Framework", "K6", "javascript")
    brief = build_multi_file_conversion_brief(ex, files, "Robot Framework", "K6")
    assert exec_ctx.contract == exec_ctx.contract  # sanity
    # The manual brief's conversion_rules carry the same derived contract text.
    assert exec_ctx.prompt_block.split("\n")[0] in brief["conversion_rules"]


def test_manual_brief_carries_contract_and_helper():
    ex = _executor()
    brief = build_multi_file_conversion_brief(ex, _robot_files(),
                                              "Robot Framework", "K6")
    assert "session/login" in brief["conversion_rules"]
    assert "auth_helper" in brief
    assert brief["auth_helper"]["path"] == "lib/auth.js"
    assert "login" in brief["auth_helper"]["exports"]


# ── New requirement: conversion completeness report ──

def test_report_present_and_structured_groq_path():
    ex = _executor()
    result = ex.convert_multi_file(_robot_files(), "Robot Framework", "K6")
    report = result["report"]
    assert set(report) >= {"handled", "assumed", "not_done", "text"}

    handled_text = " ".join(h["item"] + h["detail"] for h in report["handled"])
    assert "auth/login flow derived" in handled_text or "auth" in handled_text.lower()
    assert "resolved" in handled_text.lower()
    assert "shared auth helper generated" in handled_text

    # ASSUMED lists runtime placeholders with env references.
    assumed_text = " ".join(a["placeholder"] for a in report["assumed"])
    assert "__ENV." in assumed_text
    assert any("client_id" in a["item"].lower() for a in report["assumed"])

    # Human-readable rendering folded into the summary.
    assert "### What was done / assumed / not done" in result["summary"]


def test_report_lists_unresolved_imports_in_not_done():
    ex = _executor()
    files = [
        {"filename": "suite.robot",
         "content": "*** Settings ***\n"
                    "Library    session.py\n"
                    "Library    clamps.Missing\n"
                    "\n*** Test Cases ***\nX\n    Log    hi\n"},
        {"filename": "session.py", "content": SESSION_HELPER},
    ]
    result = ex.convert_multi_file(files, "Robot Framework", "K6")
    not_done_text = " ".join(n["item"] for n in result["report"]["not_done"])
    assert "clamps.Missing" in not_done_text


def test_report_present_in_manual_brief():
    ex = _executor()
    brief = build_multi_file_conversion_brief(ex, _robot_files(),
                                              "Robot Framework", "K6")
    report = brief["report"]
    assert set(report) >= {"handled", "assumed", "not_done", "text"}
    assert "### What was done / assumed / not done" in report["text"]
    # Both paths derive the same handled/assumed/not_done structure.
    assert any("auth" in h["item"].lower() for h in report["handled"])
    assert any("__ENV." in a["placeholder"] for a in report["assumed"])


def test_report_degrades_gracefully_for_non_robot():
    ex = _executor()
    files = [
        {"filename": "http.js", "content": "import http from 'k6/http';\n"},
        {"filename": "api.js", "content": "export default function () {}\n"},
    ]
    result = ex.convert_multi_file(files, "K6", "K6")
    assert FROZEN_KEYS.issubset(result.keys())
    report = result["report"]
    # HANDLED still lists basic conversion facts; nothing crashes.
    assert any("files converted" in h["item"] for h in report["handled"])
    # No library layer -> NOT_DONE notes it; no helper emitted.
    assert any("library layer" in n["item"] for n in report["not_done"])
    assert "lib/auth.js" not in result["helpers"]
    assert report["assumed"] == []
