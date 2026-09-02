from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


WHITESPACE_RE = re.compile(r"\s+")
WORD_OR_CJK_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+")


def log(stage: str, message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{stage}] {message}")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return data


def normalize_whitespace(text: str | None) -> str:
    if text is None:
        return ""
    return WHITESPACE_RE.sub(" ", text.replace("\u3000", " ")).strip()


def normalize_text_block(text: str | None) -> str:
    if not text:
        return ""
    lines = [normalize_whitespace(line) for line in text.replace("\r\n", "\n").split("\n")]
    compact = "\n".join(line for line in lines if line)
    return compact.strip()


def normalize_key(text: str | None) -> str:
    normalized = normalize_whitespace(text).lower()
    return normalized


def stable_id(prefix: str, raw_value: str, length: int = 12) -> str:
    digest = hashlib.sha1(raw_value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def split_paragraphs(text: str, min_chars: int = 80) -> list[str]:
    if not text.strip():
        return []
    raw_parts = re.split(r"\n\s*\n+", text.replace("\r\n", "\n"))
    cleaned_parts = [normalize_text_block(part) for part in raw_parts]
    cleaned_parts = [part for part in cleaned_parts if part]
    if not cleaned_parts:
        return []

    merged_parts: list[str] = []
    buffer = ""
    for part in cleaned_parts:
        if not buffer:
            buffer = part
            continue
        if len(buffer) < min_chars:
            buffer = f"{buffer}\n{part}".strip()
            continue
        merged_parts.append(buffer)
        buffer = part
    if buffer:
        merged_parts.append(buffer)

    if len(merged_parts) >= 2 and len(merged_parts[-1]) < min_chars:
        merged_parts[-2] = f"{merged_parts[-2]}\n{merged_parts[-1]}".strip()
        merged_parts.pop()
    return merged_parts


def extract_json_payload(raw_text: str) -> Any:
    text = raw_text.strip()
    if not text:
        raise ValueError("Empty LLM response.")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        return json.loads(fence_match.group(1))

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])

    raise ValueError("No valid JSON object found in LLM response.")


def tokenize_text(text: str) -> list[str]:
    return [token.lower() for token in WORD_OR_CJK_RE.findall(text)]
