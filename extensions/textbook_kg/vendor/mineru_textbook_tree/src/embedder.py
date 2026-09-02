from __future__ import annotations

import base64
import hashlib
import http.client
import json
import math
import os
import ssl
import struct
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .utils import ensure_dir, log, tokenize_text


class EmbeddingBackend(ABC):
    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class HashingEmbeddingBackend(EmbeddingBackend):
    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for token in tokenize_text(text):
            bucket = int(hashlib.sha1(token.encode("utf-8")).hexdigest(), 16) % self.dim
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


class OpenAICompatibleEmbeddingBackend(EmbeddingBackend):
    def __init__(
        self,
        base_url: str,
        api_key_env: str,
        model: str,
        dimensions: int | None = None,
        encoding_format: str = "float",
        timeout_seconds: int = 90,
        retries: int = 2,
        batch_size: int = 32,
        cache_dir: Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.model = model.strip()
        self.dimensions = dimensions
        self.encoding_format = encoding_format
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.batch_size = max(1, batch_size)
        self.cache_dir = cache_dir
        self.cache_path = self._build_cache_path(cache_dir)
        self._cache: dict[str, list[float]] = {}

        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"Environment variable `{self.api_key_env}` is required for embedding requests.")
        if not self.model:
            raise RuntimeError(
                "An embedding model is required. Set `embedding.model` in config/settings.yaml "
                "or set the `TEXTBOOK_KG_EMBEDDING_MODEL` environment variable."
            )
        self.api_key = api_key
        self._load_cache()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float] | None] = [None] * len(texts)
        uncached_pairs: list[tuple[int, str]] = []
        for index, text in enumerate(texts):
            cache_key = self._cache_key(text)
            cached_vector = self._cache.get(cache_key)
            if cached_vector is not None:
                vectors[index] = cached_vector
            else:
                uncached_pairs.append((index, text))

        if uncached_pairs:
            for start in range(0, len(uncached_pairs), self.batch_size):
                chunk = uncached_pairs[start : start + self.batch_size]
                chunk_indices = [item[0] for item in chunk]
                chunk_texts = [item[1] for item in chunk]
                chunk_vectors = self._embed_batch_with_fallback(chunk_texts)
                self._store_cached_vectors(chunk_texts, chunk_vectors)
                for chunk_index, vector in zip(chunk_indices, chunk_vectors):
                    vectors[chunk_index] = vector

        return [vector if vector is not None else [] for vector in vectors]

    def _embed_batch_with_fallback(self, texts: list[str]) -> list[list[float]]:
        try:
            return self._embed_batch(texts)
        except RuntimeError as exc:
            if len(texts) <= 1:
                raise
            midpoint = max(1, len(texts) // 2)
            log(
                "embedder",
                f"Embedding batch of size {len(texts)} failed repeatedly; splitting into {midpoint} + {len(texts) - midpoint}",
            )
            left_vectors = self._embed_batch_with_fallback(texts[:midpoint])
            right_vectors = self._embed_batch_with_fallback(texts[midpoint:])
            return left_vectors + right_vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        url = f"{self.base_url}/embeddings"
        payload: dict[str, Any] = {
            "model": self.model,
            "input": texts,
            "encoding_format": self.encoding_format,
        }
        if self.dimensions:
            payload["dimensions"] = self.dimensions

        request = urllib.request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Connection": "close",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    response_payload = json.loads(response.read().decode("utf-8"))
                    data = response_payload.get("data")
                    if not isinstance(data, list):
                        raise RuntimeError("Embedding response is missing `data` list.")
                    vectors = [
                        self._decode_embedding(item["embedding"])
                        for item in sorted(data, key=lambda item: item.get("index", 0))
                    ]
                    if len(vectors) != len(texts):
                        raise RuntimeError(
                            f"Embedding response length mismatch: expected {len(texts)}, got {len(vectors)}."
                        )
                    return vectors
            except urllib.error.HTTPError as exc:
                last_error = self._format_http_error(exc)
            except (
                urllib.error.URLError,
                http.client.IncompleteRead,
                ssl.SSLError,
                ConnectionError,
                TimeoutError,
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                last_error = exc

            if attempt >= self.retries:
                break
            sleep_seconds = 1.5 * (attempt + 1)
            log("embedder", f"Retrying embedding batch after API error: {last_error}")
            time.sleep(sleep_seconds)

        raise RuntimeError(f"Embedding request failed: {last_error}")

    def _build_cache_path(self, cache_dir: Path | None) -> Path | None:
        if cache_dir is None:
            return None
        safe_model = self.model.replace("/", "__").replace("\\", "__").replace(":", "_")
        suffix = f"__dim{self.dimensions}" if self.dimensions else ""
        return ensure_dir(cache_dir) / f"{safe_model}{suffix}.jsonl"

    def _cache_key(self, text: str) -> str:
        basis = f"{self.model}|{self.dimensions or 'default'}|{text}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()

    def _load_cache(self) -> None:
        if self.cache_path is None or not self.cache_path.exists():
            return
        loaded = 0
        with self.cache_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = payload.get("key")
                vector = payload.get("vector")
                if not isinstance(key, str) or not isinstance(vector, list):
                    continue
                self._cache[key] = vector
                loaded += 1
        if loaded:
            log("embedder", f"Loaded {loaded} cached embeddings from {self.cache_path}")

    def _store_cached_vectors(self, texts: list[str], vectors: list[list[float]]) -> None:
        if self.cache_path is None:
            for text, vector in zip(texts, vectors):
                self._cache[self._cache_key(text)] = vector
            return

        rows: list[str] = []
        for text, vector in zip(texts, vectors):
            key = self._cache_key(text)
            if key in self._cache:
                continue
            self._cache[key] = vector
            rows.append(json.dumps({"key": key, "vector": vector}, ensure_ascii=False))
        if not rows:
            return
        with self.cache_path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(row)
                handle.write("\n")

    def _decode_embedding(self, payload: Any) -> list[float]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, str):
            raw = base64.b64decode(payload)
            if len(raw) % 4 != 0:
                raise RuntimeError(f"Base64 embedding payload has invalid byte length: {len(raw)}")
            count = len(raw) // 4
            return list(struct.unpack(f"<{count}f", raw))
        raise RuntimeError(f"Unsupported embedding payload type: {type(payload).__name__}")

    def _format_http_error(self, exc: urllib.error.HTTPError) -> RuntimeError:
        status_code = getattr(exc, "code", None)
        response_body = ""
        try:
            if exc.fp is not None:
                response_body = exc.read().decode("utf-8", errors="replace").strip()
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

        if response_body:
            return RuntimeError(f"HTTP {status_code} from {self.base_url}: {response_body}")
        return RuntimeError(f"HTTP {status_code} from {self.base_url}: {exc.reason}")


def build_embedder(settings: dict[str, Any]) -> EmbeddingBackend:
    merging = settings.get("merging", {})
    backend_name = merging.get("embedding_backend", "hashing")
    if backend_name == "hashing":
        dim = int(merging.get("embedding_dim", 256))
        return HashingEmbeddingBackend(dim=dim)
    if backend_name == "openai_compatible":
        embedding_settings = settings.get("embedding", {})
        llm_settings = settings.get("llm", {})
        dimensions = embedding_settings.get("dimensions")
        cache_dir_value = embedding_settings.get("cache_dir", ".runtime_cache/embedding_vectors")
        return OpenAICompatibleEmbeddingBackend(
            base_url=str(embedding_settings.get("base_url") or llm_settings.get("base_url", "https://api.openai.com/v1")),
            api_key_env=str(embedding_settings.get("api_key_env") or llm_settings.get("api_key_env", "OPENAI_API_KEY")),
            model=str(embedding_settings.get("model") or os.environ.get("TEXTBOOK_KG_EMBEDDING_MODEL", "")),
            dimensions=int(dimensions) if dimensions is not None else None,
            encoding_format=str(embedding_settings.get("encoding_format", "float")),
            timeout_seconds=int(embedding_settings.get("timeout_seconds", 90)),
            retries=int(embedding_settings.get("retries", 2)),
            batch_size=int(embedding_settings.get("batch_size", 32)),
            cache_dir=Path(cache_dir_value) if cache_dir_value else None,
        )
    raise ValueError(f"Unsupported embedding backend: {backend_name}")


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Embedding vectors must have the same dimension.")
    return sum(a * b for a, b in zip(left, right))
