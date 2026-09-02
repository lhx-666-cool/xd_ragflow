from __future__ import annotations

import builtins
import hashlib
import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from flask import Flask

from api.db import LLMType
from api.services.textbook_kg_service import TextbookKgService, verify_model_gateway_token


class _Manager:
    @staticmethod
    def route(*_args, **_kwargs):
        return lambda function: function


def _load_module():
    builtins.manager = _Manager()
    try:
        return importlib.import_module("api.apps.textbook_kg_app")
    finally:
        del builtins.manager


textbook_app = _load_module()


class TextbookKgAppTest(unittest.TestCase):
    def test_gateway_rejects_forwarded_browser_requests(self):
        flask_app = Flask(__name__)
        with flask_app.test_request_context(
            "/",
            headers={"Authorization": "Bearer anything", "Origin": "https://example.test"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        ):
            with self.assertRaisesRegex(textbook_app.TextbookKgError, "local Sidecar"):
                textbook_app._gateway_claims()

    def test_model_runtime_uses_kb_embedding_and_owner_tenant_chat(self):
        doc = SimpleNamespace(id="doc-1", kb_id="kb-1")
        kb = SimpleNamespace(id="kb-1", tenant_id="tenant-1", embd_id="embed@provider", language="Chinese")
        tenant = SimpleNamespace(id="tenant-1", llm_id="chat@provider")
        service = TextbookKgService("http://sidecar", "sidecar-secret", Mock())
        model_config = {
            "llm_factory": "provider",
            "api_key": "provider-secret-must-not-leak",
            "api_base": "https://provider.invalid/v1",
        }
        with (
            patch.object(textbook_app.KnowledgebaseService, "get_by_id", return_value=(True, kb)),
            patch.object(textbook_app.TenantService, "get_by_id", return_value=(True, tenant)),
            patch.object(textbook_app.TenantLLMService, "get_model_config", return_value=model_config) as get_config,
            patch.object(textbook_app.settings, "HOST_PORT", 9443),
            patch.dict(textbook_app.os.environ, {}, clear=True),
        ):
            runtime = textbook_app._model_runtime(doc, service)

        self.assertEqual("chat@provider", runtime["llm_model"])
        self.assertEqual("embed@provider", runtime["embedding_model"])
        self.assertNotIn("provider-secret-must-not-leak", json.dumps(runtime))
        claims = verify_model_gateway_token("sidecar-secret", runtime["model_gateway_token"])
        self.assertEqual("doc-1", claims["doc_id"])
        self.assertEqual(
            [
                call("tenant-1", LLMType.CHAT.value, "chat@provider"),
                call("tenant-1", LLMType.EMBEDDING.value, "embed@provider"),
            ],
            get_config.call_args_list,
        )

    def test_chat_gateway_uses_scoped_llm_bundle(self):
        flask_app = Flask(__name__)
        claims = {"doc_id": "doc-1"}
        kb = SimpleNamespace(language="Chinese")
        tenant = SimpleNamespace(id="tenant-1")
        context = (kb, tenant, "chat@provider", "embed@provider")
        model = Mock()
        model.chat.return_value = '{"entities": []}'
        with (
            flask_app.test_request_context(
                "/",
                method="POST",
                json={
                    "model": "chat@provider",
                    "messages": [
                        {"role": "system", "content": "JSON only"},
                        {"role": "user", "content": "extract"},
                    ],
                    "temperature": 0,
                },
            ),
            patch.object(textbook_app, "_gateway_request_context", return_value=(claims, context, None)),
            patch.object(textbook_app, "LLMBundle", return_value=model) as bundle,
        ):
            response = textbook_app.model_gateway_chat()

        self.assertEqual('{"entities": []}', response.get_json()["choices"][0]["message"]["content"])
        bundle.assert_called_once_with("tenant-1", LLMType.CHAT, llm_name="chat@provider", lang="Chinese")
        model.chat.assert_called_once()

    def test_embedding_gateway_rejects_model_outside_scope(self):
        flask_app = Flask(__name__)
        context = (
            SimpleNamespace(language="Chinese"),
            SimpleNamespace(id="tenant-1"),
            "chat@provider",
            "embed@provider",
        )
        with (
            flask_app.test_request_context(
                "/",
                method="POST",
                json={"model": "other@provider", "input": ["text"]},
            ),
            patch.object(textbook_app, "_gateway_request_context", return_value=({}, context, None)),
        ):
            response, status = textbook_app.model_gateway_embeddings()

        self.assertEqual(403, status)
        self.assertIn("outside", response.get_json()["error"]["message"])

    def test_successful_sidecar_result_imports_with_kb_embedding_only(self):
        content = b'{"schema_version":"ragflow-textbook-kg/v1"}'
        checksum = hashlib.sha256(content).hexdigest()
        doc = SimpleNamespace(
            id="doc-1",
            kb_id="kb-1",
            meta_fields={"textbook_kg": {"job_id": "job-1", "status": "succeeded"}},
        )
        kb = SimpleNamespace(id="kb-1", language="Chinese")
        tenant = SimpleNamespace(id="tenant-1")
        service = TextbookKgService("http://sidecar", "test-token", Mock())
        imported = {
            "status": "imported",
            "artifact_sha256": checksum,
            "entity_count": 2,
            "relation_count": 1,
        }
        with (
            patch.object(textbook_app.DocumentService, "update_by_id", return_value=True),
            patch.object(
                textbook_app,
                "_knowledgebase_and_tenant",
                return_value=(kb, tenant, "chat@provider", "embed@provider"),
            ),
            patch.object(textbook_app, "LLMBundle", return_value=Mock()) as bundle,
            patch.object(textbook_app, "import_textbook_graph", return_value=imported) as importer,
            patch.object(service, "get_artifact", return_value=content),
        ):
            metadata = textbook_app._import_graph_if_needed(
                doc,
                service,
                {"artifacts": [{"name": "ragflow_adapter.json", "sha256": checksum}]},
            )

        self.assertEqual("imported", metadata["graphrag"]["status"])
        bundle.assert_called_once_with(
            "tenant-1",
            LLMType.EMBEDDING,
            llm_name="embed@provider",
            lang="Chinese",
        )
        importer.assert_called_once()
        self.assertNotIn("chat@provider", repr(importer.call_args))

    def test_unexpected_import_error_is_sanitized(self):
        content = b'{"schema_version":"ragflow-textbook-kg/v1"}'
        checksum = hashlib.sha256(content).hexdigest()
        doc = SimpleNamespace(
            id="doc-1",
            kb_id="kb-1",
            meta_fields={"textbook_kg": {"job_id": "job-1", "status": "succeeded"}},
        )
        service = TextbookKgService("http://sidecar", "test-token", Mock())
        with (
            patch.object(textbook_app.DocumentService, "update_by_id", return_value=True),
            patch.object(textbook_app, "_knowledgebase_and_tenant", side_effect=RuntimeError("provider-secret")),
            patch.object(service, "get_artifact", return_value=content),
        ):
            metadata = textbook_app._import_graph_if_needed(
                doc,
                service,
                {"artifacts": [{"name": "ragflow_adapter.json", "sha256": checksum}]},
            )

        self.assertEqual("failed", metadata["graphrag"]["status"])
        self.assertNotIn("provider-secret", metadata["graphrag"]["error"])


if __name__ == "__main__":
    unittest.main()
