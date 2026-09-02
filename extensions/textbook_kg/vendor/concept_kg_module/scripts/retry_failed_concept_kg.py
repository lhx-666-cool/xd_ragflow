from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.embedder import build_embedder
from src.exporter import ConceptGraphExporter
from src.extractor import EntityRelationExtractor, build_extraction_client
from src.loader import LoadedDocument
from src.models import DocumentNode, ExtractionBatch, RawEntity, RawRelation
from src.neo4j_writer import Neo4jGraphWriter, resolve_neo4j_config
from src.pipeline import build_concept_graph_from_batches
from src.utils import load_yaml, log, set_log_file, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retry failed concept KG extraction batches and rebuild exports.")
    parser.add_argument("--kg-output-dir", required=True, type=Path, help="Directory containing raw_extraction_batches.json.")
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
        help="Optional LLM backend override. Example: openai_compatible.",
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
    parser.add_argument("--write-neo4j", action="store_true", help="Write the rebuilt KG into Neo4j.")
    parser.add_argument("--neo4j-uri", default="", help="Optional Neo4j URI override.")
    parser.add_argument("--neo4j-database", default="", help="Optional Neo4j database override.")
    parser.add_argument("--neo4j-user-env", default="", help="Optional Neo4j username env var override.")
    parser.add_argument("--neo4j-password-env", default="", help="Optional Neo4j password env var override.")
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=1,
        help="Write raw_extraction_batches.json after this many retried nodes. Defaults to every node.",
    )
    parser.add_argument(
        "--max-retry-nodes",
        type=int,
        default=0,
        help="Retry at most this many failed nodes in one run. Use 0 for all failed nodes.",
    )
    parser.add_argument(
        "--skip-rebuild",
        action="store_true",
        help="Only retry extraction batches and skip cleaning, merging, and export rebuilding.",
    )
    return parser.parse_args()


def batch_from_dict(item: dict) -> ExtractionBatch:
    entities = [
        RawEntity(
            raw_entity_id=entity["raw_entity_id"],
            name=entity.get("name", ""),
            alias=entity.get("alias", []),
            entity_type=entity.get("type", entity.get("entity_type", "")),
            definition=entity.get("definition", ""),
            source_node_id=entity.get("source_node_id", ""),
        )
        for entity in item.get("entities", [])
    ]
    relations = [
        RawRelation(
            raw_relation_id=relation["raw_relation_id"],
            head=relation.get("head", ""),
            relation=relation.get("relation", ""),
            tail=relation.get("tail", ""),
            source_node_id=relation.get("source_node_id", ""),
            evidence=relation.get("evidence", ""),
        )
        for relation in item.get("relations", [])
    ]
    return ExtractionBatch(
        source_node_id=item["source_node_id"],
        entities=entities,
        relations=relations,
        raw_response=item.get("raw_response", ""),
        error=item.get("error"),
    )


