"""Offline tests for auth/transport contract reasoning + assumption flagging.

No LLM, no disk (beyond the KB the executor auto-loads), no network. Covers
Group 2 (contract reasoning) and Group 4 (assumption flagging).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools.library_ingest import (
    AuthContract,
    ResolvedImport,
    RobotImport,
    build_assumptions,
    build_library_context,
    derive_contract,
)

# ── Synthetic helper sources (CENX-neutral shapes) ──
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


def _resolved(name, content):
    return ResolvedImport(
        imp=RobotImport(kind="Library", target=name),
        filename=name,
        content=content,
    )


def test_query_param_empty_body_login():
    contract = derive_contract([_resolved("session.py", SESSION_HELPER)])
    assert contract.credential_location == "query_param"
    assert contract.body_shape == "empty"


def test_later_call_token_query_param():
    contract = derive_contract([_resolved("session.py", SESSION_HELPER)])
    assert contract.later_call_auth == "query_param:token"


def test_secondary_oidc_system():
    contract = derive_contract([_resolved("keycloak.py", KEYCLOAK_HELPER)])
    assert len(contract.secondary_systems) == 1
    sec = contract.secondary_systems[0]
    assert sec.token_response_field == "access_token"
    assert sec.attaches_as == "bearer_header"


def test_token_field_binding_when_both_present():
    contract = derive_contract([
        _resolved("session.py", SESSION_HELPER),
        _resolved("keycloak.py", KEYCLOAK_HELPER),
    ])
    assert contract.token_response_field == "token"
    assert contract.secondary_systems[0].token_response_field == "access_token"


def test_single_token_field_binds_primary():
    contract = derive_contract([_resolved("session.py", SESSION_HELPER)])
    assert contract.token_response_field == "token"
    assert contract.secondary_systems == ()


def test_primary_login_endpoint_value_on_evidence_input():
    contract = derive_contract([
        _resolved("session.py", SESSION_HELPER),
        _resolved("keycloak.py", KEYCLOAK_HELPER),
    ])
    assert contract.login_endpoint.endswith("/session/login")
    assert contract.login_method == "POST"
    assert contract.secondary_systems[0].token_endpoint.endswith(
        "openid-connect/token")


def test_later_call_auth_routes_by_helper():
    contract = derive_contract([
        _resolved("session.py", SESSION_HELPER),
        _resolved("keycloak.py", KEYCLOAK_HELPER),
    ])
    assert contract.later_call_auth == "query_param:token"
    assert contract.secondary_systems[0].attaches_as == "bearer_header"


def test_contract_fields_present_in_system_prompt():
    from src.tools.executor import ToolExecutor
    ex = ToolExecutor()
    files = [
        {"filename": "suite.robot",
         "content": "*** Settings ***\nLibrary    session.py\nLibrary    keycloak.py\n"},
        {"filename": "session.py", "content": SESSION_HELPER},
        {"filename": "keycloak.py", "content": KEYCLOAK_HELPER},
    ]
    lib_ctx = build_library_context(files, "Robot Framework", "K6", "javascript",
                                    char_budget=6000)
    prompt = ex._build_system_prompt(
        "Robot Framework", "K6", "", target_lang="javascript",
        library_block=lib_ctx.prompt_block)
    assert "session/login" in prompt
    assert "query_param" in prompt
    assert "token" in prompt
    assert "oidc" in prompt
    assert "Base paths" in prompt


def test_no_auth_content_leaves_prompt_unchanged():
    from src.tools.executor import ToolExecutor
    ex = ToolExecutor()
    base = ex._build_system_prompt("Robot Framework", "K6", "",
                                   target_lang="javascript")
    withlib = ex._build_system_prompt("Robot Framework", "K6", "",
                                      target_lang="javascript", library_block="")
    assert base == withlib


def test_assumptions_are_flagged_not_hardcoded():
    lines = build_assumptions("javascript", k6=True)
    joined = "\n".join(lines)
    assert "ASSUMPTION — verify against your API/runtime" in joined
    for env in ("OIDC_CLIENT_ID", "BASE_FQDN", "OIDC_REALM",
                "AUTH_SYSTEM_MAP", "AUTH_USERNAME", "AUTH_PASSWORD"):
        assert f"__ENV.{env}" in joined
    # No bare secret literal leaks in.
    assert "KsCriVjHmvzJJyqf" not in joined
    assert "password=tester" not in joined


def test_budget_value_changes_injected_amount():
    files = [
        {"filename": "suite.robot",
         "content": "*** Settings ***\nLibrary    session.py\nLibrary    keycloak.py\n"},
        {"filename": "session.py", "content": SESSION_HELPER * 20},
        {"filename": "keycloak.py", "content": KEYCLOAK_HELPER * 20},
    ]
    small = build_library_context(files, "Robot Framework", "K6", "javascript",
                                  char_budget=400)
    large = build_library_context(files, "Robot Framework", "K6", "javascript",
                                  char_budget=6000)
    assert len(small.prompt_block) < len(large.prompt_block)
    summary_len = len(
        __import__("src.tools.library_ingest", fromlist=["_render_contract_summary"])
        ._render_contract_summary(large.contract))
    assert len(large.prompt_block) <= max(6000, summary_len)
