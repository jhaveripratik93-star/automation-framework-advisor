"""Tests for src/conversion/consistency.py — multi-retry consistency harness
(point 6: prove reproducible, accurate output across repeated runs)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.conversion.consistency import check_consistency

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

VALID_ROBOT_OUTPUT = """*** Settings ***
Library    RequestsLibrary

*** Test Cases ***
Verify Health Status
    ${response}=    GET On Session    api    /health
    Status Should Be    200    ${response}
"""

INVALID_ROBOT_OUTPUT = "*** Test Cases ***\nT1\n    IF    True\n        Log    hi\n"

DIFFERENT_BUT_VALID_ROBOT_OUTPUT = """*** Settings ***
Library    RequestsLibrary

*** Test Cases ***
Verify Health Status
    ${response}=    GET On Session    api    /health
    Status Should Be    200    ${response}
    Should Not Be Empty    ${response.text}
"""


class _AlwaysSameLLM:
    """Returns the identical valid output on every call — simulates a
    perfectly reproducible, correct LLM."""

    def __init__(self, output: str):
        self._output = output
        self.call_count = 0

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        return self._output


class _CyclingLLM:
    """Cycles through a fixed list of outputs — simulates an LLM whose
    output varies between runs (e.g. different assertion coverage)."""

    def __init__(self, outputs: list[str]):
        self._outputs = outputs
        self.call_count = 0

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        idx = self.call_count % len(self._outputs)
        self.call_count += 1
        return self._outputs[idx]


def test_fully_consistent_output_reports_is_consistent_true():
    llm = _AlwaysSameLLM(VALID_ROBOT_OUTPUT)
    report = check_consistency(K6_SOURCE, "K6", "Robot Framework", llm, runs=3)

    assert report.total_runs == 3
    assert report.valid_count == 3
    assert report.consistency_score == 1.0
    assert report.is_consistent is True
    assert len(set(report.assertion_counts)) == 1


def test_always_invalid_output_reports_zero_valid():
    llm = _AlwaysSameLLM(INVALID_ROBOT_OUTPUT)
    report = check_consistency(K6_SOURCE, "K6", "Robot Framework", llm, runs=3)

    assert report.valid_count == 0
    assert report.consistency_score == 0.0
    assert report.is_consistent is False
    # Each run exhausts the full repair-loop attempt budget trying to fix it
    assert all(r.attempts_needed > 1 for r in report.records)


def test_varying_assertion_counts_is_not_consistent():
    llm = _CyclingLLM([VALID_ROBOT_OUTPUT, DIFFERENT_BUT_VALID_ROBOT_OUTPUT])
    report = check_consistency(K6_SOURCE, "K6", "Robot Framework", llm, runs=4)

    assert report.valid_count == 4  # both outputs are individually valid
    assert report.is_consistent is False  # but assertion counts differ run-to-run
    assert len(set(report.assertion_counts)) > 1


def test_runs_parameter_controls_iteration_count():
    llm = _AlwaysSameLLM(VALID_ROBOT_OUTPUT)
    report = check_consistency(K6_SOURCE, "K6", "Robot Framework", llm, runs=7)
    assert report.total_runs == 7
    assert len(report.records) == 7
    assert len(report.runs) == 7


def test_invalid_runs_count_raises():
    import pytest
    llm = _AlwaysSameLLM(VALID_ROBOT_OUTPUT)
    with pytest.raises(ValueError):
        check_consistency(K6_SOURCE, "K6", "Robot Framework", llm, runs=0)


def test_parity_equivalent_count_tracked():
    llm = _AlwaysSameLLM(VALID_ROBOT_OUTPUT)
    report = check_consistency(K6_SOURCE, "K6", "Robot Framework", llm, runs=3)
    assert report.parity_equivalent_count == 3  # same source/output every run -> equivalent every time


def test_render_report_consistent_case():
    llm = _AlwaysSameLLM(VALID_ROBOT_OUTPUT)
    report = check_consistency(K6_SOURCE, "K6", "Robot Framework", llm, runs=3)
    text = report.render_report()
    assert "Consistent" in text
    assert "3/3" in text


def test_render_report_inconsistent_case_lists_per_run_detail():
    # Both outputs are individually valid, but differ in assertion count —
    # exercises the "not consistent due to assertion-count variance" path
    # without the repair loop self-healing an invalid run back to valid.
    llm = _CyclingLLM([VALID_ROBOT_OUTPUT, DIFFERENT_BUT_VALID_ROBOT_OUTPUT])
    report = check_consistency(K6_SOURCE, "K6", "Robot Framework", llm, runs=4)
    text = report.render_report()
    assert "Not fully consistent" in text
    assert "Run " in text


def test_to_dict_is_json_serializable():
    import json
    llm = _AlwaysSameLLM(VALID_ROBOT_OUTPUT)
    report = check_consistency(K6_SOURCE, "K6", "Robot Framework", llm, runs=2)
    json.dumps(report.to_dict())


def test_default_runs_is_five():
    from src.conversion.consistency import DEFAULT_RUNS
    assert DEFAULT_RUNS == 5
