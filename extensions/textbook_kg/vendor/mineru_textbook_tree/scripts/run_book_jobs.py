from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils import load_yaml, log  # noqa: E402


DEFAULT_LOCAL_MANIFEST = PROJECT_ROOT / "config" / "books.local.yaml"
DEFAULT_SHARED_MANIFEST = PROJECT_ROOT / "config" / "books.yaml"
DEFAULT_EXAMPLE_MANIFEST = PROJECT_ROOT / "config" / "books.example.yaml"
INVALID_FILE_CHARS_RE = re.compile(r'[<>:"/\\|?*]+')
SPACE_RE = re.compile(r"\s+")


@dataclass
class BookJob:
    job_id: str
    pdf: Path
    book_title: str
    output_dir: Path
    toc_pages: str = ""
    page_offset: int | None = None
    chunk_size: int = 20
    lang: str = "ch"
    backend: str = "pipeline"
    force_ocr: bool = False
    text_only: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run MinerU textbook tree generation from a reusable manifest."
    )
    parser.add_argument("--manifest", type=Path, default=None, help="Manifest YAML path.")
    parser.add_argument("--pdf-dir", type=Path, default=None, help="Run every PDF in this folder as a book job.")
    parser.add_argument("--recursive", action="store_true", help="Scan --pdf-dir recursively.")
    parser.add_argument("--book-id", default="", help="Only run a single book id from the manifest.")
    parser.add_argument("--all", action="store_true", help="Run every book in the manifest.")
    parser.add_argument("--chunk-size", type=int, default=None, help="Override chunk size for selected jobs.")
    parser.add_argument("--output-root", type=Path, default=None, help="Override output root for selected jobs.")
    parser.add_argument("--text-only", action="store_true", help="Only extract OCR text and skip TOC/tree rendering.")
    parser.add_argument("--force-ocr", action="store_true", help="Force OCR for every selected job.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip jobs whose content_tree.json already exists.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")
    parser.add_argument("--python", type=Path, default=None, help="Optional explicit project Python path.")
    return parser.parse_args()


def slugify_title(title: str) -> str:
    compact = SPACE_RE.sub(" ", title.strip())
    safe = INVALID_FILE_CHARS_RE.sub("_", compact)
    safe = safe.replace(".", "_")
    safe = safe.replace(" ", "")
    return safe or "book"


def resolve_manifest_path(explicit: Path | None) -> Path:
    candidates = [explicit, DEFAULT_LOCAL_MANIFEST, DEFAULT_SHARED_MANIFEST, DEFAULT_EXAMPLE_MANIFEST]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    raise FileNotFoundError(
        "No manifest found. Create config/books.local.yaml or pass --manifest explicitly."
    )


def resolve_path(value: str | None, *, base_dir: Path) -> Path | None:
    if value is None or not str(value).strip():
        return None
    path = Path(str(value).strip())
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def parse_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def parse_job(item: dict[str, Any], defaults: dict[str, Any], *, manifest_path: Path) -> BookJob:
    merged = {**defaults, **item}
    pdf = resolve_path(str(merged.get("pdf") or "").strip(), base_dir=PROJECT_ROOT)
    if pdf is None:
        raise ValueError("Each book entry must include pdf.")

    book_title = str(merged.get("book_title") or merged.get("title") or "").strip()
    if not book_title:
        book_title = pdf.stem.strip()
    if not book_title:
        raise ValueError("Each book entry must include book_title, title, or a pdf filename.")

    job_id = str(merged.get("id") or slugify_title(book_title)).strip()

    output_dir = resolve_path(merged.get("output_dir"), base_dir=PROJECT_ROOT)
    if output_dir is None:
        output_root = resolve_path(str(merged.get("output_root") or "output"), base_dir=PROJECT_ROOT)
        output_dir = (output_root / f"{slugify_title(book_title)}_tree").resolve()

    toc_pages = str(merged.get("toc_pages") or "").strip()
    page_offset = parse_optional_int(merged.get("page_offset"))
    chunk_size = int(merged.get("chunk_size", 20))
    lang = str(merged.get("lang") or "ch").strip() or "ch"
    backend = str(merged.get("backend") or "pipeline").strip() or "pipeline"
    force_ocr = bool(merged.get("force_ocr", False))
    text_only = bool(merged.get("text_only", False))

    return BookJob(
        job_id=job_id,
        pdf=pdf,
        book_title=book_title,
        output_dir=output_dir,
        toc_pages=toc_pages,
        page_offset=page_offset,
        chunk_size=chunk_size,
        lang=lang,
        backend=backend,
        force_ocr=force_ocr,
        text_only=text_only,
    )


