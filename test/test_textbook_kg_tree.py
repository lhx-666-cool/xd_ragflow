from __future__ import annotations

import json
import unittest

from api.services.textbook_kg_tree import TextbookKgTreeError, prepare_textbook_tree


class TextbookKgTreeTests(unittest.TestCase):
    def test_normalizes_tree_for_frontend(self) -> None:
        payload = {
            "book_title": "Computer Networks",
            "toc_pages_pdf": [3, 4],
            "chapters": [
                {
                    "marker": "1",
                    "title": "Foundations",
                    "level": 1,
                    "pdf_page_start": 10,
                    "pdf_page_end": 20,
                    "content": "  Chapter   overview.  ",
                    "children": [
                        {
                            "marker": "1.1",
                            "title": "Protocols",
                            "level": 2,
                            "content": "TCP and UDP",
                            "children": [],
                        }
                    ],
                }
            ],
        }
        result = prepare_textbook_tree(json.dumps(payload).encode())

        self.assertEqual("ragflow-textbook-tree/v1", result["schema_version"])
        self.assertEqual(2, result["node_count"])
        self.assertEqual(2, result["max_depth"])
        chapter = result["chapters"][0]
        self.assertEqual("1 Foundations", chapter["label"])
        self.assertEqual("Chapter overview.", chapter["content_preview"])
        self.assertEqual(1, chapter["child_count"])
        self.assertTrue(chapter["id"].startswith("chapter-"))

    def test_truncates_content_preview(self) -> None:
        payload = {
            "book_title": "Book",
            "chapters": [{"title": "A", "content": "abcdef", "children": []}],
        }
        result = prepare_textbook_tree(json.dumps(payload).encode(), preview_chars=4)
        self.assertEqual("abc…", result["chapters"][0]["content_preview"])
        self.assertEqual(6, result["chapters"][0]["content_length"])

    def test_rejects_invalid_and_unbounded_trees(self) -> None:
        with self.assertRaises(TextbookKgTreeError):
            prepare_textbook_tree(b"not-json")
        with self.assertRaises(TextbookKgTreeError):
            prepare_textbook_tree(json.dumps({"chapters": [{"children": [{"children": []}]}]}).encode(), max_depth=1)
        with self.assertRaises(TextbookKgTreeError):
            prepare_textbook_tree(json.dumps({"chapters": [{"children": []}, {"children": []}]}).encode(), max_nodes=1)


if __name__ == "__main__":
    unittest.main()
