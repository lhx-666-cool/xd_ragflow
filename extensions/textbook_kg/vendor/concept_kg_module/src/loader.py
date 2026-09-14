from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import DocumentNode
from .utils import load_json, normalize_text_block, normalize_whitespace, split_paragraphs, stable_id


SENTENCE_BOUNDARIES = ["\n", "。", "！", "？", ".", "!", "?", "；", ";"]


@dataclass
class LoadedDocument:
    source_document: str
    book_title: str
    nodes: list[DocumentNode]
    document_nodes: list[DocumentNode] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.document_nodes:
            self.document_nodes = list(self.nodes)


class DocumentGraphLoader:
    def __init__(
        self,
        include_structural_nodes: bool = True,
        include_paragraph_nodes: bool = True,
        paragraph_min_chars: int = 80,
        max_nodes: int = 0,
        pipeline: str = "hierarchical",
        semantic_chunk_chars: int = 2200,
        semantic_chunk_min_chars: int = 300,
        semantic_chunk_overlap_chars: int = 0,
    ) -> None:
        self.include_structural_nodes = include_structural_nodes
        self.include_paragraph_nodes = include_paragraph_nodes
        self.paragraph_min_chars = paragraph_min_chars
        self.max_nodes = max_nodes
        self.pipeline = normalize_whitespace(pipeline).lower() or "hierarchical"
        if self.pipeline not in {"flat", "hierarchical"}:
            raise ValueError("`pipeline` must be `flat` or `hierarchical`.")
        self.semantic_chunk_chars = max(1, int(semantic_chunk_chars))
        self.semantic_chunk_min_chars = max(1, int(semantic_chunk_min_chars))
        self.semantic_chunk_overlap_chars = max(0, int(semantic_chunk_overlap_chars))

    def load(self, doc_graph_path: Path) -> LoadedDocument:
        payload = load_json(doc_graph_path)
        book_title = normalize_whitespace(payload.get("book_title")) or doc_graph_path.stem
        chapters = payload.get("chapters") or []
        if not isinstance(chapters, list):
            raise ValueError("`chapters` must be a list in doc graph JSON.")

        if self.pipeline == "flat":
            return self._load_flat(doc_graph_path=doc_graph_path, book_title=book_title, chapters=chapters)

        nodes: list[DocumentNode] = []
        document_nodes: list[DocumentNode] = []
        for chapter_index, chapter in enumerate(chapters, start=1):
            self._walk_hierarchical_node(
                node_payload=chapter,
                parent_id=None,
                path_labels=[book_title],
                sibling_index=chapter_index,
                extractable_sink=nodes,
                document_sink=document_nodes,
                chapter_node_id=None,
            )

        if self.max_nodes:
            nodes = nodes[: self.max_nodes]
        return LoadedDocument(
            source_document=str(doc_graph_path),
            book_title=book_title,
            nodes=nodes,
            document_nodes=document_nodes,
        )

    def _load_flat(self, doc_graph_path: Path, book_title: str, chapters: list[Any]) -> LoadedDocument:
        nodes: list[DocumentNode] = []
        for chapter_index, chapter in enumerate(chapters, start=1):
            self._walk_flat_node(
                node_payload=chapter,
                parent_id=None,
                path_labels=[book_title],
                sibling_index=chapter_index,
                sink=nodes,
                chapter_node_id=None,
            )
            if self.max_nodes and len(nodes) >= self.max_nodes:
                break

        if self.max_nodes:
            nodes = nodes[: self.max_nodes]
        return LoadedDocument(source_document=str(doc_graph_path), book_title=book_title, nodes=nodes, document_nodes=list(nodes))

    def _walk_flat_node(
        self,
        node_payload: dict[str, Any],
        parent_id: str | None,
        path_labels: list[str],
        sibling_index: int,
        sink: list[DocumentNode],
        chapter_node_id: str | None,
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
        current_chapter_node_id = source_node_id if level <= 1 or chapter_node_id is None else chapter_node_id

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
                    section_node_id=source_node_id if node_kind == "section" else None,
                    chapter_node_id=current_chapter_node_id,
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
                        section_node_id=source_node_id,
                        chapter_node_id=current_chapter_node_id,
                        chunk_index=paragraph_index,
                        chunk_count=len(paragraphs),
                    )
                )
                if self.max_nodes and len(sink) >= self.max_nodes:
                    return

        children = node_payload.get("children") or []
        if not isinstance(children, list):
            return
        next_path = path_labels + ([label or title] if label or title else [])
        for child_index, child in enumerate(children, start=1):
            if not isinstance(child, dict):
                continue
            self._walk_flat_node(
                node_payload=child,
                parent_id=source_node_id,
                path_labels=next_path,
                sibling_index=child_index,
                sink=sink,
                chapter_node_id=current_chapter_node_id,
            )
            if self.max_nodes and len(sink) >= self.max_nodes:
                return

    def _walk_hierarchical_node(
        self,
        node_payload: dict[str, Any],
        parent_id: str | None,
        path_labels: list[str],
        sibling_index: int,
        extractable_sink: list[DocumentNode],
        document_sink: list[DocumentNode],
        chapter_node_id: str | None,
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
        current_chapter_node_id = source_node_id if level <= 1 or chapter_node_id is None else chapter_node_id
        current_path = path_labels + ([label or title] if label or title else [])

        document_sink.append(
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
                path_labels=current_path,
                pdf_page_start=pdf_page_start,
                pdf_page_end=pdf_page_end,
                section_node_id=source_node_id if node_kind == "section" else None,
                chapter_node_id=current_chapter_node_id,
            )
        )

        children = node_payload.get("children") or []
        if not isinstance(children, list):
            children = []
        extractable_children = [
            child
            for child in children
            if isinstance(child, dict) and self._has_extractable_content(child)
        ]
        is_minimal_section = bool(content) and not extractable_children
        if is_minimal_section:
            chunks = self._split_semantic_chunks(content)
            for chunk_index, chunk in enumerate(chunks, start=1):
                chunk_id = f"{source_node_id}::c{chunk_index:03d}"
                chunk_node = DocumentNode(
                    node_id=chunk_id,
                    source_node_id=chunk_id,
                    parent_id=source_node_id,
                    marker=marker,
                    title=title,
                    label=f"{label or title} / Chunk {chunk_index}",
                    level=level + 1,
                    node_kind="semantic_chunk",
                    content=chunk,
                    path_labels=current_path + [f"Chunk {chunk_index}"],
                    pdf_page_start=pdf_page_start,
                    pdf_page_end=pdf_page_end,
                    section_node_id=source_node_id,
                    chapter_node_id=current_chapter_node_id,
                    chunk_index=chunk_index,
                    chunk_count=len(chunks),
                )
                document_sink.append(chunk_node)
                extractable_sink.append(chunk_node)

        for child_index, child in enumerate(children, start=1):
            if not isinstance(child, dict):
                continue
            self._walk_hierarchical_node(
                node_payload=child,
                parent_id=source_node_id,
                path_labels=current_path,
                sibling_index=child_index,
                extractable_sink=extractable_sink,
                document_sink=document_sink,
                chapter_node_id=current_chapter_node_id,
            )

    def _has_extractable_content(self, node_payload: dict[str, Any]) -> bool:
        content = normalize_text_block(node_payload.get("content") or "")
        if content:
            return True
        children = node_payload.get("children") or []
        if not isinstance(children, list):
            return False
        return any(self._has_extractable_content(child) for child in children if isinstance(child, dict))

    def _split_semantic_chunks(self, content: str) -> list[str]:
        text = normalize_text_block(content)
        if not text:
            return []
        if len(text) <= self.semantic_chunk_chars:
            return [text]

        paragraph_parts = split_paragraphs(text, min_chars=self.semantic_chunk_min_chars)
        if not paragraph_parts:
            paragraph_parts = [text]

        chunks: list[str] = []
        buffer = ""
        for part in paragraph_parts:
            if len(part) > self.semantic_chunk_chars:
                if buffer:
                    chunks.append(buffer)
                    buffer = ""
                chunks.extend(self._split_long_text(part))
                continue
            candidate = f"{buffer}\n{part}".strip() if buffer else part
            if len(candidate) <= self.semantic_chunk_chars:
                buffer = candidate
                continue
            if buffer:
                chunks.append(buffer)
            buffer = part
        if buffer:
            chunks.append(buffer)

        if len(chunks) >= 2 and len(chunks[-1]) < self.semantic_chunk_min_chars:
            merged_tail = f"{chunks[-2]}\n{chunks[-1]}".strip()
            if len(merged_tail) <= self.semantic_chunk_chars + self.semantic_chunk_min_chars:
                chunks[-2] = merged_tail
                chunks.pop()

        return self._apply_chunk_overlap([chunk for chunk in chunks if chunk])

    def _split_long_text(self, text: str) -> list[str]:
        remaining = normalize_text_block(text)
        chunks: list[str] = []
        while len(remaining) > self.semantic_chunk_chars:
            window = remaining[: self.semantic_chunk_chars]
            cut = self._find_chunk_boundary(window)
            chunks.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            chunks.append(remaining)
        return chunks

    def _find_chunk_boundary(self, window: str) -> int:
        min_cut = min(self.semantic_chunk_min_chars, len(window))
        best = -1
        for marker in SENTENCE_BOUNDARIES:
            best = max(best, window.rfind(marker))
        if best >= min_cut:
            return best + 1
        whitespace_match = list(re.finditer(r"\s+", window))
        if whitespace_match:
            last_match = whitespace_match[-1]
            if last_match.start() >= min_cut:
                return last_match.end()
        return len(window)

    def _apply_chunk_overlap(self, chunks: list[str]) -> list[str]:
        if self.semantic_chunk_overlap_chars <= 0 or len(chunks) <= 1:
            return chunks
        overlapped = [chunks[0]]
        for index in range(1, len(chunks)):
            prefix = chunks[index - 1][-self.semantic_chunk_overlap_chars :].strip()
            overlapped.append(f"{prefix}\n{chunks[index]}".strip() if prefix else chunks[index])
        return overlapped
