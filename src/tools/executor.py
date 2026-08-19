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
        }

    # ── Dispatch ──────────────────────────────────────────────────────

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name not in self.tools:
            logger.error("ToolExecutor: unknown tool '%s'", tool_name)
            return f"Error: unknown tool '{tool_name}'"
        try:
            result = self.tools[tool_name](**arguments)
            logger.info("ToolExecutor: '%s' → %d chars", tool_name, len(str(result)))
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
        top = [fw for _, fw in scored[:5]]

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
            "performance": ArchitectureType.WEB_SPA,
            "load": ArchitectureType.WEB_SPA,
        }
        arch = next(
            (v for k, v in arch_map.items() if k in use_case_lower),
            ArchitectureType.WEB_SPA,
        )

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

        # ── Weight priority header ────────────────────────────────────
        sorted_weights = sorted(
            [(lbl, self.weight_profile.get(cid)) for cid, lbl in active],
            key=lambda x: -x[1],
        )
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
        """Coverage matrix with gap analysis and recommended framework combos.

        Workflow:
          1. Build capability_map (capability label → YAML keys).
          2. Build matrix[tc_id] = {support: {fw_name: bool}}.
          3. Calculate per-framework coverage counts.
          4. Render summary table.
          5. Gap analysis per framework (top 5) with alternatives.
          6. Greedy set-cover for recommended combinations.
          7. Case-study relevance for the capabilities being tested (NEW).
        """
        capability_map = {
            "ui automation": ["shadow_dom", "iframe_cross_origin", "multi_tab", "auto_wait", "browser_testing"],
            "api testing": ["api_testing", "rest_api", "graphql_support", "schema_validation", "json_path_support"],
            "performance testing": ["performance_testing", "load_testing", "parallel_execution"],
            "load testing": ["load_testing", "performance_testing"],
            "mobile testing": ["gesture_support", "device_farm_support"],
            "visual testing": ["visual_regression"],
            "accessibility testing": ["accessibility_testing"],
            "network mocking": ["network_interception"],
            "cross browser": ["cross_browser", "multi_browser"],
            "parallel execution": ["parallel_execution"],
        }
        arch_map = {
            "api testing": ["api_testing", "microservices"],
            "mobile testing": ["native_mobile", "hybrid_mobile"],
        }

        all_fw_list = self.kb.list_all()
        fw_names = frameworks or [fw.framework_name for fw in all_fw_list]

        # Step 2 — build matrix (tolerant of any field names)
        def _tc_id(tc: dict, idx: int) -> str:
            for k in ("id", "test_id", "testid", "no", "#"):
                if tc.get(k):
                    return str(tc[k])
            return f"TC{idx+1:03d}"

        def _tc_cap(tc: dict) -> str:
            for k in ("required_capability", "capability", "type", "category",
                      "test_type", "area", "feature"):
                if tc.get(k):
                    return str(tc[k]).lower()
            # Fall back: scan all string values for known capability keywords
            known = list(capability_map.keys())
            for v in tc.values():
                v_low = str(v).lower()
                for cap in known:
                    if cap in v_low:
                        return cap
            return "ui automation"

        matrix: dict[str, dict] = {}
        for _idx, tc in enumerate(test_cases):
            tc_id = _tc_id(tc, _idx)
            cap = _tc_cap(tc)
            keywords = capability_map.get(cap, [cap.replace(" ", "_")])
            matrix[tc_id] = {
                "description": tc.get("description", tc.get("desc", tc.get("name", ""))),
                "capability": cap,
                "support": {},
                "extra_fields": {k: v for k, v in tc.items()
                                 if k not in ("id", "description", "required_capability",
                                              "capability", "steps", "expected_result")},
            }
            for name in fw_names:
                fw = self.kb.get(name.lower())
                if not fw:
                    continue
                cap_ok = any(
                    fw.capabilities.get(kw) is True
                    or (isinstance(fw.capabilities.get(kw), str)
                        and fw.capabilities.get(kw) not in ("", "false", "no"))
                    for kw in keywords
                )
                arch_ok = any(
                    fw.architecture_fit.get(kw) is True
                    or (isinstance(fw.architecture_fit.get(kw), str)
                        and fw.architecture_fit.get(kw) not in ("", "false", "no"))
                    for kw in arch_map.get(cap, [])
                ) if arch_map.get(cap) else False
                matrix[tc_id]["support"][fw.framework_name] = cap_ok or arch_ok

        # Step 3 — per-framework counts
        coverage: dict[str, dict] = {}
        for name in fw_names:
            fw = self.kb.get(name.lower())
            if not fw:
                continue
            covered = [t for t, d in matrix.items() if d["support"].get(fw.framework_name)]
            uncovered = [t for t, d in matrix.items() if not d["support"].get(fw.framework_name)]
            pct = len(covered) / len(test_cases) * 100 if test_cases else 0
            coverage[fw.framework_name] = {
                "covered": covered,
                "uncovered": uncovered,
                "count": len(covered),
                "pct": pct,
            }

        total = len(test_cases)
        lines = ["# Test Case Coverage Analysis\n"]

        # Step 4 — summary table
        lines += ["## Coverage Summary\n", "| Framework | Covered | Total | % | Status |", "|---|---|---|---|---|"]
        sorted_cov = sorted(coverage.items(), key=lambda x: -x[1]["pct"])
        for fw_name, data in sorted_cov:
            status = "✅ FULL" if data["pct"] == 100 else "⚠️ PARTIAL" if data["pct"] >= 50 else "❌ LOW"
            lines.append(f"| **{fw_name}** | {data['count']} | {total} | {data['pct']:.0f}% | {status} |")
        lines.append("")

        # Step 5 — gap analysis (top 5 frameworks)
        lines.append("## Gap Analysis\n")
        for fw_name, data in sorted_cov[:5]:
            if not data["uncovered"]:
                continue
            lines.append(f"### {fw_name} — {data['pct']:.0f}% coverage\n**⚠️ Uncovered Test Cases:**")
            uncovered_by_cap: dict[str, list] = {}
            for tc_id in data["uncovered"]:
                cap = matrix[tc_id].get("capability", "unknown")
                uncovered_by_cap.setdefault(cap, []).append(tc_id)

            for cap, tc_ids in uncovered_by_cap.items():
                cap_kw = capability_map.get(cap.lower(), [cap.lower().replace(" ", "_")])
                alts = [
                    f.framework_name for f in all_fw_list
                    if f.framework_name != fw_name
                    and any(
                        f.capabilities.get(kw) is True
                        or (isinstance(f.capabilities.get(kw), str)
                            and f.capabilities.get(kw) not in ("", "false", "no"))
                        for kw in cap_kw
                    )
                ][:3]
                alt_text = f" → Use: **{', '.join(alts)}**" if alts else ""
                lines.append(f"  - **{cap}** ({len(tc_ids)} tests){alt_text}")
            lines.append("")

        # Step 6 — recommended combinations
        lines.append("## Recommended Combinations for 100% Coverage\n")
        combos = self._find_coverage_combinations(matrix, coverage)
        for i, combo in enumerate(combos[:3], 1):
            names = [c[0] for c in combo]
            cov = self._calc_combo_coverage(names, matrix)
            lines.append(f"### Option {i}: {' + '.join(names)}")
            lines.append(f"**Coverage:** {cov['pct']:.0f}% ({cov['count']}/{total})\n")
            for fw_name, caps in combo:
                lines.append(f"  - **{fw_name}**: {', '.join(list(set(caps))[:4])}")
            if cov["uncovered"]:
                lines.append(f"\n**Still uncovered:** {', '.join(cov['uncovered'][:3])}")
            lines.append("")

        # Step 7 — case-study relevance for the capabilities being tested
        all_caps = list({d["capability"] for d in matrix.values()})
        note = self._find_case_study_relevance(all_caps)
        if note:
            lines.append(note)

        return "\n".join(lines)
    
    def _find_coverage_combinations(self, matrix: dict, coverage: dict) -> list:
        """Greedy set-cover: find minimal framework combos for full coverage."""
        combinations = []
        all_tc = set(matrix.keys())
        sorted_fw = sorted(coverage.items(), key=lambda x: -x[1]["pct"])

        for start_fw, start_data in sorted_fw[:3]:
            combo = [(start_fw, [matrix[t]["capability"] for t in start_data["covered"]])]
            covered = set(start_data["covered"])
            remaining = all_tc - covered

            while remaining and len(combo) < 4:
                best_fw, best_covers = None, []
                for fw_name, fw_data in coverage.items():
                    if fw_name in [c[0] for c in combo]:
                        continue
                    new = set(fw_data["covered"]) & remaining
                    if len(new) > len(best_covers):
                        best_fw, best_covers = fw_name, list(new)
                if best_fw and best_covers:
                    combo.append((best_fw, [matrix[t]["capability"] for t in best_covers]))
                    covered.update(best_covers)
                    remaining -= set(best_covers)
                else:
                    break
            combinations.append(combo)

        return sorted(combinations, key=lambda c: (len(c), -sum(len(caps) for _, caps in c)))

    def _calc_combo_coverage(self, fw_names: list, matrix: dict) -> dict:
        covered = {t for t, d in matrix.items() if any(d["support"].get(fw) for fw in fw_names)}
        uncovered = [t for t in matrix if t not in covered]
        return {
            "count": len(covered),
            "pct": len(covered) / len(matrix) * 100 if matrix else 0,
            "uncovered": uncovered,
        }
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

    def _convert_test_cases(
        self,
        source_code: str,
        from_framework: str,
        to_framework: str,
        language: str = "python",
    ) -> str:
        """Convert test cases from one framework to another using the LLM.

        Returns a markdown block containing:
          - Converted Python test code (runnable)
          - A list of capabilities that could NOT be directly converted
            (with generated Python helper scripts for each gap)
          - A CI/CD snippet wiring the helper scripts as pre-test steps
        """
        from_fw = self._resolve_framework(from_framework)
        to_fw = self._resolve_framework(to_framework)

        from_name = from_fw.framework_name if from_fw else from_framework
        to_name = to_fw.framework_name if to_fw else to_framework

        # Identify capability gaps so we can tell the LLM what needs helpers
        gap_notes = ""
        if from_fw and to_fw:
            from_caps = {k for k, v in from_fw.capabilities.items() if v and v is not False}
            to_caps = {k for k, v in to_fw.capabilities.items() if v and v is not False}
            gaps = from_caps - to_caps
            if gaps:
                gap_notes = (
                    f"\n\nNote: {to_name} does NOT natively support: "
                    + ", ".join(g.replace("_", " ") for g in sorted(gaps))
                    + ". For those capabilities, generate a standalone Python helper "
                    "script using `requests`/`httpx`/`subprocess` and show how to "
                    "call it from a pytest fixture."
                )

        system = (
            f"You are an expert test automation engineer. "
            f"Convert the following test code from {from_name} to {to_name} using Python. "
            f"Rules:\n"
            f"1. Output ONLY valid, runnable Python code using {to_name}'s Python API.\n"
            f"2. Preserve all test intent and assertions.\n"
            f"3. Use pytest as the test runner.\n"
            f"4. For any capability {to_name} cannot handle natively, generate a "
            f"   helper script in a `# === HELPER SCRIPT ===` section and show a "
            f"   pytest fixture that calls it.\n"
            f"5. End with a `# === CI/CD INTEGRATION ===` comment block showing "
            f"   the GitHub Actions step to run the converted tests."
            + gap_notes
        )

        prompt = (
            f"Convert this {from_name} test code to {to_name} (Python):\n\n"
            f"```\n{source_code[:4000]}\n```"
        )

        if not hasattr(self, "_llm") or self._llm is None:
            return (
                f"## Conversion: {from_name} → {to_name}\n\n"
                "LLM client not available in ToolExecutor. "
                "Pass `llm_client` to ToolExecutor to enable conversion."
            )

        try:
            result = self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system=system,
            )
            converted = result.get("content") or result.get("reasoning", "")
        except Exception as exc:
            logger.error("convert_test_cases LLM call failed: %s", exc)
            converted = f"LLM conversion failed: {exc}"

        lines = [
            f"## 🔄 Converted: {from_name} → {to_name} (Python)\n",
            converted,
        ]
        if from_fw and to_fw:
            from_caps = {k for k, v in from_fw.capabilities.items() if v and v is not False}
            to_caps = {k for k, v in to_fw.capabilities.items() if v and v is not False}
            gaps = sorted(from_caps - to_caps)
            if gaps:
                lines += [
                    "\n---\n### ⚠️ Capabilities Requiring Python Helper Scripts",
                    "| Capability | Reason | Helper Approach |",
                    "|---|---|---|",
                ]
                _helper_map = {
                    "network_interception": "Use `httpx` mock transport or `responses` library",
                    "visual_regression": "Use `Pillow` + `pixelmatch-python` for screenshot diff",
                    "performance_testing": "Use `locust` or `k6` via subprocess",
                    "load_testing": "Use `locust` programmatic API",
                    "accessibility_testing": "Use `axe-selenium-python` or `playwright-axe`",
                    "component_testing": "Use `pytest` + `playwright` component fixtures",
                    "test_recorder": "Manual — no programmatic equivalent",
                }
                for gap in gaps:
                    label = gap.replace("_", " ").title()
                    hint = _helper_map.get(gap, "Custom Python script via `subprocess`")
                    lines.append(f"| {label} | Not in {to_name} | {hint} |")
        return "\n".join(lines)

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
        for cid, w in self.weight_profile.weights.items():
            if w > 0:
                lines.append(f"- {_labels.get(cid, cid)}: **{w:.0%}**")

        if rankings:
            lines.append(f"\n### Top Pick: {rankings[0].framework}")
            lines.append(rankings[0].explanation)

        return "\n".join(lines)
