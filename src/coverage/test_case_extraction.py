"""test_case_extraction.py — structurally parse test-case datasets embedded
in free-form chat text, instead of asking an LLM to copy them.

Problem this fixes: when a user pastes/uploads a large test-case JSON
payload directly into a chat message and asks for coverage analysis, the
LLM-based ``ToolSelectionAgent`` used to have to echo the ENTIRE dataset
back as the ``test_cases`` argument of the ``analyze_test_case_coverage``
tool call. With a 250-token response cap (intentionally small — tool-call
arguments are normally tiny, e.g. ``{"framework_name": "Playwright"}``),
anything beyond ~1 full test case got silently truncated, and the
regex-based JSON parser downstream (``ToolSelectionAgent._parse_llm_response``)
would recover whatever partial/malformed fragment happened to survive —
producing a seemingly-random subset of the real dataset (e.g. 10 results
out of 38 test cases).

The fix: parse the embedded JSON structurally in Python BEFORE any LLM
call, strip it out of the message text (replaced with a short
placeholder) so no downstream LLM call wastes tokens re-reading or
re-copying it, and hand the parsed, normalized test-case list to the
pipeline via state — ``execute_tools`` (langgraph_pipeline.py) substitutes
it directly into the ``analyze_test_case_coverage`` tool call's
arguments, bypassing the LLM entirely for the bulk payload.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["extract_test_cases_from_text"]

# Keys that commonly appear in a test-case record, across the various
# shapes real teams export (Excel-derived JSON, BDD-style step lists,
# generic QA tooling exports, etc.). A dict counts as "test-case-like" if
# it has at least 2 of these (case-insensitive).
_RECOGNIZED_KEYS = {
    "id", "test_id", "testid", "no", "#",
    "description", "desc", "name", "title", "test_case_title", "test name",
    "required_capability", "capability", "type", "category", "test_type",
    "area", "feature",
    "steps", "step", "test steps", "actions", "procedure",
    "expected_result", "expected", "result", "outcome", "assertion",
    "pre_condition", "preconditions", "precondition", "dataset", "enabled",
}

_MAX_BRACKET_ATTEMPTS = 8  # cap scan cost on adversarial/huge input


def _is_test_case_like(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    keys = {str(k).strip().lower() for k in item.keys()}
    return len(keys & _RECOGNIZED_KEYS) >= 2


def _unwrap_envelope(data: Any) -> Any:
    """Flatten common wrapper shapes: ``{"test_cases": [...]}``,
    ``{"data": [...]}``, or a list containing one or more such wrapper
    objects — mirrors ``_pages/coverage.py::_unwrap_test_cases``."""
    if isinstance(data, dict):
        if isinstance(data.get("test_cases"), list):
            return data["test_cases"]
        if isinstance(data.get("data"), list):
            return data["data"]
        return [data]
    if isinstance(data, list):
        flattened: list = []
        found_wrapper = False
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("test_cases"), list):
                flattened.extend(item["test_cases"])
                found_wrapper = True
            elif isinstance(item, dict) and isinstance(item.get("data"), list):
                flattened.extend(item["data"])
                found_wrapper = True
            else:
                flattened.append(item)
        return flattened if found_wrapper else data
    return data


def _looks_like_test_case_list(data: Any) -> list[dict] | None:
    """Return the unwrapped list of test-case dicts if `data` plausibly
    represents a test-case dataset, else None.

    Small lists (<=3) must be unanimous; larger lists need a solid
    majority — tolerates a few malformed/sparse entries without
    false-rejecting a real dataset, while still refusing to treat
    arbitrary unrelated JSON as test cases.
    """
    unwrapped = _unwrap_envelope(data)
    if not isinstance(unwrapped, list) or not unwrapped:
        return None
    hits = sum(1 for item in unwrapped if _is_test_case_like(item))
    threshold = 1.0 if len(unwrapped) <= 3 else 0.6
    if hits / len(unwrapped) >= threshold:
        return [item for item in unwrapped if isinstance(item, dict)]
    return None


def _pick(raw: dict, keys: list[str], default: Any = "") -> Any:
    for k in keys:
        if k in raw and raw[k] not in (None, ""):
            return raw[k]
    lower_map = {str(k).strip().lower(): v for k, v in raw.items()}
    for k in keys:
        v = lower_map.get(k.lower())
        if v not in (None, ""):
            return v
    return default


def _normalise_test_case(raw: dict, idx: int) -> dict:
    """Map any recognized test-case shape onto the canonical fields
    ``analyze_coverage()`` (src/coverage/engine.py) expects: id,
    description, required_capability, steps, expected_result.

    Handles both the "flat, already-canonical" shape (id/description/
    required_capability/steps/expected_result) and the common
    BDD-export shape (test_case_title/category/steps-as-list-of-dicts).
    """
    tc_id = _pick(raw, ["id", "test_id", "testid", "no", "#"], f"TC{idx + 1:03d}")
    description = _pick(raw, ["description", "desc", "name", "title", "test_case_title"], "")
    capability = _pick(
        raw,
        ["required_capability", "capability", "type", "category", "test_type", "area", "feature"],
        "ui automation",
    )

    steps_raw = raw.get("steps") or raw.get("step") or raw.get("actions") or ""
    if isinstance(steps_raw, list):
        step_lines = []
        for s in steps_raw:
            if isinstance(s, dict):
                action = str(s.get("action", "")).strip()
                expected = str(s.get("expected_result", "")).strip()
                step_lines.append(f"{action} -> {expected}" if expected else action)
            else:
                step_lines.append(str(s))
        steps = "; ".join(filter(None, step_lines))
    else:
        steps = str(steps_raw)

    expected_result = _pick(raw, ["expected_result", "expected", "result", "outcome", "assertion"], "")
    if not expected_result and isinstance(steps_raw, list) and steps_raw:
        last = steps_raw[-1]
        if isinstance(last, dict):
            expected_result = str(last.get("expected_result", ""))

    return {
        "id": str(tc_id),
        "description": str(description) or str(tc_id),
        "required_capability": str(capability).strip().lower() or "ui automation",
        "steps": steps,
        "expected_result": str(expected_result),
    }


def _placeholder(count: int) -> str:
    return (
        f"[{count} test case(s) already parsed from the attached data — "
        f"do not re-list or re-type them, just reference the count.]"
    )


def extract_test_cases_from_text(text: str) -> tuple[list[dict], str]:
    """Scan `text` for an embedded JSON test-case dataset.

    Returns ``(test_cases, cleaned_text)``:
      - ``test_cases``: normalized list of dicts ready for
        ``analyze_coverage()``, or ``[]`` if nothing recognizable was found.
      - ``cleaned_text``: ``text`` with the matched JSON span replaced by a
        short placeholder (so callers can hand a MUCH smaller message to
        every downstream LLM call instead of the raw payload) — identical
        to the input if nothing was found.
    """
    if not text or ("{" not in text and "[" not in text):
        return [], text

    decoder = json.JSONDecoder()

    # Fast path: the whole message IS the JSON payload (a raw paste).
    stripped = text.strip()
    if stripped and stripped[0] in "[{":
        try:
            data = json.loads(stripped)
            tcs = _looks_like_test_case_list(data)
            if tcs is not None:
                normalised = [_normalise_test_case(tc, i) for i, tc in enumerate(tcs)]
                return normalised, _placeholder(len(normalised))
        except json.JSONDecodeError:
            pass

    # Slow path: JSON embedded alongside natural-language text — scan each
    # bracket occurrence and try to decode a complete JSON value starting
    # there, bounded to avoid pathological cost on huge/adversarial input.
    attempts = 0
    for match in re.finditer(r"[\[{]", text):
        if attempts >= _MAX_BRACKET_ATTEMPTS:
            break
        start = match.start()
        try:
            data, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        attempts += 1
        tcs = _looks_like_test_case_list(data)
        if tcs is None:
            continue
        normalised = [_normalise_test_case(tc, i) for i, tc in enumerate(tcs)]
        cleaned = text[:start] + _placeholder(len(normalised)) + text[end:]
        return normalised, cleaned

    return [], text
