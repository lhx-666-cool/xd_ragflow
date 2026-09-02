from __future__ import annotations

import json

from src.embedder import HashingEmbeddingBackend, OpenAICompatibleEmbeddingBackend, build_embedder


class DummyResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "DummyResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_build_embedder_uses_openai_compatible(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = {
        "llm": {
            "base_url": "https://api.example.com/v1",
            "api_key_env": "OPENAI_API_KEY",
        },
        "merging": {
            "embedding_backend": "openai_compatible",
        },
        "embedding": {
            "model": "text-embedding-test",
            "batch_size": 8,
        },
    }

    embedder = build_embedder(settings)

    assert isinstance(embedder, OpenAICompatibleEmbeddingBackend)
    assert embedder.model == "text-embedding-test"
    assert embedder.batch_size == 8


def test_openai_compatible_embedder_batches_and_orders(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    requests: list[dict] = []

    def fake_urlopen(request, timeout=0):
        requests.append(json.loads(request.data.decode("utf-8")))
        return DummyResponse(
            {
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    embedder = OpenAICompatibleEmbeddingBackend(
        base_url="https://api.example.com/v1",
        api_key_env="OPENAI_API_KEY",
        model="text-embedding-test",
        batch_size=2,
    )

    vectors = embedder.embed_texts(["alpha", "beta"])

    assert requests[0]["model"] == "text-embedding-test"
    assert requests[0]["input"] == ["alpha", "beta"]
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]


def test_hashing_embedder_still_available() -> None:
    embedder = HashingEmbeddingBackend(dim=16)
    vectors = embedder.embed_texts(["alpha beta"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 16
