from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DocumentNode:
    node_id: str
    source_node_id: str
    parent_id: str | None
    marker: str
    title: str
    label: str
    level: int
    node_kind: str
    content: str
    path_labels: list[str] = field(default_factory=list)
    pdf_page_start: int | None = None
    pdf_page_end: int | None = None
    section_node_id: str | None = None
    chapter_node_id: str | None = None
    chunk_index: int | None = None
    chunk_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RawEntity:
    raw_entity_id: str
    name: str
    alias: list[str]
    entity_type: str
    definition: str
    source_node_id: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["type"] = payload.pop("entity_type")
        return payload


@dataclass
class RawRelation:
    raw_relation_id: str
    head: str
    relation: str
    tail: str
    source_node_id: str
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractionBatch:
    source_node_id: str
    entities: list[RawEntity] = field(default_factory=list)
    relations: list[RawRelation] = field(default_factory=list)
    raw_response: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_node_id": self.source_node_id,
            "entities": [item.to_dict() for item in self.entities],
            "relations": [item.to_dict() for item in self.relations],
            "raw_response": self.raw_response,
            "error": self.error,
        }


@dataclass
class MergedEntity:
    entity_id: str
    canonical_name: str
    aliases: list[str]
    entity_type: str
    merged_source_node_ids: list[str]
    merged_definitions: list[str]
    merged_raw_entity_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["type"] = payload.pop("entity_type")
        return payload


@dataclass
class GraphRelation:
    relation_id: str
    head_entity_id: str
    tail_entity_id: str
    head: str
    relation: str
    tail: str
    source_node_ids: list[str]
    evidences: list[str]
    raw_relation_ids: list[str]
    is_inferred: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConceptGraph:
    source_document: str
    entities: list[MergedEntity]
    relations: list[GraphRelation]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [entity.to_dict() for entity in self.entities],
            "relations": [relation.to_dict() for relation in self.relations],
            "metadata": {
                "source_document": self.source_document,
                "entity_count": len(self.entities),
                "relation_count": len(self.relations),
            },
        }
