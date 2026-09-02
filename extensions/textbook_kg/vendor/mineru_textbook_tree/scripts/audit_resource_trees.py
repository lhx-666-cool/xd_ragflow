from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


SECTION_MARKER_RE = re.compile(r"^\d+(?:\.\d+)+$")
CHAPTER_MARKER_RE = re.compile(r"^第\s*(\d+)\s*章$")
APPENDIX_MARKER_RE = re.compile(r"^附录\s*([A-Za-zＡ-Ｚ])")
CHAPTER_NUMBER_RE = re.compile(r"第\s*(\d+)\s*章")
TRAILING_SYMBOL_NOISE_RE = re.compile(r"[+\-_=~`^*#|\\/<>]{4,}$")
IO_OCR_RE = re.compile(r"(?<![A-Za-z])(?:1\s*[/／\\]\s*[O0]|1\s*[Vv]\s*[O0]|[Vv]\s*[/／\\]?\s*[O0])")
OCR_REPLACEMENT_MARK_RE = re.compile(r"[�□■●◆◇]{1,}")
LONG_PUNCT_NOISE_RE = re.compile(r"([.。·•…]{4,}|[-_]{4,})")
ANCILLARY_TITLE_FRAGMENTS = (
    "关键术语",
    "复习题",
    "习题",
    "练习",
    "思考题",
    "推荐读物",
    "推荐阅读",
    "参考文献",
    "参考书目",
    "进一步阅读",
)
GENERIC_TITLE_ALLOWLIST = {
    "概述",
    "小结",
    "本章小结",
    "总结",
    "引言",
}


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    book: str
    path: str
    message: str
    suggestion: str = ""
    marker: str = ""
    title: str = ""
    pdf_page: str = ""


@dataclass
class NodeRef:
    raw: dict[str, Any]
    parent: "NodeRef | None"
    index: int
    path_markers: tuple[str, ...]
    path_titles: tuple[str, ...]
    children: list["NodeRef"] = field(default_factory=list)

    @property
    def marker(self) -> str:
        return str(self.raw.get("marker") or "").strip()

    @property
    def title(self) -> str:
        return str(self.raw.get("title") or "").strip()

    @property
    def level(self) -> int | None:
        value = self.raw.get("level")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @property
    def pdf_start(self) -> int | None:
        return as_int(self.raw.get("pdf_page_start"))

    @property
    def pdf_end(self) -> int | None:
        return as_int(self.raw.get("pdf_page_end"))

    @property
    def toc_start(self) -> int | None:
        return as_int(self.raw.get("toc_page_start"))

    @property
    def content(self) -> str:
        return str(self.raw.get("content") or "")

    @property
    def display_path(self) -> str:
        parts = []
        for marker, title in zip(self.path_markers, self.path_titles):
            label = f"{marker} {title}".strip()
            parts.append(label or "(untitled)")
        return " > ".join(parts)


@dataclass
class BookAudit:
    path: Path
    book_title: str
    issues: list[Issue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        severities = {issue.severity for issue in self.issues}
        if "ERROR" in severities:
            return "ERROR"
        if "WARN" in severities:
            return "WARN"
        return "OK"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit generated textbook content trees for mounting, ordering, and OCR/entity issues."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(r"C:\Users\ki\Desktop\resource-w"),
        help="Directory containing *_tree folders. Default: C:\\Users\\ki\\Desktop\\resource-w",
    )
    parser.add_argument("--book-dir", action="append", default=[], help="Only audit specific output folder name(s).")
    parser.add_argument("--output-md", type=Path, help="Optional Markdown report path.")
    parser.add_argument("--output-json", type=Path, help="Optional JSON report path.")
    parser.add_argument("--output-csv", type=Path, help="Optional CSV issue report path.")
    parser.add_argument("--max-content-heading-checks", type=int, default=300, help="Max nodes per book to compare title against content heading.")
    parser.add_argument("--fail-on", choices=["none", "warn", "error"], default="none", help="Exit non-zero on WARN or ERROR.")
    parser.add_argument("--quiet-ok", action="store_true", help="Do not print per-book OK lines to stdout.")
    return parser.parse_args()


