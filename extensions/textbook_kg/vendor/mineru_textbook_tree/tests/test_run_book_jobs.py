from __future__ import annotations

import importlib.util
import sys
import textwrap
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_book_jobs.py"
SPEC = importlib.util.spec_from_file_location("run_book_jobs", MODULE_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover
    raise RuntimeError(f"Failed to load module from {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class RunBookJobsTests(unittest.TestCase):
    def test_slugify_title_removes_path_unsafe_characters(self) -> None:
        self.assertEqual(MODULE.slugify_title("现代操作系统 第4版/Windows"), "现代操作系统第4版_Windows")

    def test_load_jobs_merges_defaults_and_derives_output_dir(self) -> None:
        manifest = Path(__file__).resolve().parent / "_manifest_test.yaml"
        try:
            manifest.write_text(
                textwrap.dedent(
                    """
                    defaults:
                      output_root: ./output
                      chunk_size: 24
                      lang: ch
                      backend: pipeline

                    books:
                      - id: modern_os4
                        pdf: C:/books/modern_os4.pdf
                        book_title: 现代操作系统 第4版
                        toc_pages: 10-16
                        page_offset: 16
                    """
                ).strip(),
                encoding="utf-8",
            )

            jobs = MODULE.load_jobs(manifest)

            self.assertEqual(len(jobs), 1)
            job = jobs[0]
            self.assertEqual(job.job_id, "modern_os4")
            self.assertEqual(job.chunk_size, 24)
            self.assertEqual(job.toc_pages, "10-16")
            self.assertEqual(job.page_offset, 16)
            self.assertEqual(job.output_dir, (MODULE.PROJECT_ROOT / "output" / "现代操作系统第4版_tree").resolve())
        finally:
            manifest.unlink(missing_ok=True)

    def test_select_jobs_filters_by_book_id(self) -> None:
        jobs = [
            MODULE.BookJob(job_id="a", pdf=Path("a.pdf"), book_title="A", output_dir=Path("out/a")),
            MODULE.BookJob(job_id="b", pdf=Path("b.pdf"), book_title="B", output_dir=Path("out/b")),
        ]

        selected = MODULE.select_jobs(jobs, book_id="b", run_all=False)

        self.assertEqual([job.job_id for job in selected], ["b"])


if __name__ == "__main__":
    unittest.main()
