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
