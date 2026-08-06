"""Entity and relationship extractor for the knowledge graph.

Extracts entities and relationships from YAML framework profiles and
user chat messages, assigning confidence scores per source type.

Validates: Requirements 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.4
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from src.graph.knowledge_graph import (
    Entity,
    EntityType,
    KnowledgeGraph,
    Relationship,
    RelationshipType,
)
from src.knowledge_base.schema import FrameworkData

if TYPE_CHECKING:
    from src.llm.groq_client import GroqClient as LLMClient

logger = logging.getLogger(__name__)

_YAML_CONFIDENCE = 1.0
_USER_CONFIDENCE_LOW = 0.5
_USER_CONFIDENCE_HIGH = 0.8


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _entity_id(name: str, entity_type: EntityType) -> str:
    return f"{_slugify(name)}-{entity_type.value.lower()}"


class EntityExtractor:
    """Extracts entities and relationships from YAML profiles and user messages.

    Validates: Requirements 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.4
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:  # type: ignore
        self._client = llm_client

    # ------------------------------------------------------------------
    # YAML extraction
    # ------------------------------------------------------------------

    def extract_from_yaml(self, fw_data: FrameworkData) -> tuple[list[Entity], list[Relationship]]:
        """Deterministically extract entities and relationships from a FrameworkData.

        Validates: Requirements 5.1, 5.2, 5.4
        """
        logger.debug("EntityExtractor.extract_from_yaml: framework=%s", fw_data.framework_name)
        entities: list[Entity] = []
        relationships: list[Relationship] = []

        fw_id = _entity_id(fw_data.framework_name, EntityType.FRAMEWORK)
        entities.append(Entity(
            id=fw_id,
            name=fw_data.framework_name,
            entity_type=EntityType.FRAMEWORK,
            properties={
                "vendor": fw_data.vendor,
                "license": fw_data.license,
                "website": fw_data.website,
                "category": fw_data.category,
            },
            source="yaml",
        ))

        for lang in fw_data.languages_supported:
            lang_id = _entity_id(lang, EntityType.LANGUAGE)
            entities.append(Entity(id=lang_id, name=lang, entity_type=EntityType.LANGUAGE, source="yaml"))
            relationships.append(Relationship(
                source_id=fw_id, target_id=lang_id,
                relationship_type=RelationshipType.SUPPORTS_LANGUAGE,
                confidence=_YAML_CONFIDENCE, source="yaml",
            ))

        for cap_name, cap_value in fw_data.capabilities.items():
            if not cap_value or cap_value is False:
                continue
            cap_label = cap_name.replace("_", " ").title()
            cap_id = _entity_id(cap_label, EntityType.CAPABILITY)
            entities.append(Entity(
                id=cap_id, name=cap_label, entity_type=EntityType.CAPABILITY,
                properties={"value": str(cap_value)}, source="yaml",
            ))
            relationships.append(Relationship(
                source_id=fw_id, target_id=cap_id,
                relationship_type=RelationshipType.HAS_CAPABILITY,
                confidence=_YAML_CONFIDENCE, source="yaml",
            ))

        for tool_name, supported in fw_data.cicd_integration.items():
            if not supported or supported is False:
                continue
            tool_label = tool_name.replace("_", " ").title()
            tool_id = _entity_id(tool_label, EntityType.CI_CD_TOOL)
            entities.append(Entity(id=tool_id, name=tool_label, entity_type=EntityType.CI_CD_TOOL, source="yaml"))
            relationships.append(Relationship(
                source_id=fw_id, target_id=tool_id,
                relationship_type=RelationshipType.INTEGRATES_WITH,
                confidence=_YAML_CONFIDENCE, source="yaml",
            ))

        for provider_name, supported in fw_data.cloud_providers.items():
            if not supported or supported is False:
                continue
            provider_label = provider_name.replace("_", " ").title()
            provider_id = _entity_id(provider_label, EntityType.CLOUD_PROVIDER)
            entities.append(Entity(id=provider_id, name=provider_label, entity_type=EntityType.CLOUD_PROVIDER, source="yaml"))
            relationships.append(Relationship(
                source_id=fw_id, target_id=provider_id,
                relationship_type=RelationshipType.COMPATIBLE_WITH,
                confidence=_YAML_CONFIDENCE, source="yaml",
            ))

        for limitation in fw_data.limitations:
            lim_id = _entity_id(limitation[:60], EntityType.LIMITATION)
            entities.append(Entity(id=lim_id, name=limitation, entity_type=EntityType.LIMITATION, source="yaml"))
            relationships.append(Relationship(
                source_id=fw_id, target_id=lim_id,
                relationship_type=RelationshipType.REQUIRES,
                confidence=_YAML_CONFIDENCE, source="yaml",
            ))

        logger.debug(
            "EntityExtractor.extract_from_yaml: %s -> entities=%d relationships=%d",
            fw_data.framework_name, len(entities), len(relationships),
        )
        return entities, relationships

    # ------------------------------------------------------------------
    # Migration path detection
    # ------------------------------------------------------------------

    def detect_migration_paths(self, frameworks: list[FrameworkData]) -> tuple[list[Entity], list[Relationship]]:
        """Detect MIGRATES_TO relationships between framework pairs.

        Validates: Requirements 5.3
        """
        logger.debug("EntityExtractor.detect_migration_paths: frameworks=%d", len(frameworks))
        entities: list[Entity] = []
        relationships: list[Relationship] = []

        def _caps(fw: FrameworkData) -> set[str]:
            return {k for k, v in fw.capabilities.items() if v and v is not False}

        for i, fw_a in enumerate(frameworks):
            langs_a = set(fw_a.languages_supported)
            caps_a = _caps(fw_a)
            fw_a_id = _entity_id(fw_a.framework_name, EntityType.FRAMEWORK)

            for fw_b in frameworks[i + 1:]:
                langs_b = set(fw_b.languages_supported)
                caps_b = _caps(fw_b)
                fw_b_id = _entity_id(fw_b.framework_name, EntityType.FRAMEWORK)

                shared_langs = langs_a & langs_b
                shared_caps = caps_a & caps_b

                if not shared_langs or not shared_caps:
                    continue

                overlap = len(shared_caps) / max(len(caps_a), len(caps_b), 1)
                confidence = min(_YAML_CONFIDENCE, round(0.5 + overlap * 0.5, 2))

                path_name = f"{fw_a.framework_name} \u2192 {fw_b.framework_name}"
                path_id = _entity_id(path_name, EntityType.MIGRATION_PATH)
                entities.append(Entity(
                    id=path_id, name=path_name, entity_type=EntityType.MIGRATION_PATH,
                    properties={"shared_languages": list(shared_langs), "shared_capabilities": list(shared_caps)},
                    source="yaml",
                ))
                relationships.append(Relationship(
                    source_id=fw_a_id, target_id=fw_b_id,
                    relationship_type=RelationshipType.MIGRATES_TO,
                    confidence=confidence, source="yaml", properties={"path_id": path_id},
                ))
                relationships.append(Relationship(
                    source_id=fw_b_id, target_id=fw_a_id,
                    relationship_type=RelationshipType.MIGRATES_TO,
                    confidence=confidence, source="yaml", properties={"path_id": path_id},
                ))
                logger.debug(
                    "EntityExtractor.detect_migration_paths: %s <-> %s confidence=%.2f",
                    fw_a.framework_name, fw_b.framework_name, confidence,
                )

        logger.debug(
            "EntityExtractor.detect_migration_paths: total paths=%d relationships=%d",
            len(entities), len(relationships),
        )
        return entities, relationships

    # ------------------------------------------------------------------
    # LLM message extraction
    # ------------------------------------------------------------------

    def extract_from_message(self, message: str) -> tuple[list[Entity], list[Relationship]]:
        """Extract entities and relationships from a user chat message via LLM.

        Validates: Requirements 6.1, 6.2, 6.4
        """
        if self._client is None:
            logger.debug("EntityExtractor.extract_from_message: no LLM client configured")
            return [], []

        if not self._client.is_available:
            logger.debug("EntityExtractor.extract_from_message: LLM unavailable, skipping extraction")
            return [], []

        logger.debug("EntityExtractor.extract_from_message: message_len=%d model=%s", len(message), self._client.model)

        prompt = (
            "Extract automation framework entities and relationships from the text below.\n"
            "Return ONLY valid JSON with this exact structure:\n"
            '{"entities": [{"name": "...", "type": "Framework|Capability|Language|'
            'CI_CD_Tool|Cloud_Provider|Migration_Path|Limitation", "specificity": "high|medium|low"}], '
            '"relationships": [{"source": "...", "target": "...", '
            '"type": "SUPPORTS_LANGUAGE|HAS_CAPABILITY|INTEGRATES_WITH|MIGRATES_TO|'
            'COMPATIBLE_WITH|REQUIRES|REPORTED_BY_USER"}]}\n\n'
            f"Text: {message}"
        )

        try:
            raw = self._client.generate(prompt=prompt)
            logger.debug("EntityExtractor.extract_from_message: raw_response_len=%d", len(raw))
            entities, relationships = self._parse_llm_extraction(raw)
            logger.debug(
                "EntityExtractor.extract_from_message: parsed entities=%d relationships=%d",
                len(entities), len(relationships),
            )
            return entities, relationships
        except Exception as exc:
            logger.warning(
                "EntityExtractor.extract_from_message: LLM extraction failed — %s: %s",
                type(exc).__name__, exc,
            )
            return [], []

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------

    @staticmethod
    def user_confidence(specificity: str) -> float:
        return {"high": _USER_CONFIDENCE_HIGH, "medium": 0.65}.get(specificity.lower(), _USER_CONFIDENCE_LOW)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_llm_extraction(self, raw: str) -> tuple[list[Entity], list[Relationship]]:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            logger.warning("EntityExtractor._parse_llm_extraction: no JSON block found in response: '%.100s'", raw)
            return [], []

        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as exc:
            logger.warning("EntityExtractor._parse_llm_extraction: JSON parse error — %s", exc)
            return [], []

        entities: list[Entity] = []
        relationships: list[Relationship] = []
        entity_name_to_id: dict[str, str] = {}

        for e in data.get("entities", []):
            name = e.get("name", "").strip()
            type_str = e.get("type", "Framework")
            specificity = e.get("specificity", "low")

            if not name:
                continue

            try:
                entity_type = EntityType(type_str)
            except ValueError:
                logger.debug("EntityExtractor._parse_llm_extraction: unknown entity type '%s', defaulting to FRAMEWORK", type_str)
                entity_type = EntityType.FRAMEWORK

            eid = _entity_id(name, entity_type)
            entity_name_to_id[name.lower()] = eid
            confidence = self.user_confidence(specificity)
            entities.append(Entity(
                id=eid, name=name, entity_type=entity_type,
                properties={"confidence": confidence}, source="user",
            ))

        for r in data.get("relationships", []):
            src_name = r.get("source", "").strip()
            tgt_name = r.get("target", "").strip()
            rel_type_str = r.get("type", "REPORTED_BY_USER")

            src_id = entity_name_to_id.get(src_name.lower())
            tgt_id = entity_name_to_id.get(tgt_name.lower())

            if not src_id or not tgt_id:
                logger.debug(
                    "EntityExtractor._parse_llm_extraction: skipping relationship '%s'->'%s' — entity not found",
                    src_name, tgt_name,
                )
                continue

            try:
                rel_type = RelationshipType(rel_type_str)
            except ValueError:
                logger.debug("EntityExtractor._parse_llm_extraction: unknown rel type '%s', defaulting to REPORTED_BY_USER", rel_type_str)
                rel_type = RelationshipType.REPORTED_BY_USER

            relationships.append(Relationship(
                source_id=src_id, target_id=tgt_id,
                relationship_type=rel_type,
                confidence=_USER_CONFIDENCE_LOW, source="user",
            ))

        return entities, relationships
