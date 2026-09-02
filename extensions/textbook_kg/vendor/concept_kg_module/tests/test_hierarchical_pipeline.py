from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.embedder import HashingEmbeddingBackend
from src.extractor import BaseExtractionClient, EntityRelationExtractor, MockLLMClient
from src.loader import DocumentGraphLoader
from src.models import ExtractionBatch, RawEntity, RawRelation
from src.pipeline import build_concept_graph_from_batches
from src.utils import safe_filename


SCHEMA = {
    "entity_types": {
        "Concept": "Concept",
        "Method": "Method",
    },
    "relation_types": {
        "is_a": "is_a",
        "used_for": "used_for",
        "prerequisite_of": "prerequisite_of",
    },
}


def base_settings(chapter_completion: bool = False) -> dict:
    return {
        "cleaning": {
            "drop_invalid_types": True,
            "drop_empty_definitions": False,
            "require_relation_evidence": True,
            "drop_self_relations": True,
        },
        "merging": {
            "embedding_backend": "hashing",
            "embedding_dim": 64,
            "similarity_threshold": 0.99,
            "lexical_similarity_threshold": 0.6,
            "exact_name_merge": True,
            "merge_same_type_only": False,
        },
        "inference": {
            "enabled": False,
        },
        "chapter_completion": {
            "enabled": chapter_completion,
            "max_candidate_pairs_per_chapter": 20,
            "max_relations_per_chapter": 5,
            "max_context_chars": 4000,
        },
    }


class FakeChapterCompletionClient(BaseExtractionClient):
    def run(self, prompt: str, node) -> str:  # noqa: ANN001
        return json.dumps(
            {
                "relations": [
                    {
                        "candidate_id": "cand_0001",
                        "head": "Alpha",
                        "relation": "prerequisite_of",
                        "tail": "Gamma",
                        "evidence": "Alpha prepares Gamma",
                    },
                    {
                        "candidate_id": "cand_9999",
                        "head": "Alpha",
                        "relation": "used_for",
                        "tail": "Invented Entity",
                        "evidence": "Should be rejected",
                    },
                ]
            }
        )


