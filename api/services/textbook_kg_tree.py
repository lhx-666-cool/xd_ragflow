from __future__ import annotations

import hashlib
import json
import re
from typing import Any


TREE_SCHEMA_VERSION = "ragflow-textbook-tree/v1"


class TextbookKgTreeError(ValueError):
    pass


def _text(value: Any, *, max_chars: int) -> str:
    if value is None:
        return ""
    normalized = re.sub(r"\s+", " ", str(value).replace("\x00", " ")).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _optional_int(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def prepare_textbook_tree(
    content: bytes,
    *,
    max_bytes: int = 10 * 1024 * 1024,
    max_nodes: int = 10_000,
    max_depth: int = 20,
    preview_chars: int = 800,
) -> dict[str, Any]:
    if len(content) > max_bytes:
        raise TextbookKgTreeError("The textbook chapter tree artifact is too large")
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TextbookKgTreeError("The textbook chapter tree artifact is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise TextbookKgTreeError("The textbook chapter tree must be a JSON object")
    chapters = payload.get("chapters")
    if not isinstance(chapters, list):
        raise TextbookKgTreeError("The textbook chapter tree is missing chapters")

    state = {"node_count": 0, "max_depth": 0}

    def visit(raw: Any, path: tuple[int, ...], depth: int) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise TextbookKgTreeError("Every textbook chapter tree node must be an object")
        if depth > max_depth:
            raise TextbookKgTreeError("The textbook chapter tree exceeds the depth limit")
        state["node_count"] += 1
        if state["node_count"] > max_nodes:
            raise TextbookKgTreeError("The textbook chapter tree exceeds the node limit")
        state["max_depth"] = max(state["max_depth"], depth)

        marker = _text(raw.get("marker"), max_chars=100)
        title = _text(raw.get("title"), max_chars=500)
        label = _text(raw.get("label"), max_chars=600) or " ".join(
            value for value in (marker, title) if value
        )
        if not label:
            label = "Untitled section"
        children = raw.get("children") or []
        if not isinstance(children, list):
            raise TextbookKgTreeError("Every textbook chapter tree node children field must be a list")

        identity = f"{'/'.join(map(str, path))}|{marker}|{title}"
        node_id = "chapter-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        normalized_children = [visit(child, (*path, index), depth + 1) for index, child in enumerate(children)]
        content_value = str(raw.get("content") or "")
        return {
            "id": node_id,
            "marker": marker,
            "title": title,
            "label": label,
            "level": _optional_int(raw.get("level")) or depth,
            "toc_page_start": _optional_int(raw.get("toc_page_start")),
            "toc_page_end": _optional_int(raw.get("toc_page_end")),
            "pdf_page_start": _optional_int(raw.get("pdf_page_start")),
            "pdf_page_end": _optional_int(raw.get("pdf_page_end")),
            "content_preview": _text(content_value, max_chars=preview_chars),
            "content_length": len(content_value),
            "child_count": len(normalized_children),
            "children": normalized_children,
        }

    normalized_chapters = [visit(chapter, (index,), 1) for index, chapter in enumerate(chapters)]
    return {
        "schema_version": TREE_SCHEMA_VERSION,
        "book_title": _text(payload.get("book_title"), max_chars=500) or "Textbook",
        "toc_pages_pdf": payload.get("toc_pages_pdf") if isinstance(payload.get("toc_pages_pdf"), list) else [],
        "node_count": state["node_count"],
        "max_depth": state["max_depth"],
        "chapters": normalized_chapters,
    }
