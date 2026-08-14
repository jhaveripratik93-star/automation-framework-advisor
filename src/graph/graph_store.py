"""Persistent storage for the KnowledgeGraph using atomic JSON file writes.

Validates: Requirements 4.1, 4.2, 4.3, 4.4
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from src.graph.knowledge_graph import (
    Entity,
    EntityType,
    KnowledgeGraph,
    Relationship,
    RelationshipType,
)

logger = logging.getLogger(__name__)


class GraphStore:
    """Persists and loads a KnowledgeGraph to/from a JSON file.

    Uses atomic write (temp file + rename) to prevent corruption.

    Validates: Requirements 4.1, 4.2, 4.3, 4.4
    """

    def __init__(self, path: str = "data/knowledge_graph.json") -> None:
        self._path = Path(path)
        logger.debug("GraphStore initialized: path=%s", self._path)

    def save(self, graph: KnowledgeGraph) -> None:
        """Serialize graph to JSON using atomic write (temp + rename).

        Validates: Requirements 4.1, 4.2
        """
        entity_count = len(graph._entities)
        rel_count = sum(len(v) for v in graph._adjacency.values())
        logger.debug("GraphStore.save: entities=%d relationships=%d -> %s", entity_count, rel_count, self._path)

        data = {
            "entities": [
                {
                    "id": e.id,
                    "name": e.name,
                    "entity_type": e.entity_type.value,
                    "properties": e.properties,
                    "source": e.source,
                }
                for e in graph._entities.values()
            ],
            "relationships": [
                {
                    "source_id": r.source_id,
                    "target_id": r.target_id,
                    "relationship_type": r.relationship_type.value,
                    "confidence": r.confidence,
                    "source": r.source,
                    "properties": r.properties,
                }
                for rels in graph._adjacency.values()
                for r in rels
            ],
        }

        self._path.parent.mkdir(parents=True, exist_ok=True)

        dir_ = str(self._path.parent)
        fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self._path)
            logger.info("GraphStore.save: saved %d entities, %d relationships to %s", entity_count, rel_count, self._path)
        except Exception as exc:
            logger.error("GraphStore.save: failed to write %s — %s: %s", self._path, type(exc).__name__, exc)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def load(self) -> KnowledgeGraph | None:
        """Deserialize JSON file into a KnowledgeGraph; return None if missing or invalid.

        Validates: Requirements 4.3, 4.4
        """
        if not self.exists():
            logger.debug("GraphStore.load: file not found at %s", self._path)
            return None

        logger.debug("GraphStore.load: loading from %s", self._path)
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)

            graph = KnowledgeGraph()

            for e in data.get("entities", []):
                graph.add_entity(
                    Entity(
                        id=e["id"],
                        name=e["name"],
                        entity_type=EntityType(e["entity_type"]),
                        properties=e.get("properties", {}),
                        source=e.get("source", "yaml"),
                    )
                )

            for r in data.get("relationships", []):
                graph.add_relationship(
                    Relationship(
                        source_id=r["source_id"],
                        target_id=r["target_id"],
                        relationship_type=RelationshipType(r["relationship_type"]),
                        confidence=r.get("confidence", 1.0),
                        source=r.get("source", "yaml"),
                        properties=r.get("properties", {}),
                    )
                )

            logger.info(
                "GraphStore.load: loaded %d entities, %d relationships from %s",
                graph.entity_count, graph.relationship_count, self._path,
            )
            return graph

        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error("GraphStore.load: failed to parse %s — %s: %s", self._path, type(exc).__name__, exc)
            return None

    def exists(self) -> bool:
        return self._path.exists()

    def is_valid(self) -> bool:
        if not self.exists():
            return False
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            return "entities" in data and "relationships" in data
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("GraphStore.is_valid: invalid file %s — %s", self._path, exc)
            return False
