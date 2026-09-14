from __future__ import annotations

import base64
import ipaddress
import os
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import flask
from flask import request
from flask_login import current_user, login_required

from api import settings
from api.db import LLMType
from api.db.services.document_service import DocumentService
from api.db.services.file2document_service import File2DocumentService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.llm_service import LLMBundle
from api.db.services.tenant_llm_service import TenantLLMService
from api.db.services.user_service import TenantService
from api.services.textbook_kg_service import (
    TextbookKgError,
    TextbookKgService,
    sign_model_gateway_token,
    verify_model_gateway_token,
)
from api.services.textbook_kg_graphrag import (
    TextbookKgGraphRagError,
    import_textbook_graph,
)
from api.services.textbook_kg_tree import TextbookKgTreeError, prepare_textbook_tree
from api.utils.api_utils import get_json_result, server_error_response
from rag.utils.storage_factory import STORAGE_IMPL


TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(message: str, code: Any = None):
    return get_json_result(
        data=False,
        message=message,
        code=code or settings.RetCode.SERVER_ERROR,
    )


def _document_or_response(doc_id: str):
    if not DocumentService.accessible(doc_id, current_user.id):
        return None, _error("No authorization.", settings.RetCode.AUTHENTICATION_ERROR)
    exists, doc = DocumentService.get_by_id(doc_id)
    if not exists:
        return None, _error("Document not found.", settings.RetCode.DATA_ERROR)
    return doc, None


def _metadata(doc) -> dict[str, Any]:
    value = doc.meta_fields or {}
    return dict(value) if isinstance(value, dict) else {}


def _save_job(doc, payload: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = _metadata(doc)
    previous = meta.get("textbook_kg")
    textbook = dict(previous) if isinstance(previous, dict) else {}
    for key in ("job_id", "status", "stage", "progress", "error", "created_at", "updated_at"):
        if key in payload:
            textbook[key] = payload[key]
    textbook["synced_at"] = _now()
    if result:
        textbook["result"] = {
            key: result.get(key)
            for key in ("entity_count", "relation_count", "chunk_count", "book_title")
            if key in result
        }
    meta["textbook_kg"] = textbook
    DocumentService.update_by_id(doc.id, {"meta_fields": meta})
    doc.meta_fields = meta
    return textbook


def _save_graphrag(doc, **values: Any) -> dict[str, Any]:
    meta = _metadata(doc)
    previous = meta.get("textbook_kg")
    textbook = dict(previous) if isinstance(previous, dict) else {}
    previous_graph = textbook.get("graphrag")
    graph_state = dict(previous_graph) if isinstance(previous_graph, dict) else {}
    graph_state.update(values)
    graph_state["updated_at"] = _now()
    textbook["graphrag"] = graph_state
    meta["textbook_kg"] = textbook
    DocumentService.update_by_id(doc.id, {"meta_fields": meta})
    doc.meta_fields = meta
    return textbook


def _adapter_sha256(result: dict[str, Any]) -> str:
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list):
        raise TextbookKgError("Textbook KG result does not contain an artifact manifest")
    for artifact in artifacts:
        if isinstance(artifact, dict) and artifact.get("name") == "ragflow_adapter.json":
            value = str(artifact.get("sha256") or "").lower()
            if len(value) == 64 and all(character in "0123456789abcdef" for character in value):
                return value
    raise TextbookKgError("Textbook KG result does not contain a valid adapter checksum")


