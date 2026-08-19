"""GraphRAG engine for context retrieval from the knowledge graph.

Identifies entities in a query via fuzzy matching, retrieves a 2-hop
subgraph, and formats it as structured triples for LLM prompts.

Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.graph.knowledge_graph import KnowledgeGraph, SubGraph

if TYPE_CHECKING:
    from src.knowledge_base.loader import KnowledgeBase

logger = logging.getLogger(__name__)

_DEFAULT_MAX_HOPS = 2
_MIN_FUZZY_SCORE = 0.6    # lowered from 0.75 — catches partial name tokens
_MIN_TOKEN_LEN = 4        # lowered from 5 — catches 'robot', 'k6', etc.
_MAX_MATCHED_ENTITIES = 20  # cap subgraph size to keep context small


def _overlap_ratio(query_token: str, candidate: str) -> float:
    q, c = query_token.lower(), candidate.lower()
    if q in c or c in q:
        return 1.0
    shorter, longer = (q, c) if len(q) <= len(c) else (c, q)
    matches = sum(1 for ch in shorter if ch in longer)
    return matches / max(len(longer), 1)


class GraphRAGEngine:
    """Retrieves structured graph context to ground LLM responses.

    Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5
    """

    def __init__(
        self,
        graph: KnowledgeGraph,
        knowledge_base: KnowledgeBase | None = None,
        max_hops: int = _DEFAULT_MAX_HOPS,
    ) -> None:
        self._graph = graph
        self._kb = knowledge_base
        self._max_hops = max_hops
        logger.debug(
            "GraphRAGEngine initialized: entities=%d relationships=%d max_hops=%d",
            graph.entity_count, graph.relationship_count, max_hops,
        )

    def retrieve_context(self, query: str) -> str:
        """Identify entities in query, retrieve subgraph, return formatted triples.

        Validates: Requirements 7.2, 7.3, 7.5
        """
        logger.info("GraphRAGEngine.retrieve_context: START query='%.80s'", query)

        entity_ids = self._identify_entities(query)
        logger.info("GraphRAGEngine.retrieve_context: matched %d entities", len(entity_ids))

        if not entity_ids:
            logger.info("GraphRAGEngine.retrieve_context: no entities matched — using YAML fallback")
            return self._yaml_fallback(query)

        subgraph = self._graph.get_subgraph(entity_ids, max_hops=self._max_hops)
        logger.info("GraphRAGEngine.retrieve_context: subgraph entities=%d relationships=%d",
                    len(subgraph.entities), len(subgraph.relationships))

        if not subgraph.relationships:
            logger.info("GraphRAGEngine.retrieve_context: empty subgraph — using YAML fallback")
            return self._yaml_fallback(query)

        context = self._format_subgraph(subgraph)
        logger.info("GraphRAGEngine.retrieve_context: DONE context_len=%d", len(context))
        return context

    def _identify_entities(self, query: str) -> list[str]:
        """Tokenize query and fuzzy-match tokens against the name index.

        Also tries multi-word combinations (e.g. 'robot framework') so
        compound names are matched even when the index key is the full name.

        Validates: Requirements 7.1
        """
        tokens = [
            t.strip(".,!?;:\"'()")
            for t in query.split()
            if len(t) >= _MIN_TOKEN_LEN
        ]
        # Also try bigrams so 'robot framework' matches as one token
        words = query.lower().split()
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)]
        all_tokens = tokens + bigrams
        logger.info("GraphRAGEngine._identify_entities: tokens=%s", all_tokens)

        matched_ids: list[str] = []
        seen: set[str] = set()

        # Exact match first
        for token in all_tokens:
            for e in self._graph.find_entities_by_name(token):
                if e.id not in seen:
                    seen.add(e.id)
                    matched_ids.append(e.id)
                    logger.debug("GraphRAGEngine._identify_entities: exact token='%s' -> '%s'", token, e.name)

        # Fuzzy match only for tokens that produced no exact hits
        for token in all_tokens:
            if len(matched_ids) >= _MAX_MATCHED_ENTITIES:
                break
            for name_key, entity_ids in self._graph._name_index.items():
                score = _overlap_ratio(token, name_key)
                if score >= _MIN_FUZZY_SCORE:
                    for eid in entity_ids:
                        if eid not in seen and len(matched_ids) < _MAX_MATCHED_ENTITIES:
                            seen.add(eid)
                            matched_ids.append(eid)
                            logger.debug(
                                "GraphRAGEngine._identify_entities: fuzzy token='%s' -> '%s' score=%.2f",
                                token, name_key, score,
                            )

        logger.info("GraphRAGEngine._identify_entities: total matched=%d", len(matched_ids))
        return matched_ids

    def _format_subgraph(self, subgraph: SubGraph) -> str:
        entity_map = {e.id: e.name for e in subgraph.entities}
        fw_entities = {e.id: e for e in subgraph.entities if e.entity_type.value == "Framework"}
        lines: list[str] = ["Knowledge Graph Context:"]

        # Enrich Framework entities with full KB data so the LLM has real content
        if fw_entities and self._kb:
            for eid, entity in fw_entities.items():
                fw = self._kb.get(entity.name.lower())
                if not fw:
                    continue
                caps = [
                    k.replace("_", " ").title()
                    for k, v in fw.capabilities.items()
                    if v is True or (isinstance(v, str) and v not in ("", "false"))
                ][:8]
                cicd = [k for k, v in fw.cicd_integration.items() if v and v is not False][:4]
                lines += [
                    f"\n[{fw.framework_name}]",
                    f"  category={fw.category}, license={fw.license}",
                    f"  languages={', '.join(fw.languages_supported)}",
                    f"  capabilities={', '.join(caps)}",
                    f"  cicd={', '.join(cicd)}",
                    f"  limitations={'; '.join(fw.limitations[:3])}",
                ]

        # Graph relationship triples
        lines.append("\nRelationships:")
        for rel in subgraph.relationships:
            src_name = entity_map.get(rel.source_id, rel.source_id)
            tgt_name = entity_map.get(rel.target_id, rel.target_id)
            lines.append(
                f"  [{src_name}] --{rel.relationship_type.value}--> "
                f"[{tgt_name}] (confidence: {rel.confidence:.1f})"
            )

        return "\n".join(lines)

    def _yaml_fallback(self, query: str) -> str:
        """Return relevant YAML knowledge base data when no graph entities found.

        Validates: Requirements 7.4
        """
        if self._kb is None:
            logger.debug("GraphRAGEngine._yaml_fallback: no knowledge_base configured")
            return ""

        query_lower = query.lower()
        lines: list[str] = ["Knowledge Base Context (no graph entities found):"]

        for fw in self._kb.list_all():
            name = fw.framework_name.lower()
            if name in query_lower or any(
                token in name for token in query_lower.split() if len(token) >= 4
            ):
                caps = [
                    k.replace("_", " ").title()
                    for k, v in fw.capabilities.items()
                    if v is True or (isinstance(v, str) and v not in ("", "false"))
                ][:6]
                lines += [
                    f"\n[{fw.framework_name}]",
                    f"  languages={', '.join(fw.languages_supported)}",
                    f"  capabilities={', '.join(caps)}",
                    f"  limitations={'; '.join(fw.limitations[:3])}",
                ]

        result = "\n".join(lines) if len(lines) > 1 else ""
        logger.info("GraphRAGEngine._yaml_fallback: matched %d frameworks context_len=%d", len(lines) - 1, len(result))
        return result
