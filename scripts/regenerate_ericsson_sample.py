"""One-off script: regenerate the ericsson-core-assurance-test-robot-tool-generated
sample using the fixed single-file convert_test_code path.

Context: convert_test_project (multi-file) is temporarily disabled (see
mcp_server/server.py) because it had no cross-file call-graph awareness and
produced orphan keyword-only files with no runnable test case. This script
converts each k6 use-case file INDEPENDENTLY via ToolExecutor._convert_test_cases
(the same path convert_test_code/MCP tool uses), now with:
  - the corrected K6->Robot Framework prompt rules (group() -> Test Case
    mapping, SHAPE A/B guidance, fixed escape-sequence corruption), and
  - the new structural conversion_validator check that fails loudly if a
    file ends up keyword-only with no runnable test case.

sanitySmokeChecks.js (the cross-file orchestrator that only imports and calls
functions from the other 11 files) is intentionally NOT converted here —
converting it alone, without the bodies of the functions it calls, is exactly
the multi-file/call-graph problem that's out of scope while
convert_test_project is disabled. It's left as a documented gap.

Run with:
    .venv\\Scripts\\python.exe scripts\\regenerate_ericsson_sample.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from mcp_server import services as svc  # noqa: E402

SRC_REPO = Path(r"C:\Users\xjhapra\idun\salman\ericsson-core-assurance-test")
OUT_REPO = Path(r"C:\Users\xjhapra\idun\salman\ericsson-core-assurance-test-robot-tool-generated")

# (source path relative to SRC_REPO, output .robot filename)
FILES = [
    ("src/js/use_cases/smoke/healthStatus.js", "healthStatus.robot"),
    ("src/js/use_cases/sanity/metrics/metricsEndpoint.js", "metricsEndpoint.robot"),
    ("src/js/use_cases/sanity/metrics/nasMetricsEndpoint.js", "nasMetricsEndpoint.robot"),
    ("src/js/use_cases/smoke/csacProvisioningCheck.js", "test_verify_csac_provisioning_state.robot"),
    ("src/js/use_cases/smoke/csacPmKpiVerification.js", "test_csac_pm_kpi_verification.robot"),
    ("src/js/use_cases/smoke/csacProvisioningAASVerification.js", "test_csac_provisioning_aas_verification.robot"),
    ("src/js/use_cases/smoke/csacNasProvisioningVerification.js", "test_verify_csac_provisions_nas.robot"),
    ("src/js/use_cases/smoke/aaiProvisioningCheck.js", "test_aai_provisioning_check.robot"),
    ("src/js/use_cases/smoke/verifyAAConfigmapData.js", "test_aa_configmap_data_should_be_valid.robot"),
    ("src/js/use_cases/functional/nas/verifyMetadataEndpoint.js", "test_verify_nas_metadata_endpoint_should_succeed.robot"),
    ("src/js/modules/nas/nasKpiVerify.js", "nasKpiVerify.robot"),
]

_FENCE_RE = re.compile(r"```(?:[a-zA-Z]*)\n(.*?)```", re.DOTALL)


def main() -> None:
    if not svc.groq_available():
        print("GROQ_API_KEY is not configured — this script requires the "
              "Groq-backed conversion path. Aborting.")
        sys.exit(1)

    executor = svc.get_tool_executor()
    tests_dir = OUT_REPO / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for rel_src, out_name in FILES:
        src_path = SRC_REPO / rel_src
        source_code = src_path.read_text(encoding="utf-8")
        print(f"Converting {rel_src} -> tests/{out_name} ...")

        markdown = executor._convert_test_cases(
            source_code=source_code,
            from_framework="K6",
            to_framework="Robot Framework",
        )
        validation = getattr(executor, "_last_conversion_validation", None)
        assertion_report = getattr(executor, "_last_assertion_report", None)

        # Strip the "## Converted: ..." header + any validation markdown
        # block _convert_test_cases prepended, and the fenced code block if
        # present, to get just the runnable .robot content.
        #
        # The validation block is a run of lines starting with "###" or "- "
        # (markdown header + bullet list) inserted right after the header and
        # before the actual code. Drop every such line, not just up to the
        # first non-blank line, since the bullet list itself starts with "-".
        body = re.sub(r"^##.*?\n", "", markdown, count=1)
        lines = body.splitlines()
        start = 0
        while start < len(lines):
            stripped = lines[start].strip()
            if stripped.startswith("###") or stripped.startswith("- ") or not stripped:
                start += 1
            else:
                break
        body = "\n".join(lines[start:]).strip()
        blocks = _FENCE_RE.findall(body)
        robot_code = blocks[0].strip() if blocks else body.strip()

        out_path = tests_dir / out_name
        out_path.write_text(robot_code + "\n", encoding="utf-8")

        results.append({
            "source": rel_src,
            "output": f"tests/{out_name}",
            "validation": validation.to_dict() if validation else None,
            "assertion_report": assertion_report,
        })
        status = "OK" if (validation is None or validation.is_valid) else "INVALID"
        print(f"  -> {status}"
              + (f" | assertions {assertion_report['source_count']}->{assertion_report['converted_count']}"
                 if assertion_report else ""))

    (OUT_REPO / "regeneration_report.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(f"\nWrote regeneration_report.json with {len(results)} file result(s).")


if __name__ == "__main__":
    main()
