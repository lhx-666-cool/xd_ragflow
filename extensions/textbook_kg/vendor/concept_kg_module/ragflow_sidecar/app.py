from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncIterator
from urllib.parse import urlsplit

import fitz
import yaml
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from .config import ServiceSettings
from .mineru_client import RemoteMinerUClient
from .runner import JobExecutor, RunnerProtocol
from .store import JobRecord, JobStore


def _job_url(job_id: str) -> str:
    return f"/v1/textbook-kg/jobs/{job_id}"


def _public_job(job: JobRecord) -> dict[str, Any]:
    payload = job.to_public_dict()
    payload["status_url"] = _job_url(job.job_id)
    if job.status == "succeeded":
        payload["result_url"] = f"{_job_url(job.job_id)}/result"
    return payload


def _safe_artifact_path(artifacts_dir: Path, name: str) -> Path:
    root = artifacts_dir.resolve()
    candidate = (root / name).resolve()
    if candidate == root or root not in candidate.parents:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return candidate


async def _save_upload(upload: UploadFile, destination: Path, max_bytes: int) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        with destination.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail="Uploaded file is too large.")
                digest.update(chunk)
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    if size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")
    return digest.hexdigest()


def _validate_pdf(path: Path) -> None:
    with path.open("rb") as handle:
        signature = handle.read(5)
    if signature != b"%PDF-":
        raise HTTPException(status_code=422, detail="The pdf field is not a PDF file.")
    try:
        with fitz.open(path) as document:
            if document.page_count < 1:
                raise HTTPException(status_code=422, detail="The PDF contains no pages.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="The PDF cannot be opened.") from exc


def _validate_content_tree(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="content_tree must be valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="content_tree must be a JSON object.")
    if not isinstance(payload.get("book_title"), str) or not payload["book_title"].strip():
        raise HTTPException(status_code=422, detail="content_tree is missing book_title.")
    if not isinstance(payload.get("chapters"), list):
        raise HTTPException(status_code=422, detail="content_tree is missing chapters.")


def _check_kg_configuration(settings: ServiceSettings) -> tuple[bool, str]:
    try:
        payload = yaml.safe_load(settings.concept_settings_path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return False, f"Cannot read KG settings: {exc}"
    llm_settings = payload.get("llm") or {}
    embedding_settings = payload.get("embedding") or {}
    merging_settings = payload.get("merging") or {}
    llm_backend = settings.concept_llm_backend or str(llm_settings.get("backend") or "")
    embedding_backend = str(merging_settings.get("embedding_backend") or "hashing")
    if settings.require_job_models:
        return True, "job-scoped RAGFlow chat and embedding models are required"
    required_env: set[str] = set()
    if llm_backend == "openai_compatible":
        required_env.add(str(llm_settings.get("api_key_env") or "OPENAI_API_KEY"))
    if embedding_backend == "openai_compatible":
        required_env.add(
            str(
                embedding_settings.get("api_key_env")
                or llm_settings.get("api_key_env")
                or "OPENAI_API_KEY"
            )
        )
    missing = sorted(name for name in required_env if name and not os.getenv(name))
    if missing:
        return False, f"Missing environment variables: {', '.join(missing)}"
    return True, f"llm={llm_backend or 'default'}, embedding={embedding_backend}"


MODEL_RUNTIME_FIELDS = (
    "model_gateway_url",
    "model_gateway_token",
    "llm_model",
    "embedding_model",
)


def _model_runtime_config(
    *,
    model_gateway_url: str | None,
    model_gateway_token: str | None,
    llm_model: str | None,
    embedding_model: str | None,
    required: bool,
) -> dict[str, str]:
    values = {
        "model_gateway_url": (model_gateway_url or "").strip().rstrip("/"),
        "model_gateway_token": (model_gateway_token or "").strip(),
        "llm_model": (llm_model or "").strip(),
        "embedding_model": (embedding_model or "").strip(),
    }
    present = {key for key, value in values.items() if value}
    if required or present:
        missing = [key for key in MODEL_RUNTIME_FIELDS if not values[key]]
        if missing:
            raise HTTPException(status_code=422, detail=f"Missing job model fields: {', '.join(missing)}")
        parsed = urlsplit(values["model_gateway_url"])
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
        ):
            raise HTTPException(status_code=422, detail="model_gateway_url must use a loopback HTTP(S) address.")
    return values if present or required else {}


def create_app(
    settings: ServiceSettings | None = None,
    *,
    runner: RunnerProtocol | None = None,
    start_executor: bool = True,
) -> FastAPI:
    settings = settings or ServiceSettings.from_env()
    store = JobStore(settings.job_root)
    executor = JobExecutor(store=store, settings=settings, runner=runner)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if start_executor:
            executor.start()
        try:
            yield
        finally:
            executor.stop()

    app = FastAPI(
        title="RAGFlow Textbook KG Sidecar",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.executor = executor

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        if not settings.require_auth:
            return
        if not settings.api_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="TEXTBOOK_KG_API_TOKEN is not configured.",
            )
        scheme, _, value = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(value, settings.api_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    auth = Depends(authorize)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, Any]:
        checks: dict[str, dict[str, Any]] = {}
        try:
            probe = settings.job_root / ".write-probe"
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            checks["job_volume"] = {"ok": True}
        except OSError as exc:
            checks["job_volume"] = {"ok": False, "detail": str(exc)}

        checks["concept_pipeline"] = {
            "ok": settings.concept_main_script.is_file(),
            "path": str(settings.concept_main_script),
        }
        checks["tree_pipeline"] = {
            "ok": settings.mineru_tree_script.is_file(),
            "path": str(settings.mineru_tree_script),
        }
        checks["api_token"] = {
            "ok": bool(settings.api_token) or not settings.require_auth,
            "required": settings.require_auth,
        }
        kg_config_ok, kg_config_detail = _check_kg_configuration(settings)
        checks["kg_configuration"] = {
            "ok": kg_config_ok,
            "settings": str(settings.concept_settings_path),
            "detail": kg_config_detail,
        }
        mineru = RemoteMinerUClient(
            base_url=settings.mineru_api_url,
            timeout_seconds=min(settings.mineru_timeout_seconds, 10),
            backend=settings.mineru_backend,
            parse_method=settings.mineru_parse_method,
        )
        mineru_ok, mineru_detail = mineru.check_ready()
        checks["mineru_api"] = {
            "ok": mineru_ok,
            "url": settings.mineru_api_url,
            "detail": mineru_detail,
        }
        ready = all(bool(item["ok"]) for item in checks.values())
        if not ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": "not_ready", "checks": checks},
            )
        return {"status": "ready", "checks": checks}

    @app.post(
        "/v1/textbook-kg/jobs",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[auth],
    )
    async def create_job(
        pdf: Annotated[UploadFile | None, File()] = None,
        content_tree: Annotated[UploadFile | None, File()] = None,
        book_title: Annotated[str | None, Form()] = None,
        toc_pages: Annotated[str | None, Form()] = None,
        page_offset: Annotated[int | None, Form()] = None,
        chunk_size: Annotated[int, Form(ge=1, le=200)] = 20,
        lang: Annotated[str, Form(min_length=1, max_length=24)] = "ch",
        idempotency_key: Annotated[str | None, Form(max_length=200)] = None,
        model_gateway_url: Annotated[str | None, Form(max_length=500)] = None,
        model_gateway_token: Annotated[str | None, Form(max_length=4096)] = None,
        llm_model: Annotated[str | None, Form(max_length=256)] = None,
        embedding_model: Annotated[str | None, Form(max_length=256)] = None,
    ) -> dict[str, Any]:
        if (pdf is None) == (content_tree is None):
            raise HTTPException(
                status_code=422,
                detail="Provide exactly one of pdf or content_tree.",
            )
        normalized_key = (idempotency_key or "").strip() or None
        if normalized_key:
            existing = store.get_by_idempotency_key(normalized_key)
            if existing is not None:
                return _public_job(existing)

        upload = pdf or content_tree
        assert upload is not None
        input_type = "pdf" if pdf is not None else "content_tree"
        suffix = ".pdf" if input_type == "pdf" else ".json"
        job_id = uuid.uuid4().hex
        input_path = settings.job_root / "jobs" / job_id / "input" / f"source{suffix}"
        source_sha256 = await _save_upload(upload, input_path, settings.max_upload_bytes)
        try:
            if input_type == "pdf":
                _validate_pdf(input_path)
            else:
                _validate_content_tree(input_path)
            config = {
                "book_title": (book_title or "").strip(),
                "toc_pages": (toc_pages or "").strip(),
                "page_offset": page_offset,
                "chunk_size": chunk_size,
                "lang": lang.strip(),
            }
            config.update(
                _model_runtime_config(
                    model_gateway_url=model_gateway_url,
                    model_gateway_token=model_gateway_token,
                    llm_model=llm_model,
                    embedding_model=embedding_model,
                    required=settings.require_job_models,
                )
            )
            try:
                job = store.create_job(
                    input_type=input_type,
                    input_path=input_path,
                    source_sha256=source_sha256,
                    config=config,
                    idempotency_key=normalized_key,
                    job_id=job_id,
                )
            except sqlite3.IntegrityError:
                input_path.unlink(missing_ok=True)
                if normalized_key:
                    existing = store.get_by_idempotency_key(normalized_key)
                    if existing is not None:
                        return _public_job(existing)
                raise
        except Exception:
            input_path.unlink(missing_ok=True)
            raise
        return _public_job(job)

    @app.get("/v1/textbook-kg/jobs/{job_id}", dependencies=[auth])
    def get_job(job_id: str) -> dict[str, Any]:
        return _public_job(_require_job(store, job_id))

    @app.post(
        "/v1/textbook-kg/jobs/{job_id}/retry",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[auth],
    )
    def retry_job(
        job_id: str,
        model_gateway_url: Annotated[str | None, Form(max_length=500)] = None,
        model_gateway_token: Annotated[str | None, Form(max_length=4096)] = None,
        llm_model: Annotated[str | None, Form(max_length=256)] = None,
        embedding_model: Annotated[str | None, Form(max_length=256)] = None,
    ) -> dict[str, Any]:
        _require_job(store, job_id)
        try:
            runtime = _model_runtime_config(
                model_gateway_url=model_gateway_url,
                model_gateway_token=model_gateway_token,
                llm_model=llm_model,
                embedding_model=embedding_model,
                required=settings.require_job_models,
            )
            return _public_job(store.retry(job_id, config_updates=runtime))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/textbook-kg/jobs/{job_id}/cancel",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[auth],
    )
    def cancel_job(job_id: str) -> dict[str, Any]:
        _require_job(store, job_id)
        return _public_job(store.request_cancel(job_id))

    @app.get("/v1/textbook-kg/jobs/{job_id}/result", dependencies=[auth])
    def get_result(job_id: str) -> dict[str, Any]:
        job = _require_job(store, job_id)
        if job.status != "succeeded" or job.result is None:
            raise HTTPException(status_code=409, detail="The job has not succeeded.")
        return job.result

    @app.get(
        "/v1/textbook-kg/jobs/{job_id}/artifacts/{name:path}",
        dependencies=[auth],
    )
    def get_artifact(job_id: str, name: str) -> FileResponse:
        _require_job(store, job_id)
        path = _safe_artifact_path(store.job_dir(job_id) / "artifacts", name)
        return FileResponse(path, filename=path.name)

    @app.get("/v1/textbook-kg/jobs/{job_id}/bundle", dependencies=[auth])
    def get_bundle(job_id: str) -> FileResponse:
        job = _require_job(store, job_id)
        if job.status != "succeeded":
            raise HTTPException(status_code=409, detail="The job has not succeeded.")
        path = _safe_artifact_path(store.job_dir(job_id) / "artifacts", "bundle.zip")
        return FileResponse(path, media_type="application/zip", filename=f"{job_id}.zip")

    return app


def _require_job(store: JobStore, job_id: str) -> JobRecord:
    if len(job_id) != 32 or any(char not in "0123456789abcdef" for char in job_id.lower()):
        raise HTTPException(status_code=404, detail="Job not found.")
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job
