from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser().resolve() if value else default.resolve()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ServiceSettings:
    job_root: Path
    mineru_api_url: str
    api_token: str
    require_auth: bool
    require_job_models: bool
    max_upload_bytes: int
    worker_concurrency: int
    mineru_timeout_seconds: int
    mineru_backend: str
    mineru_parse_method: str
    mineru_project_root: Path
    concept_project_root: Path
    concept_settings_path: Path
    concept_schema_path: Path
    concept_llm_backend: str
    poll_interval_seconds: float

    @classmethod
    def from_env(cls) -> "ServiceSettings":
        concept_root = _env_path("TEXTBOOK_KG_CONCEPT_ROOT", PROJECT_ROOT)
        mineru_root = _env_path(
            "TEXTBOOK_KG_MINERU_ROOT",
            PROJECT_ROOT.parent / "mineru_textbook_tree",
        )
        return cls(
            job_root=_env_path("TEXTBOOK_KG_JOB_ROOT", PROJECT_ROOT / ".runtime_cache" / "sidecar"),
            mineru_api_url=os.getenv("MINERU_API_URL", "").strip().rstrip("/"),
            api_token=os.getenv("TEXTBOOK_KG_API_TOKEN", "").strip(),
            require_auth=_env_bool("TEXTBOOK_KG_REQUIRE_AUTH", True),
            require_job_models=_env_bool("TEXTBOOK_KG_REQUIRE_JOB_MODELS", False),
            max_upload_bytes=int(os.getenv("TEXTBOOK_KG_MAX_UPLOAD_BYTES", str(2 * 1024**3))),
            worker_concurrency=max(1, int(os.getenv("TEXTBOOK_KG_WORKER_CONCURRENCY", "1"))),
            mineru_timeout_seconds=max(30, int(os.getenv("TEXTBOOK_KG_MINERU_TIMEOUT_SECONDS", "1800"))),
            mineru_backend=os.getenv("TEXTBOOK_KG_MINERU_BACKEND", "pipeline").strip() or "pipeline",
            mineru_parse_method=os.getenv("TEXTBOOK_KG_MINERU_PARSE_METHOD", "ocr").strip() or "ocr",
            mineru_project_root=mineru_root,
            concept_project_root=concept_root,
            concept_settings_path=_env_path(
                "TEXTBOOK_KG_SETTINGS",
                concept_root / "config" / "settings.yaml",
            ),
            concept_schema_path=_env_path(
                "TEXTBOOK_KG_SCHEMA",
                concept_root / "config" / "schema.yaml",
            ),
            concept_llm_backend=os.getenv("TEXTBOOK_KG_LLM_BACKEND", "").strip(),
            poll_interval_seconds=max(
                0.05,
                float(os.getenv("TEXTBOOK_KG_POLL_INTERVAL_SECONDS", "0.5")),
            ),
        )

    @property
    def mineru_tree_script(self) -> Path:
        return self.mineru_project_root / "scripts" / "mineru_toc_content_tree.py"

    @property
    def concept_main_script(self) -> Path:
        return self.concept_project_root / "main.py"

    @property
    def concept_retry_script(self) -> Path:
        return self.concept_project_root / "scripts" / "retry_failed_concept_kg.py"
