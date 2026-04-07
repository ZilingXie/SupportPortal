from __future__ import annotations

import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.services import local_source_sync
from backend.services.agora_doc_sync import (
    DiscoveryItem,
    DownloadResult,
    SyncConfig,
    UploadResult,
    _ingest_single_document,
    build_sync_report,
    extract_ingestion_id_from_upload_payload,
    extract_markdown_urls_from_llms_text,
    html_url_to_markdown_url,
    output_relative_path_from_markdown_url,
    run_sync,
    wait_for_ingestion_completion,
)


class _FakeClient:
    def __init__(self, ingestions: list[object], report: dict | Exception | None) -> None:
        self._ingestions = list(ingestions)
        self._report = report

    def get_ingestion(self, ingestion_id: str) -> dict:
        if not self._ingestions:
            raise AssertionError(f"Unexpected extra get_ingestion call for {ingestion_id}")
        payload = self._ingestions.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload

    def get_ingestion_report(self, ingestion_id: str) -> dict:
        if isinstance(self._report, Exception):
            raise self._report
        return self._report or {}


class _Clock:
    def __init__(self, values: list[float]) -> None:
        self._values = list(values)

    def __call__(self) -> float:
        if not self._values:
            raise AssertionError("Clock exhausted")
        return self._values.pop(0)


class _FakeRepository:
    def __init__(self) -> None:
        self.ingestion = {
            "ingestion_id": "KI-LOCAL-1",
            "status": "completed",
        }
        self.processed_source_doc_id: str | None = None
        self.failed_source_doc_id: str | None = None
        self.borrow_local_direct_enter_count = 0
        self.borrow_local_direct_active = 0
        self.upsert_borrow_depths: list[int] = []
        self.create_ingestion_borrow_depths: list[int] = []

    @contextmanager
    def borrow_local_direct_write_connection(self):
        self.borrow_local_direct_enter_count += 1
        self.borrow_local_direct_active += 1
        try:
            yield self
        finally:
            self.borrow_local_direct_active -= 1

    def local_direct_write_connection_active(self) -> bool:
        return self.borrow_local_direct_active > 0

    def upsert_source_document(self, **kwargs: object) -> dict:
        self.upsert_borrow_depths.append(self.borrow_local_direct_active)
        return {
            "source_doc_id": "SRC-LOCAL-1",
            "knowledge_type": "official",
            "source_system": "agora",
            "content_format": "markdown",
            "raw_content": kwargs.get("raw_content"),
            "checksum": "checksum-1",
            "metadata": kwargs.get("metadata") or {},
        }

    def create_sync_run(self, **_: object) -> dict:
        return {"sync_run_id": "SYNC-LOCAL-1"}

    def update_sync_run(self, *args: object, **kwargs: object) -> None:
        _ = args
        _ = kwargs
        return None

    def create_ingestion(self, **_: object) -> dict:
        self.create_ingestion_borrow_depths.append(self.borrow_local_direct_active)
        return {"ingestion_id": "KI-LOCAL-1"}

    def mark_source_document_processed(self, source_doc_id: str, *, processed_ingestion_id: str) -> None:
        _ = processed_ingestion_id
        self.processed_source_doc_id = source_doc_id

    def mark_source_document_failed(self, source_doc_id: str, *, error_message: str) -> None:
        _ = error_message
        self.failed_source_doc_id = source_doc_id

    def get_ingestion(self, ingestion_id: str, *, include_content: bool = False) -> dict:
        _ = ingestion_id
        _ = include_content
        return dict(self.ingestion)

    def get_ingestion_report(self, ingestion_id: str) -> dict:
        _ = ingestion_id
        return {
            "summary": {
                "status": "completed",
                "chunk_count": 4,
                "document_id": "official-123",
                "dedupe_action": "new_document",
            },
            "warnings": [],
        }