class HierarchicalPipelineTests(unittest.TestCase):
    def test_loader_extracts_only_minimal_section_chunks(self) -> None:
        payload = {
            "book_title": "Fixture Book",
            "chapters": [
                {
                    "marker": "1",
                    "title": "Chapter",
                    "label": "1 Chapter",
                    "level": 1,
                    "content": "Chapter overview should stay structural.",
                    "children": [
                        {
                            "marker": "1.1",
                            "title": "Parent Section",
                            "label": "1.1 Parent Section",
                            "level": 2,
                            "content": "Parent content should not be extracted when child content exists.",
                            "children": [
                                {
                                    "marker": "1.1.1",
                                    "title": "Leaf Section",
                                    "label": "1.1.1 Leaf Section",
                                    "level": 3,
                                    "content": "Alpha is a Beta. Gamma is used for Alpha.",
                                    "children": [],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "content_tree.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = DocumentGraphLoader(pipeline="hierarchical", semantic_chunk_chars=200).load(path)

        self.assertEqual([node.node_kind for node in loaded.nodes], ["semantic_chunk"])
        self.assertEqual(loaded.nodes[0].title, "Leaf Section")
        self.assertTrue(loaded.nodes[0].section_node_id)
        self.assertEqual(loaded.nodes[0].parent_id, loaded.nodes[0].section_node_id)
        self.assertGreater(len(loaded.document_nodes), len(loaded.nodes))

    def test_loader_splits_long_leaf_section_into_stable_chunks(self) -> None:
        content = " ".join(f"Sentence {index} is deliberately long." for index in range(30))
        payload = {
            "book_title": "Fixture Book",
            "chapters": [
                {
                    "marker": "1",
                    "title": "Chapter",
                    "label": "1 Chapter",
                    "level": 1,
                    "content": "",
                    "children": [
                        {
                            "marker": "1.1",
                            "title": "Leaf",
                            "label": "1.1 Leaf",
                            "level": 2,
                            "content": content,
                            "children": [],
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "content_tree.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = DocumentGraphLoader(
                pipeline="hierarchical",
                semantic_chunk_chars=120,
                semantic_chunk_min_chars=40,
            ).load(path)

        self.assertGreater(len(loaded.nodes), 1)
        self.assertTrue(all(node.node_kind == "semantic_chunk" for node in loaded.nodes))
        self.assertEqual({node.chunk_count for node in loaded.nodes}, {len(loaded.nodes)})
        self.assertEqual({node.parent_id for node in loaded.nodes}, {loaded.nodes[0].section_node_id})

    def test_mock_extraction_builds_section_fragments_then_book_graph(self) -> None:
        payload = {
            "book_title": "Fixture Book",
            "chapters": [
                {
                    "marker": "1",
                    "title": "Chapter",
                    "label": "1 Chapter",
                    "level": 1,
                    "content": "",
                    "children": [
                        {
                            "marker": "1.1",
                            "title": "Leaf",
                            "label": "1.1 Leaf",
                            "level": 2,
                            "content": "Alpha is a Beta. Gamma is used for Alpha.",
                            "children": [],
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            doc_path = root / "content_tree.json"
            prompt_path = root / "prompt.txt"
            doc_path.write_text(json.dumps(payload), encoding="utf-8")
            prompt_path.write_text("${CONTENT}", encoding="utf-8")
            loaded = DocumentGraphLoader(pipeline="hierarchical", semantic_chunk_chars=500).load(doc_path)
            extractor = EntityRelationExtractor(
                client=MockLLMClient(),
                prompt_template_path=prompt_path,
                schema=SCHEMA,
            )
            batches = extractor.extract_nodes(loaded.nodes)

        result = build_concept_graph_from_batches(
            pipeline="hierarchical",
            loaded_document=loaded,
            batches=batches,
            schema=SCHEMA,
            settings=base_settings(chapter_completion=False),
            embedder=HashingEmbeddingBackend(dim=64),
            source_document="Fixture Book",
        )
        entity_names = {entity.canonical_name for entity in result.concept_graph.entities}
        relation_types = {relation.relation for relation in result.concept_graph.relations}
        self.assertIn("Alpha", entity_names)
        self.assertIn("Beta", entity_names)
        self.assertIn("is_a", relation_types)
        self.assertEqual(len(result.section_fragments), 1)

    def test_chapter_completion_accepts_only_existing_cross_section_candidates(self) -> None:
        payload = {
            "book_title": "Fixture Book",
            "chapters": [
                {
                    "marker": "1",
                    "title": "Chapter",
                    "label": "1 Chapter",
                    "level": 1,
                    "content": "",
                    "children": [
                        {
                            "marker": "1.1",
                            "title": "First",
                            "label": "1.1 First",
                            "level": 2,
                            "content": "Alpha is a Beta.",
                            "children": [],
                        },
                        {
                            "marker": "1.2",
                            "title": "Second",
                            "label": "1.2 Second",
                            "level": 2,
                            "content": "Gamma appears here.",
                            "children": [],
                        },
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            doc_path = root / "content_tree.json"
            prompt_path = root / "chapter_prompt.txt"
            doc_path.write_text(json.dumps(payload), encoding="utf-8")
            prompt_path.write_text(
                "${CHAPTER_NODE_ID}\n${SECTION_CONTEXT}\n${CANDIDATE_PAIRS}\n${MAX_RELATIONS}",
                encoding="utf-8",
            )
            loaded = DocumentGraphLoader(pipeline="hierarchical", semantic_chunk_chars=500).load(doc_path)

            first_chunk, second_chunk = loaded.nodes
            batches = [
                ExtractionBatch(
                    source_node_id=first_chunk.source_node_id,
                    entities=[
                        RawEntity(f"{first_chunk.source_node_id}::e001", "Alpha", [], "Concept", "Alpha", first_chunk.source_node_id),
                        RawEntity(f"{first_chunk.source_node_id}::e002", "Beta", [], "Concept", "Beta", first_chunk.source_node_id),
                    ],
                    relations=[
                        RawRelation(
                            f"{first_chunk.source_node_id}::r001",
                            "Alpha",
                            "is_a",
                            "Beta",
                            first_chunk.source_node_id,
                            "Alpha is a Beta.",
                        )
                    ],
                ),
                ExtractionBatch(
                    source_node_id=second_chunk.source_node_id,
                    entities=[
                        RawEntity(f"{second_chunk.source_node_id}::e001", "Gamma", [], "Concept", "Gamma", second_chunk.source_node_id)
                    ],
                    relations=[],
                ),
            ]

            result = build_concept_graph_from_batches(
                pipeline="hierarchical",
                loaded_document=loaded,
                batches=batches,
                schema=SCHEMA,
                settings=base_settings(chapter_completion=True),
                embedder=HashingEmbeddingBackend(dim=64),
                source_document="Fixture Book",
                completion_client=FakeChapterCompletionClient(),
                completion_prompt_path=prompt_path,
            )

        completed = [
            relation
            for relation in result.concept_graph.relations
            if relation.relation == "prerequisite_of"
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].head, "Alpha")
        self.assertEqual(completed[0].tail, "Gamma")
        self.assertTrue(completed[0].is_inferred)
        self.assertEqual(completed[0].source_node_ids, [result.chapter_completions[0]["chapter_node_id"]])

    def test_flat_pipeline_remains_available(self) -> None:
        loaded = DocumentGraphLoader(pipeline="flat").load(
            self._write_tree(
                {
                    "book_title": "Fixture Book",
                    "chapters": [
                        {
                            "marker": "1",
                            "title": "Chapter",
                            "label": "1 Chapter",
                            "level": 1,
                            "content": "Alpha is a Beta.",
                            "children": [],
                        }
                    ],
                }
            )
        )
        self.assertTrue(any(node.node_kind == "chapter" for node in loaded.nodes))

    def test_safe_filename_keeps_book_title_portable(self) -> None:
        self.assertEqual(safe_filename(" Network: A/B?* "), "Network_ A_B")
        self.assertEqual(safe_filename("CON"), "_CON")
        self.assertEqual(safe_filename("", fallback="book"), "book")

    def test_main_creates_book_title_timestamp_output_subdir(self) -> None:
        payload = {
            "book_title": "Fixture Book",
            "chapters": [
                {
                    "marker": "1",
                    "title": "Chapter",
                    "label": "1 Chapter",
                    "level": 1,
                    "content": "",
                    "children": [
                        {
                            "marker": "1.1",
                            "title": "Leaf",
                            "label": "1.1 Leaf",
                            "level": 2,
                            "content": "Alpha is a Beta.",
                            "children": [],
                        }
                    ],
                }
            ],
        }
        settings = base_settings(chapter_completion=False)
        settings["llm"] = {"backend": "mock"}
        settings["extraction"] = {
            "pipeline": "hierarchical",
            "include_structural_nodes": True,
            "include_paragraph_nodes": True,
            "paragraph_min_chars": 80,
            "semantic_chunk_chars": 500,
            "semantic_chunk_min_chars": 100,
            "semantic_chunk_overlap_chars": 0,
            "max_nodes": 0,
        }
        settings["output"] = {
            "create_run_subdir": True,
            "run_dir_template": "{book_title}_{timestamp}",
            "timestamp_format": "%Y%m%d_%H%M%S",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            doc_path = root / "content_tree.json"
            schema_path = root / "schema.yaml"
            settings_path = root / "settings.yaml"
            output_root = root / "outputs"
            doc_path.write_text(json.dumps(payload), encoding="utf-8")
            schema_path.write_text(json.dumps(SCHEMA), encoding="utf-8")
            settings_path.write_text(json.dumps(settings), encoding="utf-8")

            project_root = Path(__file__).resolve().parents[1]
            subprocess.run(
                [
                    sys.executable,
                    str(project_root / "main.py"),
                    "--doc_graph",
                    str(doc_path),
                    "--output_dir",
                    str(output_root),
                    "--schema",
                    str(schema_path),
                    "--settings",
                    str(settings_path),
                    "--llm-backend",
                    "mock",
                    "--pipeline",
                    "hierarchical",
                ],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )

            run_dirs = [path for path in output_root.iterdir() if path.is_dir()]
            self.assertEqual(len(run_dirs), 1)
            run_dir = run_dirs[0]
            self.assertRegex(run_dir.name, r"^Fixture Book_\d{8}_\d{6}$")
            self.assertTrue((run_dir / "concept_kg.json").exists())
            self.assertTrue((run_dir / "document_nodes.json").exists())
            self.assertTrue((run_dir / "run.log").exists())
            self.assertFalse((output_root / "concept_kg.json").exists())

    def test_retry_script_rebuilds_with_shared_hierarchical_pipeline(self) -> None:
        payload = {
            "book_title": "Fixture Book",
            "chapters": [
                {
                    "marker": "1",
                    "title": "Chapter",
                    "label": "1 Chapter",
                    "level": 1,
                    "content": "",
                    "children": [
                        {
                            "marker": "1.1",
                            "title": "Leaf",
                            "label": "1.1 Leaf",
                            "level": 2,
                            "content": "Alpha is a Beta.",
                            "children": [],
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            doc_path = root / "content_tree.json"
            kg_dir = root / "kg"
            kg_dir.mkdir()
            doc_path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = DocumentGraphLoader(pipeline="hierarchical", semantic_chunk_chars=500).load(doc_path)
            chunk = loaded.nodes[0]
            batch = ExtractionBatch(
                source_node_id=chunk.source_node_id,
                entities=[
                    RawEntity(f"{chunk.source_node_id}::e001", "Alpha", [], "Concept", "Alpha", chunk.source_node_id),
                    RawEntity(f"{chunk.source_node_id}::e002", "Beta", [], "Concept", "Beta", chunk.source_node_id),
                ],
                relations=[
                    RawRelation(
                        f"{chunk.source_node_id}::r001",
                        "Alpha",
                        "is_a",
                        "Beta",
                        chunk.source_node_id,
                        "Alpha is a Beta.",
                    )
                ],
            )
            (kg_dir / "raw_extraction_batches.json").write_text(
                json.dumps([batch.to_dict()], ensure_ascii=False),
                encoding="utf-8",
            )
            (kg_dir / "extractable_doc_nodes.json").write_text(
                json.dumps([node.to_dict() for node in loaded.nodes], ensure_ascii=False),
                encoding="utf-8",
            )
            (kg_dir / "document_nodes.json").write_text(
                json.dumps([node.to_dict() for node in loaded.document_nodes], ensure_ascii=False),
                encoding="utf-8",
            )
            schema_path = root / "schema.yaml"
            settings_path = root / "settings.yaml"
            schema_path.write_text(json.dumps(SCHEMA), encoding="utf-8")
            settings_path.write_text(json.dumps(base_settings(chapter_completion=False)), encoding="utf-8")

            project_root = Path(__file__).resolve().parents[1]
            subprocess.run(
                [
                    sys.executable,
                    str(project_root / "scripts" / "retry_failed_concept_kg.py"),
                    "--kg-output-dir",
                    str(kg_dir),
                    "--schema",
                    str(schema_path),
                    "--settings",
                    str(settings_path),
                    "--llm-backend",
                    "mock",
                    "--pipeline",
                    "hierarchical",
                ],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertTrue((kg_dir / "concept_kg.json").exists())
            self.assertTrue((kg_dir / "section_kg_fragments.json").exists())
            self.assertTrue((kg_dir / "chapter_relation_completions.json").exists())
            concept_graph = json.loads((kg_dir / "concept_kg.json").read_text(encoding="utf-8"))
            self.assertEqual(concept_graph["metadata"]["entity_count"], 2)

    def _write_tree(self, payload: dict) -> Path:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "content_tree.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
