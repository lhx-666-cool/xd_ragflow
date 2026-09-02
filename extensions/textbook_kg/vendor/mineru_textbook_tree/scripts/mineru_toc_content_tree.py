from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz  # type: ignore

PROJECT_ROOT = Path(__file__).resolve().parent.parent


DOT_LEADER_RE = re.compile(r"[.\-．·•⋯…—_]{2,}")
SPACE_RE = re.compile(r"\s+")
CHAPTER_MARKER_RE = re.compile(r"(?:(?:\u7b2c|[$\uff04])\s*)?\d+\s*\u7ae0")
MISSING_CHAPTER_MARKER_RE = re.compile(r"(?:\u7b2c|[$\uff04])\s*\u7ae0")
APPENDIX_MARKER_RE = re.compile(r"\u9644\u5f55(?:[A-Za-z\uff21-\uff3a]|\d+|[〇零一二三四五六七八九十百两]+)")
SECTION_MARKER_RE = re.compile(r"(?<!\d)[*]{0,2}\d+\.\d+(?:\.\d+)*(?=\s*[\u4e00-\u9fffA-Za-z])")
REFERENCE_TITLE_RE = re.compile(r"^\u53c2\u8003\u4e66\u76ee")
MARKER_RE = re.compile(
    r"(?:"
    r"(?:(?:\u7b2c|[$\uff04])\s*)?\d+\s*\u7ae0"
    r"|"
    r"\u9644\u5f55(?:[A-Za-z\uff21-\uff3a]|\d+|[〇零一二三四五六七八九十百两]+)"
    r"|"
    r"(?<!\d)[*]{0,2}\d+\.\d+(?:\.\d+)*(?=\s*[\u4e00-\u9fffA-Za-z])"
    r"|"
    r"(?:\u7b2c|[$\uff04])\s*\u7ae0"
    r"|"
    r"\u53c2\u8003\u4e66\u76ee"
    r")"
)
PAGE_RE = re.compile(r"(?P<page>\d{1,3})$")
BOOKMARK_PAGE_RE = re.compile(r"^\d+$")
PURE_SECTION_MARKER_RE = re.compile(r"^\d+(?:\.\d+)+$")
BOOKMARK_CHINESE_CHAPTER_RE = re.compile(r"^第([〇零一二三四五六七八九十百两]+)章")
OCR_CHINESE_CHAPTER_RE = re.compile(r"第\s*([〇零一二三四五六七八九十百两]+)\s*章")
BOOKMARK_DASHED_SECTION_RE = re.compile(r"(?<!\d)(\d+(?:-\d+)+)(?=\s*[:：]?\s*[\u4e00-\u9fffA-Za-z])")
CHINESE_NUMERAL_TOKEN_RE = re.compile(r"^[一二三四五六七八九十]+$")
GENERIC_ENTRY_TITLES = {
    "小结",
    "本章小结",
    "总结",
    "本章总结",
    "习题",
    "练习",
    "思考题",
    "附录",
    "导读",
    "概述",
}
ANCILLARY_TOC_TITLES = {
    "要点",
    "本章要点",
    "小结",
    "本章小结",
    "总结",
    "本章总结",
    "练习",
    "习题",
    "思考题",
    "习题与思考",
    "进一步阅读材料",
    "参考书目",
    "参考文献",
    "参考文献与历史注释",
}
ANCILLARY_TOC_TITLE_FRAGMENTS = (
    "关键术语",
    "复习题",
    "习题",
    "推荐读物",
    "推荐阅读",
    "进一步阅读",
)


def has_leading_zero_component(marker: str) -> bool:
    return any(len(part) > 1 and part.startswith("0") for part in marker.split("."))


def split_numeric_marker_blob(blob: str) -> str:
    if not PURE_SECTION_MARKER_RE.fullmatch(blob):
        return blob

    head = blob.split(".", 1)[0]
    if not head.isdigit():
        return blob

    if int(head) <= 99 and not has_leading_zero_component(blob):
        return blob

    dot_index = blob.find(".")
    if dot_index <= 0:
        return blob

    prefix = blob[:dot_index]
    suffix = blob[dot_index:]
    candidates: list[tuple[int, str]] = []
    max_page_len = min(3, len(prefix) - 1)
    for page_len in range(1, max_page_len + 1):
        marker = prefix[page_len:] + suffix
        if not PURE_SECTION_MARKER_RE.fullmatch(marker):
            continue

        marker_head = marker.split(".", 1)[0]
        if not marker_head.isdigit() or marker_head.startswith("0") or has_leading_zero_component(marker):
            continue
        if int(marker_head) > 99:
            continue
        candidates.append((page_len, marker))

    if not candidates:
        return blob

    page_len, marker = min(candidates, key=lambda item: (int(prefix[: item[0]]), item[0], -len(item[1].split(".", 1)[0])))
    return f"{prefix[:page_len]} {marker}"


def insert_spaces_before_section_markers(line: str) -> str:
    pattern = re.compile(r"(?:(?<=\s)|(?<=[.\-锛幝封€⑩嫰鈥︹€擾]))(?P<blob>\d{2,}(?:\.\d+)+)")
    return pattern.sub(lambda match: split_numeric_marker_blob(match.group("blob")), line)


def repair_chapter_number(chapter_number: int | None, current_chapter: int | None) -> int | None:
    if chapter_number is None or current_chapter is None:
        return chapter_number

    expected = current_chapter + 1
    if chapter_number == expected:
        return chapter_number

    raw_text = str(chapter_number)
    expected_text = str(expected)
    if len(raw_text) < len(expected_text):
        if chapter_number == expected % (10 ** len(raw_text)):
            return expected
        if raw_text == expected_text[: len(raw_text)]:
            return expected

    return chapter_number


def trim_oversized_section_component(marker: str, title: str) -> tuple[str, str]:
    if not PURE_SECTION_MARKER_RE.fullmatch(marker):
        return marker, title

    parts = marker.split(".")
    if not parts:
        return marker, title

    last = parts[-1]
    if len(last) < 3 or not last.isdigit() or int(last) < 100:
        return marker, title

    parts[-1] = last[0]
    return ".".join(parts), f"{last[1:]}{title}"


def strip_trailing_symbol_noise(title: str) -> str:
    cleaned = re.sub(r"\s+[+\-_=~`^*#|\\/<>]{4,}$", "", title).strip()
    return cleaned or title


IO_PREFIX_RE = re.compile(r"^\s*(?:[/／\\]\s*[O0])(?=\s*[\u4e00-\u9fffA-Za-z]|$)")
IO_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])"
    r"(?:"
    r"[1Il|]\s*[/／\\]\s*[O0]"
    r"|[1Il|]\s*[Vv]\s*[O0]"
    r"|[1Il|]\s*[O0]"
    r"|[Vv]\s*[/／\\]?\s*[O0]"
    r"|[/／\\]\s*[O0]"
    r")"
    r"(?=\s*[\u4e00-\u9fffA-Za-z]|$)"
)


def repair_io_ocr_text(title: str) -> str:
    return IO_TOKEN_RE.sub("I/O", title)


def repair_io_ocr_marker_and_title(marker: str, title: str) -> tuple[str, str]:
    if PURE_SECTION_MARKER_RE.fullmatch(marker):
        parts = marker.split(".")
        tail = parts[-1]
        prefix_match = IO_PREFIX_RE.match(title)
        if prefix_match and len(tail) > 1 and tail.endswith("1"):
            parts[-1] = tail[:-1]
            marker = ".".join(parts)
            title = f"I/O{title[prefix_match.end():]}"
    return marker, repair_io_ocr_text(title)


@dataclass
class TocEntry:
    marker: str
    title: str
    level: int
    toc_page_start: int | None

    @property
    def label(self) -> str:
        return f"{self.marker} {self.title}".strip()


@dataclass
class TreeNode:
    marker: str
    title: str
    level: int
    toc_page_start: int | None
    toc_page_end: int | None = None
    pdf_page_start: int | None = None
    pdf_page_end: int | None = None
    content: str = ""
    children: list["TreeNode"] = field(default_factory=list)
    parent: "TreeNode | None" = field(default=None, repr=False, compare=False)

    @property
    def label(self) -> str:
        return f"{self.marker} {self.title}".strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "marker": self.marker,
            "title": self.title,
            "label": self.label,
            "level": self.level,
            "toc_page_start": self.toc_page_start,
            "toc_page_end": self.toc_page_end,
            "pdf_page_start": self.pdf_page_start,
            "pdf_page_end": self.pdf_page_end,
            "content": self.content,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass
class ContentBlock:
    page: int
    order: int
    block_type: str
    text: str
    match_text: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Use MinerU OCR on a PDF and build a TOC tree with body text for each section.")
    parser.add_argument("--pdf", required=True, type=Path, help="Source PDF path.")
    parser.add_argument("--output-dir", required=True, type=Path, help="Output directory.")
    parser.add_argument("--book-title", default="", help="Book title for the output root node.")
    parser.add_argument("--mineru-command", default="", help="Optional explicit MinerU executable path.")
    parser.add_argument(
        "--content-list",
        type=Path,
        help="Optional existing merged content_list.json. It is only used together with --skip-ocr.",
    )
    parser.add_argument("--chunk-size", type=int, default=40, help="Number of PDF pages per MinerU chunk.")
    parser.add_argument("--backend", default="pipeline", choices=["pipeline"], help="MinerU backend.")
    parser.add_argument("--method", default="ocr", choices=["txt", "ocr", "auto"], help="MinerU method.")
    parser.add_argument("--lang", default="ch", help="OCR language.")
    parser.add_argument("--toc-pages", default="", help="Optional explicit PDF TOC pages, e.g. 7-10 or 7,8,9,10.")
    parser.add_argument("--page-offset", type=int, default=None, help="Optional explicit offset: pdf_page = toc_page + offset.")
    parser.add_argument("--text-only", action="store_true", help="Only run OCR/text extraction and skip TOC tree construction.")
    parser.add_argument("--skip-ocr", action="store_true", help="Skip MinerU and reuse --content-list. This is for rerender/debug only.")
    parser.add_argument(
        "--force-ocr",
        action="store_true",
        help="Rerun MinerU even if merged_content_list.json and chunk caches already exist.",
    )
    return parser.parse_args()


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u3000", " ")
    text = text.replace("\\*", "*")
    text = DOT_LEADER_RE.sub(" ", text)
    text = SPACE_RE.sub(" ", text).strip()
    return text


def normalize_for_match(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalize_text(text).lower())


def normalize_catalog_title(text: str) -> str:
    normalized = normalize_text(text)
    normalized = normalized.replace(" ", "")
    return normalized.strip(" .:：、，,;；-")


def is_ancillary_toc_entry(entry: TocEntry) -> bool:
    normalized = normalize_catalog_title(entry.title)
    if normalized in ANCILLARY_TOC_TITLES:
        return True
    return any(fragment in normalized for fragment in ANCILLARY_TOC_TITLE_FRAGMENTS)


def repair_spaced_ascii_words(text: str) -> str:
    text = re.sub(r"\bC\s*\+\s*\+", "C++", text)
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"(?<=\b[A-Za-z])\s+(?=[A-Za-z]\b)", "", text)
    return text


def parse_simple_chinese_numeral(text: str) -> int | None:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "百" in text:
        left, _, right = text.partition("百")
        hundreds = 1 if not left else digits.get(left)
        if hundreds is None:
            return None
        if not right:
            return hundreds * 100
        tail = parse_simple_chinese_numeral(right)
        return (hundreds * 100 + tail) if tail is not None else None
    if "十" in text:
        left, _, right = text.partition("十")
        tens = 1 if not left else digits.get(left)
        if tens is None:
            return None
        ones = 0 if not right else digits.get(right)
        if ones is None:
            return None
        return tens * 10 + ones
    if len(text) == 1:
        return digits.get(text)
    return None


def normalize_bookmark_title(title: str) -> str:
    normalized = repair_spaced_ascii_words(normalize_text(title))

    def replace_chapter(match: re.Match[str]) -> str:
        chapter_number = parse_simple_chinese_numeral(match.group(1))
        if chapter_number is None:
            return match.group(0)
        return f"第{chapter_number}章"

    normalized = BOOKMARK_CHINESE_CHAPTER_RE.sub(replace_chapter, normalized)
    normalized = BOOKMARK_DASHED_SECTION_RE.sub(lambda match: match.group(1).replace("-", "."), normalized)
    return normalized


def normalize_toc_ocr_markers(text: str) -> str:
    """规范化扫描版目录中常见的中文章号与 § 连字符节号。"""
    normalized = repair_spaced_ascii_words(normalize_text(text))

    def replace_chapter(match: re.Match[str]) -> str:
        chapter_number = parse_simple_chinese_numeral(match.group(1))
        if chapter_number is None:
            return match.group(0)
        return f"第{chapter_number}章"

    normalized = OCR_CHINESE_CHAPTER_RE.sub(replace_chapter, normalized)
    normalized = BOOKMARK_DASHED_SECTION_RE.sub(lambda match: match.group(1).replace("-", "."), normalized)
    normalized = re.sub(r"§\s*(?=\d+(?:\.\d+)+)", "", normalized)
    return normalized


def resolve_mineru_command(explicit: str) -> str:
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists():
            return str(path.resolve())
        return explicit

    path_cmd = shutil.which("mineru")
    if path_cmd:
        return path_cmd

    local_candidates = [
        PROJECT_ROOT / ".venv-mineru" / "Scripts" / "mineru.exe",
        PROJECT_ROOT / ".venv-mineru" / "Scripts" / "mineru",
        PROJECT_ROOT / ".venv-mineru" / "bin" / "mineru",
        PROJECT_ROOT / ".venv" / "Scripts" / "mineru.exe",
        PROJECT_ROOT / ".venv" / "Scripts" / "mineru",
        PROJECT_ROOT / ".venv" / "bin" / "mineru",
        Path.cwd() / ".venv-mineru" / "Scripts" / "mineru.exe",
        Path.cwd() / ".venv-mineru" / "Scripts" / "mineru",
        Path.cwd() / ".venv-mineru" / "bin" / "mineru",
        Path.cwd() / ".venv" / "Scripts" / "mineru.exe",
        Path.cwd() / ".venv" / "Scripts" / "mineru",
        Path.cwd() / ".venv" / "bin" / "mineru",
    ]
    for local_cmd in local_candidates:
        if local_cmd.exists():
            return str(local_cmd.resolve())

    raise FileNotFoundError("MinerU executable was not found.")


