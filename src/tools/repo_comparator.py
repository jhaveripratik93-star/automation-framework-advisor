"""repo_comparator.py — Cross-repo test case parity analysis.

Extracts test cases from two repos (different frameworks), matches them
by name using exact + fuzzy token-overlap, and produces a parity report
showing assertion counts side-by-side for every matched/unmatched pair.

Supported frameworks: Robot Framework, Playwright, Selenium, K6, Cypress,
Jest, Mocha (auto-detected from file extensions + content).
"""
from __future__ import annotations

import ast
import io
import re
import zipfile
from pathlib import PurePosixPath
from typing import NamedTuple

from src.tools.assertion_counter import count as count_assertions

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class TestCase(NamedTuple):
    name: str           # normalised name (lowercase, underscores)
    display_name: str   # original name as found in source
    file: str           # relative file path
    assertions: int     # assertion count


class MatchedPair(NamedTuple):
    name: str           # canonical normalised name
    display_a: str
    display_b: str
    file_a: str
    file_b: str
    assertions_a: int
    assertions_b: int
    score: float        # match confidence 0-1 (1.0 = exact)

    @property
    def status(self) -> str:
        if self.assertions_a == self.assertions_b:
            return "match"
        return "mismatch"


class UnmatchedCase(NamedTuple):
    display_name: str
    file: str
    assertions: int
    repo: str           # "A" or "B"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compare_repos(
    files_a: list[dict],   # [{"filename": str, "content": str}, ...]
    files_b: list[dict],
    framework_a: str,
    framework_b: str,
    fuzzy_threshold: float = 0.3,
) -> dict:
    """Extract test cases from both repos and produce a parity report.

    Returns:
        {
            "matched":   [MatchedPair, ...],
            "only_in_a": [UnmatchedCase, ...],
            "only_in_b": [UnmatchedCase, ...],
            "framework_a": str,
            "framework_b": str,
            "summary": {
                "total_a": int, "total_b": int,
                "matched_count": int, "exact_matches": int,
                "fuzzy_matches": int, "only_a": int, "only_b": int,
                "assertion_match": int, "assertion_mismatch": int,
            }
        }
    """
    cases_a = _extract_all(files_a, framework_a)
    cases_b = _extract_all(files_b, framework_b)

    matched, only_a, only_b = _match(cases_a, cases_b, fuzzy_threshold)

    a_match = sum(1 for m in matched if m.status == "match")
    a_mis   = sum(1 for m in matched if m.status == "mismatch")

    return {
        "matched":     matched,
        "only_in_a":   only_a,
        "only_in_b":   only_b,
        "framework_a": framework_a,
        "framework_b": framework_b,
        "summary": {
            "total_a":            len(cases_a),
            "total_b":            len(cases_b),
            "matched_count":      len(matched),
            "only_a":             len(only_a),
            "only_b":             len(only_b),
            "assertion_match":    a_match,
            "assertion_mismatch": a_mis,
        },
    }


def extract_from_uploaded(uploaded_files, framework: str) -> list[dict]:
    """Read Streamlit UploadedFile objects (including ZIPs) into file dicts."""
    _SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv",
                  "dist", "build", ".idea", ".vscode", "target"}
    _SOURCE_EXTS = {".py", ".js", ".ts", ".robot", ".feature", ".jsx", ".tsx"}

    payload: list[dict] = []
    for uf in uploaded_files:
        name = uf.name
        if name.lower().endswith(".zip"):
            try:
                data = uf.read()
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        parts = PurePosixPath(info.filename).parts
                        if any(seg in _SKIP_DIRS for seg in parts):
                            continue
                        if PurePosixPath(info.filename).suffix.lower() not in _SOURCE_EXTS:
                            continue
                        try:
                            content = zf.read(info).decode("utf-8", errors="replace")
                            payload.append({"filename": info.filename, "content": content})
                        except Exception:
                            continue
            except zipfile.BadZipFile:
                pass
        else:
            try:
                payload.append({"filename": name,
                                 "content": uf.read().decode("utf-8", errors="replace")})
            except Exception:
                pass
    return payload


