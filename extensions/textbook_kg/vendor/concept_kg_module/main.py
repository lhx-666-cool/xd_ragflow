from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from src.embedder import build_embedder
from src.exporter import ConceptGraphExporter
from src.extractor import EntityRelationExtractor, build_extraction_client
from src.loader import DocumentGraphLoader
from src.neo4j_writer import Neo4jGraphWriter, resolve_neo4j_config
from src.pipeline import build_concept_graph_from_batches
from src.utils import ensure_dir, load_yaml, log, safe_filename, set_log_file, write_json


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
    parser.add_argument(
        "--pipeline",
        choices=["hierarchical", "flat"],
        default="",
        help="Pipeline mode override. Defaults to extraction.pipeline in settings.",
    )
    parser.add_argument(
        "--chapter-completion-prompt",
        type=Path,
        default=PROJECT_ROOT / "prompts" / "chapter_relation_completion.txt",
        help="Prompt template for chapter-level cross-section relation completion.",
    )
    parser.add_argument(
        "--no-run-subdir",
        action="store_true",
        help="Write directly into --output_dir instead of creating a book-title timestamp subdirectory.",
    )
    parser.add_argument("--write-neo4j", action="store_true", help="Write the resulting KG into Neo4j.")
    parser.add_argument("--neo4j-payload", action="store_true", help="Export Neo4j payload JSON for later import.")
    parser.add_argument("--neo4j-uri", default="", help="Optional Neo4j URI override.")
    parser.add_argument("--neo4j-database", default="", help="Optional Neo4j database override.")
    parser.add_argument("--neo4j-user-env", default="", help="Optional Neo4j username env var override.")
    parser.add_argument("--neo4j-password-env", default="", help="Optional Neo4j password env var override.")
    return parser.parse_args()


def resolve_run_output_dir(base_output_dir: Path, book_title: str, settings: dict, no_run_subdir: bool = False) -> Path:
    output_settings = settings.get("output", {})
    create_run_subdir = bool(output_settings.get("create_run_subdir", True))
    if no_run_subdir:
        create_run_subdir = False

    base_output_dir = ensure_dir(base_output_dir)
    if not create_run_subdir:
        return base_output_dir

    timestamp_format = str(output_settings.get("timestamp_format", "%Y%m%d_%H%M%S"))
    timestamp = safe_filename(datetime.now().strftime(timestamp_format), fallback="run", max_length=48)
    book_name = safe_filename(
        book_title,
        fallback="book",
        max_length=int(output_settings.get("book_title_max_chars", 80)),
    )
    template = str(output_settings.get("run_dir_template", "{book_title}_{timestamp}"))
    try:
        folder_name = template.format(book_title=book_name, timestamp=timestamp)
    except (KeyError, ValueError):
        folder_name = f"{book_name}_{timestamp}"
    folder_name = safe_filename(
        folder_name,
        fallback=f"{book_name}_{timestamp}",
        max_length=int(output_settings.get("run_dir_max_chars", 128)),
    )

    candidate = base_output_dir / folder_name
    if not candidate.exists():
        return ensure_dir(candidate)
    suffix = 2
    while True:
        suffixed = base_output_dir / f"{folder_name}_{suffix:02d}"
        if not suffixed.exists():
            return ensure_dir(suffixed)
        suffix += 1


def main() -> None:
    args = parse_args()
    schema = load_yaml(args.schema)
    settings = load_yaml(args.settings)

    extraction_settings = settings.get("extraction", {})
    pipeline_mode = args.pipeline or str(extraction_settings.get("pipeline", "hierarchical"))
    loader = DocumentGraphLoader(
        include_structural_nodes=bool(extraction_settings.get("include_structural_nodes", True)),
        include_paragraph_nodes=bool(extraction_settings.get("include_paragraph_nodes", True)),
        paragraph_min_chars=int(extraction_settings.get("paragraph_min_chars", 80)),
        max_nodes=int(extraction_settings.get("max_nodes", 0)),
        pipeline=pipeline_mode,
        semantic_chunk_chars=int(extraction_settings.get("semantic_chunk_chars", 2200)),
        semantic_chunk_min_chars=int(extraction_settings.get("semantic_chunk_min_chars", 300)),
        semantic_chunk_overlap_chars=int(extraction_settings.get("semantic_chunk_overlap_chars", 0)),
    )

    log("loader", f"Loading document graph from {args.doc_graph}")
    loaded_document = loader.load(args.doc_graph)
    output_dir = resolve_run_output_dir(
        args.output_dir,
        loaded_document.book_title,
        settings,
        no_run_subdir=args.no_run_subdir,
    )
    set_log_file(output_dir / "run.log")
    log("output", f"Output directory: {output_dir}")
    if output_dir != args.output_dir:
        log("output", f"Output root: {args.output_dir}")
    log("loader", f"Document graph: {args.doc_graph}")
    log(
        "loader",
        f"Loaded {len(loaded_document.nodes)} extractable nodes and "
        f"{len(loaded_document.document_nodes)} document nodes from {loaded_document.book_title}",
    )
    write_json(output_dir / "extractable_doc_nodes.json", [node.to_dict() for node in loaded_document.nodes])
    write_json(output_dir / "document_nodes.json", [node.to_dict() for node in loaded_document.document_nodes])

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

    embedder = build_embedder(settings)
    log("pipeline", f"Building concept graph with {pipeline_mode} pipeline")
    pipeline_result = build_concept_graph_from_batches(
        pipeline=pipeline_mode,
        loaded_document=loaded_document,
        batches=batches,
        schema=schema,
        settings=settings,
        embedder=embedder,
        source_document=f"{loaded_document.book_title} | {args.doc_graph}",
        completion_client=client,
        completion_prompt_path=args.chapter_completion_prompt,
    )
    concept_graph = pipeline_result.concept_graph
    mapping = pipeline_result.docnode_mapping
    write_json(
        output_dir / "cleaned_extractions.json",
        {
            "entities": [entity.to_dict() for entity in pipeline_result.cleaned_entities],
            "relations": [relation.to_dict() for relation in pipeline_result.cleaned_relations],
        },
    )
    write_json(output_dir / "section_kg_fragments.json", pipeline_result.section_fragments)
    write_json(output_dir / "chapter_relation_completions.json", pipeline_result.chapter_completions)
    log(
        "pipeline",
        f"Built graph with {len(concept_graph.entities)} entities and {len(concept_graph.relations)} relations",
    )

    exporter = ConceptGraphExporter()
    log("exporter", "Writing concept graph outputs")
    exporter.export(output_dir=output_dir, concept_graph=concept_graph, docnode_mapping=mapping)

    neo4j_writer = Neo4jGraphWriter()
    doc_node_rows = [node.to_dict() for node in loaded_document.document_nodes]
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