def load_jobs(manifest_path: Path) -> list[BookJob]:
    data = load_yaml(manifest_path)
    defaults = data.get("defaults", {})
    if defaults and not isinstance(defaults, dict):
        raise ValueError("Manifest defaults must be a mapping.")

    books = data.get("books", [])
    if not isinstance(books, list) or not books:
        raise ValueError("Manifest must contain a non-empty books list.")

    jobs: list[BookJob] = []
    seen_ids: set[str] = set()
    for item in books:
        if not isinstance(item, dict):
            raise ValueError("Each book entry must be a mapping.")
        job = parse_job(item, defaults, manifest_path=manifest_path)
        if job.job_id in seen_ids:
            raise ValueError(f"Duplicate book id in manifest: {job.job_id}")
        seen_ids.add(job.job_id)
        jobs.append(job)
    return jobs


def load_manifest_defaults(manifest_path: Path | None) -> dict[str, Any]:
    if manifest_path is None:
        return {}

    data = load_yaml(manifest_path)
    defaults = data.get("defaults", {})
    if defaults and not isinstance(defaults, dict):
        raise ValueError("Manifest defaults must be a mapping.")
    return dict(defaults)


def load_jobs_from_pdf_dir(pdf_dir: Path, defaults: dict[str, Any], *, recursive: bool) -> list[BookJob]:
    if not pdf_dir.exists() or not pdf_dir.is_dir():
        raise FileNotFoundError(f"PDF folder was not found: {pdf_dir}")

    iterator = pdf_dir.rglob("*.pdf") if recursive else pdf_dir.glob("*.pdf")
    pdfs = sorted(path for path in iterator if path.is_file())
    if not pdfs:
        raise FileNotFoundError(f"No PDF files found in folder: {pdf_dir}")

    jobs: list[BookJob] = []
    seen_ids: set[str] = set()
    for pdf in pdfs:
        job = parse_job({"pdf": str(pdf)}, defaults, manifest_path=pdf_dir)
        base_id = job.job_id
        suffix = 2
        while job.job_id in seen_ids:
            job.job_id = f"{base_id}_{suffix}"
            suffix += 1
        seen_ids.add(job.job_id)
        jobs.append(job)
    return jobs


def apply_cli_overrides(jobs: list[BookJob], *, chunk_size: int | None, output_root: Path | None, text_only: bool) -> None:
    resolved_output_root = resolve_path(str(output_root), base_dir=PROJECT_ROOT) if output_root else None
    for job in jobs:
        if chunk_size is not None:
            job.chunk_size = chunk_size
        if resolved_output_root is not None:
            job.output_dir = (resolved_output_root / f"{slugify_title(job.book_title)}_tree").resolve()
        if text_only:
            job.text_only = True


def select_jobs(jobs: list[BookJob], *, book_id: str, run_all: bool) -> list[BookJob]:
    if book_id:
        selected = [job for job in jobs if job.job_id == book_id]
        if not selected:
            raise ValueError(f"Book id not found in manifest: {book_id}")
        return selected
    if run_all or not book_id:
        return jobs
    return jobs


def project_python(explicit: Path | None) -> Path:
    if explicit:
        return explicit
    return PROJECT_ROOT / ".venv-mineru" / "python.exe"


