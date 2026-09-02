from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import mineru_toc_content_tree as toc  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild chapter trees and rerender HTML outputs from existing merged_content_list.json files."
    )
    parser.add_argument("--root", required=True, type=Path, help="Directory containing *_tree output folders.")
    parser.add_argument("--book-dir", action="append", default=[], help="Optional specific subdirectory names to rebuild.")
    parser.add_argument("--dry-run", action="store_true", help="Only report what would be rebuilt.")
    return parser.parse_args()


def tree_from_dict(data: dict, parent: toc.TreeNode | None = None) -> toc.TreeNode:
    node = toc.TreeNode(
        marker=data.get("marker", "") or "",
        title=data.get("title", "") or "",
        level=int(data.get("level", 1) or 1),
        toc_page_start=data.get("toc_page_start"),
        toc_page_end=data.get("toc_page_end"),
        pdf_page_start=data.get("pdf_page_start"),
        pdf_page_end=data.get("pdf_page_end"),
        content=data.get("content", "") or "",
        parent=parent,
    )
    node.children = [tree_from_dict(child, node) for child in data.get("children", [])]
    return node


def rerender_html(output_dir: Path) -> None:
    content_tree_json = output_dir / "content_tree.json"
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "render_content_tree_graph.py"),
            "--input",
            str(content_tree_json),
            "--output",
            str(output_dir / "content_tree_graph.html"),
            "--svg-output",
            str(output_dir / "content_tree_graph.svg"),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "render_content_tree_visual.py"),
            "--input",
            str(content_tree_json),
            "--output",
            str(output_dir / "content_tree_visual.html"),
        ],
        check=True,
    )


CHUNK_DIR_RE = re.compile(r"^chunk_(\d{4})_(\d{4})$")


def recover_merged_content_from_chunks(output_dir: Path) -> bool:
    chunk_root = output_dir / "chunks"
    if not chunk_root.exists():
        return False

    index_payload: list[dict[str, object]] = []
    for chunk_dir in sorted(path for path in chunk_root.iterdir() if path.is_dir()):
        match = CHUNK_DIR_RE.fullmatch(chunk_dir.name)
        if not match:
            continue
        content_lists = sorted(chunk_dir.rglob("*content_list.json"))
        if not content_lists:
            continue
        index_payload.append(
            {
                "start": int(match.group(1)),
                "end": int(match.group(2)),
                "content_list": str(content_lists[0].resolve()),
            }
        )

    if not index_payload:
        return False

    index_payload.sort(key=lambda item: int(item["start"]))
    chunk_index_path = output_dir / "chunk_index.json"
    merged_path = output_dir / "merged_content_list.json"
    chunk_index_path.write_text(json.dumps(index_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    toc.merge_chunk_content_lists(index_payload, merged_path)
    return True


def rebuild_output_dir(output_dir: Path, dry_run: bool = False) -> tuple[bool, str]:
    merged_content_path = output_dir / "merged_content_list.json"
    tree_json_path = output_dir / "content_tree.json"
    if not tree_json_path.exists():
        return False, "missing content_tree.json"

    if dry_run:
        if merged_content_path.exists():
            return True, "would rebuild"
        return (True, "would recover merged_content_list.json from chunks and rebuild") if recoverable_from_chunks(output_dir) else (False, "missing merged_content_list.json")

    recovered = False
    if not merged_content_path.exists():
        recovered = recover_merged_content_from_chunks(output_dir)
        if not recovered or not merged_content_path.exists():
            return False, "missing merged_content_list.json"

    tree_payload = json.loads(tree_json_path.read_text(encoding="utf-8"))
    roots = [tree_from_dict(item) for item in tree_payload.get("chapters", [])]
    book_title = tree_payload.get("book_title") or output_dir.name.removesuffix("_tree")
    toc_pages = tree_payload.get("toc_pages_pdf") or []
    page_offset = tree_payload.get("toc_to_pdf_offset")

    content_list = json.loads(merged_content_path.read_text(encoding="utf-8"))
    page_texts = toc.build_page_texts(content_list)
    content_blocks = toc.build_content_blocks(content_list)

    toc.normalize_tree_structure(roots)
    toc.assign_page_ranges(toc.flatten_tree(roots))
    toc.propagate_parent_ranges(roots)
    toc.attach_content(roots, page_texts, content_blocks)
    toc.repair_titles_from_content_headings(roots)
    toc.repair_existing_numeric_titles_from_body_headings(roots, content_blocks)
    toc.repair_paragraph_chapter_titles_from_toc_lines(roots, content_blocks)
    for _ in range(4):
        if not toc.expand_numbered_body_subsections_once(roots, content_blocks):
            break
        toc.repair_missing_numeric_parent_nodes(roots, content_blocks)
        toc.repair_missing_numeric_sibling_gaps(roots, content_blocks)
        toc.normalize_tree_structure(roots)
        toc.assign_page_ranges(toc.flatten_tree(roots))
        toc.propagate_parent_ranges(roots)
        toc.attach_content(roots, page_texts, content_blocks)
        toc.repair_titles_from_content_headings(roots)
        toc.repair_existing_numeric_titles_from_body_headings(roots, content_blocks)
        toc.repair_paragraph_chapter_titles_from_toc_lines(roots, content_blocks)

    toc.repair_missing_numeric_parent_nodes(roots, content_blocks)
    toc.repair_missing_numeric_sibling_gaps(roots, content_blocks)
    toc.normalize_tree_structure(roots)
    toc.assign_page_ranges(toc.flatten_tree(roots))
    toc.propagate_parent_ranges(roots)
    toc.attach_content(roots, page_texts, content_blocks)
    toc.repair_titles_from_content_headings(roots)
    toc.repair_existing_numeric_titles_from_body_headings(roots, content_blocks)
    toc.repair_paragraph_chapter_titles_from_toc_lines(roots, content_blocks)
    toc.write_outputs(output_dir, book_title, toc_pages, page_offset, roots)
    rerender_html(output_dir)
    action = "recovered+rebuilt" if recovered else "rebuilt"
    return True, f"{action} nodes={len(toc.flatten_tree(roots))}"


def recoverable_from_chunks(output_dir: Path) -> bool:
    chunk_root = output_dir / "chunks"
    if not chunk_root.exists():
        return False
    return any(
        CHUNK_DIR_RE.fullmatch(path.name) and any(path.rglob("*content_list.json"))
        for path in chunk_root.iterdir()
        if path.is_dir()
    )


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    selected = set(args.book_dir)

    output_dirs = sorted(path for path in root.iterdir() if path.is_dir())
    if selected:
        output_dirs = [path for path in output_dirs if path.name in selected]

    rebuilt = 0
    skipped = 0
    for output_dir in output_dirs:
        ok, message = rebuild_output_dir(output_dir, dry_run=args.dry_run)
        status = "OK" if ok else "SKIP"
        print(f"[{status}] {output_dir.name}: {message}")
        if ok:
            rebuilt += 1
        else:
            skipped += 1

    print(f"done rebuilt={rebuilt} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
