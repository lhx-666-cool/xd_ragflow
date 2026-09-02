from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from api.services.textbook_kg_service import (
    TextbookKgError,
    TextbookKgService,
    sign_model_gateway_token,
    verify_model_gateway_token,
)


class TextbookKgServiceTest(unittest.TestCase):
    def test_gateway_token_round_trip_tampering_and_expiration(self):
        claims = {
            "tenant_id": "tenant-1",
            "kb_id": "kb-1",
            "doc_id": "doc-1",
            "llm_id": "chat@provider",
            "embd_id": "embedding@provider",
        }
        token = sign_model_gateway_token("secret", claims, ttl_seconds=120, now=100)
        self.assertEqual("doc-1", verify_model_gateway_token("secret", token, now=150)["doc_id"])
        with self.assertRaisesRegex(TextbookKgError, "Invalid"):
            verify_model_gateway_token("secret", f"{token[:-1]}x", now=150)
        with self.assertRaisesRegex(TextbookKgError, "expired"):
            verify_model_gateway_token("secret", token, now=221)
        with self.assertRaisesRegex(TextbookKgError, "not active"):
            verify_model_gateway_token("secret", token, now=1)

    def test_gateway_token_requires_all_scoped_claims(self):
        with self.assertRaisesRegex(TextbookKgError, "embd_id"):
            sign_model_gateway_token(
                "secret",
                {"tenant_id": "t", "kb_id": "k", "doc_id": "d", "llm_id": "l"},
            )

    def test_reads_token_from_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env.sidecar"
            env_file.write_text("TEXTBOOK_KG_API_TOKEN=test-token\n", encoding="utf-8")
            with patch.dict(os.environ, {"TEXTBOOK_KG_API_ENV_FILE": str(env_file)}, clear=True):
                service = TextbookKgService(session=Mock())
        self.assertEqual(service.token, "test-token")

    def test_submit_pdf_uses_idempotency_and_options(self):
        response = Mock(ok=True, status_code=202)
        response.json.return_value = {"job_id": "job-1", "status": "queued"}
        session = Mock()
        session.request.return_value = response
        service = TextbookKgService("http://sidecar", "token", session)

        result = service.submit_pdf(
            doc_id="doc-1",
            file_name="book.pdf",
            content=b"%PDF",
            options={"toc_pages": "1-3", "chunk_size": 20},
            model_runtime={
                "model_gateway_url": "http://127.0.0.1:9443/v1/textbook_kg/model-gateway",
                "model_gateway_token": "scoped-token",
                "llm_model": "chat@provider",
                "embedding_model": "embedding@provider",
            },
        )

        self.assertEqual(result["job_id"], "job-1")
        kwargs = session.request.call_args.kwargs
        self.assertEqual(kwargs["data"]["idempotency_key"], "ragflow-document-doc-1")
        self.assertEqual(kwargs["data"]["toc_pages"], "1-3")
        self.assertEqual(kwargs["files"]["pdf"][0], "book.pdf")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer token")
        self.assertEqual(kwargs["data"]["model_gateway_token"], "scoped-token")
        self.assertEqual(kwargs["data"]["embedding_model"], "embedding@provider")

    def test_invalid_json_is_rejected(self):
        response = Mock(ok=True, status_code=200)
        response.json.side_effect = ValueError("bad json")
        session = Mock()
        session.request.return_value = response
        service = TextbookKgService("http://sidecar", "token", session)
        with self.assertRaisesRegex(TextbookKgError, "invalid JSON"):
            service.get_job("job-1")

    def test_retry_refreshes_model_runtime(self):
        response = Mock(ok=True, status_code=202)
        response.json.return_value = {"job_id": "job-1", "status": "queued"}
        session = Mock()
        session.request.return_value = response
        service = TextbookKgService("http://sidecar", "token", session)

        service.retry(
            "job-1",
            model_runtime={
                "model_gateway_url": "http://127.0.0.1:9443/gateway",
                "model_gateway_token": "fresh-token",
                "llm_model": "chat@provider",
                "embedding_model": "embedding@provider",
            },
        )

        self.assertEqual("fresh-token", session.request.call_args.kwargs["data"]["model_gateway_token"])

    def test_request_errors_do_not_leak_url_or_token(self):
        session = Mock()
        session.request.side_effect = requests.Timeout("secret-token at http://sidecar")
        service = TextbookKgService("http://sidecar", "secret-token", session)
        with self.assertRaises(TextbookKgError) as raised:
            service.get_job("job-1")
        self.assertNotIn("secret-token", str(raised.exception))
        self.assertNotIn("http://sidecar", str(raised.exception))

    def test_bundle_returns_bytes_and_type(self):
        response = Mock(ok=True, content=b"zip", headers={"Content-Type": "application/zip"})
        session = Mock()
        session.get.return_value = response
        service = TextbookKgService("http://sidecar", "token", session)
        self.assertEqual(service.get_bundle("job-1"), (b"zip", "application/zip"))

    def test_artifact_download_is_allowlisted_and_size_limited(self):
        response = Mock(ok=True, content=b'{"schema_version":"ragflow-textbook-kg/v1"}')
        session = Mock()
        session.get.return_value = response
        service = TextbookKgService("http://sidecar", "token", session)

        self.assertEqual(
            response.content,
            service.get_artifact("job-1", "ragflow_adapter.json", max_bytes=1024),
        )
        self.assertEqual(
            response.content,
            service.get_artifact("job-1", "tree/content_tree.json", max_bytes=1024),
        )
        with self.assertRaisesRegex(TextbookKgError, "not allowed"):
            service.get_artifact("job-1", "../secret")
        with self.assertRaisesRegex(TextbookKgError, "too large"):
            service.get_artifact("job-1", "ragflow_adapter.json", max_bytes=4)


if __name__ == "__main__":
    unittest.main()
