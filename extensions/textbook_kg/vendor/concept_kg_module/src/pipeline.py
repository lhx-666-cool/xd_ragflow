from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cleaner import ExtractionCleaner
from .embedder import EmbeddingBackend
from .extractor import BaseExtractionClient
from .graph_builder import ConceptGraphBuilder
from .graph_inference import GraphInferenceEngine
from .loader import LoadedDocument
from .mapper import build_docnode_mapping
from .merger import EntityMerger
from .models import ConceptGraph, DocumentNode, ExtractionBatch, GraphRelation, MergedEntity, RawEntity, RawRelation
from .utils import extract_json_payload, log, normalize_key, normalize_whitespace, stable_id


@dataclass
class PipelineBuildResult:
    concept_graph: ConceptGraph
    docnode_mapping: dict[str, dict[str, list[str]]]
    cleaned_entities: list[RawEntity]
    cleaned_relations: list[RawRelation]
    section_fragments: list[dict[str, Any]]
    chapter_completions: list[dict[str, Any]]


def build_concept_graph_from_batches(
    *,
    pipeline: str,
    loaded_document: LoadedDocument,
    batches: list[ExtractionBatch],
    schema: dict[str, Any],
    settings: dict[str, Any],
    embedder: EmbeddingBackend,
    source_document: str,
    completion_client: BaseExtractionClient | None = None,
    completion_prompt_path: Path | None = None,
) -> PipelineBuildResult:
    selected_pipeline = normalize_whitespace(pipeline).lower() or "hierarchical"
    if selected_pipeline == "flat":
        return _build_flat_result(
            loaded_document=loaded_document,
            batches=batches,
            schema=schema,
            settings=settings,
            embedder=embedder,
            source_document=source_document,
        )
    if selected_pipeline != "hierarchical":
        raise ValueError("`pipeline` must be `flat` or `hierarchical`.")
    return _build_hierarchical_result(
        loaded_document=loaded_document,
        batches=batches,
        schema=schema,
        settings=settings,
        embedder=embedder,
        source_document=source_document,
        completion_client=completion_client,
        completion_prompt_path=completion_prompt_path,
    )


def _build_flat_result(
    *,
    loaded_document: LoadedDocument,
    batches: list[ExtractionBatch],
    schema: dict[str, Any],
    settings: dict[str, Any],
    embedder: EmbeddingBackend,
    source_document: str,
) -> PipelineBuildResult:
    cleaner = ExtractionCleaner(schema=schema, settings=settings)
    raw_entities, raw_relations = cleaner.clean(batches)
    merger = EntityMerger(embedder=embedder, settings=settings)
    merge_result = merger.merge(raw_entities)
    graph = ConceptGraphBuilder().build(
        source_document=source_document,
        merged_entities=merge_result.entities,
        relations=raw_relations,
        merge_result=merge_result,
    )
    graph = GraphInferenceEngine(config=settings).infer(graph)
    mapping = build_docnode_mapping(graph.entities, graph.relations)
    return PipelineBuildResult(
        concept_graph=graph,
        docnode_mapping=mapping,
        cleaned_entities=raw_entities,
        cleaned_relations=raw_relations,
        section_fragments=[],
        chapter_completions=[],
    )