def main() -> None:
    args = parse_args()
    settings = load_yaml(args.settings)
    schema = load_yaml(args.schema)
    output_dir = args.kg_output_dir
    set_log_file(output_dir / "run.log")

    raw_batches_path = output_dir / "raw_extraction_batches.json"
    doc_nodes_path = output_dir / "extractable_doc_nodes.json"
    document_nodes_path = output_dir / "document_nodes.json"
    raw_batches = json.loads(raw_batches_path.read_text(encoding="utf-8"))
    doc_nodes = json.loads(doc_nodes_path.read_text(encoding="utf-8"))
    document_nodes = json.loads(document_nodes_path.read_text(encoding="utf-8")) if document_nodes_path.exists() else doc_nodes

    batch_by_source = {item["source_node_id"]: batch_from_dict(item) for item in raw_batches}
    node_by_source = {item["source_node_id"]: DocumentNode(**item) for item in doc_nodes}
    loaded_document = LoadedDocument(
        source_document=str(output_dir),
        book_title=read_existing_book_title(output_dir),
        nodes=[DocumentNode(**item) for item in doc_nodes],
        document_nodes=[DocumentNode(**item) for item in document_nodes],
    )
    failed_ids = [source_id for source_id, batch in batch_by_source.items() if batch.error]
    if args.max_retry_nodes > 0:
        failed_ids = failed_ids[: args.max_retry_nodes]
    log("retry", f"Found {len(failed_ids)} failed nodes to retry")

    client = build_extraction_client(settings, backend_override=args.llm_backend or None)
    llm_settings = settings.get("llm", {})
    extractor = EntityRelationExtractor(
        client=client,
        prompt_template_path=args.prompt,
        schema=schema,
        max_content_chars=int(llm_settings.get("max_content_chars", 2600)),
        retry_content_chars=[int(value) for value in llm_settings.get("retry_content_chars", [])],
        max_entities_per_node=int(llm_settings.get("max_entities_per_node", 12)),
        max_relations_per_node=int(llm_settings.get("max_relations_per_node", 12)),
    )

    checkpoint_every = max(1, int(args.checkpoint_every))
    stopped_early = False
    for index, source_id in enumerate(failed_ids, start=1):
        node = node_by_source[source_id]
        log("retry", f"Retrying failed node {index}/{len(failed_ids)}: {source_id}")
        batch_by_source[source_id] = extractor.extract_node(node)
        if batch_by_source[source_id].error:
            log("retry", f"Node still failed after retry: {source_id}")
            if is_run_blocking_error(batch_by_source[source_id].error or ""):
                log("retry", "Stopping retry batch because the provider returned a non-retryable account/access error.")
                stopped_early = True
        else:
            log("retry", f"Node recovered: {source_id}")

        if index % checkpoint_every == 0 or index == len(failed_ids):
            updated_batches = [batch_by_source[item["source_node_id"]] for item in raw_batches]
            write_json(raw_batches_path, [batch.to_dict() for batch in updated_batches])
            failed_so_far = sum(1 for batch in updated_batches if batch.error)
            log("retry", f"Checkpoint saved after {index}/{len(failed_ids)} retried nodes; failed nodes now: {failed_so_far}")
        if stopped_early:
            break

    updated_batches = [batch_by_source[item["source_node_id"]] for item in raw_batches]
    write_json(raw_batches_path, [batch.to_dict() for batch in updated_batches])

    failed_after_retry = [batch for batch in updated_batches if batch.error]
    log("retry", f"Failed nodes after retry: {len(failed_after_retry)}")
    if stopped_early:
        log("retry", "Skipping rebuild because retry stopped early")
        return
    if args.skip_rebuild:
        log("retry", "Skipping rebuild because --skip-rebuild was set")
        return

    concept_graph_path = output_dir / "concept_kg.json"
    if concept_graph_path.exists():
        source_document = json.loads(concept_graph_path.read_text(encoding="utf-8")).get("metadata", {}).get(
            "source_document",
            str(output_dir),
        )
    else:
        source_document = str(output_dir)
        log("retry", "concept_kg.json does not exist yet; rebuilding exports from intermediate extraction files")

    embedder = build_embedder(settings)
    pipeline_mode = args.pipeline or str(settings.get("extraction", {}).get("pipeline", "hierarchical"))
    pipeline_result = build_concept_graph_from_batches(
        pipeline=pipeline_mode,
        loaded_document=loaded_document,
        batches=updated_batches,
        schema=schema,
        settings=settings,
        embedder=embedder,
        source_document=source_document,
        completion_client=client,
        completion_prompt_path=args.chapter_completion_prompt,
    )
    concept_graph = pipeline_result.concept_graph
    mapping = pipeline_result.docnode_mapping
    write_json(output_dir / "document_nodes.json", [node.to_dict() for node in loaded_document.document_nodes])
    write_json(
        output_dir / "cleaned_extractions.json",
        {
            "entities": [entity.to_dict() for entity in pipeline_result.cleaned_entities],
            "relations": [relation.to_dict() for relation in pipeline_result.cleaned_relations],
        },
    )
    write_json(output_dir / "section_kg_fragments.json", pipeline_result.section_fragments)
    write_json(output_dir / "chapter_relation_completions.json", pipeline_result.chapter_completions)

    exporter = ConceptGraphExporter()
    exporter.export(output_dir=output_dir, concept_graph=concept_graph, docnode_mapping=mapping)

    neo4j_writer = Neo4jGraphWriter()
    neo4j_payload = neo4j_writer.build_payload(
        source_document=concept_graph.source_document,
        doc_nodes=[node.to_dict() for node in loaded_document.document_nodes],
        concept_graph=concept_graph,
        docnode_mapping=mapping,
    )
    if bool(settings.get("neo4j", {}).get("export_payload", False)):
        neo4j_writer.export_payload(output_dir / "neo4j_payload.json", neo4j_payload)

    neo4j_config = resolve_neo4j_config(
        settings,
        write_enabled=args.write_neo4j,
        uri_override=args.neo4j_uri,
        database_override=args.neo4j_database,
        user_env_override=args.neo4j_user_env,
        password_env_override=args.neo4j_password_env,
    )
    if neo4j_config is not None:
        log("neo4j", f"Writing rebuilt KG into Neo4j at {neo4j_config.uri} / {neo4j_config.database}")
        neo4j_writer.write_to_neo4j(neo4j_payload, neo4j_config)
        log("neo4j", "Neo4j write completed")

    log(
        "retry",
        f"Rebuild done. Entities: {len(concept_graph.entities)}, relations: {len(concept_graph.relations)}",
    )


def is_run_blocking_error(message: str) -> bool:
    lowered = message.lower()
    markers = [
        "account balance is not sufficient",
        "余额",
        "model disabled",
        "browser_signature_banned",
        "do not retry",
        "restricted access",
        "access denied",
        "edgeone",
    ]
    return any(marker in lowered for marker in markers)


def read_existing_book_title(output_dir: Path) -> str:
    concept_graph_path = output_dir / "concept_kg.json"
    if not concept_graph_path.exists():
        return output_dir.name
    try:
        source_document = json.loads(concept_graph_path.read_text(encoding="utf-8")).get("metadata", {}).get(
            "source_document",
            "",
        )
    except json.JSONDecodeError:
        return output_dir.name
    if "|" in source_document:
        return source_document.split("|", 1)[0].strip() or output_dir.name
    return output_dir.name


if __name__ == "__main__":
    main()
