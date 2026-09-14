from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any

import fitz
import yaml

from ragflow_sidecar.adapter import build_ragflow_adapter
from ragflow_sidecar.config import ServiceSettings
from ragflow_sidecar.mineru_client import MinerUClientError, RemoteMinerUClient
from ragflow_sidecar.runner import PipelineRunner
from ragflow_sidecar.store import JobStore


class _Response:
    def __init__(self, payload: bytes, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int) -> list[bytes]:
        return [self.payload]

    def close(self) -> None:
        return None


class _Session:
    def __init__(self, payloads: list[bytes]) -> None:
        self.payloads = payloads
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return _Response(self.payloads.pop(0))


def _zip_bytes(name: str, payload: Any) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, json.dumps(payload, ensure_ascii=False))
    return buffer.getvalue()


class MinerUClientTests(unittest.TestCase):
    def test_pdf_is_split_and_page_indexes_are_merged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_path = root / "book.pdf"
            document = fitz.open()
            for _ in range(3):
                document.new_page()
            document.save(pdf_path)
            document.close()
            session = _Session(
                [
                    _zip_bytes("book/content_list.json", [{"page_idx": 0, "text": "a"}]),
                    _zip_bytes("book/content_list.json", [{"page_idx": 0, "text": "b"}]),
                ]
            )
            client = RemoteMinerUClient(base_url="http://mineru", session=session)

            merged = client.parse_pdf(
                pdf_path=pdf_path,
                output_dir=root / "out",
                chunk_size=2,
                lang="ch",
            )

            self.assertEqual([0, 2], [item["page_idx"] for item in json.loads(merged.read_text())])
            self.assertEqual("0", session.calls[0]["data"]["start_page_id"])
            self.assertEqual("1", session.calls[0]["data"]["end_page_id"])
            self.assertEqual("2", session.calls[1]["data"]["start_page_id"])
            self.assertEqual("2", session.calls[1]["data"]["end_page_id"])

    def test_non_zip_response_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_path = root / "book.pdf"
            document = fitz.open()
            document.new_page()
            document.save(pdf_path)
            document.close()
            client = RemoteMinerUClient(
                base_url="http://mineru",
                session=_Session([b'{"error":"bad"}']),
            )
            with self.assertRaisesRegex(MinerUClientError, "non-ZIP"):
                client.parse_pdf(
                    pdf_path=pdf_path,
                    output_dir=root / "out",
                    chunk_size=20,
                    lang="ch",
                )

    def test_zip_traversal_and_symbolic_links_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            traversal_zip = root / "traversal.zip"
            with zipfile.ZipFile(traversal_zip, "w") as archive:
                archive.writestr("../escape.json", "{}")
            with self.assertRaisesRegex(MinerUClientError, "traversal"):
                RemoteMinerUClient.safe_extract_zip(traversal_zip, root / "a", 1000)

            symlink_zip = root / "symlink.zip"
            with zipfile.ZipFile(symlink_zip, "w") as archive:
                entry = zipfile.ZipInfo("link")
                entry.create_system = 3
                entry.external_attr = 0o120777 << 16
                archive.writestr(entry, "target")
            with self.assertRaisesRegex(MinerUClientError, "symbolic link"):
                RemoteMinerUClient.safe_extract_zip(symlink_zip, root / "b", 1000)

    def test_missing_content_list_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(MinerUClientError, "content_list"):
                RemoteMinerUClient.find_content_list(Path(temporary), "book")


class StoreTests(unittest.TestCase):
    def test_restart_marks_running_job_failed_and_retry_requeues_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.json"
            source.write_text("{}", encoding="utf-8")
            store = JobStore(root)
            job = store.create_job(
                input_type="content_tree",
                input_path=source,
                source_sha256="abc",
                config={},
                idempotency_key="key",
            )
            store.claim_next()

            restarted = JobStore(root)
            interrupted = restarted.require(job.job_id)
            self.assertEqual("failed", interrupted.status)
            self.assertEqual("interrupted", interrupted.stage)
            self.assertEqual("queued", restarted.retry(job.job_id).status)

    def test_retry_refreshes_only_job_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.json"
            source.write_text("{}", encoding="utf-8")
            store = JobStore(root)
            job = store.create_job(
                input_type="content_tree",
                input_path=source,
                source_sha256="abc",
                config={"book_title": "Book", "model_gateway_token": "old"},
                idempotency_key=None,
            )
            store.request_cancel(job.job_id)

            refreshed = store.retry(job.job_id, config_updates={"model_gateway_token": "new"})

            self.assertEqual("Book", refreshed.config["book_title"])
            self.assertEqual("new", refreshed.config["model_gateway_token"])


