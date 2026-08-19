"""Coverage engine — generic, YAML-derived capability analysis.

Key design decisions:
  1. capability_map is AUTO-DERIVED from all loaded framework YAML files —
     no hardcoded capability strings.
  2. Support levels are classified: native / plugin / third-party / partial / false
     so "third-party only" is NOT treated the same as native=true.
  3. Semantic fallback: unknown capability strings from Excel/JSON are matched
     against all known YAML keys using keyword overlap scoring.
  4. arch_map is also derived from architecture_fit keys present in YAMLs.
"""
from __future__ import annotations

import re
import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.knowledge_base.loader import KnowledgeBase

logger = logging.getLogger(__name__)

# ── Support level classification ──────────────────────────────────────────────

# Ordered from strongest to weakest
SUPPORT_LEVELS = ("native", "plugin", "third-party", "partial", "false")

# Keywords that indicate each level (checked against lowercased YAML value)
_LEVEL_SIGNALS: dict[str, list[str]] = {
    "plugin":      ["plugin", "extension", "addon", "add-on", "@axe", "cypress-", "eyes-"],
    "third-party": ["third-party", "third party", "thirdparty", "applitools", "percy",
                    "via selenium", "via cdp", "via pabot", "via cloud", "via grid",
                    "external", "only (", "only)"],
    "partial":     ["partial", "limited", "workaround", "experimental", "beta",
                    "via ", "community", "mobile web only"],
    "false":       ["false", "no", "none", "not supported"],
}


def classify_support(value: Any) -> str:
    """Return one of: native | plugin | third-party | partial | false."""
    if value is None or value is False or str(value).strip().lower() in ("false", "no", "none", ""):
        return "false"
    if value is True or str(value).strip().lower() in ("true", "yes", "native"):
        return "native"

    v = str(value).lower()
    for level, signals in _LEVEL_SIGNALS.items():
        if any(sig in v for sig in signals):
            return level
    # Non-empty string that didn't match any downgrade signal → treat as native
    return "native"


# ── Auto-derive capability_map from YAML files ────────────────────────────────

