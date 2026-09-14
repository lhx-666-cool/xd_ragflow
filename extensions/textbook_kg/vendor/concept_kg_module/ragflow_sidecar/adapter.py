from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_ragflow_adapter(*, tree_path: Path, kg_dir: Path, output_path: Path) -> dict[str, Any]:
    tree = load_json(tree_path)
    graph = load_json(kg_dir / "concept_kg.json")
    mapping = load_json(kg_dir / "docnode_to_concepts.json")
    extractable_nodes = load_json(kg_dir / "extractable_doc_nodes.json")

    entities = graph.get("entities") or []
    relations = graph.get("relations") or []
    entity_by_id = {str(entity["entity_id"]): entity for entity in entities}

    graph_nodes = [
        {
            "id": entity["entity_id"],
            "entity_name": entity.get("canonical_name", ""),
            "entity_type": entity.get("type", "Concept"),
            "description": "\n".join(entity.get("merged_definitions") or []),
            "source_id": entity.get("merged_source_node_ids") or [],
            "aliases": entity.get("aliases") or [],
        }
        for entity in entities
    ]
    graph_edges = [
        {
            "source": relation.get("head", ""),
            "target": relation.get("tail", ""),
            "src_id": relation.get("head_entity_id", ""),
            "tgt_id": relation.get("tail_entity_id", ""),
            "relation": relation.get("relation", ""),
            "description": "\n".join(relation.get("evidences") or []),
            "keywords": [relation.get("relation", "")],
            "source_id": relation.get("source_node_ids") or [],
            "is_inferred": bool(relation.get("is_inferred", False)),
            "weight": 1.0,
        }
        for relation in relations
    ]

    chunks: list[dict[str, Any]] = []
    for node in extractable_nodes:
        source_node_id = str(node.get("source_node_id") or "")
        if not source_node_id:
            continue
        node_mapping = mapping.get(source_node_id) or {}
        concept_ids = [str(value) for value in node_mapping.get("entity_ids") or []]
        relation_ids = [str(value) for value in node_mapping.get("relation_ids") or []]
        concept_rows = [entity_by_id[entity_id] for entity_id in concept_ids if entity_id in entity_by_id]
        chunks.append(
            {
                "source_node_id": source_node_id,
                "content": node.get("content", ""),
                "important_keywords": [
                    str(entity.get("canonical_name") or "")
                    for entity in concept_rows
                    if entity.get("canonical_name")
                ],
                "tag_kwd": sorted(
                    {
                        str(entity.get("type") or "")
                        for entity in concept_rows
                        if entity.get("type")
                    }
                ),
                "questions": [],
                "concept_ids": concept_ids,
                "relation_ids": relation_ids,
                "chapter_node_id": node.get("chapter_node_id"),
                "section_node_id": node.get("section_node_id"),
                "path_labels": node.get("path_labels") or [],
                "pdf_page_start": node.get("pdf_page_start"),
                "pdf_page_end": node.get("pdf_page_end"),
            }
        )

    payload = {
        "schema_version": "ragflow-textbook-kg/v1",
        "document": {
            "name": tree.get("book_title") or graph.get("metadata", {}).get("source_document", ""),
            "source_document": graph.get("metadata", {}).get("source_document", ""),
            "metadata": {
                "toc_pages_pdf": tree.get("toc_pages_pdf") or [],
                "toc_to_pdf_offset": tree.get("toc_to_pdf_offset"),
            },
        },
        "chunks": chunks,
        "knowledge_graph": {
            "directed": True,
            "multigraph": False,
            "graph": {
                "source_id": sorted(mapping),
            },
            "nodes": graph_nodes,
            "edges": graph_edges,
        },
        "summary": {
            "chunk_count": len(chunks),
            "entity_count": len(graph_nodes),
            "relation_count": len(graph_edges),
        },
    }
    write_json(output_path, payload)
    return payload


def build_artifact_manifest(artifacts_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(artifacts_dir.rglob("*")):
        if not path.is_file() or path.name == "bundle.zip":
            continue
        relative = path.relative_to(artifacts_dir).as_posix()
        rows.append(
            {
                "name": relative,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return rows
