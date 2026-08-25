"""Knowledge graph module for persistent entity and relationship storage.

Provides the core graph data structure, persistence layer, entity extraction,
and GraphRAG context retrieval for grounding LLM responses in structured
framework relationships.
"""

from __future__ import annotations

import logging

from src.graph.knowledge_graph import (
    Entity,
    EntityType,
    KnowledgeGraph,
    Relationship,
    RelationshipType,
    SubGraph,
)

logger = logging.getLogger(__name__)

__all__ = [
    "EntityType",
    "RelationshipType",
    "Entity",
    "Relationship",
    "SubGraph",
    "KnowledgeGraph",
    "seed_knowledge_graph",
    "load_or_seed_graph",
    "update_graph_with_framework",
]

try:
    from src.graph.graph_store import GraphStore
    __all__.append("GraphStore")
except ImportError:
    pass

try:
    from src.graph.entity_extractor import EntityExtractor
    __all__.append("EntityExtractor")
except ImportError:
    pass

try:
    from src.graph.graphrag_engine import GraphRAGEngine
    __all__.append("GraphRAGEngine")
except ImportError:
    pass


def seed_knowledge_graph(kb) -> KnowledgeGraph:
    """Seed a KnowledgeGraph from all YAML framework profiles in a KnowledgeBase.

    Iterates every framework, calls EntityExtractor.extract_from_yaml for each,
    then calls detect_migration_paths across all frameworks to add cross-framework
    MIGRATES_TO relationships.

    Args:
        kb: A loaded KnowledgeBase instance.

    Returns:
        A fully seeded KnowledgeGraph.

    Validates: Requirements 3.3, 5.1, 5.3
    """
    from src.graph.entity_extractor import EntityExtractor

    extractor = EntityExtractor()
    graph = KnowledgeGraph()
    frameworks = kb.list_all()

    # Task 6.1 — extract entities/relationships from each YAML profile
    for fw_data in frameworks:
        entities, relationships = extractor.extract_from_yaml(fw_data)
        for entity in entities:
            graph.add_entity(entity)
        for rel in relationships:
            graph.add_relationship(rel)

    # Task 6.2 — detect cross-framework migration paths
    path_entities, path_rels = extractor.detect_migration_paths(frameworks)
    for entity in path_entities:
        graph.add_entity(entity)
    for rel in path_rels:
        graph.add_relationship(rel)

    logger.info(
        "Knowledge graph seeded: %d entities, %d relationships from %d frameworks",
        graph.entity_count,
        graph.relationship_count,
        len(frameworks),
    )
    return graph


def load_or_seed_graph(kb, store_path: str = "data/knowledge_graph.json") -> KnowledgeGraph:
    """Load graph from store if valid, otherwise seed from YAML and save.

    Validates: Requirements 4.3, 4.4
    """
    from src.graph.graph_store import GraphStore

    store = GraphStore(path=store_path)

    if store.is_valid():
        graph = store.load()
        if graph is not None:
            logger.info("Knowledge graph loaded from %s", store_path)
            return graph
        logger.warning("Graph store invalid or corrupt at %s — re-seeding", store_path)

    graph = seed_knowledge_graph(kb)
    store.save(graph)
    logger.info("Knowledge graph seeded and saved to %s", store_path)
    return graph


def update_graph_with_framework(
    fw_data,
    kb,
    graph: KnowledgeGraph | None = None,
    store_path: str = "data/knowledge_graph.json",
) -> KnowledgeGraph:
    """Incrementally update the knowledge graph with a newly added framework.

    Extracts entities/relationships from the new framework, adds them to the
    existing graph, re-detects migration paths (since a new framework creates
    new cross-framework relationships), and persists.

    Args:
        fw_data: The FrameworkData for the newly added framework.
        kb: The loaded KnowledgeBase (for migration path detection across all frameworks).
        graph: Existing KnowledgeGraph instance. If None, loads from store.
        store_path: Path to the JSON graph file.

    Returns:
        The updated KnowledgeGraph.
    """
    from src.graph.entity_extractor import EntityExtractor
    from src.graph.graph_store import GraphStore

    store = GraphStore(path=store_path)

    # Load existing graph if not provided
    if graph is None:
        if store.is_valid():
            graph = store.load()
        if graph is None:
            # No existing graph — do a full seed
            logger.info("No existing graph found — performing full seed")
            graph = seed_knowledge_graph(kb)
            store.save(graph)
            return graph

    extractor = EntityExtractor()

    # Extract entities/relationships from the new framework
    entities, relationships = extractor.extract_from_yaml(fw_data)
    for entity in entities:
        graph.add_entity(entity)
    for rel in relationships:
        graph.add_relationship(rel)

    logger.info(
        "Added framework '%s' to knowledge graph: %d entities, %d relationships",
        fw_data.framework_name, len(entities), len(relationships),
    )

    # Re-detect migration paths (new framework may have paths to/from existing ones)
    all_frameworks = kb.list_all()
    path_entities, path_rels = extractor.detect_migration_paths(all_frameworks)
    new_paths = 0
    for entity in path_entities:
        graph.add_entity(entity)
    for rel in path_rels:
        graph.add_relationship(rel)
        new_paths += 1

    if new_paths:
        logger.info(
            "Detected %d migration path relationships involving '%s'",
            new_paths, fw_data.framework_name,
        )

    # Persist updated graph
    store.save(graph)
    logger.info(
        "Knowledge graph updated and saved: %d entities, %d relationships total",
        graph.entity_count, graph.relationship_count,
    )
    return graph
