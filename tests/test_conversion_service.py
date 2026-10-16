"""Tests for src/conversion/service.py — end-to-end conversion orchestration
with a stubbed LLM (no network calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.conversion.service import ConversionRequest, convert

K6_SOURCE_WITH_ASSERTION = """
import http from 'k6/http';
import { check, group } from 'k6';

export function getHealthStatus(msURL, msName, endpoint) {
  let result = true;
  group('Verify Health Status', function () {
    let res = http.get(msURL + endpoint);
    result = check(res, { 'status is 200': (r) => r.status === 200 });
  });
  return result;
}
"""

VALID_ROBOT_OUTPUT = """*** Settings ***
Library    RequestsLibrary

*** Keywords ***
Get Health Status
    [Arguments]    ${ms_url}    ${endpoint}
    Create Session    api    ${ms_url}
    ${response}=    GET On Session    api    ${endpoint}
    RETURN    ${response.status_code}

*** Test Cases ***
Verify Health Status
    ${status}=    Get Health Status    https://example.com    /health
    Should Be Equal As Integers    ${status}    200
"""

BROKEN_THEN_FIXED_ROBOT_OUTPUTS = [
    # Attempt 1: missing END -> real compile failure
    "*** Test Cases ***\nT1\n    IF    True\n        Log    hi\n",
    # Attempt 2 (after repair prompt): valid
    VALID_ROBOT_OUTPUT,
]

ORPHAN_ONLY_OUTPUT = """*** Settings ***
Library    RequestsLibrary

*** Keywords ***
Get Health Status
    [Arguments]    ${ms_url}    ${endpoint}
    Create Session    api    ${ms_url}
    ${response}=    GET On Session    api    ${endpoint}
    RETURN    ${response.status_code}
"""


class _ScriptedLLM:
    """Returns each item in `outputs` in order, one per call."""

    def __init__(self, outputs: list[str]):
        self._outputs = list(outputs)
        self.call_count = 0

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        idx = min(self.call_count - 1, len(self._outputs) - 1)
        return self._outputs[idx]


def test_valid_output_on_first_attempt_no_retry():
    llm = _ScriptedLLM([VALID_ROBOT_OUTPUT])
    result = convert(ConversionRequest(
        source_code=K6_SOURCE_WITH_ASSERTION,
        from_framework="K6",
        to_framework="Robot Framework",
        llm_call=llm,
    ))
    assert result.is_valid is True
    assert len(result.attempts) == 1
    assert llm.call_count == 1
    assert "*** Test Cases ***" in result.code


def test_compile_failure_triggers_repair_and_succeeds():
    llm = _ScriptedLLM(BROKEN_THEN_FIXED_ROBOT_OUTPUTS)
    result = convert(ConversionRequest(
        source_code=K6_SOURCE_WITH_ASSERTION,
        from_framework="K6",
        to_framework="Robot Framework",
        llm_call=llm,
    ))
    assert llm.call_count == 2
    assert len(result.attempts) == 2
    assert result.attempts[0].compile_result.ok is False
    assert result.is_valid is True
    assert result.code == VALID_ROBOT_OUTPUT.strip() or "Verify Health Status" in result.code


def test_structural_failure_triggers_repair():
    llm = _ScriptedLLM([ORPHAN_ONLY_OUTPUT, VALID_ROBOT_OUTPUT])
    result = convert(ConversionRequest(
        source_code=K6_SOURCE_WITH_ASSERTION,
        from_framework="K6",
        to_framework="Robot Framework",
        llm_call=llm,
    ))
    assert llm.call_count == 2
    assert result.attempts[0].validation.is_valid is False
    assert result.is_valid is True


def test_persistent_failure_gives_up_after_max_attempts():
    llm = _ScriptedLLM([ORPHAN_ONLY_OUTPUT])  # always the same broken output
    result = convert(ConversionRequest(
        source_code=K6_SOURCE_WITH_ASSERTION,
        from_framework="K6",
        to_framework="Robot Framework",
        llm_call=llm,
    ))
    # 1 initial + MAX_REPAIR_ATTEMPTS retries
    from src.conversion.service import MAX_REPAIR_ATTEMPTS
    assert len(result.attempts) == MAX_REPAIR_ATTEMPTS + 1
    assert result.is_valid is False
    assert "⚠️" in result.render_report()


def test_cicd_trailer_is_stripped_even_if_llm_emits_it():
    llm_output = VALID_ROBOT_OUTPUT + "\n# CI/CD INTEGRATION\non:\n  push:\n    branches: [main]\n"
    llm = _ScriptedLLM([llm_output])
    result = convert(ConversionRequest(
        source_code=K6_SOURCE_WITH_ASSERTION,
        from_framework="K6",
        to_framework="Robot Framework",
        llm_call=llm,
    ))
    assert "CI/CD" not in result.code
    assert "on:" not in result.code


def test_markdown_fence_is_stripped():
    llm = _ScriptedLLM(["```robot\n" + VALID_ROBOT_OUTPUT + "\n```"])
    result = convert(ConversionRequest(
        source_code=K6_SOURCE_WITH_ASSERTION,
        from_framework="K6",
        to_framework="Robot Framework",
        llm_call=llm,
    ))
    assert "```" not in result.code


def test_prompt_sources_recorded():
    llm = _ScriptedLLM([VALID_ROBOT_OUTPUT])
    result = convert(ConversionRequest(
        source_code=K6_SOURCE_WITH_ASSERTION,
        from_framework="K6",
        to_framework="Robot Framework",
        llm_call=llm,
    ))
    assert any("base_system_prompt.md" in s for s in result.prompt_sources)
    assert any("robot_framework.md" in s for s in result.prompt_sources)
    assert any("k6__to__robot_framework.md" in s for s in result.prompt_sources)


def test_to_dict_is_json_serializable():
    import json
    llm = _ScriptedLLM([VALID_ROBOT_OUTPUT])
    result = convert(ConversionRequest(
        source_code=K6_SOURCE_WITH_ASSERTION,
        from_framework="K6",
        to_framework="Robot Framework",
        llm_call=llm,
    ))
    json.dumps(result.to_dict())  # must not raise


# ── Point 4: auto-chained repo-comparator parity check ──────────────────

K6_SOURCE_WITH_TWO_CHECKS = """
import http from 'k6/http';
import { check, group } from 'k6';