def _build_hierarchical_result(
    *,
    loaded_document: LoadedDocument,
    batches: list[ExtractionBatch],
    schema: dict[str, Any],
    settings: dict[str, Any],
    embedder: EmbeddingBackend,
    source_document: str,
    completion_client: BaseExtractionClient | None,
    completion_prompt_path: Path | None,
) -> PipelineBuildResult:
    cleaner = ExtractionCleaner(schema=schema, settings=settings)
    merger = EntityMerger(embedder=embedder, settings=settings)
    builder = ConceptGraphBuilder()

    document_nodes_by_id = {node.source_node_id: node for node in loaded_document.document_nodes}
    batches_by_source = {batch.source_node_id: batch for batch in batches}
    section_to_node_ids: dict[str, list[str]] = {}
    section_order: list[str] = []
    for node in loaded_document.nodes:
        section_id = node.section_node_id or node.parent_id or node.source_node_id
        if section_id not in section_to_node_ids:
            section_to_node_ids[section_id] = []
            section_order.append(section_id)
        section_to_node_ids[section_id].append(node.source_node_id)

    cleaned_entities: list[RawEntity] = []
    cleaned_relations: list[RawRelation] = []
    book_entities: list[RawEntity] = []
    book_relations: list[RawRelation] = []
    section_fragments: list[dict[str, Any]] = []

    for section_id in section_order:
        section_batches = [
            batches_by_source[source_id]
            for source_id in section_to_node_ids[section_id]
            if source_id in batches_by_source
        ]
        section_node = document_nodes_by_id.get(section_id)
        if not section_batches:
            continue
        section_entities, section_relations = cleaner.clean(section_batches)
        cleaned_entities.extend(section_entities)
        cleaned_relations.extend(section_relations)
        section_merge = merger.merge(section_entities)
        section_graph = builder.build(
            source_document=section_id,
            merged_entities=section_merge.entities,
            relations=section_relations,
            merge_result=section_merge,
        )
        book_entities.extend(_section_entities_to_book_entities(section_id, section_merge.entities))
        book_relations.extend(_section_relations_to_book_relations(section_id, section_graph.relations))
        section_fragments.append(
            _section_fragment_to_dict(
                section_id=section_id,
                section_node=section_node,
                section_graph=section_graph,
                chunk_node_ids=section_to_node_ids[section_id],
            )
        )

    book_merge = merger.merge(book_entities)
    graph = builder.build(
        source_document=source_document,
        merged_entities=book_merge.entities,
        relations=book_relations,
        merge_result=book_merge,
    )

    completer = ChapterRelationCompleter(
        client=completion_client,
        prompt_template_path=completion_prompt_path,
        schema=schema,
        settings=settings,
    )
    chapter_completion_records = completer.complete(
        graph=graph,
        loaded_document=loaded_document,
        section_fragments=section_fragments,
    )
    graph = GraphInferenceEngine(config=settings).infer(graph)
    mapping = build_docnode_mapping(graph.entities, graph.relations)
    return PipelineBuildResult(
        concept_graph=graph,
        docnode_mapping=mapping,
        cleaned_entities=cleaned_entities,
        cleaned_relations=cleaned_relations,
        section_fragments=section_fragments,
        chapter_completions=chapter_completion_records,
    )


def _section_entities_to_book_entities(section_id: str, entities: list[MergedEntity]) -> list[RawEntity]:
    book_entities: list[RawEntity] = []
    for entity in entities:
        definition = entity.merged_definitions[0] if entity.merged_definitions else ""
        book_entities.append(
            RawEntity(
                raw_entity_id=f"{section_id}::{entity.entity_id}",
                name=entity.canonical_name,
                alias=list(entity.aliases),
                entity_type=entity.entity_type,
                definition=definition,
                source_node_id=section_id,
            )
        )
    return book_entities


def _section_relations_to_book_relations(section_id: str, relations: list[GraphRelation]) -> list[RawRelation]:
    book_relations: list[RawRelation] = []
    for relation in relations:
        evidence = " | ".join(relation.evidences)
        book_relations.append(
            RawRelation(
                raw_relation_id=f"{section_id}::{relation.relation_id}",
                head=relation.head,
                relation=relation.relation,
                tail=relation.tail,
                source_node_id=section_id,
                evidence=evidence,
            )
        )
    return book_relations


def _section_fragment_to_dict(
    *,
    section_id: str,
    section_node: DocumentNode | None,
    section_graph: ConceptGraph,
    chunk_node_ids: list[str],
) -> dict[str, Any]:
    return {
        "section_node_id": section_id,
        "chapter_node_id": section_node.chapter_node_id if section_node else None,
        "label": section_node.label if section_node else "",
        "path_labels": section_node.path_labels if section_node else [],
        "chunk_node_ids": list(chunk_node_ids),
        "entities": [entity.to_dict() for entity in section_graph.entities],
        "relations": [relation.to_dict() for relation in section_graph.relations],
        "metadata": {
            "entity_count": len(section_graph.entities),
            "relation_count": len(section_graph.relations),
        },
    }


