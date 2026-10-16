"""Tests for src/tools/conversion_validator.py.

Covers the exact regression observed in
ericsson-core-assurance-test-robot-tool-generated/tests/healthStatus.robot:
a k6 source file with a real assertion (check()) converted into a Robot
Framework file containing only *** Keywords *** definitions, with no
*** Test Cases *** entry calling them.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools.conversion_validator import validate_single_file_conversion

K6_HEALTH_STATUS_SOURCE = """
import http from 'k6/http';
import { check, group } from 'k6';

export function getHealthStatus(msURL, msName, endpoint, testStateValue) {
  group('Verify Health Status for all Microservices', function () {
    let res = http.get(msURL + endpoint);
    check(res, {
      ['Get health check for ' + msName + ' (status: 200)']: (r) => r.status === 200
    }, { testState: testStateValue });
  });
}
"""

# Mirrors the actual generated healthStatus.robot: *** Keywords *** only,
# no *** Test Cases *** section at all.
ROBOT_KEYWORDS_ONLY_NO_TEST_CASES = """*** Settings ***
Library           RequestsLibrary
Library           Collections

*** Variables ***
${TEST_STATE_NONE}            none

*** Keywords ***
Check Res
    [Arguments]    ${test_state_value}    ${ms_name}    ${response}
    ${status}=    Get From Dictionary    ${response}    status
    [Return]    ${status}

Get Health Status
    [Arguments]    ${ms_url}    ${ms_name}    ${endpoint}    ${test_state_value}
    ${response}=    GET On Session    api    ${ms_url}${endpoint}
    ${result}=    Check Res    ${test_state_value}    ${ms_name}    ${response}
    [Return]    ${result}
"""

# Same keywords, but with a real *** Test Cases *** entry calling them —
# this is what a correct single-file conversion should look like.
ROBOT_WITH_RUNNABLE_TEST_CASE = """*** Settings ***
Library           RequestsLibrary
Library           Collections

*** Keywords ***
Get Health Status
    [Arguments]    ${ms_url}    ${ms_name}    ${endpoint}    ${test_state_value}
    ${response}=    GET On Session    api    ${ms_url}${endpoint}
    [Return]    ${response.status_code}

*** Test Cases ***
Verify Health Status For CSAC
    ${status}=    Get Health Status    https://csac.example.com    CSAC    /health    none
    Should Be Equal As Integers    ${status}    200
"""

# A *** Test Cases *** section present but with only a blank/comment body —
# should also be flagged, since nothing actually runs.
ROBOT_EMPTY_TEST_CASE_BODY = """*** Keywords ***
Get Health Status
    [Arguments]    ${ms_url}
    [Return]    ${True}

*** Test Cases ***
Verify Health Status
    # TODO: implement
"""


def test_detects_keywords_only_conversion_with_source_assertions():
    """Reproduces the healthStatus.robot regression: source has a check(),
    converted file has keywords but no test case — must be flagged invalid."""
    result = validate_single_file_conversion(
        source_code=K6_HEALTH_STATUS_SOURCE,
        converted_code=ROBOT_KEYWORDS_ONLY_NO_TEST_CASES,
        from_framework="K6",
        to_framework="Robot Framework",
        target_lang="robot",
    )
    assert result.source_has_assertions is True
    assert result.source_assertion_count >= 1
    assert result.has_runnable_test is False
    assert result.is_valid is False
    assert result.errors, "expected a hard error for definitions-only output"
    assert "no runnable test entry" in result.errors[0]


def test_accepts_conversion_with_runnable_test_case():
    result = validate_single_file_conversion(
        source_code=K6_HEALTH_STATUS_SOURCE,
        converted_code=ROBOT_WITH_RUNNABLE_TEST_CASE,
        from_framework="K6",
        to_framework="Robot Framework",
        target_lang="robot",
    )
    assert result.has_runnable_test is True
    assert result.is_valid is True
    assert result.errors == []


def test_flags_test_case_with_empty_body_as_not_runnable():
    result = validate_single_file_conversion(
        source_code=K6_HEALTH_STATUS_SOURCE,
        converted_code=ROBOT_EMPTY_TEST_CASE_BODY,
        from_framework="K6",
        to_framework="Robot Framework",
        target_lang="robot",
    )
    assert result.has_runnable_test is False
    assert result.is_valid is False


def test_pure_library_source_with_no_assertions_only_warns():
    """A source file with no assertions converted to keywords-only output
    is plausibly a legitimate shared-library conversion — warn, don't fail."""
    no_assertion_source = "export function helperOnly(x) { return x + 1; }\n"
    result = validate_single_file_conversion(
        source_code=no_assertion_source,
        converted_code=ROBOT_KEYWORDS_ONLY_NO_TEST_CASES,
        from_framework="K6",
        to_framework="Robot Framework",
        target_lang="robot",
    )
    assert result.source_has_assertions is False
    assert result.is_valid is True  # not blocked
    assert result.warnings  # but flagged for manual review


def test_orphan_keyword_detection():
    result = validate_single_file_conversion(
        source_code=K6_HEALTH_STATUS_SOURCE,
        converted_code=ROBOT_WITH_RUNNABLE_TEST_CASE,
        from_framework="K6",
        to_framework="Robot Framework",
        target_lang="robot",
    )
    # "Get Health Status" IS called from the test case body, so no orphans.
    assert result.orphan_keywords == []


def test_python_target_detects_missing_test_function():
    py_no_test = "def helper():\n    return requests.get('http://x').status_code == 200\n"
    result = validate_single_file_conversion(
        source_code=K6_HEALTH_STATUS_SOURCE,
        converted_code=py_no_test,
        from_framework="K6",
        to_framework="pytest",
        target_lang="python",
    )
    assert result.has_runnable_test is False
    assert result.is_valid is False


def test_python_target_accepts_test_function():
    py_with_test = "def test_health():\n    assert requests.get('http://x').status_code == 200\n"
    result = validate_single_file_conversion(
        source_code=K6_HEALTH_STATUS_SOURCE,
        converted_code=py_with_test,
        from_framework="K6",
        to_framework="pytest",
        target_lang="python",
    )
    assert result.has_runnable_test is True
    assert result.is_valid is True


def test_render_returns_empty_for_fully_valid_result():
    result = validate_single_file_conversion(
        source_code=K6_HEALTH_STATUS_SOURCE,
        converted_code=ROBOT_WITH_RUNNABLE_TEST_CASE,
        from_framework="K6",
        to_framework="Robot Framework",
        target_lang="robot",
    )
    assert result.render() == ""


def test_render_includes_error_text_for_invalid_result():
    result = validate_single_file_conversion(
        source_code=K6_HEALTH_STATUS_SOURCE,
        converted_code=ROBOT_KEYWORDS_ONLY_NO_TEST_CASES,
        from_framework="K6",
        to_framework="Robot Framework",
        target_lang="robot",
    )
    rendered = result.render()
    assert "Conversion validation failed" in rendered
    assert "no runnable test entry" in rendered

# Note: end-to-end wiring of this validator into the conversion pipeline
# is now covered by tests/test_conversion_service.py (src/conversion/
# service.py), which replaced the old ToolExecutor._convert_test_cases
# path this file used to test here.