export function getHealthStatus() {
  group('Verify Health Status', function () {
    let res = http.get('https://example.com/health');
    check(res, {
      'status is 200': (r) => r.status === 200,
      'body not empty': (r) => r.body.length > 0,
    });
  });
}
"""

ROBOT_MISSING_ONE_ASSERTION = """*** Settings ***
Library    RequestsLibrary

*** Test Cases ***
Verify Health Status
    ${response}=    GET On Session    api    /health
    Status Should Be    200    ${response}
"""

ROBOT_FULL_PARITY = """*** Settings ***
Library    RequestsLibrary

*** Test Cases ***
Verify Health Status
    ${response}=    GET On Session    api    /health
    Status Should Be    200    ${response}
    Should Not Be Empty    ${response.text}
"""


def test_parity_report_present_on_full_result():
    llm = _ScriptedLLM([ROBOT_FULL_PARITY])
    result = convert(ConversionRequest(
        source_code=K6_SOURCE_WITH_TWO_CHECKS,
        from_framework="K6",
        to_framework="Robot Framework",
        llm_call=llm,
    ))
    assert result.parity_report is not None
    assert result.parity_report["is_equivalent"] is True
    assert "parity check" in result.render_report().lower()


def test_parity_report_flags_mismatch_in_final_result():
    llm = _ScriptedLLM([ROBOT_MISSING_ONE_ASSERTION])
    result = convert(ConversionRequest(
        source_code=K6_SOURCE_WITH_TWO_CHECKS,
        from_framework="K6",
        to_framework="Robot Framework",
        llm_call=llm,
    ))
    assert result.parity_report is not None
    assert result.parity_report["is_equivalent"] is False
    assert len(result.parity_report["mismatches"]) == 1
    report_text = result.render_report()
    assert "mismatch" in report_text.lower()


def test_parity_failure_does_not_break_conversion():
    """If check_parity() raises for any reason, convert() must still
    return a usable result — parity is advisory, not load-bearing."""
    import src.conversion.service as svc_module

    original = svc_module.check_parity
    svc_module.check_parity = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        llm = _ScriptedLLM([VALID_ROBOT_OUTPUT])
        result = convert(ConversionRequest(
            source_code=K6_SOURCE_WITH_ASSERTION,
            from_framework="K6",
            to_framework="Robot Framework",
            llm_call=llm,
        ))
        assert result.parity_report is None
        assert result.is_valid is True  # unaffected by the parity-check failure
    finally:
        svc_module.check_parity = original


def test_parity_report_in_to_dict_is_json_serializable():
    import json
    llm = _ScriptedLLM([ROBOT_FULL_PARITY])
    result = convert(ConversionRequest(
        source_code=K6_SOURCE_WITH_TWO_CHECKS,
        from_framework="K6",
        to_framework="Robot Framework",
        llm_call=llm,
    ))
    payload = result.to_dict()
    assert "parity_report" in payload
    json.dumps(payload)