def build_capability_map(kb: "KnowledgeBase") -> dict[str, list[str]]:
    """Build a mapping: human-readable label → [yaml_capability_keys].

    Strategy:
      - Collect every capability key that appears in ANY framework YAML.
      - Group keys that share a common semantic root (e.g. all *_testing keys
        under "testing", all *_execution keys under "execution").
      - Also add direct 1-to-1 mappings so exact matches always work.
      - Additionally expose architecture_fit keys as capability labels.
    """
    all_cap_keys: set[str] = set()
    all_arch_keys: set[str] = set()

    for fw in kb.list_all():
        all_cap_keys.update(fw.capabilities.keys())
        all_arch_keys.update(fw.architecture_fit.keys())

    cap_map: dict[str, list[str]] = {}

    # 1. Direct 1-to-1: every YAML key maps to itself (label = key with spaces)
    for key in all_cap_keys:
        label = key.replace("_", " ").lower()
        cap_map.setdefault(label, [])
        if key not in cap_map[label]:
            cap_map[label].append(key)

    # 2. Semantic groupings — built from the actual keys present in YAMLs
    _semantic_groups: list[tuple[str, list[str]]] = [
        # UI / browser automation
        ("ui automation",       ["shadow_dom", "iframe_cross_origin", "multi_tab", "multi_domain",
                                  "auto_wait", "browser_testing", "file_upload_download"]),
        ("browser testing",     ["browser_testing", "shadow_dom", "multi_tab", "auto_wait",
                                  "cross_browser", "multi_browser"]),
        ("cross browser",       ["cross_browser", "multi_browser", "multi_domain"]),
        # API
        ("api testing",         ["api_testing", "rest_api", "graphql_support", "schema_validation",
                                  "json_path_support", "protocol_support"]),
        ("rest api",            ["api_testing", "rest_api", "protocol_support"]),
        ("graphql",             ["graphql_support", "api_testing"]),
        # Performance / load
        ("performance testing", ["performance_testing", "load_testing", "parallel_execution",
                                  "distributed_execution"]),
        ("load testing",        ["load_testing", "performance_testing", "parallel_execution",
                                  "distributed_execution"]),
        ("stress testing",      ["load_testing", "performance_testing"]),
        # Mobile
        ("mobile testing",      ["gesture_support", "device_farm_support", "native_mobile",
                                  "hybrid_mobile"]),
        ("mobile automation",   ["gesture_support", "device_farm_support"]),
        # Visual
        ("visual testing",      ["visual_regression"]),
        ("visual regression",   ["visual_regression"]),
        ("screenshot testing",  ["visual_regression"]),
        # Accessibility
        ("accessibility testing", ["accessibility_testing"]),
        ("a11y testing",          ["accessibility_testing"]),
        # Network
        ("network mocking",     ["network_interception"]),
        ("network interception",["network_interception"]),
        ("api mocking",         ["network_interception", "api_testing"]),
        ("service mocking",     ["network_interception"]),
        # Parallel
        ("parallel execution",  ["parallel_execution", "distributed_execution"]),
        ("parallel testing",    ["parallel_execution", "distributed_execution"]),
        # Component
        ("component testing",   ["component_testing"]),
        # E2E / regression / smoke — map to broad UI automation keys
        ("e2e testing",         ["shadow_dom", "multi_tab", "auto_wait", "browser_testing",
                                  "iframe_cross_origin"]),
        ("end to end",          ["shadow_dom", "multi_tab", "auto_wait", "browser_testing"]),
        ("regression testing",  ["shadow_dom", "multi_tab", "auto_wait", "browser_testing",
                                  "parallel_execution"]),
        ("smoke testing",       ["auto_wait", "browser_testing", "api_testing"]),
        ("functional testing",  ["shadow_dom", "auto_wait", "browser_testing", "api_testing"]),
        ("integration testing", ["api_testing", "network_interception", "microservices"]),
        # Security
        ("security testing",    ["network_interception", "api_testing"]),
        # Database
        ("database testing",    ["api_testing"]),
    ]

    for label, keys in _semantic_groups:
        # Only include keys that actually exist in at least one YAML
        valid_keys = [k for k in keys if k in all_cap_keys or k in all_arch_keys]
        if valid_keys:
            existing = cap_map.get(label, [])
            merged = existing + [k for k in valid_keys if k not in existing]
            cap_map[label] = merged

    # 3. Architecture fit keys as capability labels
    for key in all_arch_keys:
        label = key.replace("_", " ").lower()
        cap_map.setdefault(label, [])
        if key not in cap_map[label]:
            cap_map[label].append(key)

    logger.debug("capability_map built: %d labels, %d unique keys",
                 len(cap_map), len(all_cap_keys))
    return cap_map


# ── Semantic fallback for unknown capability strings ──────────────────────────

def resolve_capability(
    raw_capability: str,
    cap_map: dict[str, list[str]],
    all_cap_keys: set[str],
) -> tuple[str, list[str]]:
    """Map a raw capability string to (matched_label, [yaml_keys]).

    Steps:
      1. Exact match against cap_map labels.
      2. Substring match (raw is contained in a label or vice-versa).
      3. Token overlap scoring — pick the label with the most shared tokens.
      4. If nothing scores > 0, fall back to "ui automation" keys.

    Returns (resolved_label, yaml_keys).
    """
    raw = raw_capability.strip().lower()
    raw = re.sub(r"[_\-/]", " ", raw)  # normalise separators

    # 1. Exact match
    if raw in cap_map:
        return raw, cap_map[raw]

    # 2. Substring match
    for label, keys in cap_map.items():
        if raw in label or label in raw:
            return label, keys

    # 3. Token overlap
    raw_tokens = set(t for t in raw.split() if len(t) > 2)
    best_label, best_keys, best_score = "", [], 0
    for label, keys in cap_map.items():
        label_tokens = set(t for t in label.split() if len(t) > 2)
        score = len(raw_tokens & label_tokens)
        if score > best_score:
            best_score, best_label, best_keys = score, label, keys

    if best_score > 0:
        logger.debug("Semantic fallback: '%s' → '%s' (score=%d)", raw, best_label, best_score)
        return best_label, best_keys

    # 4. Hard fallback
    logger.debug("No match for capability '%s' — defaulting to ui automation", raw)
    fallback_keys = cap_map.get("ui automation", list(all_cap_keys)[:3])
    return "ui automation (fallback)", fallback_keys


