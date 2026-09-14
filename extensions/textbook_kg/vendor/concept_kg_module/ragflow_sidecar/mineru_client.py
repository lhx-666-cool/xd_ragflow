from __future__ import annotations

import json
import re
import shutil
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fitz  # type: ignore
import requests


class MinerUClientError(RuntimeError):
    pass


class JobCanceled(RuntimeError):
    pass


class RemoteMinerUClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int = 1800,
        backend: str = "pipeline",
        parse_method: str = "ocr",
        session: requests.Session | None = None,
        max_extract_bytes: int = 8 * 1024**3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.backend = backend
        self.parse_method = parse_method
        self.session = session or requests.Session()
        self.max_extract_bytes = max_extract_bytes

    def check_ready(self) -> tuple[bool, str]:
        if not self.base_url:
            return False, "MINERU_API_URL is not configured."
        failures: list[str] = []
        for suffix in ("/health", "/openapi.json"):
            url = f"{self.base_url}{suffix}"
            try:
                response = self.session.get(url, timeout=5)
                if response.status_code < 400:
                    return True, url
                failures.append(f"{url}: HTTP {response.status_code}")
            except requests.RequestException as exc:
                failures.append(f"{url}: {exc}")
        return False, "; ".join(failures)

    def parse_pdf(
        self,
        *,
        pdf_path: Path,
        output_dir: Path,
        chunk_size: int,
        lang: str,
        progress_callback: Callable[[int, int], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Path:
        if not self.base_url:
            raise MinerUClientError("MINERU_API_URL is not configured.")
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive.")
        output_dir.mkdir(parents=True, exist_ok=True)
        with fitz.open(pdf_path) as document:
            page_count = document.page_count
        if page_count < 1:
            raise MinerUClientError("The PDF has no pages.")

        ranges = [
            (start, min(page_count - 1, start + chunk_size - 1))
            for start in range(0, page_count, chunk_size)
        ]
        index_payload: list[dict[str, Any]] = []
        for index, (start, end) in enumerate(ranges, start=1):
            if cancel_check and cancel_check():
                raise JobCanceled("The job was canceled.")
            content_list = self._parse_range(
                pdf_path=pdf_path,
                output_dir=output_dir,
                start=start,
                end=end,
                lang=lang,
            )
            index_payload.append(
                {
                    "start": start,
                    "end": end,
                    "content_list": str(content_list),
                }
            )
            if progress_callback:
                progress_callback(index, len(ranges))

        index_path = output_dir / "chunk_index.json"
        index_path.write_text(
            json.dumps(index_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        merged_path = output_dir / "merged_content_list.json"
        self.merge_content_lists(index_payload, merged_path)
        return merged_path

    def _parse_range(
        self,
        *,
        pdf_path: Path,
        output_dir: Path,
        start: int,
        end: int,
        lang: str,
    ) -> Path:
        chunk_dir = output_dir / f"chunk_{start:04d}_{end:04d}"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        zip_path = chunk_dir / "mineru_output.zip"
        extract_dir = chunk_dir / "extracted"

        data = {
            "output_dir": "./output",
            "lang_list": lang,
            "backend": self.backend,
            "parse_method": self.parse_method,
            "formula_enable": "false",
            "table_enable": "false",
            "return_md": "true",
            "return_middle_json": "true",
            "return_model_output": "false",
            "return_content_list": "true",
            "return_images": "false",
            "response_format_zip": "true",
            "start_page_id": str(start),
            "end_page_id": str(end),
        }
        response = None
        try:
            with pdf_path.open("rb") as handle:
                response = self.session.post(
                    f"{self.base_url}/file_parse",
                    files={"files": (pdf_path.name, handle, "application/pdf")},
                    data=data,
                    headers={"Accept": "application/zip, application/json"},
                    timeout=self.timeout_seconds,
                    stream=True,
                )
                response.raise_for_status()
                with zip_path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
        except requests.RequestException as exc:
            raise MinerUClientError(f"MinerU request failed for pages {start + 1}-{end + 1}: {exc}") from exc
        finally:
            if response is not None:
                response.close()

        if not zipfile.is_zipfile(zip_path):
            preview = zip_path.read_bytes()[:512].decode("utf-8", errors="replace")
            raise MinerUClientError(
                f"MinerU returned a non-ZIP response for pages {start + 1}-{end + 1}: {preview}"
            )
        self.safe_extract_zip(zip_path, extract_dir, self.max_extract_bytes)
        return self.find_content_list(extract_dir, pdf_path.stem)

    @staticmethod
    def safe_extract_zip(zip_path: Path, output_dir: Path, max_extract_bytes: int) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        base = output_dir.resolve()
        total_size = 0
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                if member.flag_bits & 0x1:
                    raise MinerUClientError(f"Encrypted ZIP entry is not allowed: {member.filename}")
                unix_mode = member.external_attr >> 16
                if stat.S_ISLNK(unix_mode):
                    raise MinerUClientError(f"ZIP symbolic link is not allowed: {member.filename}")
                normalized = member.filename.replace("\\", "/")
                if normalized.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", normalized):
                    raise MinerUClientError(f"Absolute ZIP path is not allowed: {member.filename}")
                parts = [part for part in normalized.split("/") if part not in {"", "."}]
                if any(part == ".." for part in parts):
                    raise MinerUClientError(f"ZIP path traversal is not allowed: {member.filename}")
                total_size += int(member.file_size)
                if total_size > max_extract_bytes:
                    raise MinerUClientError("MinerU ZIP output exceeds the extraction size limit.")
                destination = (output_dir.joinpath(*parts)).resolve()
                if destination != base and base not in destination.parents:
                    raise MinerUClientError(f"ZIP entry escapes the output directory: {member.filename}")
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)

    @staticmethod
    def find_content_list(root: Path, pdf_stem: str) -> Path:
        candidates = sorted(
            {
                *root.rglob("content_list.json"),
                *root.rglob("*_content_list.json"),
            }
        )
        if not candidates:
            raise MinerUClientError("MinerU ZIP does not contain a content_list JSON file.")
        stem_matches = [path for path in candidates if pdf_stem in path.name or pdf_stem in path.parts]
        return (stem_matches or candidates)[0]

    @staticmethod
    def merge_content_lists(index_payload: list[dict[str, Any]], merged_path: Path) -> None:
        merged: list[dict[str, Any]] = []
        for item in index_payload:
            start = int(item["start"])
            content_path = Path(str(item["content_list"]))
            payload = json.loads(content_path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise MinerUClientError(f"Invalid content list: {content_path}")
            for block in payload:
                if not isinstance(block, dict):
                    continue
                copied = dict(block)
                if "page_idx" in copied:
                    copied["page_idx"] = int(copied["page_idx"]) + start
                merged.append(copied)
        merged_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
