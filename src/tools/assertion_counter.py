"""assertion_counter.py — Count assertions in test code across frameworks.

Counts assertion statements/keywords so callers can verify that a
converted file preserves the same number of assertions as the source.

Key design: framework-specific pre-processors expand multi-assertion
constructs (e.g. K6 check() blocks, Python `assert a and b`) into one
logical unit per assertion BEFORE line-counting.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Per-framework line-level assertion patterns (case-insensitive)
# Applied AFTER pre-processing expands multi-assertion blocks.
# ---------------------------------------------------------------------------
_PATTERNS: dict[str, list[str]] = {
    "robot framework": [
        r"^\s+should\s+",
        r"^\s+status should be",
        r"^\s+element should",
        r"^\s+page should",
        r"^\s+location should",
        r"^\s+title should",
        r"^\s+dictionary should",
        r"^\s+list should",
        r"^\s+variable should",
        r"^\s+run keyword and expect error",
    ],
    "playwright": [
        r"\bexpect\s*\(",
        r"\.to_be_visible\(",
        r"\.to_have_text\(",
        r"\.to_have_value\(",
        r"\.to_be_checked\(",
        r"\.to_be_enabled\(",
        r"\.to_be_disabled\(",
        r"\.to_have_url\(",
        r"\.to_have_title\(",
        r"\.to_contain_text\(",
        r"\bassert\b",
    ],
    "selenium": [
        r"\bassert\b",
        r"\bassertEqual\b",
        r"\bassertTrue\b",
        r"\bassertFalse\b",
        r"\bassertIn\b",
        r"\bassertIsNotNone\b",
        r"\.assert_",
        r"pytest\.raises\(",
    ],
    # K6: after pre-processing, each check entry becomes its own synthetic line
    # tagged __k6_check__ so the pattern below matches exactly once per entry.
    "k6": [
        r"__k6_check__",
        r"\bexpect\s*\(",
    ],
}

_GENERIC = [r"\bassert\b", r"\bexpect\b", r"\bshould\b", r"\bcheck\b"]


def _compiled(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


_COMPILED: dict[str, list[re.Pattern]] = {
    fw: _compiled(pats) for fw, pats in _PATTERNS.items()
}
_COMPILED_GENERIC = _compiled(_GENERIC)


# ---------------------------------------------------------------------------
# Pre-processors — expand multi-assertion constructs into one line per entry
# ---------------------------------------------------------------------------

# Matches the entire check(res, { ... }) block (possibly multi-line)
_K6_CHECK_BLOCK = re.compile(
    r"\bcheck\s*\([^,]+,\s*\{([^}]*)\}\s*\)",
    re.DOTALL,
)
# Matches a single key: value entry inside the check object
_K6_CHECK_ENTRY = re.compile(r"['\"]([^'\"]+)['\"]\s*:", re.DOTALL)


def _preprocess_k6(code: str) -> str:
    """Replace each check() block with N synthetic __k6_check__ lines,
    one per assertion entry in the object literal."""
    def _expand(m: re.Match) -> str:
        entries = _K6_CHECK_ENTRY.findall(m.group(1))
        if not entries:
            return "__k6_check__\n"  # at least 1 if we can't parse entries
        return "\n".join(f"__k6_check__  # {e}" for e in entries) + "\n"

    return _K6_CHECK_BLOCK.sub(_expand, code)


# assert a == 1 and b == 2  →  two assertions
_PY_ASSERT_AND = re.compile(r"\bassert\b(.+)", re.IGNORECASE)


def _preprocess_python(code: str) -> str:
    """Split `assert a and b` into multiple assert lines."""
    out: list[str] = []
    for line in code.splitlines():
        m = _PY_ASSERT_AND.search(line)
        if m:
            # Count `and`/`or` conjunctions as additional assertions
            body = m.group(1)
            extras = len(re.findall(r"\b(?:and|or)\b", body, re.IGNORECASE))
            out.append(line)
            out.extend(["assert __split__"] * extras)
        else:
            out.append(line)
    return "\n".join(out)


_PREPROCESSORS = {
    "k6": _preprocess_k6,
    "playwright": _preprocess_python,
    "selenium": _preprocess_python,
}


def _resolve_fw_key(fw: str) -> str | None:
    for key in _COMPILED:
        if key in fw or fw in key:
            return key
    return None


def count(code: str, framework: str) -> int:
    """Return the number of individual assertions in *code* for *framework*."""
    fw = framework.lower()
    key = _resolve_fw_key(fw)
    patterns = _COMPILED.get(key) if key else _COMPILED_GENERIC

    # Apply framework-specific pre-processor if available
    preprocessor = _PREPROCESSORS.get(key) if key else None
    processed = preprocessor(code) if preprocessor else code

    total = 0
    for line in processed.splitlines():
        if any(p.search(line) for p in patterns):
            total += 1
    return total


def compare(
    source_code: str,
    converted_code: str,
    from_framework: str,
    to_framework: str,
) -> dict:
    """Compare assertion counts between source and converted code.

    Returns:
        {
            "source_count":    int,
            "converted_count": int,
            "match":           bool,
            "delta":           int,   # converted - source (negative = lost assertions)
            "status":          "pass" | "warn" | "fail",
            "message":         str,
        }
    """
    src = count(source_code, from_framework)
    cvt = count(converted_code, to_framework)
    delta = cvt - src
    if src == 0:
        status = "pass"
        msg = "No assertions detected in source — nothing to verify."
    elif delta == 0:
        status = "pass"
        msg = f"✅ All {src} assertion(s) accounted for."
    elif delta > 0:
        status = "warn"
        msg = f"⚠️ {cvt} assertions found vs {src} in source (+{delta} extra)."
    else:
        status = "fail"
        msg = f"❌ {abs(delta)} assertion(s) may be missing ({src} → {cvt})."

    return {
        "source_count": src,
        "converted_count": cvt,
        "match": delta == 0,
        "delta": delta,
        "status": status,
        "message": msg,
    }


def compare_multi(
    source_files: list[dict],
    converted_files: dict[str, str],
    from_framework: str,
    to_framework: str,
) -> dict:
    """Aggregate assertion comparison across multiple files.

    Args:
        source_files:    [{"filename": str, "content": str}, ...]
        converted_files: {output_path: code, ...}

    Returns same shape as compare() plus per-file breakdown.
    """
    total_src = sum(count(f["content"], from_framework) for f in source_files)
    total_cvt = sum(count(c, to_framework) for c in converted_files.values())

    # Per-file breakdown (source side)
    breakdown: list[dict] = []
    for f in source_files:
        src_n = count(f["content"], from_framework)
        breakdown.append({"file": f["filename"], "source_assertions": src_n})

    delta = total_cvt - total_src
    if total_src == 0:
        status, msg = "pass", "No assertions detected in source — nothing to verify."
    elif delta == 0:
        status = "pass"
        msg = f"✅ All {total_src} assertion(s) preserved across {len(source_files)} file(s)."
    elif delta > 0:
        status = "warn"
        msg = f"⚠️ {total_cvt} assertions found vs {total_src} in source (+{delta} extra)."
    else:
        status = "fail"
        msg = f"❌ {abs(delta)} assertion(s) may be missing ({total_src} → {total_cvt})."

    return {
        "source_count": total_src,
        "converted_count": total_cvt,
        "match": delta == 0,
        "delta": delta,
        "status": status,
        "message": msg,
        "breakdown": breakdown,
    }
