"""Tests for src/conversion/parity.py — auto-chained repo-comparator check."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.conversion.parity import check_parity

K6_SOURCE = """
import http from 'k6/http';
import { check, group } from 'k6';

export function getHealthStatus() {
  group('Verify Health Status', function () {
    let res = http.get('https://example.com/health');
    check(res, { 'status is 200': (r) => r.status === 200 });
  });
}
"""

ROBOT_EQUIVALENT = """*** Settings ***
Library    RequestsLibrary

*** Test Cases ***
Verify Health Status
    ${response}=    GET On Session    api    /health
    Status Should Be    200    ${response}
"""

ROBOT_MISSING_ASSERTION = """*** Settings ***
Library    RequestsLibrary

*** Test Cases ***
Verify Health Status
    ${response}=    GET On Session    api    /health
    Log    done
"""

ROBOT_UNRELATED_NAME = """*** Settings ***
Library    RequestsLibrary

*** Test Cases ***
Something Totally Different
    Log    nothing to do with health
"""


def test_equivalent_conversion_reports_is_equivalent_true():
    report = check_parity(K6_SOURCE, ROBOT_EQUIVALENT, "K6", "Robot Framework", target_lang="robot")
    assert report["is_equivalent"] is True
    assert report["mismatches"] == []
    assert report["only_in_a"] == []
    assert report["only_in_b"] == []


def test_missing_assertion_is_reported_as_mismatch():
    report = check_parity(K6_SOURCE, ROBOT_MISSING_ASSERTION, "K6", "Robot Framework", target_lang="robot")
    assert report["is_equivalent"] is False
    assert len(report["mismatches"]) == 1
    mismatch = report["mismatches"][0]
    assert mismatch["assertions_a"] != mismatch["assertions_b"]


def test_unmatched_test_case_names_show_up_as_only_in_b():
    report = check_parity(K6_SOURCE, ROBOT_UNRELATED_NAME, "K6", "Robot Framework", target_lang="robot")
    assert report["is_equivalent"] is False
    # The k6 case couldn't be matched to the unrelated robot case name
    assert report["only_in_a"] or report["only_in_b"]


def test_report_is_json_serializable():
    import json
    report = check_parity(K6_SOURCE, ROBOT_EQUIVALENT, "K6", "Robot Framework", target_lang="robot")
    json.dumps(report)  # must not raise


def test_report_has_expected_top_level_keys():
    report = check_parity(K6_SOURCE, ROBOT_EQUIVALENT, "K6", "Robot Framework", target_lang="robot")
    assert set(report) == {
        "framework_a", "framework_b", "summary", "matched",
        "mismatches", "only_in_a", "only_in_b", "is_equivalent",
    }
