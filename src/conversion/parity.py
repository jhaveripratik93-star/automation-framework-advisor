"""parity.py — Auto-chained repo-comparator parity check after conversion.

Point 4 of the conversion rewrite: "In the end it should execute code
repo comparator and confirm does the framework a and framework b has
equivalent validation or not, and give report in end showing which
validation is having mismatch."

This wraps `src.tools.repo_comparator` — the SAME engine behind the
standalone "Cross-Repo Test Parity" page/MCP tools (`compare_repos`,
`compare_repos_csv`) — and runs it as the LAST step of every conversion:
source code (framework A) vs. converted code (framework B), extracting
test cases from each side and comparing assertion counts per matched
test case by name.

This module does not re-implement any matching/extraction logic — it
only adapts repo_comparator's single-file-pair result into plain,
JSON-serializable dicts and adds two conveniences: `mismatches` (matched
pairs whose assertion counts differ) and `is_equivalent` (True only if
every test case matched AND every match's assertion counts agree).
"""
from __future__ import annotations

from typing import Any

from src.conversion.language import LANG_ECOSYSTEM

__all__ = ["check_parity"]

# Minimal extension hints so repo_comparator's extension-based extractor
# dispatch (Robot / .py / .js / .java) picks the right parser for the
# SOURCE side. The target side already has a resolved extension via
# src.conversion.language.LANG_ECOSYSTEM, since conversion always knows
# its own target language.
_SOURCE_EXTENSION_HINTS: dict[str, str] = {
    "k6": ".js",
    "cypress": ".js",
    "jest": ".js",
    "mocha": ".js",
    "playwright": ".py",
    "selenium": ".py",
    "robot framework": ".robot",
    "robot": ".robot",
    "rest assured": ".java",
    "appium": ".py",
}


def _guess_source_extension(framework_name: str) -> str:
    name_lower = framework_name.lower()
    for key, ext in _SOURCE_EXTENSION_HINTS.items():
        if key in name_lower:
            return ext
    return ".py"


def _pair_to_dict(m: Any) -> dict:
    return {
        "name": m.name,
        "display_a": m.display_a,
        "display_b": m.display_b,
        "file_a": m.file_a,
        "file_b": m.file_b,
        "assertions_a": m.assertions_a,
        "assertions_b": m.assertions_b,
        "loop_assertions_a": m.loop_assertions_a,
        "loop_assertions_b": m.loop_assertions_b,
        "score": m.score,
        "status": m.status,
    }


def _unmatched_to_dict(u: Any) -> dict:
    return {
        "display_name": u.display_name,
        "file": u.file,
        "assertions": u.assertions,
        "repo": u.repo,
        "loop_assertions": u.loop_assertions,
    }


def check_parity(
    source_code: str,
    converted_code: str,
    from_framework: str,
    to_framework: str,
    target_lang: str = "python",
) -> dict:
    """Run the repo-comparator engine on a single source/converted pair.

    Returns a fully JSON-serializable dict:
      {
        "framework_a": str, "framework_b": str,
        "summary": {...},                 # same shape as compare_repos
        "matched": [{...}, ...],          # per-test-case assertion comparison
        "mismatches": [{...}, ...],       # subset of matched with status == "mismatch"
        "only_in_a": [{...}, ...],        # test cases found only in source
        "only_in_b": [{...}, ...],        # test cases found only in converted code
        "is_equivalent": bool,            # True iff every case matched AND no mismatches
      }
    """
    from src.tools.repo_comparator import compare_repos

    src_ext = _guess_source_extension(from_framework)
    to_ext = LANG_ECOSYSTEM.get(target_lang, {}).get("ext", ".py")

    files_a = [{"filename": f"source{src_ext}", "content": source_code}]
    files_b = [{"filename": f"converted{to_ext}", "content": converted_code}]

    raw = compare_repos(files_a, files_b, from_framework, to_framework)

    matched = [_pair_to_dict(m) for m in raw["matched"]]
    only_in_a = [_unmatched_to_dict(u) for u in raw["only_in_a"]]
    only_in_b = [_unmatched_to_dict(u) for u in raw["only_in_b"]]
    mismatches = [m for m in matched if m["status"] == "mismatch"]

    is_equivalent = not mismatches and not only_in_a and not only_in_b and bool(matched)

    return {
        "framework_a": raw["framework_a"],
        "framework_b": raw["framework_b"],
        "summary": raw["summary"],
        "matched": matched,
        "mismatches": mismatches,
        "only_in_a": only_in_a,
        "only_in_b": only_in_b,
        "is_equivalent": is_equivalent,
    }
