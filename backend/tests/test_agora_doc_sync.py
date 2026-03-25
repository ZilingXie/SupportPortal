from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

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

    def upsert_source_document(self, **kwargs: object) -> dict:
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

            with patch("backend.services.local_source_sync.process_knowledge_ingestion") as process:
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


if __name__ == "__main__":
    unittest.main()
