"""Tool executor — runs tool calls requested by the agent pipeline.

Tools available:
  - search_knowledge_graph
  - get_framework_details
  - run_framework_comparison
  - find_migration_paths
  - analyze_test_case_coverage
  - analyze_uploaded_content
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.graph import KnowledgeGraph
    from src.graph.graphrag_engine import GraphRAGEngine
    from src.knowledge_base.loader import KnowledgeBase

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes tool calls by name, routing to the appropriate handler."""

    def __init__(
        self,
        knowledge_graph: KnowledgeGraph,
        graphrag_engine: GraphRAGEngine,
        knowledge_base: KnowledgeBase,
        uploaded_docs_context: str = "",
        case_study_context: str = "",
    ) -> None:
        self.kg = knowledge_graph
        self.graphrag = graphrag_engine
        self.kb = knowledge_base
        self.uploaded_docs = uploaded_docs_context
        self.case_study = case_study_context

        self.tools: dict[str, Callable] = {
            "search_knowledge_graph": self._search_knowledge_graph,
            "get_framework_details": self._get_framework_details,
            "run_framework_comparison": self._run_framework_comparison,
            "recommend_frameworks": self._recommend_frameworks,
            "find_migration_paths": self._find_migration_paths,
            "analyze_test_case_coverage": self._analyze_test_case_coverage,
            "analyze_uploaded_content": self._analyze_uploaded_content,
            "analyze_version_compatibility": self._analyze_version_compatibility,
        }

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

    # ── Tool implementations ──────────────────────────────────────────

    def _search_knowledge_graph(self, query: str, entity_types: list[str] | None = None, **_kwargs) -> str:
        """Search KB for frameworks matching the query terms — returns structured data, not raw edges."""
        query_lower = query.lower()
        all_frameworks = self.kb.list_all()

        # Score each framework by how many query words appear in its name/capabilities/category
        query_tokens = set(t for t in query_lower.split() if len(t) > 3)
        scored: list[tuple[int, object]] = []
        for fw in all_frameworks:
            score = 0
            fw_text = " ".join([
                fw.framework_name.lower(),
                fw.category.lower() if fw.category else "",
                " ".join(fw.languages_supported).lower(),
                " ".join(k for k, v in fw.capabilities.items() if v and v is not False),
            ])
            for token in query_tokens:
                if token in fw_text:
                    score += 1
            if score > 0:
                scored.append((score, fw))

        scored.sort(key=lambda x: -x[0])
        top = [fw for _, fw in scored[:5]]

        if not top:
            return f"No frameworks found matching: {query}"

        lines = [f"## Frameworks matching '{query}'\n"]
        for fw in top:
            caps = [k.replace("_", " ").title() for k, v in fw.capabilities.items()
                    if v is True or (isinstance(v, str) and v not in ("", "false"))][:5]
            lines += [
                f"### {fw.framework_name}",
                f"- **Category:** {fw.category}",
                f"- **Languages:** {', '.join(fw.languages_supported)}",
                f"- **Key capabilities:** {', '.join(caps)}",
                "",
            ]
        return "\n".join(lines)

    def _get_framework_details(self, framework_name: str) -> str:
        fw = self.kb.get(framework_name.lower())
        if not fw:
            return f"Framework '{framework_name}' not found in knowledge base."
        lines = [
            f"## {fw.framework_name}",
            f"**Languages:** {', '.join(fw.languages_supported)}",
            f"**License:** {fw.license}",
            f"**Architecture Fit:** {', '.join(k for k, v in fw.architecture_fit.items() if v is True)}",
            "",
            "**Key Capabilities:**",
        ]
        for k, v in fw.capabilities.items():
            if v is True or (isinstance(v, str) and v not in ("", "false")):
                lines.append(f"  - {k.replace('_', ' ').title()}: {v}")
        if fw.limitations:
            lines.append("\n**Limitations:**")
            for lim in fw.limitations[:5]:
                lines.append(f"  - {lim}")
        return "\n".join(lines)

    def _recommend_frameworks(
        self, use_case: str, required_capability: str | None = None
    ) -> str:
        """Return all frameworks that support the requested capability, ranked by fit."""
        all_frameworks = self.kb.list_all()
        use_case_lower = use_case.lower()

        # Capability keywords to check in framework data
        cap_keywords: list[str] = []
        if required_capability:
            cap_keywords.append(required_capability)
        # Also infer from use_case text
        _infer = {
            "api": ["api_testing", "rest_api"],
            "microservice": ["api_testing", "rest_api", "contract_testing"],
            "web": ["browser_testing", "ui_testing", "cross_browser"],
            "mobile": ["mobile_testing", "cross_platform"],
            "performance": ["performance_testing", "load_testing"],
            "load": ["load_testing", "performance_testing"],
            "graphql": ["graphql_support"],
        }
        for kw, caps in _infer.items():
            if kw in use_case_lower:
                cap_keywords.extend(caps)

        matched: list[tuple[int, object]] = []
        for fw in all_frameworks:
            if not cap_keywords:
                matched.append((1, fw))
                continue
            score = sum(
                1 for cap in cap_keywords
                if fw.capabilities.get(cap) is True
                or (isinstance(fw.capabilities.get(cap), str)
                    and fw.capabilities.get(cap) not in ("", "false", "no"))
            )
            if score > 0:
                matched.append((score, fw))

        matched.sort(key=lambda x: -x[0])

        if not matched:
            # Fall back: return all frameworks with their categories
            matched = [(0, fw) for fw in all_frameworks]

        lines = [f"## Recommended Frameworks for: {use_case}\n",
                 "| Framework | Category | Languages | Key Capabilities |",
                 "|---|---|---|---|"]
        for score, fw in matched[:8]:
            caps = [k.replace("_", " ").title() for k, v in fw.capabilities.items()
                    if v is True or (isinstance(v, str) and v not in ("", "false"))][:4]
            lines.append(
                f"| **{fw.framework_name}** | {fw.category} "
                f"| {', '.join(fw.languages_supported[:3])} "
                f"| {', '.join(caps)} |"
            )
        return "\n".join(lines)

    def _run_framework_comparison(
        self, frameworks: list[str], focus_criteria: list[str] | None = None
    ) -> str:
        lines = [f"## Framework Comparison: {' vs '.join(frameworks)}\n"]
        for name in frameworks:
            fw = self.kb.get(name.lower())
            if not fw:
                lines.append(f"- **{name}**: not found in knowledge base")
                continue
            caps = [
                k.replace("_", " ").title()
                for k, v in fw.capabilities.items()
                if v is True or (isinstance(v, str) and v not in ("", "false"))
            ]
            lines += [
                f"### {fw.framework_name}",
                f"- **Languages:** {', '.join(fw.languages_supported)}",
                f"- **License:** {fw.license}",
                f"- **Capabilities:** {', '.join(caps[:5])}",
                "",
            ]
        return "\n".join(lines)

    def _find_migration_paths(
        self, from_framework: str, to_frameworks: list[str] | None = None
    ) -> str:
        from src.graph import RelationshipType

        sources = self.kg.find_entities_by_name(from_framework)
        if not sources:
            return f"Framework '{from_framework}' not found in knowledge graph."

        rels = self.kg.get_relationships(sources[0].id, hops=2)
        paths = [r for r in rels if r.relationship_type == RelationshipType.MIGRATES_TO]
        if not paths:
            return f"No migration paths found from '{from_framework}'."

        lines = [f"## Migration Paths from {from_framework}\n"]
        for rel in paths:
            target = self.kg.get_entity(rel.target_id)
            if target:
                lines.append(f"- **{target.name}** (confidence: {int(rel.confidence * 100)}%)")
                for k, v in (rel.properties or {}).items():
                    lines.append(f"  - {k}: {v}")
        return "\n".join(lines)

    def _analyze_test_case_coverage(
        self, test_cases: list[dict], frameworks: list[str] | None = None
    ) -> str:
        capability_map = {
            "ui automation": ["ui_testing", "browser_testing", "web_automation"],
            "api testing": ["api_testing", "rest_api", "graphql_support"],
            "performance testing": ["performance_testing", "load_testing"],
            "load testing": ["load_testing", "performance_testing"],
            "mobile testing": ["mobile_testing", "cross_platform"],
        }
        fw_names = frameworks or [fw.framework_name for fw in self.kb.list_all()]

        matrix: dict[str, dict] = {}
        for tc in test_cases:
            tc_id, cap = tc["id"], tc["required_capability"].lower()
            keywords = capability_map.get(cap, [cap.replace(" ", "_")])
            matrix[tc_id] = {"description": tc["description"], "capability": tc["required_capability"], "support": {}}
            for name in fw_names:
                fw = self.kb.get(name.lower())
                if not fw:
                    continue
                supported = any(
                    fw.capabilities.get(kw) is True
                    or (isinstance(fw.capabilities.get(kw), str) and fw.capabilities.get(kw) not in ("", "false", "no"))
                    for kw in keywords
                )
                matrix[tc_id]["support"][fw.framework_name] = supported

        # Summary table
        coverage: dict[str, int] = {n: 0 for n in fw_names}
        for tc_data in matrix.values():
            for fw_name, ok in tc_data["support"].items():
                if ok:
                    coverage[fw_name] = coverage.get(fw_name, 0) + 1

        total = len(test_cases)
        lines = ["# Test Case Coverage Analysis\n", "| Framework | Covered | Total | % |", "|---|---|---|---|"]
        for name, count in sorted(coverage.items(), key=lambda x: -x[1]):
            pct = count / total * 100 if total else 0
            lines.append(f"| {name} | {count} | {total} | {pct:.0f}% |")
        return "\n".join(lines)

    # ── Helpers ─────────────────────────────────────────────────────────

    def _resolve_framework(self, name: str):
        """Resolve a framework name with fuzzy matching (handles 'Selenium' → 'Selenium WebDriver')."""
        # Exact match first
        fw = self.kb.get(name.lower())
        if fw:
            return fw
        # Partial match: check if the input is a substring of any framework name
        name_lower = name.lower()
        for candidate in self.kb.list_all():
            if name_lower in candidate.framework_name.lower():
                return candidate
        # Reverse: check if any framework name is a substring of the input
        for candidate in self.kb.list_all():
            if candidate.framework_name.lower() in name_lower:
                return candidate
        return None

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

    def _analyze_version_compatibility(
        self,
        framework_name: str,
        target_version: str = "",
        current_version: str = "",
        test_apis: list[str] | None = None,
    ) -> str:
        """Analyze version-specific compatibility for test cases.

        Args:
            framework_name: Framework to analyze (e.g. "Playwright", "Selenium")
            target_version: Version to migrate TO (e.g. "4.0"). If empty, uses latest.
            current_version: Version migrating FROM (e.g. "3.141"). If empty, shows all.
            test_apis: List of API patterns used in test cases to check against breaking changes.
        """
        fw = self._resolve_framework(framework_name)
        if not fw:
            available = ", ".join(self.kb.list_names())
            return f"Framework '{framework_name}' not found in knowledge base. Available: {available}"

        if not fw.versions:
            return f"No version data available for '{framework_name}'. Version analysis not supported."

        # Sort versions by release date (newest first)
        sorted_versions = sorted(
            fw.versions, key=lambda v: v.release_date or "", reverse=True
        )

        # Resolve target version
        target_ver = None
        if target_version:
            target_ver = next((v for v in sorted_versions if v.version == target_version), None)
            if not target_ver:
                available = ", ".join(v.version for v in sorted_versions)
                return f"Version '{target_version}' not found for {framework_name}. Available: {available}"
        else:
            target_ver = sorted_versions[0]  # latest

        # Resolve current version
        current_ver = None
        if current_version:
            current_ver = next((v for v in sorted_versions if v.version == current_version), None)

        lines = [f"# Version Compatibility Analysis: {fw.framework_name}\n"]

        # ── Target version summary ────────────────────────────────────
        lines += [
            f"## Target Version: {target_ver.version}",
            f"- **Release date:** {target_ver.release_date or 'unknown'}",
            f"- **Min runtime:** {target_ver.min_runtime or 'not specified'}",
            f"- **EOL:** {target_ver.eol_date or 'still supported'}",
            "",
        ]

        # ── Breaking changes between current → target ─────────────────
        if current_ver:
            # Collect all breaking changes in versions AFTER current up to target
            current_date = current_ver.release_date or ""
            target_date = target_ver.release_date or ""
            upgrade_versions = [
                v for v in sorted_versions
                if (v.release_date or "") > current_date
                and (v.release_date or "") <= target_date
            ]

            if upgrade_versions:
                lines.append(f"## Breaking Changes ({current_ver.version} → {target_ver.version})\n")
                total_breaking = 0
                for v in sorted(upgrade_versions, key=lambda x: x.release_date or ""):
                    if v.breaking_changes:
                        lines.append(f"### Introduced in {v.version}")
                        for bc in v.breaking_changes:
                            total_breaking += 1
                            lines.append(f"- **[{bc.migration_effort.upper()}]** {bc.description}")
                            if bc.affected_apis:
                                lines.append(f"  - Affected APIs: `{', '.join(bc.affected_apis)}`")
                            if bc.workaround:
                                lines.append(f"  - Fix: {bc.workaround}")
                        lines.append("")

                if total_breaking == 0:
                    lines.append("No breaking changes in this upgrade path.\n")
            else:
                lines.append(f"## No intermediate versions between {current_ver.version} → {target_ver.version}\n")

            # ── Deprecated APIs in the path ───────────────────────────
            deprecated_in_path = []
            for v in upgrade_versions:
                for cap in v.capabilities_deprecated:
                    deprecated_in_path.append((v.version, cap))

            if deprecated_in_path:
                lines.append("## Deprecated APIs in Upgrade Path\n")
                lines.append("| Deprecated In | API | Status | Replacement |")
                lines.append("|---|---|---|---|")
                for ver_str, cap in deprecated_in_path:
                    lines.append(f"| {ver_str} | {cap.name} | {cap.status} | {cap.replacement or 'none'} |")
                lines.append("")

        # ── Test API compatibility check ──────────────────────────────
        if test_apis:
            lines.append("## Test API Compatibility Check\n")
            lines.append("| API Pattern | Status | Details |")
            lines.append("|---|---|---|")

            # Gather all affected APIs from breaking changes across all versions up to target
            all_breaking_apis: dict[str, dict] = {}
            for v in sorted_versions:
                if target_ver.release_date and (v.release_date or "") > (target_ver.release_date or ""):
                    continue  # skip versions after target
                for bc in v.breaking_changes:
                    for api in bc.affected_apis:
                        all_breaking_apis[api.lower()] = {
                            "version": v.version,
                            "effort": bc.migration_effort,
                            "workaround": bc.workaround,
                        }

            # Also gather deprecated capabilities
            all_deprecated: dict[str, dict] = {}
            for v in sorted_versions:
                if target_ver.release_date and (v.release_date or "") > (target_ver.release_date or ""):
                    continue
                for cap in v.capabilities_deprecated:
                    all_deprecated[cap.name.lower()] = {
                        "version": v.version,
                        "status": cap.status,
                        "replacement": cap.replacement,
                    }

            for api in test_apis:
                api_lower = api.lower()
                # Check against breaking changes
                matched_breaking = None
                for broken_api, info in all_breaking_apis.items():
                    if api_lower in broken_api or broken_api in api_lower:
                        matched_breaking = info
                        break

                if matched_breaking:
                    lines.append(
                        f"| `{api}` | BROKEN (v{matched_breaking['version']}) | "
                        f"Effort: {matched_breaking['effort']}. {matched_breaking['workaround']} |"
                    )
                    continue

                # Check against deprecated
                matched_deprecated = None
                for dep_name, info in all_deprecated.items():
                    if api_lower in dep_name or dep_name in api_lower:
                        matched_deprecated = info
                        break

                if matched_deprecated:
                    lines.append(
                        f"| `{api}` | {matched_deprecated['status'].upper()} (v{matched_deprecated['version']}) | "
                        f"Replace with: {matched_deprecated['replacement'] or 'TBD'} |"
                    )
                else:
                    lines.append(f"| `{api}` | COMPATIBLE | No known issues in {target_ver.version} |")

            lines.append("")

        # ── New capabilities available in target ──────────────────────
        if target_ver.capabilities_added:
            lines.append("## New Capabilities Available\n")
            for cap in target_ver.capabilities_added:
                lines.append(f"- **{cap.name.replace('_', ' ').title()}**: {cap.description}")
            lines.append("")

        # ── Known limitations of target version ───────────────────────
        if target_ver.known_limitations:
            lines.append("## Known Limitations\n")
            for lim in target_ver.known_limitations:
                lines.append(f"- {lim}")

        return "\n".join(lines)
