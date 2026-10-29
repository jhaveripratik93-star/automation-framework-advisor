"""Test Automation Pilot — MCP server.

Wraps the feature-owned executors (``src.advisor_tools``, ``src.coverage``,
``src.prerequisites``, composed by ``src.agents.tool_router.ToolRouter``)
plus the migration planner/coverage analyzer, boilerplate generator, repo
comparator, test code generator, and framework discovery scanner as MCP
tools, so the whole advisor can be driven from Kiro chat (or any MCP
client) without the Streamlit UI.

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

from mcp.server.fastmcp import Context, FastMCP

from mcp_server import services as svc
from mcp_server.tool_logging import log_tool_call

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
        "analysis, cross-framework test-CODE conversion (test code only — "
        "never CI/CD pipelines, GitHub Actions, or deployment config), "
        "test-code generation from manual test cases, cross-repo test "
        "parity comparison, prerequisite automation, and live framework "
        "discovery. Knowledge is grounded in a curated YAML knowledge base "
        "and knowledge graph — prefer these tools over general knowledge "
        "when answering automation-framework questions.\n\n"
        "convert_test_code prefers true MCP sampling (your client's own "
        "model) and falls back to GROQ_API_KEY automatically if your "
        "client doesn't support sampling — no manual steps needed either "
        "way."
    ),
)


# ════════════════════════════════════════════════════════════════════
# 1. Knowledge base / knowledge graph lookup tools
# ════════════════════════════════════════════════════════════════════

@mcp.tool()
@log_tool_call
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
@log_tool_call
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
@log_tool_call
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
@log_tool_call
def get_framework_details(framework_name: str) -> str:
    """Get the full profile of ONE named framework: vendor/version,
    languages, license, architecture fit, capabilities, CI/CD integration,
    cloud grid support, performance characteristics, maintainability
    tooling, and known limitations.

    Use this when the user asks about a single framework by name, e.g.
    "tell me about Playwright", "what license does Karate use", "is
    Cypress good for mobile". For 2+ frameworks side by side, use
    compare_frameworks instead."""
    executor = svc.get_tool_executor()
    return executor.execute("get_framework_details", {"framework_name": framework_name})


@mcp.tool()
@log_tool_call
def compare_frameworks(frameworks: list[str], focus_criteria: list[str] | None = None) -> str:
    """Produce a side-by-side comparison of 2+ named frameworks (languages,
    license, architecture fit, capabilities, CI/CD, cloud grids,
    performance, maintainability, limitations).

    Use this for "X vs Y" style questions, e.g. "Playwright vs Cypress",
    "compare Selenium, Playwright and WebdriverIO". For a ranked
    recommendation given a use case rather than a fixed list of names, use
    recommend_frameworks or score_frameworks instead; for just one
    framework, use get_framework_details."""
    executor = svc.get_tool_executor()
    return executor.execute(
        "run_framework_comparison",
        {"frameworks": frameworks, "focus_criteria": focus_criteria},
    )


# ════════════════════════════════════════════════════════════════════
# 2. Recommendation / scoring tools
# ════════════════════════════════════════════════════════════════════

@mcp.tool()
@log_tool_call
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
@log_tool_call
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
@log_tool_call
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
@log_tool_call
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
@log_tool_call
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
@log_tool_call
def analyze_test_case_coverage(
    test_cases: list[dict[str, Any]],
    frameworks: list[str] | None = None,
) -> dict[str, Any]:
    """Map a list of manual/legacy test cases onto framework capabilities
    and find the best-fit framework(s). Each test case dict should include
    at least 'id' (or 'description') and 'required_capability' (free
    text, e.g. "api automation", "visual regression", "mobile gestures").

    Use this when the user asks which framework(s) best fit a LIST of
    their own manual/legacy test cases ("which framework covers these test
    cases", "framework fit for this test suite"). It scores frameworks
    against capability requirements, not assertions — for comparing
    assertion counts between an already-converted source/target test file
    pair, use compare_repos instead; for converting the code itself, use
    convert_test_code.

    Returns {"fw_names": [...ranked best-first...], "fw_scores":
    {framework: {"supported": [tc_id,...], "unsupported": [...], "pct":
    float}}, "matrix": {tc_id: {"description", "capability", "support":
    {framework: level}}}} — the same data backing the Streamlit
    "Coverage Analyzer" page. Restrict to specific frameworks via the
    `frameworks` argument, or leave empty to evaluate every framework in
    the knowledge base."""
    from src.coverage.engine import analyze_coverage, build_display_coverage

    kb = svc.get_kb()
    analysis = analyze_coverage(test_cases, kb, frameworks)
    return build_display_coverage(analysis)


@mcp.tool()
@log_tool_call
def analyze_prerequisites(prerequisites: list[dict[str, Any]], target_framework: str = "") -> str:
    """Analyze manual test prerequisites/setup steps (e.g. database seeding,
    environment config, service startup, mocking) and suggest automation
    scripts, CI/CD pipeline stages, and framework-specific setup hooks
    (conftest.py, etc.) for the given target framework.

    Use this when a user describes manual setup/precondition steps for
    their tests and asks how to automate them, e.g. "how do I automate
    seeding the database before each test" or "what setup hooks does
    Playwright need for this precondition". Each prerequisite dict should
    include 'name', 'description', and optionally 'manual_steps'."""
    executor = svc.get_tool_executor()
    return executor.execute(
        "analyze_prerequisites",
        {"prerequisites": prerequisites, "target_framework": target_framework or None},
    )


@mcp.tool()
@log_tool_call
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
@log_tool_call
async def convert_test_code(
    source_code: str,
    from_framework: str,
    to_framework: str,
    ctx: Context,
    team_id: str = "default",
) -> dict[str, Any]:
    """Convert a single test file's source code from one framework to
    another (e.g. Selenium -> Playwright, K6 -> Robot Framework),
    preserving every assertion. This is the tool to use when asked to
    "convert this test to X" or "port this Robot/Selenium file to
    Playwright" — it does the conversion AND validates it in one call (see
    `parity_report` below), so prefer it over manually comparing files.
    This tool performs ONLY code conversion — it never generates or
    suggests CI/CD pipeline config, GitHub Actions workflows, or
    deployment YAML.

    Related tools: use check_conversion_consistency instead if you need to
    verify the conversion is reproducible across repeated runs, not just
    valid once. Use compare_repos/compare_repos_csv instead if the source
    and already-converted files live as separate, already-written repos/
    directories you want to diff for assertion parity — convert_test_code
    already runs that same comparison internally when you're converting
    fresh, so don't call compare_repos again right after a convert_test_code
    call on the same file.

    LLM backend: this tool prefers true MCP sampling — it asks YOUR
    connected client's model (e.g. Kiro's own model) to do the generation
    via sampling/createMessage, so no server-side API key is spent. If
    your client hasn't declared the `sampling` capability, it falls back
    to GROQ_API_KEY (config/.env) automatically. The conversion work
    itself (prompt assembly, real-compiler validation, structural
    "no orphan test" checks, and an automatic repair retry loop) always
    runs in this server regardless of which LLM backend produced the
    text.

    After conversion, this tool automatically runs the SAME cross-repo
    parity engine behind compare_repos/compare_repos_csv (source vs.
    converted code) to confirm the two frameworks have equivalent
    validation — any test case whose assertion count doesn't match
    between source and converted code is surfaced in `parity_report`.

    Returns a dict: `code` (final converted code), `target_language`,
    `is_valid` (compiled AND structurally sound), `llm_mode`
    ("sampling" or "groq"), `attempts` (full retry history with each
    attempt's compile/validation result), `assertion_report` (source vs.
    converted assertion-count delta), `parity_report` (per-test-case
    mismatch detail from the auto-chained repo comparator, with
    `is_equivalent` as the headline signal), and `report` (short
    markdown summary of all of the above).

    `team_id` selects a named team profile (created in the Streamlit
    app's Settings -> Team Profiles dialog) so this team's own
    conversion rules are used instead of the tool's shared defaults.
    Leave as "default" unless the user has told you which team profile
    to use — if this workspace's mcp.json sets DEFAULT_TEAM_PROFILE,
    leaving this as "default" resolves to that configured profile
    automatically, not the literal built-in default."""
    import anyio

    from mcp_server.sampling import make_llm_call
    from src.conversion.service import ConversionRequest, convert

    llm_call, llm_mode = make_llm_call(ctx)
    resolved_team_id = svc.resolve_team_id(team_id)

    def _run_conversion():
        return convert(ConversionRequest(
            source_code=source_code,
            from_framework=from_framework,
            to_framework=to_framework,
            llm_call=llm_call,
            team_id=resolved_team_id,
        ))

    result = await anyio.to_thread.run_sync(_run_conversion)

    payload = result.to_dict()
    payload["llm_mode"] = llm_mode
    payload["report"] = result.render_report()
    return payload


@mcp.tool()
@log_tool_call
async def check_conversion_consistency(
    source_code: str,
    from_framework: str,
    to_framework: str,
    ctx: Context,
    runs: int = 5,
    team_id: str = "default",
) -> dict[str, Any]:
    """Run the SAME code conversion `runs` times in a row and report how
    reproducible and accurate the result is — this is the measurement
    tool for proving convert_test_code gives consistent, trustworthy
    output rather than a one-shot lucky result.

    Use this when asked "is this conversion reliable/reproducible" or
    "run the conversion a few times and check it's stable" — NOT for a
    single one-off conversion (use convert_test_code for that) and NOT
    for comparing two already-written files (use compare_repos for that).

    Each run goes through the full convert_test_code pipeline
    independently (its own compile check, structural check, cross-repo
    parity check, and repair-loop retries), using the same LLM backend
    selection as convert_test_code (true MCP sampling if your client
    supports it, else GROQ_API_KEY).

    Returns a dict: `records` (per-run detail: valid?, assertion count,
    attempts needed, parity-equivalent?), `valid_count`/`total_runs`,
    `consistency_score` (fraction of runs that were fully valid),
    `is_consistent` (True only if EVERY run was valid AND every run
    produced the same assertion count — the strict reproducibility bar),
    `assertion_counts` (list, one per run, to eyeball variance directly),
    and `report` (markdown summary). Keep `runs` modest (5-10) — each run
    can itself make multiple LLM calls via its repair loop.

    `team_id` selects a named team profile, same as convert_test_code.
    Leave as "default" unless told otherwise — if this workspace's
    mcp.json sets DEFAULT_TEAM_PROFILE, that profile is used automatically."""
    import anyio

    from mcp_server.sampling import make_llm_call
    from src.conversion.consistency import check_consistency

    llm_call, llm_mode = make_llm_call(ctx)
    resolved_team_id = svc.resolve_team_id(team_id)

    def _run_check():
        return check_consistency(
            source_code=source_code,
            from_framework=from_framework,
            to_framework=to_framework,
            llm_call=llm_call,
            runs=runs,
            team_id=resolved_team_id,
        )

    report = await anyio.to_thread.run_sync(_run_check)

    payload = report.to_dict()
    payload["llm_mode"] = llm_mode
    payload["report"] = report.render_report()
    return payload


# Multi-file/project conversion is not yet re-implemented under the new
# architecture. convert_test_code is the single supported conversion
# tool for a single file; convert files one at a time.


# ════════════════════════════════════════════════════════════════════
# 5. Test-code generation from manual test cases
# ════════════════════════════════════════════════════════════════════

_CODEGEN_FRAMEWORKS = {
    "playwright_ts", "playwright_py", "selenium_py", "selenium_java",
    "cypress_js", "rest_assured", "robot_framework",
}


@mcp.tool()
@log_tool_call
def generate_test_code(
    test_cases: list[dict[str, Any]],
    target_framework: str = "playwright_py",
    selector_map: dict[str, str] | None = None,
    team_id: str = "default",
) -> dict[str, Any]:
    """Generate executable automated test code from manual/plain-English
    test cases.

    Each test case dict needs: 'id', 'title', 'steps' (list of
    {"step_number", "action", "test_data", "expected_result"}), and
    optionally 'description', 'preconditions', 'priority', 'category'.

    target_framework: one of playwright_ts, playwright_py, selenium_py,
    selenium_java, cypress_js, rest_assured, robot_framework.

    team_id selects a named team profile (created in the Streamlit app's
    Settings -> Team Profiles dialog) so generated code follows that
    team's own coding standards instead of the tool's built-in defaults.
    Leave as "default" unless the user has told you which team profile
    to use — if this workspace's mcp.json sets DEFAULT_TEAM_PROFILE,
    leaving this as "default" resolves to that configured profile
    automatically.

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

    resolved_team_id = svc.resolve_team_id(team_id)

    if not svc.groq_available():
        from mcp_server.manual_mode import build_codegen_brief
        return build_codegen_brief(fw_value, test_cases, selector_map, team_id=resolved_team_id)

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
        team_id=resolved_team_id,
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
@log_tool_call
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

    Use this when asked to "validate/verify that these converted tests
    still match the original assertions" for files that ALREADY exist on
    disk as two separate sets (e.g. a legacy Robot Framework directory vs.
    a hand-written or previously-converted Playwright directory). If the
    conversion itself hasn't happened yet, use convert_test_code instead —
    it performs this same parity check automatically as its last step, so
    there's no need to call compare_repos again right after it for the
    same file.

    Note: assertion counting here only looks within a matched test case's
    OWN body text, not assertions defined in separately-called helper
    keywords/functions in a different part of the source — if both
    frameworks factor their checks into shared helpers, the count may
    read misleadingly low (even 0 vs 0) despite real, deeper assertion
    parity. For a helper-aware/expanded count, prefer convert_test_code's
    `assertion_report`, which resolves keyword/function calls recursively.

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
@log_tool_call
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
    from src.tools.repo_comparator import compare_repos as _compare_repos
    from src.tools.repo_comparator import to_csv

    report = _compare_repos(files_a, files_b, framework_a, framework_b)
    return to_csv(report)


# ════════════════════════════════════════════════════════════════════
# 7. Live framework discovery
# ════════════════════════════════════════════════════════════════════

@mcp.tool()
@log_tool_call
def discover_new_frameworks(force: bool = False) -> dict[str, Any]:
    """Scan GitHub (and a curated fallback list) for automation frameworks
    not yet present in the knowledge base. By default this is throttled to
    once per 24h; pass force=True to scan immediately. Returns newly
    discovered frameworks (name, description, stars, language, repo URL,
    license) grouped by category, plus a flat list.

    Use this when asked things like "are there any new/trending test
    frameworks we're missing" or "what's out there besides what's in the
    knowledge base already". To actually add one of the results to the
    knowledge base afterwards, use add_framework_to_kb."""
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
@log_tool_call
def add_framework_to_kb(name: str) -> dict[str, Any]:
    """Research a named framework (via web search, and an LLM if
    GROQ_API_KEY is configured) and add it to the local YAML knowledge
    base as a new profile, making it available to all other tools
    afterwards.

    Use this when asked to add, research, or onboard a specific framework
    that isn't already in the knowledge base (e.g. "add Playwright-java to
    the knowledge base", "research Codeception for us") — including a
    framework surfaced by discover_new_frameworks. GROQ_API_KEY is
    OPTIONAL — without it, the profile is built from a template with
    sensible defaults instead of LLM-written descriptions; expect to
    review it manually (the returned `completeness` field flags any
    gaps). Returns the written profile path, the generated profile data,
    and the completeness check."""
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
    logger.info("Starting Test Automation Pilot MCP server (stdio)")
    mcp.run(transport="stdio")