# ── Per-framework support scoring ─────────────────────────────────────────────

# Weight per support level for scoring (0–1)
LEVEL_WEIGHTS: dict[str, float] = {
    "native":      1.0,
    "plugin":      0.75,
    "third-party": 0.5,
    "partial":     0.4,
    "false":       0.0,
}


def score_framework_for_capability(
    fw_capabilities: dict[str, Any],
    fw_architecture: dict[str, Any],
    yaml_keys: list[str],
) -> tuple[str, float]:
    """Return (best_support_level, score 0.0–1.0) for a set of yaml_keys."""
    best_level = "false"
    best_score = 0.0

    for key in yaml_keys:
        # Check capabilities first, then architecture_fit
        value = fw_capabilities.get(key)
        if value is None:
            value = fw_architecture.get(key)
        if value is None:
            continue

        level = classify_support(value)
        score = LEVEL_WEIGHTS[level]
        if score > best_score:
            best_score = score
            best_level = level

    return best_level, best_score


# ── Main analysis function ────────────────────────────────────────────────────

def analyze_coverage(
    test_cases: list[dict],
    kb: "KnowledgeBase",
    frameworks: list[str] | None = None,
) -> dict:
    """Full coverage analysis.

    Returns:
      {
        "matrix":   {tc_id: {"description", "capability", "resolved_label",
                              "yaml_keys", "support": {fw_name: level}}},
        "coverage": {fw_name: {"covered", "partial", "uncovered", "pct", "pct_with_partial"}},
        "cap_map":  capability_map,
        "all_cap_keys": set,
        "fw_names": [str],
      }
    """
    cap_map = build_capability_map(kb)
    all_cap_keys: set[str] = set()
    for fw in kb.list_all():
        all_cap_keys.update(fw.capabilities.keys())
        all_cap_keys.update(fw.architecture_fit.keys())

    all_fw_list = kb.list_all()
    fw_names = frameworks or [fw.framework_name for fw in all_fw_list]

    # ── Build matrix ──────────────────────────────────────────────────
    def _tc_id(tc: dict, idx: int) -> str:
        for k in ("id", "test_id", "testid", "no", "#"):
            if tc.get(k):
                return str(tc[k])
        return f"TC{idx+1:03d}"

    def _tc_cap(tc: dict) -> str:
        for k in ("required_capability", "capability", "type", "category",
                  "test_type", "area", "feature"):
            if tc.get(k):
                return str(tc[k]).strip()
        # Scan all string values for any known label
        for v in tc.values():
            v_low = str(v).lower()
            for label in cap_map:
                if label in v_low:
                    return label
        return "ui automation"

    matrix: dict[str, dict] = {}
    for idx, tc in enumerate(test_cases):
        tc_id = _tc_id(tc, idx)
        raw_cap = _tc_cap(tc)
        resolved_label, yaml_keys = resolve_capability(raw_cap, cap_map, all_cap_keys)

        support: dict[str, str] = {}
        for name in fw_names:
            fw = kb.get(name.lower())
            if not fw:
                continue
            level, _ = score_framework_for_capability(
                fw.capabilities, fw.architecture_fit, yaml_keys
            )
            support[fw.framework_name] = level

        matrix[tc_id] = {
            "description":    tc.get("description", tc.get("desc", tc.get("name", ""))),
            "capability":     raw_cap,
            "resolved_label": resolved_label,
            "yaml_keys":      yaml_keys,
            "support":        support,
            "steps":          tc.get("steps", ""),
            "expected_result":tc.get("expected_result", tc.get("expected", "")),
        }

    # ── Per-framework coverage counts ─────────────────────────────────
    coverage: dict[str, dict] = {}
    total = len(test_cases)
    for name in fw_names:
        fw = kb.get(name.lower())
        if not fw:
            continue
        covered   = [t for t, d in matrix.items() if d["support"].get(fw.framework_name) == "native"]
        partial   = [t for t, d in matrix.items() if d["support"].get(fw.framework_name)
                     in ("plugin", "third-party", "partial")]
        uncovered = [t for t, d in matrix.items() if d["support"].get(fw.framework_name) == "false"]
        pct       = len(covered) / total * 100 if total else 0
        pct_wp    = (len(covered) + len(partial)) / total * 100 if total else 0
        coverage[fw.framework_name] = {
            "covered":          covered,
            "partial":          partial,
            "uncovered":        uncovered,
            "count":            len(covered),
            "partial_count":    len(partial),
            "pct":              pct,
            "pct_with_partial": pct_wp,
        }

    return {
        "matrix":       matrix,
        "coverage":     coverage,
        "cap_map":      cap_map,
        "all_cap_keys": all_cap_keys,
        "fw_names":     fw_names,
    }


