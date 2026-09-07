"""Deterministic post-processing for Robot Framework output.

The LLM is instructed to emit exactly one test case per manual test case, but
it can still drift badly. Two failure modes are seen in practice:

  1. A single scenario split into several ``*** Test Cases ***`` entries
     (one per step / assertion / page).
  2. Malformed output where the ``*** Test Cases ***`` header is REPEATED
     inside the body — sometimes indented — with fresh test-case names after
     each repeat, e.g.::

         *** Test Cases ***
         Login With Valid Credentials
             [Documentation]  ...
                 # comment
             *** Test Cases ***      <- spurious, indented
             Login Test
                 Open Browser  ...
             *** Test Cases ***      <- spurious again
             Verify Dashboard
                 ...

This module deterministically normalizes that mess into exactly ONE test case,
preserving every step in order. Settings / Variables / Keywords sections are
left intact — extracted reusable keywords are valid and must be kept.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Matches a section header regardless of surrounding whitespace/indentation,
# e.g. "*** Test Cases ***", "  *** test cases ***".
_SECTION_RE = re.compile(r"^\s*\*{3}\s*([^*]+?)\s*\*{3}\s*$")

_KNOWN_SECTIONS = {"settings", "variables", "test cases", "tasks", "keywords", "comments"}

# Test-body settings that must be de-duplicated / repositioned when merging.
_DOC_RE = re.compile(r"^\s*\[documentation\]", re.IGNORECASE)
_TAGS_RE = re.compile(r"^\s*\[tags\]", re.IGNORECASE)
_SETUP_RE = re.compile(r"^\s*\[setup\]", re.IGNORECASE)
_TEARDOWN_RE = re.compile(r"^\s*\[teardown\]", re.IGNORECASE)
_SETTING_RE = re.compile(r"^\s*\[[^\]]+\]")


def _section_name(line: str) -> str | None:
    """Return the lowercased section name if the line is a section header."""
    m = _SECTION_RE.match(line)
    if not m:
        return None
    return m.group(1).strip().lower()


def _split_sections_robust(code: str) -> list[tuple[str, list[str]]]:
    """Split code into (section_name, body_lines) blocks.

    Robust to the malformed case: a REPEATED header for a section already open
    (e.g. a second ``*** Test Cases ***``) is treated as a continuation of the
    same section, NOT a new one — its header line is dropped and its content is
    appended to the existing section. This is what merges the LLM's scattered
    ``*** Test Cases ***`` fragments back together.

    body_lines does NOT include the header line.
    """
    sections: list[tuple[str, list[str]]] = []
    order: dict[str, int] = {}          # section name -> index in `sections`
    current = ""                         # "" = preamble before first header
    sections.append((current, []))
    order[current] = 0
    drop_next_name = False               # drop phantom name after a repeated header

    for line in code.splitlines():
        name = _section_name(line)
        if name is not None and name in _KNOWN_SECTIONS:
            if name in order:
                # Repeated/duplicate header — reopen the existing section, skip
                # the header line, and drop the phantom test-case NAME that the
                # LLM emits right after each spurious "*** Test Cases ***".
                current = name
                drop_next_name = (name == "test cases")
            else:
                sections.append((name, []))
                order[name] = len(sections) - 1
                current = name
                drop_next_name = False
            continue

        if drop_next_name:
            # The first non-blank, non-comment line after a repeated
            # "*** Test Cases ***" header is a spurious duplicate test-case
            # name — drop it. Blank/comment lines pass through untouched.
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                drop_next_name = False
                continue  # drop this phantom name line

        sections[order[current]][1].append(line)

    return sections


def _parse_test_cases(body_lines: list[str]) -> list[tuple[str, list[str]]]:
    """Parse a *** Test Cases *** body into [(name, step_lines), ...].

    A test-case name is a non-indented, non-empty, non-comment, non-header line.
    Its steps are the following indented lines (comments/blanks included) until
    the next name. Any stray section-header lines are ignored.
    """
    tests: list[tuple[str, list[str]]] = []
    name: str | None = None
    steps: list[str] = []

    for line in body_lines:
        if _section_name(line) is not None:
            continue  # ignore any stray header that slipped through
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if name is not None:
                steps.append(line)
            continue
        is_indented = line[:1] in (" ", "\t")
        if not is_indented:
            if name is not None:
                tests.append((name, steps))
            name = stripped
            steps = []
        else:
            if name is not None:
                steps.append(line)
    if name is not None:
        tests.append((name, steps))
    return tests


def _normalize_step_indent(line: str) -> str:
    """Ensure a step line uses a single 4-space indent (Robot needs >=2 spaces).

    Collapses the leading whitespace of any non-empty content line to exactly
    four spaces; blank lines are returned as-is.
    """
    if not line.strip():
        return ""
    return "    " + line.strip()


def collapse_to_single_test_case(code: str, test_name: str | None = None) -> str:
    """Collapse the *** Test Cases *** section(s) to a single test case.

    Handles both simple over-splitting and the malformed repeated-header case.
    Returns the code unchanged when there is at most one test case and no
    duplicate headers to clean up.
    """
    if "*** test cases ***" not in code.lower():
        return code

    sections = _split_sections_robust(code)

    # Gather all test cases across the (now-merged) test-cases section.
    tc_body: list[str] = []
    for name, body in sections:
        if name == "test cases":
            tc_body.extend(body)
    tests = _parse_test_cases(tc_body)

    # Decide whether any change is needed.
    header_count = sum(
        1 for ln in code.splitlines() if _section_name(ln) == "test cases"
    )
    if len(tests) <= 1 and header_count <= 1:
        return code

    logger.info(
        "robot_postprocess: normalizing %d test-case fragment(s) / %d header(s) into 1",
        len(tests), header_count,
    )

    doc_line: str | None = None
    tag_line: str | None = None
    setup_line: str | None = None
    teardown_line: str | None = None
    step_lines: list[str] = []

    for _tname, steps in tests:
        for s in steps:
            if _DOC_RE.match(s):
                if doc_line is None:
                    doc_line = "    " + s.strip()
                continue
            if _TAGS_RE.match(s):
                if tag_line is None:
                    tag_line = "    " + s.strip()
                continue
            if _SETUP_RE.match(s):
                if setup_line is None:
                    setup_line = "    " + s.strip()
                continue
            if _TEARDOWN_RE.match(s):
                teardown_line = "    " + s.strip()  # keep the last one
                continue
            step_lines.append(_normalize_step_indent(s))

    # Trim leading/trailing blank steps.
    while step_lines and not step_lines[0].strip():
        step_lines.pop(0)
    while step_lines and not step_lines[-1].strip():
        step_lines.pop()

    final_name = (test_name or (tests[0][0] if tests else "Test Case")).strip()

    tc_section = ["*** Test Cases ***", final_name]
    if doc_line:
        tc_section.append(doc_line)
    if tag_line:
        tc_section.append(tag_line)
    if setup_line:
        tc_section.append(setup_line)
    tc_section.extend(step_lines)
    if teardown_line:
        tc_section.append(teardown_line)

    # Rebuild the document: keep non-test-case sections in their original
    # position, replace the (first) test-cases section with the single one.
    out_blocks: list[list[str]] = []
    test_section_emitted = False
    for name, body in sections:
        if name == "":
            # preamble (lines before first header) — keep only non-blank noise
            trimmed = [ln for ln in body]
            if any(l.strip() for l in trimmed):
                out_blocks.append(trimmed)
            continue
        if name == "test cases":
            if not test_section_emitted:
                out_blocks.append(tc_section)
                test_section_emitted = True
            continue
        # other sections: re-emit header + body
        header = f"*** {name.title()} ***" if name != "test cases" else "*** Test Cases ***"
        block = [header] + body
        # strip trailing blank lines within the block
        while block and not block[-1].strip():
            block.pop()
        out_blocks.append(block)

    # Join blocks with a single blank line between them for readability.
    result_lines: list[str] = []
    for block in out_blocks:
        if not block:
            continue
        if result_lines:
            result_lines.append("")  # blank line separates sections
        result_lines.extend(block)
    return "\n".join(result_lines).rstrip() + "\n"


def count_test_cases(code: str) -> int:
    """Return the number of test cases under *** Test Cases *** (0 if none)."""
    if "*** test cases ***" not in code.lower():
        return 0
    sections = _split_sections_robust(code)
    tc_body: list[str] = []
    for name, body in sections:
        if name == "test cases":
            tc_body.extend(body)
    return len(_parse_test_cases(tc_body))


def _collect_section(sections: list[tuple[str, list[str]]], name: str) -> list[str]:
    """Return the concatenated body lines for a given section across a doc."""
    out: list[str] = []
    for sec_name, body in sections:
        if sec_name == name:
            out.extend(body)
    return out


def _dedupe_keep_order(lines: list[str]) -> list[str]:
    """Drop duplicate non-blank lines (by stripped text), preserving order.

    Consecutive blank lines are collapsed to a single blank line.
    """
    seen: set[str] = set()
    out: list[str] = []
    for ln in lines:
        key = ln.strip()
        if not key:
            if out and not out[-1].strip():
                continue  # collapse consecutive blanks
            out.append("")
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(ln)
    # trim leading/trailing blanks
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def _parse_keyword_defs(body_lines: list[str]) -> "dict[str, list[str]]":
    """Parse a *** Keywords *** body into {keyword_name: [definition_lines]}.

    A keyword name is a non-indented, non-empty, non-comment line; its body is
    the following indented lines. Later duplicates (same name) are ignored.
    """
    defs: dict[str, list[str]] = {}
    name: str | None = None
    block: list[str] = []

    def _flush() -> None:
        if name is not None and name not in defs:
            defs[name] = [name] + block

    for line in body_lines:
        if _section_name(line) is not None:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if name is not None:
                block.append(line)
            continue
        if line[:1] not in (" ", "\t"):
            _flush()
            name = stripped
            block = []
        else:
            if name is not None:
                block.append(line)
    _flush()
    return defs


def merge_robot_files(codes: list[str]) -> str:
    """Merge several single-test Robot files into one grouped suite file.

    - ``*** Settings ***``: union of unique lines (libraries/resources deduped).
    - ``*** Variables ***``: union of unique variable lines.
    - ``*** Test Cases ***``: every test case concatenated (one per input file);
      different manual test cases stay as distinct test cases.
    - ``*** Keywords ***``: union of keyword definitions, deduped by name.

    Non-Robot / empty inputs are skipped. A single input is returned as-is.
    """
    robot_codes = [c for c in codes if c and "*** test cases ***" in c.lower()]
    if not robot_codes:
        return "\n\n".join(c.strip() for c in codes if c and c.strip()) + "\n"
    if len(robot_codes) == 1:
        return robot_codes[0].strip() + "\n"

    settings: list[str] = []
    variables: list[str] = []
    test_cases: list[str] = []
    keyword_defs: dict[str, list[str]] = {}

    for code in robot_codes:
        sections = _split_sections_robust(code)
        settings.extend(_collect_section(sections, "settings"))
        variables.extend(_collect_section(sections, "variables"))

        tc_body = _collect_section(sections, "test cases")
        tc_lines = [ln for ln in tc_body if _section_name(ln) is None]
        # trim leading/trailing blanks per file
        while tc_lines and not tc_lines[0].strip():
            tc_lines.pop(0)
        while tc_lines and not tc_lines[-1].strip():
            tc_lines.pop()
        if tc_lines:
            if test_cases:
                test_cases.append("")  # blank line between test cases
            test_cases.extend(tc_lines)

        for kw_name, kw_lines in _parse_keyword_defs(
            _collect_section(sections, "keywords")
        ).items():
            keyword_defs.setdefault(kw_name, kw_lines)

    blocks: list[list[str]] = []

    settings = _dedupe_keep_order(settings)
    if settings:
        blocks.append(["*** Settings ***"] + settings)

    variables = _dedupe_keep_order(variables)
    if variables:
        blocks.append(["*** Variables ***"] + variables)

    if test_cases:
        blocks.append(["*** Test Cases ***"] + test_cases)

    if keyword_defs:
        kw_block: list[str] = ["*** Keywords ***"]
        for i, (_name, kw_lines) in enumerate(keyword_defs.items()):
            if i > 0:
                kw_block.append("")
            kw_block.extend(kw_lines)
        blocks.append(kw_block)

    result: list[str] = []
    for block in blocks:
        if result:
            result.append("")
        result.extend(block)
    return "\n".join(result).rstrip() + "\n"
