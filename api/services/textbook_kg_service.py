from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

import requests


class TextbookKgError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


GATEWAY_CLAIM_FIELDS = {
    "tenant_id",
    "kb_id",
    "doc_id",
    "llm_id",
    "embd_id",
}
GATEWAY_TOKEN_PURPOSE = "textbook-kg-model-gateway-v1"


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_model_gateway_token(
    secret: str,
    claims: dict[str, Any],
    *,
    ttl_seconds: int = 7 * 24 * 60 * 60,
    now: int | None = None,
) -> str:
    if not secret:
        raise TextbookKgError("Textbook KG API token is not configured")
    missing = sorted(field for field in GATEWAY_CLAIM_FIELDS if not str(claims.get(field) or "").strip())
    if missing:
        raise TextbookKgError(f"Missing model gateway claims: {', '.join(missing)}")
    issued_at = int(time.time() if now is None else now)
    payload = {field: str(claims[field]) for field in sorted(GATEWAY_CLAIM_FIELDS)}
    bounded_ttl = min(30 * 24 * 60 * 60, max(60, int(ttl_seconds)))
    payload.update(
        {
            "purpose": GATEWAY_TOKEN_PURPOSE,
            "iat": issued_at,
            "exp": issued_at + bounded_ttl,
        }
    )
    encoded_payload = _base64url_encode(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded_payload}.{_base64url_encode(signature)}"


