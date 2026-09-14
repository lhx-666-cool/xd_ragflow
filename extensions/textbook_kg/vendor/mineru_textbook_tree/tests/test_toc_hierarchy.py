from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "mineru_toc_content_tree.py"
SPEC = importlib.util.spec_from_file_location("mineru_toc_content_tree", MODULE_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover
    raise RuntimeError(f"Failed to load module from {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class TocHierarchyTests(unittest.TestCase):
    def test_parse_segment_supports_deep_section_markers(self) -> None:
        entry = MODULE.parse_segment("3.2.1.4.5 深层知识点 218", current_chapter=3)

        self.assertIsNotNone(entry)
        self.assertEqual(entry.marker, "3.2.1.4.5")
        self.assertEqual(entry.level, 5)
        self.assertEqual(entry.title, "深层知识点")
        self.assertEqual(entry.toc_page_start, 218)

    def test_parse_segment_normalizes_bare_chapter_marker(self) -> None:
        entry = MODULE.parse_segment("2章应用层 54", current_chapter=1)

        self.assertIsNotNone(entry)
        self.assertEqual(entry.marker, "第2章")
        self.assertEqual(entry.level, 1)
        self.assertEqual(entry.title, "应用层")
        self.assertEqual(entry.toc_page_start, 54)

    def test_parse_segment_recovers_missing_chapter_number(self) -> None:
        entry = MODULE.parse_segment("第章引论1", current_chapter=None)

        self.assertIsNotNone(entry)
        self.assertEqual(entry.marker, "第1章")
        self.assertEqual(entry.level, 1)
        self.assertEqual(entry.title, "引论")
        self.assertEqual(entry.toc_page_start, 1)

    def test_parse_segment_ignores_protocol_version_numbers(self) -> None:
        entry = MODULE.parse_segment("802.11 MAC 协议 350", current_chapter=7)

        self.assertIsNone(entry)

    def test_is_memory_pressure_error_matches_windows_pagefile_failure(self) -> None:
        self.assertTrue(MODULE.is_memory_pressure_error("页面文件太小，无法完成操作。 (os error 1455)"))
        self.assertTrue(MODULE.is_memory_pressure_error("Paging file is too small to complete the operation."))
        self.assertFalse(MODULE.is_memory_pressure_error("ordinary parse failure"))

    def test_split_toc_segments_keeps_titles_containing_directory_word(self) -> None:
        segments = MODULE.split_toc_segments("2.4DNS:因特网的目录服务 83")

        self.assertEqual(segments, ["2.4DNS:因特网的目录服务 83"])

    def test_split_toc_segments_normalizes_chinese_chapters_and_section_signs(self) -> None:
        segments = MODULE.split_toc_segments(
            "第章 点的运动 3 § 1-1 点的直线运动 3 第二章 刚体的基本运动 32 § 2-1 刚体的平动 32"
        )

        self.assertEqual(
            segments,
            [
                "第章 点的运动 3",
                "1.1 点的直线运动 3",
                "第2章 刚体的基本运动 32",
                "2.1 刚体的平动 32",
            ],
        )

    def test_parse_toc_entries_builds_multiple_chapters_from_scanned_catalog(self) -> None:
        entries = MODULE.parse_toc_entries(
            {
                10: [
                    "目录 第章 点的运动 3 § 1-1 点的直线运动 3 "
                    "第二章 刚体的基本运动 32 § 2-1 刚体的平动 32"
                ]
            },
            [10],
        )

        self.assertEqual([entry.marker for entry in entries], ["第1章", "1.1", "第2章", "2.1"])

    def test_parse_toc_entries_skips_repeated_chapters_inside_answer_appendix(self) -> None:
        entries = MODULE.parse_toc_entries(
            {
                10: [
                    "第十章 振动 232 附录一 运动学和动力学习题答案 272 "
                    "第一章 点的运动 272 第二章 刚体的基本运动 272 "
                    "附录二 国际制词冠表 280"
                ]
            },
            [10],
        )

        self.assertEqual([entry.marker for entry in entries], ["第10章", "附录一", "附录二"])

    def test_split_toc_segments_handles_concatenated_nested_markers(self) -> None:
        segments = MODULE.split_toc_segments("1.1什么是操作系统1.1.1 作为扩展机器的操作系统...21.1.2 作为资源管理者的操作系统......3")

        self.assertEqual(
            segments,
            [
                "1.1什么是操作系统",
                "1.1.1 作为扩展机器的操作系统 2",
                "1.1.2 作为资源管理者的操作系统 3",
            ],
        )

    def test_parse_segment_repairs_truncated_two_digit_chapter_marker(self) -> None:
        entry = MODULE.parse_segment("第1章 参考书与文献 584", current_chapter=12)

        self.assertIsNotNone(entry)
        self.assertEqual(entry.marker, "第13章")
        self.assertEqual(entry.level, 1)
        self.assertEqual(entry.title, "参考书与文献")
        self.assertEqual(entry.toc_page_start, 584)

    def test_parse_segment_repairs_zero_chapter_marker_to_ten(self) -> None:
        entry = MODULE.parse_segment("第0章 UNIX 与 Linux 403", current_chapter=9)

        self.assertIsNotNone(entry)
        self.assertEqual(entry.marker, "第10章")
        self.assertEqual(entry.level, 1)
        self.assertEqual(entry.title, "UNIX 与 Linux")
        self.assertEqual(entry.toc_page_start, 403)

    def test_split_toc_segments_preserves_two_digit_chapter_markers(self) -> None:
        segments = MODULE.split_toc_segments("第11章 实例研究: Windows 487")

        self.assertEqual(segments, ["第11章 实例研究: Windows 487"])

    def test_parse_segment_trims_title_digits_from_oversized_section_component(self) -> None:
        entry = MODULE.parse_segment("11.1.120世纪80年代:MS-DOS 487", current_chapter=11)

        self.assertIsNotNone(entry)
        self.assertEqual(entry.marker, "11.1.1")
        self.assertEqual(entry.level, 3)
        self.assertEqual(entry.title, "20世纪80年代:MS-DOS")
        self.assertEqual(entry.toc_page_start, 487)

    def test_split_hidden_gap_entries_recovers_missing_sibling_from_title_tail(self) -> None:
        entries = [
            MODULE.TocEntry(
                marker="13.1.10",
                title="实例研究1:UNIX、Linux和Android 1 3.1 1 实例研究2:Windows8",
                level=3,
                toc_page_start=588,
            ),
            MODULE.TocEntry(marker="13.1.12", title="操作系统设计", level=3, toc_page_start=589),
        ]

        repaired = MODULE.split_hidden_gap_entries(entries)

        self.assertEqual([entry.marker for entry in repaired], ["13.1.10", "13.1.11", "13.1.12"])
        self.assertEqual(repaired[0].title, "实例研究1:UNIX、Linux和Android")
        self.assertEqual(repaired[1].title, "实例研究2:Windows8")

    def test_build_tree_uses_marker_ancestry_when_intermediate_parent_is_missing(self) -> None:
        entries = [
            MODULE.TocEntry(marker="第3章", title="运输层", level=1, toc_page_start=120),
            MODULE.TocEntry(marker="3.1", title="概述", level=2, toc_page_start=121),
            MODULE.TocEntry(marker="3.2.1", title="多路复用与多路分解", level=3, toc_page_start=130),
        ]

        roots = MODULE.build_tree(entries, page_offset=0)

        self.assertEqual(len(roots), 1)
        chapter = roots[0]
        self.assertEqual([child.marker for child in chapter.children], ["3.1", "3.2"])
        self.assertEqual(chapter.children[0].children, [])
        self.assertEqual([child.marker for child in chapter.children[1].children], ["3.2.1"])


if __name__ == "__main__":
    unittest.main()