class PipelineRunnerTests(unittest.TestCase):
    def test_job_model_settings_do_not_write_gateway_token(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tree.json"
            source.write_text("{}", encoding="utf-8")
            settings = replace(
                ServiceSettings.from_env(),
                job_root=root / "state",
                concept_project_root=project_root,
                concept_settings_path=project_root / "config" / "settings.yaml",
                require_job_models=True,
            )
            store = JobStore(settings.job_root)
            job = store.create_job(
                input_type="content_tree",
                input_path=source,
                source_sha256="abc",
                config={
                    "model_gateway_url": "http://127.0.0.1:9443/v1/textbook_kg/model-gateway",
                    "model_gateway_token": "scoped-secret",
                    "llm_model": "chat@provider",
                    "embedding_model": "embedding@provider",
                },
                idempotency_key=None,
            )
            work_dir = root / "work"
            work_dir.mkdir()

            settings_path, environment = PipelineRunner(settings=settings, store=store)._job_model_settings(job, work_dir)

            runtime_text = settings_path.read_text(encoding="utf-8")
            runtime = yaml.safe_load(runtime_text)
            self.assertNotIn("scoped-secret", runtime_text)
            self.assertEqual("TEXTBOOK_KG_JOB_TOKEN", runtime["llm"]["api_key_env"])
            self.assertEqual("chat@provider", runtime["llm"]["model"])
            self.assertEqual("embedding@provider", runtime["embedding"]["model"])
            self.assertEqual({"TEXTBOOK_KG_JOB_TOKEN": "scoped-secret"}, environment)

    def test_content_tree_runs_through_mock_kg_and_exports_adapter(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tree.json"
            source.write_text(
                json.dumps(
                    {
                        "book_title": "Fixture Book",
                        "chapters": [
                            {
                                "marker": "1",
                                "title": "Networks",
                                "label": "1 Networks",
                                "level": 1,
                                "content": "",
                                "children": [
                                    {
                                        "marker": "1.1",
                                        "title": "Protocols",
                                        "label": "1.1 Protocols",
                                        "level": 2,
                                        "content": "TCP is a Protocol. TCP is used for Reliable Delivery.",
                                        "children": [],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            settings = replace(
                ServiceSettings.from_env(),
                job_root=root / "state",
                concept_project_root=project_root,
                concept_settings_path=project_root
                / "ragflow_sidecar"
                / "config"
                / "settings.mock.yaml",
                concept_schema_path=project_root / "config" / "schema.yaml",
                concept_llm_backend="mock",
            )
            store = JobStore(settings.job_root)
            job_id = "a" * 32
            job = store.create_job(
                input_type="content_tree",
                input_path=source,
                source_sha256="abc",
                config={},
                idempotency_key=None,
                job_id=job_id,
            )

            result = PipelineRunner(settings=settings, store=store).run(job)

            self.assertGreater(result["entity_count"], 0)
            self.assertGreater(result["relation_count"], 0)
            self.assertGreater(result["chunk_count"], 0)
            artifacts = store.job_dir(job_id) / "artifacts"
            self.assertTrue((artifacts / "ragflow_adapter.json").is_file())
            self.assertTrue((artifacts / "bundle.zip").is_file())


class AdapterTests(unittest.TestCase):
    def test_adapter_maps_graph_and_chapter_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kg_dir = root / "kg"
            kg_dir.mkdir()
            (root / "content_tree.json").write_text(
                json.dumps({"book_title": "Book", "chapters": []}),
                encoding="utf-8",
            )
            (kg_dir / "concept_kg.json").write_text(
                json.dumps(
                    {
                        "metadata": {"source_document": "Book"},
                        "entities": [
                            {
                                "entity_id": "e1",
                                "canonical_name": "TCP",
                                "type": "Protocol",
                                "merged_definitions": ["传输协议"],
                                "merged_source_node_ids": ["s1"],
                            }
                        ],
                        "relations": [
                            {
                                "head": "TCP",
                                "tail": "IP",
                                "head_entity_id": "e1",
                                "tail_entity_id": "e2",
                                "relation": "RUNS_OVER",
                                "evidences": ["证据"],
                                "source_node_ids": ["s1"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (kg_dir / "docnode_to_concepts.json").write_text(
                json.dumps({"s1": {"entity_ids": ["e1"], "relation_ids": ["r1"]}}),
                encoding="utf-8",
            )
            (kg_dir / "extractable_doc_nodes.json").write_text(
                json.dumps(
                    [
                        {
                            "source_node_id": "s1",
                            "content": "TCP content",
                            "path_labels": ["Chapter 1"],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            payload = build_ragflow_adapter(
                tree_path=root / "content_tree.json",
                kg_dir=kg_dir,
                output_path=root / "adapter.json",
            )

            self.assertEqual("TCP", payload["knowledge_graph"]["nodes"][0]["entity_name"])
            self.assertEqual("e1", payload["knowledge_graph"]["edges"][0]["src_id"])
            self.assertEqual(["TCP"], payload["chunks"][0]["important_keywords"])


class ApiTests(unittest.TestCase):
    @staticmethod
    def _settings(root: Path) -> ServiceSettings:
        base = ServiceSettings.from_env()
        return replace(
            base,
            job_root=root,
            api_token="secret",
            require_auth=True,
            mineru_api_url="",
        )

    def test_auth_upload_idempotency_cancel_and_path_safety(self) -> None:
        try:
            from fastapi.testclient import TestClient
            from ragflow_sidecar.app import create_app
        except (ImportError, RuntimeError) as exc:
            self.skipTest(str(exc))
            return
        with tempfile.TemporaryDirectory() as temporary:
            app = create_app(self._settings(Path(temporary)), start_executor=False)
            tree = json.dumps({"book_title": "Book", "chapters": []}).encode()
            with TestClient(app) as client:
                unauthorized = client.post(
                    "/v1/textbook-kg/jobs",
                    files={"content_tree": ("tree.json", tree, "application/json")},
                )
                self.assertEqual(401, unauthorized.status_code)
                headers = {"Authorization": "Bearer secret"}
                first = client.post(
                    "/v1/textbook-kg/jobs",
                    headers=headers,
                    files={"content_tree": ("tree.json", tree, "application/json")},
                    data={"idempotency_key": "same"},
                )
                self.assertEqual(202, first.status_code)
                second = client.post(
                    "/v1/textbook-kg/jobs",
                    headers=headers,
                    files={"content_tree": ("tree.json", tree, "application/json")},
                    data={"idempotency_key": "same"},
                )
                self.assertEqual(first.json()["job_id"], second.json()["job_id"])
                job_id = first.json()["job_id"]
                canceled = client.post(
                    f"/v1/textbook-kg/jobs/{job_id}/cancel",
                    headers=headers,
                )
                self.assertEqual("canceled", canceled.json()["status"])
                traversal = client.get(
                    f"/v1/textbook-kg/jobs/{job_id}/artifacts/../input/source.json",
                    headers=headers,
                )
                self.assertEqual(404, traversal.status_code)

    def test_invalid_inputs_and_not_ready(self) -> None:
        try:
            from fastapi.testclient import TestClient
            from ragflow_sidecar.app import create_app
        except (ImportError, RuntimeError) as exc:
            self.skipTest(str(exc))
            return
        with tempfile.TemporaryDirectory() as temporary:
            app = create_app(self._settings(Path(temporary)), start_executor=False)
            with TestClient(app) as client:
                headers = {"Authorization": "Bearer secret"}
                response = client.post(
                    "/v1/textbook-kg/jobs",
                    headers=headers,
                    files={"content_tree": ("bad.json", b"{}", "application/json")},
                )
                self.assertEqual(422, response.status_code)
                self.assertEqual(503, client.get("/readyz").status_code)

    def test_job_model_fields_are_required_redacted_and_refreshed(self) -> None:
        try:
            from fastapi.testclient import TestClient
            from ragflow_sidecar.app import create_app
        except (ImportError, RuntimeError) as exc:
            self.skipTest(str(exc))
            return
        with tempfile.TemporaryDirectory() as temporary:
            settings = replace(self._settings(Path(temporary)), require_job_models=True)
            app = create_app(settings, start_executor=False)
            tree = json.dumps({"book_title": "Book", "chapters": []}).encode()
            headers = {"Authorization": "Bearer secret"}
            with TestClient(app) as client:
                missing = client.post(
                    "/v1/textbook-kg/jobs",
                    headers=headers,
                    files={"content_tree": ("tree.json", tree, "application/json")},
                )
                self.assertEqual(422, missing.status_code)
                runtime = {
                    "model_gateway_url": "http://127.0.0.1:9443/v1/textbook_kg/model-gateway",
                    "model_gateway_token": "old-scoped-token",
                    "llm_model": "chat@provider",
                    "embedding_model": "embedding@provider",
                }
                created = client.post(
                    "/v1/textbook-kg/jobs",
                    headers=headers,
                    files={"content_tree": ("tree.json", tree, "application/json")},
                    data=runtime,
                )
                self.assertEqual(202, created.status_code)
                self.assertNotIn("token", json.dumps(created.json()).lower())
                job_id = created.json()["job_id"]
                client.post(f"/v1/textbook-kg/jobs/{job_id}/cancel", headers=headers)
                runtime["model_gateway_token"] = "new-scoped-token"
                retried = client.post(
                    f"/v1/textbook-kg/jobs/{job_id}/retry",
                    headers=headers,
                    data=runtime,
                )
                self.assertEqual(202, retried.status_code)
                self.assertNotIn("new-scoped-token", json.dumps(retried.json()))
                self.assertEqual("new-scoped-token", app.state.store.require(job_id).config["model_gateway_token"])


if __name__ == "__main__":
    unittest.main()
