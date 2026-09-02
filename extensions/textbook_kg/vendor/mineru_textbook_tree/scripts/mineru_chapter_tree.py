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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import fitz  # type: ignore
except Exception:  # pragma: no cover
    fitz = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent


TOC_KEYWORDS = ("目录", "contents", "table of contents")
DOT_LEADER_RE = re.compile(r"[\.\.．·•…·\-—_]{2,}")
MULTISPACE_RE = re.compile(r"\s+")
NUMERIC_ENTRY_RE = re.compile(
    r"^(?P<title>(?:第[0-9一二三四五六七八九十百零两]+[编部卷篇章节节]|chapter\s+\d+|\d+(?:\.\d+){0,5}|[（(]?[一二三四五六七八九十]+[)）]|[A-Z]\.)[^\d]*?.*?)\s*(?P<page>\d{1,4}|[ivxlcdmIVXLCDM]{1,8})$",
    re.IGNORECASE,
)
TRAILING_PAGE_RE = re.compile(r"^(?P<title>.+?)\s*(?P<page>\d{1,4}|[ivxlcdmIVXLCDM]{1,8})$", re.IGNORECASE)
SECTION_PREFIX_RE = re.compile(r"^(?P<prefix>\d+(?:\.\d+){0,5})\b")
CHINESE_CHAPTER_RE = re.compile(r"^第[0-9一二三四五六七八九十百零两]+([编部卷篇章节节])")
CHINESE_ENUM_RE = re.compile(r"^[（(]?[一二三四五六七八九十]+[)）]")
ALPHA_ENUM_RE = re.compile(r"^[A-Z]\.")
VALID_TEXT_TYPES = {"text", "title", "index", "list"}
NOISE_LINE_RE = re.compile(r"^(?:p\s*d\s*f\s*page\b|pdf\s*page\b|page\b)", re.IGNORECASE)
RETROACTIVE_TITLE_RE = re.compile(r"^(Partially|Fully)\s+Retroactive\s+", re.IGNORECASE)


@dataclass
class TocEntry:
    title: str
    toc_page: int
    level: int
    raw_line: str
    source_page: int


@dataclass
class ChapterNode:
    title: str
    level: int
    toc_page_start: int | None
    toc_page_end: int | None = None
    pdf_page_start: int | None = None
    pdf_page_end: int | None = None
    source_page: int | None = None
    children: list["ChapterNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "level": self.level,
            "toc_page_start": self.toc_page_start,
            "toc_page_end": self.toc_page_end,
            "pdf_page_start": self.pdf_page_start,
            "pdf_page_end": self.pdf_page_end,
            "source_page": self.source_page,
            "children": [child.to_dict() for child in self.children],
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use MinerU output to build a TOC-based chapter tree for scanned textbook PDFs."
    )
    parser.add_argument("--pdf", type=Path, help="Source PDF path.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Directory for MinerU output and generated chapter tree artifacts.",
    )
    parser.add_argument(
        "--content-list",
        type=Path,
        help="Existing MinerU content_list.json path. If omitted, the script tries to run MinerU or locate it in output-dir.",
    )
    parser.add_argument(
        "--book-title",
        default="教材",
        help="Root node label used in the generated tree graph.",
    )
    parser.add_argument(
        "--page-offset",
        type=int,
        default=None,
        help="Optional manual offset from TOC page number to actual PDF page number. Example: TOC 1 -> PDF 9 means offset=8.",
    )
    parser.add_argument(
        "--toc-pages",
        default="",
        help="Optional TOC page range in PDF page numbers, e.g. 2-4 or 2,3,4.",
    )
    parser.add_argument(
        "--backend",
        default="pipeline",
        choices=[
            "pipeline",
            "vlm-http-client",
            "hybrid-http-client",
            "vlm-auto-engine",
            "hybrid-auto-engine",
        ],
        help="MinerU backend to use when parsing the PDF.",
    )
    parser.add_argument(
        "--method",
        default="ocr",
        choices=["auto", "txt", "ocr"],
        help="MinerU parsing method. For scanned textbook PDFs, OCR is usually the right default.",
    )
    parser.add_argument(
        "--lang",
        default="ch",
        help="OCR language passed to MinerU, e.g. ch / en.",
    )
    parser.add_argument(
        "--mineru-command",
        default="",
        help="Optional explicit MinerU executable path. If omitted, the script checks PATH and .venv-mineru/Scripts/mineru(.exe).",
    )
    parser.add_argument(
        "--skip-mineru",
        action="store_true",
        help="Do not invoke MinerU even if --pdf is provided.",
    )
    parser.add_argument(
        "--structure-mode",
        default="auto",
        choices=["auto", "toc", "headings"],
        help="Choose TOC-based or page-heading-based tree building. Auto tries TOC first and falls back to headings.",
    )
    return parser.parse_args()


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u3000", " ")
    text = DOT_LEADER_RE.sub(" ", text)
    text = MULTISPACE_RE.sub(" ", text).strip()
    return text


