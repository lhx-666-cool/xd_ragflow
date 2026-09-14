from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models import ConceptGraph, GraphRelation, MergedEntity
from src.neo4j_writer import Neo4jGraphWriter, resolve_neo4j_config
from src.utils import load_yaml, log


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load an exported concept KG output directory into Neo4j.")
    parser.add_argument("--input-dir", required=True, type=Path, help="Directory containing concept_kg.json and related files.")
    parser.add_argument(
        "--settings",
        type=Path,
        default=PROJECT_ROOT / "config" / "settings.yaml",
        help="Settings YAML with Neo4j defaults.",
    )
    parser.add_argument("--neo4j-uri", default="", help="Optional Neo4j URI override.")
    parser.add_argument("--neo4j-database", default="", help="Optional Neo4j database override.")
    parser.add_argument("--neo4j-user-env", default="", help="Optional Neo4j username env var override.")
    parser.add_argument("--neo4j-password-env", default="", help="Optional Neo4j password env var override.")
    parser.add_argument("--payload-output", type=Path, default=None, help="Optional path to save the prepared Neo4j payload JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Prepare payload only, do not connect to Neo4j.")
    return parser.parse_args()


def load_concept_graph(path: Path) -> ConceptGraph:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entities = [
        MergedEntity(
            entity_id=item["entity_id"],
            canonical_name=item["canonical_name"],
            aliases=item.get("aliases", []),
            entity_type=item.get("type", item.get("entity_type", "Concept")),
            merged_source_node_ids=item.get("merged_source_node_ids", []),
            merged_definitions=item.get("merged_definitions", []),
            merged_raw_entity_ids=item.get("merged_raw_entity_ids", []),
        )
        for item in payload.get("entities", [])
    ]
    relations = [
        GraphRelation(
            relation_id=item["relation_id"],
            head_entity_id=item["head_entity_id"],
            tail_entity_id=item["tail_entity_id"],
            head=item["head"],
            relation=item["relation"],
            tail=item["tail"],
            source_node_ids=item.get("source_node_ids", []),
            evidences=item.get("evidences", []),
            raw_relation_ids=item.get("raw_relation_ids", []),
        )
        for item in payload.get("relations", [])
    ]
    metadata = payload.get("metadata", {})
    return ConceptGraph(
        source_document=str(metadata.get("source_document", path)),
        entities=entities,
        relations=relations,
    )


def main() -> None:
    args = parse_args()
    settings = load_yaml(args.settings)
    input_dir = args.input_dir
    concept_graph = load_concept_graph(input_dir / "concept_kg.json")
    doc_nodes = json.loads((input_dir / "extractable_doc_nodes.json").read_text(encoding="utf-8"))
    docnode_mapping = json.loads((input_dir / "docnode_to_concepts.json").read_text(encoding="utf-8"))

    writer = Neo4jGraphWriter()
    payload = writer.build_payload(
        source_document=concept_graph.source_document,
        doc_nodes=doc_nodes,
        concept_graph=concept_graph,
        docnode_mapping=docnode_mapping,
    )

    if args.payload_output:
        writer.export_payload(args.payload_output, payload)
        log("neo4j", f"Saved Neo4j payload to {args.payload_output}")

    if args.dry_run:
        log("neo4j", f"Dry run only. Prepared payload with {payload['metadata']}")
        return

    neo4j_config = resolve_neo4j_config(
        settings,
        write_enabled=True,
        uri_override=args.neo4j_uri,
        database_override=args.neo4j_database,
        user_env_override=args.neo4j_user_env,
        password_env_override=args.neo4j_password_env,
    )
    if neo4j_config is None:
        raise RuntimeError("Neo4j config could not be resolved.")
    log("neo4j", f"Writing payload into Neo4j at {neo4j_config.uri} / {neo4j_config.database}")
    writer.write_to_neo4j(payload, neo4j_config)
    log("neo4j", "Neo4j import completed")


if __name__ == "__main__":
    main()