class ChapterRelationCompleter:
    def __init__(
        self,
        *,
        client: BaseExtractionClient | None,
        prompt_template_path: Path | None,
        schema: dict[str, Any],
        settings: dict[str, Any],
    ) -> None:
        config = settings.get("chapter_completion", {})
        self.enabled = bool(config.get("enabled", False))
        self.client = client
        self.schema = schema
        self.max_candidate_pairs = int(config.get("max_candidate_pairs_per_chapter", 120))
        self.max_relations = int(config.get("max_relations_per_chapter", 20))
        self.max_context_chars = int(config.get("max_context_chars", 8000))
        self.allowed_relation_types = set((schema.get("relation_types") or {}).keys())
        self.prompt_template = ""
        if self.enabled and prompt_template_path is not None and prompt_template_path.exists():
            self.prompt_template = prompt_template_path.read_text(encoding="utf-8")

    def complete(
        self,
        *,
        graph: ConceptGraph,
        loaded_document: LoadedDocument,
        section_fragments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        if self.client is None:
            log("chapter_completion", "Chapter completion enabled but no LLM client was provided; skipping")
            return []
        if not self.prompt_template:
            log("chapter_completion", "Chapter completion enabled but prompt template is missing; skipping")
            return []

        document_nodes_by_id = {node.source_node_id: node for node in loaded_document.document_nodes}
        chapter_to_sections = self._chapter_to_sections(section_fragments)
        completion_records: list[dict[str, Any]] = []
        existing = {(rel.head_entity_id, rel.relation, rel.tail_entity_id) for rel in graph.relations}
        existing_any_direction = {
            tuple(sorted([rel.head_entity_id, rel.tail_entity_id]))
            for rel in graph.relations
        }

        for chapter_id, section_ids in chapter_to_sections.items():
            chapter_node = document_nodes_by_id.get(chapter_id)
            if chapter_node is None:
                continue
            candidates = self._build_candidates(
                graph=graph,
                section_ids=section_ids,
                existing_any_direction=existing_any_direction,
            )
            if not candidates:
                completion_records.append(
                    {
                        "chapter_node_id": chapter_id,
                        "candidate_count": 0,
                        "relations": [],
                        "error": None,
                    }
                )
                continue
            prompt = self._render_prompt(chapter_node, section_fragments, section_ids, candidates)
            raw_response = ""
            try:
                raw_response = self.client.run(prompt, chapter_node)
                payload = extract_json_payload(raw_response)
                accepted = self._payload_to_relations(
                    payload=payload,
                    graph=graph,
                    chapter_node=chapter_node,
                    candidates=candidates,
                    existing=existing,
                    existing_any_direction=existing_any_direction,
                )
                graph.relations.extend(accepted)
                graph.relations.sort(key=lambda item: (item.head.lower(), item.relation, item.tail.lower()))
                completion_records.append(
                    {
                        "chapter_node_id": chapter_id,
                        "candidate_count": len(candidates),
                        "relations": [relation.to_dict() for relation in accepted],
                        "raw_response": raw_response,
                        "error": None,
                    }
                )
                log("chapter_completion", f"Chapter {chapter_id}: accepted {len(accepted)} completion relations")
            except Exception as exc:  # noqa: BLE001
                completion_records.append(
                    {
                        "chapter_node_id": chapter_id,
                        "candidate_count": len(candidates),
                        "relations": [],
                        "raw_response": raw_response,
                        "error": str(exc),
                    }
                )
                log("chapter_completion", f"Chapter {chapter_id}: completion failed: {exc}")
        return completion_records

    def _chapter_to_sections(self, section_fragments: list[dict[str, Any]]) -> dict[str, list[str]]:
        chapter_to_sections: dict[str, list[str]] = {}
        for fragment in section_fragments:
            chapter_id = normalize_whitespace(fragment.get("chapter_node_id"))
            section_id = normalize_whitespace(fragment.get("section_node_id"))
            if not chapter_id or not section_id:
                continue
            chapter_to_sections.setdefault(chapter_id, []).append(section_id)
        return chapter_to_sections

    def _build_candidates(
        self,
        *,
        graph: ConceptGraph,
        section_ids: list[str],
        existing_any_direction: set[tuple[str, str]],
    ) -> list[dict[str, Any]]:
        section_id_set = set(section_ids)
        entity_sections: dict[str, set[str]] = {}
        for entity in graph.entities:
            sections = set(entity.merged_source_node_ids) & section_id_set
            if sections:
                entity_sections[entity.entity_id] = sections

        candidate_entities = [entity for entity in graph.entities if entity.entity_id in entity_sections]
        candidates: list[dict[str, Any]] = []
        for left_index, left in enumerate(candidate_entities):
            for right in candidate_entities[left_index + 1 :]:
                pair_key = tuple(sorted([left.entity_id, right.entity_id]))
                if pair_key in existing_any_direction:
                    continue
                if not _has_cross_section_pair(entity_sections[left.entity_id], entity_sections[right.entity_id]):
                    continue
                candidate_id = f"cand_{len(candidates) + 1:04d}"
                candidates.append(
                    {
                        "candidate_id": candidate_id,
                        "left_entity_id": left.entity_id,
                        "left": left.canonical_name,
                        "left_sections": sorted(entity_sections[left.entity_id]),
                        "right_entity_id": right.entity_id,
                        "right": right.canonical_name,
                        "right_sections": sorted(entity_sections[right.entity_id]),
                    }
                )
                if len(candidates) >= self.max_candidate_pairs:
                    return candidates
        return candidates

    def _render_prompt(
        self,
        chapter_node: DocumentNode,
        section_fragments: list[dict[str, Any]],
        section_ids: list[str],
        candidates: list[dict[str, Any]],
    ) -> str:
        relation_lines = [
            f"- {name}: {description}"
            for name, description in (self.schema.get("relation_types") or {}).items()
        ]
        section_context = [
            {
                "section_node_id": fragment["section_node_id"],
                "label": fragment.get("label", ""),
                "entities": [
                    {
                        "entity_id": entity.get("entity_id"),
                        "name": entity.get("canonical_name"),
                        "type": entity.get("type"),
                        "definitions": entity.get("merged_definitions", [])[:2],
                    }
                    for entity in fragment.get("entities", [])
                ],
                "relations": [
                    {
                        "head": relation.get("head"),
                        "relation": relation.get("relation"),
                        "tail": relation.get("tail"),
                    }
                    for relation in fragment.get("relations", [])
                ],
            }
            for fragment in section_fragments
            if fragment.get("section_node_id") in set(section_ids)
        ]
        section_context_json = json.dumps(section_context, ensure_ascii=False, indent=2)
        if len(section_context_json) > self.max_context_chars:
            section_context_json = section_context_json[: self.max_context_chars].rstrip()
        candidates_json = json.dumps(candidates, ensure_ascii=False, indent=2)
        prompt = self.prompt_template
        prompt = prompt.replace("${CHAPTER_NODE_ID}", chapter_node.source_node_id)
        prompt = prompt.replace("${CHAPTER_LABEL}", chapter_node.label)
        prompt = prompt.replace("${RELATION_TYPES}", "\n".join(relation_lines))
        prompt = prompt.replace("${MAX_RELATIONS}", str(self.max_relations))
        prompt = prompt.replace("${SECTION_CONTEXT}", section_context_json)
        prompt = prompt.replace("${CANDIDATE_PAIRS}", candidates_json)
        return prompt

    def _payload_to_relations(
        self,
        *,
        payload: Any,
        graph: ConceptGraph,
        chapter_node: DocumentNode,
        candidates: list[dict[str, Any]],
        existing: set[tuple[str, str, str]],
        existing_any_direction: set[tuple[str, str]],
    ) -> list[GraphRelation]:
        if not isinstance(payload, dict):
            raise ValueError("Chapter completion payload must be a JSON object.")
        raw_relations = payload.get("relations") or []
        if not isinstance(raw_relations, list):
            raise ValueError("`relations` must be an array.")

        entity_by_id = {entity.entity_id: entity for entity in graph.entities}
        candidate_by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
        candidate_by_name_pair = {
            frozenset([normalize_key(candidate["left"]), normalize_key(candidate["right"])]): candidate
            for candidate in candidates
        }
        accepted: list[GraphRelation] = []
        for index, item in enumerate(raw_relations, start=1):
            if len(accepted) >= self.max_relations:
                break
            if not isinstance(item, dict):
                continue
            relation_type = normalize_whitespace(item.get("relation")).lower().replace("-", "_").replace(" ", "_")
            if relation_type not in self.allowed_relation_types:
                continue
            head = normalize_whitespace(item.get("head"))
            tail = normalize_whitespace(item.get("tail"))
            if not head or not tail or normalize_key(head) == normalize_key(tail):
                continue
            candidate = candidate_by_id.get(normalize_whitespace(item.get("candidate_id")))
            if candidate is None:
                candidate = candidate_by_name_pair.get(frozenset([normalize_key(head), normalize_key(tail)]))
            if candidate is None:
                continue
            left_key = normalize_key(candidate["left"])
            right_key = normalize_key(candidate["right"])
            if {normalize_key(head), normalize_key(tail)} != {left_key, right_key}:
                continue
            if normalize_key(head) == left_key:
                head_entity_id = candidate["left_entity_id"]
                tail_entity_id = candidate["right_entity_id"]
            else:
                head_entity_id = candidate["right_entity_id"]
                tail_entity_id = candidate["left_entity_id"]
            relation_key = (head_entity_id, relation_type, tail_entity_id)
            any_direction_key = tuple(sorted([head_entity_id, tail_entity_id]))
            if relation_key in existing:
                continue
            if any_direction_key in existing_any_direction:
                continue
            existing.add(relation_key)
            existing_any_direction.add(any_direction_key)
            evidence = normalize_whitespace(item.get("evidence") or item.get("rationale")) or "chapter-level completion"
            relation_id = stable_id(
                "relation",
                f"{chapter_node.source_node_id}|{head_entity_id}|{relation_type}|{tail_entity_id}|chapter_completion|{index}",
            )
            accepted.append(
                GraphRelation(
                    relation_id=relation_id,
                    head_entity_id=head_entity_id,
                    tail_entity_id=tail_entity_id,
                    head=entity_by_id[head_entity_id].canonical_name,
                    relation=relation_type,
                    tail=entity_by_id[tail_entity_id].canonical_name,
                    source_node_ids=[chapter_node.source_node_id],
                    evidences=[evidence],
                    raw_relation_ids=[f"{chapter_node.source_node_id}::chapter_completion::{index:03d}"],
                    is_inferred=True,
                )
            )
        return accepted


def _has_cross_section_pair(left_sections: set[str], right_sections: set[str]) -> bool:
    return any(left != right for left in left_sections for right in right_sections)