def normalize_for_match(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text.lower())
    return text


def roman_to_int(token: str) -> int | None:
    token = token.upper()
    if not token or re.search(r"[^IVXLCDM]", token):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for char in reversed(token):
        value = values[char]
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    return total


def parse_page_number(token: str) -> int | None:
    token = normalize_text(token)
    if token.isdigit():
        return int(token)
    return roman_to_int(token)


def infer_level(title: str) -> int | None:
    title = normalize_text(title)
    if not title:
        return None

    section_match = SECTION_PREFIX_RE.match(title)
    if section_match:
        prefix = section_match.group("prefix")
        return prefix.count(".") + 1

    chapter_match = CHINESE_CHAPTER_RE.match(title)
    if chapter_match:
        unit = chapter_match.group(1)
        if unit in {"编", "部", "卷", "篇"}:
            return 1
        if unit in {"章"}:
            return 1
        if unit in {"节"}:
            return 2

    if title.lower().startswith("chapter "):
        return 1
    if re.match(r"^[^\w\u4e00-\u9fff]{0,3}\d+[^\d].*", title):
        return 1
    if CHINESE_ENUM_RE.match(title):
        return 2
    if ALPHA_ENUM_RE.match(title):
        return 2
    return None


def parse_toc_line(line: str) -> tuple[str, int] | None:
    cleaned = normalize_text(line)
    if not cleaned:
        return None
    lowered = cleaned.lower()
    if lowered in TOC_KEYWORDS:
        return None
    if NOISE_LINE_RE.match(cleaned):
        return None
    if len(cleaned) < 3:
        return None

    for pattern in (NUMERIC_ENTRY_RE, TRAILING_PAGE_RE):
        match = pattern.match(cleaned)
        if not match:
            continue
        title = normalize_text(match.group("title"))
        page = parse_page_number(match.group("page"))
        if not title or page is None:
            continue
        if infer_level(title) is None:
            continue
        return title, page
    return None


def resolve_mineru_command(explicit_command: str) -> str | None:
    if explicit_command:
        return str(Path(explicit_command).expanduser().resolve())

    path_command = shutil.which("mineru")
    if path_command:
        return path_command

    local_candidates = [
        PROJECT_ROOT / ".venv-mineru" / "Scripts" / "mineru.exe",
        PROJECT_ROOT / ".venv-mineru" / "Scripts" / "mineru",
        Path.cwd() / ".venv-mineru" / "Scripts" / "mineru.exe",
        Path.cwd() / ".venv-mineru" / "Scripts" / "mineru",
    ]
    for candidate in local_candidates:
        if candidate.exists():
            return str(candidate.resolve())
    return None


