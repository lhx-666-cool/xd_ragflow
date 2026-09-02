from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit_resource_trees.py"
SPEC = importlib.util.spec_from_file_location("audit_resource_trees", MODULE_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover
    raise RuntimeError(f"Failed to load module from {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_tree(tmp: Path, chapters: list[dict]) -> Path:
    output_dir = tmp / "book_tree"
    output_dir.mkdir()
    (output_dir / "content_tree.json").write_text(
        json.dumps({"book_title": "book", "chapters": chapters}, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "content_tree_graph.html").write_text("<html></html>", encoding="utf-8")
    return output_dir


class AuditResourceTreesTests(unittest.TestCase):
    def test_detects_wrong_parent_mounting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            output_dir = write_tree(
                Path(tmp_name),
                [
                    {
                        "marker": "第5章",
                        "title": "网络层",
                        "level": 1,
                        "children": [
                            {"marker": "5.2", "title": "路由选择", "level": 2, "children": []},
                            {"marker": "5.2.1", "title": "链路状态", "level": 3, "children": []},
                        ],
                    }
                ],
            )

            audit = MODULE.audit_output_dir(output_dir)
            codes = [issue.code for issue in audit.issues]
            self.assertIn("wrong-parent", codes)

    def test_warns_about_io_ocr_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            output_dir = write_tree(
                Path(tmp_name),
                [
                    {
                        "marker": "第7章",
                        "title": "设备管理",
                        "level": 1,
                        "children": [
                            {"marker": "7.1", "title": "1/O系统", "level": 2, "children": []},
                        ],
                    }
                ],
            )

            audit = MODULE.audit_output_dir(output_dir)
            codes = [issue.code for issue in audit.issues]
            self.assertIn("ocr-io-token", codes)

    def test_allows_chapter_zero_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            output_dir = write_tree(
                Path(tmp_name),
                [
                    {
                        "marker": "第0章",
                        "title": "读者指南",
                        "level": 1,
                        "children": [
                            {"marker": "0.1", "title": "本书概述", "level": 2, "children": []},
                        ],
                    }
                ],
            )

            audit = MODULE.audit_output_dir(output_dir)
            self.assertNotIn("invalid-marker-zero", [issue.code for issue in audit.issues])

    def test_does_not_flag_legal_tenth_section_as_glued_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            output_dir = write_tree(
                Path(tmp_name),
                [
                    {
                        "marker": "第9章",
                        "title": "分布式系统",
                        "level": 1,
                        "children": [
                            {"marker": "9.1", "title": "概述", "level": 2, "children": []},
                            {"marker": "9.10", "title": "死锁处理", "level": 2, "children": []},
                        ],
                    }
                ],
            )

            audit = MODULE.audit_output_dir(output_dir)
            self.assertNotIn("possible-glued-marker", [issue.code for issue in audit.issues])


if __name__ == "__main__":
    unittest.main()
