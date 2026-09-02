from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .models import ConceptGraph
from .utils import ensure_dir, log, write_json


@dataclass
class Neo4jConfig:
    uri: str
    username: str
    password: str
    database: str = "neo4j"
    create_constraints: bool = True
    batch_size: int = 200


class Neo4jGraphWriter:
    CONSTRAINT_QUERIES = [
        "CREATE CONSTRAINT source_document_id IF NOT EXISTS FOR (s:SourceDocument) REQUIRE s.source_document IS UNIQUE",
        "CREATE CONSTRAINT document_node_id IF NOT EXISTS FOR (d:DocumentNode) REQUIRE d.source_node_id IS UNIQUE",
        "CREATE CONSTRAINT concept_entity_id IF NOT EXISTS FOR (c:Concept) REQUIRE c.entity_id IS UNIQUE",
        "CREATE CONSTRAINT relation_assertion_id IF NOT EXISTS FOR (a:RelationAssertion) REQUIRE a.relation_id IS UNIQUE",
    ]

    SOURCE_DOCUMENT_QUERY = """
    MERGE (s:SourceDocument {source_document: $source_document})
    SET s.updated_at = datetime()
    """

    DOCUMENT_NODE_QUERY = """
    UNWIND $rows AS row
    MERGE (d:DocumentNode {source_node_id: row.source_node_id})
    SET d.node_id = row.node_id,
        d.parent_id = row.parent_id,
        d.marker = row.marker,
        d.title = row.title,
        d.label = row.label,
        d.level = row.level,
        d.node_kind = row.node_kind,
        d.content = row.content,
        d.path_labels = row.path_labels,
        d.path = row.path,
        d.pdf_page_start = row.pdf_page_start,
        d.pdf_page_end = row.pdf_page_end,
        d.section_node_id = row.section_node_id,
        d.chapter_node_id = row.chapter_node_id,
        d.chunk_index = row.chunk_index,
        d.chunk_count = row.chunk_count,
        d.source_document = row.source_document
    WITH d, row
    MATCH (s:SourceDocument {source_document: row.source_document})
    MERGE (s)-[:HAS_DOC_NODE]->(d)
    """

    DOCUMENT_PARENT_QUERY = """
    UNWIND $rows AS row
    MATCH (p:DocumentNode {source_node_id: row.parent_id})
    MATCH (c:DocumentNode {source_node_id: row.child_id})
    MERGE (p)-[:HAS_CHILD]->(c)
    """

    CONCEPT_QUERY = """
    UNWIND $rows AS row
    MERGE (c:Concept {entity_id: row.entity_id})
    SET c.canonical_name = row.canonical_name,
        c.type = row.type,
        c.aliases = row.aliases,
        c.merged_source_node_ids = row.merged_source_node_ids,
        c.merged_definitions = row.merged_definitions,
        c.merged_raw_entity_ids = row.merged_raw_entity_ids,
        c.source_document = row.source_document
    WITH c, row
    MATCH (s:SourceDocument {source_document: row.source_document})
    MERGE (s)-[:HAS_CONCEPT]->(c)
    """

    MENTION_QUERY = """
    UNWIND $rows AS row
    MATCH (d:DocumentNode {source_node_id: row.source_node_id})
    MATCH (c:Concept {entity_id: row.entity_id})
    MERGE (d)-[:MENTIONS]->(c)
    """

    RELATION_ASSERTION_QUERY = """
    UNWIND $rows AS row
    MERGE (a:RelationAssertion {relation_id: row.relation_id})
    SET a.relation_type = row.relation_type,
        a.head_entity_id = row.head_entity_id,
        a.tail_entity_id = row.tail_entity_id,
        a.head = row.head,
        a.tail = row.tail,
        a.evidences = row.evidences,
        a.source_node_ids = row.source_node_ids,
        a.raw_relation_ids = row.raw_relation_ids,
        a.is_inferred = row.is_inferred,
        a.source_document = row.source_document
    WITH a, row
    MATCH (s:SourceDocument {source_document: row.source_document})
    MERGE (s)-[:HAS_ASSERTION]->(a)
    WITH a, row
    MATCH (h:Concept {entity_id: row.head_entity_id})
    MATCH (t:Concept {entity_id: row.tail_entity_id})
    MERGE (h)-[:ASSERTS_HEAD]->(a)
    MERGE (a)-[:ASSERTS_TAIL]->(t)
    """

    SEMANTIC_RELATION_QUERY = """
    UNWIND $rows AS row
    MATCH (h:Concept {entity_id: row.head_entity_id})
    MATCH (t:Concept {entity_id: row.tail_entity_id})
    MERGE (h)-[r:SEMANTIC_RELATION {relation_id: row.relation_id}]->(t)
    SET r.relation_type = row.relation_type,
        r.evidences = row.evidences,
        r.source_node_ids = row.source_node_ids,
        r.raw_relation_ids = row.raw_relation_ids,
        r.is_inferred = row.is_inferred,
        r.source_document = row.source_document
    """

    SUPPORT_QUERY = """
    UNWIND $rows AS row
    MATCH (d:DocumentNode {source_node_id: row.source_node_id})
    MATCH (a:RelationAssertion {relation_id: row.relation_id})
    MERGE (d)-[:SUPPORTS]->(a)
    """

    def build_payload(
        self,
        source_document: str,
        doc_nodes: list[dict[str, Any]],
        concept_graph: ConceptGraph,
        docnode_mapping: dict[str, dict[str, list[str]]],
    ) -> dict[str, Any]:
        doc_node_rows: list[dict[str, Any]] = []
        doc_parent_rows: list[dict[str, str]] = []
        for node in doc_nodes:
            row = {
                "node_id": node.get("node_id"),
                "source_node_id": node.get("source_node_id"),
                "parent_id": node.get("parent_id"),
                "marker": node.get("marker"),
                "title": node.get("title"),
                "label": node.get("label"),
                "level": node.get("level"),
                "node_kind": node.get("node_kind"),
                "content": node.get("content"),
                "path_labels": node.get("path_labels") or [],
                "path": " > ".join(node.get("path_labels") or []),
                "pdf_page_start": node.get("pdf_page_start"),
                "pdf_page_end": node.get("pdf_page_end"),
                "section_node_id": node.get("section_node_id"),
                "chapter_node_id": node.get("chapter_node_id"),
                "chunk_index": node.get("chunk_index"),
                "chunk_count": node.get("chunk_count"),
                "source_document": source_document,
            }
            doc_node_rows.append(row)
            if row["parent_id"]:
                doc_parent_rows.append(
                    {
                        "parent_id": str(row["parent_id"]),
                        "child_id": str(row["source_node_id"]),
                    }
                )

        concept_rows = [
            {
                "entity_id": entity.entity_id,
                "canonical_name": entity.canonical_name,
                "type": entity.entity_type,
                "aliases": entity.aliases,
                "merged_source_node_ids": entity.merged_source_node_ids,
                "merged_definitions": entity.merged_definitions,
                "merged_raw_entity_ids": entity.merged_raw_entity_ids,
                "source_document": source_document,
            }
            for entity in concept_graph.entities
        ]

        relation_rows = [
            {
                "relation_id": relation.relation_id,
                "head_entity_id": relation.head_entity_id,
                "tail_entity_id": relation.tail_entity_id,
                "head": relation.head,
                "tail": relation.tail,
                "relation_type": relation.relation,
                "evidences": relation.evidences,
                "source_node_ids": relation.source_node_ids,
                "raw_relation_ids": relation.raw_relation_ids,
                "is_inferred": relation.is_inferred,
                "source_document": source_document,
            }
            for relation in concept_graph.relations
        ]

        mention_rows: list[dict[str, str]] = []
        support_rows: list[dict[str, str]] = []
        for source_node_id, mapping in docnode_mapping.items():
            for entity_id in mapping.get("entity_ids", []):
                mention_rows.append(
                    {
                        "source_node_id": source_node_id,
                        "entity_id": entity_id,
                    }
                )
            for relation_id in mapping.get("relation_ids", []):
                support_rows.append(
                    {
                        "source_node_id": source_node_id,
                        "relation_id": relation_id,
                    }
                )

        return {
            "source_document": source_document,
            "doc_nodes": doc_node_rows,
            "doc_parent_edges": doc_parent_rows,
            "concepts": concept_rows,
            "relations": relation_rows,
            "mentions": mention_rows,
            "supports": support_rows,
            "metadata": {
                "doc_node_count": len(doc_node_rows),
                "concept_count": len(concept_rows),
                "relation_count": len(relation_rows),
                "mention_count": len(mention_rows),
                "support_count": len(support_rows),
            },
        }

    def export_payload(self, output_path: Path, payload: dict[str, Any]) -> None:
        ensure_dir(output_path.parent)
        write_json(output_path, payload)

    def write_to_neo4j(self, payload: dict[str, Any], config: Neo4jConfig) -> None:
        try:
            from neo4j import GraphDatabase
            from neo4j.exceptions import ClientError, GqlError
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Neo4j driver is not installed. Run `pip install neo4j`.") from exc

        driver = GraphDatabase.driver(config.uri, auth=(config.username, config.password))
        try:
            try:
                with driver.session(database=config.database) as session:
                    if config.create_constraints:
                        for query in self.CONSTRAINT_QUERIES:
                            session.run(query).consume()

                    session.run(self.SOURCE_DOCUMENT_QUERY, source_document=payload["source_document"]).consume()
                    self._run_batched(session, self.DOCUMENT_NODE_QUERY, payload["doc_nodes"], config.batch_size, "doc nodes")
                    self._run_batched(
                        session,
                        self.DOCUMENT_PARENT_QUERY,
                        payload["doc_parent_edges"],
                        config.batch_size,
                        "document edges",
                    )
                    self._run_batched(session, self.CONCEPT_QUERY, payload["concepts"], config.batch_size, "concepts")
                    self._run_batched(session, self.MENTION_QUERY, payload["mentions"], config.batch_size, "doc mentions")
                    self._run_batched(
                        session,
                        self.RELATION_ASSERTION_QUERY,
                        payload["relations"],
                        config.batch_size,
                        "relation assertions",
                    )
                    self._run_batched(
                        session,
                        self.SEMANTIC_RELATION_QUERY,
                        payload["relations"],
                        config.batch_size,
                        "semantic relations",
                    )
                    self._run_batched(session, self.SUPPORT_QUERY, payload["supports"], config.batch_size, "relation supports")
            except (ClientError, GqlError) as exc:
                code = getattr(exc, "code", "") or getattr(exc, "gql_status", "")
                message = str(exc)
                if "CredentialsExpired" in code or "must be changed before you can use this instance" in message:
                    raise RuntimeError(
                        "Neo4j accepted the default credentials, but this instance requires a password change before any query can run. "
                        "Please change the `neo4j` user's password first, then rerun the import."
                    ) from exc
                raise
        finally:
            driver.close()

    def _run_batched(
        self,
        session: Any,
        query: str,
        rows: list[dict[str, Any]],
        batch_size: int,
        label: str,
    ) -> None:
        if not rows:
            return
        total = 0
        for chunk in chunked(rows, batch_size):
            session.run(query, rows=chunk).consume()
            total += len(chunk)
        log("neo4j", f"Wrote {total} {label}")


