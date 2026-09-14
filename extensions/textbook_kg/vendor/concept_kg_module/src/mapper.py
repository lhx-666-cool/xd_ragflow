from __future__ import annotations

from collections import defaultdict

from .models import GraphRelation, MergedEntity


def build_docnode_mapping(entities: list[MergedEntity], relations: list[GraphRelation]) -> dict[str, dict[str, list[str]]]:
    mapping: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"entity_ids": set(), "relation_ids": set()})

    for entity in entities:
        for source_node_id in entity.merged_source_node_ids:
            mapping[source_node_id]["entity_ids"].add(entity.entity_id)

    for relation in relations:
        for source_node_id in relation.source_node_ids:
            mapping[source_node_id]["relation_ids"].add(relation.relation_id)

    return {
        source_node_id: {
            "entity_ids": sorted(values["entity_ids"]),
            "relation_ids": sorted(values["relation_ids"]),
        }
        for source_node_id, values in sorted(mapping.items(), key=lambda item: item[0])
    }
