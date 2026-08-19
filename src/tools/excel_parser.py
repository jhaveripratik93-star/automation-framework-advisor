"""Excel test case parser — reads .xlsx files and returns structured test case dicts.

Expected columns (case-insensitive, order-independent):
  Test ID | Description | Capability | Steps | Expected Result

Any missing column is filled with a sensible default so the parser never crashes.
"""
from __future__ import annotations

import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Canonical column names → list of accepted header variants
_COL_ALIASES: dict[str, list[str]] = {
    "id":          ["test id", "id", "test_id", "testid", "no", "#"],
    "description": ["description", "desc", "test name", "name", "title", "summary"],
    "capability":  ["capability", "type", "category", "test type", "area", "feature"],
    "steps":       ["steps", "step", "test steps", "actions", "procedure"],
    "expected":    ["expected result", "expected", "result", "outcome", "assertion"],
}


def _match_header(raw: str) -> str | None:
    """Return canonical key for a raw header string, or None if unrecognised."""
    clean = raw.strip().lower()
    for canonical, aliases in _COL_ALIASES.items():
        if clean in aliases:
            return canonical
    return None


def parse_excel_test_cases(file_bytes: bytes) -> list[dict[str, Any]]:
    """Parse an Excel file and return a list of test case dicts.

    Each dict has keys: id, description, capability, steps, expected.
    Rows where every meaningful cell is empty are skipped.

    Args:
        file_bytes: Raw bytes of the .xlsx file (from st.file_uploader.read()).

    Returns:
        List of test case dicts, or [] on parse failure.
    """
    try:
        import openpyxl
    except ImportError:
        logger.error("openpyxl not installed — run: pip install openpyxl")
        return []

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    except Exception as exc:
        logger.error("Excel parse failed: %s", exc)
        return []

    if not rows:
        return []

    # Build column index map from first non-empty row
    header_row = rows[0]
    col_map: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        if cell is None:
            continue
        canonical = _match_header(str(cell))
        if canonical and canonical not in col_map:
            col_map[canonical] = idx

    logger.info("Excel headers mapped: %s", col_map)

    # Build a raw-header → col-index map for ALL columns (not just known ones)
    raw_col_map: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        if cell is not None:
            raw_col_map[str(cell).strip()] = idx

    def _get(row: tuple, key: str, default: str = "") -> str:
        idx = col_map.get(key)
        if idx is None or idx >= len(row):
            return default
        val = row[idx]
        return str(val).strip() if val is not None else default

    def _get_raw(row: tuple, raw_key: str, default: str = "") -> str:
        idx = raw_col_map.get(raw_key)
        if idx is None or idx >= len(row):
            return default
        val = row[idx]
        return str(val).strip() if val is not None else default

    test_cases: list[dict[str, Any]] = []
    for row_num, row in enumerate(rows[1:], start=2):
        # Skip fully empty rows
        if all(c is None or str(c).strip() == "" for c in row):
            continue

        tc_id = _get(row, "id") or f"TC{row_num:03d}"
        capability = _get(row, "capability", "ui automation")

        # Start with canonical fields
        tc: dict[str, Any] = {
            "id": tc_id,
            "description": _get(row, "description", "No description"),
            "required_capability": capability.lower(),
            "steps": _get(row, "steps"),
            "expected_result": _get(row, "expected"),
        }

        # Preserve ALL extra columns as-is so nothing is lost
        for raw_key, col_idx in raw_col_map.items():
            if col_idx < len(row) and row[col_idx] is not None:
                normalised = raw_key.lower().replace(" ", "_")
                if normalised not in tc:
                    tc[normalised] = str(row[col_idx]).strip()

        test_cases.append(tc)

    logger.info("Excel parsed: %d test cases", len(test_cases))
    return test_cases


def summarise_excel(test_cases: list[dict]) -> str:
    """Return a short markdown summary of parsed test cases for display."""
    if not test_cases:
        return "No test cases parsed."

    cap_counts: dict[str, int] = {}
    for tc in test_cases:
        cap = tc.get("required_capability", "unknown")
        cap_counts[cap] = cap_counts.get(cap, 0) + 1

    lines = [f"**{len(test_cases)} test cases loaded**\n", "| Capability | Count |", "|---|---|"]
    for cap, count in sorted(cap_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {cap.title()} | {count} |")
    return "\n".join(lines)