def resolve_neo4j_config(
    settings: dict[str, Any],
    write_enabled: bool = False,
    *,
    uri_override: str = "",
    database_override: str = "",
    user_env_override: str = "",
    password_env_override: str = "",
) -> Neo4jConfig | None:
    neo4j_settings = settings.get("neo4j", {})
    enabled = write_enabled or bool(neo4j_settings.get("enabled", False))
    if not enabled:
        return None

    uri_env = str(neo4j_settings.get("uri_env", "NEO4J_URI"))
    user_env = user_env_override or str(neo4j_settings.get("user_env", "NEO4J_USERNAME"))
    password_env = password_env_override or str(neo4j_settings.get("password_env", "NEO4J_PASSWORD"))
    uri = uri_override or os.environ.get(uri_env) or str(neo4j_settings.get("uri", "")).strip()
    username = os.environ.get(user_env) or str(neo4j_settings.get("username", "")).strip()
    password = os.environ.get(password_env) or str(neo4j_settings.get("password", "")).strip()
    database = database_override or str(neo4j_settings.get("database", "neo4j"))

    missing = []
    if not uri:
        missing.append(uri_env)
    if not username:
        missing.append(user_env)
    if not password:
        missing.append(password_env)
    if missing:
        raise RuntimeError(f"Neo4j is enabled but missing required settings/env vars: {', '.join(missing)}")

    return Neo4jConfig(
        uri=uri,
        username=username,
        password=password,
        database=database,
        create_constraints=bool(neo4j_settings.get("create_constraints", True)),
        batch_size=int(neo4j_settings.get("batch_size", 200)),
    )


def chunked(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    if size <= 0:
        size = len(items) or 1
    for index in range(0, len(items), size):
        yield items[index : index + size]
