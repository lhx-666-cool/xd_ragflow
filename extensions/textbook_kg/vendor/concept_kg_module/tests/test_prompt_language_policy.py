from __future__ import annotations

import unittest
from pathlib import Path


class PromptLanguagePolicyTests(unittest.TestCase):
    def test_extraction_prompt_requires_chinese_canonical_names(self) -> None:
        prompt = (
            Path(__file__).resolve().parents[1] / "prompts" / "entity_relation_extraction.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("name 使用中文规范名", prompt)
        self.assertIn("只有原文确实只提供英文术语时", prompt)
        self.assertIn("evidence 始终保持原文", prompt)


if __name__ == "__main__":
    unittest.main()
