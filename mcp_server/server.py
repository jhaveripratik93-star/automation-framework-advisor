"""Automation Framework Advisor — MCP server.

Wraps the existing ``src.tools.executor.ToolExecutor`` (plus the migration
planner/coverage analyzer, boilerplate generator, repo comparator, test
code generator, and framework discovery scanner) as MCP tools, so the
whole advisor can be driven from Kiro chat (or any MCP client) without
the Streamlit UI.

Run directly for local testing:
    python mcp_server/server.py

Configured for Kiro via a stdio MCP server entry in mcp.json — see
docs/MCP_SERVER_SETUP.md for the full guide.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp.server.fastmcp import FastMCP

from mcp_server import services as svc

# ── Logging — stdio MCP servers must NEVER write to stdout; file-only ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(_ROOT / "logs" / "mcp_server.log", encoding="utf-8")],
)
logger = logging.getLogger("mcp_server")

mcp = FastMCP(
    name="automation-framework-advisor",
    instructions=(
        "Tools for choosing, migrating to, and validating test automation "
        "frameworks (Playwright, Cypress, Selenium, Robot Framework, K6, "
        "Appium, Karate, REST Assured, and more). Covers: framework "
        "recommendations/scoring, migration planning, coverage gap "
        "analysis, cross-framework test-code conversion, test-code "
        "generation from manual test cases, cross-repo test parity "
        "comparison, CI/CD + prerequisite automation, and live framework "
        "discovery. Knowledge is grounded in a curated YAML knowledge base "
        "and knowledge graph — prefer these tools over general knowledge "
        "when answering automation-framework questions.\n\n"
        "No API key is required to use this server. The code-conversion "
        "and code-generation tools (convert_test_code, "
        "convert_test_project, generate_test_code) work without "
        "GROQ_API_KEY: when it's not configured, they return a dict with "
        "mode='manual' containing the framework-specific conversion/"
        "generation rules and the source material — follow the "
        "`instructions` field in that dict and produce the code yourself "
        "using your own model rather than treating the tool call as "
        "failed."
    ),
)


# ════════════════════════════════════════════════════════════════════
# 1. Knowledge base / knowledge graph lookup tools
# ════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_frameworks() -> str:
    """List every automation framework in the knowledge base with its
    vendor, license, and supported languages. Use this to discover what
    frameworks are available before calling other tools."""
    kb = svc.get_kb()
    lines = ["## Frameworks in Knowledge Base\n"]
    for fw in sorted(kb.list_all(), key=lambda f: f.framework_name):
        lines.append(
            f"- **{fw.framework_name}** ({fw.vendor}) — {fw.license} — "
            f"{', '.join(fw.languages_supported)}"
        )
    return "\n".join(lines)


@mcp.tool()
def list_frameworks_by_category(category: str = "") -> str:
    """List frameworks grouped by functional category (web UI testing, API
    testing, mobile testing, performance testing, infrastructure as code,
    desktop testing, unit testing, BDD, security, visual/accessibility,
    chaos engineering, contract testing). Pass an empty category to see
    all categories; pass a category keyword (e.g. "api", "mobile",
    "performance", "cloud") to filter to one."""
    executor = svc.get_tool_executor()
    return executor.execute("list_frameworks_by_category", {"category": category or None})


@mcp.tool()
def search_knowledge_graph(query: str, entity_types: list[str] | None = None) -> str:
    """Search the knowledge base/graph for frameworks matching a free-text
    query (e.g. "API testing in Python", "mobile gesture support"). Returns
    the top matches with category, languages, version, capabilities, and
    architecture fit."""
    executor = svc.get_tool_executor()
    return executor.execute(
        "search_knowledge_graph", {"query": query, "entity_types": entity_types}
    )


@mcp.tool()
def get_framework_details(framework_name: str) -> str:
    """Get the full profile of one framework: vendor/version, languages,
    license, architecture fit, capabilities, CI/CD integration, cloud grid
    support, performance characteristics, maintainability tooling, and
    known limitations."""
    executor = svc.get_tool_executor()
    return executor.execute("get_framework_details", {"framework_name": framework_name})


@mcp.tool()
def compare_frameworks(frameworks: list[str], focus_criteria: list[str] | None = None) -> str:
    """Produce a side-by-side comparison of 2+ named frameworks (languages,
    license, architecture fit, capabilities, CI/CD, cloud grids,
    performance, maintainability, limitations)."""
    executor = svc.get_tool_executor()
    return executor.execute(
        "run_framework_comparison",
        {"frameworks": frameworks, "focus_criteria": focus_criteria},
    )


# ════════════════════════════════════════════════════════════════════
# 2. Recommendation / scoring tools
# ════════════════════════════════════════════════════════════════════

@mcp.tool()
def recommend_frameworks(
    use_case: str,
    required_capability: str = "",
    weight_preset: str = "balanced",
) -> str:
    """Recommend and rank frameworks for a described use case (e.g. "API
    testing for a Python microservices backend", "cross-browser E2E for a
    React SPA", "mobile app automation"). Returns a weighted scorecard,
    score legend, per-criterion breakdown for the top 3, and the overall
    top pick with pros/cons.

    weight_preset controls scoring priorities — one of: balanced,
    api_heavy, enterprise, startup, devops_first, cloud_migration,
    cloud_native, hybrid_cloud. Use score_frameworks instead if you need
    to restrict the ranking to specific framework names."""
    wp = svc.build_weight_profile(preset=weight_preset)
    executor = svc.get_tool_executor(weight_profile=wp)
    return executor.execute(
        "recommend_frameworks",
        {"use_case": use_case, "required_capability": required_capability or None},
    )


@mcp.tool()
def score_frameworks(
    use_case: str = "",
    frameworks: list[str] | None = None,
    language: str = "python",
    architecture: str = "web_spa",
    weight_preset: str = "balanced",
    custom_weights: dict[str, float] | None = None,
) -> str:
    """Run the full weighted ScoringEngine and return a ranked scorecard
    (score, confidence, top strength, key weakness per framework), plus the
    active criteria weights and the top pick's explanation.

    architecture: one of web_spa, web_mpa, native_mobile, hybrid_mobile,
    desktop, api_only, microservices, cloud_infrastructure.
    weight_preset: balanced, api_heavy, enterprise, startup, devops_first,
    cloud_migration, cloud_native, hybrid_cloud (ignored if custom_weights
    is given). custom_weights lets you set exact per-criterion weights,
    e.g. {"C4_cicd_integration": 0.4, "C1_language_compatibility": 0.3, ...}
    using IDs C1_language_compatibility, C2_api_validation,
    C3_performance_load, C4_cicd_integration, C5_maintainability,
    C6_cloud_readiness, C7_license_cost (weights need not sum to 1 — they
    are normalised automatically). Optionally restrict the ranking to a
    specific list of framework names via `frameworks`."""
    wp = svc.build_weight_profile(preset=weight_preset, custom_weights=custom_weights)
    executor = svc.get_tool_executor(weight_profile=wp)
    return executor.execute(
        "score_frameworks",
        {
            "use_case": use_case,
            "frameworks": frameworks,
            "language": language,
            "architecture": architecture,
        },
    )


# ════════════════════════════════════════════════════════════════════
# 3. Migration planning / coverage analysis
# ════════════════════════════════════════════════════════════════════

@mcp.tool()
def find_migration_paths(from_framework: str, to_frameworks: list[str] | None = None) -> str:
    """Find migration paths from one framework to one or more targets.
    Returns, per target: confidence score, covered capabilities, capability
    gaps (with suggested alternative tools), CI/CD gaps, and the target's
    known limitations."""
    executor = svc.get_tool_executor()
    return executor.execute(
        "find_migration_paths",
        {"from_framework": from_framework, "to_frameworks": to_frameworks},
    )


@mcp.tool()
def generate_migration_roadmap(
    target_framework: str,
    project_name: str = "migration_project",
    legacy_test_count: int = 100,
    team_size: int = 5,
    primary_language: str = "python",
    ci_cd_tool: str = "github_actions",
    timeline_weeks: int = 12,
    must_support: list[str] | None = None,
    containerized: bool = False,
) -> dict[str, Any]:
    """Generate a phased migration roadmap (Foundation → API tests → UI
    tests → Validation & Cutover) with per-phase duration, script counts,
    and task lists, sized to the team and legacy test inventory described.
    must_support accepts hints like "api testing", "mobile" to activate
    the matching phase."""
    profile = svc.build_user_profile(
        project_name=project_name,
        primary_language=primary_language,
        team_size=team_size,
        ci_cd_tool=ci_cd_tool,
        legacy_test_count=legacy_test_count,
        timeline_weeks=timeline_weeks,
        must_support=must_support,
        containerized=containerized,
    )
    from src.migration.planner import MigrationPlanner
    roadmap = MigrationPlanner().generate_roadmap(profile, target_framework)
    return roadmap.to_dict()


@mcp.tool()
def analyze_coverage_gaps(
    target_framework: str,
    project_name: str = "migration_project",
    legacy_test_count: int = 100,
    must_support: list[str] | None = None,
    special_ui: list[str] | None = None,
    primary_language: str = "python",
) -> dict[str, Any]:
    """Analyze functional coverage parity between a legacy test suite and a
    target framework: estimated coverage %, identified gaps (category,
    reason, mitigation, severity), and new testing capabilities gained by
    migrating. must_support/special_ui accept free-text hints such as
    "performance", "mobile", "canvas", "webgl"."""
    kb = svc.get_kb()
    fw_data = kb.get(target_framework.lower())
    if not fw_data:
        return {"error": f"Framework '{target_framework}' not found in knowledge base."}

    profile = svc.build_user_profile(
        project_name=project_name,
        primary_language=primary_language,
        legacy_test_count=legacy_test_count,
        must_support=must_support,
        special_ui=special_ui,
    )
    from src.migration.coverage import CoverageAnalyzer
    report = CoverageAnalyzer().analyze(profile, fw_data)
    return report.to_dict()


@mcp.tool()
def analyze_test_case_coverage(
    test_cases: list[dict[str, Any]],
    frameworks: list[str] | None = None,
) -> dict[str, Any]:
    """Map a list of manual/legacy test cases onto framework capabilities
    and find the best-fit framework(s). Each test case dict should include
    at least 'id' (or 'description') and 'required_capability' (free
    text, e.g. "api automation", "visual regression", "mobile gestures").

    Returns {"fw_names": [...ranked best-first...], "fw_scores":
    {framework: {"supported": [tc_id,...], "unsupported": [...], "pct":
    float}}, "matrix": {tc_id: {"description", "capability", "support":
    {framework: level}}}} — the same data backing the Streamlit
    "Framework Fit" page. Restrict to specific frameworks via the
    `frameworks` argument, or leave empty to evaluate every framework in
    the knowledge base."""
    from src.tools.coverage_engine import analyze_coverage, build_display_coverage

    kb = svc.get_kb()
    analysis = analyze_coverage(test_cases, kb, frameworks)
    return build_display_coverage(analysis)


@mcp.tool()
def analyze_prerequisites(prerequisites: list[dict[str, Any]], target_framework: str = "") -> str:
    """Analyze manual test prerequisites/setup steps (e.g. database seeding,
    environment config, service startup, mocking) and suggest automation
    scripts, CI/CD pipeline stages, and framework-specific setup hooks
    (conftest.py, etc.) for the given target framework. Each prerequisite
    dict should include 'name', 'description', and optionally
    'manual_steps'."""
    executor = svc.get_tool_executor()
    return executor.execute(
        "analyze_prerequisites",
        {"prerequisites": prerequisites, "target_framework": target_framework or None},
    )


@mcp.tool()
def generate_boilerplate_project(
    target_framework: str,
    project_name: str = "automation_project",
    primary_language: str = "python",
    ci_cd_tool: str = "github_actions",
    containerized: bool = False,
) -> dict[str, Any]:
    """Generate a ready-to-run project boilerplate (test runner config,
    conftest/fixtures, sample login + API tests, Dockerfile, CI/CD
    workflow file, README) for the given framework. Returns the file tree
    and full file contents — write these out verbatim to scaffold a new
    project."""
    profile = svc.build_user_profile(
        project_name=project_name,
        primary_language=primary_language,
        ci_cd_tool=ci_cd_tool,
        containerized=containerized,
    )
    from src.generator import BoilerplateGenerator
    template = BoilerplateGenerator().generate(target_framework, profile)
    return {
        "framework": template.framework,
        "project_name": template.project_name,
        "file_tree": template.file_tree(),
        "files": [{"path": f.path, "content": f.content} for f in template.files],
    }


# ════════════════════════════════════════════════════════════════════
# 4. Test-code conversion (cross-framework migration of actual code)
# ════════════════════════════════════════════════════════════════════

@mcp.tool()
def convert_test_code(
    source_code: str,
    from_framework: str,
    to_framework: str,
    language: str = "python",
) -> Any:
    """Convert a single test file's source code from one framework to
    another (e.g. Selenium -> Playwright, K6 -> Robot Framework),
    preserving every assertion. Returns the converted code plus a
    capability-gap table and a CI/CD integration snippet.

    GROQ_API_KEY is OPTIONAL. If it's configured, this tool performs the
    conversion itself via Groq and returns a markdown string. If it's
    NOT configured (the default — no key needed to use this server from
    Kiro), it returns a dict with `mode: "manual"` containing the
    framework conversion rules and the source code — convert the code
    yourself using that brief and present the result to the user.

    For multi-file projects, use convert_test_project instead so
    cross-file imports and shared context are handled correctly."""
    executor = svc.get_tool_executor()
    if not svc.groq_available():
        from mcp_server.manual_mode import build_conversion_brief
        return build_conversion_brief(executor, source_code, from_framework, to_framework)
    return executor.execute(
        "convert_test_cases",
        {
            "source_code": source_code,
            "from_framework": from_framework,
            "to_framework": to_framework,
            "language": language,
        },
    )


@mcp.tool()
def convert_test_project(
    files: list[dict[str, str]],
    from_framework: str,
    to_framework: str,
) -> dict[str, Any]:
    """Convert an entire multi-file test project from one framework to
    another. `files` is a list of {"filename": relative/path.ext,
    "content": "..."} dicts. Preserves cross-file references via shared
    context, generates helper scripts for any capability gaps, a
    conftest.py / fixtures file, and a requirements.txt (for Python
    targets).

    GROQ_API_KEY is OPTIONAL. If it's configured, this tool performs the
    full conversion itself via Groq and returns `converted` (path ->
    code), `helpers`, `conftest`, `requirements`, `gaps`, `summary`
    (markdown), and `assertion_report` (parity check). If it's NOT
    configured (the default), it returns `mode: "manual"` with the
    conversion rules, the deterministically-generated `conftest`/
    `requirements`, and `files_to_convert` — convert each file yourself
    using the rules and present the complete project to the user."""
    executor = svc.get_tool_executor()
    if not svc.groq_available():
        from mcp_server.manual_mode import build_multi_file_conversion_brief
        return build_multi_file_conversion_brief(executor, files, from_framework, to_framework)
    return executor.convert_multi_file(
        files=files, from_framework=from_framework, to_framework=to_framework
    )


# ════════════════════════════════════════════════════════════════════
# 5. Test-code generation from manual test cases
# ════════════════════════════════════════════════════════════════════

_CODEGEN_FRAMEWORKS = {
    "playwright_ts", "playwright_py", "selenium_py", "selenium_java",
    "cypress_js", "rest_assured", "robot_framework",
}


@mcp.tool()
def generate_test_code(
    test_cases: list[dict[str, Any]],
    target_framework: str = "playwright_py",
    selector_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Generate executable automated test code from manual/plain-English
    test cases.

    Each test case dict needs: 'id', 'title', 'steps' (list of
    {"step_number", "action", "test_data", "expected_result"}), and
    optionally 'description', 'preconditions', 'priority', 'category'.

    target_framework: one of playwright_ts, playwright_py, selenium_py,
    selenium_java, cypress_js, rest_assured, robot_framework.

    selector_map optionally maps element phrases (e.g. "username field")
    to concrete CSS/XPath selectors — anything left unmapped is
    auto-generated and flagged for verification in the result.

    GROQ_API_KEY is OPTIONAL. If it's configured, this tool runs the
    hybrid template+LLM pipeline itself (confidence-routed: known action
    patterns use templates, novel ones use Groq and are learned for next
    time) and returns `files` (path/content/file_type per generated
    file), `framework`, `language`, `install_instructions`, `run_command`,
    `selector_map`, `confidence_score`, and `validation`. If it's NOT
    configured (the default), it returns `mode: "manual"` with the
    generation rules and test cases — generate the code yourself using
    the brief and present it to the user."""
    fw_value = target_framework.lower()
    if fw_value not in _CODEGEN_FRAMEWORKS:
        return {
            "error": (
                f"Unknown target_framework '{target_framework}'. "
                f"Valid options: {sorted(_CODEGEN_FRAMEWORKS)}"
            )
        }

    if not svc.groq_available():
        from mcp_server.manual_mode import build_codegen_brief
        return build_codegen_brief(fw_value, test_cases, selector_map)

    from src.codegen import CodeGenRequest, ManualTestCase, TargetFramework, TestStep

    manual_tests = []
    for i, tc in enumerate(test_cases):
        steps = [
            TestStep(
                step_number=s.get("step_number", j + 1),
                action=s["action"],
                test_data=s.get("test_data", ""),
                expected_result=s.get("expected_result", ""),
            )
            for j, s in enumerate(tc.get("steps", []))
        ]
        manual_tests.append(ManualTestCase(
            id=tc.get("id", f"TC{i + 1:03d}"),
            title=tc.get("title", "Untitled"),
            description=tc.get("description", ""),
            preconditions=tc.get("preconditions", []),
            steps=steps,
            expected_results=tc.get("expected_results", []),
            priority=tc.get("priority", "medium"),
            category=tc.get("category", ""),
            tags=tc.get("tags", []),
        ))

    request = CodeGenRequest(
        test_cases=manual_tests,
        target_framework=TargetFramework(fw_value),
        selector_map=selector_map or {},
    )
    orchestrator = svc.get_codegen_orchestrator()
    suite = orchestrator.generate(request)

    return {
        "framework": suite.framework,
        "language": suite.language,
        "install_instructions": suite.install_instructions,
        "run_command": suite.run_command,
        "selector_map": suite.selector_map,
        "confidence_score": suite.confidence_score,
        "validation": suite.validation.model_dump(),
        "stats": suite.stats.model_dump(),
        "files": [
            {"path": f.path, "content": f.content, "file_type": f.file_type.value,
             "source": f.source.value, "confidence": f.confidence}
            for f in suite.files
        ],
    }


# ════════════════════════════════════════════════════════════════════
# 6. Cross-repo test parity comparator
# ════════════════════════════════════════════════════════════════════

@mcp.tool()
def compare_repos(
    files_a: list[dict[str, str]],
    files_b: list[dict[str, str]],
    framework_a: str,
    framework_b: str,
) -> dict[str, Any]:
    """Compare test cases across two repos (potentially different
    frameworks) and produce an assertion-count parity report. Extracts
    test cases from both file sets, matches them by name (exact + fuzzy
    token overlap), and reports assertion-count matches/mismatches plus
    tests that exist in only one repo.

    files_a / files_b: list of {"filename": "path.ext", "content": "..."}
    dicts (equivalent to unzipping each repo's test directory).
    framework_a / framework_b: one of Robot Framework, Playwright,
    Selenium, K6, Cypress, Jest, Mocha (auto-detected per file from
    extension/content, but used as a hint for assertion-counting rules).

    Returns summary counts, matched pairs (with assertion deltas and
    loop-assertion counts), and unmatched-case lists for both repos — the
    same data backing the Streamlit "Cross-Repo Test Parity" page."""
    from src.tools.repo_comparator import compare_repos as _compare_repos

    report = _compare_repos(files_a, files_b, framework_a, framework_b)

    def _pair(m):
        return {
            "name": m.name, "display_a": m.display_a, "display_b": m.display_b,
            "file_a": m.file_a, "file_b": m.file_b,
            "assertions_a": m.assertions_a, "assertions_b": m.assertions_b,
            "loop_assertions_a": m.loop_assertions_a, "loop_assertions_b": m.loop_assertions_b,
            "score": m.score, "status": m.status,
        }

    def _unmatched(u):
        return {
            "display_name": u.display_name, "file": u.file,
            "assertions": u.assertions, "repo": u.repo,
            "loop_assertions": u.loop_assertions,
        }

    return {
        "framework_a": report["framework_a"],
        "framework_b": report["framework_b"],
        "summary": report["summary"],
        "matched": [_pair(m) for m in report["matched"]],
        "only_in_a": [_unmatched(u) for u in report["only_in_a"]],
        "only_in_b": [_unmatched(u) for u in report["only_in_b"]],
    }


@mcp.tool()
def compare_repos_csv(
    files_a: list[dict[str, str]],
    files_b: list[dict[str, str]],
    framework_a: str,
    framework_b: str,
) -> str:
    """Same analysis as compare_repos, but returns the parity report as a
    downloadable CSV string (Test Case, File A, File B, Assertions A,
    Assertions B, Delta, Status) — convenient for writing straight to a
    .csv file."""
    from src.tools.repo_comparator import compare_repos as _compare_repos, to_csv

    report = _compare_repos(files_a, files_b, framework_a, framework_b)
    return to_csv(report)


# ════════════════════════════════════════════════════════════════════
# 7. Live framework discovery
# ════════════════════════════════════════════════════════════════════

@mcp.tool()
def discover_new_frameworks(force: bool = False) -> dict[str, Any]:
    """Scan GitHub (and a curated fallback list) for automation frameworks
    not yet present in the knowledge base. By default this is throttled to
    once per 24h; pass force=True to scan immediately. Returns newly
    discovered frameworks (name, description, stars, language, repo URL,
    license) grouped by category, plus a flat list."""
    from src.knowledge_base.schema import FRAMEWORK_CATEGORIES

    scanner = svc.get_scanner()
    new_fws = scanner.scan(force=force)

    flat_list = [
        {
            "name": fw.name, "description": fw.description, "stars": fw.stars,
            "language": fw.language, "repo_url": fw.repo_url, "license": fw.license,
            "categories": fw.categories,
        }
        for fw in new_fws
    ]
    categorized = scanner.get_categorized_results(new_fws)
    categories_response = {
        cat_id: {
            "label": FRAMEWORK_CATEGORIES[cat_id]["label"],
            "frameworks": [
                {"name": fw.name, "description": fw.description, "stars": fw.stars,
                 "language": fw.language, "repo_url": fw.repo_url, "license": fw.license}
                for fw in fws
            ],
        }
        for cat_id, fws in categorized.items()
    }
    return {"new_frameworks": flat_list, "count": len(new_fws), "categorized": categories_response}


@mcp.tool()
def add_framework_to_kb(name: str) -> dict[str, Any]:
    """Research a named framework (via web search, and an LLM if
    GROQ_API_KEY is configured) and add it to the local YAML knowledge
    base as a new profile, making it available to all other tools
    afterwards. GROQ_API_KEY is OPTIONAL — without it, the profile is
    built from a template with sensible defaults instead of LLM-written
    descriptions; expect to review it manually (the returned
    `completeness` field flags any gaps). Returns the written profile
    path, the generated profile data, and the completeness check."""
    from src.knowledge_base.schema import FrameworkData

    scanner = svc.get_scanner()
    profile = scanner.research_framework(name)
    if not profile:
        return {"error": f"Could not research framework: {name}"}

    is_complete, issues = FrameworkData.validate_for_addition(profile)
    path = scanner.add_framework(profile)
    if not path:
        return {"error": f"Could not write profile for: {name}"}

    # Invalidate the cached KB/graph singletons so later tool calls see the
    # newly added framework immediately.
    svc._kb = None
    svc._graph = None
    svc._graphrag = None

    return {
        "status": "added", "framework": name, "path": str(path),
        "profile": profile,
        "completeness": {"is_complete": is_complete, "issues": issues},
    }


# ════════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("Starting Automation Framework Advisor MCP server (stdio)")
    mcp.run(transport="stdio")
