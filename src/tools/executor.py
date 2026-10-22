"""Tool executor — runs tool calls requested by the agent pipeline.

Tools available:
  - search_knowledge_graph
  - get_framework_details
  - run_framework_comparison
  - recommend_frameworks
  - find_migration_paths
  - analyze_test_case_coverage
  - analyze_uploaded_content
  - analyze_prerequisites
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

# External helpers — single source of truth for formatting, patterns and CI/CD
from src.scoring.weights import WeightProfile
from src.tools.cicd_generators import generate_cicd_pipeline, generate_framework_hooks
from src.tools.formatters import (
    format_architecture_fit,
    format_cicd,
    format_cloud_grids,
    format_maintainability,
    format_performance,
    format_version_info,
    resolve_framework,
)
from pathlib import Path
from src.tools.prerequisites import PREREQ_PATTERNS, generate_script, match_prereq_pattern

if TYPE_CHECKING:
    from src.graph import KnowledgeGraph
    from src.graph.graphrag_engine import GraphRAGEngine
    from src.knowledge_base.loader import KnowledgeBase

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes tool calls by name, routing to the appropriate handler."""

    def __init__(
        self,
        knowledge_graph: KnowledgeGraph | None = None,
        graphrag_engine: GraphRAGEngine | None = None,
        knowledge_base: KnowledgeBase | None = None,
        uploaded_docs_context: str = "",
        case_study_context: str = "",
        weight_profile: WeightProfile | None = None,
    ) -> None:
        # Auto-load knowledge base if not provided
        if knowledge_base is None:
            from src.knowledge_base import KnowledgeBase as _KB
            knowledge_base = _KB(data_dir="data/frameworks")
            knowledge_base.load()
        self.kb = knowledge_base

        # Auto-load knowledge graph if not provided
        if knowledge_graph is None:
            from src.graph import load_or_seed_graph
            knowledge_graph = load_or_seed_graph(self.kb)
        self.kg = knowledge_graph

        self.graphrag = graphrag_engine
        self.uploaded_docs = uploaded_docs_context
        self.case_study = case_study_context
        self.weight_profile = weight_profile or WeightProfile.default()
        self._llm: Any | None = None  # injected by orchestrator when available

        self.tools: dict[str, Callable] = {
            "search_knowledge_graph": self._search_knowledge_graph,
            "get_framework_details": self._get_framework_details,
            "run_framework_comparison": self._run_framework_comparison,
            "recommend_frameworks": self._recommend_frameworks,
            "find_migration_paths": self._find_migration_paths,
            "analyze_test_case_coverage": self._analyze_test_case_coverage,
            "analyze_uploaded_content": self._analyze_uploaded_content,
            "analyze_prerequisites": self._analyze_prerequisites,
            "score_frameworks": self._score_frameworks,
            "convert_test_cases": self._convert_test_cases,
            "list_frameworks_by_category": self._list_frameworks_by_category,
        }

    # ── Dispatch ──────────────────────────────────────────────────────

    # Tools whose output depends on the active scoring weights. Their cache
    # entries must be keyed on the weight signature, otherwise changing the
    # weights and re-asking the same query returns a stale ranking.
    _WEIGHT_SENSITIVE_TOOLS = frozenset({"recommend_frameworks", "score_frameworks"})

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        from src.agents.cache import tool_cache, make_tool_cache_key, weight_signature

        if tool_name not in self.tools:
            logger.error("ToolExecutor: unknown tool '%s'", tool_name)
            return f"Error: unknown tool '{tool_name}'"

        # Check cache first — fold the weight signature in for weight-sensitive
        # tools so a weight change invalidates their cached ranking.
        weight_sig = (
            weight_signature(self.weight_profile)
            if tool_name in self._WEIGHT_SENSITIVE_TOOLS else ""
        )
        cache_key = make_tool_cache_key(tool_name, arguments, weight_sig)
        cached = tool_cache.get(cache_key)
        if cached is not None:
            logger.info("ToolExecutor: CACHE HIT '%s' → %d chars", tool_name, len(cached))
            return cached

        try:
            result = self.tools[tool_name](**arguments)
            logger.info("ToolExecutor: '%s' → %d chars", tool_name, len(str(result)))
            # Store in cache
            tool_cache.set(cache_key, result)
            return result
        except Exception as exc:
            logger.error("ToolExecutor: '%s' failed — %s: %s", tool_name, type(exc).__name__, exc)
            return f"Error executing {tool_name}: {exc}"

    # ── Formatting helpers ────────────────────────────────────────────
    # Thin wrappers that delegate to src.tools.formatters — single source
    # of truth. Kept as instance methods so existing call sites (_search_*,
    # _get_*, etc.) don't need to change.

    def _format_architecture_fit(self, architecture_fit: dict[str, Any]) -> str:
        return format_architecture_fit(architecture_fit)

    def _format_cicd(self, cicd_integration: dict[str, Any]) -> str:
        return format_cicd(cicd_integration)

    def _format_cloud_grids(self, cloud_grids: dict[str, Any]) -> str:
        return format_cloud_grids(cloud_grids)

    def _format_performance(self, performance: dict[str, Any]) -> str:
        return format_performance(performance)

    def _format_maintainability(self, maintainability: dict[str, Any]) -> str:
        return format_maintainability(maintainability)

    def _format_version_info(self, fw: Any) -> str:
        return format_version_info(fw)

    def _resolve_framework(self, name: str) -> Any | None:
        """Resolve a framework name with fuzzy matching via formatters.resolve_framework."""
        return resolve_framework(self.kb, name)
    
    def _find_case_study_relevance(self, subject_terms: list[str], max_excerpts: int = 3) -> str:
        """Scan the uploaded case study for paragraphs mentioning any subject term.

        Returns "" when no case study is loaded or nothing relevant is found,
        so callers can safely do: `if note: lines.append(note)`.
        """
        if not self.case_study or not subject_terms:
            return ""

        terms = [t.lower() for t in subject_terms if t]
        paragraphs = [p.strip() for p in self.case_study.split("\n\n") if p.strip()]
        if len(paragraphs) <= 1:
            paragraphs = [line.strip() for line in self.case_study.split("\n") if line.strip()]

        matches = []
        for para in paragraphs:
            if any(term in para.lower() for term in terms):
                matches.append(para)
            if len(matches) >= max_excerpts:
                break

        if not matches:
            return ""

        lines = ["\n**📄 From Your Case Study:**"]
        for m in matches:
            snippet = m if len(m) <= 300 else m[:300].rstrip() + "…"
            lines.append(f"> {snippet}")
        return "\n".join(lines)

    # ── Tool implementations ──────────────────────────────────────────
    
    async def _search_graphrag(self, query: str, depth: int = 2) -> str:
        """Query the GraphRAG engine for semantic search results."""
        if not self.graphrag:
            return "GraphRAG engine not available."
        try:
            result = await self.graphrag.arespond(query, depth=depth)
            return result.response
        except Exception as exc:
            logger.error("GraphRAG search failed: %s", exc)
            return f"Error searching GraphRAG: {exc}"

    def _search_knowledge_graph(self, query: str, entity_types: list[str] | None = None, **_kwargs) -> str:
        """Search KB for frameworks matching the query — returns structured data.

        Workflow:
          1. Tokenise query; score each framework by token overlap.
          2. Take top 5 matches.
          3. Render: category, languages, capabilities, architecture fit,
             version info (NEW), case-study relevance (NEW).
        """
        query_lower = query.lower()
        all_frameworks = self.kb.list_all()

        query_tokens = {t for t in query_lower.split() if len(t) > 3}
        scored: list[tuple[int, Any]] = []
        for fw in all_frameworks:
            fw_text = " ".join([
                fw.framework_name.lower(),
                fw.category.lower() if fw.category else "",
                " ".join(fw.languages_supported).lower(),
                " ".join(k for k, v in fw.capabilities.items() if v and v is not False),
            ])
            score = sum(1 for t in query_tokens if t in fw_text)
            if score > 0:
                scored.append((score, fw))

        scored.sort(key=lambda x: -x[0])
        top = [fw for _, fw in scored[:10]]

        if not top:
            return f"No frameworks found matching: {query}"

        lines = [f"## Frameworks matching '{query}'\n"]
        for fw in top:
            caps = [
                k.replace("_", " ").title()
                for k, v in fw.capabilities.items()
                if v is True or (isinstance(v, str) and v not in ("", "false"))
            ][:5]
            lines += [
                f"### {fw.framework_name}",
                f"- **Category:** {fw.category}",
                f"- **Languages:** {', '.join(fw.languages_supported)}",
                f"- **Version:** {self._format_version_info(fw)}",
                f"- **Key capabilities:** {', '.join(caps)}",
                f"- **Architecture Fit:** {self._format_architecture_fit(fw.architecture_fit)}",
            ]
            note = self._find_case_study_relevance([fw.framework_name])
            if note:
                lines.append(note)
            lines.append("")

        return "\n".join(lines)
    
    def _get_framework_details(self, framework_name: str) -> str:
        """Assemble a full profile of one framework.

        Workflow:
          1. Resolve — bail out with unchanged not-found message if missing.
          2. Header + version.
          3. Core facts: languages, license, architecture fit.
          4. Capabilities.
          5. CI/CD, cloud grids, performance, maintainability (NEW).
          6. Limitations.
          7. Case-study relevance (NEW).
        """
        # Step 1 — resolve or fail fast
        fw = self.kb.get(framework_name.lower())
        if not fw:
            return f"Framework '{framework_name}' not found in knowledge base."

        # Step 2 — header + version
        lines = [
            f"## {fw.framework_name}",
            f"**Vendor / Version:** {self._format_version_info(fw)}",
        ]

        # Step 3 — core facts
        lines += [
            f"**Languages:** {', '.join(fw.languages_supported)}",
            f"**License:** {fw.license}",
            f"**Architecture Fit:** {self._format_architecture_fit(fw.architecture_fit)}",
            "",
            "**Key Capabilities:**",
        ]

        # Step 4 — capabilities
        for k, v in fw.capabilities.items():
            if v is True or (isinstance(v, str) and v not in ("", "false")):
                lines.append(f"  - {k.replace('_', ' ').title()}: {v}")

        # Step 5 — enrichment sections (each self-guards on empty data)
        cicd = self._format_cicd(fw.cicd_integration)
        if cicd:
            lines.append(f"\n**CI/CD Integration:** {cicd}")

        cloud_grids = self._format_cloud_grids(fw.cloud_grids)
        if cloud_grids:
            lines.append(f"\n**Cloud Grids:** {cloud_grids}")

        performance = self._format_performance(fw.performance)
        if performance:
            lines.append(f"\n**Performance:** {performance}")

        maintainability = self._format_maintainability(fw.maintainability)
        if maintainability:
            lines.append("\n**Maintainability:**")
            lines.append(maintainability)

        # Step 6 — limitations
        if fw.limitations:
            lines.append("\n**Limitations:**")
            for lim in fw.limitations[:5]:
                lines.append(f"  - {lim}")

        # Step 7 — case-study relevance (orthogonal signal, appended last)
        note = self._find_case_study_relevance([fw.framework_name])
        if note:
            lines.append(note)

        return "\n".join(lines)

    def _run_framework_comparison(
        self, frameworks: list[str], focus_criteria: list[str] | None = None
    ) -> str:
        """Side-by-side comparison across N frameworks.

        Workflow — runs steps 2-6 ONCE PER FRAMEWORK inside the loop:
          1. Resolve name; emit not-found bullet and continue if missing
             (never aborts the whole comparison).
          2. Header.
          3. Core facts: languages, license, capabilities.
          4. CI/CD, cloud grids, performance, maintainability (NEW).
          5. Limitations (from fw.limitations, not from capabilities).
          6. Case-study relevance for this specific framework (NEW).
        """
        lines = [f"## Framework Comparison: {' vs '.join(frameworks)}\n"]

        for name in frameworks:
            # Step 1 — resolve per-item; continue, don't abort
            fw = self.kb.get(name.lower())
            if not fw:
                lines.append(f"- **{name}**: not found in knowledge base")
                continue

            # Step 2 — header
            lines.append(f"### {fw.framework_name}")

            # Step 3 — core facts
            caps = [
                k.replace("_", " ").title()
                for k, v in fw.capabilities.items()
                if v is True or (isinstance(v, str) and v not in ("", "false"))
            ]
            lines += [
                f"- **Languages:** {', '.join(fw.languages_supported)}",
                f"- **License:** {fw.license}",
                f"- **Architecture Fit:** {self._format_architecture_fit(fw.architecture_fit)}",
                f"- **Capabilities:** {', '.join(caps[:5])}",
            ]

            # Step 4 — enrichment
            cicd = self._format_cicd(fw.cicd_integration)
            if cicd:
                lines.append(f"- **CI/CD Integration:** {cicd}")

            cloud_grids = self._format_cloud_grids(fw.cloud_grids)
            if cloud_grids:
                lines.append(f"- **Cloud Grids:** {cloud_grids}")

            performance = self._format_performance(fw.performance)
            if performance:
                lines.append(f"- **Performance:** {performance}")

            maintainability = self._format_maintainability(fw.maintainability)
            if maintainability:
                lines.append("- **Maintainability:**")
                lines.append(maintainability)

            # Step 5 — limitations (real field, not a re-slice of caps)
            if fw.limitations:
                lines.append(f"- **Limitations:** {', '.join(fw.limitations[:3])}")

            # Step 6 — case-study relevance for THIS framework
            note = self._find_case_study_relevance([fw.framework_name])
            if note:
                lines.append(note)

            lines.append("")  # blank separator between frameworks

        return "\n".join(lines)

    def _recommend_frameworks(
        self, use_case: str, required_capability: str | None = None
    ) -> str:
        """Rank frameworks using ScoringEngine + active WeightProfile.

        Renders:
          - Weight priority header  (e.g. CI/CD(30%) > Maintainability(20%) > ...)
          - Per-criterion score table with weighted total
          - Best-fit badge on top scorer
          - Case-study relevance
        """
        from src.scoring.engine import ScoringEngine
        from src.models import UserProfile, ArchitectureType, ExperienceLevel

        use_case_lower = use_case.lower()

        # Infer architecture from use-case keywords
        arch_map = {
            "api": ArchitectureType.API_ONLY,
            "microservice": ArchitectureType.MICROSERVICES,
            "mobile": ArchitectureType.NATIVE_MOBILE,
        }
        arch = next(
            (v for k, v in arch_map.items() if k in use_case_lower),
            ArchitectureType.WEB_SPA,
        )

        # For performance/load queries, pre-filter to only performance frameworks
        # so the scoring engine doesn't rank web UI frameworks above K6/Locust
        perf_keywords = {"performance", "load", "stress", "benchmark", "throughput"}
        is_perf_query = any(kw in use_case_lower for kw in perf_keywords)
        if is_perf_query:
            from src.knowledge_base.schema import classify_framework_data
            perf_frameworks = [
                fw.framework_name for fw in self.kb.list_all()
                if "performance_testing" in classify_framework_data(fw)
            ]
            if perf_frameworks:
                return self._list_frameworks_by_category(category="performance_testing")

        profile = UserProfile(
            project_name="advisor_query",
            architecture_types=[arch],
            primary_language="python",
            team_size=5,
            automation_experience=ExperienceLevel.INTERMEDIATE,
            ci_cd_tool="github_actions",
        )

        engine = ScoringEngine(knowledge_base=self.kb, weight_profile=self.weight_profile)
        matrix = engine.evaluate(profile)
        rankings = matrix.rankings[:8]

        # Criterion display config: id → short label
        _criteria = [
            ("C1_language_compatibility", "Language"),
            ("C2_api_validation",         "API"),
            ("C3_performance_load",        "Perf"),
            ("C4_cicd_integration",        "CI/CD"),
            ("C5_maintainability",         "Maint."),
            ("C6_cloud_readiness",         "Cloud"),
            ("C7_license_cost",            "Cost"),
        ]
        active = [(cid, lbl) for cid, lbl in _criteria if self.weight_profile.get(cid) > 0]
        # Fallback: a degenerate all-zero profile would leave `active` empty,
        # producing a malformed table with no criterion columns. Show all
        # criteria in that case so the ranking is still readable.
        if not active:
            active = _criteria

        # Order criteria by weight (descending) so the highest-priority
        # criterion appears first everywhere it is shown — the column order,
        # the "You prioritized" header and the per-criterion cells all follow
        # the user's weighting rather than the fixed C1→C7 definition order.
        active = sorted(active, key=lambda pair: -self.weight_profile.get(pair[0]))

        # ── Weight priority header ────────────────────────────────────
        sorted_weights = [(lbl, self.weight_profile.get(cid)) for cid, lbl in active]
        priority_str = " > ".join(f"{lbl}({w:.0%})" for lbl, w in sorted_weights)
        lines = [
            f"## Recommended Frameworks for: {use_case}\n",
            f"**You prioritized:** {priority_str}\n",
        ]

        # ── Per-criterion score table ─────────────────────────────────
        col_headers = " | ".join(lbl for _, lbl in active)
        col_sep     = " | ".join("---" for _ in active)
        lines += [
            f"| Rank | Framework | {col_headers} | Weighted Score |",
            f"|---|---|{col_sep}|---|",
        ]

        for i, r in enumerate(rankings):
            scores_dict = r.criteria_scores.model_dump()
            cell_values = " | ".join(str(scores_dict.get(cid, 0)) for cid, _ in active)
            badge = " ✅ Best fit" if i == 0 else ""
            lines.append(
                f"| {r.rank} | **{r.framework}** | {cell_values} "
                f"| **{r.overall_score}**{badge} |"
            )

        # ── Score legend ─────────────────────────────────────────
        lines += [
            "\n### 📖 Score Guide",
            "| Range | Rating | What it means |",
            "|---|---|---|",
            "| 85–100 | 🟢 Excellent | Best-in-class for this criterion — no gaps |",
            "| 65–84  | 🟡 Good     | Solid support — minor workarounds may be needed |",
            "| 40–64  | 🟠 Fair     | Partial support — plan for extra setup or plugins |",
            "| 0–39   | 🔴 Poor     | Significant gap — consider a complementary tool |",
        ]

        # ── Per-criterion breakdown for top 3 ────────────────────────────
        lines.append("\n### 🔍 What Each Score Means For You")
        for r in rankings[:3]:
            lines.append(f"\n**{r.framework}** ({r.overall_score}/100)")
            lines.append(r.explanation)

        # ── Top pick explanation ──────────────────────────────────────
        if rankings:
            top = rankings[0]
            lines += [
                f"\n### 🏆 Top Pick: {top.framework} ({top.overall_score}/100)",
                top.explanation,
            ]
            if top.pros:
                lines.append("**Strengths:** " + " · ".join(top.pros[:3]))
            if top.cons:
                lines.append("**Watch out for:** " + " · ".join(top.cons[:2]))

        # ── Case-study relevance ──────────────────────────────────────
        note = self._find_case_study_relevance(use_case_lower.split())
        if note:
            lines.append(note)

        return "\n".join(lines)

    def _find_migration_paths(
        self, from_framework: str, to_frameworks: list[str] | None = None
    ) -> str:
        """Find migration paths with shared capabilities, gaps, and effort estimate.

        Workflow:
          1. Resolve source in knowledge graph; fail fast if not found.
          2. Look up source FrameworkData for capability comparison.
          3. Get MIGRATES_TO relationships; filter to to_frameworks if given.
          4. Deduplicate targets (keep highest confidence per target).
          5. Per target: shared caps, gaps + alternatives, CI/CD overlap,
             target limitations.
          6. Case-study relevance for the from/to framework pair (NEW).
        """
        from src.graph import RelationshipType

        if not self.kg:
            return "Knowledge graph not available. Please ensure the graph is loaded."

        # Step 1 — source in graph
        sources = self.kg.find_entities_by_name(from_framework)
        if not sources:
            return f"Framework '{from_framework}' not found in knowledge graph."

        # Step 2 — source in KB
        source_fw = self.kb.get(from_framework.lower())
        if not source_fw:
            return f"Framework '{from_framework}' not found in knowledge base."

        source_caps = {k for k, v in source_fw.capabilities.items() if v and v is not False}
        source_cicd = {k for k, v in source_fw.cicd_integration.items() if v and v is not False}

        # Step 3 — relationships
        rels = self.kg.get_relationships(sources[0].id, hops=2)
        paths = [r for r in rels if r.relationship_type == RelationshipType.MIGRATES_TO]

        if to_frameworks:
            to_lower = [t.lower() for t in to_frameworks]
            paths = [
                r for r in paths
                if any(
                    t in (self.kg.get_entity(r.target_id).name.lower()
                          if self.kg.get_entity(r.target_id) else "")
                    for t in to_lower
                )
            ]

        if not paths:
            return f"No migration paths found from '{from_framework}'."

        # Step 4 — deduplicate by target
        seen_targets: dict[str, tuple] = {}
        for rel in paths:
            target = self.kg.get_entity(rel.target_id)
            if not target:
                continue
            if target.id not in seen_targets or rel.confidence > seen_targets[target.id][0]:
                seen_targets[target.id] = (rel.confidence, rel, target)

        # Step 5 — build output
        lines = [f"## Migration Paths from {from_framework}\n"]
        all_fw_map = {fw.framework_name.lower(): fw for fw in self.kb.list_all()}

        for conf, rel, target in sorted(seen_targets.values(), key=lambda x: -x[0]):
            target_fw = self.kb.get(target.name.lower())
            if not target_fw:
                continue

            target_caps = {k for k, v in target_fw.capabilities.items() if v and v is not False}
            target_cicd = {k for k, v in target_fw.cicd_integration.items() if v and v is not False}

            capability_gaps = source_caps - target_caps
            cicd_gaps = source_cicd - target_cicd
            shared_caps = source_caps & target_caps
            shared_cicd = source_cicd & target_cicd

            lines.append(f"### {from_framework} → {target.name} ({int(conf * 100)}% confidence)\n")

            # Covered
            lines.append("**✅ Covered Capabilities:**")
            if shared_caps:
                lines.append(f"  {', '.join(sorted(c.replace('_', ' ').title() for c in list(shared_caps)[:6]))}")
            if shared_cicd:
                lines.append(f"  CI/CD: {', '.join(sorted(c.replace('_', ' ').title() for c in list(shared_cicd)[:4]))}")
            lines.append("")

            # Gaps
            if capability_gaps or cicd_gaps:
                lines.append("**⚠️ Gaps (not covered by target):**")

                for gap in sorted(capability_gaps):
                    gap_label = gap.replace("_", " ").title()
                    alternatives = [
                        fw.framework_name
                        for fw_name, fw in all_fw_map.items()
                        if fw_name not in (target.name.lower(), from_framework.lower())
                        and fw.capabilities.get(gap)
                        and fw.capabilities.get(gap) is not False
                        and set(fw.languages_supported) & set(source_fw.languages_supported)
                    ][:3]
                    alt_text = f" → Consider: **{', '.join(alternatives)}**" if alternatives else ""
                    lines.append(f"  - {gap_label}{alt_text}")

                if cicd_gaps:
                    lines.append(
                        f"  - CI/CD gaps: "
                        f"{', '.join(c.replace('_', ' ').title() for c in sorted(cicd_gaps)[:3])}"
                    )
                lines.append("")
            else:
                lines.append("**✅ No capability gaps — full coverage!**\n")

            # Target limitations
            if target_fw.limitations:
                lines.append("**⚠️ Target Framework Limitations:**")
                for lim in target_fw.limitations[:3]:
                    lines.append(f"  - {lim}")
                lines.append("")

            # Case-study relevance for this from/to pair
            note = self._find_case_study_relevance([from_framework, target.name])
            if note:
                lines.append(note)

            lines.append("---\n")

        return "\n".join(lines)

    def _analyze_test_case_coverage(
        self,
        test_cases: list[dict],
        frameworks: list[str] | None = None,
        prerequisites: list[dict] | None = None,
    ) -> str:
        """Delegates to coverage_engine — fully generic, YAML-derived."""
        from src.tools.coverage_engine import analyze_coverage, render_coverage_report
        analysis = analyze_coverage(test_cases, self.kb, frameworks)
        report = render_coverage_report(analysis, len(test_cases))
        note = self._find_case_study_relevance(
            list({d["capability"] for d in analysis["matrix"].values()})
        )
        return report + ("\n" + note if note else "")
    def _list_frameworks_by_category(self, category: str | None = None) -> str:
        """List frameworks grouped by their functional category using architecture_fit classification."""
        from collections import defaultdict
        from src.knowledge_base.schema import FRAMEWORK_CATEGORIES, classify_framework_data

        # Build a keyword → cat_id map for fuzzy matching user input
        _KEYWORD_MAP: dict[str, str] = {
            "api": "api_testing",
            "rest": "api_testing",
            "http": "api_testing",
            "microservice": "api_testing",
            "web": "web_ui_testing",
            "ui": "web_ui_testing",
            "browser": "web_ui_testing",
            "e2e": "web_ui_testing",
            "mobile": "mobile_testing",
            "ios": "mobile_testing",
            "android": "mobile_testing",
            "performance": "performance_testing",
            "load": "performance_testing",
            "stress": "performance_testing",
            "cloud": "infrastructure_as_code",
            "infrastructure": "infrastructure_as_code",
            "iac": "infrastructure_as_code",
            "devops": "infrastructure_as_code",
            "desktop": "desktop_testing",
        }

        # Classify every framework using architecture_fit (not the raw category string)
        by_cat: dict[str, list] = defaultdict(list)
        for fw in self.kb.list_all():
            for cat_id in classify_framework_data(fw):
                by_cat[cat_id].append(fw)

        # Resolve requested category
        target_cat: str | None = None
        if category:
            cat_lower = category.lower().strip()
            # Direct match first
            if cat_lower in FRAMEWORK_CATEGORIES:
                target_cat = cat_lower
            else:
                # Keyword fuzzy match
                for kw, cat_id in _KEYWORD_MAP.items():
                    if kw in cat_lower:
                        target_cat = cat_id
                        break

        if target_cat:
            cat_def = FRAMEWORK_CATEGORIES[target_cat]
            fws = sorted(by_cat.get(target_cat, []), key=lambda f: f.framework_name)
            lines = [f"## {cat_def['icon']} {cat_def['label']} Frameworks ({len(fws)} found)\n",
                     f"*{cat_def['description']}*\n"]
            if not fws:
                lines.append("No frameworks found for this category.")
            else:
                for fw in fws:
                    arch_keys = [
                        k.replace("_", " ").title()
                        for k, v in fw.architecture_fit.items()
                        if v is True or (isinstance(v, str) and v.lower() not in ("false", ""))
                    ]
                    langs = ", ".join(fw.languages_supported[:3])
                    lines.append(f"### {fw.framework_name}")
                    lines.append(f"- **Vendor:** {fw.vendor}")
                    lines.append(f"- **Languages:** {langs}")
                    lines.append(f"- **Architecture fit:** {', '.join(arch_keys[:5])}")
                    if fw.limitations:
                        lines.append(f"- **Key limitation:** {fw.limitations[0]}")
                    lines.append("")
        else:
            # No category filter — show all grouped
            lines = ["## All Frameworks by Category\n"]
            for cat_id, cat_def in FRAMEWORK_CATEGORIES.items():
                fws = sorted(by_cat.get(cat_id, []), key=lambda f: f.framework_name)
                if not fws:
                    continue
                lines.append(f"### {cat_def['icon']} {cat_def['label']} ({len(fws)})")
                for fw in fws:
                    lines.append(f"- **{fw.framework_name}** ({fw.vendor})")
                lines.append("")

        return "\n".join(lines)

    def _analyze_uploaded_content(self, search_term: str, document_type: str = "all") -> str:
        results = []
        term = search_term.lower()
        if document_type in ("test_files", "all") and self.uploaded_docs:
            matches = [l for l in self.uploaded_docs.split("\n") if term in l.lower()][:10]
            if matches:
                results.append(f"**Test Files ({len(matches)} matches):**")
                results.extend(matches)
        if document_type in ("case_study", "all") and self.case_study:
            matches = [l for l in self.case_study.split("\n") if term in l.lower()][:10]
            if matches:
                results.append(f"\n**Case Study ({len(matches)} matches):**")
                results.extend(matches)
        return "\n".join(results) if results else f"No matches found for '{search_term}'."

    def _analyze_prerequisites(
        self, prerequisites: list[dict], target_framework: str | None = None
    ) -> str:
        """Analyze pre-requisites; suggest automation scripts and CI/CD integration.

        Workflow:
          1. Resolve optional target framework.
          2. Match each prerequisite to an automation pattern (via prerequisites module).
          3. Emit script skeleton + CI/CD stage for each.
          4. Generate full CI/CD pipeline YAML.
          5. Generate framework-specific hooks (conftest.py, playwright config, etc.).
        """
        lines = ["# Pre-requisite Automation Analysis\n"]

        fw = self._resolve_framework(target_framework) if target_framework else None
        fw_cicd: dict = {}
        if fw:
            fw_cicd = {k: v for k, v in fw.cicd_integration.items() if v and v is not False}
            lines.append(f"**Target Framework:** {fw.framework_name}\n")

        automation_plan: list[dict] = []

        for prereq in prerequisites:
            name = prereq.get("name", "Unknown")
            desc = prereq.get("description", "")
            manual = prereq.get("manual_steps", "")

            matched_pattern = match_prereq_pattern(prereq)

            lines.append(f"## {name}\n")
            lines.append(f"**Description:** {desc or 'N/A'}")
            if manual:
                lines.append(f"**Current Manual Steps:** {manual}\n")

            if matched_pattern:
                pname, p = matched_pattern
                lines += [
                    f"**✅ Can be Automated:** Yes ({p['automation'].upper()} script)\n",
                    f"**Script Type:** {p['script_type']}",
                    f"**Recommended Tools:** {', '.join(p['tools'])}",
                    f"**CI/CD Stage:** {p['cicd_step']}",
                    f"**Example Command:** `{p['example']}`\n",
                ]
                script = generate_script(pname, prereq, p)
                lines += [
                    "**Suggested Script:**",
                    f"```{p['automation']}",
                    script,
                    "```\n",
                ]
                automation_plan.append({
                    "name": name,
                    "pattern": pname,
                    "stage": p["cicd_step"],
                    "command": p["example"],
                    "automation": p["automation"],
                })
            else:
                safe_name = name.lower().replace(" ", "_")
                lines += [
                    "**⚠️ Automation:** Needs custom script\n",
                    "```python",
                    f"# scripts/{safe_name}_setup.py",
                    f"# TODO: Implement automation for: {desc}",
                    "import subprocess",
                    "def setup():",
                    f"    # Manual steps: {manual[:100]}...",
                    "    pass",
                    "if __name__ == '__main__': setup()",
                    "```\n",
                ]
                automation_plan.append({
                    "name": name,
                    "pattern": "custom",
                    "stage": "pre-test",
                    "command": f"python scripts/{safe_name}_setup.py",
                    "automation": "python",
                })

            lines.append("---\n")

        # CI/CD pipeline YAML (delegates to cicd_generators module)
        lines.append("# CI/CD Pipeline Integration\n")
        lines.append(generate_cicd_pipeline(automation_plan, fw, fw_cicd))

        # Framework-specific hooks
        if fw:
            lines.append("\n# Framework Integration\n")
            lines.append(generate_framework_hooks(automation_plan, fw))

        return "\n".join(lines)

    # ── Shared gap/helper utilities ───────────────────────────────────

    _HELPER_MAP: dict[str, str] = {
        "network_interception": "Use `httpx` mock transport or `responses` library",
        "visual_regression": "Use `Pillow` + `pixelmatch-python` for screenshot diff",
        "performance_testing": "Use `locust` or `k6` via subprocess",
        "load_testing": "Use `locust` programmatic API",
        "accessibility_testing": "Use `axe-selenium-python` or `playwright-axe`",
        "component_testing": "Use `pytest` + `playwright` component fixtures",
        "test_recorder": "Manual — no programmatic equivalent",
        "shadow_dom": "Use `playwright` evaluate() to pierce shadow roots",
        "iframe_cross_origin": "Use `playwright` frame_locator() for cross-origin iframes",
        "gesture_support": "Use `appium-python-client` for mobile gestures",
        "device_farm_support": "Use `BrowserStack` or `Sauce Labs` REST API",
    }

    # Language → ecosystem details (extension, test runner, package manifest)
    _LANG_ECOSYSTEM: dict[str, dict[str, str]] = {
        "python":     {"ext": ".py",    "runner": "pytest",                                        "manifest": "requirements.txt", "fence": "python"},
        "robot":      {"ext": ".robot", "runner": "robot (e.g. robot tests/)",                    "manifest": "requirements.txt", "fence": "robot"},
        "javascript": {"ext": ".js",    "runner": "the framework CLI (e.g. k6 run, npx cypress run)", "manifest": "package.json",    "fence": "javascript"},
        "typescript": {"ext": ".ts",    "runner": "the framework CLI",                             "manifest": "package.json",    "fence": "typescript"},
        "java":       {"ext": ".java",  "runner": "Maven/Gradle (JUnit/TestNG)",                  "manifest": "pom.xml",         "fence": "java"},
        "c#":         {"ext": ".cs",    "runner": "dotnet test",                                  "manifest": "packages.config", "fence": "csharp"},
        "ruby":       {"ext": ".rb",    "runner": "rspec",                                        "manifest": "Gemfile",         "fence": "ruby"},
        "go":         {"ext": ".go",    "runner": "go test",                                      "manifest": "go.mod",          "fence": "go"},
    }

    def _resolve_target_language(self, to_fw: Any, to_name: str) -> tuple[str, dict[str, str]]:
        """Determine the target language + ecosystem from the framework's YAML.

        Returns (language_lower, ecosystem_dict). Falls back to Python if unknown.
        """
        # Robot Framework is its own language/format — never resolve to "python"
        if "robot" in to_name.lower():
            return "robot", self._LANG_ECOSYSTEM["robot"]

        default = self._LANG_ECOSYSTEM["python"]
        if not to_fw or not getattr(to_fw, "languages_supported", None):
            return "python", default

        langs = [str(l).lower() for l in to_fw.languages_supported]

        # Pick the first language that has a known ecosystem mapping
        for lang in langs:
            for key in self._LANG_ECOSYSTEM:
                if key in lang:  # handles "TypeScript (via bundler)" etc.
                    return key, self._LANG_ECOSYSTEM[key]

        return "python", default

    def _build_gap_notes(self, from_fw: Any, to_fw: Any, to_name: str,
                         target_lang: str = "python") -> tuple[str, list[str]]:
        """Return (gap_notes_for_prompt, sorted_gap_list)."""
        if not (from_fw and to_fw):
            return "", []
        from_caps = {k for k, v in from_fw.capabilities.items() if v and v is not False}
        to_caps = {k for k, v in to_fw.capabilities.items() if v and v is not False}
        gaps = sorted(from_caps - to_caps)
        if not gaps:
            return "", []
        note = (
            f"\n\nNote: {to_name} does NOT natively support: "
            + ", ".join(g.replace("_", " ") for g in gaps)
            + f". For those capabilities, generate a standalone helper "
            f"in {target_lang} and show how to call it from the test setup."
        )
        return note, gaps

    def _build_api_notes(self, to_fw: Any, to_name: str) -> str:
        """Build notes about the target framework's current API and known
        deprecations, derived from its YAML versions section, so the LLM
        generates up-to-date syntax (not deprecated imports/APIs)."""
        if not to_fw:
            return ""

        notes: list[str] = []
        latest = getattr(to_fw, "latest_version", "")
        if latest:
            notes.append(f"Target the latest stable {to_name} version ({latest}) API.")

        # Collect deprecated/removed APIs and their replacements from versions
        versions = getattr(to_fw, "versions", []) or []
        for ver in versions:
            for dep in ver.get("capabilities_deprecated", []) or []:
                name = dep.get("name", "")
                repl = dep.get("replacement", "")
                desc = dep.get("description", "")
                if repl:
                    notes.append(f"Do NOT use '{name}' ({desc}) — use '{repl}' instead.")
            for bc in ver.get("breaking_changes", []) or []:
                workaround = bc.get("workaround", "")
                affected = bc.get("affected_apis", []) or []
                if workaround and affected:
                    notes.append(
                        f"Avoid {', '.join(affected)} — {workaround}."
                    )

        if not notes:
            return ""
        # Cap to keep the prompt focused
        return "\n\nIMPORTANT — use current, non-deprecated APIs:\n" + "\n".join(
            f"- {n}" for n in notes[:8]
        )

    # Per-target-framework syntax rules injected into the system prompt
    _FW_SYNTAX_RULES: dict[str, str] = {
        "robot framework": """
    OUTPUT RULES (STRICT):
    1. Output ONLY valid Robot Framework code.
    2. Do NOT wrap the code in markdown code fences.
    3. Do NOT include introductory text, explanations, or conclusions.

    SYNTAX & FORMATTING:
    - Maintain EXACTLY these four standard sections in order (omit empty sections):
        *** Settings ***
        *** Variables ***
        *** Keywords ***
        *** Test Cases ***
    - CRITICAL SPACING RULE: Separate all keywords, arguments, and variable assignments with AT LEAST TWO SPACES (or a TAB).
    - Set global teardown under *** Settings ***:
        Test Teardown    Close Browser
    - Test Case titles must start at the beginning of the line (0 indentation).
    - Steps inside test cases and keywords MUST be indented with 4 spaces.

    LIBRARY & KEYWORD USAGE:
    - Default Library: Library    SeleniumLibrary
    - Allowed Core Keywords: Open Browser, Input Text, Input Password, Click Button, Click Element, Element Should Be Visible, Element Text Should Be, Location Should Contain, Close Browser.

    KEYWORD CREATION RULES:
    - Write steps inline inside *** Test Cases *** by default.
    - DO NOT create single-action wrapper keywords.
    - Define custom keywords under *** Keywords *** ONLY if they encapsulate 2+ sequential steps AND are reused across multiple test cases.

    SYNTAX & PARSING RULES:
    - CRITICAL SPACING: Separate all keywords, arguments, and assignments with AT LEAST TWO SPACES (or a TAB). Single spaces are invalid cell separators.
    - NO PYTHON DOCSTRINGS: Do NOT use triple quotes (\"\"\") for keyword documentation or comments. ALWAYS use [Documentation] or '#' comments.
    - DEPRECATION RULE: DO NOT use [Return] to return values from keywords. ALWAYS use the modern block keyword 'RETURN'.
    - ARGUMENT ORDER: Do NOT put list varargs (@{LIST}) before standard scalar arguments (\({ARG}) in [Arguments]. Always place scalar arguments first or pass lists as standard scalars (\){LIST}).
    - NESTED DICTIONARIES: Do NOT pass multiple key arguments to 'Get From Dictionary'. Access nested keys directly using RF variable syntax (e.g., '${json}[data][result]').
    - INLINE IF STATEMENTS: Always use 'Set Variable' inside inline IF/ELSE blocks when assigning values.

    CORRECT EXAMPLE:
    *** Settings ***
    Library    SeleniumLibrary
    Test Teardown    Close Browser

    *** Variables ***
    ${URL}    https://example.com
    ${BROWSER}    chrome

    *** Test Cases ***
    Login With Valid Credentials
        Open Browser    \({URL}\){BROWSER}
        Input Text    css=[data-testid='username']    admin
        Input Text    css=[data-testid='password']    secret
        Click Button    css=[data-testid='login-btn']
        Location Should Contain    /dashboard
        Element Text Should Be    css=h1    Dashboard
    """,

            "playwright": """
    OUTPUT RULES (STRICT):
    1. Output ONLY valid executable Python code using pytest-playwright idioms.
    2. Do NOT wrap the code in markdown code fences.
    3. Do NOT output any explanations or conversational text.

    TECHNICAL REQUIREMENTS:
    - Standard Imports:
        from playwright.sync_api import Page, expect
    - Structure tests as standalone pytest functions: `def test_(page: Page):`
    - Preferred Locator Strategy: Use user-facing locators (`page.get_by_role`, `page.get_by_label`, `page.get_by_test_id`) over raw CSS/XPath when possible.
    - Assertions: ALWAYS use auto-retrying web-first assertions via `expect()` (e.g., `expect(page.get_by_role("heading")).to_be_visible()`).
    - Do NOT explicitly instantiate browsers or drivers inside tests; rely on the injected `page` fixture.
    - Do NOT output Robot Framework, Selenium, or JavaScript syntax.
    """,

            "selenium": """
    OUTPUT RULES (STRICT):
    1. Output ONLY valid Python code using pytest and Selenium Webdriver.
    2. Do NOT wrap the output in markdown code fences.
    3. Do NOT include explanatory text outside the code block.

    TECHNICAL REQUIREMENTS:
    - Imports:
        import pytest
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
    - Test Structure: Plain pytest functions (e.g., `def test_(driver):`).
    - Element Selection: ALWAYS use `By` enum explicit locators (e.g., `By.CSS_SELECTOR`, `By.ID`, `By.XPATH`).
    - Explicit Waits: Favor `WebDriverWait(driver, timeout).until(...)` over raw `time.sleep()`.
    - Do NOT output Playwright, K6, or Robot Framework syntax.
    """,

            "k6": """
    OUTPUT RULES (STRICT):
    1. Output ONLY valid K6 JavaScript ES6 code.
    2. Do NOT wrap the output in markdown code fences.
    3. Do NOT include conversational text or markdown headings.

    TECHNICAL REQUIREMENTS:
    - Standard API Imports:
        import http from 'k6/http';
        import { check, sleep } from 'k6';
    - Mandatory Config Export:
        export const options = { vus: 1, duration: '10s' };
    - Default Function Entrypoint:
        export default function (data) { ... }
    - Assertion Pattern: Use `check(res, { 'status is 200': (r) => r.status === 200 });`

    K6 SETUP() CONTEXT ISOLATION (CRITICAL — violating this produces broken code):
    - setup() runs in a SEPARATE JavaScript context from default(). Global/module-level
      variables mutated inside setup() are NOT visible inside default(). This is a k6
      architectural fact, not optional.
    - ALL data produced in setup() (tokens, session IDs, config) MUST be returned as the
      setup() return value and received by default(data).
    - CORRECT pattern:
        export function setup() {
          const token = login();          // obtain token
          return { token };               // return it
        }
        export default function (data) {
          const token = data.token;       // receive from setup
          const res = http.get(url, { headers: { Authorization: `Bearer ${token}` } });
        }
    - WRONG pattern (NEVER do this):
        let token = '';
        export function setup() { token = login(); }   // mutation lost!
        export default function () { /* token is '' here */ }

    AUTH TOKEN PASSING RULES:
    - If the source framework uses authentication, generate a shared auth helper module
      (e.g. auth.js) that exports login functions returning the token string.
    - Helper functions that build authenticated headers MUST accept the token as a
      parameter (not read from a global), because the caller receives the token from
      setup(data), not from a module global.
    - CORRECT: export function authHeaders(token) { return { Authorization: `Bearer ${token}` }; }
    - WRONG:   export function authHeaders() { return { Authorization: `Bearer ${CONFIG.token}` }; }
      (CONFIG.token was set in setup — default() can't see it.)
    - When generating a config module (config.js), use __ENV for initial/default values
      only. Runtime-obtained values (tokens) flow through setup() → default(data).

    HTTP HEADERS vs QUERY PARAMETERS:
    - In k6, http.get(url, { headers: {...}, params: {...} })
    - `headers` = HTTP headers (Authorization, Content-Type, Accept).
    - `params` = URL query parameters (appended as ?key=value).
    - Do NOT put query parameters (rows, start, query, token) in the headers object.
    - Do NOT put non-standard keys (bufferedWriterSize, rows) in headers.

    FOR BROWSER (UI) PERFORMANCE TESTS:
    - Import `browser` module: `import { browser } from 'k6/browser';`
    - Scenario Config: Set `options.scenarios` with `executor: 'shared-iterations'` and `options: { browser: { type: 'chromium' } }`.
    - Execution: Use `const context = await browser.newContext(); const page = await context.newPage();`.
    - STRICT NEGATIVE RULE: Do NOT import or use `chromium.launch()` or standard Node/Playwright syntax.

    CORRECT K6 API EXAMPLE (with setup/auth):
    import http from 'k6/http';
    import { check, sleep } from 'k6';

    export const options = {
      vus: 1,
      duration: '10s',
    };

    function login() {
      const res = http.post('https://example.com/auth/token',
        'grant_type=password&client_id=my-client&username=user&password=pass',
        { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
      return res.json('access_token') || '';
    }

    export function setup() {
      return { token: login() };
    }

    export default function (data) {
      const headers = { Authorization: `Bearer ${data.token}`, Accept: 'application/json' };
      const res = http.get('https://example.com/api/users', { headers });
      check(res, {
        'status is 200': (r) => r.status === 200,
      });
      sleep(1);
    }
    """,

            "k6_to_robot": """
    OUTPUT RULES (STRICT):
    1. Output ONLY valid Robot Framework syntax (.robot file).
    2. Do NOT wrap the output in markdown code fences.
    3. Do NOT include explanations, conversational text, or header commentary.

    SHARED HELPER & COMMON FUNCTION CONVERSION RULES:
    - Identify imported functions from `common/` or shared JS modules (e.g., `import { login } from '../common/auth.js'`).
    - Convert every shared helper function into a reusable entry under the `*** Keywords ***` section.
    - Function parameters must be converted to Robot Framework arguments using standard syntax:
        [Arguments]    \({username}\){password}
    - Convert K6 API calls (`http.get`, `http.post`) to `RequestsLibrary` keywords (`GET On Session`, `POST On Session`).
    - Convert K6 browser calls (`page.goto`, `page.fill`) to `Browser` or `SeleniumLibrary` keywords (`New Page`, `Type Text`, `Open Browser`, `Input Text`).
    - Convert K6 `check()` assertions into explicit Robot Framework assertions (e.g., `Status Should Be    200`).

    FORMATTING RULES:
    - Maintain section order:
        *** Settings ***
        *** Variables ***
        *** Keywords ***
        *** Test Cases ***
    - Under `*** Settings ***`, import required libraries (e.g., `Library    RequestsLibrary` or `Library    SeleniumLibrary`).
    - CRITICAL SPACING RULE: Use AT LEAST TWO SPACES (or a TAB) between keywords, arguments, and variable names. Single spaces are invalid.
    """
        }

    # Pair-specific conversion rules: (from_lower, to_lower) → extra instructions
    _FW_PAIR_RULES: dict[tuple[str, str], str] = {
            ("robot framework", "playwright"): """
    ROBOT FRAMEWORK → PLAYWRIGHT CONVERSION RULES:

    GENERIC COMPLETENESS PRINCIPLE (applies to every conversion, not just this file):
    Convert EVERYTHING the source test actually uses — do not convert the test
    body alone. Walk the dependency chain and port each part that the chosen
    test(s) reference:
      - Every `Library` import in *** Settings *** (built-in AND custom Python libraries).
      - Every `Resource` import, recursively, and the keywords within them that the test calls.
      - Every `Variables` file / *** Variables *** entry the test or its keywords read.
      - `Suite Setup`, `Suite Teardown`, `Test Setup`, `Test Teardown` — convert these to
        pytest fixtures (module/function scope as appropriate), never silently drop them.
      - Every user keyword transitively called by the test, with its real argument
        signature and defaults preserved.
    If a referenced keyword/library cannot be found in the provided context, emit a
    clearly-named helper stub and a TODO naming exactly what is missing, rather than
    omitting the behavior.

    PORT CUSTOM PYTHON LIBRARIES FROM THEIR IMPLEMENTATION, NOT GUESSES:
    When the suite imports a custom library (e.g. a Cassandra/Kafka/DB/HTTP client),
    read that library's Python source if it is in context and replicate its real
    construction details in the helper:
      - Authentication: auth providers, usernames, password/secret FILE paths, tokens,
        TLS. A client that connects without the source's auth WILL fail against a
        secured backend. (e.g. cassandra-driver PlainTextAuthProvider reading a mounted
        secret file; Kafka SASL/SSL; bearer tokens for HTTP.)
      - Ports, keyspaces/topics/namespaces, load-balancing/retry policies, timeouts.
      - Expose each as an env override with the SAME default the source used.

    REMOTE / KEYWORD-SERVER LIBRARIES (Robot `Remote` library, XML-RPC keyword servers):
    The keyword logic lives on the SERVER, not in the suite. The helper is a thin
    XML-RPC proxy (xmlrpc.client is sufficient — no extra dependency needed).
      - Keyword names on the wire are NORMALIZED: Robot lowercases and replaces spaces
        with underscores, keeps a trailing `?`, and keeps the `namespace/` prefix.
        So Robot `clj.analytics/combined metrics disabled?` is sent as
        `analytics/combined_metrics_disabled?`. NEVER send the spaced Robot form —
        the server throws a NullPointerException on an unresolved name.
      - If the exact registered names are unknown, call the server's
        `get_keyword_names` once and map to the precise strings (casing matters).
      - Pass arguments with the same arity the server expects; a zero-arg call to a
        keyword that needs a host raises an ArityException.

    RUNTIME-RESOLVED CONFIG (service discovery, not env vars):
    Robot config modules often compute hosts at runtime (e.g.
    resolve_hostname_to_ip("cassandra")) rather than reading env vars. In-cluster,
    default the helper to those SAME service names/ports so the test needs zero
    configuration, with env vars as optional overrides. Do not invent IPs.

    STATEFUL CONNECTION LIFECYCLE (servers that keep a global/persistent session):
    - Make connect idempotent: disconnect any stale session first, then connect,
      so a prior failed run does not leave the server "Already connected".
    - Wrap fixture setup so that if any setup step fails, connections are still
      released before the error propagates (pytest runs post-yield teardown only
      when setup reached `yield`).
    - Faithfully port multi-step connect keywords (e.g. "connect to each host and
      stay on the first that reports healthy"), not just the first call.

    ASSERTIONS & DATA SEMANTICS:
    Preserve exact computed values and data shapes (timestamps, week math, numeric
    results). Map Robot assertions to explicit checks (Should Not Be Empty,
    Should Be Equal As Numbers, list containment). Playwright's expect() targets
    DOM/HTTP only — for backend values use a small value-assertion helper, and keep
    pytest fixtures + pytest-playwright structure. `Pass Execution If` → pytest.skip.
    When a required backend/driver is unavailable, SKIP with a clear reason rather
    than letting the test falsely pass.
""",
            ("k6", "robot framework"): """
    K6 → Robot Framework CONVERSION RULES:
    K6 is an HTTP/load tool. Robot Framework replaces it using RequestsLibrary for HTTP calls.
    MANDATORY library imports under *** Settings ***:
        Library    RequestsLibrary
        Library    Collections

    - Return Statements: Use modern 'RETURN    ${variable}' syntax (NEVER use '[Return]').
    - Exception Handling: Use standard TRY / EXCEPT AS ${error} / END blocks.
    - Dictionary Lookups: Use direct index syntax '${json}[key1][key2]' for nested properties instead of multi-arg 'Get From Dictionary'.
    - Variable Types in Keywords: Pass list parameters as standard scalars '${METRICS}' when placed before other arguments in [Arguments].

    EXAMPLE CONVERSION PATTERN:
    *** Keywords ***
    Get PM Server Response
        [Arguments]    ${metric_name}
        [Documentation]    Calls the PM server endpoint for a single metric.
        \({endpoint}=    Set Variable    /metrics/\){metric_name}
        \({response}=    GET On Session    api\){endpoint}
        RETURN    ${response}

    K6 → Robot Framework keyword mapping (MUST follow exactly):
    http.get(url)                        → GET On Session    alias    endpoint
    http.post(url, body)                 → POST On Session   alias    endpoint    json=${body}
    http.put(url, body)                  → PUT On Session    alias    endpoint    json=${body}
    http.del(url)                        → DELETE On Session alias    endpoint
    check(res, {'status is 200': ...})   → Status Should Be    200    ${response}
    check(res, {'body contains X': ...}) → Should Contain    ${response.text}    X
    check(res, {'duration < N': ...})    → Should Be True    ${response.elapsed.total_seconds()} < N
    JSON field assertion                 → \({json}=    Set Variable\){response.json()}
                                            Dictionary Should Contain Key    ${json}    field_name
                                            Should Be Equal As Strings    ${json}[field]    expected
    sleep(N)                             → Sleep    Ns
    options.vus / options.duration       → Document in *** Variables *** as \({VUS} and\){DURATION}

    Session setup pattern (REQUIRED for every test suite):
    *** Keywords ***
    Setup Session
        Create Session    alias    ${BASE_URL}

    Each test MUST:
    1. Call Setup Session (or use Suite Setup    Setup Session under *** Settings ***)
    2. Make the HTTP call and store in ${response}
    3. Assert EVERY k6 check() entry with the matching RF keyword above

    EXAMPLE — converting this k6 code:
    const res = http.get(`${BASE_URL}/users`);
    check(res, {
        'status is 200': (r) => r.status === 200,
        'has users key': (r) => r.json().users !== undefined,
    });

    MUST produce:
    *** Settings ***
    Library    RequestsLibrary
    Library    Collections

    *** Variables ***
    ${BASE_URL}    https://example.com

    *** Test Cases ***
    Get Users Returns 200 With Users Key
        Create Session    api    \({BASE_URL}\){response}=    GET On Session    api    /users
        Status Should Be    200    ${response}
        \({json}=    Set Variable\){response.json()}
        Dictionary Should Contain Key    ${json}    users
    """,
        }

    def _build_system_prompt(self, from_name: str, to_name: str, gap_notes: str,
                              shared_context: str = "", target_lang: str = "python",
                              to_fw: Any = None, library_block: str = "") -> str:
        eco = self._LANG_ECOSYSTEM.get(target_lang, self._LANG_ECOSYSTEM["python"])
        lang_title = target_lang.title() if target_lang != "c#" else "C#"
        ctx = f"\n\nShared project context (page objects / base classes):\n{shared_context[:2000]}" if shared_context else ""
        lib = f"\n\n{library_block}" if library_block else ""
        api_notes = self._build_api_notes(to_fw, to_name)

        conv_notes = ""
        raw_conv = getattr(to_fw, "conversion_notes", "") if to_fw else ""
        if raw_conv:
            conv_notes = f"\n\n{to_name}-specific conversion guidance:\n{raw_conv.strip()}"

        # Inject framework-specific syntax rules
        syntax_rules = self._FW_SYNTAX_RULES.get(to_name.lower(), "")
        # Inject pair-specific rules (overrides generic syntax rules when present)
        pair_rules = self._FW_PAIR_RULES.get((from_name.lower(), to_name.lower()), "")

        return (
            f"You are an expert test automation engineer.{ctx}{lib}\n"
            f"Convert the following test code from {from_name} to {to_name}. "
            f"Output ONLY valid {to_name} syntax — {'a .robot file with *** sections ***' if target_lang == 'robot' else f'{lang_title} code'}.\n"
            f"Rules:\n"
            f"1. Output ONLY valid, runnable {to_name} {'keywords and test cases' if target_lang == 'robot' else f'{lang_title} code'} using {to_name}'s native API.\n"
            f"2. Preserve ALL test intent and assertions — every source assertion MUST become a {to_name} assertion {'keyword' if target_lang == 'robot' else 'call'}.\n"
            f"3. Use {eco['runner']} as the test runner/entry point.\n"
            f"4. Convert EVERYTHING the test actually uses, not just the test body: resolve "
            f"each import, shared resource, variables file, setup/teardown, and every "
            f"helper/keyword the test transitively calls from the context above. For any "
            f"custom client library in context, replicate its REAL construction (auth/"
            f"credentials, ports, hosts, policies) — do not guess. If something referenced "
            f"is missing from context, emit a clearly-named stub with a TODO rather than "
            f"dropping the behavior.\n"
            f"5. {'Define reusable steps in a *** Keywords *** section.' if target_lang == 'robot' else f'For any capability {to_name} cannot handle natively, generate a helper in {lang_title} in a `# === HELPER: <name> ===` section.'}\n"
            f"6. Use the CURRENT stable API — do NOT use deprecated or experimental import paths.\n"
            f"7. End with a CI/CD INTEGRATION comment block showing the GitHub Actions step."
            + conv_notes
            + api_notes
            + gap_notes
            + (f"\n\n{pair_rules}" if pair_rules else (f"\n\n{syntax_rules}" if syntax_rules else ""))
        )

    def _build_library_context(self, files: list[dict], from_name: str,
                               to_name: str, target_lang: str) -> Any:
        """Parse+resolve Robot Settings imports within `files`, derive the auth
        contract, budget it, and return a LibraryContext. Pure w.r.t. `files`
        (no disk/network). Returns an empty LibraryContext for non-Robot or
        import-less input, and also on any unexpected failure so the conversion
        is never broken by this advisory feature."""
        from src.tools import library_ingest as _li
        from src.codegen.agent_config import PIPELINE_SETTINGS
        try:
            return _li.build_library_context(
                files, from_name, to_name, target_lang,
                char_budget=PIPELINE_SETTINGS["max_library_context_chars"],
            )
        except Exception as exc:  # noqa: BLE001 — feature must never break conversion
            logger.error("library ingest failed — %s: %s", type(exc).__name__, exc)
            return _li.LibraryContext()

    def _build_conversion_report(
        self,
        lib_ctx: Any,
        *,
        from_name: str,
        to_name: str,
        target_lang: str,
        converted: dict,
        helpers: dict,
        conftest: str,
        requirements: str,
        gaps: list[str],
        assertion_report: Any,
    ) -> dict:
        """Build a generic, additive conversion completeness report.

        Shared by both conversion paths so no-Groq-key (manual-mode) and
        Groq-backed conversions report identically. Populated purely from what
        THIS conversion actually did — no framework specifics hardcoded. Degrades
        gracefully: for a non-Robot source or a suite with no resolvable imports,
        HANDLED still lists the basic conversion facts and ASSUMED/NOT_DONE may be
        empty or note 'no library layer found'.

        Returns:
            {
              "handled":  [{"item": str, "detail": str}, ...],
              "assumed":  [{"item": str, "why": str, "placeholder": str}, ...],
              "not_done": [{"item": str, "reason": str}, ...],
              "text":     str,  # human-readable rendering (also folded into summary)
            }
        """
        handled: list[dict] = []
        assumed: list[dict] = []
        not_done: list[dict] = []

        # ── HANDLED (basic conversion facts — always present) ──
        handled.append({
            "item": "files converted",
            "detail": f"{len(converted)} file(s) converted from {from_name} to "
                      f"{to_name} ({target_lang}).",
        })

        # Assertion parity, when the counter produced a count.
        preserved = None
        if isinstance(assertion_report, dict):
            for key in ("converted_assertions", "target_assertions",
                        "total_converted", "converted", "target"):
                if isinstance(assertion_report.get(key), int):
                    preserved = assertion_report[key]
                    break
        if preserved is not None:
            handled.append({
                "item": "assertions preserved",
                "detail": f"{preserved} assertion(s) emitted in the converted suite.",
            })

        if conftest:
            handled.append({"item": "conftest generated",
                            "detail": "conftest.py generated for the target suite."})
        if requirements:
            handled.append({"item": "requirements generated",
                            "detail": "requirements.txt dependency manifest generated."})

        lib_present = lib_ctx is not None and not getattr(lib_ctx, "is_empty", True)
        resolved = tuple(getattr(lib_ctx, "resolved", ()) or ())
        unresolved = tuple(getattr(lib_ctx, "unresolved", ()) or ())
        contract = getattr(lib_ctx, "contract", None)
        helper_spec = getattr(lib_ctx, "helper_spec", None)
        aliases = dict(getattr(lib_ctx, "aliases", {}) or {})

        if resolved:
            names = ", ".join(
                (r.filename or getattr(r.imp, "target", "?")) for r in resolved
            )
            handled.append({
                "item": "library/resource imports resolved",
                "detail": f"{len(resolved)} import(s) resolved within the uploaded "
                          f"set: {names}.",
            })
        if aliases:
            mapping = ", ".join(f"{a} -> {t}" for a, t in aliases.items())
            handled.append({"item": "import aliases",
                            "detail": f"WITH NAME aliases carried: {mapping}."})

        auth_derived = (
            contract is not None and not getattr(contract, "is_empty", True)
        )
        if auth_derived:
            bits: list[str] = []
            if contract.login_endpoint:
                bits.append(f"login {contract.login_method or '?'} "
                            f"{contract.login_endpoint}")
            if contract.credential_location:
                bits.append(f"credentials via {contract.credential_location}")
            if contract.token_response_field:
                bits.append(f"token field '{contract.token_response_field}'")
            if contract.later_call_auth:
                bits.append(f"later calls use {contract.later_call_auth}")
            for sec in contract.secondary_systems:
                bits.append(f"secondary '{sec.name}' token endpoint "
                            f"{sec.token_endpoint} attaches as {sec.attaches_as}")
            handled.append({
                "item": "auth/login flow derived",
                "detail": "Derived from ingested libraries: "
                          + ("; ".join(bits) if bits else "auth contract detected")
                          + ".",
            })

        if helper_spec is not None:
            handled.append({
                "item": "shared auth helper generated",
                "detail": f"{helper_spec.path} exporting "
                          f"{', '.join(helper_spec.exports)}.",
            })

        # ── ASSUMED (needs verification) ──
        if auth_derived and contract is not None:
            for line in getattr(contract, "assumptions", ()) or ():
                # Lines look like "<marker> ASSUMPTION — ...: <label> -> <ref>"
                label, _, ref = line.partition("->")
                why = label.split(":", 1)[-1].strip() if ":" in label else label.strip()
                assumed.append({
                    "item": why or "runtime value",
                    "why": "cannot be derived from static library code — a runtime/"
                           "config value.",
                    "placeholder": ref.strip() or "(env var)",
                })

        # ── NOT DONE (gaps) ──
        for g in gaps or []:
            not_done.append({
                "item": f"capability gap: {g.replace('_', ' ')}",
                "reason": f"{to_name} has no native equivalent; a helper/manual step "
                          f"is required.",
            })
        for imp in unresolved:
            not_done.append({
                "item": f"unresolved import: {getattr(imp, 'kind', '?')} "
                        f"{getattr(imp, 'target', '?')}",
                "reason": "not present in the uploaded file set — could not be "
                          "ingested for auth/transport derivation.",
            })
        if lib_present and not auth_derived:
            not_done.append({
                "item": "auth flow derivation",
                "reason": "library layer found but no auth/transport contract could "
                          "be derived from it.",
            })
        if not resolved and not unresolved:
            not_done.append({
                "item": "library layer ingestion",
                "reason": "no Robot library/resource layer found in the uploaded set "
                          "(nothing to derive auth from).",
            })

        report = {
            "handled": handled,
            "assumed": assumed,
            "not_done": not_done,
        }
        report["text"] = self._render_report_text(report)
        return report

    @staticmethod
    def _render_report_text(report: dict) -> str:
        """Render a report dict into the human-readable summary section."""
        lines = ["### What was done / assumed / not done"]

        lines.append("\n**Done (handled):**")
        if report["handled"]:
            for h in report["handled"]:
                lines.append(f"- {h['item']}: {h['detail']}")
        else:
            lines.append("- (nothing recorded)")

        lines.append("\n**Assumed (verify before running):**")
        if report["assumed"]:
            for a in report["assumed"]:
                lines.append(f"- {a['item']} — {a['why']} Fill: {a['placeholder']}")
        else:
            lines.append("- none")

        lines.append("\n**Not done (gaps):**")
        if report["not_done"]:
            for n in report["not_done"]:
                lines.append(f"- {n['item']} — {n['reason']}")
        else:
            lines.append("- none")

        return "\n".join(lines)

    def _llm_convert(self, prompt: str, system: str) -> str:
        """Call LLM and return content string, or error string."""
        if not getattr(self, "_llm", None):
            return "LLM client not available. Pass `llm_client` to ToolExecutor."
        try:
            result = self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system=system,
                temperature=0.1,
            )
            return result.get("content") or result.get("reasoning", "")
        except Exception as exc:
            logger.error("LLM call failed: %s", exc)
            return f"LLM conversion failed: {exc}"

    def _gap_table_lines(self, gaps: list[str], to_name: str) -> list[str]:
        if not gaps:
            return []
        rows = [
            "\n---\n### ⚠️ Capabilities Requiring Python Helper Scripts",
            "| Capability | Reason | Helper Approach |",
            "|---|---|---|",
        ]
        for gap in gaps:
            label = gap.replace("_", " ").title()
            hint = self._HELPER_MAP.get(gap, "Custom Python script via `subprocess`")
            rows.append(f"| {label} | Not in {to_name} | {hint} |")
        return rows

    def _generate_helper_scripts(self, gaps: list[str], to_name: str,
                                 target_lang: str = "python") -> dict[str, str]:
        """Generate standalone helper files for each capability gap in the target language."""
        if not getattr(self, "_llm", None) or not gaps:
            return {}
        eco = self._LANG_ECOSYSTEM.get(target_lang, self._LANG_ECOSYSTEM["python"])
        ext = eco["ext"]
        fence = eco["fence"]
        lang_title = target_lang.title() if target_lang != "c#" else "C#"

        helpers: dict[str, str] = {}
        import re
        for gap in gaps:
            label = gap.replace("_", " ").title()
            hint = self._HELPER_MAP.get(gap, f"Custom {lang_title} helper")
            prompt = (
                f"Generate a complete, runnable {lang_title} helper that implements "
                f"'{label}' support for a {to_name} test suite.\n"
                f"Approach (adapt to {lang_title}): {hint}\n"
                f"Requirements:\n"
                f"1. Provide it in a form the {to_name} tests can import/call.\n"
                f"2. Include all imports and a usage example.\n"
                f"3. Output ONLY the {lang_title} code block."
            )
            system = f"You are an expert {lang_title} test automation engineer. Output only valid {lang_title} code."
            code = self._llm_convert(prompt, system)
            blocks = re.findall(rf"```(?:{fence})?\n(.*?)```", code, re.DOTALL)
            if not blocks:
                blocks = re.findall(r"```[a-zA-Z]*\n(.*?)```", code, re.DOTALL)
            helpers[f"helpers/{gap}_helper{ext}"] = blocks[0].strip() if blocks else code
        return helpers

    def _convert_test_cases(
        self,
        source_code: str,
        from_framework: str,
        to_framework: str,
        language: str = "python",
    ) -> str:
        import re as _re
        from_fw = self._resolve_framework(from_framework)
        to_fw   = self._resolve_framework(to_framework)
        from_name = from_fw.framework_name if from_fw else from_framework
        to_name   = to_fw.framework_name   if to_fw   else to_framework
        target_lang, eco = self._resolve_target_language(to_fw, to_name)
        lang_title = target_lang.title() if target_lang != "c#" else "C#"
        gap_notes, _ = self._build_gap_notes(from_fw, to_fw, to_name, target_lang)
        system = self._build_system_prompt(from_name, to_name, gap_notes,
                                           target_lang=target_lang, to_fw=to_fw)
        prompt = (
            f"Convert this {from_name} test code to {to_name} ({lang_title}):\n\n"
            f"```\n{source_code[:4000]}\n```"
        )
        converted = self._llm_convert(prompt, system)
        # Strip any markdown fence the LLM added
        code_blocks = _re.findall(r"```(?:[a-zA-Z]*)\n(.*?)```", converted, _re.DOTALL)
        clean_code = code_blocks[0].strip() if code_blocks else converted.strip()
        # Single-file conversion — store as-is, no splitting
        ext = eco.get("ext", ".py")
        self._last_split_files = {f"tests/test_converted{ext}": clean_code}
        # Assertion parity check
        from src.tools.assertion_counter import compare as _cmp_assert
        self._last_assertion_report = _cmp_assert(source_code, clean_code, from_name, to_name)
        return f"## Converted: {from_name} -> {to_name} ({lang_title})\n" + converted

    def convert_multi_file(
        self,
        files: list[dict],          # [{"filename": str, "content": str}, ...]
        from_framework: str,
        to_framework: str,
    ) -> dict:
        """Convert multiple source files to the target framework.

        Returns a dict:
          {
            "converted": {"tests/<name>.py": code, ...},
            "helpers":   {"helpers/<gap>_helper.py": code, ...},
            "conftest":  str,          # conftest.py content
            "requirements": str,       # requirements.txt content
            "gaps":      [str, ...],   # capability gap names
            "summary":   str,          # markdown summary
          }
        """
        from_fw = self._resolve_framework(from_framework)
        to_fw = self._resolve_framework(to_framework)
        from_name = from_fw.framework_name if from_fw else from_framework
        to_name = to_fw.framework_name if to_fw else to_framework

        target_lang, eco = self._resolve_target_language(to_fw, to_name)
        lang_title = target_lang.title() if target_lang != "c#" else "C#"
        ext = eco["ext"]
        fence = eco["fence"]

        gap_notes, gaps = self._build_gap_notes(from_fw, to_fw, to_name, target_lang)

        # Build shared context: first 100 lines of each file (for cross-file imports)
        shared_context = "\n\n".join(
            f"# --- {f['filename']} ---\n" + "\n".join(f['content'].splitlines()[:100])
            for f in files
        )

        # Ingest the imported library/helper layer and derive an auth/transport
        # contract (additive; empty for non-Robot / import-less input).
        lib_ctx = self._build_library_context(files, from_name, to_name, target_lang)

        system = self._build_system_prompt(from_name, to_name, gap_notes,
                                           shared_context, target_lang, to_fw=to_fw,
                                           library_block=lib_ctx.prompt_block)

        converted: dict[str, str] = {}
        import re

        for file in files:
            fname = file["filename"]
            content = file["content"]

            # Chunk large files at 4000 chars, convert each chunk, join
            chunks = [content[i:i+4000] for i in range(0, max(len(content), 1), 4000)]
            file_parts: list[str] = []
            for idx, chunk in enumerate(chunks):
                chunk_note = f" (part {idx+1}/{len(chunks)})" if len(chunks) > 1 else ""
                prompt = (
                    f"Convert this {from_name} file '{fname}'{chunk_note} to {to_name} ({lang_title}):\n\n"
                    f"```\n{chunk}\n```\n"
                    f"Output ONLY the {lang_title} code block, no explanation."
                )
                raw = self._llm_convert(prompt, system)
                # Try language-specific fence first, then any fenced block
                blocks = re.findall(rf"```(?:{fence})?\n(.*?)```", raw, re.DOTALL)
                if not blocks:
                    blocks = re.findall(r"```[a-zA-Z]*\n(.*?)```", raw, re.DOTALL)
                file_parts.append(blocks[0].strip() if blocks else raw)

            # Derive output filename: preserve the source directory structure,
            # use the target language's extension.
            src_path = Path(fname)
            stem = src_path.stem
            if stem.startswith("conftest") and target_lang == "python":
                out_name = "conftest.py"
            else:
                parent = src_path.parent.as_posix()
                if parent in (".", "", "/"):
                    out_name = f"tests/{stem}{ext}"
                else:
                    out_name = f"{parent}/{stem}{ext}"
            converted[out_name] = "\n\n".join(file_parts)


        # Split each converted file into logical sub-files
        from src.tools.output_splitter import split as split_output
        split_converted: dict[str, str] = {}
        for _path, _code in converted.items():
            _stem = Path(_path).stem
            _parts = split_output(_code, to_name, source_stem=_stem)
            if len(_parts) > 1:
                split_converted.update(_parts)
            else:
                split_converted[_path] = _code
        converted = split_converted

        # Generate helper scripts for every capability gap (in the target language)
        helpers = self._generate_helper_scripts(gaps, to_name, target_lang)

        # Emit the derived shared auth helper (e.g. lib/auth.js) via the existing
        # additive `helpers` dict when the ingest produced one.
        if lib_ctx.helper_spec is not None:
            helpers[lib_ctx.helper_spec.path] = lib_ctx.helper_spec.content

        # Generate conftest/setup + manifest (only meaningful for Python;
        # for other langs these become language-appropriate stubs)
        conftest = self._generate_conftest(to_name, gaps, list(helpers.keys())) if target_lang == "python" else ""

        # Generate dependency manifest
        requirements = self._generate_requirements(to_name, gaps, from_name) if target_lang == "python" else ""

        # Assertion parity check
        from src.tools.assertion_counter import compare_multi as _cmp_multi
        all_source = files
        all_converted_code = converted
        assertion_report = _cmp_multi(all_source, all_converted_code, from_name, to_name)

        # Markdown summary
        summary_lines = [
            f"## 🔄 Multi-File Conversion: {from_name} → {to_name} ({lang_title})",
            f"**{len(files)} source file(s) converted · {len(helpers)} helper script(s) generated**\n",
            "### 📁 Output Structure",
            "```",
            f"converted_{to_name.lower().replace(' ', '_')}/",
        ]
        if conftest:
            summary_lines.append("├── conftest.py")
        if requirements:
            summary_lines.append("├── requirements.txt")
        for path in sorted(converted):
            summary_lines.append(f"├── {path}")
        for path in sorted(helpers):
            summary_lines.append(f"├── {path}")
        summary_lines.append("```")

        if gaps:
            summary_lines += self._gap_table_lines(gaps, to_name)

        # Append the (guarded) unresolved-import notes only when there are any,
        # so an import-less / non-Robot conversion's summary is byte-for-byte
        # identical to pre-change output.
        if lib_ctx.notes:
            summary_lines.append("\n### ⚠️ Unresolved library imports")
            for note in lib_ctx.notes:
                summary_lines.append(f"- {note}")

        # Build the generic conversion completeness report (additive) and fold a
        # concise human-readable rendering into the summary.
        report = self._build_conversion_report(
            lib_ctx,
            from_name=from_name,
            to_name=to_name,
            target_lang=target_lang,
            converted=converted,
            helpers=helpers,
            conftest=conftest,
            requirements=requirements,
            gaps=gaps,
            assertion_report=assertion_report,
        )
        summary_lines.append("")
        summary_lines.append(report["text"])

        return {
            "converted": converted,
            "helpers": helpers,
            "conftest": conftest,
            "requirements": requirements,
            "gaps": gaps,
            "summary": "\n".join(summary_lines),
            "assertion_report": assertion_report,
            "report": report,
        }

    def _generate_conftest(self, to_name: str, gaps: list[str], helper_paths: list[str]) -> str:
        imports = "\n".join(
            f"from {p.replace('/', '.').removesuffix('.py')} import *  # noqa"
            for p in helper_paths
        )
        return (
            f"# conftest.py — auto-generated for {to_name} migration\n"
            f"# Imports all gap-bridging helper fixtures\n"
            f"import pytest\n"
            + (imports + "\n" if imports else "")
            + "\n\n"
            f"# Add any project-wide fixtures below\n"
        )

    def _generate_requirements(self, to_name: str, gaps: list[str], from_name: str = "") -> str:
        base = {"pytest": ">=7.0", "pytest-asyncio": ">=0.21"}
        # Read pip_packages from YAML — no hardcoded framework list
        fw = self._resolve_framework(to_name)
        fw_deps = dict(fw.pip_packages) if fw else {}
        gap_deps: dict[str, dict[str, str]] = {
            "network_interception": {"responses": ">=0.24", "httpx": ">=0.25"},
            "visual_regression": {"Pillow": ">=10.0", "pixelmatch": ">=0.3"},
            "performance_testing": {"locust": ">=2.0"},
            "load_testing": {"locust": ">=2.0"},
            "accessibility_testing": {"axe-selenium-python": ">=2.1"},
            "gesture_support": {"Appium-Python-Client": ">=3.0"},
            "device_farm_support": {"requests": ">=2.31"},
        }
        deps = {**base, **fw_deps}
        for gap in gaps:
            deps.update(gap_deps.get(gap, {}))
        # K6 → Robot Framework needs RequestsLibrary for HTTP assertions
        if "k6" in from_name.lower() and "robot" in to_name.lower():
            deps["robotframework-requests"] = ">=0.9"
            deps["robotframework"] = ">=7.0"
        return "\n".join(f"{pkg}{ver}" for pkg, ver in sorted(deps.items()))

    def _score_frameworks(
        self,
        use_case: str = "",
        frameworks: list[str] | None = None,
        language: str = "python",
        architecture: str = "web_spa",
    ) -> str:
        """Run ScoringEngine with the active WeightProfile and return a ranked scorecard."""
        from src.scoring.engine import ScoringEngine
        from src.models import UserProfile, ArchitectureType, ExperienceLevel

        arch_map = {
            "api": ArchitectureType.API_ONLY,
            "api_only": ArchitectureType.API_ONLY,
            "microservice": ArchitectureType.MICROSERVICES,
            "microservices": ArchitectureType.MICROSERVICES,
            "mobile": ArchitectureType.NATIVE_MOBILE,
            "web_spa": ArchitectureType.WEB_SPA,
            "web_mpa": ArchitectureType.WEB_MPA,
            "desktop": ArchitectureType.DESKTOP,
        }
        arch_enum = arch_map.get(architecture.lower(), ArchitectureType.WEB_SPA)

        profile = UserProfile(
            project_name="advisor_query",
            architecture_types=[arch_enum],
            primary_language=language or "python",
            team_size=5,
            automation_experience=ExperienceLevel.INTERMEDIATE,
            ci_cd_tool="github_actions",
        )

        engine = ScoringEngine(knowledge_base=self.kb, weight_profile=self.weight_profile)
        matrix = engine.evaluate(profile)

        rankings = matrix.rankings
        if frameworks:
            fw_lower = [f.lower() for f in frameworks]
            filtered = [r for r in rankings if r.framework.lower() in fw_lower]
            rankings = filtered or rankings

        _labels = {
            "C1_language_compatibility": "Language Compatibility",
            "C2_api_validation": "API Validation",
            "C3_performance_load": "Performance Testing",
            "C4_cicd_integration": "CI/CD Integration",
            "C5_maintainability": "Maintainability",
            "C6_cloud_readiness": "Cloud Readiness",
            "C7_license_cost": "License & Cost",
        }

        lines = [
            f"## Weighted Framework Scorecard",
            f"*Active weight preset: **{self.weight_profile.profile_name}***\n",
            "| Rank | Framework | Score | Confidence | Top Strength | Key Weakness |",
            "|---|---|---|---|---|---|",
        ]
        for r in rankings[:10]:
            top_pro = r.pros[0] if r.pros else "—"
            top_con = r.cons[0] if r.cons else "—"
            lines.append(
                f"| {r.rank} | **{r.framework}** | {r.overall_score}/100 "
                f"| {r.confidence} | {top_pro} | {top_con} |"
            )

        lines.append("\n### Criteria Weights Applied")
        # Highest-weighted criterion first, so the priority order is explicit.
        for cid, w in sorted(
            self.weight_profile.weights.items(), key=lambda kv: -kv[1]
        ):
            if w > 0:
                lines.append(f"- {_labels.get(cid, cid)}: **{w:.0%}**")

        if rankings:
            lines.append(f"\n### Top Pick: {rankings[0].framework}")
            lines.append(rankings[0].explanation)

        return "\n".join(lines)