class _QueueingRepository:
    def __init__(self, *, recovered_stale_count: int = 0) -> None:
        self.borrow_local_direct_active = 0
        self.sync_run_updates: list[tuple[str, dict[str, object]]] = []
        self.claim_calls: list[dict[str, object]] = []
        self.recovered_stale_count = recovered_stale_count
        self.recover_calls = 0
        self._source_documents: dict[str, dict[str, object]] = {}
        self._ingestion_source_map: dict[str, str] = {}
        self._ingestion_counter = 0

    def initialize(self) -> None:
        return None

    @contextmanager
    def borrow_local_direct_write_connection(self):
        self.borrow_local_direct_active += 1
        try:
            yield self
        finally:
            self.borrow_local_direct_active -= 1

    def local_direct_write_connection_active(self) -> bool:
        return self.borrow_local_direct_active > 0

    def upsert_source_document(self, **kwargs: object) -> dict[str, object]:
        metadata = dict(kwargs.get("metadata") or {})
        external_id = str(kwargs.get("external_id") or "")
        source_doc_id = f"SRC-{external_id.replace('/', '_')}"
        existing = self._source_documents.get(source_doc_id)
        checksum = str(kwargs.get("checksum") or "")
        if existing is not None and str(existing.get("checksum") or "") == checksum:
            return dict(existing)
        row = {
            "source_doc_id": source_doc_id,
            "knowledge_type": "official",
            "source_system": "agora",
            "external_id": external_id,
            "title": kwargs.get("title") or external_id,
            "source_url": kwargs.get("source_url"),
            "published_url": kwargs.get("published_url"),
            "content_format": kwargs.get("content_format") or "markdown",
            "raw_content": kwargs.get("raw_content"),
            "raw_payload": kwargs.get("raw_payload") or {},
            "checksum": checksum,
            "sync_status": "pending",
            "metadata": metadata,
            "processed_ingestion_id": None,
            "last_error": None,
        }
        if existing is not None and str(existing.get("sync_status") or "") == "processed":
            row["sync_status"] = "pending"
        self._source_documents[source_doc_id] = row
        return dict(row)

    def get_source_document(self, source_doc_id: str) -> dict[str, object] | None:
        row = self._source_documents.get(source_doc_id)
        return dict(row) if row is not None else None

    def create_sync_run(self, **_: object) -> dict[str, str]:
        return {"sync_run_id": "SYNC-QUEUE-1"}

    def update_sync_run(self, sync_run_id: str, **kwargs: object) -> None:
        self.sync_run_updates.append((sync_run_id, dict(kwargs)))

    def recover_stale_processing_ingestions(self, **_: object) -> list[dict[str, object]]:
        self.recover_calls += 1
        if self.recover_calls != 1 or self.recovered_stale_count <= 0:
            return []
        return [
            {
                "ingestion_id": f"KI-STALE-{index + 1}",
                "source_doc_id": f"SRC-STALE-{index + 1}",
            }
            for index in range(self.recovered_stale_count)
        ]

    def claim_source_documents(
        self,
        *,
        limit: int,
        source_system: str | None = None,
        knowledge_type: str | None = None,
        claim_token: str,
        claim_host: str | None = None,
        source_doc_ids: list[str] | None = None,
    ) -> list[dict[str, object]]:
        self.claim_calls.append(
            {
                "limit": limit,
                "source_system": source_system,
                "knowledge_type": knowledge_type,
                "claim_token": claim_token,
                "claim_host": claim_host,
                "source_doc_ids": list(source_doc_ids or []),
            }
        )
        allowed_ids = set(source_doc_ids or [])
        claimed: list[dict[str, object]] = []
        for source_doc_id in sorted(allowed_ids):
            if len(claimed) >= limit:
                break
            row = self._source_documents.get(source_doc_id)
            if row is None:
                continue
            if str(row.get("sync_status") or "") not in {"pending", "failed"}:
                continue
            row["sync_status"] = "claimed"
            claimed.append(dict(row))
        return claimed

    def create_ingestion(self, **kwargs: object) -> dict[str, str]:
        self._ingestion_counter += 1
        ingestion_id = f"KI-QUEUE-{self._ingestion_counter}"
        metadata = kwargs.get("request_metadata") if isinstance(kwargs.get("request_metadata"), dict) else {}
        self._ingestion_source_map[ingestion_id] = str(metadata.get("source_doc_id") or "")
        return {"ingestion_id": ingestion_id}

    def get_ingestion_report(self, ingestion_id: str) -> dict[str, object]:
        source_doc_id = self._ingestion_source_map.get(ingestion_id, "unknown")
        return {
            "summary": {
                "status": "completed",
                "document_id": f"doc-{source_doc_id}",
                "chunk_count": 3,
                "dedupe_action": "new_document",
            },
            "warnings": [],
        }

    def mark_source_document_processed(self, source_doc_id: str, *, processed_ingestion_id: str) -> None:
        row = self._source_documents[source_doc_id]
        row["sync_status"] = "processed"
        row["processed_ingestion_id"] = processed_ingestion_id
        row["last_error"] = None

    def mark_source_document_failed(self, source_doc_id: str, *, error_message: str) -> None:
        row = self._source_documents[source_doc_id]
        row["sync_status"] = "failed"
        row["last_error"] = error_message


