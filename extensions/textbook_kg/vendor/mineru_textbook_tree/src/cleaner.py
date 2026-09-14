from __future__ import annotations

from collections import defaultdict

from .models import ExtractionBatch, RawEntity, RawRelation
from .utils import normalize_key, normalize_whitespace, stable_id


class ExtractionCleaner:
    def __init__(self, schema: dict, settings: dict) -> None:
        self.allowed_entity_types = set((schema.get("entity_types") or {}).keys())
        self.allowed_relation_types = set((schema.get("relation_types") or {}).keys())
        cleaning_settings = settings.get("cleaning", {})
        self.drop_invalid_types = bool(cleaning_settings.get("drop_invalid_types", True))
        self.drop_empty_definitions = bool(cleaning_settings.get("drop_empty_definitions", False))

    def clean(self, batches: list[ExtractionBatch]) -> tuple[list[RawEntity], list[RawRelation]]:
        entities = self._clean_entities(batches)
        relations = self._clean_relations(batches)
        entities = self._ensure_relation_endpoint_entities(entities, relations)
        entities = self._dedupe_entities(entities)
        relations = self._dedupe_relations(relations)
        return entities, relations

    def _clean_entities(self, batches: list[ExtractionBatch]) -> list[RawEntity]:
        cleaned: list[RawEntity] = []
        for batch in batches:
            for entity in batch.entities:
                name = normalize_whitespace(entity.name)
                if not name:
                    continue
                entity_type = normalize_whitespace(entity.entity_type) or "Concept"
                if entity_type not in self.allowed_entity_types:
                    if self.drop_invalid_types:
                        entity_type = "Concept"
                    else:
                        continue
                definition = normalize_whitespace(entity.definition)
                if self.drop_empty_definitions and not definition:
                    continue
                aliases = self._normalize_aliases(name, entity.alias)
                cleaned.append(
                    RawEntity(
                        raw_entity_id=entity.raw_entity_id,
                        name=name,
                        alias=aliases,
                        entity_type=entity_type,
                        definition=definition,
                        source_node_id=normalize_whitespace(entity.source_node_id) or batch.source_node_id,
                    )
                )
        return self._dedupe_entities(cleaned)

    def _clean_relations(self, batches: list[ExtractionBatch]) -> list[RawRelation]:
        cleaned: list[RawRelation] = []
        for batch in batches:
            for relation in batch.relations:
                head = normalize_whitespace(relation.head)
                tail = normalize_whitespace(relation.tail)
                relation_type = normalize_whitespace(relation.relation)
                if not head or not tail or not relation_type:
                    continue
                if relation_type not in self.allowed_relation_types:
                    if self.drop_invalid_types:
                        continue
                    relation_type = relation_type
                cleaned.append(
                    RawRelation(
                        raw_relation_id=relation.raw_relation_id,
                        head=head,
                        relation=relation_type,
                        tail=tail,
                        source_node_id=normalize_whitespace(relation.source_node_id) or batch.source_node_id,
                        evidence=normalize_whitespace(relation.evidence),
                    )
                )
        return self._dedupe_relations(cleaned)

    def _normalize_aliases(self, name: str, aliases: list[str]) -> list[str]:
        normalized: list[str] = []
        seen = {normalize_key(name)}
        for alias in aliases:
            normalized_alias = normalize_whitespace(alias)
            if not normalized_alias:
                continue
            alias_key = normalize_key(normalized_alias)
            if alias_key in seen:
                continue
            seen.add(alias_key)
            normalized.append(normalized_alias)
        return normalized

    def _dedupe_entities(self, entities: list[RawEntity]) -> list[RawEntity]:
        grouped: dict[tuple[str, str, str], RawEntity] = {}
        for entity in entities:
            key = (entity.source_node_id, normalize_key(entity.name), entity.entity_type)
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = entity
                continue
            merged_aliases = self._normalize_aliases(existing.name, existing.alias + [entity.name] + entity.alias)
            better_definition = existing.definition if len(existing.definition) >= len(entity.definition) else entity.definition
            grouped[key] = RawEntity(
                raw_entity_id=existing.raw_entity_id,
                name=existing.name if len(existing.name) >= len(entity.name) else entity.name,
                alias=merged_aliases,
                entity_type=existing.entity_type,
                definition=better_definition,
                source_node_id=existing.source_node_id,
            )
        return list(grouped.values())

    def _dedupe_relations(self, relations: list[RawRelation]) -> list[RawRelation]:
        grouped: dict[tuple[str, str, str, str], RawRelation] = {}
        for relation in relations:
            key = (
                relation.source_node_id,
                normalize_key(relation.head),
                relation.relation,
                normalize_key(relation.tail),
            )
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = relation
                continue
            better_evidence = existing.evidence if len(existing.evidence) >= len(relation.evidence) else relation.evidence
            grouped[key] = RawRelation(
                raw_relation_id=existing.raw_relation_id,
                head=existing.head,
                relation=existing.relation,
                tail=existing.tail,
                source_node_id=existing.source_node_id,
                evidence=better_evidence,
            )
        return list(grouped.values())

    def _ensure_relation_endpoint_entities(self, entities: list[RawEntity], relations: list[RawRelation]) -> list[RawEntity]:
        by_source: dict[str, set[str]] = defaultdict(set)
        global_names: set[str] = set()
        for entity in entities:
            by_source[entity.source_node_id].add(normalize_key(entity.name))
            global_names.add(normalize_key(entity.name))
            for alias in entity.alias:
                alias_key = normalize_key(alias)
                by_source[entity.source_node_id].add(alias_key)
                global_names.add(alias_key)

        completed = list(entities)
        for relation in relations:
            for endpoint in (relation.head, relation.tail):
                endpoint_key = normalize_key(endpoint)
                if endpoint_key in by_source[relation.source_node_id] or endpoint_key in global_names:
                    continue
                stub = RawEntity(
                    raw_entity_id=stable_id("rawent", f"{relation.source_node_id}|{endpoint}|stub"),
                    name=endpoint,
                    alias=[],
                    entity_type="Concept",
                    definition="",
                    source_node_id=relation.source_node_id,
                )
                completed.append(stub)
                by_source[relation.source_node_id].add(endpoint_key)
                global_names.add(endpoint_key)
        return completed
