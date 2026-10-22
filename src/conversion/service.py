"""service.py — Code conversion orchestration (rewritten architecture).

This is the single place conversion logic lives now. It is deliberately
LLM-transport-agnostic: callers pass an `llm_call` function
(str system, str user) -> str — Streamlit supplies one backed by Groq,
the MCP server supplies one backed by true MCP sampling
(ctx.session.create_message) with a Groq fallback. Nothing in this module
knows or cares which transport produced the text.

Pipeline per conversion:
  1. Load the editable prompt profile (src/conversion/prompts.py) for the
     (from, to) pair — no prompt text is hardcoded here.
  2. Call the LLM to produce converted code.
  3. Strip markdown fences / any CI/CD trailer the LLM ignored instructions
     and still emitted.
  4. Run the REAL compiler/parser for the target language
     (src/conversion/compilers.py) — not a regex heuristic.
  5. Run the structural "no orphan test" validator
     (src/tools/conversion_validator.py).
  6. If either check fails, feed the concrete errors back to the LLM and
     ask for a fix (bounded retries) — this is the self-repair loop that
     makes output "compilable and accurate" (point 5) rather than
     one-shot and hope.
  7. Run the cross-repo parity check (src/conversion/parity.py) comparing
     the ORIGINAL source against the FINAL converted code using the same
     repo-comparator engine behind the standalone "Cross-Repo Test
     Parity" feature — reports which test case (if any) has a mismatched
     assertion count between the two frameworks.
  8. Return a ConversionResult with the final code, every check's result,
     the parity report, and the full attempt history for transparency.

Explicitly OUT of scope here (point 1): no CI/CD YAML, pipeline config,
or GitHub Actions content is ever requested, generated, or returned by
this module.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

from src.conversion.compilers import CompileResult, compile_check
from src.conversion.language import resolve_target_language
from src.conversion.parity import check_parity
from src.conversion.prompts import load_prompt_profile
from src.tools.assertion_counter import compare as compare_assertions
from src.tools.conversion_validator import ConversionValidation, validate_single_file_conversion

logger = logging.getLogger(__name__)

__all__ = ["ConversionRequest", "ConversionAttempt", "ConversionResult", "convert", "LLMCall"]

_FENCE_RE = re.compile(r"```(?:[a-zA-Z]*)\n(.*?)```", re.DOTALL)
_CICD_TRAILER_MARKER = re.compile(
    r"(ci/cd\s*integration|github\s*actions?\s*(step|workflow)|\.github/workflows)",
    re.IGNORECASE,
)

MAX_REPAIR_ATTEMPTS = 2  # total attempts = 1 initial + this many repairs


class LLMCall(Protocol):
    """Transport-agnostic LLM call. Implementations: Groq (Streamlit) or
    true MCP sampling with Groq fallback (MCP server) — see
    mcp_server/sampling.py and services/app_services.py."""

    def __call__(self, system_prompt: str, user_prompt: str) -> str: ...


@dataclass
class ConversionRequest:
    source_code: str
    from_framework: str
    to_framework: str
    llm_call: LLMCall
    shared_context: str = ""   # cross-file context, optional (kept for future multi-file support)


@dataclass
class ConversionAttempt:
    attempt_number: int
    raw_llm_output: str
    code: str
    compile_result: CompileResult
    validation: ConversionValidation

    def to_dict(self) -> dict:
        return {
            "attempt_number": self.attempt_number,
            "code": self.code,
            "compile_result": self.compile_result.to_dict(),
            "validation": self.validation.to_dict(),
        }


@dataclass
class ConversionResult:
    code: str
    target_language: str
    is_valid: bool                      # final attempt's compile + structural check both passed
    attempts: list[ConversionAttempt] = field(default_factory=list)
    assertion_report: dict | None = None
    parity_report: dict | None = None   # src/conversion/parity.py — cross-repo-comparator check
    prompt_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "target_language": self.target_language,
            "is_valid": self.is_valid,
            "attempts": [a.to_dict() for a in self.attempts],
            "assertion_report": self.assertion_report,
            "parity_report": self.parity_report,
            "prompt_sources": self.prompt_sources,
        }

    def render_report(self) -> str:
        """Short markdown summary of validation/compile/retry/parity outcome."""
        lines: list[str] = []
        n = len(self.attempts)
        last = self.attempts[-1] if self.attempts else None
        if self.is_valid:
            lines.append(f"✅ Conversion succeeded (compiled + structurally valid after {n} attempt(s)).")
        else:
            lines.append(f"⚠️ Conversion completed after {n} attempt(s) but still has issues:")
            if last and not last.compile_result.ok and last.compile_result.checked:
                lines.append(f"- Compile errors ({last.compile_result.tool}): " + "; ".join(last.compile_result.errors))
            if last and not last.validation.is_valid:
                lines.extend(f"- {e}" for e in last.validation.errors)
        if self.assertion_report and self.assertion_report.get("status") != "pass":
            lines.append(f"- Assertion parity: {self.assertion_report.get('message', '')}")
        if self.parity_report is not None:
            if self.parity_report.get("is_equivalent"):
                lines.append("✅ Cross-repo parity check: every test case matched with equivalent assertions.")
            else:
                lines.append("⚠️ Cross-repo parity check found mismatches:")
                for m in self.parity_report.get("mismatches", []):
                    lines.append(
                        f"  - '{m['display_a']}' ↔ '{m['display_b']}': "
                        f"{m['assertions_a']} assertion(s) in source vs "
                        f"{m['assertions_b']} in converted code."
                    )
                for u in self.parity_report.get("only_in_a", []):
                    lines.append(f"  - '{u['display_name']}' exists in the source but has no matching converted test case.")
                for u in self.parity_report.get("only_in_b", []):
                    lines.append(f"  - '{u['display_name']}' appears in the converted code with no equivalent in the source.")
        return "\n".join(lines)


def _strip_cicd_trailer(code: str) -> str:
    """Second-guard against a leaked CI/CD block (point 1) — the prompt
    already forbids it, this catches an LLM that ignores the instruction."""
    lines = code.splitlines()
    for i, line in enumerate(lines):
        if _CICD_TRAILER_MARKER.search(line):
            lines = lines[:i]
            break
    return "\n".join(lines).rstrip()


def _extract_code(raw: str, fence: str) -> str:
    blocks = re.findall(rf"```(?:{fence})?\n(.*?)```", raw, re.DOTALL) if fence else []
    if not blocks:
        blocks = _FENCE_RE.findall(raw)
    code = blocks[0].strip() if blocks else raw.strip()
    return _strip_cicd_trailer(code)


def _build_repair_prompt(
    from_name: str, to_name: str, previous_code: str,
    compile_result: CompileResult, validation: ConversionValidation,
) -> str:
    issues: list[str] = []
    if compile_result.checked and not compile_result.ok:
        issues.append(
            f"The {to_name} compiler/parser ({compile_result.tool}) rejected this code with:\n"
            + "\n".join(f"  - {e}" for e in compile_result.errors)
        )
    if not validation.is_valid:
        issues.extend(f"  - {e}" for e in validation.errors)
    issue_text = "\n".join(issues) if issues else "The output needs correction."

    return (
        f"The {to_name} code you produced has the following problem(s):\n\n"
        f"{issue_text}\n\n"
        f"Here is the code you produced:\n```\n{previous_code}\n```\n\n"
        f"Fix ONLY these problems. Output the complete corrected {to_name} "
        f"code. Follow all the same rules as before (no CI/CD content, no "
        f"markdown fences, every assertion preserved, no orphan "
        f"keywords/functions with nothing calling them)."
    )


def convert(request: ConversionRequest, *, to_fw_data: object | None = None) -> ConversionResult:
    """Convert `request.source_code` from one framework to another.

    `to_fw_data` is an optional knowledge-base FrameworkData for the
    target framework, used only to resolve the target language (e.g.
    Playwright -> python). Conversion still works without it (defaults to
    python unless the target name itself implies another language).
    """
    from_name = request.from_framework
    to_name = request.to_framework
    target_lang, eco = resolve_target_language(to_fw_data, to_name)
    fence = eco.get("fence", "")

    profile = load_prompt_profile(from_name, to_name)
    system_prompt = profile.render_system_prompt(from_name, to_name)

    attempts: list[ConversionAttempt] = []
    code = ""
    user_prompt = (
        f"Convert this {from_name} test code to {to_name}:\n\n"
        f"```\n{request.source_code}\n```"
    )
    if request.shared_context:
        user_prompt += f"\n\nShared project context:\n{request.shared_context[:2000]}"

    for attempt_number in range(1, MAX_REPAIR_ATTEMPTS + 2):  # 1 initial + N repairs
        raw = request.llm_call(system_prompt, user_prompt)
        code = _extract_code(raw, fence)

        compile_result = compile_check(code, target_lang)
        validation = validate_single_file_conversion(
            request.source_code, code, from_name, to_name, target_lang=target_lang,
        )
        attempts.append(ConversionAttempt(
            attempt_number=attempt_number,
            raw_llm_output=raw,
            code=code,
            compile_result=compile_result,
            validation=validation,
        ))

        compile_ok = (not compile_result.checked) or compile_result.ok
        if compile_ok and validation.is_valid:
            break

        if attempt_number > MAX_REPAIR_ATTEMPTS:
            break

        logger.info(
            "conversion repair loop: attempt %d failed (compile_ok=%s, validation_ok=%s) — retrying",
            attempt_number, compile_ok, validation.is_valid,
        )
        user_prompt = _build_repair_prompt(from_name, to_name, code, compile_result, validation)

    final = attempts[-1]
    is_valid = (not final.compile_result.checked or final.compile_result.ok) and final.validation.is_valid

    assertion_report = compare_assertions(request.source_code, code, from_name, to_name)

    try:
        parity_report = check_parity(
            request.source_code, code, from_name, to_name, target_lang=target_lang,
        )
    except Exception as exc:  # noqa: BLE001 — parity check is advisory, must never break conversion
        logger.warning("conversion parity check failed (%s) — continuing without it", exc)
        parity_report = None

    return ConversionResult(
        code=code,
        target_language=target_lang,
        is_valid=is_valid,
        attempts=attempts,
        assertion_report=assertion_report,
        parity_report=parity_report,
        prompt_sources=profile.sources,
    )
