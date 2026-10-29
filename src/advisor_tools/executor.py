"""AdvisorToolsExecutor — feature-owned dispatcher for the Advisor's
framework search/details/comparison/recommendation/scoring tools.

Extracted from the former monolithic ``src.tools.executor.ToolExecutor``.
Owns: search_knowledge_graph, get_framework_details,
run_framework_comparison, recommend_frameworks, score_frameworks,
list_frameworks_by_category, find_migration_paths.

``find_migration_paths`` (confidence-scored MIGRATES_TO graph lookup with
capability-gap alternatives) stays here rather than in ``src.migration``
because it's graph/KB-driven advisor Q&A, not the roadmap/coverage
planning ``src.migration.planner``/``src.migration.coverage`` already own.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.scoring.weights import WeightProfile
from src.tools.base_executor import BaseToolExecutor
from src.tools.formatters import (
    active_capabilities,
    find_case_study_relevance,
    format_architecture_fit,
    format_cicd,
    format_cloud_grids,
    format_maintainability,
    format_performance,
    format_version_info,
    positive_items,
    resolve_framework,
)

if TYPE_CHECKING:
    from src.graph import KnowledgeGraph
    from src.graph.graphrag_engine import GraphRAGEngine
    from src.knowledge_base.loader import KnowledgeBase

logger = logging.getLogger(__name__)

# Module-level lazy singleton — scan_category()'s own TTL cache already
# dedupes repeated GitHub calls, but the FrameworkScanner instance itself
# (and its httpx.Client) is cheap to share across every AdvisorToolsExecutor
# built per-request rather than re-constructing one every call.
#
# IMPORTANT: the scanner MUST be bound to a real KnowledgeBase, or its
# _filter_existing() short-circuits to "nothing is filtered" (see
# FrameworkScanner._filter_existing: `if not self._kb: return frameworks`),
# which would surface frameworks ALREADY in the KB (e.g. Playwright itself)
# as if they were new discoveries. We re-bind `._kb` on every call instead
# of baking a KB in at construction time, since each AdvisorToolsExecutor
# may be handed its own KnowledgeBase instance.
_scanner_singleton: Any | None = None


def _get_scanner_singleton(kb: Any) -> Any | None:
    global _scanner_singleton
    try:
        from src.discovery.framework_scanner import FrameworkScanner
        if _scanner_singleton is None:
            _scanner_singleton = FrameworkScanner(kb=kb, data_dir="data/frameworks")
        else:
            _scanner_singleton._kb = kb
        return _scanner_singleton
    except Exception as exc:  # noqa: BLE001 — discovery is optional enrichment
        logger.debug("_get_scanner_singleton: unavailable (%s)", exc)
        return None


class AdvisorToolsExecutor(BaseToolExecutor):
    """Executes advisor tool calls by name, routing to the appropriate handler."""

    def __init__(
        self,
        knowledge_graph: KnowledgeGraph | None = None,
        graphrag_engine: GraphRAGEngine | None = None,
        knowledge_base: KnowledgeBase | None = None,
        case_study_context: str = "",
        weight_profile: WeightProfile | None = None,
    ) -> None:
        super().__init__(knowledge_base)

        if knowledge_graph is None:
            from src.graph import load_or_seed_graph
            knowledge_graph = load_or_seed_graph(self.kb)
        self.kg = knowledge_graph

        self.graphrag = graphrag_engine
        self.case_study = case_study_context
        self.weight_profile = weight_profile or WeightProfile.default()

        self.tools: dict[str, Callable] = {
            "search_knowledge_graph": self._search_knowledge_graph,
            "get_framework_details": self._get_framework_details,
            "run_framework_comparison": self._run_framework_comparison,
            "recommend_frameworks": self._recommend_frameworks,
            "find_migration_paths": self._find_migration_paths,
            "score_frameworks": self._score_frameworks,
            "list_frameworks_by_category": self._list_frameworks_by_category,
        }

    # ── Dispatch (execute() inherited from BaseToolExecutor) ──────────

    # ── Helpers ───────────────────────────────────────────────────────

    def _resolve_framework(self, name: str) -> Any | None:
        return resolve_framework(self.kb, name)

    def _find_case_study_relevance(self, subject_terms: list[str], max_excerpts: int = 3) -> str:
        return find_case_study_relevance(self.case_study, subject_terms, max_excerpts)

    def _discovery_nudge(self, use_case_text: str, category_hint: str | None = None) -> str:
        """Best-effort "are we missing something?" footer for recommendation/
        comparison output.

        Always mentions how fresh the 19-framework knowledge base is, and —
        for a use case/category that maps to a known category — runs ONE
        scoped, short-timeout, cached GitHub search
        (FrameworkScanner.scan_category) to check for a newer/higher-starred
        option not yet in the KB. Never raises and never blocks long: any
        failure (rate limit, timeout, network) just means this footer is
        skipped, the recommendation itself is unaffected.

        `category_hint` lets a caller that already knows the relevant
        FRAMEWORK_CATEGORIES id (e.g. _run_framework_comparison, which can
        classify the frameworks being compared directly) skip the free-text
        keyword guess, which is unreliable against bare framework names.
        """
        try:
            from src.discovery.framework_scanner import guess_category_from_text
            from src.knowledge_base.schema import FRAMEWORK_CATEGORIES

            scanner = _get_scanner_singleton(self.kb)
            age_days = scanner.kb_age_days() if scanner else None
            age_note = f" (last refreshed {age_days}d ago)" if age_days is not None else ""

            category = category_hint or guess_category_from_text(use_case_text)
            if not category or not scanner:
                return (
                    f"\n*Ranked against the {len(self.kb.list_all())} frameworks "
                    f"currently in the knowledge base{age_note}.*"
                )

            discovered = scanner.scan_category(category)
            if not discovered:
                return (
                    f"\n*Ranked against the {len(self.kb.list_all())} frameworks "
                    f"currently in the knowledge base{age_note} — a live scan of "
                    f"{FRAMEWORK_CATEGORIES[category]['label']} tools on GitHub found "
                    f"nothing newer worth adding right now.*"
                )

            top = sorted(discovered, key=lambda f: -f.stars)[:3]
            bullets = "\n".join(
                f"  - **{fw.name}** (⭐{fw.stars}, {fw.language or 'unknown lang'}) — {fw.repo_url}"
                for fw in top
            )
            return (
                f"\n**💡 Not in the comparison above — found via a live GitHub scan "
                f"({FRAMEWORK_CATEGORIES[category]['label']}):**\n{bullets}\n"
                f"Ask me to \"add <name> to the knowledge base\" to research and "
                f"include it in a future comparison."
            )
        except Exception as exc:  # noqa: BLE001 — this footer must never break a recommendation
            logger.debug("_discovery_nudge: skipped (%s)", exc)
            return ""

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
          2. Take top matches.
          3. Render: category, languages, capabilities, architecture fit,
             version info, case-study relevance.
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
            caps = active_capabilities(fw.capabilities, limit=5)
            lines += [
                f"### {fw.framework_name}",
                f"- **Category:** {fw.category}",
                f"- **Languages:** {', '.join(fw.languages_supported)}",
                f"- **Version:** {format_version_info(fw)}",
                f"- **Key capabilities:** {', '.join(caps)}",
                f"- **Architecture Fit:** {format_architecture_fit(fw.architecture_fit)}",
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
          5. CI/CD, cloud grids, performance, maintainability.
          6. Limitations.
          7. Case-study relevance.
        """
        fw = self.kb.get(framework_name.lower())
        if not fw:
            return f"Framework '{framework_name}' not found in knowledge base."

        lines = [
            f"## {fw.framework_name}",
            f"**Vendor / Version:** {format_version_info(fw)}",
        ]

        lines += [
            f"**Languages:** {', '.join(fw.languages_supported)}",
            f"**License:** {fw.license}",
            f"**Architecture Fit:** {format_architecture_fit(fw.architecture_fit)}",
            "",
            "**Key Capabilities:**",
        ]

        for k, v in positive_items(fw.capabilities):
            lines.append(f"  - {k.replace('_', ' ').title()}: {v}")

        cicd = format_cicd(fw.cicd_integration)
        if cicd:
            lines.append(f"\n**CI/CD Integration:** {cicd}")

        cloud_grids = format_cloud_grids(fw.cloud_grids)
        if cloud_grids:
            lines.append(f"\n**Cloud Grids:** {cloud_grids}")

        performance = format_performance(fw.performance)
        if performance:
            lines.append(f"\n**Performance:** {performance}")

        maintainability = format_maintainability(fw.maintainability)
        if maintainability:
            lines.append("\n**Maintainability:**")
            lines.append(maintainability)

        if fw.limitations:
            lines.append("\n**Limitations:**")
            for lim in fw.limitations[:5]:
                lines.append(f"  - {lim}")

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
          4. CI/CD, cloud grids, performance, maintainability.
          5. Limitations (from fw.limitations, not from capabilities).
          6. Case-study relevance for this specific framework.
        """
        from collections import Counter

        from src.knowledge_base.schema import classify_framework_data

        lines = [f"## Framework Comparison: {' vs '.join(frameworks)}\n"]
        resolved_categories: list[str] = []

        for name in frameworks:
            fw = self.kb.get(name.lower())
            if not fw:
                lines.append(f"- **{name}**: not found in knowledge base")
                continue

            lines.append(f"### {fw.framework_name}")

            caps = active_capabilities(fw.capabilities)
            lines += [
                f"- **Languages:** {', '.join(fw.languages_supported)}",
                f"- **License:** {fw.license}",
                f"- **Architecture Fit:** {format_architecture_fit(fw.architecture_fit)}",
                f"- **Capabilities:** {', '.join(caps[:5])}",
            ]

            cicd = format_cicd(fw.cicd_integration)
            if cicd:
                lines.append(f"- **CI/CD Integration:** {cicd}")

            cloud_grids = format_cloud_grids(fw.cloud_grids)
            if cloud_grids:
                lines.append(f"- **Cloud Grids:** {cloud_grids}")

            performance = format_performance(fw.performance)
            if performance:
                lines.append(f"- **Performance:** {performance}")

            maintainability = format_maintainability(fw.maintainability)
            if maintainability:
                lines.append("- **Maintainability:**")
                lines.append(maintainability)

            if fw.limitations:
                lines.append(f"- **Limitations:** {', '.join(fw.limitations[:3])}")

            note = self._find_case_study_relevance([fw.framework_name])
            if note:
                lines.append(note)

            resolved_categories.extend(classify_framework_data(fw))
            lines.append("")

        # Use the most common category across the compared frameworks as the
        # scoped-scan target — e.g. comparing 3 web UI tools should check for
        # newer web UI tools, not whatever the first framework happened to be.
        # On a count tie, `max(set(...))` would pick an arbitrary category due
        # to set iteration order — break ties deterministically by preferring
        # whichever tied category appears EARLIEST in resolved_categories
        # (i.e. closest to the first compared framework's primary category).
        category_hint: str | None = None
        if resolved_categories:
            counts = Counter(resolved_categories)
            top_count = max(counts.values())
            tied = {cat for cat, n in counts.items() if n == top_count}
            category_hint = next(c for c in resolved_categories if c in tied)
        lines.append(self._discovery_nudge(" ".join(frameworks), category_hint=category_hint))

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
        from src.models import ArchitectureType, ExperienceLevel, UserProfile
        from src.scoring.engine import ScoringEngine

        use_case_lower = use_case.lower()

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
        if not active:
            active = _criteria

        # Order criteria by weight (descending) so the highest-priority
        # criterion appears first everywhere it is shown.
        active = sorted(active, key=lambda pair: -self.weight_profile.get(pair[0]))

        sorted_weights = [(lbl, self.weight_profile.get(cid)) for cid, lbl in active]
        priority_str = " > ".join(f"{lbl}({w:.0%})" for lbl, w in sorted_weights)
        lines = [
            f"## Recommended Frameworks for: {use_case}\n",
            f"**You prioritized:** {priority_str}\n",
        ]

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

        lines += [
            "\n### 📖 Score Guide",
            "| Range | Rating | What it means |",
            "|---|---|---|",
            "| 85–100 | 🟢 Excellent | Best-in-class for this criterion — no gaps |",
            "| 65–84  | 🟡 Good     | Solid support — minor workarounds may be needed |",
            "| 40–64  | 🟠 Fair     | Partial support — plan for extra setup or plugins |",
            "| 0–39   | 🔴 Poor     | Significant gap — consider a complementary tool |",
        ]

        lines.append("\n### 🔍 What Each Score Means For You")
        for r in rankings[:3]:
            lines.append(f"\n**{r.framework}** ({r.overall_score}/100)")
            lines.append(r.explanation)

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

        note = self._find_case_study_relevance(use_case_lower.split())
        if note:
            lines.append(note)

        lines.append(self._discovery_nudge(use_case))

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
          6. Case-study relevance for the from/to framework pair.
        """
        from src.graph import RelationshipType

        if not self.kg:
            return "Knowledge graph not available. Please ensure the graph is loaded."

        sources = self.kg.find_entities_by_name(from_framework)
        if not sources:
            return f"Framework '{from_framework}' not found in knowledge graph."

        source_fw = self.kb.get(from_framework.lower())
        if not source_fw:
            return f"Framework '{from_framework}' not found in knowledge base."

        source_caps = {k for k, v in source_fw.capabilities.items() if v and v is not False}
        source_cicd = {k for k, v in source_fw.cicd_integration.items() if v and v is not False}

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

        seen_targets: dict[str, tuple] = {}
        for rel in paths:
            target = self.kg.get_entity(rel.target_id)
            if not target:
                continue
            if target.id not in seen_targets or rel.confidence > seen_targets[target.id][0]:
                seen_targets[target.id] = (rel.confidence, rel, target)

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

            lines.append("**✅ Covered Capabilities:**")
            if shared_caps:
                lines.append(f"  {', '.join(sorted(c.replace('_', ' ').title() for c in list(shared_caps)[:6]))}")
            if shared_cicd:
                lines.append(f"  CI/CD: {', '.join(sorted(c.replace('_', ' ').title() for c in list(shared_cicd)[:4]))}")
            lines.append("")

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

            if target_fw.limitations:
                lines.append("**⚠️ Target Framework Limitations:**")
                for lim in target_fw.limitations[:3]:
                    lines.append(f"  - {lim}")
                lines.append("")

            note = self._find_case_study_relevance([from_framework, target.name])
            if note:
                lines.append(note)

            lines.append("---\n")

        return "\n".join(lines)

    def _list_frameworks_by_category(self, category: str | None = None) -> str:
        """List frameworks grouped by their functional category using architecture_fit classification."""
        from collections import defaultdict

        from src.knowledge_base.schema import FRAMEWORK_CATEGORIES, classify_framework_data

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

        by_cat: dict[str, list] = defaultdict(list)
        for fw in self.kb.list_all():
            for cat_id in classify_framework_data(fw):
                by_cat[cat_id].append(fw)

        target_cat: str | None = None
        if category:
            cat_lower = category.lower().strip()
            if cat_lower in FRAMEWORK_CATEGORIES:
                target_cat = cat_lower
            else:
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

    def _score_frameworks(
        self,
        use_case: str = "",
        frameworks: list[str] | None = None,
        language: str = "python",
        architecture: str = "web_spa",
    ) -> str:
        """Run ScoringEngine with the active WeightProfile and return a ranked scorecard."""
        from src.models import ArchitectureType, ExperienceLevel, UserProfile
        from src.scoring.engine import ScoringEngine

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
            "## Weighted Framework Scorecard",
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
        for cid, w in sorted(
            self.weight_profile.weights.items(), key=lambda kv: -kv[1]
        ):
            if w > 0:
                lines.append(f"- {_labels.get(cid, cid)}: **{w:.0%}**")

        if rankings:
            lines.append(f"\n### Top Pick: {rankings[0].framework}")
            lines.append(rankings[0].explanation)

        return "\n".join(lines)