def verify_model_gateway_token(
    secret: str,
    token: str,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        expected = hmac.new(secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).digest()
        supplied = _base64url_decode(encoded_signature)
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("signature")
        payload = json.loads(_base64url_decode(encoded_payload).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("payload")
        if any(not str(payload.get(field) or "").strip() for field in GATEWAY_CLAIM_FIELDS):
            raise ValueError("claims")
        if payload.get("purpose") != GATEWAY_TOKEN_PURPOSE:
            raise ValueError("purpose")
        current_time = int(time.time() if now is None else now)
        if int(payload.get("iat", current_time)) > current_time + 60:
            raise TextbookKgError("Textbook KG model gateway token is not active yet")
        if int(payload.get("exp", 0)) < current_time:
            raise TextbookKgError("Textbook KG model gateway token has expired")
    except TextbookKgError:
        raise
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise TextbookKgError("Invalid Textbook KG model gateway token") from exc
    return payload


class TextbookKgService:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        session: Any | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("TEXTBOOK_KG_API_URL") or "http://127.0.0.1:8890").rstrip("/")
        self.token = token or os.getenv("TEXTBOOK_KG_API_TOKEN") or self._read_token_file()
        self.session = session or requests.Session()
        if not self.token:
            raise TextbookKgError("Textbook KG API token is not configured")

    @staticmethod
    def _read_token_file() -> str:
        configured = os.getenv("TEXTBOOK_KG_API_ENV_FILE")
        candidates = [Path(configured)] if configured else []
        candidates.extend(
            [
                Path(__file__).resolve().parents[2] / "extensions" / "textbook_kg" / ".env.sidecar",
                Path("/mnt/ragflow_backup/rF_copy/extensions/textbook_kg/.env.sidecar"),
            ]
        )
        for candidate in candidates:
            try:
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    key, separator, value = line.partition("=")
                    if separator and key.strip() == "TEXTBOOK_KG_API_TOKEN":
                        return value.strip().strip('"').strip("'")
            except (OSError, UnicodeError):
                continue
        return ""

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _json_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=self.headers,
                timeout=kwargs.pop("timeout", (5, 30)),
                **kwargs,
            )
        except requests.RequestException as exc:
            raise TextbookKgError(f"Textbook KG service is unavailable: {exc.__class__.__name__}") from exc
        if not response.ok:
            detail = ""
            try:
                payload = response.json()
                detail = str(payload.get("detail") or payload.get("message") or "")
            except (ValueError, AttributeError):
                detail = response.text[:300]
            raise TextbookKgError(
                f"Textbook KG service returned HTTP {response.status_code}: {detail}".rstrip(": "),
                response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise TextbookKgError("Textbook KG service returned invalid JSON", response.status_code) from exc
        if not isinstance(payload, dict):
            raise TextbookKgError("Textbook KG service returned an invalid response", response.status_code)
        return payload

    def submit_pdf(
        self,
        *,
        doc_id: str,
        file_name: str,
        content: bytes,
        options: dict[str, Any] | None = None,
        model_runtime: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        data: dict[str, str] = {
            "book_title": Path(file_name).stem,
            "idempotency_key": f"ragflow-document-{doc_id}",
        }
        for key in ("toc_pages", "page_offset", "chunk_size", "lang"):
            value = (options or {}).get(key)
            if value is not None and value != "":
                data[key] = str(value)
        for key in ("model_gateway_url", "model_gateway_token", "llm_model", "embedding_model"):
            value = (model_runtime or {}).get(key)
            if value:
                data[key] = str(value)
        return self._json_request(
            "POST",
            "/v1/textbook-kg/jobs",
            data=data,
            files={"pdf": (file_name, content, "application/pdf")},
            timeout=(5, 180),
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._json_request("GET", f"/v1/textbook-kg/jobs/{job_id}")

    def get_result(self, job_id: str) -> dict[str, Any]:
        return self._json_request("GET", f"/v1/textbook-kg/jobs/{job_id}/result")

    def retry(self, job_id: str, *, model_runtime: dict[str, str] | None = None) -> dict[str, Any]:
        data = {
            key: str(value)
            for key in ("model_gateway_url", "model_gateway_token", "llm_model", "embedding_model")
            if (value := (model_runtime or {}).get(key))
        }
        return self._json_request("POST", f"/v1/textbook-kg/jobs/{job_id}/retry", data=data)

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._json_request("POST", f"/v1/textbook-kg/jobs/{job_id}/cancel")

    def get_bundle(self, job_id: str) -> tuple[bytes, str]:
        try:
            response = self.session.get(
                f"{self.base_url}/v1/textbook-kg/jobs/{job_id}/bundle",
                headers=self.headers,
                timeout=(5, 180),
            )
        except requests.RequestException as exc:
            raise TextbookKgError(f"Textbook KG service is unavailable: {exc.__class__.__name__}") from exc
        if not response.ok:
            raise TextbookKgError(
                f"Textbook KG service returned HTTP {response.status_code}",
                response.status_code,
            )
        return response.content, response.headers.get("Content-Type", "application/zip")

    def get_artifact(self, job_id: str, name: str, *, max_bytes: int | None = None) -> bytes:
        allowed_limits = {
            "ragflow_adapter.json": 64 * 1024 * 1024,
            "tree/content_tree.json": 10 * 1024 * 1024,
        }
        artifact_limit = allowed_limits.get(name)
        if artifact_limit is None:
            raise TextbookKgError("The requested Textbook KG artifact is not allowed")
        effective_limit = min(max_bytes, artifact_limit) if max_bytes is not None else artifact_limit
        try:
            response = self.session.get(
                f"{self.base_url}/v1/textbook-kg/jobs/{job_id}/artifacts/{name}",
                headers=self.headers,
                timeout=(5, 180),
            )
        except requests.RequestException as exc:
            raise TextbookKgError(f"Textbook KG service is unavailable: {exc.__class__.__name__}") from exc
        if not response.ok:
            raise TextbookKgError(
                f"Textbook KG service returned HTTP {response.status_code}",
                response.status_code,
            )
        content = response.content
        if not isinstance(content, bytes) or not content:
            raise TextbookKgError("The requested Textbook KG artifact is empty")
        if len(content) > effective_limit:
            raise TextbookKgError("The requested Textbook KG artifact is too large")
        return content
