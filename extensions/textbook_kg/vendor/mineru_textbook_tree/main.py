from __future__ import annotations

import argparse
from pathlib import Path

from src.cleaner import ExtractionCleaner
from src.embedder import build_embedder
from src.exporter import ConceptGraphExporter
from src.extractor import EntityRelationExtractor, build_extraction_client
from src.graph_builder import ConceptGraphBuilder
from src.loader import DocumentGraphLoader
from src.mapper import build_docnode_mapping
from src.merger import EntityMerger
from src.neo4j_writer import Neo4jGraphWriter, resolve_neo4j_config
from src.utils import ensure_dir, load_yaml, log, write_json


PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a concept knowledge graph from a textbook document graph JSON.")
    parser.add_argument("--doc_graph", required=True, type=Path, help="Path to content_tree/doc graph JSON.")
    parser.add_argument("--output_dir", required=True, type=Path, help="Directory for KG outputs.")
    parser.add_argument(
        "--schema",
        type=Path,
        default=PROJECT_ROOT / "config" / "schema.yaml",
        help="Schema YAML defining entity and relation types.",
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=PROJECT_ROOT / "config" / "settings.yaml",
        help="Settings YAML for extraction and merging.",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=PROJECT_ROOT / "prompts" / "entity_relation_extraction.txt",
        help="Prompt template for entity and relation extraction.",
    )
    parser.add_argument(
        "--llm-backend",
        default="",
        help="Optional LLM backend override. Example: mock or openai_compatible.",
    )
    parser.add_argument("--write-neo4j", action="store_true", help="Write the resulting KG into Neo4j.")
    parser.add_argument("--neo4j-payload", action="store_true", help="Export Neo4j payload JSON for later import.")
    parser.add_argument("--neo4j-uri", default="", help="Optional Neo4j URI override.")
    parser.add_argument("--neo4j-database", default="", help="Optional Neo4j database override.")
    parser.add_argument("--neo4j-user-env", default="", help="Optional Neo4j username env var override.")
    parser.add_argument("--neo4j-password-env", default="", help="Optional Neo4j password env var override.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = ensure_dir(args.output_dir)
    schema = load_yaml(args.schema)
    settings = load_yaml(args.settings)

    extraction_settings = settings.get("extraction", {})
    loader = DocumentGraphLoader(
        include_structural_nodes=bool(extraction_settings.get("include_structural_nodes", True)),
        include_paragraph_nodes=bool(extraction_settings.get("include_paragraph_nodes", True)),
        paragraph_min_chars=int(extraction_settings.get("paragraph_min_chars", 80)),
        max_nodes=int(extraction_settings.get("max_nodes", 0)),
    )

    log("loader", f"Loading document graph from {args.doc_graph}")
    loaded_document = loader.load(args.doc_graph)
    log("loader", f"Loaded {len(loaded_document.nodes)} extractable nodes from {loaded_document.book_title}")
    write_json(output_dir / "extractable_doc_nodes.json", [node.to_dict() for node in loaded_document.nodes])

    client = build_extraction_client(settings, backend_override=args.llm_backend or None)
    llm_settings = settings.get("llm", {})
    log(
        "extractor",
        "LLM config: "
        f"backend={args.llm_backend or llm_settings.get('backend', 'openai_compatible')}, "
        f"base_url={llm_settings.get('base_url', 'https://api.openai.com/v1')}, "
        f"model={llm_settings.get('model', 'gpt-4o-mini')}, "
        f"api_key_env={llm_settings.get('api_key_env', 'OPENAI_API_KEY')}",
    )
    extractor = EntityRelationExtractor(
        client=client,
        prompt_template_path=args.prompt,
        schema=schema,
        max_content_chars=int(settings.get("llm", {}).get("max_content_chars", 4000)),
        retry_content_chars=[int(value) for value in settings.get("llm", {}).get("retry_content_chars", [])],
        max_entities_per_node=int(settings.get("llm", {}).get("max_entities_per_node", 12)),
        max_relations_per_node=int(settings.get("llm", {}).get("max_relations_per_node", 12)),
    )

    log("extractor", "Running entity and relation extraction")
    batches = extractor.extract_nodes(loaded_document.nodes)
    write_json(output_dir / "raw_extraction_batches.json", [batch.to_dict() for batch in batches])
    failed_batches = [batch for batch in batches if batch.error]
    if failed_batches:
        log("extractor", f"{len(failed_batches)} nodes failed during extraction; continuing with remaining results")

    cleaner = ExtractionCleaner(schema=schema, settings=settings)
    log("cleaner", "Cleaning and normalizing extraction results")
    raw_entities, raw_relations = cleaner.clean(batches)
    write_json(
        output_dir / "cleaned_extractions.json",
        {
            "entities": [entity.to_dict() for entity in raw_entities],
            "relations": [relation.to_dict() for relation in raw_relations],
        },
    )
    log("cleaner", f"Kept {len(raw_entities)} cleaned entities and {len(raw_relations)} cleaned relations")

    embedder = build_embedder(settings)
    merger = EntityMerger(embedder=embedder, settings=settings)
    log("merger", "Merging semantically similar entities")
    merge_result = merger.merge(raw_entities)
    log("merger", f"Merged into {len(merge_result.entities)} canonical entities")

    builder = ConceptGraphBuilder()
    concept_graph = builder.build(
        source_document=f"{loaded_document.book_title} | {args.doc_graph}",
        merged_entities=merge_result.entities,
        relations=raw_relations,
        merge_result=merge_result,
    )
    mapping = build_docnode_mapping(concept_graph.entities, concept_graph.relations)

    exporter = ConceptGraphExporter()
    log("exporter", "Writing concept graph outputs")
    exporter.export(output_dir=output_dir, concept_graph=concept_graph, docnode_mapping=mapping)

    neo4j_writer = Neo4jGraphWriter()
    doc_node_rows = [node.to_dict() for node in loaded_document.nodes]
    neo4j_payload = neo4j_writer.build_payload(
        source_document=concept_graph.source_document,
        doc_nodes=doc_node_rows,
        concept_graph=concept_graph,
        docnode_mapping=mapping,
    )
    if args.neo4j_payload or bool(settings.get("neo4j", {}).get("export_payload", False)):
        neo4j_writer.export_payload(output_dir / "neo4j_payload.json", neo4j_payload)
        log("neo4j", "Exported Neo4j payload JSON")

    neo4j_config = resolve_neo4j_config(
        settings,
        write_enabled=args.write_neo4j,
        uri_override=args.neo4j_uri,
        database_override=args.neo4j_database,
        user_env_override=args.neo4j_user_env,
        password_env_override=args.neo4j_password_env,
    )
    if neo4j_config is not None:
        log("neo4j", f"Writing KG into Neo4j at {neo4j_config.uri} / {neo4j_config.database}")
        neo4j_writer.write_to_neo4j(neo4j_payload, neo4j_config)
        log("neo4j", "Neo4j write completed")

    log(
        "exporter",
        f"Done. Entities: {len(concept_graph.entities)}, relations: {len(concept_graph.relations)}, output_dir: {output_dir}",
    )


if __name__ == "__main__":
    main()
