from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Protocol

import yaml

from .adapter import build_artifact_manifest, build_ragflow_adapter, load_json, write_json
from .config import ServiceSettings
from .mineru_client import JobCanceled, RemoteMinerUClient
from .store import JobRecord, JobStore


class RunnerProtocol(Protocol):
    def run(self, job: JobRecord) -> dict[str, Any]: ...


class PipelineRunner:
    def __init__(self, *, settings: ServiceSettings, store: JobStore) -> None:
        self.settings = settings
        self.store = store
        self.mineru = RemoteMinerUClient(
            base_url=settings.mineru_api_url,
            timeout_seconds=settings.mineru_timeout_seconds,
            backend=settings.mineru_backend,
            parse_method=settings.mineru_parse_method,
        )

    def run(self, job: JobRecord) -> dict[str, Any]:
        job_dir = self.store.job_dir(job.job_id)
        artifacts_dir = job_dir / "artifacts"
        tree_dir = artifacts_dir / "tree"
        kg_dir = artifacts_dir / "kg"
        work_dir = job_dir / "work"
        log_path = job_dir / "pipeline.log"
        for path in (artifacts_dir, tree_dir, kg_dir, work_dir):
            path.mkdir(parents=True, exist_ok=True)

        tree_path = tree_dir / "content_tree.json"
        input_path = Path(job.input_path)
        if not tree_path.exists():
            if job.input_type == "pdf":
                self.store.update(job.job_id, stage="ocr", progress=0.05)
                merged_content = self.mineru.parse_pdf(
                    pdf_path=input_path,
                    output_dir=work_dir / "mineru",
                    chunk_size=int(job.config.get("chunk_size", 20)),
                    lang=str(job.config.get("lang", "ch")),
                    progress_callback=lambda index, total: self.store.update(
                        job.job_id,
                        stage="ocr",
                        progress=0.05 + 0.35 * index / max(total, 1),
                    ),
                    cancel_check=lambda: self.store.is_cancel_requested(job.job_id),
                )
                self._ensure_not_canceled(job.job_id)
                self.store.update(job.job_id, stage="tree", progress=0.42)
                command = [
                    sys.executable,
                    str(self.settings.mineru_tree_script),
                    "--pdf",
                    str(input_path),
                    "--output-dir",
                    str(tree_dir),
                    "--skip-ocr",
                    "--content-list",
                    str(merged_content),
                ]
                self._append_tree_options(command, job.config)
                self._run_command(job.job_id, command, self.settings.mineru_project_root, log_path)
            else:
                self.store.update(job.job_id, stage="tree", progress=0.45)
                shutil.copy2(input_path, tree_path)
        self._validate_tree(tree_path)

        concept_graph_path = kg_dir / "concept_kg.json"
        self._ensure_not_canceled(job.job_id)
        if not concept_graph_path.exists():
            self.store.update(job.job_id, stage="kg_extract", progress=0.5)
            concept_settings_path, model_environment = self._job_model_settings(job, work_dir)
            raw_batches = kg_dir / "raw_extraction_batches.json"
            if raw_batches.exists():
                command = [
                    sys.executable,
                    str(self.settings.concept_retry_script),
                    "--kg-output-dir",
                    str(kg_dir),
                    "--schema",
                    str(self.settings.concept_schema_path),
                    "--settings",
                    str(concept_settings_path),
                    "--pipeline",
                    "hierarchical",
                ]
            else:
                command = [
                    sys.executable,
                    str(self.settings.concept_main_script),
                    "--doc_graph",
                    str(tree_path),
                    "--output_dir",
                    str(kg_dir),
                    "--schema",
                    str(self.settings.concept_schema_path),
                    "--settings",
                    str(concept_settings_path),
                    "--pipeline",
                    "hierarchical",
                    "--no-run-subdir",
                ]
            if self.settings.concept_llm_backend:
                command.extend(["--llm-backend", self.settings.concept_llm_backend])
            self._run_command(
                job.job_id,
                command,
                self.settings.concept_project_root,
                log_path,
                extra_environment=model_environment,
            )

        self._ensure_not_canceled(job.job_id)
        self.store.update(job.job_id, stage="kg_build", progress=0.86)
        graph = load_json(concept_graph_path)
        if not isinstance(graph.get("entities"), list) or not isinstance(graph.get("relations"), list):
            raise RuntimeError("concept_kg.json has an invalid structure.")

        self.store.update(job.job_id, stage="export", progress=0.92)
        adapter_path = artifacts_dir / "ragflow_adapter.json"
        adapter = build_ragflow_adapter(
            tree_path=tree_path,
            kg_dir=kg_dir,
            output_path=adapter_path,
        )
        result = {
            "job_id": job.job_id,
            "book_title": adapter["document"]["name"],
            "entity_count": len(graph["entities"]),
            "relation_count": len(graph["relations"]),
            "chunk_count": adapter["summary"]["chunk_count"],
            "artifacts": [],
        }
        write_json(artifacts_dir / "result.json", result)
        result["artifacts"] = build_artifact_manifest(artifacts_dir)
        write_json(artifacts_dir / "result.json", result)
        self._write_bundle(artifacts_dir)
        result["bundle"] = {
            "name": "bundle.zip",
            "size": (artifacts_dir / "bundle.zip").stat().st_size,
        }
        write_json(artifacts_dir / "result.json", result)
        return result

    @staticmethod
    def _append_tree_options(command: list[str], config: dict[str, Any]) -> None:
        book_title = str(config.get("book_title") or "").strip()
        toc_pages = str(config.get("toc_pages") or "").strip()
        page_offset = config.get("page_offset")
        if book_title:
            command.extend(["--book-title", book_title])
        if toc_pages:
            command.extend(["--toc-pages", toc_pages])
        if page_offset is not None:
            command.extend(["--page-offset", str(int(page_offset))])

    @staticmethod
    def _validate_tree(path: Path) -> None:
        payload = load_json(path)
        if not isinstance(payload, dict):
            raise RuntimeError("content_tree.json must be a JSON object.")
        if not isinstance(payload.get("book_title"), str) or not payload["book_title"].strip():
            raise RuntimeError("content_tree.json is missing book_title.")
        if not isinstance(payload.get("chapters"), list):
            raise RuntimeError("content_tree.json is missing chapters.")

    def _job_model_settings(self, job: JobRecord, work_dir: Path) -> tuple[Path, dict[str, str]]:
        runtime_values = {key: str(job.config.get(key) or "").strip() for key in (
            "model_gateway_url",
            "model_gateway_token",
            "llm_model",
            "embedding_model",
        )}
        if not any(runtime_values.values()):
            if self.settings.require_job_models:
                raise RuntimeError("This job is missing its RAGFlow model configuration.")
            return self.settings.concept_settings_path, {}
        missing = [key for key, value in runtime_values.items() if not value]
        if missing:
            raise RuntimeError(f"This job has incomplete RAGFlow model configuration: {', '.join(missing)}")
        try:
            payload = yaml.safe_load(self.settings.concept_settings_path.read_text(encoding="utf-8-sig")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise RuntimeError("Cannot load the concept KG settings template.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("The concept KG settings template must be a mapping.")
        llm = dict(payload.get("llm") or {})
        embedding = dict(payload.get("embedding") or {})
        merging = dict(payload.get("merging") or {})
        llm.update(
            {
                "backend": "openai_compatible",
                "base_url": runtime_values["model_gateway_url"],
                "api_key_env": "TEXTBOOK_KG_JOB_TOKEN",
                "model": runtime_values["llm_model"],
            }
        )
        embedding.update(
            {
                "base_url": runtime_values["model_gateway_url"],
                "api_key_env": "TEXTBOOK_KG_JOB_TOKEN",
                "model": runtime_values["embedding_model"],
                "encoding_format": "float",
            }
        )
        merging["embedding_backend"] = "openai_compatible"
        payload.update({"llm": llm, "embedding": embedding, "merging": merging})
        settings_path = work_dir / "concept_settings.yaml"
        settings_path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return settings_path, {"TEXTBOOK_KG_JOB_TOKEN": runtime_values["model_gateway_token"]}

    def _run_command(
        self,
        job_id: str,
        command: list[str],
        cwd: Path,
        log_path: Path,
        *,
        extra_environment: dict[str, str] | None = None,
    ) -> None:
        if not command[1] or not Path(command[1]).exists():
            raise FileNotFoundError(f"Pipeline entry point does not exist: {command[1]}")
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment.update(extra_environment or {})
        popen_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "env": environment,
            "stdout": None,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        with log_path.open("a", encoding="utf-8") as log_handle:
            log_handle.write(f"\n$ {' '.join(command)}\n")
            log_handle.flush()
            popen_kwargs["stdout"] = log_handle
            process = subprocess.Popen(command, **popen_kwargs)
            while process.poll() is None:
                if self.store.is_cancel_requested(job_id):
                    self._terminate_process(process)
                    raise JobCanceled("The job was canceled.")
                time.sleep(0.25)
            if process.returncode != 0:
                tail = self._read_log_tail(log_path)
                raise RuntimeError(
                    f"Pipeline command failed with exit code {process.returncode}.\n{tail}"
                )

    @staticmethod
    def _terminate_process(process: subprocess.Popen[Any]) -> None:
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    @staticmethod
    def _read_log_tail(path: Path, max_chars: int = 6000) -> str:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:]

    def _ensure_not_canceled(self, job_id: str) -> None:
        if self.store.is_cancel_requested(job_id):
            raise JobCanceled("The job was canceled.")

    @staticmethod
    def _write_bundle(artifacts_dir: Path) -> None:
        bundle_path = artifacts_dir / "bundle.zip"
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(artifacts_dir.rglob("*")):
                if not path.is_file() or path == bundle_path:
                    continue
                archive.write(path, path.relative_to(artifacts_dir).as_posix())


class JobExecutor:
    def __init__(
        self,
        *,
        store: JobStore,
        settings: ServiceSettings,
        runner: RunnerProtocol | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.runner = runner or PipelineRunner(settings=settings, store=store)
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._threads:
            return
        for index in range(self.settings.worker_concurrency):
            thread = threading.Thread(
                target=self._run_loop,
                name=f"textbook-kg-worker-{index + 1}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads.clear()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            job = self.store.claim_next()
            if job is None:
                self._stop_event.wait(self.settings.poll_interval_seconds)
                continue
            try:
                result = self.runner.run(job)
                if self.store.is_cancel_requested(job.job_id):
                    raise JobCanceled("The job was canceled.")
                self.store.update(
                    job.job_id,
                    status="succeeded",
                    stage="completed",
                    progress=1,
                    result=result,
                    clear_error=True,
                )
            except JobCanceled as exc:
                self.store.update(
                    job.job_id,
                    status="canceled",
                    stage="canceled",
                    progress=1,
                    error=str(exc),
                )
            except Exception as exc:  # noqa: BLE001
                self.store.update(
                    job.job_id,
                    status="failed",
                    stage="failed",
                    error=str(exc)[-12000:],
                )