def run_mineru(pdf_path: Path, output_dir: Path, backend: str, method: str, lang: str, explicit_command: str) -> Path:
    mineru_cmd = resolve_mineru_command(explicit_command)
    if not mineru_cmd:
        raise FileNotFoundError(
            "未找到 mineru 命令。请先在 Python 3.10-3.12 环境中安装 `mineru[all]`，或传入 --mineru-command / --content-list。"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = output_dir / ".runtime_cache"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    shared_cache_dir = PROJECT_ROOT / ".mineru_cache"
    env = os.environ.copy()
    env_updates = {
        "MPLCONFIGDIR": str((runtime_dir / "matplotlib").resolve()),
        "YOLO_CONFIG_DIR": str((runtime_dir / "ultralytics").resolve()),
        "HF_HOME": str((shared_cache_dir / "huggingface").resolve()),
        "HUGGINGFACE_HUB_CACHE": str((shared_cache_dir / "huggingface" / "hub").resolve()),
        "TRANSFORMERS_CACHE": str((shared_cache_dir / "huggingface" / "transformers").resolve()),
        "MODELSCOPE_CACHE": str((shared_cache_dir / "modelscope").resolve()),
        "TORCH_HOME": str((shared_cache_dir / "torch").resolve()),
        "XDG_CACHE_HOME": str(runtime_dir.resolve()),
        "TMP": str((runtime_dir / "tmp").resolve()),
        "TEMP": str((runtime_dir / "tmp").resolve()),
        "PYTHONIOENCODING": "utf-8",
    }
    cache_keys = {
        "MPLCONFIGDIR",
        "YOLO_CONFIG_DIR",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "MODELSCOPE_CACHE",
        "TORCH_HOME",
        "XDG_CACHE_HOME",
        "TMP",
        "TEMP",
    }
    for key in cache_keys:
        Path(env_updates[key]).mkdir(parents=True, exist_ok=True)
    env.update(env_updates)
    command = [mineru_cmd, "-p", str(pdf_path), "-o", str(output_dir), "-b", backend, "-m", method, "-l", lang]
    subprocess.run(command, check=True, env=env)
    return locate_content_list(output_dir, pdf_path.stem)


def locate_content_list(output_dir: Path, pdf_stem: str | None = None) -> Path:
    patterns = []
    if pdf_stem:
        patterns.append(f"**/{pdf_stem}*_content_list.json")
    patterns.append("**/*_content_list.json")

    for pattern in patterns:
        candidates = sorted(output_dir.glob(pattern))
        if candidates:
            return candidates[0]
    raise FileNotFoundError(f"在 {output_dir} 中未找到 MinerU 输出的 content_list.json。")


def load_content_list(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("content_list.json 顶层结构不是 list。")
    return data


def build_page_blocks(content_list: list[dict[str, Any]]) -> dict[int, list[str]]:
    pages: dict[int, list[str]] = {}
    for block in content_list:
        if block.get("type") not in VALID_TEXT_TYPES:
            continue
        page_idx = int(block.get("page_idx", 0))
        raw_text = unicodedata.normalize("NFKC", str(block.get("text", ""))).replace("\u3000", " ")
        for raw_line in raw_text.splitlines() or [raw_text]:
            text = normalize_text(raw_line)
            if not text or NOISE_LINE_RE.match(text):
                continue
            pages.setdefault(page_idx + 1, []).append(text)
    return pages


def load_pdf_page_texts(pdf_path: Path) -> dict[int, str]:
    if fitz is None:
        return {}
    page_texts: dict[int, str] = {}
    with fitz.open(pdf_path) as doc:
        for page_number in range(doc.page_count):
            page_texts[page_number + 1] = normalize_text(doc.load_page(page_number).get_text("text"))
    return page_texts


def score_toc_page(lines: list[str], full_text: str) -> int:
    score = 0
    lowered = full_text.lower()
    if any(keyword in lowered for keyword in TOC_KEYWORDS):
        score += 8
    score += sum(1 for line in lines if parse_toc_line(line))
    return score


def parse_explicit_pages(spec: str) -> list[int]:
    if not spec:
        return []
    pages: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_str, end_str = chunk.split("-", 1)
            start = int(start_str)
            end = int(end_str)
            pages.extend(range(start, end + 1))
        else:
            pages.append(int(chunk))
    return sorted(set(pages))


def detect_toc_pages(page_blocks: dict[int, list[str]], page_texts: dict[int, str], explicit_pages: list[int]) -> list[int]:
    if explicit_pages:
        return explicit_pages

    scored: list[tuple[int, int]] = []
    for page_number, lines in page_blocks.items():
        full_text = page_texts.get(page_number, " ".join(lines))
        score = score_toc_page(lines, full_text)
        if score > 0:
            scored.append((page_number, score))

    if not scored:
        return []

    scored.sort()
    best_page, best_score = max(scored, key=lambda item: item[1])
    toc_pages = [best_page]
    current = best_page + 1
    while current in page_blocks:
        next_score = score_toc_page(page_blocks[current], page_texts.get(current, " ".join(page_blocks[current])))
        if next_score < max(2, best_score // 3):
            break
        toc_pages.append(current)
        current += 1

    previous = best_page - 1
    while previous in page_blocks:
        prev_score = score_toc_page(page_blocks[previous], page_texts.get(previous, " ".join(page_blocks[previous])))
        if prev_score < 2:
            break
        toc_pages.insert(0, previous)
        previous -= 1

    return toc_pages


def collect_toc_entries(page_blocks: dict[int, list[str]], toc_pages: list[int]) -> list[TocEntry]:
    entries: list[TocEntry] = []
    for page_number in toc_pages:
        for line in page_blocks.get(page_number, []):
            parsed = parse_toc_line(line)
            if not parsed:
                continue
            title, toc_page = parsed
            level = infer_level(title)
            if level is None:
                continue
            entries.append(TocEntry(title=title, toc_page=toc_page, level=level, raw_line=line, source_page=page_number))

    deduped: list[TocEntry] = []
    seen: set[tuple[str, int]] = set()
    for entry in entries:
        key = (normalize_for_match(entry.title), entry.toc_page)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def extract_heading_blocks(content_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    heading_blocks: list[dict[str, Any]] = []
    for block in content_list:
        text = normalize_text(str(block.get("text", "")))
        if not text:
            continue
        level = block.get("text_level")
        if level is None:
            level = 1 if block.get("type") == "title" else 0
        if int(level) <= 0:
            continue
        heading_blocks.append(
            {
                "text": text,
                "page": int(block.get("page_idx", 0)) + 1,
                "level": int(level),
            }
        )
    return heading_blocks


def titles_match(lhs: str, rhs: str) -> bool:
    a = normalize_for_match(lhs)
    b = normalize_for_match(rhs)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) >= 4 and shorter in longer:
        return True
    return False


def infer_page_offset(
    entries: list[TocEntry],
    heading_blocks: list[dict[str, Any]],
    page_blocks: dict[int, list[str]],
    toc_pages: list[int],
) -> int | None:
    offsets: list[int] = []
    top_entries = [entry for entry in entries if entry.level == 1]
    toc_end_page = max(toc_pages) if toc_pages else 0
    for entry in top_entries or entries:
        match = next((block for block in heading_blocks if titles_match(entry.title, block["text"])), None)
        if match:
            offsets.append(match["page"] - entry.toc_page)
            continue

        for page_number in sorted(page_blocks):
            if page_number <= toc_end_page:
                continue
            if any(titles_match(entry.title, line) for line in page_blocks[page_number]):
                offsets.append(page_number - entry.toc_page)
                break

    if not offsets:
        return None
    return int(statistics.median(offsets))


def build_tree(entries: list[TocEntry], page_offset: int | None, total_pages: int | None) -> list[ChapterNode]:
    if not entries:
        return []

    nodes = [
        ChapterNode(
            title=entry.title,
            level=entry.level,
            toc_page_start=entry.toc_page,
            source_page=entry.source_page,
            pdf_page_start=entry.toc_page + page_offset if page_offset is not None else None,
        )
        for entry in entries
    ]

    roots: list[ChapterNode] = []
    stack: list[ChapterNode] = []

    for node in nodes:
        while stack and stack[-1].level >= node.level:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)

    assign_end_pages(nodes, total_pages, page_offset)
    return roots


def assign_end_pages(flat_nodes: list[ChapterNode], total_pages: int | None, page_offset: int | None) -> None:
    for index, node in enumerate(flat_nodes):
        next_sibling_or_following = None
        for later in flat_nodes[index + 1 :]:
            if later.level <= node.level:
                next_sibling_or_following = later
                break
        if next_sibling_or_following:
            node.toc_page_end = max(node.toc_page_start, next_sibling_or_following.toc_page_start - 1)
            if page_offset is not None:
                node.pdf_page_end = max(node.pdf_page_start or 0, next_sibling_or_following.toc_page_start + page_offset - 1)
        elif total_pages is not None:
            node.pdf_page_end = total_pages
            if page_offset is not None:
                node.toc_page_end = max(node.toc_page_start, total_pages - page_offset)

        if node.pdf_page_start is not None and node.pdf_page_end is not None and node.pdf_page_end < node.pdf_page_start:
            node.pdf_page_end = node.pdf_page_start


def flatten_tree(nodes: list[ChapterNode]) -> list[ChapterNode]:
    flat: list[ChapterNode] = []
    for node in nodes:
        flat.append(node)
        flat.extend(flatten_tree(node.children))
    return flat


def render_ascii_tree(nodes: list[ChapterNode], prefix: str = "") -> list[str]:
    lines: list[str] = []
    for index, node in enumerate(nodes):
        is_last = index == len(nodes) - 1
        branch = "└─ " if is_last else "├─ "
        page_bits = []
        if node.toc_page_start:
            toc_range = (
                f"目录页 {node.toc_page_start}-{node.toc_page_end}"
                if node.toc_page_end and node.toc_page_end != node.toc_page_start
                else f"目录页 {node.toc_page_start}"
            )
            page_bits.append(toc_range)
        if node.pdf_page_start:
            pdf_range = (
                f"PDF页 {node.pdf_page_start}-{node.pdf_page_end}"
                if node.pdf_page_end and node.pdf_page_end != node.pdf_page_start
                else f"PDF页 {node.pdf_page_start}"
            )
            page_bits.append(pdf_range)
        suffix = f" ({' | '.join(page_bits)})" if page_bits else ""
        lines.append(f"{prefix}{branch}{node.title}{suffix}")
        child_prefix = f"{prefix}{'   ' if is_last else '│  '}"
        lines.extend(render_ascii_tree(node.children, child_prefix))
    return lines


def render_mermaid(book_title: str, roots: list[ChapterNode]) -> str:
    lines = ["graph TD"]
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"n{counter}"

    root_id = next_id()
    lines.append(f'    {root_id}["{escape_mermaid(book_title)}"]')

    def visit(parent_id: str, node: ChapterNode) -> None:
        node_id = next_id()
        label = node.title
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


def resolve_total_pages(pdf_path: Path | None, content_list: list[dict[str, Any]]) -> int | None:
    if pdf_path and fitz is not None and pdf_path.exists():
        with fitz.open(pdf_path) as doc:
            return doc.page_count
    page_numbers = [int(item.get("page_idx", 0)) + 1 for item in content_list if "page_idx" in item]
    return max(page_numbers) if page_numbers else None


def extract_heading_phrase(text: str) -> str | None:
    normalized = normalize_text(text)
    if not normalized:
        return None

    watermark_match = re.search(r"\bkczno\d*\b", normalized, re.IGNORECASE)
    if watermark_match:
        normalized = normalized[: watermark_match.start()].strip()
    if not normalized:
        return None

    if re.match(r"^[\u4e00-\u9fff]", normalized):
        return normalized[:80].strip()

    tokens = normalized.split()
    if not tokens:
        return None

    heading_tokens: list[str] = []
    for index, token in enumerate(tokens):
        if re.search(r"[\u4e00-\u9fff]", token):
            break
        if re.fullmatch(r"kczno\d*", token, re.IGNORECASE):
            break
        if index > 0 and re.fullmatch(r"[A-Z][A-Z]+", token):
            break
        heading_tokens.append(token)
        if len(heading_tokens) >= 6:
            break

    heading = " ".join(heading_tokens).strip(" .,:;")
    if not heading:
        return None

    heading = heading.replace("Deﬁnition", "Definition")
    if heading.lower().startswith("referrences"):
        return "References"
    return heading


def infer_heading_level(title: str) -> int:
    if RETROACTIVE_TITLE_RE.match(title):
        return 2
    return 1


def assign_pdf_end_pages(flat_nodes: list[ChapterNode], total_pages: int | None) -> None:
    for index, node in enumerate(flat_nodes):
        next_sibling_or_following = None
        for later in flat_nodes[index + 1 :]:
            if later.level <= node.level:
                next_sibling_or_following = later
                break
        if next_sibling_or_following:
            node.pdf_page_end = max(node.pdf_page_start or 0, (next_sibling_or_following.pdf_page_start or 0) - 1)
        elif total_pages is not None:
            node.pdf_page_end = total_pages

        if node.pdf_page_start is not None and node.pdf_page_end is not None and node.pdf_page_end < node.pdf_page_start:
            node.pdf_page_end = node.pdf_page_start


def build_heading_tree(pdf_path: Path | None, total_pages: int | None) -> list[ChapterNode]:
    if not pdf_path or not pdf_path.exists():
        raise RuntimeError("Heading mode requires a readable --pdf file.")
    if fitz is None:
        raise RuntimeError("Heading mode requires PyMuPDF (fitz).")

    raw_titles: list[tuple[int, str]] = []
    with fitz.open(pdf_path) as doc:
        for page_number in range(doc.page_count):
            title = extract_heading_phrase(doc.load_page(page_number).get_text("text"))
            if not title:
                continue
            page = page_number + 1
            if raw_titles and raw_titles[-1][1] == title:
                continue
            raw_titles.append((page, title))

    cleaned_titles: list[tuple[int, str]] = []
    for page, title in raw_titles:
        if cleaned_titles and cleaned_titles[0][1] == title and page != cleaned_titles[0][0]:
            continue
        cleaned_titles.append((page, title))

    nodes = [
        ChapterNode(
            title=title,
            level=infer_heading_level(title),
            toc_page_start=None,
            pdf_page_start=page,
            source_page=page,
        )
        for page, title in cleaned_titles
    ]

    roots: list[ChapterNode] = []
    stack: list[ChapterNode] = []
    for node in nodes:
        while stack and stack[-1].level >= node.level:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)

    assign_pdf_end_pages(nodes, total_pages)
    return roots


def write_outputs(output_dir: Path, book_title: str, toc_pages: list[int], offset: int | None, roots: list[ChapterNode]) -> None:
    tree_path = output_dir / "chapter_tree.json"
    ascii_path = output_dir / "chapter_tree.txt"
    mermaid_path = output_dir / "chapter_tree.mmd"

    payload = {
        "book_title": book_title,
        "toc_pages_pdf": toc_pages,
        "toc_to_pdf_offset": offset,
        "chapters": [node.to_dict() for node in roots],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    tree_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ascii_lines = [book_title] + render_ascii_tree(roots)
    ascii_path.write_text("\n".join(ascii_lines) + "\n", encoding="utf-8")
    mermaid_path.write_text(render_mermaid(book_title, roots), encoding="utf-8")


def main() -> int:
    args = parse_args()
    pdf_path = args.pdf.resolve() if args.pdf else None
    output_dir = args.output_dir.resolve()

    content_list_path = args.content_list.resolve() if args.content_list else None
    if not content_list_path and pdf_path and not args.skip_mineru:
        content_list_path = run_mineru(
            pdf_path,
            output_dir,
            args.backend,
            args.method,
            args.lang,
            args.mineru_command,
        )
    elif not content_list_path and output_dir.exists():
        content_list_path = locate_content_list(output_dir, pdf_path.stem if pdf_path else None)

    if not content_list_path:
        raise FileNotFoundError("没有可用的 content_list.json。请提供 --content-list，或使用 --pdf 让脚本调用 MinerU。")

    content_list = load_content_list(content_list_path)
    page_blocks = build_page_blocks(content_list)
    page_texts = load_pdf_page_texts(pdf_path) if pdf_path and pdf_path.exists() else {}

    toc_pages = detect_toc_pages(page_blocks, page_texts, parse_explicit_pages(args.toc_pages))
    if not toc_pages:
        raise RuntimeError("未检测到目录页。请使用 --toc-pages 显式指定目录所在的 PDF 页码范围。")

    entries = collect_toc_entries(page_blocks, toc_pages)
    if not entries:
        raise RuntimeError("目录页已找到，但未能解析出有效目录项。请检查 OCR 结果，或手动清洗 content_list.json。")

    heading_blocks = extract_heading_blocks(content_list)
    total_pages = resolve_total_pages(pdf_path, content_list)
    offset = (
        args.page_offset
        if args.page_offset is not None
        else infer_page_offset(entries, heading_blocks, page_blocks, toc_pages)
    )
    roots = build_tree(entries, offset, total_pages)

    write_outputs(output_dir, args.book_title, toc_pages, offset, roots)

    print(f"content_list: {content_list_path}")
    print(f"toc_pages_pdf: {toc_pages}")
    print(f"toc_to_pdf_offset: {offset}")
    print(f"nodes: {len(flatten_tree(roots))}")
    print(f"outputs: {output_dir}")
    return 0


def main_v2() -> int:
    args = parse_args()
    pdf_path = args.pdf.resolve() if args.pdf else None
    output_dir = args.output_dir.resolve()

    content_list_path = args.content_list.resolve() if args.content_list else None
    if not content_list_path and pdf_path and not args.skip_mineru:
        content_list_path = run_mineru(
            pdf_path,
            output_dir,
            args.backend,
            args.method,
            args.lang,
            args.mineru_command,
        )
    elif not content_list_path and output_dir.exists():
        content_list_path = locate_content_list(output_dir, pdf_path.stem if pdf_path else None)

    if not content_list_path:
        raise FileNotFoundError("No usable content_list.json was found. Pass --content-list or let the script run MinerU with --pdf.")

    content_list = load_content_list(content_list_path)
    total_pages = resolve_total_pages(pdf_path, content_list)
    page_blocks = build_page_blocks(content_list)
    page_texts = load_pdf_page_texts(pdf_path) if pdf_path and pdf_path.exists() else {}

    toc_pages: list[int] = []
    offset: int | None = None
    roots: list[ChapterNode]
    use_heading_mode = args.structure_mode == "headings"

    if args.structure_mode != "headings":
        toc_pages = detect_toc_pages(page_blocks, page_texts, parse_explicit_pages(args.toc_pages))
        entries = collect_toc_entries(page_blocks, toc_pages) if toc_pages else []
        if toc_pages and entries:
            heading_blocks = extract_heading_blocks(content_list)
            offset = (
                args.page_offset
                if args.page_offset is not None
                else infer_page_offset(entries, heading_blocks, page_blocks, toc_pages)
            )
            roots = build_tree(entries, offset, total_pages)
        elif args.structure_mode == "toc":
            raise RuntimeError("TOC mode was requested, but no usable TOC entries were detected.")
        else:
            use_heading_mode = True

    if use_heading_mode:
        roots = build_heading_tree(pdf_path, total_pages)
        if roots and normalize_for_match(roots[0].title) == normalize_for_match(args.book_title):
            roots = roots[1:]

    write_outputs(output_dir, args.book_title, toc_pages, offset, roots)

    print(f"content_list: {content_list_path}")
    print(f"toc_pages_pdf: {toc_pages}")
    print(f"toc_to_pdf_offset: {offset}")
    print(f"nodes: {len(flatten_tree(roots))}")
    print(f"outputs: {output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main_v2())
    except Exception as exc:  # pragma: no cover
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
