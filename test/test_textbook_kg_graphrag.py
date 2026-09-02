from __future__ import annotations

import hashlib
import json
import unittest

from api.services.textbook_kg_graphrag import (
    TextbookKgGraphRagError,
    prepare_textbook_graph,
)


def _adapter() -> dict:
    return {
        "schema_version": "ragflow-textbook-kg/v1",
        "knowledge_graph": {
            "nodes": [
                {
                    "id": "e1",
                    "entity_name": "TCP",
                    "entity_type": "Protocol",
                    "description": "A transport protocol",
                    "source_id": ["chapter-1"],
                },
                {
                    "id": "e2",
                    "entity_name": "IP",
                    "entity_type": "Protocol",
                    "description": "A network protocol",
                    "source_id": ["chapter-2"],
                },
            ],
            "edges": [
                {
                    "src_id": "e1",
                    "tgt_id": "e2",
                    "source": "TCP",
                    "target": "IP",
                    "relation": "RUNS_OVER",
                    "description": "TCP runs over IP",
                    "keywords": ["RUNS_OVER"],
                    "source_id": ["section-1.1"],
                    "weight": 1,
                }
            ],
        },
    }


class TextbookKgGraphRagTest(unittest.TestCase):
    def test_prepares_native_graph_with_document_scoped_provenance(self):
        content = json.dumps(_adapter()).encode("utf-8")
        prepared = prepare_textbook_graph(
            content,
            doc_id="doc-1",
            expected_sha256=hashlib.sha256(content).hexdigest(),
        )

        self.assertEqual(["doc-1"], prepared.graph.graph["source_id"])
        self.assertEqual(["doc-1"], prepared.graph.nodes["TCP"]["source_id"])
        self.assertEqual(["chapter-1"], prepared.graph.nodes["TCP"]["textbook_source_ids"])
        edge = prepared.graph.get_edge_data("TCP", "IP")
        self.assertEqual(["doc-1"], edge["source_id"])
        self.assertEqual(["section-1.1"], edge["textbook_source_ids"])
        self.assertEqual(["RUNS_OVER"], edge["keywords"])

    def test_rejects_checksum_schema_and_missing_endpoints(self):
        content = json.dumps(_adapter()).encode("utf-8")
        with self.assertRaisesRegex(TextbookKgGraphRagError, "checksum"):
            prepare_textbook_graph(content, doc_id="doc-1", expected_sha256="0" * 64)

        payload = _adapter()
        payload["schema_version"] = "unknown"
        with self.assertRaisesRegex(TextbookKgGraphRagError, "schema"):
            prepare_textbook_graph(json.dumps(payload).encode(), doc_id="doc-1")

        payload = _adapter()
        payload["knowledge_graph"]["edges"][0]["tgt_id"] = "missing"
        with self.assertRaisesRegex(TextbookKgGraphRagError, "endpoint"):
            prepare_textbook_graph(json.dumps(payload).encode(), doc_id="doc-1")

    def test_combines_duplicate_edges_without_losing_relation_labels(self):
        payload = _adapter()
        duplicate = dict(payload["knowledge_graph"]["edges"][0])
        duplicate.update(
            {
                "relation": "DEPENDS_ON",
                "description": "TCP depends on IP",
                "keywords": ["DEPENDS_ON"],
                "source_id": ["section-1.2"],
            }
        )
        payload["knowledge_graph"]["edges"].append(duplicate)

        prepared = prepare_textbook_graph(json.dumps(payload).encode(), doc_id="doc-1")
        edge = prepared.graph.get_edge_data("TCP", "IP")
        self.assertEqual(["DEPENDS_ON", "RUNS_OVER"], edge["keywords"])
        self.assertEqual(["DEPENDS_ON", "RUNS_OVER"], edge["relation_types"])
        self.assertEqual(["section-1.1", "section-1.2"], edge["textbook_source_ids"])
        self.assertEqual(2.0, edge["weight"])

    def test_rejects_oversized_artifact(self):
        with self.assertRaisesRegex(TextbookKgGraphRagError, "too large"):
            prepare_textbook_graph(b"x" * 65, doc_id="doc-1", max_bytes=64)


if __name__ == "__main__":
    unittest.main()
