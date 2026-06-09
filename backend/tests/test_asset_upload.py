from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from fastapi.testclient import TestClient

import backend.main as main
from backend.repositories.asset_repository import InMemoryAssetRepository
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.openai_input_guardrail import OpenAIInputGuardrailResult


class _FakeAssetStorage:
    def create_presigned_post(self, asset: dict[str, object]) -> dict[str, object]:
        return {
            "url": "https://s3.example.invalid/upload",
            "fields": {
                "key": str(asset.get("s3_key") or ""),
                "Content-Type": str(asset.get("content_type") or "text/plain"),
            },
        }

    def verify_uploaded(self, asset: dict[str, object]) -> dict[str, object]:
        return {"size_bytes": int(asset.get("size_bytes") or 0)}

    def create_download_url(self, asset: dict[str, object]) -> str:
        return f"https://s3.example.invalid/download/{asset.get('asset_id')}"


class AssetUploadRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ticket_repository = InMemoryTicketRepository()
        self.ticket_repository.initialize()
        self.asset_repository = InMemoryAssetRepository()
        self.asset_repository.initialize()
        self.original_ticket_repository = main.ticket_repository
        self.original_asset_repository = main.asset_repository
        self.original_asset_storage = main.asset_storage
        main.ticket_repository = self.ticket_repository
        main.asset_repository = self.asset_repository
        main.asset_storage = _FakeAssetStorage()
        self.guardrail_patcher = patch.object(
            main,
            "evaluate_openai_input_guardrail",
            AsyncMock(return_value=OpenAIInputGuardrailResult.allow_result()),
        )
        self.guardrail_patcher.start()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()
        self.guardrail_patcher.stop()
        main.ticket_repository = self.original_ticket_repository
        main.asset_repository = self.original_asset_repository
        main.asset_storage = self.original_asset_storage

    def _create_upload_intent(
        self,
        *,
        file_name: str = "rtc-error.log",
        size_bytes: int = 1024,
        ticket_id: str = "TK-ASSET-001",
        customer_id: str = "C-001",
    ) -> dict[str, object]:
        response = self.client.post(
            "/api/assets/upload-intents",
            json={
                "ticket_id": ticket_id,
                "customer_id": customer_id,
                "file_name": file_name,
                "content_type": "text/plain",
                "size_bytes": size_bytes,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_upload_intent_allows_log_err_and_txt_files(self) -> None:
        for file_name in ("client.log", "sdk.err", "trace.txt"):
            payload = self._create_upload_intent(file_name=file_name)
            asset = payload["asset"]
            self.assertEqual(asset["status"], "pending_upload")
            self.assertEqual(asset["original_filename"], file_name)
            self.assertTrue(str(asset["asset_id"]).startswith("ASSET-"))
            self.assertEqual(payload["upload"]["url"], "https://s3.example.invalid/upload")
            self.assertIn("key", payload["upload"]["fields"])

    def test_upload_intent_rejects_unsupported_extension(self) -> None:
        response = self.client.post(
            "/api/assets/upload-intents",
            json={
                "ticket_id": "TK-ASSET-002",
                "customer_id": "C-001",
                "file_name": "screenshot.png",
                "content_type": "image/png",
                "size_bytes": 1024,
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn(".log", response.text)

    def test_upload_intent_rejects_files_over_configured_limit(self) -> None:
        response = self.client.post(
            "/api/assets/upload-intents",
            json={
                "ticket_id": "TK-ASSET-003",
                "customer_id": "C-001",
                "file_name": "huge.log",
                "content_type": "text/plain",
                "size_bytes": 20 * 1024 * 1024 + 1,
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("too large", response.text.lower())

    def test_complete_upload_marks_asset_uploaded_after_storage_head_check(self) -> None:
        payload = self._create_upload_intent()
        asset_id = payload["asset"]["asset_id"]

        response = self.client.post(
            f"/api/assets/{asset_id}/complete",
            json={"customer_id": "C-001"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["asset"]["status"], "uploaded")
        stored = self.asset_repository.get_asset(asset_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["status"], "uploaded")

    def test_ticket_query_attaches_uploaded_assets_to_customer_message_without_agent_reading_file(self) -> None:
        payload = self._create_upload_intent()
        asset_id = payload["asset"]["asset_id"]
        self.client.post(f"/api/assets/{asset_id}/complete", json={"customer_id": "C-001"})

        enqueue_mock = AsyncMock(return_value=True)
        with patch.object(main, "ASYNC_QUERY_ENABLED", True), patch.object(
            main,
            "OPTIMISTIC_PARALLEL_ROUTE_ENABLED",
            True,
            create=True,
        ), patch.object(
            main,
            "build_initial_ack",
            side_effect=AssertionError("attachments must not force server ack"),
        ), patch.object(
            main,
            "execute_client_ticket_agent_runtime",
            side_effect=AssertionError("attachments must not force sync agent execution"),
        ), patch.object(
            main.task_queue,
            "enqueue",
            enqueue_mock,
        ), patch.object(
            main,
            "_enqueue_or_defer_message_sentiment_tag",
            AsyncMock(return_value=False),
        ), patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/query",
                json={
                    "ticket_id": "TK-ASSET-001",
                    "customer_id": "C-001",
                    "product": "audio_video_calling",
                    "message": "Please check this SDK log.",
                    "content_format": "markdown",
                    "asset_ids": [asset_id],
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        enqueue_mock.assert_awaited_once()
        stored_ticket = self.ticket_repository.get_ticket("TK-ASSET-001")
        self.assertIsNotNone(stored_ticket)
        assert stored_ticket is not None
        message = stored_ticket["messages"][-1]
        self.assertEqual(message["content"], "Please check this SDK log.")
        self.assertEqual(message["attachments"][0]["asset_id"], asset_id)
        self.assertEqual(message["attachments"][0]["agent_read_enabled"], False)
        stored_asset = self.asset_repository.get_asset(asset_id)
        self.assertIsNotNone(stored_asset)
        assert stored_asset is not None
        self.assertEqual(stored_asset["status"], "attached")

    def test_ticket_query_rejects_pending_asset_binding(self) -> None:
        payload = self._create_upload_intent(ticket_id="TK-ASSET-PENDING")
        asset_id = payload["asset"]["asset_id"]

        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/api/tickets/query",
                json={
                    "ticket_id": "TK-ASSET-PENDING",
                    "customer_id": "C-001",
                    "product": "audio_video_calling",
                    "message": "Please check this SDK log.",
                    "asset_ids": [asset_id],
                },
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIsNone(self.ticket_repository.get_ticket("TK-ASSET-PENDING"))

    def test_download_url_requires_asset_to_belong_to_customer_when_customer_id_is_supplied(self) -> None:
        payload = self._create_upload_intent()
        asset_id = payload["asset"]["asset_id"]
        self.client.post(f"/api/assets/{asset_id}/complete", json={"customer_id": "C-001"})

        response = self.client.get(f"/api/assets/{asset_id}/download-url?customer_id=C-OTHER")

        self.assertEqual(response.status_code, 403, response.text)

        allowed = self.client.get(f"/api/assets/{asset_id}/download-url?customer_id=C-001")
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertIn(asset_id, allowed.json()["download_url"])
