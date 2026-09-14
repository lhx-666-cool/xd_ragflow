from __future__ import annotations

from collections import defaultdict

from .merger import MergeResult
from .models import ConceptGraph, GraphRelation, MergedEntity, RawRelation
from .utils import log, normalize_key, stable_id


class ConceptGraphBuilder:
    def build(
        self,
        source_document: str,
        merged_entities: list[MergedEntity],
        relations: list[RawRelation],
        merge_result: MergeResult,
    ) -> ConceptGraph:
        entity_map = {entity.entity_id: entity for entity in merged_entities}
        name_index = defaultdict(list, merge_result.name_to_merged_ids)

        relation_accumulator: dict[tuple[str, str, str], GraphRelation] = {}
        skipped_self_loops = 0
        for relation in relations:
            head_entity_id = self._resolve_entity_id(relation.head, relation.source_node_id, entity_map, name_index)
            tail_entity_id = self._resolve_entity_id(relation.tail, relation.source_node_id, entity_map, name_index)
            if head_entity_id == tail_entity_id:
                skipped_self_loops += 1
                continue
            key = (head_entity_id, relation.relation, tail_entity_id)
            existing = relation_accumulator.get(key)
            if existing is None:
                relation_id = stable_id("relation", f"{head_entity_id}|{relation.relation}|{tail_entity_id}")
                relation_accumulator[key] = GraphRelation(
                    relation_id=relation_id,
                    head_entity_id=head_entity_id,
                    tail_entity_id=tail_entity_id,
                    head=entity_map[head_entity_id].canonical_name,
                    relation=relation.relation,
                    tail=entity_map[tail_entity_id].canonical_name,
                    source_node_ids=[relation.source_node_id],
                    evidences=[relation.evidence] if relation.evidence else [],
                    raw_relation_ids=[relation.raw_relation_id],
                )
                continue

            if relation.source_node_id not in existing.source_node_ids:
                existing.source_node_ids.append(relation.source_node_id)
            if relation.evidence and relation.evidence not in existing.evidences:
                existing.evidences.append(relation.evidence)
            if relation.raw_relation_id not in existing.raw_relation_ids:
                existing.raw_relation_ids.append(relation.raw_relation_id)

        for graph_relation in relation_accumulator.values():
            graph_relation.source_node_ids.sort()
            graph_relation.evidences.sort()
            graph_relation.raw_relation_ids.sort()

        if skipped_self_loops:
            log("graph_builder", f"Skipped {skipped_self_loops} self-loop relations after entity merging")

        concept_graph = ConceptGraph(
            source_document=source_document,
            entities=sorted(entity_map.values(), key=lambda item: item.canonical_name.lower()),
            relations=sorted(relation_accumulator.values(), key=lambda item: (item.head.lower(), item.relation, item.tail.lower())),
        )
        return concept_graph

    def _resolve_entity_id(
        self,
        entity_name: str,
        source_node_id: str,
        entity_map: dict[str, MergedEntity],
        name_index: dict[str, list[str]],
    ) -> str:
        key = normalize_key(entity_name)
        candidate_ids = list(name_index.get(key, []))
        if not candidate_ids:
            synthetic = MergedEntity(
                entity_id=stable_id("entity", f"synthetic|{entity_name}|{source_node_id}"),
                canonical_name=entity_name,
                aliases=[],
                entity_type="Concept",
                merged_source_node_ids=[source_node_id],
                merged_definitions=[],
                merged_raw_entity_ids=[],
            )
            entity_map[synthetic.entity_id] = synthetic
            name_index[key] = [synthetic.entity_id]
            return synthetic.entity_id
        if len(candidate_ids) == 1:
            return candidate_ids[0]

        with_same_source = [
            entity_id
            for entity_id in candidate_ids
            if source_node_id in entity_map[entity_id].merged_source_node_ids
        ]
        if len(with_same_source) == 1:
            return with_same_source[0]
        return sorted(candidate_ids)[0]
