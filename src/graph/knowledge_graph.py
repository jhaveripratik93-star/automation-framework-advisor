"""Knowledge graph data structure with typed entities and relationships.

Provides the in-memory graph representation used by the GraphRAG engine
and entity extractor for storing and querying automation framework
entities and their relationships.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EntityType(str, Enum):
    """Types of entities stored in the knowledge graph.

    Validates: Requirements 3.1
    """

    FRAMEWORK = "Framework"
    CAPABILITY = "Capability"
    LANGUAGE = "Language"
    CI_CD_TOOL = "CI_CD_Tool"
    CLOUD_PROVIDER = "Cloud_Provider"
    MIGRATION_PATH = "Migration_Path"
    USER_EXPERIENCE = "User_Experience"
    LIMITATION = "Limitation"


class RelationshipType(str, Enum):
    """Types of relationships between entities in the knowledge graph.

    Validates: Requirements 3.2
    """

    SUPPORTS_LANGUAGE = "SUPPORTS_LANGUAGE"
    HAS_CAPABILITY = "HAS_CAPABILITY"
    INTEGRATES_WITH = "INTEGRATES_WITH"
    MIGRATES_TO = "MIGRATES_TO"
    COMPATIBLE_WITH = "COMPATIBLE_WITH"
    REQUIRES = "REQUIRES"
    REPORTED_BY_USER = "REPORTED_BY_USER"


@dataclass
class Entity:
    """A node in the knowledge graph representing an automation domain concept.

    Entities are uniquely identified by a slugified combination of their name
    and type. They can originate from YAML framework profiles or user interactions.

    Validates: Requirements 3.1, 3.4
    """

    id: str
    """Unique identifier (slugified name + type)."""

    name: str
    """Human-readable name of the entity."""

    entity_type: EntityType
    """Category of the entity (Framework, Capability, Language, etc.)."""

    properties: dict[str, Any] = field(default_factory=dict)
    """Flexible key-value properties for additional metadata."""

    source: str = "yaml"
    """Origin of the entity: 'yaml' for framework profiles, 'user' for interactions."""


@dataclass
class Relationship:
    """A directed edge in the knowledge graph connecting two entities.

    Relationships carry a confidence score indicating how certain the link is.
    YAML-sourced relationships receive confidence 1.0; user-sourced relationships
    receive 0.5–0.8 depending on specificity.

    Validates: Requirements 3.2, 3.4
    """

    source_id: str
    """ID of the source entity."""

    target_id: str
    """ID of the target entity."""

    relationship_type: RelationshipType
    """Type of the relationship (SUPPORTS_LANGUAGE, HAS_CAPABILITY, etc.)."""

    confidence: float = 1.0
    """Confidence score from 0.0 to 1.0. YAML-sourced = 1.0, user-sourced = 0.5–0.8."""

    source: str = "yaml"
    """Origin of the relationship: 'yaml' or 'user'."""

    properties: dict[str, Any] = field(default_factory=dict)
    """Additional metadata (e.g., migration duration, version info)."""


@dataclass
class SubGraph:
    """A subset of the knowledge graph containing related entities and relationships.

    Used by the GraphRAG engine to pass focused context to the LLM.
    """

    entities: list[Entity] = field(default_factory=list)
    """Entities within the subgraph."""

    relationships: list[Relationship] = field(default_factory=list)
    """Relationships between included entities."""


class KnowledgeGraph:
    """In-memory graph data structure with typed entities and relationships.

    Stores entities in a dict keyed by ID, relationships in an adjacency list
    keyed by source_id, and maintains a reverse name index for efficient
    case-insensitive name lookups.

    Validates: Requirements 3.1, 3.2, 3.4, 7.1, 7.2
    """

    def __init__(self) -> None:
        """Initialize an empty knowledge graph."""
        self._entities: dict[str, Entity] = {}
        self._adjacency: dict[str, list[Relationship]] = {}
        self._name_index: dict[str, list[str]] = {}

    def add_entity(self, entity: Entity) -> None:
        """Add an entity to the graph and update the name index.

        If an entity with the same ID already exists, it is overwritten
        and the name index is updated accordingly.

        Args:
            entity: The entity to add.
        """
        # If replacing an existing entity, remove old name index entry
        if entity.id in self._entities:
            old_entity = self._entities[entity.id]
            old_key = old_entity.name.lower()
            if old_key in self._name_index:
                self._name_index[old_key] = [
                    eid for eid in self._name_index[old_key] if eid != entity.id
                ]
                if not self._name_index[old_key]:
                    del self._name_index[old_key]

        self._entities[entity.id] = entity

        # Update reverse name index
        name_key = entity.name.lower()
        if name_key not in self._name_index:
            self._name_index[name_key] = []
        if entity.id not in self._name_index[name_key]:
            self._name_index[name_key].append(entity.id)

    def add_relationship(self, relationship: Relationship) -> None:
        """Add a relationship to the adjacency list keyed by source_id.

        Args:
            relationship: The relationship to add.
        """
        if relationship.source_id not in self._adjacency:
            self._adjacency[relationship.source_id] = []
        self._adjacency[relationship.source_id].append(relationship)

    def get_entity(self, entity_id: str) -> Entity | None:
        """Retrieve an entity by its ID.

        Args:
            entity_id: The unique identifier of the entity.

        Returns:
            The entity if found, None otherwise.
        """
        return self._entities.get(entity_id)

    def get_relationships(self, entity_id: str, hops: int = 1) -> list[Relationship]:
        """Get all relationships reachable from an entity within N hops using BFS.

        Traverses the graph starting from the given entity, collecting all
        relationships encountered up to the specified hop depth.

        Args:
            entity_id: The starting entity ID.
            hops: Maximum number of hops to traverse (default 1).

        Returns:
            List of all relationships found within the hop distance.
        """
        if hops < 1:
            return []

        collected: list[Relationship] = []
        visited: set[str] = set()
        # BFS queue: (entity_id, current_depth)
        queue: list[tuple[str, int]] = [(entity_id, 0)]
        visited.add(entity_id)

        while queue:
            current_id, depth = queue.pop(0)
            if depth >= hops:
                continue

            for rel in self._adjacency.get(current_id, []):
                collected.append(rel)
                if rel.target_id not in visited:
                    visited.add(rel.target_id)
                    queue.append((rel.target_id, depth + 1))

        return collected

    def find_entities_by_name(self, name: str) -> list[Entity]:
        """Find entities by name using case-insensitive lookup via the reverse index.

        Args:
            name: The entity name to search for (case-insensitive).

        Returns:
            List of entities matching the given name.

        Validates: Requirements 7.1
        """
        name_key = name.lower()
        entity_ids = self._name_index.get(name_key, [])
        return [self._entities[eid] for eid in entity_ids if eid in self._entities]

    def find_entities_by_type(self, entity_type: EntityType) -> list[Entity]:
        """Find all entities of a given type.

        Args:
            entity_type: The entity type to filter by.

        Returns:
            List of entities matching the given type.
        """
        return [e for e in self._entities.values() if e.entity_type == entity_type]

    def get_subgraph(self, entity_ids: list[str], max_hops: int = 2) -> SubGraph:
        """Extract a subgraph by BFS from seed entities up to max_hops.

        Collects all entities and relationships reachable from the seed entities
        within the specified hop distance.

        Args:
            entity_ids: Seed entity IDs to start the BFS from.
            max_hops: Maximum number of hops to traverse (default 2).

        Returns:
            A SubGraph containing the discovered entities and relationships.

        Validates: Requirements 7.2
        """
        collected_entities: dict[str, Entity] = {}
        collected_relationships: list[Relationship] = []
        visited: set[str] = set()
        # BFS queue: (entity_id, current_depth)
        queue: list[tuple[str, int]] = []

        # Initialize BFS from all seed entities
        for eid in entity_ids:
            if eid in self._entities and eid not in visited:
                visited.add(eid)
                collected_entities[eid] = self._entities[eid]
                queue.append((eid, 0))

        while queue:
            current_id, depth = queue.pop(0)
            if depth >= max_hops:
                continue

            for rel in self._adjacency.get(current_id, []):
                collected_relationships.append(rel)
                if rel.target_id not in visited:
                    visited.add(rel.target_id)
                    if rel.target_id in self._entities:
                        collected_entities[rel.target_id] = self._entities[rel.target_id]
                    queue.append((rel.target_id, depth + 1))

        return SubGraph(
            entities=list(collected_entities.values()),
            relationships=collected_relationships,
        )

    def update_confidence(
        self,
        source_id: str,
        target_id: str,
        relationship_type: RelationshipType,
        delta: float,
    ) -> None:
        """Update the confidence score of a matching relationship.

        Finds the relationship matching the given source, target, and type,
        then updates its confidence by adding delta (capped at 1.0).

        Args:
            source_id: The source entity ID of the relationship.
            target_id: The target entity ID of the relationship.
            relationship_type: The type of relationship to match.
            delta: The value to add to the confidence score.

        Validates: Requirements 3.4, 6.3
        """
        for rel in self._adjacency.get(source_id, []):
            if rel.target_id == target_id and rel.relationship_type == relationship_type:
                rel.confidence = min(rel.confidence + delta, 1.0)
                break

    @property
    def entity_count(self) -> int:
        """Return the total number of entities in the graph."""
        return len(self._entities)

    @property
    def relationship_count(self) -> int:
        """Return the total number of relationships in the graph."""
        return sum(len(rels) for rels in self._adjacency.values())