def run_command(command: list[str], *, dry_run: bool) -> None:
    display = subprocess.list2cmdline(command)
    log("runner", display)
    if dry_run:
        return
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {completed.returncode}: {display}")


def run_job(job: BookJob, *, python_path: Path, dry_run: bool, skip_existing: bool, force_ocr_override: bool) -> None:
    primary_output = job.output_dir / ("merged_content_list.json" if job.text_only else "content_tree.json")
    if skip_existing and primary_output.exists() and not force_ocr_override:
        log("runner", f"skip existing job: {job.job_id} -> {primary_output}")
        return

    main_script = PROJECT_ROOT / "scripts" / "mineru_toc_content_tree.py"
    graph_script = PROJECT_ROOT / "scripts" / "render_content_tree_graph.py"
    visual_script = PROJECT_ROOT / "scripts" / "render_content_tree_visual.py"

    command = [
        str(python_path),
        str(main_script),
        "--pdf",
        str(job.pdf),
        "--output-dir",
        str(job.output_dir),
        "--book-title",
        job.book_title,
        "--backend",
        job.backend,
        "--method",
        "ocr",
        "--lang",
        job.lang,
        "--chunk-size",
        str(job.chunk_size),
    ]
    if job.toc_pages:
        command.extend(["--toc-pages", job.toc_pages])
    if job.page_offset is not None:
        command.extend(["--page-offset", str(job.page_offset)])
    if force_ocr_override or job.force_ocr:
        command.append("--force-ocr")
    if job.text_only:
        command.append("--text-only")
    run_command(command, dry_run=dry_run)

    if job.text_only:
        return

    tree_json_path = job.output_dir / "content_tree.json"
    graph_html = job.output_dir / "content_tree_graph.html"
    graph_svg = job.output_dir / "content_tree_graph.svg"
    visual_html = job.output_dir / "content_tree_visual.html"

    run_command(
        [
            str(python_path),
            str(graph_script),
            "--input",
            str(tree_json_path),
            "--output",
            str(graph_html),
            "--svg-output",
            str(graph_svg),
        ],
        dry_run=dry_run,
    )
    run_command(
        [
            str(python_path),
            str(visual_script),
            "--input",
            str(tree_json_path),
            "--output",
            str(visual_html),
        ],
        dry_run=dry_run,
    )


def main() -> None:
    args = parse_args()
    explicit_manifest_path = args.manifest.resolve() if args.manifest else None
    python_path = project_python(args.python if args.python else None)
    if not python_path.exists():
        raise FileNotFoundError(f"Project Python was not found: {python_path}")

    if args.pdf_dir:
        manifest_path = explicit_manifest_path if explicit_manifest_path and explicit_manifest_path.exists() else None
        defaults = load_manifest_defaults(manifest_path)
        jobs = load_jobs_from_pdf_dir(args.pdf_dir.resolve(), defaults, recursive=args.recursive)
        manifest_display = f"pdf_dir={args.pdf_dir.resolve()}"
    else:
        manifest_path = resolve_manifest_path(explicit_manifest_path)
        jobs = load_jobs(manifest_path)
        manifest_display = str(manifest_path)

    apply_cli_overrides(jobs, chunk_size=args.chunk_size, output_root=args.output_root, text_only=args.text_only)
    selected_jobs = select_jobs(jobs, book_id=args.book_id.strip(), run_all=args.all)

    log("runner", f"manifest={manifest_display}")
    log("runner", f"selected_jobs={len(selected_jobs)}")
    for job in selected_jobs:
        log("runner", f"start {job.job_id}: {job.book_title}")
        run_job(
            job,
            python_path=python_path,
            dry_run=args.dry_run,
            skip_existing=args.skip_existing,
            force_ocr_override=args.force_ocr,
        )
        log("runner", f"done {job.job_id}")


if __name__ == "__main__":
    main()
