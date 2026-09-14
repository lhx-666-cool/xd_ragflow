from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}
JOB_STATUSES = {"queued", "running", *TERMINAL_STATUSES}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    status: str
    stage: str
    progress: float
    input_type: str
    input_path: str
    source_sha256: str
    config: dict[str, Any]
    error: str | None
    result: dict[str, Any] | None
    idempotency_key: str | None
    cancel_requested: bool
    created_at: str
    updated_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "input_type": self.input_type,
            "source_sha256": self.source_sha256,
            "error": self.error,
            "result": self.result,
            "cancel_requested": self.cancel_requested,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.jobs_root = self.root / "jobs"
        self.db_path = self.root / "jobs.sqlite3"
        self._lock = threading.RLock()
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self) -> Any:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress REAL NOT NULL,
                    input_type TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    error TEXT,
                    result_json TEXT,
                    idempotency_key TEXT UNIQUE,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            now = utc_now()
            connection.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    stage = 'interrupted',
                    error = 'Service restarted while the task was running; submit retry to continue.',
                    updated_at = ?
                WHERE status = 'running'
                """,
                (now,),
            )

    def create_job(
        self,
        *,
        input_type: str,
        input_path: Path,
        source_sha256: str,
        config: dict[str, Any],
        idempotency_key: str | None,
        job_id: str | None = None,
    ) -> JobRecord:
        now = utc_now()
        job_id = job_id or uuid.uuid4().hex
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, status, stage, progress, input_type, input_path,
                    source_sha256, config_json, idempotency_key, created_at, updated_at
                ) VALUES (?, 'queued', 'queued', 0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    input_type,
                    str(input_path),
                    source_sha256,
                    json.dumps(config, ensure_ascii=False, sort_keys=True),
                    idempotency_key,
                    now,
                    now,
                ),
            )
        return self.require(job_id)

    def get_by_idempotency_key(self, key: str) -> JobRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def get(self, job_id: str) -> JobRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def require(self, job_id: str) -> JobRecord:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def claim_next(self) -> JobRecord | None:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued' AND cancel_requested = 0
                ORDER BY created_at
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            now = utc_now()
            connection.execute(
                """
                UPDATE jobs
                SET status = 'running', stage = 'starting', progress = 0.01, updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (now, row["job_id"]),
            )
            connection.commit()
        return self.require(str(row["job_id"]))

    def update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress: float | None = None,
        error: str | None = None,
        result: dict[str, Any] | None = None,
        clear_error: bool = False,
    ) -> JobRecord:
        if status is not None and status not in JOB_STATUSES:
            raise ValueError(f"Invalid status: {status}")
        assignments = ["updated_at = ?"]
        values: list[Any] = [utc_now()]
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if stage is not None:
            assignments.append("stage = ?")
            values.append(stage)
        if progress is not None:
            assignments.append("progress = ?")
            values.append(max(0.0, min(1.0, float(progress))))
        if clear_error:
            assignments.append("error = NULL")
        elif error is not None:
            assignments.append("error = ?")
            values.append(error)
        if result is not None:
            assignments.append("result_json = ?")
            values.append(json.dumps(result, ensure_ascii=False, sort_keys=True))
        values.append(job_id)
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE job_id = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)
        return self.require(job_id)

    def request_cancel(self, job_id: str) -> JobRecord:
        job = self.require(job_id)
        if job.status in TERMINAL_STATUSES:
            return job
        now = utc_now()
        with self._lock, self._connection() as connection:
            if job.status == "queued":
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'canceled', stage = 'canceled', progress = 1,
                        cancel_requested = 1, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (now, job_id),
                )
            else:
                connection.execute(
                    "UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE job_id = ?",
                    (now, job_id),
                )
        return self.require(job_id)

    def retry(
        self,
        job_id: str,
        *,
        config_updates: dict[str, Any] | None = None,
    ) -> JobRecord:
        job = self.require(job_id)
        if job.status not in {"failed", "canceled"}:
            raise ValueError("Only failed or canceled jobs can be retried.")
        config = dict(job.config)
        config.update(config_updates or {})
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', stage = 'queued', progress = 0,
                    error = NULL, result_json = NULL, cancel_requested = 0, config_json = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (json.dumps(config, ensure_ascii=False, sort_keys=True), utc_now(), job_id),
            )
        return self.require(job_id)

    def job_dir(self, job_id: str) -> Path:
        self.require(job_id)
        path = (self.jobs_root / job_id).resolve()
        if self.jobs_root != path.parent:
            raise ValueError("Invalid job path.")
        return path

    def is_cancel_requested(self, job_id: str) -> bool:
        return self.require(job_id).cancel_requested

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=str(row["job_id"]),
            status=str(row["status"]),
            stage=str(row["stage"]),
            progress=float(row["progress"]),
            input_type=str(row["input_type"]),
            input_path=str(row["input_path"]),
            source_sha256=str(row["source_sha256"]),
            config=json.loads(row["config_json"]),
            error=str(row["error"]) if row["error"] is not None else None,
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            idempotency_key=str(row["idempotency_key"]) if row["idempotency_key"] else None,
            cancel_requested=bool(row["cancel_requested"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