class AgoraDocSyncTests(unittest.TestCase):
    def test_html_url_to_markdown_url_handles_platform_query(self) -> None:
        actual = html_url_to_markdown_url(
            "https://docs.agora.io/en/video-calling/get-started/get-started-sdk?platform=android"
        )
        self.assertEqual(
            actual,
            "https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk_android.md",
        )

    def test_html_url_to_markdown_url_handles_plain_pages(self) -> None:
        actual = html_url_to_markdown_url(
            "https://docs.agora.io/en/video-calling/overview/product-overview"
        )
        self.assertEqual(
            actual,
            "https://docs-md.agora.io/en/video-calling/overview/product-overview.md",
        )

    def test_output_relative_path_from_markdown_url_uses_url_path(self) -> None:
        actual = output_relative_path_from_markdown_url(
            "https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk_android.md"
        )
        self.assertEqual(actual, "en/video-calling/get-started/get-started-sdk_android.md")

    def test_extract_markdown_urls_from_llms_text_filters_external_links(self) -> None:
        text = """
        - [Video Calling](https://docs-md.agora.io/en/video-calling/overview/product-overview.md)
        - [Console](https://console.agora.io/)
        - [Quickstart](https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk_web.md)
        """
        actual = extract_markdown_urls_from_llms_text(text)
        self.assertEqual(
            actual,
            [
                "https://docs-md.agora.io/en/video-calling/overview/product-overview.md",
                "https://docs-md.agora.io/en/video-calling/get-started/get-started-sdk_web.md",
            ],
        )

    def test_extract_ingestion_id_from_upload_payload_reads_wrapped_response(self) -> None:
        actual = extract_ingestion_id_from_upload_payload(
            {
                "ingestion": {
                    "ingestion_id": "KI-123456",
                },
                "queued": True,
            }
        )
        self.assertEqual(actual, "KI-123456")

    def test_wait_for_ingestion_completion_returns_report_on_completed_status(self) -> None:
        client = _FakeClient(
            ingestions=[
                {"ingestion": {"status": "queued"}},
                {"ingestion": {"status": "processing"}},
                {"ingestion": {"status": "completed", "document_id": "DOC-1", "chunk_count": 12}},
            ],
            report={
                "summary": {
                    "ingestion_id": "KI-1",
                    "status": "completed",
                    "document_id": "DOC-1",
                    "chunk_count": 12,
                    "dedupe_action": "inserted",
                },
                "warnings": [],
            },
        )
        result = wait_for_ingestion_completion(
            client=client,
            ingestion_id="KI-1",
            poll_interval_seconds=0.01,
            poll_timeout_seconds=10.0,
            monotonic=_Clock([0.0, 0.1, 0.2]),
            sleep=lambda _: None,
        )

        self.assertEqual(result.status, "completed")
        self.assertFalse(result.timed_out)
        self.assertEqual(result.report["summary"]["document_id"], "DOC-1")

    def test_wait_for_ingestion_completion_times_out(self) -> None:
        client = _FakeClient(
            ingestions=[
                {"ingestion": {"status": "processing"}},
            ],
            report=None,
        )
        result = wait_for_ingestion_completion(
            client=client,
            ingestion_id="KI-TIMEOUT",
            poll_interval_seconds=0.01,
            poll_timeout_seconds=5.0,
            monotonic=_Clock([0.0, 6.0]),
            sleep=lambda _: None,
        )

        self.assertTrue(result.timed_out)
        self.assertEqual(result.status, "processing")
        self.assertIn("Timed out waiting for ingestion KI-TIMEOUT", result.error)

    def test_build_sync_report_aggregates_failure_counts(self) -> None:
        config = SyncConfig(
            api_base_url="http://localhost:8080",
            output_dir=Path("/tmp/local_knowledge/official/raw"),
            limit=3,
        )
        report = build_sync_report(
            started_at="2026-03-17T10:00:00+00:00",
            finished_at="2026-03-17T10:05:00+00:00",
            config=config,
            discovery={
                "attempted_sources": ["sitemap"],
                "selected_source": "sitemap",
                "errors": [],
                "total_discovered": 3,
                "selected_count": 3,
                "effective_limit": 3,
            },
            download_results=[
                DownloadResult(
                    discovery_url="https://docs.agora.io/en/a",
                    markdown_url="https://docs-md.agora.io/en/a.md",
                    local_path="en/a.md",
                    status="downloaded",
                    size_bytes=100,
                ),
                DownloadResult(
                    discovery_url="https://docs.agora.io/en/b",
                    markdown_url="https://docs-md.agora.io/en/b.md",
                    local_path="en/b.md",
                    status="failed",
                    error="HTTP 404",
                ),
            ],
            upload_results=[
                UploadResult(local_path="en/a.md", upload_status="completed"),
                UploadResult(local_path="en/b.md", upload_status="upload_failed"),
                UploadResult(local_path="en/c.md", upload_status="ingestion_failed"),
                UploadResult(local_path="en/d.md", upload_status="timed_out"),
            ],
            run_error=None,
        )

        self.assertFalse(report["success"])
        self.assertEqual(report["downloads"]["failed"], 1)
        self.assertEqual(report["uploads"]["completed"], 1)
        self.assertEqual(report["uploads"]["upload_failed"], 1)
        self.assertEqual(report["uploads"]["ingestion_failed"], 1)
        self.assertEqual(report["uploads"]["timed_out"], 1)

    def test_ingest_single_document_runs_local_direct_mode(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            markdown_path = root / "en" / "video-calling" / "overview.md"
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text("# Overview\n", encoding="utf-8")
            repository = _FakeRepository()

            with patch.object(local_source_sync, "process_knowledge_ingestion") as process:
                result = _ingest_single_document(
                    file_path=markdown_path,
                    output_dir=root,
                    repository=repository,
                )

        self.assertEqual(result.upload_status, "completed")
        self.assertEqual(result.processing_mode, "local_direct")
        self.assertEqual(result.ingestion_id, "KI-LOCAL-1")
        self.assertEqual(repository.processed_source_doc_id, "SRC-LOCAL-1")
        process.assert_called_once()

    def test_ingest_single_document_reuses_borrowed_local_direct_connection_scope(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            markdown_path = root / "en" / "video-calling" / "overview.md"
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text("# Overview\n", encoding="utf-8")
            repository = _FakeRepository()

            with patch.object(local_source_sync, "process_knowledge_ingestion"):
                result = _ingest_single_document(
                    file_path=markdown_path,
                    output_dir=root,
                    repository=repository,
                )

        self.assertEqual(result.upload_status, "completed")
        self.assertEqual(repository.borrow_local_direct_enter_count, 1)
        self.assertEqual(repository.upsert_borrow_depths, [1])
        self.assertEqual(repository.create_ingestion_borrow_depths, [1])

    def test_run_sync_passes_upload_workers_to_local_ingest(self) -> None:
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "official" / "raw"
            discovery_items = [
                DiscoveryItem(
                    discovery_url="https://docs.agora.io/en/video-calling/overview/product-overview",
                    markdown_url="https://docs-md.agora.io/en/video-calling/overview/product-overview.md",
                    local_path="en/video-calling/overview/product-overview.md",
                )
            ]
            download_results = [
                DownloadResult(
                    discovery_url=discovery_items[0].discovery_url,
                    markdown_url=discovery_items[0].markdown_url,
                    local_path=discovery_items[0].local_path,
                    status="downloaded",
                    size_bytes=100,
                )
            ]
            config = SyncConfig(
                output_dir=output_dir,
                api_base_url=None,
                upload_workers=7,
                limit=1,
            )

            def _fake_download_documents(*, items, output_dir, workers):
                _ = items
                _ = workers
                target = output_dir / "en" / "video-calling" / "overview" / "product-overview.md"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("# Product overview\n", encoding="utf-8")
                return download_results

            with patch(
                "backend.services.agora_doc_sync.discover_documents",
                return_value=(
                    discovery_items,
                    {
                        "attempted_sources": ["sitemap"],
                        "selected_source": "sitemap",
                        "errors": [],
                        "total_discovered": 1,
                        "selected_count": 1,
                        "effective_limit": 1,
                    },
                ),
            ):
                with patch("backend.services.agora_doc_sync.download_documents", side_effect=_fake_download_documents):
                    with patch(
                        "backend.services.agora_doc_sync.ingest_documents_locally",
                        return_value=[UploadResult(local_path=download_results[0].local_path, upload_status="completed")],
                    ) as ingest_mock:
                        exit_code, report, report_path = run_sync(config)

        self.assertEqual(exit_code, 0)
        self.assertTrue(report["success"])
        self.assertEqual(report_path.name, "_sync_report.json")
        ingest_mock.assert_called_once()
        self.assertEqual(ingest_mock.call_args.kwargs["workers"], 7)

    def test_ingest_documents_locally_stages_documents_then_claims_db_queue_with_progress_updates(self) -> None:
        repository = _QueueingRepository(recovered_stale_count=1)
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first = root / "en" / "video-calling" / "overview.md"
            second = root / "en" / "video-calling" / "join-channel.md"
            first.parent.mkdir(parents=True, exist_ok=True)
            first.write_text("# Overview\n", encoding="utf-8")
            second.write_text("# Join channel\n", encoding="utf-8")

            with patch(
                "backend.repositories.knowledge_repository.create_knowledge_repository",
                return_value=repository,
            ):
                with patch(
                    "backend.services.agora_doc_sync._probe_embedding_provider_for_local_sync",
                    return_value={"provider": "siliconflow", "model_id": "BAAI/bge-m3"},
                    create=True,
                ) as probe_mock:
                    with patch.object(local_source_sync, "process_knowledge_ingestion"):
                        from backend.services.agora_doc_sync import ingest_documents_locally

                        results = ingest_documents_locally(
                            markdown_files=[first, second],
                            output_dir=root,
                            workers=2,
                        )

        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(item.local_path for item in results), sorted(["en/video-calling/overview.md", "en/video-calling/join-channel.md"]))
        probe_mock.assert_called_once()
        self.assertTrue(repository.claim_calls)
        self.assertEqual(
            sorted(repository.claim_calls[0]["source_doc_ids"]),
            sorted(["SRC-en_video-calling_join-channel.md", "SRC-en_video-calling_overview.md"]),
        )
        self.assertGreaterEqual(len(repository.sync_run_updates), 2)
        running_updates = [item for item in repository.sync_run_updates if item[1].get("status") == "running"]
        self.assertTrue(running_updates)
        self.assertEqual(running_updates[0][1].get("discovered_count"), 2)
        self.assertEqual(running_updates[0][1].get("summary", {}).get("stale_recovered_count"), 1)
        self.assertEqual(repository.sync_run_updates[-1][1].get("status"), "completed")

    def test_ingest_documents_locally_fails_fast_when_embedding_provider_probe_fails(self) -> None:
        repository = _QueueingRepository()
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            markdown_path = root / "en" / "video-calling" / "overview.md"
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text("# Overview\n", encoding="utf-8")

            with patch(
                "backend.repositories.knowledge_repository.create_knowledge_repository",
                return_value=repository,
            ):
                with patch(
                    "backend.services.agora_doc_sync._probe_embedding_provider_for_local_sync",
                    side_effect=RuntimeError("SiliconFlow embedding request failed: Sorry, your account balance is insufficient"),
                    create=True,
                ):
                    from backend.services.agora_doc_sync import ingest_documents_locally

                    with self.assertRaises(RuntimeError):
                        ingest_documents_locally(
                            markdown_files=[markdown_path],
                            output_dir=root,
                            workers=1,
                        )

        self.assertFalse(repository.claim_calls)
        self.assertEqual(repository.sync_run_updates[-1][1].get("status"), "failed")
        self.assertIn("insufficient", str(repository.sync_run_updates[-1][1].get("summary", {}).get("error_message", "")).lower())

    def test_ingest_documents_locally_serial_retries_conflict_failures(self) -> None:
        repository = _QueueingRepository()
        attempts: dict[str, int] = {}

        def _flaky_process(_repository, ingestion_id):
            source_doc_id = repository._ingestion_source_map[ingestion_id]
            attempts[source_doc_id] = attempts.get(source_doc_id, 0) + 1
            if source_doc_id.endswith("firewall_web.md") and attempts[source_doc_id] == 1:
                raise RuntimeError("deadlock detected")
            return None

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first = root / "en" / "video-calling" / "reference" / "firewall_web.md"
            second = root / "en" / "video-calling" / "reference" / "firewall_unreal.md"
            first.parent.mkdir(parents=True, exist_ok=True)
            first.write_text("# firewall web\n", encoding="utf-8")
            second.write_text("# firewall unreal\n", encoding="utf-8")

            with patch(
                "backend.repositories.knowledge_repository.create_knowledge_repository",
                return_value=repository,
            ):
                with patch(
                    "backend.services.agora_doc_sync._probe_embedding_provider_for_local_sync",
                    return_value={"provider": "siliconflow", "model_id": "BAAI/bge-m3"},
                    create=True,
                ):
                    with patch.object(local_source_sync, "process_knowledge_ingestion", side_effect=_flaky_process):
                        from backend.services.agora_doc_sync import ingest_documents_locally

                        results = ingest_documents_locally(
                            markdown_files=[first, second],
                            output_dir=root,
                            workers=2,
                        )

        self.assertEqual(sorted(item.upload_status for item in results), ["completed", "completed"])
        self.assertEqual(repository.sync_run_updates[-1][1].get("status"), "completed")
        self.assertEqual(repository.sync_run_updates[-1][1].get("summary", {}).get("serial_retry_count"), 1)


if __name__ == "__main__":
    unittest.main()
