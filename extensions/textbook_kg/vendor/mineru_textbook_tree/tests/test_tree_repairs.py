from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "mineru_toc_content_tree.py"
SPEC = importlib.util.spec_from_file_location("mineru_toc_content_tree_tree_repairs", MODULE_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover
    raise RuntimeError(f"Failed to load module from {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class TreeRepairTests(unittest.TestCase):
    def test_strip_trailing_symbol_noise_removes_ocr_garbage(self) -> None:
        self.assertEqual(MODULE.strip_trailing_symbol_noise("标准库名字空间 +=++-++"), "标准库名字空间")
        self.assertEqual(MODULE.strip_trailing_symbol_noise("C++"), "C++")

    def test_repair_titles_from_content_headings_prefers_heading_text(self) -> None:
        node = MODULE.TreeNode(marker="3.6", title="输人", level=2, toc_page_start=42, pdf_page_start=71, pdf_page_end=73)
        node.content = "3.6 输入\n\n这里是正文。"
        changed = MODULE.repair_titles_from_content_headings([node])
        self.assertTrue(changed)
        self.assertEqual(node.title, "输入")

    def test_repairs_missing_intermediate_node_from_malformed_numeric_blob(self) -> None:
        entries = [
            MODULE.TocEntry(marker="第4章", title="指令系统与汇编语言", level=1, toc_page_start=96),
            MODULE.TocEntry(marker="4.1", title="指令格式", level=2, toc_page_start=96),
            MODULE.TocEntry(marker="4.2", title="寻址方式", level=2, toc_page_start=102),
            MODULE.TocEntry(marker="4.38086", title="(88)的指令系统", level=2, toc_page_start=109),
            MODULE.TocEntry(marker="4.3.1", title="传送指令", level=3, toc_page_start=109),
            MODULE.TocEntry(marker="4.3.2", title="算术运算指令", level=3, toc_page_start=114),
            MODULE.TocEntry(marker="4.4", title="汇编语言及其程序设计", level=2, toc_page_start=131),
        ]

        roots = MODULE.build_tree(entries, page_offset=0)
        chapter = roots[0]

        self.assertEqual([child.marker for child in chapter.children], ["4.1", "4.2", "4.3", "4.4"])
        repaired = chapter.children[2]
        self.assertEqual(repaired.title, "8086(88)的指令系统")
        self.assertEqual([child.marker for child in repaired.children], ["4.3.1", "4.3.2"])

    def test_reparents_misplaced_numeric_siblings_under_existing_parent(self) -> None:
        chapter = MODULE.TreeNode(marker="第5章", title="网络层:控制平面", level=1, toc_page_start=260, pdf_page_start=260, pdf_page_end=302)
        sec_51 = MODULE.TreeNode(marker="5.1", title="概述", level=2, toc_page_start=260, pdf_page_start=260, pdf_page_end=262, parent=chapter)
        sec_52 = MODULE.TreeNode(marker="5.2", title="路由选择算法", level=2, toc_page_start=262, pdf_page_start=262, pdf_page_end=302, parent=chapter)
        sec_521 = MODULE.TreeNode(marker="5.2.1", title="链路状态路由选择算法", level=3, toc_page_start=264, pdf_page_start=264, pdf_page_end=266, parent=chapter)
        sec_522 = MODULE.TreeNode(marker="5.2.2", title="距离向量路由选择算法", level=3, toc_page_start=266, pdf_page_start=266, pdf_page_end=272, parent=chapter)
        sec_53 = MODULE.TreeNode(marker="5.3", title="因特网中自治系统内部的路由选择:OSPF", level=2, toc_page_start=272, pdf_page_start=272, pdf_page_end=274, parent=chapter)
        chapter.children = [sec_51, sec_52, sec_521, sec_522, sec_53]

        MODULE.reparent_misplaced_numeric_siblings([chapter])

        self.assertEqual([child.marker for child in chapter.children], ["5.1", "5.2", "5.3"])
        self.assertEqual([child.marker for child in sec_52.children], ["5.2.1", "5.2.2"])
        self.assertIs(sec_521.parent, sec_52)
        self.assertIs(sec_522.parent, sec_52)

    def test_normalize_tree_structure_repairs_existing_malformed_tree(self) -> None:
        chapter = MODULE.TreeNode(marker="第4章", title="指令系统与汇编语言", level=1, toc_page_start=96, pdf_page_start=96, pdf_page_end=150)
        sec_41 = MODULE.TreeNode(marker="4.1", title="指令格式", level=2, toc_page_start=96, pdf_page_start=96, pdf_page_end=102, parent=chapter)
        sec_42 = MODULE.TreeNode(marker="4.2", title="寻址方式", level=2, toc_page_start=102, pdf_page_start=102, pdf_page_end=109, parent=chapter)
        bad_43 = MODULE.TreeNode(marker="4.38086", title="(88)的指令系统", level=2, toc_page_start=109, pdf_page_start=109, pdf_page_end=150, parent=chapter)
        sec_431 = MODULE.TreeNode(marker="4.3.1", title="传送指令", level=3, toc_page_start=109, pdf_page_start=109, pdf_page_end=114, parent=chapter)
        sec_432 = MODULE.TreeNode(marker="4.3.2", title="算术运算指令", level=3, toc_page_start=114, pdf_page_start=114, pdf_page_end=119, parent=chapter)
        sec_44 = MODULE.TreeNode(marker="4.4", title="汇编语言及其程序设计", level=2, toc_page_start=131, pdf_page_start=131, pdf_page_end=146, parent=chapter)
        chapter.children = [sec_41, sec_42, bad_43, sec_431, sec_432, sec_44]

        MODULE.normalize_tree_structure([chapter])

        self.assertEqual([child.marker for child in chapter.children], ["4.1", "4.2", "4.3", "4.4"])
        repaired = chapter.children[2]
        self.assertEqual(repaired.title, "8086(88)的指令系统")
        self.assertEqual([child.marker for child in repaired.children], ["4.3.1", "4.3.2"])

    def test_normalize_tree_structure_repairs_duplicate_numeric_siblings(self) -> None:
        chapter = MODULE.TreeNode(marker="第6章", title="链路层和局域网", level=1, toc_page_start=303, pdf_page_start=303, pdf_page_end=355)
        sec_62 = MODULE.TreeNode(marker="6.2", title="差错检测和纠正技术", level=2, toc_page_start=307, pdf_page_start=307, pdf_page_end=320, parent=chapter)
        dup_62 = MODULE.TreeNode(marker="6.2", title="偶校验", level=2, toc_page_start=307, pdf_page_start=307, pdf_page_end=310, parent=chapter)
        sec_622 = MODULE.TreeNode(marker="6.2.2", title="二维奇偶校验", level=3, toc_page_start=310, pdf_page_start=310, pdf_page_end=314, parent=dup_62)
        sec_623 = MODULE.TreeNode(marker="6.2.3", title="检查和", level=3, toc_page_start=314, pdf_page_start=314, pdf_page_end=320, parent=dup_62)
        dup_62.children = [sec_622, sec_623]
        sec_63 = MODULE.TreeNode(marker="6.3", title="多路访问链路和协议", level=2, toc_page_start=310, pdf_page_start=310, pdf_page_end=320, parent=chapter)
        chapter.children = [sec_62, dup_62, sec_63]

        MODULE.normalize_tree_structure([chapter])

        self.assertEqual([child.marker for child in chapter.children], ["6.2", "6.3"])
        self.assertEqual([child.marker for child in sec_62.children], ["6.2.1", "6.2.2", "6.2.3"])
        self.assertEqual(sec_62.children[0].title, "偶校验")
        self.assertEqual(sec_62.children[0].children, [])

    def test_normalize_tree_structure_reparents_nested_nodes_to_existing_parent(self) -> None:
        chapter = MODULE.TreeNode(marker="第6章", title="链路层", level=1, toc_page_start=303, pdf_page_start=303, pdf_page_end=355)
        sec_62 = MODULE.TreeNode(marker="6.2", title="差错检测", level=2, toc_page_start=307, pdf_page_start=307, pdf_page_end=320, parent=chapter)
        sec_621 = MODULE.TreeNode(marker="6.2.1", title="奇偶校验", level=3, toc_page_start=307, pdf_page_start=307, pdf_page_end=310, parent=sec_62)
        sec_622 = MODULE.TreeNode(marker="6.2.2", title="检验和方法", level=3, toc_page_start=310, pdf_page_start=310, pdf_page_end=314, parent=sec_621)
        sec_623 = MODULE.TreeNode(marker="6.2.3", title="循环冗余检测", level=3, toc_page_start=314, pdf_page_start=314, pdf_page_end=320, parent=sec_621)
        sec_621.children = [sec_622, sec_623]
        sec_62.children = [sec_621]
        chapter.children = [sec_62]

        MODULE.normalize_tree_structure([chapter])

        self.assertEqual([child.marker for child in sec_62.children], ["6.2.1", "6.2.2", "6.2.3"])
        self.assertEqual(sec_621.children, [])
        self.assertIs(sec_622.parent, sec_62)
        self.assertIs(sec_623.parent, sec_62)

    def test_normalize_tree_structure_sorts_root_chapters(self) -> None:
        chapter_18 = MODULE.TreeNode(marker="第18章", title="新标准", level=1, toc_page_start=400, pdf_page_start=400, pdf_page_end=420)
        chapter_2 = MODULE.TreeNode(marker="第2章", title="开始学习", level=1, toc_page_start=30, pdf_page_start=30, pdf_page_end=50)
        roots = [chapter_18, chapter_2]

        MODULE.normalize_tree_structure(roots)

        self.assertEqual([node.marker for node in roots], ["第2章", "第18章"])

    def test_normalize_tree_structure_groups_root_sections_under_synthetic_chapters(self) -> None:
        sec_11 = MODULE.TreeNode(marker="1.1", title="数据通信", level=2, toc_page_start=1, pdf_page_start=12, pdf_page_end=15)
        sec_12 = MODULE.TreeNode(marker="1.2", title="网络", level=2, toc_page_start=4, pdf_page_start=15, pdf_page_end=21)
        chapter_2 = MODULE.TreeNode(marker="第2章", title="网络模型", level=1, toc_page_start=15, pdf_page_start=27, pdf_page_end=76)
        roots = [chapter_2, sec_11, sec_12]

        MODULE.normalize_tree_structure(roots)

        self.assertEqual([node.marker for node in roots], ["第1章", "第2章"])
        self.assertEqual([child.marker for child in roots[0].children], ["1.1", "1.2"])
        self.assertIs(sec_11.parent, roots[0])

    def test_normalize_tree_structure_repairs_zero_prefixed_root_sections_when_chapter_ten_exists(self) -> None:
        sec_01 = MODULE.TreeNode(marker="0.1", title="智能控制的发展", level=2, toc_page_start=321, pdf_page_start=341, pdf_page_end=344)
        sec_103 = MODULE.TreeNode(marker="10.3", title="智能控制的研究领域", level=2, toc_page_start=329, pdf_page_start=349, pdf_page_end=352)
        roots = [sec_103, sec_01]

        MODULE.normalize_tree_structure(roots)

        self.assertEqual([node.marker for node in roots], ["第10章"])
        self.assertEqual([child.marker for child in roots[0].children], ["10.1", "10.3"])


    def test_sort_children_prefers_direct_numeric_order_over_page_noise(self) -> None:
        chapter = MODULE.TreeNode(marker="第1章", title="Chapter", level=1, toc_page_start=1, pdf_page_start=1, pdf_page_end=40)
        sec_13 = MODULE.TreeNode(marker="1.3", title="Three", level=2, toc_page_start=None, pdf_page_start=21, parent=chapter)
        sec_15 = MODULE.TreeNode(marker="1.5", title="Five", level=2, toc_page_start=None, pdf_page_start=26, parent=chapter)
        sec_14 = MODULE.TreeNode(marker="1.4", title="Four", level=2, toc_page_start=None, pdf_page_start=34, parent=chapter)
        chapter.children = [sec_13, sec_15, sec_14]

        MODULE.sort_children_in_place(chapter)

        self.assertEqual([child.marker for child in chapter.children], ["1.3", "1.4", "1.5"])

    def test_repair_transposed_numeric_sibling_pages(self) -> None:
        chapter = MODULE.TreeNode(marker="第1章", title="Chapter", level=1, toc_page_start=1, pdf_page_start=13, pdf_page_end=40)
        sec_13 = MODULE.TreeNode(marker="1.3", title="Three", level=2, toc_page_start=9, pdf_page_start=21, parent=chapter)
        sec_14 = MODULE.TreeNode(marker="1.4", title="Four", level=2, toc_page_start=21, pdf_page_start=33, parent=chapter)
        sec_15 = MODULE.TreeNode(marker="1.5", title="Five", level=2, toc_page_start=14, pdf_page_start=26, parent=chapter)
        chapter.children = [sec_13, sec_14, sec_15]

        MODULE.repair_transposed_numeric_sibling_pages([chapter])

        self.assertEqual(sec_14.toc_page_start, 12)
        self.assertEqual(sec_14.pdf_page_start, 24)

    def test_body_subsection_scan_resumes_after_exercise_blocks(self) -> None:
        node = MODULE.TreeNode(marker="1.9", title="Topics", level=2, toc_page_start=None, pdf_page_start=52, pdf_page_end=56)
        blocks = [
            MODULE.ContentBlock(page=52, order=0, block_type="title", text="1.9 Topics", match_text=MODULE.normalize_for_match("1.9 Topics")),
            MODULE.ContentBlock(page=52, order=1, block_type="text", text="1.9.1 Amdahl law", match_text=MODULE.normalize_for_match("1.9.1 Amdahl law")),
            MODULE.ContentBlock(page=53, order=2, block_type="text", text="Exercise 1.1", match_text=MODULE.normalize_for_match("Exercise 1.1")),
            MODULE.ContentBlock(page=54, order=3, block_type="text", text="exercise body", match_text=MODULE.normalize_for_match("exercise body")),
            MODULE.ContentBlock(page=55, order=4, block_type="text", text="1.9.2 Concurrency and parallelism", match_text=MODULE.normalize_for_match("1.9.2 Concurrency and parallelism")),
            MODULE.ContentBlock(page=56, order=5, block_type="text", text="1.9.3 Abstractions", match_text=MODULE.normalize_for_match("1.9.3 Abstractions")),
        ]

        changed = MODULE.expand_numbered_body_subsections_once([node], blocks)

        self.assertTrue(changed)
        self.assertEqual([child.marker for child in node.children], ["1.9.1", "1.9.2", "1.9.3"])

    def test_body_subsection_scan_rejects_exercise_prompts(self) -> None:
        chapter = MODULE.TreeNode(marker="第2章", title="Info", level=1, toc_page_start=None, pdf_page_start=60, pdf_page_end=144)
        blocks = [
            MODULE.ContentBlock(page=60, order=0, block_type="title", text="第2章 Info", match_text=MODULE.normalize_for_match("第2章 Info")),
            MODULE.ContentBlock(page=77, order=1, block_type="text", text="2.2 整数表示", match_text=MODULE.normalize_for_match("2.2 整数表示")),
            MODULE.ContentBlock(page=126, order=2, block_type="text", text="2.64 写出代码实现如下函数", match_text=MODULE.normalize_for_match("2.64 写出代码实现如下函数")),
            MODULE.ContentBlock(page=133, order=3, block_type="text", text="2.93 遵循位级浮点编码规则,实现具有如下原型的函数", match_text=MODULE.normalize_for_match("2.93 遵循位级浮点编码规则,实现具有如下原型的函数")),
        ]

        changed = MODULE.expand_numbered_body_subsections_once([chapter], blocks)

        self.assertTrue(changed)
        self.assertEqual([child.marker for child in chapter.children], ["2.2"])

    def test_repairs_missing_numeric_parent_from_orphan_children(self) -> None:
        chapter = MODULE.TreeNode(marker="第13章", title="Applications", level=1, toc_page_start=1, pdf_page_start=237, pdf_page_end=259)
        sec_131 = MODULE.TreeNode(marker="13.1", title="Logistics", level=2, toc_page_start=2, pdf_page_start=237, pdf_page_end=241, parent=chapter)
        orphan = MODULE.TreeNode(marker="13.10.1", title="Security", level=3, toc_page_start=None, pdf_page_start=255, pdf_page_end=256, parent=sec_131)
        sec_131.children = [orphan]
        chapter.children = [sec_131]
        blocks = [
            MODULE.ContentBlock(page=254, order=0, block_type="text", text="13.10 Big data security", match_text=MODULE.normalize_for_match("13.10 Big data security")),
        ]

        changed = MODULE.repair_missing_numeric_parent_nodes([chapter], blocks)

        self.assertTrue(changed)
        self.assertEqual([child.marker for child in chapter.children], ["13.1", "13.10"])
        repaired = chapter.children[1]
        self.assertEqual(repaired.title, "Big data security")
        self.assertEqual(repaired.pdf_page_start, 254)
        self.assertEqual([child.marker for child in repaired.children], ["13.10.1"])
        self.assertIs(orphan.parent, repaired)

    def test_duplicate_repair_keeps_legal_two_digit_section_marker(self) -> None:
        chapter = MODULE.TreeNode(marker="第13章", title="Applications", level=1, toc_page_start=1, pdf_page_start=237, pdf_page_end=259)
        sec_131 = MODULE.TreeNode(marker="13.1", title="大数据在物流领域中的应用", level=2, toc_page_start=2, pdf_page_start=237, parent=chapter)
        sec_1310 = MODULE.TreeNode(marker="13.10", title="大数据在安全领域中的应用", level=2, toc_page_start=None, pdf_page_start=254, parent=chapter)
        chapter.children = [sec_131, sec_1310]

        MODULE.drop_malformed_duplicate_numeric_siblings([chapter])

        self.assertEqual([child.marker for child in chapter.children], ["13.1", "13.10"])

    def test_title_repair_does_not_use_descendant_heading(self) -> None:
        node = MODULE.TreeNode(marker="13.10", title="大数据在安全领域中的应用", level=2, toc_page_start=None, pdf_page_start=255, pdf_page_end=257)
        node.content = "13.10.1大数据与国家安全\n\n这里是正文。"

        self.assertIsNone(MODULE.extract_heading_title_from_content(node))


    def test_title_repair_strips_embedded_following_markers(self) -> None:
        node = MODULE.TreeNode(marker="1.2.4", title="Network models1.2.5Network categories1.2.6Internet", level=3, toc_page_start=None)

        changed = MODULE.repair_embedded_following_marker_titles([node])

        self.assertTrue(changed)
        self.assertEqual(node.title, "Network models")

    def test_repair_missing_numeric_sibling_gap_from_body_heading(self) -> None:
        chapter = MODULE.TreeNode(marker="Chapter 2", title="Physical layer", level=1, toc_page_start=None, pdf_page_start=10, pdf_page_end=20)
        parent = MODULE.TreeNode(marker="2.4", title="Multiplexing", level=2, toc_page_start=None, pdf_page_start=17, pdf_page_end=17, parent=chapter)
        child_242 = MODULE.TreeNode(marker="2.4.2", title="WDM", level=3, toc_page_start=None, pdf_page_start=17, parent=parent)
        child_243 = MODULE.TreeNode(marker="2.4.3", title="CDM", level=3, toc_page_start=None, pdf_page_start=17, parent=parent)
        parent.children = [child_242, child_243]
        chapter.children = [parent]
        blocks = [
            MODULE.ContentBlock(page=16, order=0, block_type="title", text="2.4.1 FDM", match_text=MODULE.normalize_for_match("2.4.1 FDM")),
        ]

        changed = MODULE.repair_missing_numeric_sibling_gaps([chapter], blocks)

        self.assertTrue(changed)
        self.assertEqual([child.marker for child in parent.children], ["2.4.1", "2.4.2", "2.4.3"])
        self.assertEqual(parent.children[0].title, "FDM")

    def test_title_repair_rejects_paragraph_like_chapter_heading(self) -> None:
        node = MODULE.TreeNode(marker="Chapter 1", title="Introduction", level=1, toc_page_start=None)
        node.content = "Chapter 1 This chapter explains the network, and it has a very long paragraph-like sentence."

        self.assertIsNone(MODULE.extract_heading_title_from_content(node))

    def test_repairs_paragraph_chapter_title_from_toc_line(self) -> None:
        node = MODULE.TreeNode(marker="第1章", title="概述了网络。这一章的目标是介绍整本书。", level=1, toc_page_start=1)
        blocks = [
            MODULE.ContentBlock(page=12, order=0, block_type="text", text="第1章计算机网络和因特网 1", match_text=MODULE.normalize_for_match("第1章计算机网络和因特网 1")),
        ]

        changed = MODULE.repair_paragraph_chapter_titles_from_toc_lines([node], blocks)

        self.assertTrue(changed)
        self.assertEqual(node.title, "计算机网络和因特网")

    def test_repairs_existing_numeric_title_from_exact_body_heading(self) -> None:
        node = MODULE.TreeNode(marker="5.3", title="3 Signal implementation", level=2, toc_page_start=None)
        blocks = [
            MODULE.ContentBlock(page=158, order=0, block_type="text", text="5.3 Semaphores", match_text=MODULE.normalize_for_match("5.3 Semaphores")),
        ]

        changed = MODULE.repair_existing_numeric_titles_from_body_headings([node], blocks)

        self.assertTrue(changed)
        self.assertEqual(node.title, "Semaphores")

    def test_body_heading_filter_rejects_exercise_question_prompts(self) -> None:
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("请将附录1A中的式(1.1)和式(1.2)推广到n级存储器层次。"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("考虑一个具有如下参数的存储器系统"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("为什么需要两种模式(用户模式和内核模式)?"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("操作系统创建一个新进程的步骤是什么?"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("中断和陷阱有何区别?"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("举出中断的三个例子。"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("有如下C程序"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("考虑图5.13。下面各处互换对程序的含义有无影响?"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("下面对一个写者/多个读者问题的解法错在哪里?"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("TinyOS 操作系统的软件组成是怎样的?"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("图13.6是eCos 内核中使用的代码清单。"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("试述真正例率(TPR)、假正例率(FPR)与查准率(P)、查全率(R)之间的联系"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("试述x2检验过程"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("*试推导用于Elman网络的BP算法"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("以西瓜数据集2.0为训练集,试基于BIC准则构建一个贝叶斯网"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("试给出求解L1范数最小化问题中的闭式解"))
        self.assertTrue(MODULE.is_exercise_like_body_heading_title("关键术语、复习题和习题"))
        self.assertFalse(MODULE.is_exercise_like_body_heading_title("Linux操作系统"))
        self.assertFalse(MODULE.is_exercise_like_body_heading_title("计算相似度矩阵"))

    def test_drop_existing_exercise_like_numeric_nodes(self) -> None:
        chapter = MODULE.TreeNode(marker="第2章", title="Chapter", level=1, toc_page_start=None)
        normal = MODULE.TreeNode(marker="2.4", title="比较检验", level=2, toc_page_start=None, parent=chapter)
        exercise = MODULE.TreeNode(marker="2.9", title="试述x2检验过程", level=2, toc_page_start=None, parent=chapter)
        chapter.children = [normal, exercise]

        changed = MODULE.drop_exercise_like_numeric_nodes([chapter])

        self.assertTrue(changed)
        self.assertEqual([child.marker for child in chapter.children], ["2.4"])


if __name__ == "__main__":
    unittest.main()