def as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_marker(marker: str) -> str:
    return re.sub(r"\s+", "", marker.strip().lstrip("*"))


def normalize_title(title: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", title).lower()


def marker_parts(marker: str) -> tuple[int, ...] | None:
    marker = normalize_marker(marker)
    if not SECTION_MARKER_RE.fullmatch(marker):
        return None
    return tuple(int(part) for part in marker.split("."))


def chapter_number(marker: str) -> int | None:
    match = CHAPTER_MARKER_RE.fullmatch(normalize_marker(marker))
    if match:
        return int(match.group(1))
    parts = marker_parts(marker)
    if parts:
        return parts[0]
    return None


def expected_level(marker: str) -> int | None:
    clean = normalize_marker(marker)
    if CHAPTER_MARKER_RE.fullmatch(clean) or APPENDIX_MARKER_RE.match(clean):
        return 1
    parts = marker_parts(clean)
    if parts:
        return len(parts)
    return None


def direct_parent_marker(marker: str) -> str | None:
    parts = marker_parts(marker)
    if not parts:
        return None
    if len(parts) == 2:
        return f"第{parts[0]}章"
    return ".".join(str(part) for part in parts[:-1])


def numeric_sort_key(marker: str) -> tuple[int, ...] | None:
    clean = normalize_marker(marker)
    chapter = CHAPTER_MARKER_RE.fullmatch(clean)
    if chapter:
        return (int(chapter.group(1)),)
    return marker_parts(clean)


def build_tree_refs(chapters: list[dict[str, Any]]) -> list[NodeRef]:
    def visit(raw: dict[str, Any], parent: NodeRef | None, index: int) -> NodeRef:
        marker = str(raw.get("marker") or "").strip()
        title = str(raw.get("title") or "").strip()
        path_markers = (*parent.path_markers, marker) if parent else (marker,)
        path_titles = (*parent.path_titles, title) if parent else (title,)
        node = NodeRef(raw=raw, parent=parent, index=index, path_markers=path_markers, path_titles=path_titles)
        node.children = [visit(child, node, child_index) for child_index, child in enumerate(raw.get("children") or []) if isinstance(child, dict)]
        return node

    return [visit(chapter, None, index) for index, chapter in enumerate(chapters)]


def flatten(nodes: Iterable[NodeRef]) -> list[NodeRef]:
    out: list[NodeRef] = []
    for node in nodes:
        out.append(node)
        out.extend(flatten(node.children))
    return out


def issue(
    audit: BookAudit,
    severity: str,
    code: str,
    node: NodeRef | None,
    message: str,
    suggestion: str = "",
) -> None:
    audit.issues.append(
        Issue(
            severity=severity,
            code=code,
            book=audit.book_title,
            path=node.display_path if node else "",
            message=message,
            suggestion=suggestion,
            marker=node.marker if node else "",
            title=node.title if node else "",
            pdf_page=format_page_range(node.pdf_start, node.pdf_end) if node else "",
        )
    )


def book_issue(audit: BookAudit, severity: str, code: str, message: str, suggestion: str = "") -> None:
    issue(audit, severity, code, None, message, suggestion)


def format_page_range(start: int | None, end: int | None) -> str:
    if start is None:
        return ""
    if end is None or end == start:
        return str(start)
    return f"{start}-{end}"


def load_book(output_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    tree_path = output_dir / "content_tree.json"
    if not tree_path.exists():
        return None, "missing content_tree.json"
    try:
        return json.loads(tree_path.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001 - report malformed user data cleanly.
        return None, f"failed to read content_tree.json: {exc}"


def audit_output_dir(output_dir: Path, max_content_heading_checks: int = 300) -> BookAudit:
    payload, load_error = load_book(output_dir)
    book_title = output_dir.name.removesuffix("_tree")
    if isinstance(payload, dict):
        book_title = str(payload.get("book_title") or book_title)
    audit = BookAudit(path=output_dir, book_title=book_title)

    if load_error:
        book_issue(audit, "ERROR", "missing-tree-json", load_error, "重新生成该书，或确认目录确实是树输出目录。")
        return audit

    assert payload is not None
    chapters = payload.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        book_issue(audit, "ERROR", "empty-chapters", "content_tree.json has no chapters.", "检查目录页识别或重新生成该书。")
        return audit

    roots = build_tree_refs(chapters)
    nodes = flatten(roots)
    audit.stats.update(
        {
            "nodes": len(nodes),
            "root_count": len(roots),
            "toc_pages_pdf": payload.get("toc_pages_pdf"),
            "toc_to_pdf_offset": payload.get("toc_to_pdf_offset"),
            "content_tree_mtime": mtime_iso(output_dir / "content_tree.json"),
            "graph_html_mtime": mtime_iso(output_dir / "content_tree_graph.html"),
        }
    )

    check_sidecar_files(audit)
    check_root_chapter_sequence(audit, roots)
    check_markers_and_levels(audit, nodes)
    check_parent_mounting(audit, nodes)
    check_sibling_order_and_duplicates(audit, nodes)
    check_page_ranges(audit, nodes)
    check_titles(audit, nodes)
    check_content_heading_titles(audit, nodes[:max_content_heading_checks])
    return audit


def mtime_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def check_sidecar_files(audit: BookAudit) -> None:
    required = ["content_tree.json", "content_tree_graph.html"]
    for name in required:
        if not (audit.path / name).exists():
            book_issue(audit, "ERROR", "missing-output-file", f"Missing {name}.", "重新渲染或重新生成该书。")

    tree_path = audit.path / "content_tree.json"
    for html_name in ("content_tree_graph.html", "content_tree_visual.html"):
        html_path = audit.path / html_name
        if tree_path.exists() and html_path.exists() and html_path.stat().st_mtime + 1 < tree_path.stat().st_mtime:
            book_issue(
                audit,
                "WARN",
                "stale-html",
                f"{html_name} is older than content_tree.json.",
                "重新运行 render_content_tree_graph.py / render_content_tree_visual.py，或重新生成该书。",
            )


def check_root_chapter_sequence(audit: BookAudit, roots: list[NodeRef]) -> None:
    chapter_roots = [(node, chapter_number(node.marker)) for node in roots]
    numbered = [(node, number) for node, number in chapter_roots if number is not None]
    if not numbered:
        book_issue(audit, "ERROR", "no-numbered-chapters", "No numbered root chapters found.", "检查目录页是否识别错，或是否误用了书签/正文。")
        return

    numbers = [number for _, number in numbered]
    for prev, current in zip(numbers, numbers[1:]):
        if current < prev:
            book_issue(audit, "ERROR", "chapter-order", f"Root chapter order decreases: 第{prev}章 -> 第{current}章.", "检查根章节顺序和目录页 OCR。")
        elif current - prev > 1:
            missing = ", ".join(f"第{number}章" for number in range(prev + 1, current))
            book_issue(audit, "WARN", "chapter-gap", f"Possible missing root chapter(s): {missing}.", "对照 PDF 目录确认是否漏解析目录页。")


def check_markers_and_levels(audit: BookAudit, nodes: list[NodeRef]) -> None:
    for node in nodes:
        if not node.marker:
            issue(audit, "WARN", "empty-marker", node, "Node marker is empty.", "检查是否是无编号章节，必要时手动确认。")
        if not node.title:
            issue(audit, "ERROR", "empty-title", node, "Node title is empty.", "重新解析目录页或修正标题。")

        expected = expected_level(node.marker)
        if expected is not None and node.level is not None and node.level != expected:
            issue(
                audit,
                "WARN",
                "level-mismatch",
                node,
                f"Stored level={node.level}, expected level={expected} from marker.",
                "通常可通过 rebuild_existing_trees.py 或重新生成修复。",
            )

        parts = marker_parts(node.marker)
        if parts and any(part <= 0 for part in parts[1:]):
            issue(audit, "ERROR", "invalid-marker-zero", node, "Numeric marker contains zero component.", "检查 OCR 是否把章节号识别错。")
        if parts and any(len(str_part) > 1 and str_part.startswith("0") for str_part in normalize_marker(node.marker).split(".")):
            issue(audit, "WARN", "leading-zero-marker", node, "Numeric marker has a leading zero component.", "检查是否是 OCR 粘连或页码混入。")


def check_parent_mounting(audit: BookAudit, nodes: list[NodeRef]) -> None:
    by_marker: dict[str, list[NodeRef]] = defaultdict(list)
    for node in nodes:
        by_marker[normalize_marker(node.marker)].append(node)

    for node in nodes:
        parts = marker_parts(node.marker)
        if not parts:
            continue
        expected_parent = direct_parent_marker(node.marker)
        if not expected_parent:
            continue
        expected_parent_key = normalize_marker(expected_parent)
        actual_parent_key = normalize_marker(node.parent.marker) if node.parent else ""

        if len(parts) == 2 and node.parent and CHAPTER_MARKER_RE.fullmatch(expected_parent_key):
            if chapter_number(node.parent.marker) == parts[0]:
                continue

        if len(parts) > 2 and actual_parent_key == expected_parent_key:
            continue

        expected_nodes = by_marker.get(expected_parent_key, [])
        if expected_nodes:
            issue(
                audit,
                "ERROR",
                "wrong-parent",
                node,
                f"Expected parent marker {expected_parent}, actual parent is {node.parent.marker if node.parent else '(root)'}.",
                "该节点大概率挂错层级；重新运行修复脚本或检查父节点是否被 OCR 误识别。",
            )
        elif len(parts) > 2:
            issue(
                audit,
                "WARN",
                "missing-parent",
                node,
                f"Expected parent marker {expected_parent} is missing.",
                "可能需要补中间节点，或确认目录本身是否跳级。",
            )


def check_sibling_order_and_duplicates(audit: BookAudit, nodes: list[NodeRef]) -> None:
    for parent in nodes:
        if len(parent.children) < 2:
            continue

        marker_counter = Counter(normalize_marker(child.marker) for child in parent.children if child.marker)
        for marker, count in marker_counter.items():
            if count > 1:
                issue(
                    audit,
                    "ERROR",
                    "duplicate-sibling-marker",
                    parent,
                    f"Duplicate child marker under same parent: {marker} appears {count} times.",
                    "检查是否是 OCR 把小节标题拆成重复节点。",
                )

        previous_key: tuple[int, ...] | None = None
        previous_child: NodeRef | None = None
        for child in parent.children:
            key = numeric_sort_key(child.marker)
            if key is None:
                continue
            if previous_key is not None and key < previous_key:
                issue(
                    audit,
                    "WARN",
                    "sibling-marker-order",
                    child,
                    f"Sibling marker order decreases: {previous_child.marker if previous_child else previous_key} -> {child.marker}.",
                    "对照目录页确认是否排序错乱；有时是 OCR 页码导致。",
                )
            previous_key = key
            previous_child = child

        previous_page: int | None = None
        previous_page_child: NodeRef | None = None
        for child in parent.children:
            if child.pdf_start is None:
                continue
            if previous_page is not None and child.pdf_start + 1 < previous_page:
                issue(
                    audit,
                    "WARN",
                    "sibling-page-order",
                    child,
                    f"Sibling PDF page order decreases: {previous_page_child.marker if previous_page_child else ''} page {previous_page} -> {child.marker} page {child.pdf_start}.",
                    "检查目录页码或 offset 是否错误。",
                )
            previous_page = child.pdf_start
            previous_page_child = child


def check_page_ranges(audit: BookAudit, nodes: list[NodeRef]) -> None:
    for node in nodes:
        if node.pdf_start is not None and node.pdf_end is not None and node.pdf_end < node.pdf_start:
            issue(audit, "ERROR", "invalid-page-range", node, "pdf_page_end is before pdf_page_start.", "重新计算页码范围。")
        if node.parent and node.pdf_start is not None:
            parent_start = node.parent.pdf_start
            parent_end = node.parent.pdf_end
            if parent_start is not None and node.pdf_start < parent_start:
                issue(audit, "WARN", "child-before-parent-range", node, "Child starts before parent page range.", "检查挂载或页码 offset。")
            if parent_end is not None and node.pdf_start > parent_end + 1:
                issue(audit, "WARN", "child-after-parent-range", node, "Child starts after parent page range.", "检查父节点范围是否没有传播。")


def check_titles(audit: BookAudit, nodes: list[NodeRef]) -> None:
    for node in nodes:
        title = node.title
        normalized = normalize_title(title)
        if not title:
            continue
        if IO_OCR_RE.search(title):
            issue(audit, "WARN", "ocr-io-token", node, "Title has suspicious I/O OCR token such as 1/O, 1/0, IVO or VO.", "重新生成或应用 I/O OCR 修正规则。")
        if TRAILING_SYMBOL_NOISE_RE.search(title):
            issue(audit, "WARN", "trailing-symbol-noise", node, "Title ends with repeated symbol noise.", "可用标题清洗规则移除 OCR 噪声。")
        if OCR_REPLACEMENT_MARK_RE.search(title) or LONG_PUNCT_NOISE_RE.search(title):
            issue(audit, "WARN", "ocr-title-noise", node, "Title contains OCR replacement/noise characters.", "对照目录页修正标题。")
        if len(title) > 60:
            issue(audit, "WARN", "long-title", node, "Title is unusually long and may be body text.", "检查是否把正文句子识别成章节标题。")
        if any(fragment in title for fragment in ANCILLARY_TITLE_FRAGMENTS):
            issue(audit, "WARN", "ancillary-node", node, "Ancillary material appears as a node title.", "如果不希望习题/复习题入图，可过滤或重建。")
        if not normalized and title not in GENERIC_TITLE_ALLOWLIST:
            issue(audit, "WARN", "non-informative-title", node, "Title has little searchable text after normalization.", "检查标题是否为空白或符号。")

        parts = marker_parts(node.marker)
        if parts and len(parts[-1:]) == 1:
            tail = str(parts[-1])
            if len(tail) >= 3:
                shorter = ".".join(str(part) for part in (*parts[:-1], int(tail[:-1])))
                sibling_by_marker = {
                    normalize_marker(sibling.marker): sibling
                    for sibling in (node.parent.children if node.parent else [])
                }
                sibling = sibling_by_marker.get(shorter)
                if sibling and (titles_likely_same(sibling.title, node.title) or re.match(r"^\s*[/／\\]\s*[O0]", node.title)):
                    issue(
                        audit,
                        "WARN",
                        "possible-glued-marker",
                        node,
                        f"Marker {node.marker} may be glued OCR form of sibling {shorter}.",
                        "例如 7.2.11 /O 可能应为 7.2.1 I/O。",
                    )


def titles_likely_same(left: str, right: str) -> bool:
    left_key = normalize_title(left)
    right_key = normalize_title(right)
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


def check_content_heading_titles(audit: BookAudit, nodes: list[NodeRef]) -> None:
    for node in nodes:
        if not node.content or not node.marker:
            continue
        candidate = first_heading_title_from_content(node)
        if not candidate:
            continue
        current_key = normalize_title(node.title)
        candidate_key = normalize_title(candidate)
        if not current_key or not candidate_key:
            continue
        if current_key == candidate_key:
            continue
        if current_key in candidate_key or candidate_key in current_key:
            continue
        issue(
            audit,
            "WARN",
            "title-content-heading-mismatch",
            node,
            f"Node title differs from first content heading: content heading is '{candidate}'.",
            "目录标题和正文标题不一致；优先对照 PDF 目录/正文确认。",
        )


def first_heading_title_from_content(node: NodeRef) -> str | None:
    marker = re.escape(node.marker)
    for raw_line in node.content.splitlines()[:12]:
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(rf"^\*?{marker}\s*(.+)$", line)
        if match:
            candidate = match.group(1).strip(" .:：、，,;；·-—")
            return candidate or None
    return None


def summarize(audits: list[BookAudit]) -> dict[str, Any]:
    issue_counter = Counter()
    severity_counter = Counter()
    status_counter = Counter(audit.status for audit in audits)
    for audit in audits:
        for item in audit.issues:
            issue_counter[item.code] += 1
            severity_counter[item.severity] += 1
    return {
        "books": len(audits),
        "status": dict(status_counter),
        "severities": dict(severity_counter),
        "issue_codes": dict(issue_counter.most_common()),
    }


def render_markdown(audits: list[BookAudit]) -> str:
    summary = summarize(audits)
    lines = [
        "# Resource Tree Audit Report",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Books: {summary['books']}",
        f"- Status: {summary['status']}",
        f"- Severities: {summary['severities']}",
        "",
        "## Books",
        "",
    ]

    for audit in audits:
        lines.append(f"### {audit.status} {audit.path.name}")
        lines.append("")
        lines.append(f"- Title: {audit.book_title}")
        lines.append(f"- Nodes: {audit.stats.get('nodes', 0)}")
        lines.append(f"- Root chapters: {audit.stats.get('root_count', 0)}")
        lines.append(f"- TOC pages: {audit.stats.get('toc_pages_pdf')}")
        lines.append(f"- Page offset: {audit.stats.get('toc_to_pdf_offset')}")
        if not audit.issues:
            lines.append("- Issues: none")
            lines.append("")
            continue
        lines.append("")
        for item in audit.issues:
            where = f" `{item.path}`" if item.path else ""
            page = f" PDF {item.pdf_page}" if item.pdf_page else ""
            lines.append(f"- **{item.severity} [{item.code}]**{where}{page}: {item.message}")
            if item.suggestion:
                lines.append(f"  Suggestion: {item.suggestion}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def audit_to_json(audits: list[BookAudit]) -> dict[str, Any]:
    return {
        "summary": summarize(audits),
        "books": [
            {
                "path": str(audit.path),
                "book_title": audit.book_title,
                "status": audit.status,
                "stats": audit.stats,
                "issues": [item.__dict__ for item in audit.issues],
            }
            for audit in audits
        ],
    }


def write_csv(path: Path, audits: list[BookAudit]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["severity", "code", "book", "marker", "title", "pdf_page", "path", "message", "suggestion"],
        )
        writer.writeheader()
        for audit in audits:
            for item in audit.issues:
                writer.writerow(item.__dict__)


def find_output_dirs(root: Path, selected: set[str]) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Root does not exist: {root}")
    dirs = [path for path in root.iterdir() if path.is_dir() and (path / "content_tree.json").exists()]
    if selected:
        dirs = [path for path in dirs if path.name in selected]
    return sorted(dirs, key=lambda path: path.name.lower())


def print_console(audits: list[BookAudit], quiet_ok: bool = False) -> None:
    for audit in audits:
        if quiet_ok and audit.status == "OK":
            continue
        counts = Counter(item.severity for item in audit.issues)
        print(
            f"[{audit.status}] {audit.path.name} "
            f"nodes={audit.stats.get('nodes', 0)} "
            f"errors={counts.get('ERROR', 0)} warns={counts.get('WARN', 0)}"
        )
        for item in audit.issues[:8]:
            where = f" | {item.path}" if item.path else ""
            print(f"  - {item.severity} {item.code}{where}: {item.message}")
        if len(audit.issues) > 8:
            print(f"  ... {len(audit.issues) - 8} more issues")

    summary = summarize(audits)
    print(f"summary: books={summary['books']} status={summary['status']} severities={summary['severities']}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    root = args.root.resolve()
    selected = set(args.book_dir)
    output_dirs = find_output_dirs(root, selected)
    audits = [audit_output_dir(path, max_content_heading_checks=args.max_content_heading_checks) for path in output_dirs]

    print_console(audits, quiet_ok=args.quiet_ok)

    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(render_markdown(audits), encoding="utf-8")
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(audit_to_json(audits), ensure_ascii=False, indent=2), encoding="utf-8")
    if args.output_csv:
        write_csv(args.output_csv, audits)

    if args.fail_on == "error" and any(audit.status == "ERROR" for audit in audits):
        return 2
    if args.fail_on == "warn" and any(audit.status in {"WARN", "ERROR"} for audit in audits):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
