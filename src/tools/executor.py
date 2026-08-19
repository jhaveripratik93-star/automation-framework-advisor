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
        """Delegates to coverage_engine — fully generic, YAML-derived."""
        from src.tools.coverage_engine import analyze_coverage, render_coverage_report
        analysis = analyze_coverage(test_cases, self.kb, frameworks)
        report = render_coverage_report(analysis, len(test_cases))
        note = self._find_case_study_relevance(
            list({d["capability"] for d in analysis["matrix"].values()})
        )
        return report + ("\n" + note if note else "")
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

    def _build_gap_notes(self, from_fw: Any, to_fw: Any, to_name: str) -> tuple[str, list[str]]:
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
            + ". For those capabilities, generate a standalone Python helper "
            "script using `requests`/`httpx`/`subprocess` and show how to "
            "call it from a pytest fixture."
        )
        return note, gaps

    def _build_system_prompt(self, from_name: str, to_name: str, gap_notes: str,
                              shared_context: str = "") -> str:
        ctx = f"\n\nShared project context (page objects / base classes):\n{shared_context[:2000]}" if shared_context else ""
        return (
            f"You are an expert test automation engineer.{ctx}\n"
            f"Convert the following test code from {from_name} to {to_name} using Python.\n"
            f"Rules:\n"
            f"1. Output ONLY valid, runnable Python code using {to_name}'s Python API.\n"
            f"2. Preserve all test intent and assertions.\n"
            f"3. Use pytest as the test runner.\n"
            f"4. Resolve any imports that reference other files in the shared context above.\n"
            f"5. For any capability {to_name} cannot handle natively, generate a "
            f"   helper script in a `# === HELPER SCRIPT: <name> ===` section and show a "
            f"   pytest fixture that calls it.\n"
            f"6. End with a `# === CI/CD INTEGRATION ===` comment block showing "
            f"   the GitHub Actions step to run the converted tests."
            + gap_notes
        )

    def _llm_convert(self, prompt: str, system: str) -> str:
        """Call LLM and return content string, or error string."""
        if not getattr(self, "_llm", None):
            return "LLM client not available. Pass `llm_client` to ToolExecutor."
        try:
            result = self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system=system,
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

    def _generate_helper_scripts(self, gaps: list[str], to_name: str) -> dict[str, str]:
        """Generate standalone helper .py files for each capability gap."""
        if not getattr(self, "_llm", None) or not gaps:
            return {}
        helpers: dict[str, str] = {}
        for gap in gaps:
            label = gap.replace("_", " ").title()
            hint = self._HELPER_MAP.get(gap, "Custom Python script via subprocess")
            prompt = (
                f"Generate a complete, runnable Python helper script that implements "
                f"'{label}' support for a {to_name} test suite.\n"
                f"Approach: {hint}\n"
                f"Requirements:\n"
                f"1. Provide a pytest fixture in conftest.py style.\n"
                f"2. Include all imports and a usage example.\n"
                f"3. Output ONLY the Python code block."
            )
            system = "You are an expert Python test automation engineer. Output only valid Python code."
            code = self._llm_convert(prompt, system)
            import re
            blocks = re.findall(r"```(?:python)?\n(.*?)```", code, re.DOTALL)
            helpers[f"helpers/{gap}_helper.py"] = blocks[0].strip() if blocks else code
        return helpers

    def _convert_test_cases(
        self,
        source_code: str,
        from_framework: str,
        to_framework: str,
        language: str = "python",
    ) -> str:
        """Single-file conversion — preserves existing behaviour."""
        from_fw = self._resolve_framework(from_framework)
        to_fw = self._resolve_framework(to_framework)
        from_name = from_fw.framework_name if from_fw else from_framework
        to_name = to_fw.framework_name if to_fw else to_framework

        gap_notes, gaps = self._build_gap_notes(from_fw, to_fw, to_name)
        system = self._build_system_prompt(from_name, to_name, gap_notes)
        prompt = (
            f"Convert this {from_name} test code to {to_name} (Python):\n\n"
            f"```\n{source_code[:4000]}\n```"
        )
        converted = self._llm_convert(prompt, system)
        lines = [f"## 🔄 Converted: {from_name} → {to_name} (Python)\n", converted]
        lines += self._gap_table_lines(gaps, to_name)
        return "\n".join(lines)

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

        gap_notes, gaps = self._build_gap_notes(from_fw, to_fw, to_name)

        # Build shared context: first 100 lines of each file (for cross-file imports)
        shared_context = "\n\n".join(
            f"# --- {f['filename']} ---\n" + "\n".join(f['content'].splitlines()[:100])
            for f in files
        )

        system = self._build_system_prompt(from_name, to_name, gap_notes, shared_context)

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
                    f"Convert this {from_name} file '{fname}'{chunk_note} to {to_name} (Python):\n\n"
                    f"```\n{chunk}\n```\n"
                    f"Output ONLY the Python code block, no explanation."
                )
                raw = self._llm_convert(prompt, system)
                blocks = re.findall(r"```(?:python)?\n(.*?)```", raw, re.DOTALL)
                file_parts.append(blocks[0].strip() if blocks else raw)

            # Derive output filename: keep stem, force .py, put under tests/
            stem = Path(fname).stem
            out_name = f"tests/{stem}.py" if not stem.startswith("conftest") else "conftest.py"
            converted[out_name] = "\n\n".join(file_parts)

        # Generate helper scripts for every capability gap
        helpers = self._generate_helper_scripts(gaps, to_name)

        # Generate conftest.py that imports all helpers
        conftest = self._generate_conftest(to_name, gaps, list(helpers.keys()))

        # Generate requirements.txt
        requirements = self._generate_requirements(to_name, gaps)

        # Markdown summary
        summary_lines = [
            f"## 🔄 Multi-File Conversion: {from_name} → {to_name}",
            f"**{len(files)} source file(s) converted · {len(helpers)} helper script(s) generated**\n",
            "### 📁 Output Structure",
            "```",
            f"converted_{to_name.lower().replace(' ', '_')}/",
            "├── conftest.py",
            "├── requirements.txt",
        ]
        for path in sorted(converted):
            summary_lines.append(f"├── {path}")
        for path in sorted(helpers):
            summary_lines.append(f"├── {path}")
        summary_lines.append("```")

        if gaps:
            summary_lines += self._gap_table_lines(gaps, to_name)

        return {
            "converted": converted,
            "helpers": helpers,
            "conftest": conftest,
            "requirements": requirements,
            "gaps": gaps,
            "summary": "\n".join(summary_lines),
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

    def _generate_requirements(self, to_name: str, gaps: list[str]) -> str:
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
        for cid, w in self.weight_profile.weights.items():
            if w > 0:
                lines.append(f"- {_labels.get(cid, cid)}: **{w:.0%}**")

        if rankings:
            lines.append(f"\n### Top Pick: {rankings[0].framework}")
            lines.append(rankings[0].explanation)

        return "\n".join(lines)