# ---------------------------------------------------------------------------
# Test case extraction
# ---------------------------------------------------------------------------

# Robot Framework: non-indented line inside *** Test Cases *** section
_RF_SECTION  = re.compile(r"^\*{3}\s*(.+?)\s*\*{3}", re.MULTILINE)
_RF_TC_NAME  = re.compile(r"^([^\s#\*][^\n]*)", re.MULTILINE)

# K6: check() call with a label comment or surrounding describe/group
_K6_DESCRIBE   = re.compile(r"(?:describe|group)\s*\(\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
_K6_DEFAULT    = re.compile(r"export\s+default\s+(?:async\s+)?function\s*(\w*)", re.MULTILINE)
# Matches `const res = http.METHOD(...)` — used to infer a test boundary label
_K6_HTTP_CALL  = re.compile(
    r"(?:const|let|var)\s+(\w+)\s*=\s*http\.(?:get|post|put|del|patch|head)\s*\(",
    re.IGNORECASE,
)

# JS/TS: it('...') / test('...')
_JS_TEST     = re.compile(r"""(?:^|\s)(?:it|test)\s*\(\s*['"`]([^'"`]+)['"`]""", re.MULTILINE)
_JS_DESCRIBE = re.compile(r"""describe\s*\(\s*['"`]([^'"`]+)['"`]""", re.MULTILINE)


def _extract_all(files: list[dict], framework: str) -> list[TestCase]:
    fw = framework.lower()
    cases: list[TestCase] = []
    for f in files:
        fname   = f["filename"]
        content = f["content"]
        ext     = PurePosixPath(fname).suffix.lower()

        if "robot" in fw or ext == ".robot":
            cases.extend(_extract_robot(content, fname, framework))
        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            if "k6" in fw or "k6" in fname.lower():
                cases.extend(_extract_k6(content, fname, framework))
            else:
                cases.extend(_extract_js(content, fname, framework))
        elif ext == ".py":
            cases.extend(_extract_python(content, fname, framework))

    return cases


def _extract_robot(code: str, fname: str, framework: str) -> list[TestCase]:
    sections = {}
    matches  = list(_RF_SECTION.finditer(code))
    for i, m in enumerate(matches):
        key   = m.group(1).strip().lower()
        start = m.end()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(code)
        sections[key] = code[start:end]

    tc_block = sections.get("test cases", "")
    cases: list[TestCase] = []

    # Parse the block ONCE into {display_name: body_text} slices
    tc_bodies: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in tc_block.splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            if current_name is not None:
                tc_bodies[current_name] = "\n".join(current_lines)
            current_name = line.strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_name is not None:
        tc_bodies[current_name] = "\n".join(current_lines)

    for display, body in tc_bodies.items():
        cases.append(TestCase(
            name=_normalise(display),
            display_name=display,
            file=fname,
            assertions=count_assertions(body, framework),
        ))
    return cases


def _extract_python(code: str, fname: str, framework: str) -> list[TestCase]:
    cases: list[TestCase] = []
    try:
        tree  = ast.parse(code)
        lines = code.splitlines(keepends=True)
    except SyntaxError:
        return cases

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        start = node.lineno - 1
        end   = node.end_lineno  # type: ignore[attr-defined]
        body  = "".join(lines[start:end])
        cases.append(TestCase(
            name=_normalise(node.name),
            display_name=node.name,
            file=fname,
            assertions=count_assertions(body, framework),
        ))
    return cases


def _extract_k6(code: str, fname: str, framework: str) -> list[TestCase]:
    """Extract K6 test cases.

    Priority:
    1. describe/group blocks  → one TestCase per block
    2. http.METHOD() calls    → one TestCase per HTTP call (name from variable)
    3. Fallback               → whole file as one TestCase
    """
    cases: list[TestCase] = []

    # 1. describe/group blocks
    groups = _K6_DESCRIBE.findall(code)
    if groups:
        for g in groups:
            cases.append(TestCase(
                name=_normalise(g),
                display_name=g,
                file=fname,
                assertions=count_assertions(code, framework) // max(len(groups), 1),
            ))
        return cases

    # 2. Split by HTTP calls — each http.get/post/... is a logical test boundary
    http_matches = list(_K6_HTTP_CALL.finditer(code))
    if len(http_matches) > 1:
        lines = code.splitlines()
        for idx, m in enumerate(http_matches):
            start_ln = code[:m.start()].count("\n")
            if idx + 1 < len(http_matches):
                end_ln = code[:http_matches[idx + 1].start()].count("\n")
            else:
                end_ln = len(lines)
            block    = "\n".join(lines[start_ln:end_ln])
            call_line = lines[start_ln].strip()

            # Prefer a // comment within 3 lines before the http call
            # e.g. "// TC2: Get user profile" -> "Get user profile"
            comment_display: str | None = None
            for lookback in range(1, 4):
                if start_ln - lookback < 0:
                    break
                prev = lines[start_ln - lookback].strip()
                if not prev:  # skip blank lines
                    continue
                cm = re.match(r"//+\s*(?:TC\d+[:\-]?\s*)?(.*)", prev)
                if cm and cm.group(1).strip():
                    comment_display = cm.group(1).strip()
                break  # stop at first non-blank line regardless

            if comment_display:
                display = comment_display
            else:
                method_m = re.search(
                    r"http\.(\w+)\s*\(\s*(?:['\"`])([^'\"` ]+)(?:['\"`])",
                    call_line,
                )
                if method_m:
                    path = re.sub(r"^[^/]*", "", method_m.group(2))
                    display = f"{method_m.group(1).upper()} {path or method_m.group(2)}"
                else:
                    display = m.group(1)

            cases.append(TestCase(
                name=_normalise(display),
                display_name=display,
                file=fname,
                assertions=count_assertions(block, framework),
            ))
        return cases

    # 3. Single http call — use comment above it or default export name
    if not http_matches:
        return cases
    m_http = http_matches[0]
    start_ln = code[:m_http.start()].count("\n")
    lines = code.splitlines()
    comment_display = None
    for lookback in range(1, 4):
        if start_ln - lookback < 0:
            break
        prev = lines[start_ln - lookback].strip()
        if not prev:
            continue
        cm = re.match(r"//+\s*(?:TC\d+[:\-]?\s*)?(.*)", prev)
        if cm and cm.group(1).strip():
            comment_display = cm.group(1).strip()
        break
    if comment_display:
        display = comment_display
    else:
        m_def = _K6_DEFAULT.search(code)
        display = (m_def.group(1).strip() if (m_def and m_def.group(1).strip())
                   else PurePosixPath(fname).stem)
    cases.append(TestCase(
        name=_normalise(display),
        display_name=display,
        file=fname,
        assertions=count_assertions(code, framework),
    ))
    return cases


def _extract_js(code: str, fname: str, framework: str) -> list[TestCase]:
    """Cypress / Jest / Mocha: it('...') / test('...')."""
    cases: list[TestCase] = []
    for m in _JS_TEST.finditer(code):
        display = m.group(1).strip()
        # Approximate body: 20 lines after the match
        start_line = code[:m.start()].count("\n")
        body_lines = code.splitlines()[start_line: start_line + 20]
        body = "\n".join(body_lines)
        cases.append(TestCase(
            name=_normalise(display),
            display_name=display,
            file=fname,
            assertions=count_assertions(body, framework),
        ))
    return cases


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _match(
    cases_a: list[TestCase],
    cases_b: list[TestCase],
    threshold: float,
) -> tuple[list[MatchedPair], list[UnmatchedCase], list[UnmatchedCase]]:
    matched:  list[MatchedPair]   = []
    used_b:   set[int]            = set()

    for tc_a in cases_a:
        best_idx   = -1
        best_score = 0.0

        for i, tc_b in enumerate(cases_b):
            if i in used_b:
                continue
            score = _similarity(tc_a.name, tc_b.name)
            if score > best_score:
                best_score = score
                best_idx   = i

        if best_idx >= 0 and best_score >= threshold:
            tc_b = cases_b[best_idx]
            used_b.add(best_idx)
            matched.append(MatchedPair(
                name=tc_a.name,
                display_a=tc_a.display_name,
                display_b=tc_b.display_name,
                file_a=tc_a.file,
                file_b=tc_b.file,
                assertions_a=tc_a.assertions,
                assertions_b=tc_b.assertions,
                score=best_score,
            ))
        else:
            # No match found — will go into only_a
            pass

    # Collect unmatched
    matched_a_names = {m.display_a for m in matched}
    matched_b_idxs  = used_b

    only_a = [
        UnmatchedCase(tc.display_name, tc.file, tc.assertions, "A")
        for tc in cases_a if tc.display_name not in matched_a_names
    ]
    only_b = [
        UnmatchedCase(cases_b[i].display_name, cases_b[i].file, cases_b[i].assertions, "B")
        for i in range(len(cases_b)) if i not in matched_b_idxs
    ]

    return matched, only_a, only_b


def _similarity(a: str, b: str) -> float:
    """Token overlap similarity — Jaccard on word tokens."""
    if a == b:
        return 1.0
    ta = set(_tokens(a))
    tb = set(_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _tokens(name: str) -> list[str]:
    """Split normalised name into meaningful tokens, drop stop words."""
    _STOP = {
        "test", "the", "a", "an", "with", "for", "and", "or", "is",
        "should", "when", "given", "then", "verify", "check", "validate",
        # Common filler words in test names across frameworks
        "returns", "return", "response", "request", "key", "value",
        "that", "has", "have", "be", "are", "not", "no", "on", "in",
        "valid", "invalid", "credentials", "success", "successfully",
        "expected", "correct", "incorrect", "proper",
        # HTTP status codes as words
        "200", "201", "400", "401", "403", "404", "500",
    }
    return [t for t in re.split(r"[\s_\-]+", name.lower()) if t and t not in _STOP]


def _normalise(name: str) -> str:
    """Lowercase, strip test_ prefix, HTTP method prefix, replace spaces/hyphens with underscores."""
    n = name.lower().strip()
    n = re.sub(r"^test[_\s]+", "", n)
    # Strip leading HTTP method (GET, POST, PUT, DELETE, PATCH) so
    # "POST /login" and "Login With Valid Credentials" share the token "login"
    n = re.sub(r"^(get|post|put|delete|patch|head)\s+", "", n)
    # Strip leading slashes from URL paths: /login -> login
    n = re.sub(r"^/+", "", n)
    n = re.sub(r"[\s\-/]+", "_", n)
    n = re.sub(r"[^\w]", "", n)
    return n


# ---------------------------------------------------------------------------
# CSV export helper
# ---------------------------------------------------------------------------

def to_csv(report: dict) -> str:
    """Render the full report as a CSV string for download."""
    rows = ["Test Case,File A,File B,Assertions A,Assertions B,Delta,Status"]

    for m in report["matched"]:
        delta   = m.assertions_b - m.assertions_a
        display = m.display_a if len(m.display_a) >= len(m.display_b) else m.display_b
        rows.append(
            f'"{display}","{m.file_a}","{m.file_b}",'
            f"{m.assertions_a},{m.assertions_b},{delta:+d},{m.status}"
        )
    for u in report["only_in_a"]:
        rows.append(f'"{u.display_name}","{u.file}","",{u.assertions},—,—,only_a')
    for u in report["only_in_b"]:
        rows.append(f'"{u.display_name}","","{u.file}",—,{u.assertions},—,only_b')

    return "\n".join(rows)