def build_runtime_env(output_dir: Path) -> dict[str, str]:
    runtime_dir = output_dir / ".runtime_cache"
    shared_cache_dir = PROJECT_ROOT / ".mineru_cache"
    env = os.environ.copy()

    # Local cache seeding is useful on one developer machine, but it is risky on
    # shared servers because a partially copied demo cache can poison MinerU's
    # model directory layout. Keep it opt-in instead of always-on.
    if env.get("MINERU_TEXTBOOK_TREE_ENABLE_CACHE_SEED", "").strip() == "1":
        seeded_cache_dir = Path(
            env.get(
                "MINERU_TEXTBOOK_TREE_CACHE_SEED_DIR",
                str(PROJECT_ROOT / "output" / "demo_run2" / ".runtime_cache"),
            )
        )
        seed_pairs = [
            (seeded_cache_dir / "huggingface", shared_cache_dir / "huggingface"),
            (seeded_cache_dir / "modelscope", shared_cache_dir / "modelscope"),
            (seeded_cache_dir / "torch", shared_cache_dir / "torch"),
        ]
        for source, target in seed_pairs:
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target)

    paths = {
        "MPLCONFIGDIR": runtime_dir / "matplotlib",
        "YOLO_CONFIG_DIR": runtime_dir / "ultralytics",
        "HF_HOME": shared_cache_dir / "huggingface",
        "HUGGINGFACE_HUB_CACHE": shared_cache_dir / "huggingface" / "hub",
        "TRANSFORMERS_CACHE": shared_cache_dir / "huggingface" / "transformers",
        "MODELSCOPE_CACHE": shared_cache_dir / "modelscope",
        "TORCH_HOME": shared_cache_dir / "torch",
        "XDG_CACHE_HOME": runtime_dir,
        "TMP": runtime_dir / "tmp",
        "TEMP": runtime_dir / "tmp",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    env.update({key: str(value.resolve()) for key, value in paths.items()})
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def locate_content_list(output_dir: Path) -> Path:
    candidates = sorted(output_dir.rglob("*content_list.json"))
    if not candidates:
        raise FileNotFoundError(f"No content_list.json found under {output_dir}")
    return candidates[0]


def chunk_ranges(page_count: int, chunk_size: int) -> list[tuple[int, int]]:
    ranges = []
    start = 0
    while start < page_count:
        end = min(page_count - 1, start + chunk_size - 1)
        ranges.append((start, end))
        start = end + 1
    return ranges


def is_memory_pressure_error(output: str) -> bool:
    text = output.lower()
    markers = [
        "os error 1455",
        "页面文件太小",
        "paging file is too small",
    ]
    return any(marker in text for marker in markers)


def is_model_download_error(output: str) -> bool:
    text = output.lower()
    markers = [
        "localentrynotfounderror",
        "network is unreachable",
        "maxretryerror",
        "failed to establish a new connection",
        "huggingface.co",
        "snapshot folder for the specified revision",
        "can't load the configuration of",
        "containing a config.json file",
    ]
    return any(marker in text for marker in markers)


def run_mineru_command(command: list[str], env: dict[str, str]) -> tuple[int, str]:
    completed = subprocess.run(
        command,
        check=False,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = completed.stdout or ""
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    return completed.returncode, output


def process_chunk_range(
    *,
    mineru_cmd: str,
    pdf_path: Path,
    chunk_root: Path,
    env: dict[str, str],
    args: argparse.Namespace,
    start: int,
    end: int,
) -> list[dict[str, Any]]:
    chunk_name = f"chunk_{start:04d}_{end:04d}"
    chunk_dir = chunk_root / chunk_name
    chunk_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(chunk_dir.rglob("*content_list.json"))
    if existing and not args.force_ocr:
        return [{"start": start, "end": end, "content_list": str(existing[0].resolve())}]

    if args.force_ocr and chunk_dir.exists():
        shutil.rmtree(chunk_dir)
        chunk_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running MinerU on PDF pages {start + 1}-{end + 1}")
    command = [
        mineru_cmd,
        "-p",
        str(pdf_path),
        "-o",
        str(chunk_dir),
        "-b",
        args.backend,
        "-m",
        args.method,
        "-l",
        args.lang,
        "-f",
        "false",
        "-t",
        "false",
        "-s",
        str(start),
        "-e",
        str(end),
    ]
    return_code, output = run_mineru_command(command, env)
    existing = sorted(chunk_dir.rglob("*content_list.json"))
    if return_code == 0 and existing:
        return [{"start": start, "end": end, "content_list": str(existing[0].resolve())}]

    if is_model_download_error(output):
        raise RuntimeError(
            "MinerU could not access the required PDF-Extract-Kit model files. "
            "This usually means the server cannot reach Hugging Face, or the local "
            "'.mineru_cache/huggingface' snapshot is incomplete. Pre-download the "
            "MinerU model cache on a machine with internet access and upload that "
            "cache to the server, or provide the server with outbound access to "
            "huggingface.co before retrying."
        )

    span = end - start + 1
    if span > 1 and (is_memory_pressure_error(output) or not existing):
        midpoint = start + (span // 2) - 1
        print(f"Retrying {chunk_name} with smaller chunks: {start + 1}-{midpoint + 1} and {midpoint + 2}-{end + 1}")
        left = process_chunk_range(
            mineru_cmd=mineru_cmd,
            pdf_path=pdf_path,
            chunk_root=chunk_root,
            env=env,
            args=args,
            start=start,
            end=midpoint,
        )
        right = process_chunk_range(
            mineru_cmd=mineru_cmd,
            pdf_path=pdf_path,
            chunk_root=chunk_root,
            env=env,
            args=args,
            start=midpoint + 1,
            end=end,
        )
        return [*left, *right]

    if is_memory_pressure_error(output):
        raise RuntimeError(
            "MinerU hit Windows paging-file pressure while loading layout models. "
            "Close memory-heavy apps, increase the Windows pagefile size, or rerun after a reboot."
        )

    if not existing:
        snippet = output.strip().splitlines()[-20:]
        raise FileNotFoundError(
            f"Chunk {chunk_name} completed without a content_list.json.\n" + "\n".join(snippet)
        )

    raise RuntimeError(f"Chunk {chunk_name} failed with exit code {return_code}")


def run_chunked_mineru(args: argparse.Namespace, pdf_path: Path, output_dir: Path) -> Path:
    if args.skip_ocr:
        if not args.content_list:
            raise ValueError("--skip-ocr requires --content-list.")
        return args.content_list.resolve()

    mineru_cmd = resolve_mineru_command(args.mineru_command)
    env = build_runtime_env(output_dir)
    chunk_root = output_dir / "chunks"
    chunk_root.mkdir(parents=True, exist_ok=True)

    with fitz.open(pdf_path) as doc:
        ranges = chunk_ranges(doc.page_count, args.chunk_size)

    merged_path = output_dir / "merged_content_list.json"
    chunk_index_path = output_dir / "chunk_index.json"
    if args.force_ocr:
        if chunk_root.exists():
            shutil.rmtree(chunk_root)
        chunk_root.mkdir(parents=True, exist_ok=True)
        if merged_path.exists():
            merged_path.unlink()
        if chunk_index_path.exists():
            chunk_index_path.unlink()
    elif merged_path.exists() and chunk_index_path.exists():
        return merged_path

    print(f"Using MinerU executable: {mineru_cmd}")
    print(f"Using MinerU method: {args.method}")
    print(f"Using MinerU language: {args.lang}")

    index_payload = []
    for start, end in ranges:
        index_payload.extend(
            process_chunk_range(
                mineru_cmd=mineru_cmd,
                pdf_path=pdf_path,
                chunk_root=chunk_root,
                env=env,
                args=args,
                start=start,
                end=end,
            )
        )

    chunk_index_path.write_text(json.dumps(index_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    merge_chunk_content_lists(index_payload, merged_path)
    return merged_path


def merge_chunk_content_lists(index_payload: list[dict[str, Any]], merged_path: Path) -> None:
    merged: list[dict[str, Any]] = []
    for item in index_payload:
        start = int(item["start"])
        content_path = Path(item["content_list"])
        data = json.loads(content_path.read_text(encoding="utf-8"))
        for block in data:
            copied = dict(block)
            if "page_idx" in copied:
                copied["page_idx"] = int(copied["page_idx"]) + start
            merged.append(copied)
    merged_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


def build_page_lines(content_list: list[dict[str, Any]]) -> dict[int, list[str]]:
    pages: dict[int, list[str]] = {}
    for block in content_list:
        if block.get("type") not in {"text", "title", "list", "index"}:
            continue
        page = int(block.get("page_idx", 0)) + 1
        raw = unicodedata.normalize("NFKC", str(block.get("text", ""))).replace("\u3000", " ")
        for line in raw.splitlines():
            line = normalize_text(line)
            if line:
                pages.setdefault(page, []).append(line)
    return pages


def build_page_texts(content_list: list[dict[str, Any]]) -> dict[int, str]:
    page_map: dict[int, list[str]] = {}
    for block in content_list:
        if block.get("type") not in {"text", "title", "list", "index"}:
            continue
        page = int(block.get("page_idx", 0)) + 1
        text = normalize_text(str(block.get("text", "")))
        if text:
            page_map.setdefault(page, []).append(text)
    return {page: "\n".join(lines).strip() for page, lines in page_map.items()}


def infer_offset_from_page_number_blocks(content_list: list[dict[str, Any]], toc_pages: list[int]) -> int | None:
    min_body_page = (max(toc_pages) + 1) if toc_pages else 1
    offsets: list[int] = []

    for block in content_list:
        if block.get("type") != "page_number":
            continue
        page = int(block.get("page_idx", 0)) + 1
        if page < min_body_page:
            continue

        text = normalize_text(str(block.get("text", "")))
        if not re.fullmatch(r"\d{1,4}", text):
            continue

        printed_page = int(text)
        if printed_page <= 0 or printed_page > page:
            continue
        offsets.append(page - printed_page)

    if len(offsets) < 2:
        return None

    counts = Counter(offsets)
    best_offset, _ = max(counts.items(), key=lambda item: (item[1], -abs(item[0]), item[0]))
    return int(best_offset)


def build_content_blocks(content_list: list[dict[str, Any]]) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for order, block in enumerate(content_list):
        if block.get("type") not in {"text", "title", "list", "index"}:
            continue
        text = normalize_text(str(block.get("text", "")))
        if not text:
            continue
        blocks.append(
            ContentBlock(
                page=int(block.get("page_idx", 0)) + 1,
                order=order,
                block_type=str(block.get("type", "")),
                text=text,
                match_text=normalize_for_match(text),
            )
        )
    return blocks


def build_page_block_ranges(content_blocks: list[ContentBlock]) -> dict[int, tuple[int, int]]:
    ranges: dict[int, list[int]] = {}
    for index, block in enumerate(content_blocks):
        if block.page not in ranges:
            ranges[block.page] = [index, index + 1]
        else:
            ranges[block.page][1] = index + 1
    return {page: (bounds[0], bounds[1]) for page, bounds in ranges.items()}


def first_block_index_between_pages(
    sorted_pages: list[int],
    page_block_ranges: dict[int, tuple[int, int]],
    start_page: int | None,
    end_page: int | None,
) -> int | None:
    if start_page is None:
        return None
    for page in sorted_pages:
        if page < start_page:
            continue
        if end_page is not None and page > end_page:
            break
        return page_block_ranges[page][0]
    return None


def after_last_block_index_between_pages(
    sorted_pages: list[int],
    page_block_ranges: dict[int, tuple[int, int]],
    start_page: int | None,
    end_page: int | None,
) -> int | None:
    if start_page is None:
        return None
    last_after: int | None = None
    for page in sorted_pages:
        if page < start_page:
            continue
        if end_page is not None and page > end_page:
            break
        last_after = page_block_ranges[page][1]
    return last_after


def parse_explicit_pages(spec: str) -> list[int]:
    if not spec:
        return []
    pages: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_s, end_s = chunk.split("-", 1)
            pages.extend(range(int(start_s), int(end_s) + 1))
        else:
            pages.append(int(chunk))
    return sorted(set(pages))


def count_toc_candidates(lines: list[str]) -> int:
    count = 0
    for line in lines:
        line = normalize_toc_ocr_markers(line)
        if not line:
            continue
        if "\u76ee\u5f55" in line.replace(" ", ""):
            count += 3
        count += len(CHAPTER_MARKER_RE.findall(line))
        count += len(APPENDIX_MARKER_RE.findall(line))
        count += len(re.findall(r"\d+\.\d+(?:\.\d+)*", line))
        if REFERENCE_TITLE_RE.search(line):
            count += 1
    return count


def count_trailing_page_lines(lines: list[str]) -> int:
    count = 0
    for line in lines:
        normalized = normalize_text(line)
        if normalized and PAGE_RE.search(normalized):
            count += 1
    return count


def count_inline_page_numbers(lines: list[str]) -> int:
    count = 0
    for line in lines:
        normalized = normalize_text(line)
        count += len(re.findall(r"(?<![\dA-Za-z.\-])\d{1,3}(?![\dA-Za-z.\-])", normalized))
    return count


def count_sentence_punctuation(lines: list[str]) -> int:
    return sum(line.count("。") + line.count("！") + line.count("？") for line in lines)


def looks_like_compact_toc_page(lines: list[str]) -> bool:
    page_numbers = count_inline_page_numbers(lines)
    if page_numbers < 4:
        return False
    if count_sentence_punctuation(lines) > 1:
        return False
    total_length = sum(len(normalize_text(line)) for line in lines)
    return total_length <= 220


def looks_like_toc_page(lines: list[str]) -> bool:
    return (
        count_toc_candidates(lines) >= 4 and count_trailing_page_lines(lines) >= 2
    ) or looks_like_compact_toc_page(lines)


def extend_explicit_toc_pages(page_lines: dict[int, list[str]], explicit_pages: list[int]) -> list[int]:
    pages = sorted(set(explicit_pages))
    if not pages:
        return pages

    last_page = pages[-1]
    for page in sorted(candidate for candidate in page_lines if candidate > last_page):
        lines = page_lines.get(page, [])
        if looks_like_toc_page(lines):
            pages.append(page)
            last_page = page
            continue
        break
    return pages


def detect_toc_pages(page_lines: dict[int, list[str]], explicit_pages: list[int]) -> list[int]:
    if explicit_pages:
        # When the user supplies explicit TOC pages, trust that range instead of
        # trying to auto-extend into following body pages. Auto-extension is too
        # eager for scan-heavy textbooks where numbered body paragraphs can look
        # like TOC entries after OCR.
        return sorted(set(explicit_pages))

    start_page = None
    for page in sorted(page_lines):
        merged = "".join(page_lines[page]).replace(" ", "")
        normalized_lines = [normalize_text(line).replace(" ", "") for line in page_lines[page]]
        if "\u76ee\u5f55" in merged or ("录" in normalized_lines and looks_like_toc_page(page_lines[page])):
            start_page = page
            break
    if start_page is None:
        early_pages = [page for page in sorted(page_lines) if page <= 40]
        toc_like_pages = [page for page in early_pages if looks_like_toc_page(page_lines[page])]
        if toc_like_pages:
            for page in toc_like_pages:
                if page + 1 in page_lines and looks_like_toc_page(page_lines[page + 1]):
                    start_page = page
                    break
            if start_page is None:
                start_page = toc_like_pages[0]
    if start_page is None:
        return []

    toc_pages: list[int] = []
    low_score_streak = 0
    for page in sorted(p for p in page_lines if p >= start_page):
        lines = page_lines[page]
        if page == start_page or looks_like_toc_page(lines):
            toc_pages.append(page)
            low_score_streak = 0
            continue
        low_score_streak += 1
        if low_score_streak >= 1:
            break
    return toc_pages


def split_toc_segments(line: str) -> list[str]:
    line = normalize_toc_ocr_markers(line)
    normalized_compact = line.replace(" ", "")
    if not line or normalized_compact in {"\u76ee\u5f55", "\u76ee\u6b21"}:
        return []
    line = re.sub(r"([*]{1,2})\s+(?=\d)", r"\1", line)
    line = re.sub(r"(\d+):(\d+)", r"\1.\2", line)
    line = re.sub(r"([^\d\u7b2c])(\d{1,3})(?=(?:(?:\u7b2c|[$\uff04])\s*)?\d+\s*\u7ae0)", r"\1\2 ", line)
    line = re.sub(
        r"(?:(?<=\s)|(?<=[.\-．·•⋯…—_]))(\d{1,3})(?=(?:[*]{0,2}\d+\.\d|(?:\u7b2c|[$\uff04])\s*\d+\s*\u7ae0|\u9644\u5f55[A-Za-z\uff21-\uff3a]|\u53c2\u8003\u4e66\u76ee))",
        r"\1 ",
        line,
    )
    line = re.sub(r"(?<=\d)(?=\u9644\u5f55[A-Za-z\uff21-\uff3a])", " ", line)
    line = re.sub(r"(?<=\d)(?=\u53c2\u8003\u4e66\u76ee)", " ", line)

    markers = list(MARKER_RE.finditer(line))
    if not markers:
        return []

    segments: list[str] = []
    for index, match in enumerate(markers):
        start = match.start()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(line)
        segment = line[start:end].strip()
        if segment:
            segments.append(segment)
    return segments


def strip_unnumbered_title_tokens(tokens: list[str]) -> list[str]:
    stripped = [token.strip(" -·.,，。;:：") for token in tokens if token.strip(" -·.,，。;:：")]
    while stripped and CHINESE_NUMERAL_TOKEN_RE.fullmatch(stripped[0]):
        stripped = stripped[1:]
    return stripped


def title_from_unnumbered_tokens(tokens: list[str]) -> str:
    stripped = strip_unnumbered_title_tokens(tokens)
    if not stripped:
        return ""
    title = "".join(stripped)
    return title.strip(" -·.,，。;:：")


def split_unpaged_and_paged_tokens(tokens: list[str]) -> tuple[list[str], list[str]]:
    stripped = [token.strip(" -·.,，。;:：") for token in tokens if token.strip(" -·.,，。;:：")]
    if not stripped:
        return [], []
    if len(stripped) == 1:
        return [], stripped
    if CHINESE_NUMERAL_TOKEN_RE.fullmatch(stripped[-2]):
        return stripped[:-2], stripped[-2:]
    return stripped[:-1], stripped[-1:]


def parse_unnumbered_toc_lines(page_lines: dict[int, list[str]], toc_pages: list[int]) -> list[TocEntry]:
    entries: list[TocEntry] = []
    emitted_titles: set[str] = set()
    first_number_seen = False
    pending_prefix_tokens: list[str] = []

    for page in toc_pages:
        for line in page_lines.get(page, []):
            normalized = normalize_text(line)
            if not normalized:
                continue
            if normalize_for_match(normalized) in {"录", "目录"}:
                continue

            tokens = normalized.split()
            buffer: list[str] = []
            line_emitted = False
            for token in tokens:
                clean_token = token.strip(" -·.,，。;:：")
                if clean_token.isdigit():
                    combined_buffer = [*pending_prefix_tokens, *buffer]
                    leftover_tokens, paged_tokens = split_unpaged_and_paged_tokens(combined_buffer)
                    if not first_number_seen and leftover_tokens:
                        title = title_from_unnumbered_tokens(leftover_tokens)
                        key = normalize_for_match(title)
                        if title and key and key not in emitted_titles:
                            entries.append(TocEntry(marker="", title=title, level=1, toc_page_start=1))
                            emitted_titles.add(key)

                    title = title_from_unnumbered_tokens(paged_tokens)
                    key = normalize_for_match(title)
                    if title and key and key not in emitted_titles:
                        entries.append(TocEntry(marker="", title=title, level=1, toc_page_start=int(clean_token)))
                        emitted_titles.add(key)
                    buffer = []
                    pending_prefix_tokens = []
                    first_number_seen = True
                    line_emitted = True
                else:
                    buffer.append(clean_token)

            if buffer:
                pending_prefix_tokens = [*pending_prefix_tokens, *buffer] if not line_emitted else buffer

    if pending_prefix_tokens and not first_number_seen:
        title = title_from_unnumbered_tokens(pending_prefix_tokens)
        key = normalize_for_match(title)
        if title and key and key not in emitted_titles:
            entries.append(TocEntry(marker="", title=title, level=1, toc_page_start=1))
            emitted_titles.add(key)

    return entries


def parse_segment(segment: str, current_chapter: int | None) -> TocEntry | None:
    if REFERENCE_TITLE_RE.match(segment):
        page_match = PAGE_RE.search(segment)
        toc_page = int(page_match.group("page")) if page_match else None
        return TocEntry(marker="", title="\u53c2\u8003\u4e66\u76ee", level=1, toc_page_start=toc_page)

    marker_match = MARKER_RE.match(segment)
    if not marker_match:
        return None

    raw_marker = marker_match.group(0).strip()
    rest = segment[marker_match.end() :].strip()
    page_match = PAGE_RE.search(rest)
    toc_page = int(page_match.group("page")) if page_match else None
    title = rest[: page_match.start()].strip() if page_match else rest
    title = title.strip(" .…。·*")
    title = re.sub(r"\s+", " ", title)
    title = strip_trailing_symbol_noise(title)

    if CHAPTER_MARKER_RE.match(raw_marker) or MISSING_CHAPTER_MARKER_RE.fullmatch(raw_marker.replace(" ", "")):
        level = 1
        chapter_match = re.search(r"\d+", raw_marker)
        chapter_number = int(chapter_match.group(0)) if chapter_match else None
        if chapter_number is None and MISSING_CHAPTER_MARKER_RE.fullmatch(raw_marker.replace(" ", "")):
            chapter_number = 1 if current_chapter is None else current_chapter + 1
        chapter_number = repair_chapter_number(chapter_number, current_chapter)
        if title:
            nested = re.search(r"\d+\.\d", title)
            if nested:
                title = title[: nested.start()].strip(" .…。·*")
                toc_page = None
        marker = raw_marker.replace(" ", "")
        if chapter_number is not None:
            marker = f"\u7b2c{chapter_number}\u7ae0"
    elif APPENDIX_MARKER_RE.match(raw_marker):
        level = 1
        marker = raw_marker.replace(" ", "")
    else:
        stripped_marker = raw_marker.lstrip("*")
        if "." in stripped_marker:
            repaired_marker = repair_section_marker(stripped_marker, current_chapter)
            if not marker_matches_current_chapter(repaired_marker, current_chapter):
                return None
            repaired_marker, title = trim_oversized_section_component(repaired_marker, title)
            repaired_marker, title = repair_io_ocr_marker_and_title(repaired_marker, title)
            level = repaired_marker.count(".") + 1
            marker_prefix = raw_marker[: len(raw_marker) - len(stripped_marker)]
            marker = f"{marker_prefix}{repaired_marker}"
        else:
            if current_chapter is None:
                return None
            level = 2
            marker = f"{current_chapter}.{stripped_marker}"
            if raw_marker.startswith("*"):
                marker = f"{raw_marker[: len(raw_marker) - len(stripped_marker)]}{marker}"

    if not title:
        return None
    return TocEntry(marker=marker, title=title, level=level, toc_page_start=toc_page)


def repair_section_marker(marker: str, current_chapter: int | None) -> str:
    if current_chapter is None or current_chapter < 10:
        return marker

    parts = marker.split(".")
    if not parts or not parts[0].isdigit():
        return marker

    head = parts[0]
    chapter_text = str(current_chapter)
    if len(head) >= len(chapter_text):
        return marker

    head_number = int(head)
    suffix_match = current_chapter % (10 ** len(head))
    if head_number != suffix_match:
        return marker

    return ".".join([chapter_text, *parts[1:]]) if len(parts) > 1 else chapter_text


def marker_matches_current_chapter(marker: str, current_chapter: int | None) -> bool:
    if current_chapter is None:
        return True
    chapter_number = extract_chapter_number(marker)
    return chapter_number == current_chapter


def split_hidden_gap_entries(entries: list[TocEntry]) -> list[TocEntry]:
    repaired: list[TocEntry] = []

    for index, entry in enumerate(entries):
        if index + 1 < len(entries):
            next_entry = entries[index + 1]
            if (
                entry.level == next_entry.level
                and PURE_SECTION_MARKER_RE.fullmatch(entry.marker)
                and PURE_SECTION_MARKER_RE.fullmatch(next_entry.marker)
            ):
                parent_marker, current_tail = entry.marker.rsplit(".", 1)
                next_parent_marker, next_tail = next_entry.marker.rsplit(".", 1)
                if (
                    parent_marker == next_parent_marker
                    and current_tail.isdigit()
                    and next_tail.isdigit()
                    and int(next_tail) - int(current_tail) == 2
                ):
                    hidden_match = re.search(r"\s+\d\s+\d+\.\d(?:\s+\d)+\s+", entry.title)
                    if hidden_match:
                        left_title = entry.title[: hidden_match.start()].strip()
                        right_title = entry.title[hidden_match.end() :].strip()
                        if left_title and right_title:
                            repaired.append(
                                TocEntry(
                                    marker=entry.marker,
                                    title=left_title,
                                    level=entry.level,
                                    toc_page_start=entry.toc_page_start,
                                )
                            )
                            repaired.append(
                                TocEntry(
                                    marker=f"{parent_marker}.{int(current_tail) + 1}",
                                    title=right_title,
                                    level=entry.level,
                                    toc_page_start=entry.toc_page_start,
                                )
                            )
                            continue
        repaired.append(entry)

    return repaired


def parse_toc_entries(
    page_lines: dict[int, list[str]],
    toc_pages: list[int],
    *,
    strict_page_numbers: bool = False,
) -> list[TocEntry]:
    entries: list[TocEntry] = []
    current_chapter: int | None = None
    skip_answer_appendix_children = False

    for page in toc_pages:
        for line in page_lines.get(page, []):
            for segment in split_toc_segments(line):
                entry = parse_segment(segment, current_chapter)
                if not entry:
                    continue
                if APPENDIX_MARKER_RE.fullmatch(entry.marker):
                    skip_answer_appendix_children = "习题答案" in normalize_catalog_title(entry.title)
                elif skip_answer_appendix_children:
                    # 习题答案附录常重复列出全书章名，不应误作新的正文根章节。
                    continue
                if strict_page_numbers and entry.level > 1 and entry.toc_page_start is None:
                    continue
                entries.append(entry)
                if entry.level == 1:
                    chapter_match = re.search(r"\d+", entry.marker)
                    current_chapter = int(chapter_match.group(0)) if chapter_match else current_chapter

    deduped: list[TocEntry] = []
    seen: set[tuple[str, str, int | None]] = set()
    for entry in entries:
        key = (entry.marker, normalize_for_match(entry.title), entry.toc_page_start)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    if not deduped:
        deduped = parse_unnumbered_toc_lines(page_lines, toc_pages)
        seen.clear()
        deduped = [
            entry
            for entry in deduped
            if not ((entry.marker, normalize_for_match(entry.title), entry.toc_page_start) in seen)
            and not seen.add((entry.marker, normalize_for_match(entry.title), entry.toc_page_start))
        ]
    deduped = [
        entry
        for entry in deduped
        if APPENDIX_MARKER_RE.fullmatch(entry.marker) or not is_ancillary_toc_entry(entry)
    ]
    deduped = split_hidden_gap_entries(deduped)
    repair_truncated_toc_pages(deduped)
    repair_chapter_markers_sequence(deduped)
    normalize_section_markers_to_current_chapter(deduped)
    fill_missing_toc_pages(deduped)
    return deduped


def fill_missing_toc_pages(entries: list[TocEntry]) -> None:
    root_pages = [entry.toc_page_start for entry in entries if entry.level == 1 and entry.toc_page_start is not None]
    first_root_page = min(root_pages) if root_pages else 1
    stack: list[TocEntry] = []
    previous: TocEntry | None = None

    for entry in entries:
        while stack and stack[-1].level >= entry.level:
            stack.pop()
        parent = stack[-1] if stack else None

        if entry.toc_page_start is None:
            if entry.level == 1:
                entry.toc_page_start = 1 if previous is None else previous.toc_page_start or first_root_page
            elif parent and parent.toc_page_start is not None:
                entry.toc_page_start = parent.toc_page_start
            elif previous and previous.toc_page_start is not None:
                entry.toc_page_start = previous.toc_page_start
            else:
                entry.toc_page_start = first_root_page

        stack.append(entry)
        previous = entry


def repair_truncated_toc_pages(entries: list[TocEntry]) -> None:
    previous_page: int | None = None
    for entry in entries:
        page = entry.toc_page_start
        if page is None:
            continue
        if previous_page is not None and page < previous_page:
            page_text = str(page)
            valid_candidates = [
                candidate
                for candidate in range(previous_page, previous_page + 41)
                if str(candidate).endswith(page_text)
            ]
            if valid_candidates:
                entry.toc_page_start = min(valid_candidates)
                page = entry.toc_page_start
        previous_page = page


def repair_chapter_markers_sequence(entries: list[TocEntry]) -> None:
    expected_chapter = 1
    for entry in entries:
        if entry.level != 1:
            continue
        chapter_number = extract_chapter_number(entry.marker)
        if chapter_number is None:
            continue
        if chapter_number != expected_chapter and str(chapter_number).endswith(str(expected_chapter)):
            entry.marker = f"第{expected_chapter}章"
            chapter_number = expected_chapter
        expected_chapter = chapter_number + 1


def normalize_section_markers_to_current_chapter(entries: list[TocEntry]) -> None:
    current_chapter: str | None = None
    for entry in entries:
        marker = normalize_marker_key(entry.marker)
        if entry.level == 1:
            chapter_number = extract_chapter_number(marker)
            current_chapter = str(chapter_number) if chapter_number is not None else None
            continue
        if current_chapter is None or not PURE_SECTION_MARKER_RE.fullmatch(marker):
            continue
        parts = marker.split(".")
        first = parts[0]
        if first == current_chapter:
            continue
        if len(first) > len(current_chapter) and first.endswith(current_chapter):
            parts[0] = current_chapter
            entry.marker = ".".join(parts)


def parse_entries_from_bookmarks(pdf_path: Path) -> list[TocEntry]:
    entries: list[TocEntry] = []
    current_chapter: int | None = None

    with fitz.open(pdf_path) as doc:
        for _, title, page in doc.get_toc(simple=True):
            if not title or not page:
                continue

            segment = normalize_bookmark_title(f"{title} {page}")
            if not segment:
                continue

            entry = parse_segment(segment, current_chapter)
            if not entry:
                continue

            # PyMuPDF bookmark pages are already PDF page numbers, so treat them
            # as resolved page starts and skip TOC-to-PDF offset inference later.
            entry.title = repair_spaced_ascii_words(entry.title)
            entry.toc_page_start = page
            entries.append(entry)

            if entry.level == 1:
                chapter_match = re.search(r"\d+", entry.marker)
                current_chapter = int(chapter_match.group(0)) if chapter_match else current_chapter

    deduped: list[TocEntry] = []
    seen: set[tuple[str, str, int | None]] = set()
    for entry in entries:
        key = (entry.marker, normalize_for_match(entry.title), entry.toc_page_start)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def should_use_bookmark_entries(entries: list[TocEntry]) -> bool:
    if len(entries) < 10:
        return False

    chapter_entries = [entry for entry in entries if entry.level == 1 and CHAPTER_MARKER_RE.fullmatch(entry.marker)]
    return len(chapter_entries) >= 3


def infer_offset_from_bookmarks(pdf_path: Path) -> int | None:
    offsets: list[int] = []
    with fitz.open(pdf_path) as doc:
        for level, title, page in doc.get_toc(simple=True):
            if level != 1 or not BOOKMARK_PAGE_RE.fullmatch(str(title).strip()):
                continue
            offsets.append(int(page) - int(title))
    if not offsets:
        return None
    return int(statistics.median(offsets))


def titles_substantially_overlap(left: str, right: str) -> bool:
    left_key = normalize_for_match(left)
    right_key = normalize_for_match(right)
    if not left_key or not right_key:
        return False
    if min(len(left_key), len(right_key)) < 3:
        return False
    return left_key in right_key or right_key in left_key


def infer_offset_from_bookmark_entries(entries: list[TocEntry], bookmark_entries: list[TocEntry]) -> int | None:
    offsets: list[int] = []

    for entry in entries:
        if entry.toc_page_start is None:
            continue

        matches: list[tuple[int, TocEntry]] = []
        entry_marker = normalize_marker_key(entry.marker)
        for bookmark_entry in bookmark_entries:
            if bookmark_entry.toc_page_start is None:
                continue

            bookmark_marker = normalize_marker_key(bookmark_entry.marker)
            marker_match = entry_marker and entry_marker == bookmark_marker
            title_match = titles_substantially_overlap(entry.title, bookmark_entry.title)

            score = 0
            if marker_match and title_match:
                score = 3
            elif marker_match:
                score = 2
            elif entry.level == bookmark_entry.level == 1 and title_match:
                score = 1

            if score:
                matches.append((score, bookmark_entry))

        if not matches:
            continue

        matches.sort(key=lambda item: item[0], reverse=True)
        best_score = matches[0][0]
        best_entries = [bookmark_entry for score, bookmark_entry in matches if score == best_score]
        if len(best_entries) != 1:
            continue
        offsets.append(best_entries[0].toc_page_start - entry.toc_page_start)

    if not offsets:
        return None

    counts = Counter(offsets)
    best_offset, _ = max(counts.items(), key=lambda item: (item[1], -abs(item[0]), item[0]))
    return int(best_offset)


def is_distinctive_entry_title(entry: TocEntry) -> bool:
    normalized_title = normalize_for_match(entry.title)
    if not normalized_title:
        return False
    if normalize_text(entry.title) in GENERIC_ENTRY_TITLES:
        return False
    if entry.level == 1:
        return len(normalized_title) >= 3
    return len(normalized_title) >= 5


def block_looks_like_heading(block: ContentBlock, title_match: str) -> bool:
    text = block.match_text
    if not text or title_match not in text:
        return False
    if block.block_type == "title":
        return True
    if len(text) > max(len(title_match) * 2 + 6, 24):
        return False
    if any(mark in block.text for mark in ("。", "！", "？", "：", ":")):
        return False
    if text == title_match:
        return True
    if text.endswith(title_match) and len(text) - len(title_match) <= 3:
        return True
    if text.startswith(title_match) and len(text) - len(title_match) <= 6:
        return True
    return False


def block_looks_like_marker_heading(block: ContentBlock, marker_match: str) -> bool:
    text = block.match_text
    if not text or not marker_match or not text.startswith(marker_match):
        return False
    if block.block_type == "title":
        return True
    if len(text) > max(len(marker_match) + 24, 36):
        return False
    if any(mark in block.text for mark in ("。", "！", "？", "：", ":")):
        return False
    return True


def is_ancillary_heading_block(block: ContentBlock) -> bool:
    normalized = normalize_catalog_title(block.text)
    if not normalized:
        return False
    if normalized in ANCILLARY_TOC_TITLES:
        return True
    return any(fragment in normalized for fragment in ANCILLARY_TOC_TITLE_FRAGMENTS)


def block_starts_new_chapter_heading(block: ContentBlock) -> bool:
    text = normalize_text(block.text)
    if not text:
        return False
    compact = text.replace(" ", "")
    return bool(CHAPTER_MARKER_RE.match(compact))


def block_looks_like_marker_heading(block: ContentBlock, marker_match: str) -> bool:
    text = block.match_text
    if not text or not marker_match or not text.startswith(marker_match):
        return False
    if block.block_type == "title":
        return True
    if len(text) > max(len(marker_match) + 24, 36):
        return False
    if any(mark in block.text for mark in ("ĄŁ", "ŁĄ", "Łż", "Łş")):
        return False
    return True


def candidate_heading_pages_for_entry(
    entry: TocEntry,
    content_blocks: list[ContentBlock],
    *,
    min_page: int,
    title_only: bool,
) -> list[int]:
    title_match = normalize_for_match(entry.title)
    marker_match = normalize_for_match(entry.marker)
    if not title_match:
        return []

    pages: list[int] = []
    for block in content_blocks:
        if block.page < min_page:
            continue
        if title_match not in block.match_text:
            continue
        if not title_only and marker_match and marker_match in block.match_text:
            pages.append(block.page)
            continue
        if block_looks_like_heading(block, title_match):
            pages.append(block.page)
    return sorted(set(pages))


def infer_offset_from_content(
    entries: list[TocEntry],
    toc_pages: list[int],
    content_blocks: list[ContentBlock],
) -> int | None:
    if not entries or not content_blocks:
        return None

    min_body_page = (max(toc_pages) + 1) if toc_pages else 1
    chapter_entries = [
        entry
        for entry in entries
        if entry.level == 1 and entry.toc_page_start is not None and is_distinctive_entry_title(entry)
    ]

    offsets: list[int] = []
    for entry in chapter_entries:
        pages = candidate_heading_pages_for_entry(entry, content_blocks, min_page=min_body_page, title_only=True)
        if len(pages) == 1:
            offsets.append(pages[0] - entry.toc_page_start)

    if not offsets:
        for entry in entries:
            if entry.toc_page_start is None or not is_distinctive_entry_title(entry):
                continue
            pages = candidate_heading_pages_for_entry(entry, content_blocks, min_page=min_body_page, title_only=False)
            if len(pages) == 1:
                offsets.append(pages[0] - entry.toc_page_start)

    if not offsets:
        return None

    counts = Counter(offsets)
    best_offset, _ = max(counts.items(), key=lambda item: (item[1], -abs(item[0]), item[0]))
    return int(best_offset)


def normalize_marker_key(marker: str) -> str:
    return normalize_text(marker).lstrip("*").replace(" ", "")


def extract_chapter_number(marker: str) -> int | None:
    normalized = normalize_marker_key(marker)
    if CHAPTER_MARKER_RE.fullmatch(normalized):
        number_match = re.search(r"\d+", normalized)
        return int(number_match.group(0)) if number_match else None
    if PURE_SECTION_MARKER_RE.fullmatch(normalized):
        return int(normalized.split(".", 1)[0])
    return None


def section_parent_candidates(marker: str) -> list[str]:
    normalized = normalize_marker_key(marker)
    if not PURE_SECTION_MARKER_RE.fullmatch(normalized):
        return []
    parts = normalized.split(".")
    return [".".join(parts[:size]) for size in range(len(parts) - 1, 1, -1)]


def resolve_marker_parent(
    node: TreeNode,
    marker_lookup: dict[str, TreeNode],
    chapter_lookup: dict[int, TreeNode],
) -> TreeNode | None:
    normalized = normalize_marker_key(node.marker)
    if not PURE_SECTION_MARKER_RE.fullmatch(normalized):
        return None

    for candidate in section_parent_candidates(normalized):
        parent = marker_lookup.get(candidate)
        if parent is not None:
            return parent

    chapter_number = extract_chapter_number(normalized)
    if chapter_number is not None:
        return chapter_lookup.get(chapter_number)
    return None


def build_parent_stack(node: TreeNode) -> list[TreeNode]:
    stack: list[TreeNode] = []
    current: TreeNode | None = node
    while current is not None:
        stack.append(current)
        current = current.parent
    stack.reverse()
    return stack


def direct_child_marker_for_parent(parent_marker: str, child_marker: str) -> str | None:
    parent_key = normalize_marker_key(parent_marker)
    child_key = normalize_marker_key(child_marker)
    if not PURE_SECTION_MARKER_RE.fullmatch(child_key):
        return None

    child_parts = child_key.split(".")
    if CHAPTER_MARKER_RE.fullmatch(parent_key):
        chapter_number = extract_chapter_number(parent_key)
        if chapter_number is None or len(child_parts) < 3 or child_parts[0] != str(chapter_number):
            return None
        return ".".join(child_parts[:2])

    if PURE_SECTION_MARKER_RE.fullmatch(parent_key):
        parent_parts = parent_key.split(".")
        if len(child_parts) < len(parent_parts) + 2 or child_parts[: len(parent_parts)] != parent_parts:
            return None
        return ".".join(child_parts[: len(parent_parts) + 1])

    return None


def is_direct_child_marker(parent_marker: str, child_marker: str) -> bool:
    parent_key = normalize_marker_key(parent_marker)
    child_key = normalize_marker_key(child_marker)
    if not PURE_SECTION_MARKER_RE.fullmatch(child_key):
        return False

    child_parts = child_key.split(".")
    if CHAPTER_MARKER_RE.fullmatch(parent_key):
        chapter_number = extract_chapter_number(parent_key)
        return chapter_number is not None and len(child_parts) == 2 and child_parts[0] == str(chapter_number)

    if PURE_SECTION_MARKER_RE.fullmatch(parent_key):
        parent_parts = parent_key.split(".")
        return len(child_parts) == len(parent_parts) + 1 and child_parts[: len(parent_parts)] == parent_parts

    return False


def title_for_missing_parent_from_malformed_node(missing_marker: str, node: TreeNode) -> str | None:
    marker_key = normalize_marker_key(node.marker)
    if marker_key == missing_marker or not marker_key.startswith(missing_marker):
        return None
    if marker_key.startswith(f"{missing_marker}."):
        return None

    suffix = marker_key[len(missing_marker) :]
    if not suffix or not suffix.isdigit():
        return None

    title = f"{suffix}{node.title}".strip()
    return title if len(normalize_for_match(title)) >= 2 else None


def repair_missing_intermediate_numeric_nodes(nodes: list[TreeNode]) -> None:
    for node in nodes:
        repair_missing_intermediate_numeric_nodes(node.children)
        if len(node.children) < 2:
            continue

        existing_markers = {normalize_marker_key(child.marker): child for child in node.children}
        missing_groups: dict[str, list[TreeNode]] = {}
        for child in node.children:
            child_marker = normalize_marker_key(child.marker)
            if not PURE_SECTION_MARKER_RE.fullmatch(child_marker):
                continue
            direct_marker = direct_child_marker_for_parent(node.marker, child_marker)
            if not direct_marker or direct_marker == child_marker or direct_marker in existing_markers:
                continue
            missing_groups.setdefault(direct_marker, []).append(child)

        if not missing_groups:
            continue

        updated_children = list(node.children)
        changed = False
        for missing_marker, descendants in missing_groups.items():
            malformed = next(
                (
                    child
                    for child in updated_children
                    if title_for_missing_parent_from_malformed_node(missing_marker, child) is not None
                ),
                None,
            )
            if malformed is None:
                continue

            repaired_title = title_for_missing_parent_from_malformed_node(missing_marker, malformed)
            if not repaired_title:
                continue

            malformed.marker = missing_marker
            malformed.title = repaired_title
            malformed.level = missing_marker.count(".") + 1

            adopted = [
                child
                for child in updated_children
                if child is not malformed and is_direct_child_marker(missing_marker, child.marker)
            ]
            if not adopted:
                continue

            malformed.children = adopted
            for child in adopted:
                child.parent = malformed
            updated_children = [child for child in updated_children if child is malformed or child not in adopted]
            changed = True

        if changed:
            for child in updated_children:
                child.parent = node
            node.children = updated_children
            sort_children_in_place(node)


def reparent_misplaced_numeric_siblings(nodes: list[TreeNode]) -> None:
    for node in nodes:
        reparent_misplaced_numeric_siblings(node.children)
        if len(node.children) < 2:
            continue

        child_lookup = {normalize_marker_key(child.marker): child for child in node.children}
        moved_any = False
        new_children: list[TreeNode] = []
        for child in node.children:
            child_key = normalize_marker_key(child.marker)
            if PURE_SECTION_MARKER_RE.fullmatch(child_key):
                candidates = section_parent_candidates(child_key)
                direct_parent_marker = candidates[0] if candidates else None
                if direct_parent_marker:
                    target_parent = child_lookup.get(direct_parent_marker)
                    if (
                        target_parent is not None
                        and target_parent is not child
                        and target_parent.parent is node
                        and target_parent.level == child.level - 1
                    ):
                        child.parent = target_parent
                        target_parent.children.append(child)
                        sort_children_in_place(target_parent)
                        moved_any = True
                        continue
            new_children.append(child)

        if moved_any:
            for child in new_children:
                child.parent = node
            node.children = new_children
            sort_children_in_place(node)


def repair_duplicate_numeric_siblings(nodes: list[TreeNode]) -> None:
    for node in nodes:
        repair_duplicate_numeric_siblings(node.children)
        if len(node.children) < 2:
            continue

        first_by_marker: dict[str, TreeNode] = {}
        moved_any = False
        new_children: list[TreeNode] = []
        for child in node.children:
            child_key = normalize_marker_key(child.marker)
            if not PURE_SECTION_MARKER_RE.fullmatch(child_key):
                new_children.append(child)
                continue

            existing = first_by_marker.get(child_key)
            if existing is None:
                first_by_marker[child_key] = child
                new_children.append(child)
                continue

            existing_child_keys = {
                normalize_marker_key(grandchild.marker)
                for grandchild in existing.children
                if PURE_SECTION_MARKER_RE.fullmatch(normalize_marker_key(grandchild.marker))
            }
            candidate_marker = None
            for index in range(1, 100):
                probe = f"{child_key}.{index}"
                if probe not in existing_child_keys:
                    candidate_marker = probe
                    break

            if candidate_marker is None:
                new_children.append(child)
                continue

            child.marker = candidate_marker
            child.level = candidate_marker.count(".") + 1
            child.parent = existing
            existing.children.append(child)
            sort_children_in_place(existing)
            moved_any = True

        if moved_any:
            for child in new_children:
                child.parent = node
            node.children = new_children
            sort_children_in_place(node)


def titles_likely_same_after_ocr_glue(left: str, right: str) -> bool:
    left_key = normalize_for_match(left)
    right_key = normalize_for_match(right)
    if not left_key or not right_key:
        return False
    if left_key in right_key or right_key in left_key:
        return True

    max_suffix = min(len(left_key), len(right_key))
    common_suffix = 0
    for index in range(1, max_suffix + 1):
        if left_key[-index] != right_key[-index]:
            break
        common_suffix = index
    return common_suffix >= min(6, max_suffix)


def drop_malformed_duplicate_numeric_siblings(nodes: list[TreeNode]) -> None:
    for node in nodes:
        drop_malformed_duplicate_numeric_siblings(node.children)
        if len(node.children) < 2:
            continue

        by_key = {
            normalize_marker_key(child.marker): child
            for child in node.children
            if PURE_SECTION_MARKER_RE.fullmatch(normalize_marker_key(child.marker))
        }
        to_drop: set[int] = set()
        for child in node.children:
            child_key = normalize_marker_key(child.marker)
            if not PURE_SECTION_MARKER_RE.fullmatch(child_key):
                continue
            parts = child_key.split(".")
            if not parts or not parts[-1].isdigit() or len(parts[-1]) < 3:
                continue
            for cut in range(1, len(parts[-1])):
                candidate_key = ".".join([*parts[:-1], parts[-1][:cut]])
                existing = by_key.get(candidate_key)
                if existing is None or existing is child:
                    continue
                if not titles_likely_same_after_ocr_glue(existing.title, child.title):
                    continue
                for grandchild in child.children:
                    grandchild.parent = existing
                    existing.children.append(grandchild)
                sort_children_in_place(existing)
                to_drop.add(id(child))
                break

        if to_drop:
            node.children = [child for child in node.children if id(child) not in to_drop]
            sort_children_in_place(node)


def direct_parent_key_for_marker(marker: str) -> str | None:
    normalized = normalize_marker_key(marker)
    if not PURE_SECTION_MARKER_RE.fullmatch(normalized):
        return None
    parts = normalized.split(".")
    if len(parts) == 2:
        return f"第{parts[0]}章"
    return ".".join(parts[:-1])


def node_contains(root: TreeNode, candidate: TreeNode) -> bool:
    if root is candidate:
        return True
    return any(node_contains(child, candidate) for child in root.children)


def root_sort_key(node: TreeNode) -> tuple[int, int, int, str]:
    marker = normalize_marker_key(node.marker)
    chapter = extract_chapter_number(marker)
    if CHAPTER_MARKER_RE.fullmatch(marker) and chapter is not None:
        return (0, chapter, node.pdf_page_start or 10**9, normalize_for_match(node.title))
    if APPENDIX_MARKER_RE.fullmatch(marker):
        return (1, 10**9, node.pdf_page_start or 10**9, normalize_for_match(node.title))
    return (2, 10**9, node.pdf_page_start or 10**9, normalize_for_match(node.title))


def sort_roots_in_place(roots: list[TreeNode]) -> None:
    roots.sort(key=root_sort_key)


def repair_zero_prefixed_root_sections(roots: list[TreeNode]) -> None:
    existing_chapters = {
        extract_chapter_number(root.marker)
        for root in roots
        if CHAPTER_MARKER_RE.fullmatch(normalize_marker_key(root.marker))
    }
    section_chapters = [
        parts[0]
        for root in roots
        if (parts := parse_numeric_marker_parts(root.marker)) and parts[0] > 0
    ]
    if 10 not in existing_chapters and 10 not in section_chapters:
        return

    for root in roots:
        parts = parse_numeric_marker_parts(root.marker)
        if not parts or parts[0] != 0:
            continue
        repaired = (10, *parts[1:])
        root.marker = ".".join(str(part) for part in repaired)
        root.level = root.marker.count(".") + 1


def synthetic_chapter_for_section(chapter_number: int, children: list[TreeNode]) -> TreeNode:
    first_child = min(children, key=lambda node: node.pdf_page_start or 10**9)
    last_child = max(children, key=lambda node: node.pdf_page_end or node.pdf_page_start or -1)
    return TreeNode(
        marker=f"第{chapter_number}章",
        title=f"第{chapter_number}章",
        level=1,
        toc_page_start=min((child.toc_page_start for child in children if child.toc_page_start is not None), default=None),
        toc_page_end=max((child.toc_page_end or child.toc_page_start for child in children if child.toc_page_start is not None), default=None),
        pdf_page_start=first_child.pdf_page_start,
        pdf_page_end=last_child.pdf_page_end or last_child.pdf_page_start,
    )


def group_root_sections_under_chapters(roots: list[TreeNode]) -> None:
    repair_zero_prefixed_root_sections(roots)

    existing_chapter_roots: dict[int, TreeNode] = {}
    for root in roots:
        key = normalize_marker_key(root.marker)
        chapter = extract_chapter_number(key)
        if CHAPTER_MARKER_RE.fullmatch(key) and chapter is not None:
            existing_chapter_roots.setdefault(chapter, root)

    grouped_sections: dict[int, list[TreeNode]] = defaultdict(list)
    passthrough_roots: list[TreeNode] = []
    for root in roots:
        key = normalize_marker_key(root.marker)
        parts = parse_numeric_marker_parts(key)
        if parts:
            grouped_sections[parts[0]].append(root)
        else:
            passthrough_roots.append(root)

    if not grouped_sections:
        return

    for chapter_number, section_roots in sorted(grouped_sections.items()):
        parent = existing_chapter_roots.get(chapter_number)
        if parent is None:
            parent = synthetic_chapter_for_section(chapter_number, section_roots)
            existing_chapter_roots[chapter_number] = parent
            passthrough_roots.append(parent)
        for section in section_roots:
            if section.parent is not None:
                section.parent.children = [child for child in section.parent.children if child is not section]
            section.parent = parent
            section.level = expected_node_level_from_marker(section.marker, fallback=section.level)
            parent.children.append(section)
        sort_children_in_place(parent)

    roots[:] = passthrough_roots


def reparent_numeric_nodes_to_existing_parents(roots: list[TreeNode]) -> None:
    flat_nodes = flatten_tree(roots)
    marker_lookup: dict[str, TreeNode] = {}
    chapter_lookup: dict[str, TreeNode] = {}
    for node in flat_nodes:
        key = normalize_marker_key(node.marker)
        if not key:
            continue
        marker_lookup.setdefault(key, node)
        chapter = extract_chapter_number(key)
        if CHAPTER_MARKER_RE.fullmatch(key) and chapter is not None:
            chapter_lookup.setdefault(f"第{chapter}章", node)

    moves: list[tuple[TreeNode, TreeNode]] = []
    for node in flat_nodes:
        node_key = normalize_marker_key(node.marker)
        if not PURE_SECTION_MARKER_RE.fullmatch(node_key):
            continue
        parent_key = direct_parent_key_for_marker(node_key)
        if parent_key is None:
            continue
        target_parent = marker_lookup.get(parent_key) or chapter_lookup.get(parent_key)
        if target_parent is None or target_parent is node or node_contains(node, target_parent):
            continue
        if node.parent is target_parent:
            continue
        moves.append((node, target_parent))

    for node, target_parent in moves:
        if node.parent is not None:
            node.parent.children = [child for child in node.parent.children if child is not node]
        else:
            roots[:] = [root for root in roots if root is not node]
        node.parent = target_parent
        node.level = expected_node_level_from_marker(node.marker, fallback=node.level)
        target_parent.children.append(node)
        sort_children_in_place(target_parent)


def repair_transposed_numeric_sibling_pages(nodes: list[TreeNode]) -> None:
    for node in nodes:
        sort_children_in_place(node)
        children = node.children
        for index, child in enumerate(children):
            current = child.toc_page_start
            if current is None or current < 10 or current > 99:
                continue
            prev_page = None
            for prev in reversed(children[:index]):
                if prev.toc_page_start is not None:
                    prev_page = prev.toc_page_start
                    break
            next_page = None
            for next_child in children[index + 1 :]:
                if next_child.toc_page_start is not None:
                    next_page = next_child.toc_page_start
                    break
            if prev_page is None or next_page is None or current <= next_page:
                continue
            reversed_page = int(str(current)[::-1])
            if prev_page <= reversed_page <= next_page:
                pdf_offset = (
                    child.pdf_page_start - child.toc_page_start
                    if child.pdf_page_start is not None and child.toc_page_start is not None
                    else None
                )
                child.toc_page_start = reversed_page
                if child.toc_page_end is not None and child.toc_page_end == current:
                    child.toc_page_end = reversed_page
                if pdf_offset is not None:
                    child.pdf_page_start = reversed_page + pdf_offset
                    if child.pdf_page_end is not None and child.pdf_page_end == current + pdf_offset:
                        child.pdf_page_end = reversed_page + pdf_offset
        repair_transposed_numeric_sibling_pages(children)


def find_body_heading_for_marker(marker: str, content_blocks: list[ContentBlock]) -> tuple[str, int] | None:
    marker_key = normalize_marker_key(marker)
    if not PURE_SECTION_MARKER_RE.fullmatch(marker_key):
        return None
    marker_pattern = re.compile(rf"^\s*{re.escape(marker_key)}\s*(.+?)\s*$")
    for block in content_blocks:
        text = normalize_text(block.text)
        match = marker_pattern.match(text)
        if not match:
            continue
        raw_title = match.group(1).strip(" .:,;")
        title = strip_embedded_following_markers(
            strip_trailing_symbol_noise(repair_io_ocr_text(raw_title)),
            marker_key,
        )
        if title and len(normalize_for_match(title)) >= 2 and not is_exercise_like_body_heading_title(title):
            return title, block.page
    return None


def build_body_heading_lookup(content_blocks: list[ContentBlock]) -> dict[str, tuple[str, int]]:
    lookup: dict[str, tuple[str, int]] = {}
    heading_pattern = re.compile(r"^\s*\*?(\d+(?:\.\d+)+)\s*(.+?)\s*$")
    for block in content_blocks:
        text = normalize_text(block.text)
        match = heading_pattern.match(text)
        if not match:
            continue
        marker = normalize_marker_key(match.group(1))
        raw_title = match.group(2).strip(" .:,;")
        marker, raw_title = trim_oversized_section_component(marker, raw_title)
        marker, raw_title = repair_io_ocr_marker_and_title(marker, raw_title)
        if not PURE_SECTION_MARKER_RE.fullmatch(marker) or has_leading_zero_component(marker):
            continue
        marker_match = normalize_for_match(marker)
        if not (block.block_type == "title" or block_looks_like_marker_heading(block, marker_match)):
            continue
        title = strip_embedded_following_markers(
            strip_trailing_symbol_noise(repair_io_ocr_text(raw_title.strip(" .:：、，,;；·-—"))),
            marker,
        )
        if not title or len(normalize_for_match(title)) < 2:
            continue
        if is_exercise_like_body_heading_title(title):
            continue
        if heading_title_looks_like_body_paragraph(title, TreeNode(marker=marker, title=title, level=marker.count(".") + 1, toc_page_start=None)):
            continue
        lookup.setdefault(marker, (title, block.page))
    return lookup


def strip_embedded_following_markers(title: str, marker: str) -> str:
    marker_key = normalize_marker_key(marker)
    if not title or not PURE_SECTION_MARKER_RE.fullmatch(marker_key):
        return title

    marker_parts = marker_key.split(".")
    parent_prefix = ".".join(marker_parts[:-1])
    if not parent_prefix:
        return title

    cut_at: int | None = None
    for match in re.finditer(r"(?<!\d)(\d+(?:\.\d+)+)\s*", title):
        embedded_key = normalize_marker_key(match.group(1))
        if embedded_key == marker_key:
            continue
        embedded_parts = embedded_key.split(".")
        if len(embedded_parts) != len(marker_parts):
            continue
        if ".".join(embedded_parts[:-1]) != parent_prefix:
            continue
        try:
            if int(embedded_parts[-1]) <= int(marker_parts[-1]):
                continue
        except ValueError:
            continue
        cut_at = match.start()
        break

    if cut_at is None:
        return title
    return strip_trailing_symbol_noise(title[:cut_at].strip(" .:,;锛氥€侊紝"))


def heading_title_looks_like_body_paragraph(title: str, node: TreeNode) -> bool:
    normalized = normalize_text(title)
    if not normalized:
        return True

    marker_key = normalize_marker_key(node.marker)
    lowered = normalized.lower()
    if "这一章的目标" in normalized or "这一节" in normalized[:20] or "this chapter" in lowered[:40]:
        return True

    if len(normalize_for_match(normalized)) >= 36 and any(mark in normalized for mark in ("。", "，", "；", ";")):
        return True

    if CHAPTER_MARKER_RE.fullmatch(marker_key) and len(normalize_for_match(normalized)) >= 24:
        return True

    return False


def repair_missing_numeric_parent_nodes(roots: list[TreeNode], content_blocks: list[ContentBlock] | None = None) -> bool:
    flat_nodes = flatten_tree(roots)
    marker_lookup = {normalize_marker_key(node.marker): node for node in flat_nodes if normalize_marker_key(node.marker)}
    chapter_lookup: dict[int, TreeNode] = {}
    for node in flat_nodes:
        key = normalize_marker_key(node.marker)
        chapter = extract_chapter_number(key)
        if CHAPTER_MARKER_RE.fullmatch(key) and chapter is not None:
            chapter_lookup.setdefault(chapter, node)

    groups: dict[tuple[str, int], list[TreeNode]] = defaultdict(list)
    targets: dict[tuple[str, int], TreeNode] = {}
    for node in flat_nodes:
        node_key = normalize_marker_key(node.marker)
        if not PURE_SECTION_MARKER_RE.fullmatch(node_key):
            continue
        missing_parent_key = direct_parent_key_for_marker(node_key)
        if not missing_parent_key or missing_parent_key in marker_lookup:
            continue
        missing_parts = parse_numeric_marker_parts(missing_parent_key)
        if len(missing_parts) < 2:
            continue
        target_key = direct_parent_key_for_marker(missing_parent_key)
        target_parent = marker_lookup.get(target_key or "") or chapter_lookup.get(missing_parts[0])
        if target_parent is None or node_contains(node, target_parent):
            continue
        groups[(missing_parent_key, id(target_parent))].append(node)
        targets[(missing_parent_key, id(target_parent))] = target_parent

    changed = False
    for (missing_parent_key, target_id), children in sorted(groups.items(), key=lambda item: parse_numeric_marker_parts(item[0][0])):
        target_parent = targets[(missing_parent_key, target_id)]
        heading = find_body_heading_for_marker(missing_parent_key, content_blocks or [])
        title = heading[0] if heading else missing_parent_key
        heading_page = heading[1] if heading else None
        first_child_page = min((child.pdf_page_start for child in children if child.pdf_page_start is not None), default=None)
        last_child_page = max((child.pdf_page_end or child.pdf_page_start for child in children if child.pdf_page_start is not None), default=None)
        synthetic_parent = TreeNode(
            marker=missing_parent_key,
            title=title,
            level=missing_parent_key.count(".") + 1,
            toc_page_start=None,
            pdf_page_start=heading_page if heading_page is not None else first_child_page,
            pdf_page_end=last_child_page,
            parent=target_parent,
        )
        target_parent.children.append(synthetic_parent)
        marker_lookup[missing_parent_key] = synthetic_parent
        for child in children:
            if child.parent is not None:
                child.parent.children = [sibling for sibling in child.parent.children if sibling is not child]
            else:
                roots[:] = [root for root in roots if root is not child]
            child.parent = synthetic_parent
            synthetic_parent.children.append(child)
        sort_children_in_place(synthetic_parent)
        sort_children_in_place(target_parent)
        changed = True
    return changed


def expected_direct_child_marker(parent_marker: str, child_number: int) -> str | None:
    parent_key = normalize_marker_key(parent_marker)
    if PURE_SECTION_MARKER_RE.fullmatch(parent_key):
        return f"{parent_key}.{child_number}"
    if CHAPTER_MARKER_RE.fullmatch(parent_key):
        chapter_number = extract_chapter_number(parent_key)
        if chapter_number is None:
            return None
        return f"{chapter_number}.{child_number}"
    return None


def repair_missing_numeric_sibling_gaps(roots: list[TreeNode], content_blocks: list[ContentBlock]) -> bool:
    if not content_blocks:
        return False

    flat_nodes = flatten_tree(roots)
    existing_markers = {normalize_marker_key(node.marker) for node in flat_nodes if normalize_marker_key(node.marker)}
    heading_lookup = build_body_heading_lookup(content_blocks)
    changed = False

    for parent in flat_nodes:
        parent_key = normalize_marker_key(parent.marker)
        if not (PURE_SECTION_MARKER_RE.fullmatch(parent_key) or CHAPTER_MARKER_RE.fullmatch(parent_key)):
            continue

        child_numbers: set[int] = set()
        for child in parent.children:
            child_key = normalize_marker_key(child.marker)
            if not is_direct_numeric_child_marker(parent_key, child_key):
                continue
            child_parts = parse_numeric_marker_parts(child_key)
            if child_parts:
                child_numbers.add(child_parts[-1])

        if not child_numbers:
            continue

        search_limit = max(child_numbers) + 3
        for child_number in range(1, search_limit + 1):
            if child_number in child_numbers:
                continue
            marker = expected_direct_child_marker(parent_key, child_number)
            if not marker or marker in existing_markers:
                continue
            heading = heading_lookup.get(marker)
            if heading is None:
                continue
            title, page = heading
            child = TreeNode(
                marker=marker,
                title=title,
                level=marker.count(".") + 1,
                toc_page_start=None,
                pdf_page_start=page,
                pdf_page_end=page,
                parent=parent,
            )
            parent.children.append(child)
            existing_markers.add(marker)
            child_numbers.add(child_number)
            changed = True

        if changed:
            sort_children_in_place(parent)

    return changed


def repair_existing_numeric_titles_from_body_headings(roots: list[TreeNode], content_blocks: list[ContentBlock]) -> bool:
    if not content_blocks:
        return False

    heading_lookup = build_body_heading_lookup(content_blocks)
    changed = False
    for node in flatten_tree(roots):
        marker_key = normalize_marker_key(node.marker)
        if not PURE_SECTION_MARKER_RE.fullmatch(marker_key):
            continue
        heading = heading_lookup.get(marker_key)
        if heading is None:
            continue
        title, _ = heading
        if title and title != node.title:
            node.title = title
            changed = True
    return changed


def repair_embedded_following_marker_titles(roots: list[TreeNode]) -> bool:
    changed = False
    for node in flatten_tree(roots):
        cleaned = strip_embedded_following_markers(node.title, node.marker)
        if cleaned and cleaned != node.title:
            node.title = cleaned
            changed = True
    return changed


def drop_exercise_like_numeric_nodes(roots: list[TreeNode]) -> bool:
    changed = False

    def visit(children: list[TreeNode]) -> None:
        nonlocal changed
        kept: list[TreeNode] = []
        for child in children:
            visit(child.children)
            child_key = normalize_marker_key(child.marker)
            if PURE_SECTION_MARKER_RE.fullmatch(child_key) and is_exercise_like_body_heading_title(child.title):
                changed = True
                continue
            kept.append(child)
        if len(kept) != len(children):
            children[:] = kept

    visit(roots)
    return changed


def find_chapter_title_in_toc_lines(node: TreeNode, content_blocks: list[ContentBlock]) -> str | None:
    marker_key = normalize_marker_key(node.marker)
    chapter_number = extract_chapter_number(marker_key)
    if chapter_number is None or not CHAPTER_MARKER_RE.fullmatch(marker_key):
        return None

    pattern = re.compile(rf"^\s*\u7b2c\s*{chapter_number}\s*\u7ae0\s*(.+?)\s*$")
    for block in content_blocks:
        for raw_line in normalize_text(block.text).splitlines():
            line = raw_line.strip()
            match = pattern.match(line)
            if not match:
                continue
            candidate = re.split(r"\s+\d+\s+(?=\d+\.\d+)", match.group(1).strip(), maxsplit=1)[0]
            candidate = re.sub(r"\s*\d+\s*$", "", candidate)
            title = strip_trailing_symbol_noise(repair_io_ocr_text(candidate.strip(" .:：、，,;；·-—")))
            if not title or len(normalize_for_match(title)) < 2:
                continue
            if heading_title_looks_like_body_paragraph(title, node):
                continue
            return title
    return None


def repair_paragraph_chapter_titles_from_toc_lines(roots: list[TreeNode], content_blocks: list[ContentBlock]) -> bool:
    if not content_blocks:
        return False
    changed = False
    for node in flatten_tree(roots):
        if not heading_title_looks_like_body_paragraph(node.title, node):
            continue
        candidate = find_chapter_title_in_toc_lines(node, content_blocks)
        if candidate and candidate != node.title:
            node.title = candidate
            changed = True
    return changed


def expected_node_level_from_marker(marker: str, fallback: int) -> int:
    normalized = normalize_marker_key(marker)
    if CHAPTER_MARKER_RE.fullmatch(normalized) or APPENDIX_MARKER_RE.fullmatch(normalized):
        return 1
    if PURE_SECTION_MARKER_RE.fullmatch(normalized):
        return normalized.count(".") + 1
    return fallback


def normalize_tree_structure(roots: list[TreeNode]) -> None:
    group_root_sections_under_chapters(roots)
    repair_embedded_following_marker_titles(roots)
    drop_exercise_like_numeric_nodes(roots)
    repair_missing_intermediate_numeric_nodes(roots)
    drop_malformed_duplicate_numeric_siblings(roots)
    reparent_misplaced_numeric_siblings(roots)
    reparent_numeric_nodes_to_existing_parents(roots)
    repair_missing_numeric_parent_nodes(roots)
    repair_duplicate_numeric_siblings(roots)
    repair_transposed_numeric_sibling_pages(roots)
    sort_roots_in_place(roots)


def build_tree(entries: list[TocEntry], page_offset: int) -> list[TreeNode]:
    nodes = [
        TreeNode(
            marker=entry.marker,
            title=entry.title,
            level=entry.level,
            toc_page_start=entry.toc_page_start,
            pdf_page_start=(entry.toc_page_start + page_offset) if entry.toc_page_start is not None else None,
        )
        for entry in entries
    ]

    roots: list[TreeNode] = []
    stack: list[TreeNode] = []
    marker_lookup: dict[str, TreeNode] = {}
    chapter_lookup: dict[int, TreeNode] = {}
    for node in nodes:
        while stack and stack[-1].level >= node.level:
            stack.pop()

        parent = resolve_marker_parent(node, marker_lookup, chapter_lookup)
        if parent is None and stack:
            parent = stack[-1]

        if parent is not None:
            node.parent = parent
            parent.children.append(node)
        else:
            roots.append(node)

        stack = build_parent_stack(node)
        marker_lookup[normalize_marker_key(node.marker)] = node
        chapter_number = extract_chapter_number(node.marker)
        if node.level == 1 and chapter_number is not None:
            chapter_lookup[chapter_number] = node

    normalize_tree_structure(roots)
    assign_page_ranges(flatten_tree(roots))
    return roots


def assign_page_ranges(nodes: list[TreeNode]) -> None:
    for index, node in enumerate(nodes):
        next_node = None
        for later in nodes[index + 1 :]:
            if later.level <= node.level:
                next_node = later
                break
        if next_node:
            if node.toc_page_start is not None and next_node.toc_page_start is not None:
                node.toc_page_end = max(node.toc_page_start, next_node.toc_page_start - 1)
            if node.pdf_page_start is not None and next_node.pdf_page_start is not None:
                node.pdf_page_end = max(node.pdf_page_start, next_node.pdf_page_start - 1)
        elif node.pdf_page_start is not None:
            node.pdf_page_end = node.pdf_page_start


def extend_last_node_to_book_end(nodes: list[TreeNode], total_pages: int) -> None:
    if not nodes:
        return
    nodes[-1].pdf_page_end = total_pages
    if nodes[-1].toc_page_start is not None and nodes[-1].pdf_page_start is not None:
        nodes[-1].toc_page_end = nodes[-1].toc_page_start + (total_pages - nodes[-1].pdf_page_start)


def propagate_parent_ranges(nodes: list[TreeNode]) -> None:
    for node in nodes:
        if not node.children:
            continue
        propagate_parent_ranges(node.children)
        last_child = node.children[-1]
        if last_child.pdf_page_end is not None:
            node.pdf_page_end = max(node.pdf_page_end or last_child.pdf_page_end, last_child.pdf_page_end)
        if last_child.toc_page_end is not None:
            node.toc_page_end = max(node.toc_page_end or last_child.toc_page_end, last_child.toc_page_end)
        elif last_child.toc_page_start is not None:
            node.toc_page_end = max(node.toc_page_end or last_child.toc_page_start, last_child.toc_page_start)


def flatten_tree(nodes: list[TreeNode]) -> list[TreeNode]:
    flat: list[TreeNode] = []
    for node in nodes:
        flat.append(node)
        flat.extend(flatten_tree(node.children))
    return flat


def build_preorder_with_subtree_successors(nodes: list[TreeNode]) -> tuple[list[TreeNode], dict[int, TreeNode | None]]:
    ordered: list[TreeNode] = []
    subtree_end_indices: dict[int, int] = {}

    def visit(node: TreeNode) -> None:
        ordered.append(node)
        for child in node.children:
            visit(child)
        subtree_end_indices[id(node)] = len(ordered) - 1

    for root in nodes:
        visit(root)

    next_after_subtree: dict[int, TreeNode | None] = {}
    for node in ordered:
        end_index = subtree_end_indices[id(node)]
        next_after_subtree[id(node)] = ordered[end_index + 1] if end_index + 1 < len(ordered) else None
    return ordered, next_after_subtree


def find_heading_block_index(
    node: TreeNode,
    content_blocks: list[ContentBlock],
    sorted_pages: list[int],
    page_block_ranges: dict[int, tuple[int, int]],
) -> int | None:
    search_start = first_block_index_between_pages(sorted_pages, page_block_ranges, node.pdf_page_start, node.pdf_page_end)
    search_end = after_last_block_index_between_pages(sorted_pages, page_block_ranges, node.pdf_page_start, node.pdf_page_end)
    if search_start is None or search_end is None:
        return None

    label_match = normalize_for_match(node.label)
    marker_match = normalize_for_match(node.marker)
    title_match = normalize_for_match(node.title)
    fallback_index: int | None = None

    for index in range(search_start, search_end):
        block = content_blocks[index]
        text = block.match_text
        if not text:
            continue
        if label_match and label_match in text:
            return index
        if marker_match and title_match and marker_match in text and title_match in text:
            return index
        if title_match and block.block_type == "title" and title_match in text and fallback_index is None:
            fallback_index = index
        elif marker_match and text.startswith(marker_match) and title_match and title_match in text and fallback_index is None:
            fallback_index = index
        elif marker_match and block_looks_like_marker_heading(block, marker_match) and fallback_index is None:
            fallback_index = index

    return fallback_index if fallback_index is not None else search_start


def compute_node_block_span(
    node: TreeNode,
    heading_indices: dict[int, int | None],
    next_after_subtree: dict[int, TreeNode | None],
    sorted_pages: list[int],
    page_block_ranges: dict[int, tuple[int, int]],
) -> tuple[int | None, int | None]:
    fallback_start = first_block_index_between_pages(sorted_pages, page_block_ranges, node.pdf_page_start, node.pdf_page_end)
    fallback_end = after_last_block_index_between_pages(sorted_pages, page_block_ranges, node.pdf_page_start, node.pdf_page_end)
    start_index = heading_indices.get(id(node)) if heading_indices.get(id(node)) is not None else fallback_start

    end_index = fallback_end
    next_node = next_after_subtree.get(id(node))
    if next_node is not None:
        next_start = heading_indices.get(id(next_node))
        if next_start is not None and start_index is not None and next_start > start_index:
            end_index = next_start
    return start_index, end_index


def parse_numeric_marker_parts(marker: str) -> tuple[int, ...]:
    normalized = normalize_marker_key(marker)
    if not PURE_SECTION_MARKER_RE.fullmatch(normalized):
        return tuple()
    return tuple(int(part) for part in normalized.split("."))


def is_direct_numeric_child_marker(parent_marker: str, child_marker: str) -> bool:
    parent_key = normalize_marker_key(parent_marker)
    child_key = normalize_marker_key(child_marker)

    if not PURE_SECTION_MARKER_RE.fullmatch(child_key):
        return False

    if PURE_SECTION_MARKER_RE.fullmatch(parent_key):
        if not child_key.startswith(f"{parent_key}."):
            return False
        return ".".join(child_key.split(".")[:-1]) == parent_key

    if CHAPTER_MARKER_RE.fullmatch(parent_key):
        chapter_number = extract_chapter_number(parent_key)
        child_parts = child_key.split(".")
        return chapter_number is not None and len(child_parts) == 2 and child_parts[0] == str(chapter_number)

    return False


def parse_body_numbered_heading(block: ContentBlock, parent_marker: str) -> tuple[str, str] | None:
    parent_key = normalize_marker_key(parent_marker)

    text = normalize_text(block.text)
    if not text:
        return None

    heading_match = re.match(r"^\*?(\d+(?:\.\d+)+)\s*(.+?)\s*$", text)
    if not heading_match:
        return None

    marker = normalize_marker_key(heading_match.group(1))
    raw_title = heading_match.group(2).strip()
    marker, raw_title = trim_oversized_section_component(marker, raw_title)
    marker, raw_title = repair_io_ocr_marker_and_title(marker, raw_title)
    if has_leading_zero_component(marker):
        return None

    if PURE_SECTION_MARKER_RE.fullmatch(parent_key):
        if not marker.startswith(f"{parent_key}."):
            return None
        if ".".join(marker.split(".")[:-1]) != parent_key:
            return None
    elif CHAPTER_MARKER_RE.fullmatch(parent_key):
        chapter_number = extract_chapter_number(parent_key)
        marker_parts = marker.split(".")
        if chapter_number is None or len(marker_parts) != 2 or marker_parts[0] != str(chapter_number):
            return None
    else:
        return None

    marker_match = normalize_for_match(marker)
    if not (block.block_type == "title" or block_looks_like_marker_heading(block, marker_match)):
        return None

    title = strip_trailing_symbol_noise(repair_io_ocr_text(raw_title.strip(" .:：、，,;；·-—")))
    title = strip_embedded_following_markers(title, marker)
    if not title or len(normalize_for_match(title)) < 2:
        return None
    if any(mark in title for mark in ("。", "！", "？", "；")):
        return None

    if is_exercise_like_body_heading_title(title):
        return None
    if heading_title_looks_like_body_paragraph(title, TreeNode(marker=marker, title=title, level=marker.count(".") + 1, toc_page_start=None)):
        return None

    return marker, title


def is_exercise_like_body_heading_title(title: str) -> bool:
    normalized = normalize_text(title)
    if not normalized:
        return False
    strong_exercise_fragments = (
        "\u4e60\u9898",  # 习题
        "\u590d\u4e60\u9898",  # 复习题
        "\u7ec3\u4e60",  # 练习
        "\u8bf7\u5c06",  # 请将
        "\u8bf7\u7ed9\u51fa",  # 请给出
        "\u8003\u8651\u4e00\u4e2a",  # 考虑一个
        "\u8003\u8651\u4e0b\u9762",  # 考虑下面
        "\u8003\u8651\u56fe",  # 考虑图
        "\u6709\u5982\u4e0b",  # 有如下
        "\u4e0b\u9762",  # 下面
        "\u4e3a\u4ec0\u4e48",  # 为什么
        "\u9519\u5728\u54ea\u91cc",  # 错在哪里
        "\u6b65\u9aa4\u662f\u4ec0\u4e48",  # 步骤是什么
        "\u6709\u4f55\u533a\u522b",  # 有何区别
        "\u4e3e\u51fa",  # 举出
        "\u7b80\u8981\u8bf4\u660e",  # 简要说明
        "\u7b80\u8981\u63cf\u8ff0",  # 简要描述
        "\u4e3e\u4f8b",  # 举例
        "\u8bd5\u8bc1\u660e",  # 试证明
        "\u8bd5\u8ff0",  # 试述
        "\u8bd5\u6c42",  # 试求
        "\u8bd5\u8bbe\u8ba1",  # 试设计
        "\u8bd5\u5c06",  # 试将
        "\u8bd5\u4f7f\u7528",  # 试使用
        "\u8bd5\u57fa\u4e8e",  # 试基于
        "\u8bd5\u6790",  # 试析
        "\u8bd5\u63a8\u5bfc",  # 试推导
        "\u8bd5\u7ed9\u51fa",  # 试给出
        "\u8bd5\u6539\u8fdb",  # 试改进
        "\u8bd5\u8ba8\u8bba",  # 试讨论
        "\u8bd5\u751f\u6210",  # 试生成
        "\u8bc1\u660e",  # 证明
        "\u5217\u51fa",  # 列出
        "\u63cf\u8ff0\u4e00\u4e2a",  # 描述一个
    )
    if any(fragment in normalized for fragment in strong_exercise_fragments):
        return True
    if re.search(r"(?<!\u6d4b)\u8bd5[\u4e00-\u9fffA-Za-z0-9]", normalized):
        return True
    if re.match(r"^[\u56fe\u8868]\s*\d+(?:\.\d+)*", normalized):
        return True
    if re.search(r"[?？]\s*$", normalized) and any(
        fragment in normalized
        for fragment in (
            "\u4e3a\u4ec0\u4e48",  # 为什么
            "\u4ec0\u4e48",  # 什么
            "\u5982\u4f55",  # 如何
            "\u600e\u6837",  # 怎样
            "\u662f\u5426",  # 是否
            "\u54ea\u4e9b",  # 哪些
            "\u4f55",  # 何
        )
    ):
        return True
    exercise_fragments = (
        "\u5982\u4e0b",  # 如下
        "\u5199\u51fa",  # 写出
        "\u4ee3\u7801",  # 代码
        "\u51fd\u6570",  # 函数
        "\u539f\u578b",  # 原型
        "\u8868\u8fbe\u5f0f",  # 表达式
        "\u9075\u5faa",  # 遵循
        "\u89c4\u5219",  # 规则
        "\u5b9e\u73b0",  # 实现
        "\u5047\u8bbe",  # 假设
        "\u6b7b\u9501",  # 死锁
        "\u4e3a\u4ec0\u4e48",  # 为什么
    )
    hits = sum(1 for fragment in exercise_fragments if fragment in normalized)
    if hits >= 2:
        return True
    return bool(re.search(r"\b(?:write|implement|function|prototype|expression|assume|deadlock)\b", normalized, re.IGNORECASE))


def sort_children_in_place(node: TreeNode) -> None:
    parent_key = normalize_marker_key(node.marker)

    def child_sort_key(child: TreeNode) -> tuple[int, tuple[int, ...], int, str]:
        child_parts = parse_numeric_marker_parts(child.marker)
        if child_parts and is_direct_numeric_child_marker(parent_key, normalize_marker_key(child.marker)):
            return (0, child_parts, child.pdf_page_start if child.pdf_page_start is not None else 10**9, normalize_text(child.title))
        return (
            1,
            child_parts or (10**9,),
            child.pdf_page_start if child.pdf_page_start is not None else 10**9,
            normalize_text(child.title),
        )

    node.children.sort(
        key=child_sort_key
    )


def expand_numbered_body_subsections_once(nodes: list[TreeNode], content_blocks: list[ContentBlock]) -> bool:
    if not content_blocks:
        return False

    page_block_ranges = build_page_block_ranges(content_blocks)
    sorted_pages = sorted(page_block_ranges)
    ordered_nodes, next_after_subtree = build_preorder_with_subtree_successors(nodes)
    heading_indices = {id(node): find_heading_block_index(node, content_blocks, sorted_pages, page_block_ranges) for node in ordered_nodes}
    changed_any = False

    for node in ordered_nodes:
        normalized_marker = normalize_marker_key(node.marker)
        if not (PURE_SECTION_MARKER_RE.fullmatch(normalized_marker) or CHAPTER_MARKER_RE.fullmatch(normalized_marker)):
            continue

        start_index, end_index = compute_node_block_span(node, heading_indices, next_after_subtree, sorted_pages, page_block_ranges)
        if start_index is None or end_index is None or end_index <= start_index + 1:
            continue

        scan_start = start_index
        if CHAPTER_MARKER_RE.fullmatch(normalized_marker):
            chapter_fallback_start = first_block_index_between_pages(
                sorted_pages,
                page_block_ranges,
                node.pdf_page_start,
                node.pdf_page_end,
            )
            if chapter_fallback_start is not None and chapter_fallback_start < scan_start:
                scan_start = chapter_fallback_start

        preserved_children: list[TreeNode] = []
        synthetic_children_by_marker: dict[str, TreeNode] = {}
        for child in node.children:
            child_marker = normalize_marker_key(child.marker)
            if child.toc_page_start is None and is_direct_numeric_child_marker(normalized_marker, child_marker):
                synthetic_children_by_marker[child_marker] = child
            else:
                preserved_children.append(child)

        discovered: list[tuple[str, str, int]] = []
        seen_markers: set[str] = set()
        in_ancillary_section = False

        for index in range(scan_start + 1, end_index):
            block = content_blocks[index]
            parsed = parse_body_numbered_heading(block, normalized_marker)
            if parsed is not None:
                in_ancillary_section = False
            elif in_ancillary_section:
                if block_starts_new_chapter_heading(block):
                    break
                continue

            if is_ancillary_heading_block(block):
                in_ancillary_section = True
                continue
            if parsed is None:
                continue
            marker, title = parsed
            if marker in seen_markers:
                continue
            if any(normalize_marker_key(child.marker) == marker for child in preserved_children):
                seen_markers.add(marker)
                continue
            discovered.append((marker, title, block.page))
            seen_markers.add(marker)

        new_synthetic_children: list[TreeNode] = []
        for marker, title, page in discovered:
            child = synthetic_children_by_marker.get(marker)
            if child is None:
                child = TreeNode(
                    marker=marker,
                    title=title,
                    level=marker.count(".") + 1,
                    toc_page_start=None,
                    pdf_page_start=page,
                    pdf_page_end=node.pdf_page_end or page,
                    parent=node,
                )
            else:
                if child.title != title or child.pdf_page_start != page or child.parent is not node:
                    changed_any = True
                child.title = title
                child.level = marker.count(".") + 1
                child.pdf_page_start = page
                child.pdf_page_end = node.pdf_page_end or page
                child.parent = node
            new_synthetic_children.append(child)

        previous_synthetic_markers = list(synthetic_children_by_marker)
        new_synthetic_markers = [marker for marker, _, _ in discovered]
        if previous_synthetic_markers != new_synthetic_markers:
            changed_any = True

        node.children = preserved_children + new_synthetic_children
        sort_children_in_place(node)

    return changed_any


def attach_content(nodes: list[TreeNode], page_texts: dict[int, str], content_blocks: list[ContentBlock]) -> None:
    if not content_blocks:
        return

    page_block_ranges = build_page_block_ranges(content_blocks)
    sorted_pages = sorted(page_block_ranges)
    ordered_nodes, next_after_subtree = build_preorder_with_subtree_successors(nodes)
    heading_indices = {id(node): find_heading_block_index(node, content_blocks, sorted_pages, page_block_ranges) for node in ordered_nodes}

    for node in ordered_nodes:
        start_index, end_index = compute_node_block_span(node, heading_indices, next_after_subtree, sorted_pages, page_block_ranges)

        if start_index is None or end_index is None or end_index <= start_index:
            if node.pdf_page_start is None or node.pdf_page_end is None:
                continue
            parts = [page_texts[page] for page in range(node.pdf_page_start, node.pdf_page_end + 1) if page_texts.get(page)]
            node.content = repair_io_ocr_text("\n\n".join(parts).strip())
            continue

        selected = content_blocks[start_index:end_index]
        node.content = repair_io_ocr_text("\n\n".join(block.text for block in selected).strip())
        if selected:
            node.pdf_page_start = selected[0].page
            node.pdf_page_end = selected[-1].page


def extract_heading_title_from_content(node: TreeNode) -> str | None:
    if not node.content:
        return None

    marker_key = normalize_marker_key(node.marker)
    if not marker_key:
        return None

    for raw_line in node.content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        normalized_line = normalize_marker_key(line)
        if not normalized_line.startswith(marker_key):
            continue
        suffix = normalized_line[len(marker_key) :]
        if suffix.startswith(".") and len(suffix) > 1 and suffix[1].isdigit():
            continue

        candidate = line
        if candidate.startswith(node.marker):
            candidate = candidate[len(node.marker) :].strip()
        else:
            match = re.match(rf"^\*?{re.escape(node.marker)}\s*(.*)$", candidate)
            if not match:
                continue
            candidate = match.group(1).strip()

        candidate = strip_trailing_symbol_noise(repair_io_ocr_text(candidate.strip(" .:：、，,;；·-—")))
        candidate = strip_embedded_following_markers(candidate, marker_key)
        if candidate and len(normalize_for_match(candidate)) >= 2 and not heading_title_looks_like_body_paragraph(candidate, node):
            return candidate
    return None


def repair_titles_from_content_headings(nodes: list[TreeNode]) -> bool:
    changed = False
    for node in flatten_tree(nodes):
        candidate = extract_heading_title_from_content(node)
        if not candidate or candidate == node.title:
            continue
        node.title = candidate
        changed = True
    return changed


def render_ascii_tree(nodes: list[TreeNode], prefix: str = "") -> list[str]:
    lines: list[str] = []
    for index, node in enumerate(nodes):
        is_last = index == len(nodes) - 1
        branch = "└─ " if is_last else "├─ "
        if node.pdf_page_start is not None and node.pdf_page_end is not None and node.pdf_page_end != node.pdf_page_start:
            pages = f"PDF页 {node.pdf_page_start}-{node.pdf_page_end}"
        elif node.pdf_page_start is not None:
            pages = f"PDF页 {node.pdf_page_start}"
        else:
            pages = ""
        suffix = f" ({pages})" if pages else ""
        lines.append(f"{prefix}{branch}{node.label}{suffix}")
        child_prefix = f"{prefix}{'   ' if is_last else '│  '}"
        lines.extend(render_ascii_tree(node.children, child_prefix))
    return lines


def render_text_with_content(book_title: str, roots: list[TreeNode]) -> str:
    lines = [book_title, ""]

    def visit(node: TreeNode, depth: int) -> None:
        indent = "  " * max(depth - 1, 0)
        lines.append(f"{indent}{node.label}")
        if node.pdf_page_start is not None and node.pdf_page_end is not None:
            if node.pdf_page_end != node.pdf_page_start:
                lines.append(f"{indent}PDF页码：{node.pdf_page_start}-{node.pdf_page_end}")
            else:
                lines.append(f"{indent}PDF页码：{node.pdf_page_start}")
        if node.toc_page_start is not None:
            if node.toc_page_end and node.toc_page_end != node.toc_page_start:
                lines.append(f"{indent}目录页码：{node.toc_page_start}-{node.toc_page_end}")
            else:
                lines.append(f"{indent}目录页码：{node.toc_page_start}")
        lines.append("")
        if node.content:
            for paragraph in node.content.split("\n"):
                lines.append(f"{indent}{paragraph}")
        else:
            lines.append(f"{indent}(无正文内容)")
        lines.append("")
        for child in node.children:
            visit(child, depth + 1)

    for root in roots:
        visit(root, 0)
    return "\n".join(lines).rstrip() + "\n"


def render_mermaid(book_title: str, roots: list[TreeNode]) -> str:
    lines = ["graph TD"]
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"n{counter}"

    root_id = next_id()
    lines.append(f'    {root_id}["{escape_mermaid(book_title)}"]')

    def visit(parent_id: str, node: TreeNode) -> None:
        node_id = next_id()
        label = node.label
        if node.pdf_page_start is not None:
            if node.pdf_page_end and node.pdf_page_end != node.pdf_page_start:
                label += f"\\nPDF {node.pdf_page_start}-{node.pdf_page_end}"
            else:
                label += f"\\nPDF {node.pdf_page_start}"
        lines.append(f'    {node_id}["{escape_mermaid(label)}"]')
        lines.append(f"    {parent_id} --> {node_id}")
        for child in node.children:
            visit(node_id, child)

    for root in roots:
        visit(root_id, root)
    return "\n".join(lines) + "\n"


def escape_mermaid(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def render_markdown(book_title: str, roots: list[TreeNode]) -> str:
    lines = [f"# {book_title}", ""]

    def visit(node: TreeNode, depth: int) -> None:
        heading_level = min(depth + 1, 6)
        lines.append(f'{"#" * heading_level} {node.label}')
        if node.pdf_page_start is not None and node.pdf_page_end is not None:
            lines.append(f"PDF页码：{node.pdf_page_start}-{node.pdf_page_end}" if node.pdf_page_end != node.pdf_page_start else f"PDF页码：{node.pdf_page_start}")
        if node.toc_page_start is not None:
            if node.toc_page_end and node.toc_page_end != node.toc_page_start:
                lines.append(f"目录页码：{node.toc_page_start}-{node.toc_page_end}")
            else:
                lines.append(f"目录页码：{node.toc_page_start}")
        lines.append("")
        lines.append(node.content or "(无正文内容)")
        lines.append("")
        for child in node.children:
            visit(child, depth + 1)

    for root in roots:
        visit(root, 1)
    return "\n".join(lines).rstrip() + "\n"


def render_page_text(book_title: str, page_texts: dict[int, str]) -> str:
    lines = [book_title, ""]
    for page in sorted(page_texts):
        lines.append(f"## PDF页码：{page}")
        lines.append("")
        lines.append(page_texts[page] or "(无正文内容)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_text_only_outputs(output_dir: Path, book_title: str, page_texts: dict[int, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    pages_json = output_dir / "content_pages.json"
    pages_txt = output_dir / "content_pages.txt"
    pages_md = output_dir / "content_pages.md"

    payload = {
        "book_title": book_title,
        "pages": [{"pdf_page": page, "content": page_texts[page]} for page in sorted(page_texts)],
    }
    text = render_page_text(book_title, page_texts)
    pages_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    pages_txt.write_text(text, encoding="utf-8")
    pages_md.write_text(text, encoding="utf-8")


def write_outputs(output_dir: Path, book_title: str, toc_pages: list[int], page_offset: int | None, roots: list[TreeNode]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    tree_json = output_dir / "content_tree.json"
    tree_txt = output_dir / "content_tree.txt"
    tree_outline_txt = output_dir / "content_tree_outline.txt"
    tree_md = output_dir / "content_tree.md"
    tree_mmd = output_dir / "content_tree.mmd"

    payload = {
        "book_title": book_title,
        "toc_pages_pdf": toc_pages,
        "toc_to_pdf_offset": page_offset,
        "chapters": [root.to_dict() for root in roots],
    }
    tree_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tree_txt.write_text(render_text_with_content(book_title, roots), encoding="utf-8")
    tree_outline_txt.write_text("\n".join([book_title] + render_ascii_tree(roots)) + "\n", encoding="utf-8")
    tree_md.write_text(render_markdown(book_title, roots), encoding="utf-8")
    tree_mmd.write_text(render_mermaid(book_title, roots), encoding="utf-8")


def main() -> int:
    args = parse_args()
    pdf_path = args.pdf.resolve()
    output_dir = args.output_dir.resolve()
    book_title = args.book_title or pdf_path.stem

    merged_content_path = run_chunked_mineru(args, pdf_path, output_dir)
    content_list = json.loads(merged_content_path.read_text(encoding="utf-8"))
    page_lines = build_page_lines(content_list)
    page_texts = build_page_texts(content_list)
    content_blocks = build_content_blocks(content_list)

    if args.text_only:
        write_text_only_outputs(output_dir, book_title, page_texts)
        print(f"merged_content_list: {merged_content_path}")
        print(f"text_only_pages: {len(page_texts)}")
        print(f"outputs: {output_dir}")
        return 0

    explicit_toc_pages = parse_explicit_pages(args.toc_pages)
    bookmark_entries = parse_entries_from_bookmarks(pdf_path)
    if not explicit_toc_pages and should_use_bookmark_entries(bookmark_entries):
        entries = bookmark_entries
        toc_pages = sorted({entry.toc_page_start for entry in entries if entry.toc_page_start is not None})
        page_offset = 0
        print(f"Using PDF bookmarks as TOC source ({len(entries)} entries).")
    else:
        toc_pages = detect_toc_pages(page_lines, explicit_toc_pages)
        if not toc_pages:
            raise RuntimeError("Failed to detect TOC pages in the OCR output.")

        entries = parse_toc_entries(page_lines, toc_pages, strict_page_numbers=bool(explicit_toc_pages))
        if not entries:
            raise RuntimeError("Failed to parse any TOC entries from the OCR output.")

        if args.page_offset is not None:
            page_offset = args.page_offset
        else:
            page_offset = infer_offset_from_page_number_blocks(content_list, toc_pages)
            if page_offset is None:
                page_offset = infer_offset_from_bookmarks(pdf_path)
            if page_offset is None:
                page_offset = infer_offset_from_bookmark_entries(entries, bookmark_entries)
            if page_offset is None:
                page_offset = infer_offset_from_content(entries, toc_pages, content_blocks)
        if page_offset is None:
            raise RuntimeError("Failed to infer the TOC-to-PDF page offset. Pass --page-offset explicitly.")

    roots = build_tree(entries, page_offset)
    flat_nodes = flatten_tree(roots)
    if flat_nodes:
        with fitz.open(pdf_path) as doc:
            extend_last_node_to_book_end(flat_nodes, doc.page_count)
    propagate_parent_ranges(roots)
    attach_content(roots, page_texts, content_blocks)
    repair_titles_from_content_headings(roots)
    repair_existing_numeric_titles_from_body_headings(roots, content_blocks)
    repair_paragraph_chapter_titles_from_toc_lines(roots, content_blocks)
    for _ in range(4):
        if not expand_numbered_body_subsections_once(roots, content_blocks):
            break
        repair_missing_numeric_parent_nodes(roots, content_blocks)
        repair_missing_numeric_sibling_gaps(roots, content_blocks)
        normalize_tree_structure(roots)
        assign_page_ranges(flatten_tree(roots))
        propagate_parent_ranges(roots)
        attach_content(roots, page_texts, content_blocks)
        repair_titles_from_content_headings(roots)
        repair_existing_numeric_titles_from_body_headings(roots, content_blocks)
        repair_paragraph_chapter_titles_from_toc_lines(roots, content_blocks)

    repair_missing_numeric_parent_nodes(roots, content_blocks)
    repair_missing_numeric_sibling_gaps(roots, content_blocks)
    normalize_tree_structure(roots)
    assign_page_ranges(flatten_tree(roots))
    propagate_parent_ranges(roots)
    attach_content(roots, page_texts, content_blocks)
    repair_titles_from_content_headings(roots)
    repair_existing_numeric_titles_from_body_headings(roots, content_blocks)
    repair_paragraph_chapter_titles_from_toc_lines(roots, content_blocks)
    flat_nodes = flatten_tree(roots)

    write_outputs(output_dir, book_title, toc_pages, page_offset, roots)

    print(f"merged_content_list: {merged_content_path}")
    print(f"toc_pages_pdf: {toc_pages}")
    print(f"toc_to_pdf_offset: {page_offset}")
    print(f"nodes: {len(flat_nodes)}")
    print(f"outputs: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
