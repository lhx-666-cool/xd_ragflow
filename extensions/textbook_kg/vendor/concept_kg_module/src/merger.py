from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from .embedder import EmbeddingBackend, cosine_similarity
from .models import MergedEntity, RawEntity
from .utils import normalize_key, stable_id, tokenize_text


@dataclass
class MergeResult:
    entities: list[MergedEntity]
    raw_to_merged: dict[str, str]
    name_to_merged_ids: dict[str, list[str]]


class EntityMerger:
    def __init__(self, embedder: EmbeddingBackend, settings: dict) -> None:
        merging = settings.get("merging", {})
        self.embedder = embedder
        self.similarity_threshold = float(merging.get("similarity_threshold", 0.84))
        self.lexical_similarity_threshold = float(merging.get("lexical_similarity_threshold", 0.55))
        self.exact_name_merge = bool(merging.get("exact_name_merge", True))
        self.merge_same_type_only = bool(merging.get("merge_same_type_only", False))

    def merge(self, entities: list[RawEntity]) -> MergeResult:
        if not entities:
            return MergeResult(entities=[], raw_to_merged={}, name_to_merged_ids={})

        parents = list(range(len(entities)))
        texts = [self._entity_text(entity) for entity in entities]
        embeddings = self.embedder.embed_texts(texts)

        if self.exact_name_merge:
            name_groups: dict[tuple[str, str | None], list[int]] = defaultdict(list)
            for index, entity in enumerate(entities):
                name_key = normalize_key(entity.name)
                type_key = entity.entity_type if self.merge_same_type_only else None
                name_groups[(name_key, type_key)].append(index)
            for indices in name_groups.values():
                self._union_group(indices, parents)

        for left_index in range(len(entities)):
            for right_index in range(left_index + 1, len(entities)):
                left_entity = entities[left_index]
                right_entity = entities[right_index]
                if self.merge_same_type_only and left_entity.entity_type != right_entity.entity_type:
                    continue
                similarity = cosine_similarity(embeddings[left_index], embeddings[right_index])
                if similarity >= self.similarity_threshold and self._passes_lexical_gate(left_entity, right_entity):
                    self._union(left_index, right_index, parents)

        cluster_members: dict[int, list[int]] = defaultdict(list)
        for index in range(len(entities)):
            cluster_members[self._find(index, parents)].append(index)

        merged_entities: list[MergedEntity] = []
        raw_to_merged: dict[str, str] = {}
        name_to_ids: dict[str, list[str]] = defaultdict(list)

        for member_indices in cluster_members.values():
            cluster_entities = [entities[index] for index in member_indices]
            merged_entity = self._merge_cluster(cluster_entities)
            merged_entities.append(merged_entity)
            for entity in cluster_entities:
                raw_to_merged[entity.raw_entity_id] = merged_entity.entity_id
            for name in [merged_entity.canonical_name, *merged_entity.aliases]:
                name_key = normalize_key(name)
                if merged_entity.entity_id not in name_to_ids[name_key]:
                    name_to_ids[name_key].append(merged_entity.entity_id)

        merged_entities.sort(key=lambda item: item.canonical_name.lower())
        name_to_merged_ids = {key: sorted(value) for key, value in name_to_ids.items()}
        return MergeResult(entities=merged_entities, raw_to_merged=raw_to_merged, name_to_merged_ids=name_to_merged_ids)

    def _entity_text(self, entity: RawEntity) -> str:
        parts = [entity.name]
        if entity.definition:
            parts.append(entity.definition)
        if entity.alias:
            parts.append(" ".join(entity.alias))
        return " ".join(parts)

    def _merge_cluster(self, cluster_entities: list[RawEntity]) -> MergedEntity:
        name_counter = Counter(entity.name for entity in cluster_entities)
        canonical_name = sorted(
            name_counter.keys(),
            key=lambda name: (-name_counter[name], -len(name), name.lower()),
        )[0]

        alias_candidates: list[str] = []
        for entity in cluster_entities:
            alias_candidates.extend([entity.name, *entity.alias])
        aliases = []
        seen = {normalize_key(canonical_name)}
        for alias in alias_candidates:
            alias_key = normalize_key(alias)
            if not alias or alias_key in seen:
                continue
            seen.add(alias_key)
            aliases.append(alias)

        type_counter = Counter(entity.entity_type for entity in cluster_entities)
        entity_type = type_counter.most_common(1)[0][0]
        source_node_ids = sorted({entity.source_node_id for entity in cluster_entities})
        definitions = sorted({entity.definition for entity in cluster_entities if entity.definition})
        raw_ids = sorted(entity.raw_entity_id for entity in cluster_entities)
        entity_id = stable_id("entity", f"{canonical_name}|{entity_type}|{'|'.join(raw_ids)}")

        return MergedEntity(
            entity_id=entity_id,
            canonical_name=canonical_name,
            aliases=aliases,
            entity_type=entity_type,
            merged_source_node_ids=source_node_ids,
            merged_definitions=definitions,
            merged_raw_entity_ids=raw_ids,
        )

    def _passes_lexical_gate(self, left: RawEntity, right: RawEntity) -> bool:
        if normalize_key(left.name) == normalize_key(right.name):
            return True
        left_tokens = set(tokenize_text(left.name))
        right_tokens = set(tokenize_text(right.name))
        if not left_tokens or not right_tokens:
            return False
        overlap = len(left_tokens & right_tokens)
        union = len(left_tokens | right_tokens)
        if union == 0:
            return False
        jaccard = overlap / union
        return jaccard >= self.lexical_similarity_threshold

    def _union_group(self, indices: list[int], parents: list[int]) -> None:
        if not indices:
            return
        root = indices[0]
        for index in indices[1:]:
            self._union(root, index, parents)

    def _find(self, index: int, parents: list[int]) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def _union(self, left: int, right: int, parents: list[int]) -> None:
        left_root = self._find(left, parents)
        right_root = self._find(right, parents)
        if left_root != right_root:
            parents[right_root] = left_root