def render_coverage_report(analysis: dict, total: int) -> str:
    """Render the analysis dict to a markdown string."""
    matrix   = analysis["matrix"]
    coverage = analysis["coverage"]
    fw_names = analysis["fw_names"]
    lines: list[str] = ["# Test Case Coverage Analysis\n"]

    # ── Summary table ─────────────────────────────────────────────────
    lines += [
        "## Coverage Summary\n",
        "| Framework | Native ✅ | Plugin/3rd-party ⚠️ | Uncovered ❌ | Native% | With-plugin% | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    sorted_cov = sorted(coverage.items(), key=lambda x: -x[1]["pct_with_partial"])
    for fw_name, data in sorted_cov:
        if data["pct"] == 100:
            status = "✅ FULL NATIVE"
        elif data["pct_with_partial"] == 100:
            status = "🟡 FULL (plugins needed)"
        elif data["pct_with_partial"] >= 70:
            status = "⚠️ PARTIAL"
        else:
            status = "❌ LOW"
        lines.append(
            f"| **{fw_name}** | {data['count']} | {data['partial_count']} "
            f"| {len(data['uncovered'])} "
            f"| {data['pct']:.0f}% | {data['pct_with_partial']:.0f}% | {status} |"
        )
    lines.append("")

    # ── Support level legend ──────────────────────────────────────────
    lines += [
        "### 📖 Support Level Guide",
        "| Level | Meaning |",
        "|---|---|",
        "| ✅ Native | Built-in, no extra setup |",
        "| 🔌 Plugin | Requires an official plugin/extension |",
        "| 🔧 Third-party | Needs a separate third-party library |",
        "| ⚡ Partial | Limited/experimental support |",
        "| ❌ False | Not supported |",
        "",
    ]

    # ── Capability resolution notes (show any semantic fallbacks) ─────
    fallbacks = [(tc_id, d) for tc_id, d in matrix.items()
                 if "fallback" in d["resolved_label"]]
    if fallbacks:
        lines.append("### ℹ️ Capability Resolution Notes")
        lines.append("The following test case capabilities were not directly recognised "
                     "and were mapped via semantic matching:\n")
        lines.append("| Test ID | Original Capability | Resolved As | YAML Keys Used |")
        lines.append("|---|---|---|---|")
        for tc_id, d in fallbacks:
            lines.append(
                f"| {tc_id} | `{d['capability']}` | `{d['resolved_label']}` "
                f"| {', '.join(d['yaml_keys'][:4])} |"
            )
        lines.append("")

    # ── Gap analysis (top 5 frameworks) ──────────────────────────────
    lines.append("## Gap Analysis\n")
    for fw_name, data in sorted_cov[:5]:
        if not data["uncovered"] and not data["partial"]:
            lines.append(f"### {fw_name} — ✅ Full native coverage\n")
            continue

        lines.append(f"### {fw_name} — {data['pct']:.0f}% native / "
                     f"{data['pct_with_partial']:.0f}% with plugins\n")

        if data["uncovered"]:
            lines.append("**❌ Uncovered (no support):**")
            uncov_by_cap: dict[str, list[str]] = {}
            for tc_id in data["uncovered"]:
                cap = matrix[tc_id]["resolved_label"]
                uncov_by_cap.setdefault(cap, []).append(tc_id)
            for cap, tc_ids in uncov_by_cap.items():
                yaml_keys = matrix[tc_ids[0]]["yaml_keys"]
                # Find frameworks that DO support this natively
                alts = [
                    f.framework_name for f in kb.list_all()
                    if f.framework_name != fw_name
                    and score_framework_for_capability(
                        f.capabilities, f.architecture_fit, yaml_keys
                    )[0] in ("native", "plugin")
                ][:3]
                alt_text = f" → Consider: **{', '.join(alts)}**" if alts else ""
                lines.append(f"  - **{cap}** ({len(tc_ids)} tests){alt_text}")
            lines.append("")

        if data["partial"]:
            lines.append("**⚠️ Partial/Plugin support (extra setup needed):**")
            part_by_cap: dict[str, list[str]] = {}
            for tc_id in data["partial"]:
                cap = matrix[tc_id]["resolved_label"]
                level = matrix[tc_id]["support"].get(fw_name, "false")
                part_by_cap.setdefault(f"{cap} [{level}]", []).append(tc_id)
            for cap_level, tc_ids in part_by_cap.items():
                lines.append(f"  - **{cap_level}** ({len(tc_ids)} tests)")
            lines.append("")

    # ── Recommended combinations ──────────────────────────────────────
    lines.append("## Recommended Combinations for 100% Coverage\n")
    combos = _find_coverage_combinations(matrix, coverage)
    for i, combo in enumerate(combos[:3], 1):
        names = [c[0] for c in combo]
        cov = _calc_combo_coverage(names, matrix)
        lines.append(f"### Option {i}: {' + '.join(names)}")
        lines.append(f"**Coverage:** {cov['pct']:.0f}% native + "
                     f"{cov['pct_with_partial']:.0f}% with plugins "
                     f"({cov['count']}/{total})\n")
        for fw_name, caps in combo:
            lines.append(f"  - **{fw_name}**: {', '.join(list(set(caps))[:4])}")
        if cov["uncovered"]:
            lines.append(f"\n**Still uncovered:** {', '.join(cov['uncovered'][:3])}")
        lines.append("")

    return "\n".join(lines)


def _find_coverage_combinations(matrix: dict, coverage: dict) -> list:
    """Greedy set-cover considering both native and partial support."""
    combinations = []
    all_tc = set(matrix.keys())
    sorted_fw = sorted(coverage.items(), key=lambda x: -x[1]["pct_with_partial"])

    for start_fw, start_data in sorted_fw[:3]:
        covered_native  = set(start_data["covered"])
        covered_partial = set(start_data["partial"])
        covered_all     = covered_native | covered_partial
        combo = [(start_fw, [matrix[t]["resolved_label"] for t in covered_all])]
        remaining = all_tc - covered_all

        while remaining and len(combo) < 4:
            best_fw, best_covers = None, []
            for fw_name, fw_data in coverage.items():
                if fw_name in [c[0] for c in combo]:
                    continue
                new = (set(fw_data["covered"]) | set(fw_data["partial"])) & remaining
                if len(new) > len(best_covers):
                    best_fw, best_covers = fw_name, list(new)
            if best_fw and best_covers:
                combo.append((best_fw, [matrix[t]["resolved_label"] for t in best_covers]))
                covered_all.update(best_covers)
                remaining -= set(best_covers)
            else:
                break
        combinations.append(combo)

    return sorted(combinations, key=lambda c: (len(c), -sum(len(caps) for _, caps in c)))


def _calc_combo_coverage(fw_names: list[str], matrix: dict) -> dict:
    covered = {t for t, d in matrix.items()
               if d["support"].get(fw_names[0] if fw_names else "") in ("native",)}
    covered_wp = {t for t, d in matrix.items()
                  if any(d["support"].get(fw) in ("native", "plugin", "third-party", "partial")
                         for fw in fw_names)}
    uncovered = [t for t in matrix if t not in covered_wp]
    return {
        "count":            len(covered),
        "pct":              len(covered) / len(matrix) * 100 if matrix else 0,
        "pct_with_partial": len(covered_wp) / len(matrix) * 100 if matrix else 0,
        "uncovered":        uncovered,
    }