def _import_graph_if_needed(
    doc,
    service: TextbookKgService,
    result: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    try:
        expected_sha256 = _adapter_sha256(result)
        current = _metadata(doc).get("textbook_kg", {}).get("graphrag", {})
        if (
            isinstance(current, dict)
            and current.get("status") == "imported"
            and current.get("artifact_sha256") == expected_sha256
        ):
            return _metadata(doc)["textbook_kg"]
        if isinstance(current, dict) and current.get("status") == "imported" and not force:
            return _save_graphrag(
                doc,
                status="failed",
                error="The adapter changed after import; explicit graph replacement is required.",
            )

        _save_graphrag(doc, status="importing", error=None, artifact_sha256=expected_sha256)
        job_id = _job_id(doc)
        if not job_id:
            raise TextbookKgError("This document has no textbook KG job")
        content = service.get_artifact(job_id, "ragflow_adapter.json")
        kb, tenant, _, embd_id = _knowledgebase_and_tenant(doc)
        embedding_model = LLMBundle(
            tenant.id,
            LLMType.EMBEDDING,
            llm_name=embd_id,
            lang=kb.language or "Chinese",
        )
        imported = import_textbook_graph(
            content,
            tenant_id=str(tenant.id),
            kb_id=str(kb.id),
            doc_id=str(doc.id),
            embedding_model=embedding_model,
            expected_sha256=expected_sha256,
        )
        return _save_graphrag(
            doc,
            **imported,
            error=None,
            imported_at=_now(),
        )
    except (TextbookKgError, TextbookKgGraphRagError) as exc:
        return _save_graphrag(doc, status="failed", error=str(exc)[:500])
    except Exception as exc:  # noqa: BLE001
        return _save_graphrag(
            doc,
            status="failed",
            error=f"Native GraphRAG import failed ({exc.__class__.__name__}).",
        )


def _job_id(doc) -> str | None:
    value = _metadata(doc).get("textbook_kg")
    return value.get("job_id") if isinstance(value, dict) else None


def _service_or_response():
    try:
        return TextbookKgService(), None
    except TextbookKgError as exc:
        return None, _error(str(exc))


def _knowledgebase_and_tenant(doc):
    exists, kb = KnowledgebaseService.get_by_id(doc.kb_id)
    if not exists:
        raise TextbookKgError("Knowledge base not found.")
    exists, tenant = TenantService.get_by_id(kb.tenant_id)
    if not exists:
        raise TextbookKgError("Knowledge base tenant not found.")
    llm_id = str(tenant.llm_id or "").strip()
    embd_id = str(kb.embd_id or "").strip()
    if not llm_id or not embd_id:
        raise TextbookKgError("Configure both the chat and embedding models in RAGFlow before building a textbook KG.")
    return kb, tenant, llm_id, embd_id


def _model_runtime(doc, service: TextbookKgService) -> dict[str, str]:
    kb, tenant, llm_id, embd_id = _knowledgebase_and_tenant(doc)
    try:
        TenantLLMService.get_model_config(tenant.id, LLMType.CHAT.value, llm_id)
        TenantLLMService.get_model_config(tenant.id, LLMType.EMBEDDING.value, embd_id)
    except (AssertionError, LookupError) as exc:
        raise TextbookKgError("The knowledge base models are not configured for its owner.") from exc
    token = sign_model_gateway_token(
        service.token,
        {
            "tenant_id": tenant.id,
            "kb_id": kb.id,
            "doc_id": doc.id,
            "llm_id": llm_id,
            "embd_id": embd_id,
        },
        ttl_seconds=int(os.getenv("TEXTBOOK_KG_GATEWAY_TOKEN_TTL_SECONDS", str(7 * 24 * 60 * 60))),
    )
    internal_url = os.getenv("TEXTBOOK_KG_RAGFLOW_INTERNAL_URL") or f"http://127.0.0.1:{settings.HOST_PORT}"
    return {
        "model_gateway_url": f"{internal_url.rstrip('/')}/v1/textbook_kg/model-gateway",
        "model_gateway_token": token,
        "llm_model": llm_id,
        "embedding_model": embd_id,
    }


@manager.route("/submit", methods=["POST"])  # noqa: F821
@login_required
def submit():
    payload = request.get_json(silent=True) or {}
    doc_ids = payload.get("doc_ids")
    if not isinstance(doc_ids, list) or not doc_ids:
        return _error('"doc_ids" must be a non-empty list.', settings.RetCode.ARGUMENT_ERROR)
    service, error_response = _service_or_response()
    if error_response:
        return error_response

    submitted: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for doc_id in dict.fromkeys(str(item) for item in doc_ids):
        doc, response = _document_or_response(doc_id)
        if response:
            failed.append({"doc_id": doc_id, "error": "Document is inaccessible or missing."})
            continue
        if (doc.suffix or Path(doc.name or "").suffix).lower().lstrip(".") != "pdf":
            failed.append({"doc_id": doc_id, "error": "Only PDF documents can build a textbook KG."})
            continue
        existing_job_id = _job_id(doc)
        if existing_job_id:
            submitted.append({"doc_id": doc.id, "job_id": existing_job_id, "idempotent": True})
            continue
        try:
            bucket, name = File2DocumentService.get_storage_address(doc_id=doc.id)
            content = STORAGE_IMPL.get(bucket, name)
            if hasattr(content, "read"):
                content = content.read()
            if not isinstance(content, bytes):
                content = bytes(content)
            job = service.submit_pdf(
                doc_id=doc.id,
                file_name=doc.name,
                content=content,
                options=payload.get("options") if isinstance(payload.get("options"), dict) else None,
                model_runtime=_model_runtime(doc, service),
            )
            textbook = _save_job(doc, job)
            textbook = _save_graphrag(doc, status="pending", error=None)
            submitted.append({"doc_id": doc.id, **textbook})
        except (TextbookKgError, OSError, TypeError, ValueError) as exc:
            failed.append({"doc_id": doc.id, "error": str(exc)})

    if not submitted:
        return _error(failed[0]["error"] if failed else "No documents were submitted.")
    return get_json_result(data={"submitted": submitted, "failed": failed})


def _sync_job(doc, service: TextbookKgService) -> dict[str, Any]:
    job_id = _job_id(doc)
    if not job_id:
        raise TextbookKgError("This document has no textbook KG job.")
    job = service.get_job(job_id)
    result = None
    if job.get("status") == "succeeded":
        result = job.get("result") if isinstance(job.get("result"), dict) else service.get_result(job_id)
    textbook = _save_job(doc, job, result)
    if result:
        textbook = _import_graph_if_needed(doc, service, result)
    return textbook


@manager.route("/job/<doc_id>", methods=["GET"])  # noqa: F821
@login_required
def job_status(doc_id: str):
    doc, response = _document_or_response(doc_id)
    if response:
        return response
    service, response = _service_or_response()
    if response:
        return response
    try:
        return get_json_result(data=_sync_job(doc, service))
    except TextbookKgError as exc:
        return _error(str(exc))


@manager.route("/job/<doc_id>/result", methods=["GET"])  # noqa: F821
@login_required
def job_result(doc_id: str):
    doc, response = _document_or_response(doc_id)
    if response:
        return response
    service, response = _service_or_response()
    if response:
        return response
    try:
        job_id = _job_id(doc)
        if not job_id:
            raise TextbookKgError("This document has no textbook KG job.")
        result = service.get_result(job_id)
        _save_job(doc, {"job_id": job_id, "status": "succeeded", "stage": "completed", "progress": 1.0}, result)
        textbook = _import_graph_if_needed(doc, service, result)
        return get_json_result(data={**result, "graphrag": textbook.get("graphrag")})
    except TextbookKgError as exc:
        return _error(str(exc))


def _job_action(doc_id: str, action: str):
    doc, response = _document_or_response(doc_id)
    if response:
        return response
    service, response = _service_or_response()
    if response:
        return response
    try:
        job_id = _job_id(doc)
        if not job_id:
            raise TextbookKgError("This document has no textbook KG job.")
        if action == "retry":
            job = service.retry(job_id, model_runtime=_model_runtime(doc, service))
        else:
            job = getattr(service, action)(job_id)
        return get_json_result(data=_save_job(doc, job))
    except TextbookKgError as exc:
        return _error(str(exc))


@manager.route("/job/<doc_id>/retry", methods=["POST"])  # noqa: F821
@login_required
def retry(doc_id: str):
    return _job_action(doc_id, "retry")


@manager.route("/job/<doc_id>/cancel", methods=["POST"])  # noqa: F821
@login_required
def cancel(doc_id: str):
    return _job_action(doc_id, "cancel")


@manager.route("/job/<doc_id>/import", methods=["POST"])  # noqa: F821
@login_required
def import_graph(doc_id: str):
    doc, response = _document_or_response(doc_id)
    if response:
        return response
    service, response = _service_or_response()
    if response:
        return response
    try:
        job_id = _job_id(doc)
        if not job_id:
            raise TextbookKgError("This document has no textbook KG job")
        job = service.get_job(job_id)
        if job.get("status") != "succeeded":
            raise TextbookKgError("The Textbook KG job has not succeeded")
        result = job.get("result") if isinstance(job.get("result"), dict) else service.get_result(job_id)
        return get_json_result(data=_import_graph_if_needed(doc, service, result, force=True))
    except TextbookKgError as exc:
        return _error(str(exc))


@manager.route("/job/<doc_id>/bundle", methods=["GET"])  # noqa: F821
@login_required
def bundle(doc_id: str):
    doc, response = _document_or_response(doc_id)
    if response:
        return response
    service, response = _service_or_response()
    if response:
        return response
    try:
        job_id = _job_id(doc)
        if not job_id:
            raise TextbookKgError("This document has no textbook KG job.")
        content, content_type = service.get_bundle(job_id)
        response = flask.make_response(content)
        response.headers.set("Content-Type", content_type)
        response.headers.set("Content-Disposition", "attachment", filename=f"{doc.id}-textbook-kg.zip")
        return response
    except TextbookKgError as exc:
        return server_error_response(exc)


@manager.route("/job/<doc_id>/tree", methods=["GET"])  # noqa: F821
@login_required
def chapter_tree(doc_id: str):
    doc, response = _document_or_response(doc_id)
    if response:
        return response
    service, response = _service_or_response()
    if response:
        return response
    try:
        job_id = _job_id(doc)
        if not job_id:
            raise TextbookKgError("This document has no textbook KG job.")
        job = service.get_job(job_id)
        if job.get("status") != "succeeded":
            raise TextbookKgError("The Textbook KG job has not succeeded.")
        content = service.get_artifact(job_id, "tree/content_tree.json")
        return get_json_result(data=prepare_textbook_tree(content))
    except (TextbookKgError, TextbookKgTreeError) as exc:
        return _error(str(exc))


def _gateway_error(message: str, status_code: int = 400):
    return flask.jsonify({"error": {"message": message, "type": "textbook_kg_gateway_error"}}), status_code


def _gateway_claims() -> dict[str, Any]:
    try:
        remote_address = ipaddress.ip_address(request.remote_addr or "")
    except ValueError as exc:
        raise TextbookKgError("The model gateway is only available locally", 403) from exc
    if (
        not remote_address.is_loopback
        or request.headers.get("Origin")
        or request.headers.get("X-Forwarded-For")
        or request.headers.get("Forwarded")
    ):
        raise TextbookKgError("The model gateway is only available to local Sidecar requests", 403)
    scheme, _, token = (request.headers.get("Authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise TextbookKgError("Missing model gateway token", 401)
    service = TextbookKgService()
    return verify_model_gateway_token(service.token, token)


def _gateway_context(claims: dict[str, Any]):
    exists, doc = DocumentService.get_by_id(str(claims["doc_id"]))
    if not exists or str(doc.kb_id) != str(claims["kb_id"]):
        raise TextbookKgError("The model gateway document scope is invalid", 403)
    kb, tenant, llm_id, embd_id = _knowledgebase_and_tenant(doc)
    if str(kb.id) != str(claims["kb_id"]) or str(tenant.id) != str(claims["tenant_id"]):
        raise TextbookKgError("The model gateway tenant scope is invalid", 403)
    if llm_id != str(claims["llm_id"]) or embd_id != str(claims["embd_id"]):
        raise TextbookKgError("The configured knowledge base models changed; retry the job", 409)
    return kb, tenant, llm_id, embd_id


def _gateway_request_context():
    try:
        claims = _gateway_claims()
        return claims, _gateway_context(claims), None
    except TextbookKgError as exc:
        return None, None, _gateway_error(str(exc), exc.status_code or 401)


@manager.route("/model-gateway/chat/completions", methods=["POST"])  # noqa: F821
def model_gateway_chat():
    claims, context, error_response = _gateway_request_context()
    if error_response:
        return error_response
    assert claims is not None and context is not None
    kb, tenant, llm_id, _ = context
    payload = request.get_json(silent=True) or {}
    if str(payload.get("model") or "") != llm_id:
        return _gateway_error("The requested chat model is outside the job scope", 403)
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return _gateway_error("messages must be a non-empty list", 422)
    if len(messages) > 32:
        return _gateway_error("messages exceeds the gateway limit", 413)
    system_parts: list[str] = []
    history: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            return _gateway_error("Each message must contain string content", 422)
        role = str(message.get("role") or "user")
        if role == "system":
            system_parts.append(message["content"])
        else:
            if role not in {"user", "assistant"}:
                return _gateway_error("Only system, user, and assistant message roles are supported", 422)
            history.append({"role": role, "content": message["content"]})
    if sum(len(message["content"]) for message in messages) > 200_000:
        return _gateway_error("messages content exceeds the gateway limit", 413)
    if "max_tokens" in payload:
        try:
            max_tokens = int(payload["max_tokens"])
        except (TypeError, ValueError):
            return _gateway_error("max_tokens must be an integer", 422)
        if max_tokens < 1 or max_tokens > 32_768:
            return _gateway_error("max_tokens is outside the gateway limit", 422)
    generation_config = {
        key: payload[key]
        for key in ("temperature", "top_p", "max_tokens", "frequency_penalty", "presence_penalty")
        if key in payload
    }
    try:
        model = LLMBundle(tenant.id, LLMType.CHAT, llm_name=llm_id, lang=kb.language or "Chinese")
        answer = model.chat("\n".join(system_parts), history, generation_config)
    except Exception as exc:  # noqa: BLE001
        return _gateway_error(f"The configured chat model request failed: {exc.__class__.__name__}", 502)
    return flask.jsonify(
        {
            "id": f"textbook-kg-{doc_id}" if (doc_id := claims.get("doc_id")) else "textbook-kg",
            "object": "chat.completion",
            "model": llm_id,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    )


@manager.route("/model-gateway/embeddings", methods=["POST"])  # noqa: F821
def model_gateway_embeddings():
    _, context, error_response = _gateway_request_context()
    if error_response:
        return error_response
    assert context is not None
    kb, tenant, _, embd_id = context
    payload = request.get_json(silent=True) or {}
    if str(payload.get("model") or "") != embd_id:
        return _gateway_error("The requested embedding model is outside the job scope", 403)
    inputs = payload.get("input")
    if isinstance(inputs, str):
        inputs = [inputs]
    if not isinstance(inputs, list) or not inputs or not all(isinstance(item, str) for item in inputs):
        return _gateway_error("input must be a string or non-empty string list", 422)
    if len(inputs) > 128 or sum(len(item) for item in inputs) > 500_000:
        return _gateway_error("embedding input exceeds the gateway limit", 413)
    try:
        model = LLMBundle(tenant.id, LLMType.EMBEDDING, llm_name=embd_id, lang=kb.language or "Chinese")
        vectors, used_tokens = model.encode(inputs)
    except Exception as exc:  # noqa: BLE001
        return _gateway_error(f"The configured embedding model request failed: {exc.__class__.__name__}", 502)
    encoding_format = str(payload.get("encoding_format") or "float")
    data = []
    for index, vector in enumerate(vectors):
        values = vector.tolist() if hasattr(vector, "tolist") else list(vector)
        embedding: Any = values
        if encoding_format == "base64":
            embedding = base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode("ascii")
        data.append({"object": "embedding", "index": index, "embedding": embedding})
    return flask.jsonify(
        {
            "object": "list",
            "model": embd_id,
            "data": data,
            "usage": {"prompt_tokens": int(used_tokens or 0), "total_tokens": int(used_tokens or 0)},
        }
    )
