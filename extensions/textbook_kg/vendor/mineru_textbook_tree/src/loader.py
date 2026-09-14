from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import DocumentNode
from .utils import load_json, normalize_text_block, normalize_whitespace, split_paragraphs, stable_id


@dataclass
class LoadedDocument:
    source_document: str
    book_title: str
    nodes: list[DocumentNode]


class DocumentGraphLoader:
    def __init__(
        self,
        include_structural_nodes: bool = True,
        include_paragraph_nodes: bool = True,
        paragraph_min_chars: int = 80,
        max_nodes: int = 0,
    ) -> None:
        self.include_structural_nodes = include_structural_nodes
        self.include_paragraph_nodes = include_paragraph_nodes
        self.paragraph_min_chars = paragraph_min_chars
        self.max_nodes = max_nodes

    def load(self, doc_graph_path: Path) -> LoadedDocument:
        payload = load_json(doc_graph_path)
        book_title = normalize_whitespace(payload.get("book_title")) or doc_graph_path.stem
        chapters = payload.get("chapters") or []
        if not isinstance(chapters, list):
            raise ValueError("`chapters` must be a list in doc graph JSON.")

        nodes: list[DocumentNode] = []
        for chapter_index, chapter in enumerate(chapters, start=1):
            self._walk_node(
                node_payload=chapter,
                parent_id=None,
                path_labels=[book_title],
                sibling_index=chapter_index,
                sink=nodes,
            )
            if self.max_nodes and len(nodes) >= self.max_nodes:
                break

        if self.max_nodes:
            nodes = nodes[: self.max_nodes]
        return LoadedDocument(source_document=str(doc_graph_path), book_title=book_title, nodes=nodes)

    def _walk_node(
        self,
        node_payload: dict[str, Any],
        parent_id: str | None,
        path_labels: list[str],
        sibling_index: int,
        sink: list[DocumentNode],
    ) -> None:
        marker = normalize_whitespace(node_payload.get("marker"))
        title = normalize_whitespace(node_payload.get("title"))
        label = normalize_whitespace(node_payload.get("label")) or f"{marker} {title}".strip()
        level = int(node_payload.get("level") or 0)
        content = normalize_text_block(node_payload.get("content") or "")
        pdf_page_start = node_payload.get("pdf_page_start")
        pdf_page_end = node_payload.get("pdf_page_end")
        node_key = " / ".join(path_labels + [label or title or str(sibling_index)])
        source_node_id = stable_id("doc", f"{node_key}|{level}|{parent_id or 'root'}")
        node_kind = "chapter" if level <= 1 else "section"

        if self.include_structural_nodes and content:
            sink.append(
                DocumentNode(
                    node_id=source_node_id,
                    source_node_id=source_node_id,
                    parent_id=parent_id,
                    marker=marker,
                    title=title,
                    label=label or title,
                    level=level,
                    node_kind=node_kind,
                    content=content,
                    path_labels=path_labels + [label or title],
                    pdf_page_start=pdf_page_start,
                    pdf_page_end=pdf_page_end,
                )
            )
            if self.max_nodes and len(sink) >= self.max_nodes:
                return
        if self.include_paragraph_nodes and content:
            paragraphs = split_paragraphs(content, min_chars=self.paragraph_min_chars)
            for paragraph_index, paragraph in enumerate(paragraphs, start=1):
                paragraph_id = f"{source_node_id}::p{paragraph_index:03d}"
                sink.append(
                    DocumentNode(
                        node_id=paragraph_id,
                        source_node_id=paragraph_id,
                        parent_id=source_node_id,
                        marker=marker,
                        title=title,
                        label=f"{label or title} / Paragraph {paragraph_index}",
                        level=level + 1,
                        node_kind="paragraph",
                        content=paragraph,
                        path_labels=path_labels + [label or title, f"Paragraph {paragraph_index}"],
                        pdf_page_start=pdf_page_start,
                        pdf_page_end=pdf_page_end,
                    )
                )
                if self.max_nodes and len(sink) >= self.max_nodes:
                    return

        children = node_payload.get("children") or []
        if not isinstance(children, list):
            return
        next_path = path_labels + ([label or title] if label or title else [])
        for child_index, child in enumerate(children, start=1):
            self._walk_node(
                node_payload=child,
                parent_id=source_node_id,
                path_labels=next_path,
                sibling_index=child_index,
                sink=sink,
            )
            if self.max_nodes and len(sink) >= self.max_nodes:
                return
