from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

import networkx as nx
import trio


SCHEMA_VERSION = "ragflow-textbook-kg/v1"
MAX_ADAPTER_BYTES = 64 * 1024 * 1024
MAX_NODES = 50_000
MAX_EDGES = 200_000
MAX_NAME_CHARS = 512
MAX_DESCRIPTION_CHARS = 200_000
FIELD_SEPARATOR = "<SEP>"
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


class TextbookKgGraphRagError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedTextbookGraph:
    graph: nx.Graph
    sha256: str
    entity_count: int
    relation_count: int


def _text(value: Any, *, field: str, max_chars: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise TextbookKgGraphRagError(f"Adapter field {field} must be a string")
    value = _CONTROL_CHARS.sub("", value).strip()
    if required and not value:
        raise TextbookKgGraphRagError(f"Adapter field {field} is required")
    if len(value) > max_chars:
        raise TextbookKgGraphRagError(f"Adapter field {field} is too long")
    return value


def _string_list(value: Any, *, field: str, limit: int = 2048) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise TextbookKgGraphRagError(f"Adapter field {field} must be a bounded list")
    rows = []
    for item in value:
        text = _text(item, field=field, max_chars=MAX_NAME_CHARS)
        if text:
            rows.append(text)
    return sorted(set(rows))


def _merge_text(left: str, right: str) -> str:
    values = [value for value in (left, right) if value]
    return FIELD_SEPARATOR.join(dict.fromkeys(values))


def _edge_weight(value: Any) -> float:
    try:
        weight = float(value if value is not None else 1.0)
    except (TypeError, ValueError) as exc:
        raise TextbookKgGraphRagError("Adapter edge weight must be numeric") from exc
    if not math.isfinite(weight) or weight <= 0 or weight > 1_000_000:
        raise TextbookKgGraphRagError("Adapter edge weight is outside the allowed range")
    return weight


def prepare_textbook_graph(
    content: bytes,
    *,
    doc_id: str,
    expected_sha256: str | None = None,
    max_bytes: int = MAX_ADAPTER_BYTES,
) -> PreparedTextbookGraph:
    if not isinstance(content, bytes) or not content:
        raise TextbookKgGraphRagError("Adapter artifact is empty")
    if len(content) > max_bytes:
        raise TextbookKgGraphRagError("Adapter artifact is too large")
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 and digest.lower() != str(expected_sha256).lower():
        raise TextbookKgGraphRagError("Adapter artifact checksum mismatch")
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TextbookKgGraphRagError("Adapter artifact is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise TextbookKgGraphRagError("Unsupported adapter schema version")
    graph_payload = payload.get("knowledge_graph")
    if not isinstance(graph_payload, dict):
        raise TextbookKgGraphRagError("Adapter knowledge_graph must be an object")
    nodes = graph_payload.get("nodes")
    edges = graph_payload.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise TextbookKgGraphRagError("Adapter nodes and edges must be lists")
    if not nodes or len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise TextbookKgGraphRagError("Adapter graph size is outside the allowed range")

    document_id = _text(doc_id, field="doc_id", max_chars=128, required=True)
    graph = nx.Graph()
    external_to_name: dict[str, str] = {}
    for index, row in enumerate(nodes):
        if not isinstance(row, dict):
            raise TextbookKgGraphRagError(f"Adapter node {index} must be an object")
        external_id = _text(row.get("id"), field=f"nodes[{index}].id", max_chars=256, required=True)
        name = _text(
            row.get("entity_name"),
            field=f"nodes[{index}].entity_name",
            max_chars=MAX_NAME_CHARS,
            required=True,
        )
        external_to_name[external_id] = name
        description = _text(
            row.get("description") or name,
            field=f"nodes[{index}].description",
            max_chars=MAX_DESCRIPTION_CHARS,
            required=True,
        )
        entity_type = _text(
            row.get("entity_type") or "Concept",
            field=f"nodes[{index}].entity_type",
            max_chars=128,
            required=True,
        )
        textbook_sources = _string_list(row.get("source_id"), field=f"nodes[{index}].source_id")
        aliases = _string_list(row.get("aliases"), field=f"nodes[{index}].aliases")
        if graph.has_node(name):
            attrs = graph.nodes[name]
            attrs["description"] = _merge_text(attrs["description"], description)
            attrs["textbook_source_ids"] = sorted(set(attrs["textbook_source_ids"] + textbook_sources))
            attrs["aliases"] = sorted(set(attrs["aliases"] + aliases))
            attrs["textbook_entity_ids"] = sorted(set(attrs["textbook_entity_ids"] + [external_id]))
            continue
        graph.add_node(
            name,
            entity_name=name,
            entity_type=entity_type,
            description=description,
            source_id=[document_id],
            textbook_source_ids=textbook_sources,
            textbook_entity_ids=[external_id],
            aliases=aliases,
        )

    for index, row in enumerate(edges):
        if not isinstance(row, dict):
            raise TextbookKgGraphRagError(f"Adapter edge {index} must be an object")
        source_id = _text(row.get("src_id"), field=f"edges[{index}].src_id", max_chars=256)
        target_id = _text(row.get("tgt_id"), field=f"edges[{index}].tgt_id", max_chars=256)
        if (source_id and source_id not in external_to_name) or (target_id and target_id not in external_to_name):
            raise TextbookKgGraphRagError(f"Adapter edge {index} references a missing endpoint")
        source = external_to_name.get(source_id) or _text(
            row.get("source"), field=f"edges[{index}].source", max_chars=MAX_NAME_CHARS
        )
        target = external_to_name.get(target_id) or _text(
            row.get("target"), field=f"edges[{index}].target", max_chars=MAX_NAME_CHARS
        )
        if not source or not target or not graph.has_node(source) or not graph.has_node(target):
            raise TextbookKgGraphRagError(f"Adapter edge {index} references a missing endpoint")
        if source == target:
            continue
        relation = _text(
            row.get("relation") or "RELATED_TO",
            field=f"edges[{index}].relation",
            max_chars=256,
            required=True,
        )
        description = _text(
            row.get("description") or relation,
            field=f"edges[{index}].description",
            max_chars=MAX_DESCRIPTION_CHARS,
            required=True,
        )
        keywords = sorted(set(_string_list(row.get("keywords"), field=f"edges[{index}].keywords") + [relation]))
        textbook_sources = _string_list(row.get("source_id"), field=f"edges[{index}].source_id")
        weight = _edge_weight(row.get("weight"))
        existing = graph.get_edge_data(source, target)
        if existing:
            existing["description"] = _merge_text(existing["description"], description)
            existing["keywords"] = sorted(set(existing["keywords"] + keywords))
            existing["relation_types"] = sorted(set(existing["relation_types"] + [relation]))
            existing["textbook_source_ids"] = sorted(
                set(existing["textbook_source_ids"] + textbook_sources)
            )
            existing["weight"] += weight
            existing["is_inferred"] = bool(existing["is_inferred"] and row.get("is_inferred", False))
            continue
        graph.add_edge(
            source,
            target,
            description=description,
            keywords=keywords,
            relation_types=[relation],
            source_id=[document_id],
            textbook_source_ids=textbook_sources,
            is_inferred=bool(row.get("is_inferred", False)),
            weight=weight,
        )

    graph.graph.update(
        {
            "source_id": [document_id],
            "producer": "textbook-kg-api",
            "adapter_sha256": digest,
        }
    )
    return PreparedTextbookGraph(
        graph=graph,
        sha256=digest,
        entity_count=graph.number_of_nodes(),
        relation_count=graph.number_of_edges(),
    )


async def _merge_native_graph(
    *,
    tenant_id: str,
    kb_id: str,
    doc_id: str,
    graph: nx.Graph,
    embedding_model: Any,
    callback: Any,
) -> None:
    from graphrag.utils import GraphChange, get_graph, graph_merge, set_graph, tidy_graph
    from rag.utils.redis_conn import RedisDistributedLock

    lock = RedisDistributedLock(f"graphrag_task_{kb_id}", lock_value=f"textbook-kg-{doc_id}", timeout=1200)
    await lock.spin_acquire()
    try:
        old_graph = await get_graph(tenant_id, kb_id)
        change = GraphChange()
        if old_graph is not None:
            tidy_graph(old_graph, callback)
            if doc_id in set(old_graph.graph.get("source_id", [])):
                raise TextbookKgGraphRagError(
                    "This document already belongs to the native GraphRAG graph; refusing an ambiguous duplicate import"
                )
            merged = graph_merge(old_graph, graph, change)
            for node_name, incoming in graph.nodes(data=True):
                current = merged.nodes[node_name]
                for field in ("textbook_source_ids", "textbook_entity_ids", "aliases"):
                    current[field] = sorted(set(current.get(field, []) + incoming.get(field, [])))
            for source, target, incoming in graph.edges(data=True):
                current = merged.get_edge_data(source, target)
                if not current:
                    continue
                for field in ("textbook_source_ids", "relation_types"):
                    current[field] = sorted(set(current.get(field, []) + incoming.get(field, [])))
                current["is_inferred"] = bool(current.get("is_inferred", False) and incoming.get("is_inferred", False))
        else:
            merged = graph
            change.added_updated_nodes = set(merged.nodes())
            change.added_updated_edges = set(merged.edges())
        pagerank = nx.pagerank(merged)
        for node_name, score in pagerank.items():
            merged.nodes[node_name]["pagerank"] = score
        await set_graph(tenant_id, kb_id, embedding_model, merged, change, callback)
    finally:
        lock.release()


def import_textbook_graph(
    content: bytes,
    *,
    tenant_id: str,
    kb_id: str,
    doc_id: str,
    embedding_model: Any,
    expected_sha256: str | None = None,
    callback: Any = None,
) -> dict[str, Any]:
    prepared = prepare_textbook_graph(content, doc_id=doc_id, expected_sha256=expected_sha256)
    progress = callback or (lambda **_kwargs: None)

    async def run_import() -> None:
        await _merge_native_graph(
            tenant_id=tenant_id,
            kb_id=kb_id,
            doc_id=doc_id,
            graph=prepared.graph,
            embedding_model=embedding_model,
            callback=progress,
        )

    trio.run(run_import)
    return {
        "status": "imported",
        "artifact_sha256": prepared.sha256,
        "entity_count": prepared.entity_count,
        "relation_count": prepared.relation_count,
    }
