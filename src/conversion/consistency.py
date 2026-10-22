"""consistency.py — Multi-retry consistency harness for code conversion.

Point 6 of the conversion rewrite: "The main goal of this tool is to
prove that you can get similar result for multiple retry and also there
would be accurate result similar to the one you can get it using kiro."

This module does NOT claim an LLM will produce byte-identical output
every run — it can't guarantee that, and no prompt engineering can. What
it DOES provide is a measurement harness: run the exact same conversion
request N times (same source, same llm_call), and report, with real
numbers:
  - how often the output compiled (src/conversion/compilers.py)
  - how often it passed the structural "no orphan test" check
  - how often the cross-repo parity check found the two frameworks
    equivalent (src/conversion/parity.py)
  - how much the assertion count varied across runs
  - how many repair-loop attempts each run needed

`is_consistent` is True only when EVERY run was valid AND every run's
assertion count agreed — i.e. the strict bar for "accurate and
reproducible." `consistency_score` (0.0-1.0) gives a softer signal:
fraction of runs that were fully valid, so a 4/5 result is still visible
and actionable rather than a single pass/fail bit.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Any

from src.conversion.service import ConversionRequest, ConversionResult, convert

logger = logging.getLogger(__name__)

__all__ = ["ConsistencyRun", "ConsistencyReport", "check_consistency"]

DEFAULT_RUNS = 5


@dataclass
class ConsistencyRun:
    run_number: int
    is_valid: bool
    attempts_needed: int
    assertion_count: int
    is_parity_equivalent: bool | None  # None if the parity check itself failed to run
    compile_tool: str
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_number": self.run_number,
            "is_valid": self.is_valid,
            "attempts_needed": self.attempts_needed,
            "assertion_count": self.assertion_count,
            "is_parity_equivalent": self.is_parity_equivalent,
            "compile_tool": self.compile_tool,
            "errors": self.errors,
        }


@dataclass
class ConsistencyReport:
    runs: list[ConversionResult]
    records: list[ConsistencyRun]
    valid_count: int
    total_runs: int
    consistency_score: float            # valid_count / total_runs
    is_consistent: bool                 # strict bar: all valid AND same assertion count
    assertion_counts: list[int]
    assertion_count_mode: int | None
    parity_equivalent_count: int

    def to_dict(self) -> dict:
        return {
            "records": [r.to_dict() for r in self.records],
            "valid_count": self.valid_count,
            "total_runs": self.total_runs,
            "consistency_score": self.consistency_score,
            "is_consistent": self.is_consistent,
            "assertion_counts": self.assertion_counts,
            "assertion_count_mode": self.assertion_count_mode,
            "parity_equivalent_count": self.parity_equivalent_count,
        }

    def render_report(self) -> str:
        lines = [
            f"## Consistency check — {self.total_runs} run(s)",
            f"- Valid (compiled + structurally sound): **{self.valid_count}/{self.total_runs}** "
            f"({self.consistency_score:.0%})",
            f"- Cross-repo parity equivalent: **{self.parity_equivalent_count}/{self.total_runs}**",
            f"- Assertion counts across runs: {self.assertion_counts}"
            + (f" (mode: {self.assertion_count_mode})" if self.assertion_count_mode is not None else ""),
        ]
        if self.is_consistent:
            lines.append("\n✅ **Consistent**: every run compiled, passed structural checks, "
                         "and produced the same assertion count.")
        else:
            lines.append("\n⚠️ **Not fully consistent** — see per-run detail below.")
            for r in self.records:
                if not r.is_valid or r.assertion_count != self.assertion_count_mode:
                    icon = "❌" if not r.is_valid else "⚠️"
                    lines.append(
                        f"  {icon} Run {r.run_number}: valid={r.is_valid}, "
                        f"assertions={r.assertion_count}, attempts={r.attempts_needed}"
                        + (f", errors: {'; '.join(r.errors)}" if r.errors else "")
                    )
        return "\n".join(lines)


def _record_from_result(run_number: int, result: ConversionResult) -> ConsistencyRun:
    last = result.attempts[-1] if result.attempts else None
    errors: list[str] = []
    if last is not None:
        if last.compile_result.checked and not last.compile_result.ok:
            errors.extend(last.compile_result.errors)
        if not last.validation.is_valid:
            errors.extend(last.validation.errors)

    assertion_count = 0
    if result.assertion_report:
        assertion_count = result.assertion_report.get("converted_count", 0)

    is_parity_equivalent = None
    if result.parity_report is not None:
        is_parity_equivalent = bool(result.parity_report.get("is_equivalent"))

    return ConsistencyRun(
        run_number=run_number,
        is_valid=result.is_valid,
        attempts_needed=len(result.attempts),
        assertion_count=assertion_count,
        is_parity_equivalent=is_parity_equivalent,
        compile_tool=last.compile_result.tool if last else "",
        errors=errors,
    )


def check_consistency(
    source_code: str,
    from_framework: str,
    to_framework: str,
    llm_call: Any,
    *,
    runs: int = DEFAULT_RUNS,
    to_fw_data: object | None = None,
) -> ConsistencyReport:
    """Run the SAME conversion `runs` times and measure reproducibility.

    `llm_call` is called fresh on every run (and on every repair-loop
    retry within a run) — each run is an independent conversion attempt
    through the full pipeline (compile check, structural check, parity
    check), exactly as a single `convert()` call would do.
    """
    if runs < 1:
        raise ValueError("runs must be >= 1")

    results: list[ConversionResult] = []
    records: list[ConsistencyRun] = []

    for i in range(1, runs + 1):
        logger.info("consistency check: run %d/%d", i, runs)
        result = convert(
            ConversionRequest(
                source_code=source_code,
                from_framework=from_framework,
                to_framework=to_framework,
                llm_call=llm_call,
            ),
            to_fw_data=to_fw_data,
        )
        results.append(result)
        records.append(_record_from_result(i, result))

    valid_count = sum(1 for r in records if r.is_valid)
    parity_equivalent_count = sum(1 for r in records if r.is_parity_equivalent is True)
    assertion_counts = [r.assertion_count for r in records]

    assertion_count_mode: int | None = None
    if assertion_counts:
        try:
            assertion_count_mode = statistics.mode(assertion_counts)
        except statistics.StatisticsError:
            # No unique mode (e.g. all distinct) — fall back to the most
            # common value from the first run's count for display purposes.
            assertion_count_mode = assertion_counts[0]

    all_same_assertion_count = len(set(assertion_counts)) <= 1
    is_consistent = (valid_count == len(records)) and all_same_assertion_count

    return ConsistencyReport(
        runs=results,
        records=records,
        valid_count=valid_count,
        total_runs=len(records),
        consistency_score=valid_count / len(records) if records else 0.0,
        is_consistent=is_consistent,
        assertion_counts=assertion_counts,
        assertion_count_mode=assertion_count_mode,
        parity_equivalent_count=parity_equivalent_count,
    )
