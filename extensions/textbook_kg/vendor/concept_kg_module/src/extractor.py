from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .models import DocumentNode, ExtractionBatch, RawEntity, RawRelation
from .utils import extract_json_payload, log, normalize_whitespace, tokenize_text


class BaseExtractionClient(ABC):
    @abstractmethod
    def run(self, prompt: str, node: DocumentNode) -> str:
        raise NotImplementedError


class OpenAICompatibleClient(BaseExtractionClient):
    def __init__(
        self,
        base_url: str,
        api_key_env: str,
        model: str,
        temperature: float = 0,
        max_tokens: int = 1600,
        timeout_seconds: int = 90,
        retries: int = 2,
        response_format_json: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.response_format_json = response_format_json

        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"Environment variable `{self.api_key_env}` is required for LLM extraction.")
        self.api_key = api_key

    def run(self, prompt: str, node: DocumentNode) -> str:
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a careful information extraction engine. Output JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.response_format_json:
            payload["response_format"] = {"type": "json_object"}

        request = urllib.request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "Connection": "close",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    response_payload = json.loads(response.read().decode("utf-8"))
                    return response_payload["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as exc:
                last_error = self._format_http_error(exc)
                if not _is_retryable_http_error(getattr(exc, "code", None), str(last_error)):
                    break
                if attempt >= self.retries:
                    break
                sleep_seconds = 1.5 * (attempt + 1)
                log("extractor", f"Retrying node {node.source_node_id} after LLM error: {last_error}")
                time.sleep(sleep_seconds)
            except (
                urllib.error.URLError,
                http.client.IncompleteRead,
                ssl.SSLError,
                ConnectionError,
                TimeoutError,
                urllib.error.HTTPError,
                KeyError,
                json.JSONDecodeError,
            ) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                sleep_seconds = 1.5 * (attempt + 1)
                log("extractor", f"Retrying node {node.source_node_id} after LLM error: {exc}")
                time.sleep(sleep_seconds)
        raise RuntimeError(f"LLM request failed for node {node.source_node_id}: {last_error}")

    def _format_http_error(self, exc: urllib.error.HTTPError) -> RuntimeError:
        status_code = getattr(exc, "code", None)
        response_body = ""
        try:
            if exc.fp is not None:
                response_body = _compact_error_body(exc.read().decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            response_body = ""

        if status_code == 401:
            message = (
                f"HTTP 401 Unauthorized from {self.base_url}. "
                f"Check `{self.api_key_env}` in the same terminal session, and confirm the key matches this provider."
            )
            if response_body:
                message = f"{message} Response body: {response_body}"
            return RuntimeError(message)

        trace_id = ""
        try:
            trace_id = exc.headers.get("x-siliconcloud-trace-id", "").strip()
        except Exception:  # noqa: BLE001
            trace_id = ""

        if response_body:
            suffix = f" trace_id={trace_id}" if trace_id else ""
            return RuntimeError(f"HTTP {status_code} from {self.base_url}: {response_body}{suffix}")
        suffix = f" trace_id={trace_id}" if trace_id else ""
        return RuntimeError(f"HTTP {status_code} from {self.base_url}: {exc.reason}{suffix}")


def _compact_error_body(response_body: str, limit: int = 800) -> str:
    compacted = normalize_whitespace(response_body)
    if len(compacted) <= limit:
        return compacted
    return f"{compacted[:limit].rstrip()}... [truncated]"


def _is_retryable_http_error(status_code: int | None, message: str) -> bool:
    lowered = message.lower()
    non_retryable_markers = [
        "account balance is not sufficient",
        "余额",
        "model disabled",
        "browser_signature_banned",
        "do not retry",
        "restricted access",
        "access denied",
        "edgeone",
    ]
    if any(marker in lowered for marker in non_retryable_markers):
        return False
    return status_code in {408, 409, 425, 429, 500, 502, 503, 504}


class MockLLMClient(BaseExtractionClient):
    RELATION_PATTERNS: list[tuple[str, str]] = [
        (r"(?P<head>[A-Z][A-Za-z0-9_\- ]+?)\s+is a[n]?\s+(?P<tail>[A-Z][A-Za-z0-9_\- ]+)", "is_a"),
        (r"(?P<head>[A-Z][A-Za-z0-9_\- ]+?)\s+is used for\s+(?P<tail>[A-Z][A-Za-z0-9_\- ]+)", "used_for"),
        (r"(?P<head>[A-Z][A-Za-z0-9_\- ]+?)\s+consists of\s+(?P<tail>[A-Z][A-Za-z0-9_\- ]+)", "consists_of"),
        (r"(?P<head>[A-Z][A-Za-z0-9_\- ]+?)\s+is implemented by\s+(?P<tail>[A-Z][A-Za-z0-9_\- ]+)", "implemented_by"),
        (r"(?P<head>[A-Z][A-Za-z0-9_\- ]+?)\s+is evaluated by\s+(?P<tail>[A-Z][A-Za-z0-9_\- ]+)", "evaluated_by"),
        (r"(?P<head>[A-Z][A-Za-z0-9_\- ]+?)\s+is derived from\s+(?P<tail>[A-Z][A-Za-z0-9_\- ]+)", "derived_from"),
        (r"(?P<head>[A-Z][A-Za-z0-9_\- ]+?)\s+affects\s+(?P<tail>[A-Z][A-Za-z0-9_\- ]+)", "affects"),
        (r"(?P<head>[A-Z][A-Za-z0-9_\- ]+?)\s+causes\s+(?P<tail>[A-Z][A-Za-z0-9_\- ]+)", "causes"),
        (r"(?P<head>[A-Z][A-Za-z0-9_\- ]+?)\s+is part of\s+(?P<tail>[A-Z][A-Za-z0-9_\- ]+)", "part_of"),
        (r"(?P<head>[A-Z][A-Za-z0-9_\- ]+?)\s+is compared with\s+(?P<tail>[A-Z][A-Za-z0-9_\- ]+)", "compared_with"),
        (r"(?P<head>[A-Z][A-Za-z0-9_\- ]+?)\s+is an instance of\s+(?P<tail>[A-Z][A-Za-z0-9_\- ]+)", "instance_of"),
    ]

    def run(self, prompt: str, node: DocumentNode) -> str:
        content = node.content
        entities: dict[str, dict[str, Any]] = {}
        relations: list[dict[str, Any]] = []
        sentences = [chunk.strip() for chunk in re.split(r"(?<=[.!?。；;])\s+", content) if chunk.strip()]

        for sentence in sentences:
            for pattern, relation_type in self.RELATION_PATTERNS:
                for match in re.finditer(pattern, sentence):
                    head = normalize_whitespace(match.group("head"))
                    tail = normalize_whitespace(match.group("tail"))
                    if not head or not tail:
                        continue
                    entities.setdefault(head.lower(), self._build_entity(head, sentence, node.source_node_id))
                    entities.setdefault(tail.lower(), self._build_entity(tail, sentence, node.source_node_id))
                    relations.append(
                        {
                            "head": head,
                            "relation": relation_type,
                            "tail": tail,
                            "source_node_id": node.source_node_id,
                            "evidence": sentence,
                        }
                    )

        if not entities:
            fallback_terms = self._extract_candidate_terms(content)
            for term in fallback_terms[:3]:
                entities.setdefault(term.lower(), self._build_entity(term, "", node.source_node_id))

        return json.dumps({"entities": list(entities.values()), "relations": relations}, ensure_ascii=False)

    def _extract_candidate_terms(self, content: str) -> list[str]:
        terms: list[str] = []
        for token in tokenize_text(content):
            if len(token) <= 2:
                continue
            if token.isdigit():
                continue
            if re.fullmatch(r"[\u4e00-\u9fff]", token):
                continue
            if token[0].isalpha():
                pretty = token.replace("_", " ").strip()
                terms.append(pretty[:1].upper() + pretty[1:])
        deduped: list[str] = []
        seen: set[str] = set()
        for term in terms:
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(term)
        return deduped

    def _build_entity(self, name: str, sentence: str, source_node_id: str) -> dict[str, Any]:
        lowered = name.lower()
        tokens = set(tokenize_text(name))
        entity_type = "Concept"
        inferred_from_definition = self._infer_type_from_definition(name, sentence)
        if inferred_from_definition:
            entity_type = inferred_from_definition
        elif any(keyword in tokens for keyword in ["algorithm"]):
            entity_type = "Algorithm"
        elif any(keyword in tokens for keyword in ["queue", "stack", "tree", "graph", "heap", "list", "structure"]):
            entity_type = "Structure"
        elif "method" in tokens:
            entity_type = "Method"
        elif "principle" in tokens:
            entity_type = "Principle"
        elif "formula" in tokens:
            entity_type = "Formula"
        elif "process" in tokens:
            entity_type = "Process"
        elif "metric" in tokens:
            entity_type = "Metric"
        elif "tool" in tokens:
            entity_type = "Tool"
        elif "phenomenon" in tokens:
            entity_type = "Phenomenon"

        definition = sentence if name in sentence else ""
        return {
            "name": name,
            "alias": [],
            "type": entity_type,
            "definition": definition,
            "source_node_id": source_node_id,
        }

    def _infer_type_from_definition(self, name: str, sentence: str) -> str | None:
        sentence_lower = sentence.lower()
        name_lower = name.lower()
        match = re.search(rf"{re.escape(name_lower)}\s+is a[n]?\s+([a-z][a-z \-]+)", sentence_lower)
        if not match:
            return None
        tail = match.group(1).strip()
        if tail.startswith("algorithm"):
            return "Algorithm"
        if tail.startswith("structure"):
            return "Structure"
        if tail.startswith("method"):
            return "Method"
        if tail.startswith("principle"):
            return "Principle"
        if tail.startswith("formula"):
            return "Formula"
        if tail.startswith("process"):
            return "Process"
        if tail.startswith("metric"):
            return "Metric"
        if tail.startswith("tool"):
            return "Tool"
        if tail.startswith("phenomenon"):
            return "Phenomenon"
        if tail.startswith("concept"):
            return "Concept"
        return None


def build_extraction_client(settings: dict[str, Any], backend_override: str | None = None) -> BaseExtractionClient:
    llm_settings = settings.get("llm", {})
    backend = backend_override or llm_settings.get("backend", "openai_compatible")
    if backend == "mock":
        return MockLLMClient()
    if backend != "openai_compatible":
        raise ValueError(f"Unsupported LLM backend: {backend}")
    return OpenAICompatibleClient(
        base_url=llm_settings.get("base_url", "https://api.openai.com/v1"),
        api_key_env=llm_settings.get("api_key_env", "OPENAI_API_KEY"),
        model=llm_settings.get("model", "gpt-4o-mini"),
        temperature=float(llm_settings.get("temperature", 0)),
        max_tokens=int(llm_settings.get("max_tokens", 1600)),
        timeout_seconds=int(llm_settings.get("timeout_seconds", 90)),
        retries=int(llm_settings.get("retries", 2)),
        response_format_json=bool(llm_settings.get("response_format_json", True)),
    )


class EntityRelationExtractor:
    def __init__(
        self,
        client: BaseExtractionClient,
        prompt_template_path: Path,
        schema: dict[str, Any],
        max_content_chars: int = 4000,
        retry_content_chars: list[int] | None = None,
        max_entities_per_node: int = 12,
        max_relations_per_node: int = 12,
    ) -> None:
        self.client = client
        self.prompt_template = prompt_template_path.read_text(encoding="utf-8")
        self.schema = schema
        self.max_content_chars = max_content_chars
        self.retry_content_chars = retry_content_chars or []
        self.max_entities_per_node = max_entities_per_node
        self.max_relations_per_node = max_relations_per_node

    def extract_nodes(self, nodes: list[DocumentNode]) -> list[ExtractionBatch]:
        results: list[ExtractionBatch] = []
        total = len(nodes)
        failed = 0
        for index, node in enumerate(nodes, start=1):
            log("extractor", f"Progress {index}/{total} ({index / total:.1%}) | extracting {node.source_node_id}")
            batch = self.extract_node(node)
            if batch.error:
                failed += 1
                log(
                    "extractor",
                    f"Progress {index}/{total} ({index / total:.1%}) | failed={failed} | node={node.source_node_id}",
                )
            else:
                log(
                    "extractor",
                    f"Progress {index}/{total} ({index / total:.1%}) | ok | node={node.source_node_id}",
                )
            results.append(batch)
        return results

    def extract_node(self, node: DocumentNode) -> ExtractionBatch:
        raw_response = ""
        last_error: Exception | None = None
        attempted_limits: list[int] = []
        for content_limit in self._content_limits():
            if content_limit in attempted_limits:
                continue
            attempted_limits.append(content_limit)
            try:
                prompt = self._render_prompt(node, content_limit)
                raw_response = self.client.run(prompt, node)
                payload = extract_json_payload(raw_response)
                batch = self._payload_to_batch(payload, node, raw_response)
                if len(attempted_limits) > 1:
                    log(
                        "extractor",
                        f"Node {node.source_node_id} recovered after retry with content limit {content_limit}",
                    )
                return batch
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if not self._should_retry(exc):
                    break
                log(
                    "extractor",
                    f"Retrying node {node.source_node_id} with a shorter content window after parse error: {exc}",
                )
                continue

        log("extractor", f"Node {node.source_node_id} extraction failed: {last_error}")
        return ExtractionBatch(
            source_node_id=node.source_node_id,
            entities=[],
            relations=[],
            raw_response=raw_response,
            error=str(last_error) if last_error else "Unknown extraction failure",
        )

    def _content_limits(self) -> list[int]:
        limits = [self.max_content_chars]
        limits.extend(int(value) for value in self.retry_content_chars if int(value) > 0)
        return limits

    def _should_retry(self, exc: Exception) -> bool:
        message = str(exc).lower()
        retry_markers = [
            "expecting",
            "unterminated",
            "no valid json",
            "must both be arrays",
            "payload must be a json object",
        ]
        return any(marker in message for marker in retry_markers)

    def _render_prompt(self, node: DocumentNode, content_limit: int) -> str:
        entity_types = self.schema.get("entity_types", {})
        relation_types = self.schema.get("relation_types", {})
        entity_lines = [f"- {name}: {description}" for name, description in entity_types.items()]
        relation_lines = [f"- {name}: {description}" for name, description in relation_types.items()]
        content = node.content[:content_limit]
        output_budget = "\n".join(
            [
                f"- Return at most {self.max_entities_per_node} entities.",
                f"- Return at most {self.max_relations_per_node} relations.",
            ]
        )
        prompt = self.prompt_template
        prompt = prompt.replace("${SOURCE_NODE_ID}", node.source_node_id)
        prompt = prompt.replace("${NODE_LABEL}", node.label)
        prompt = prompt.replace("${ENTITY_TYPES}", "\n".join(entity_lines))
        prompt = prompt.replace("${RELATION_TYPES}", "\n".join(relation_lines))
        prompt = prompt.replace("${OUTPUT_BUDGET}", output_budget)
        prompt = prompt.replace("${CONTENT}", content)
        return prompt

    def _payload_to_batch(self, payload: Any, node: DocumentNode, raw_response: str) -> ExtractionBatch:
        if not isinstance(payload, dict):
            raise ValueError("LLM payload must be a JSON object.")
        raw_entities = payload.get("entities") or []
        raw_relations = payload.get("relations") or []
        if not isinstance(raw_entities, list) or not isinstance(raw_relations, list):
            raise ValueError("`entities` and `relations` must both be arrays.")

        entities: list[RawEntity] = []
        for index, item in enumerate(raw_entities, start=1):
            if not isinstance(item, dict):
                continue
            entities.append(
                RawEntity(
                    raw_entity_id=f"{node.source_node_id}::e{index:03d}",
                    name=normalize_whitespace(item.get("name")),
                    alias=[normalize_whitespace(alias) for alias in (item.get("alias") or []) if normalize_whitespace(alias)],
                    entity_type=normalize_whitespace(item.get("type") or item.get("entity_type")),
                    definition=normalize_whitespace(item.get("definition")),
                    source_node_id=normalize_whitespace(item.get("source_node_id")) or node.source_node_id,
                )
            )

        relations: list[RawRelation] = []
        for index, item in enumerate(raw_relations, start=1):
            if not isinstance(item, dict):
                continue
            relations.append(
                RawRelation(
                    raw_relation_id=f"{node.source_node_id}::r{index:03d}",
                    head=normalize_whitespace(item.get("head")),
                    relation=normalize_whitespace(item.get("relation")),
                    tail=normalize_whitespace(item.get("tail")),
                    source_node_id=normalize_whitespace(item.get("source_node_id")) or node.source_node_id,
                    evidence=normalize_whitespace(item.get("evidence")),
                )
            )

        return ExtractionBatch(
            source_node_id=node.source_node_id,
            entities=entities,
            relations=relations,
            raw_response=raw_response,
            error=None,
        )
