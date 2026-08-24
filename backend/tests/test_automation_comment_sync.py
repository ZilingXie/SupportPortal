"""Contract tests for the /automation/production comment ingestion (p2-110 Phase C)."""

import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import backend.services.automation_account_reply_sync as reply_module
from backend.automation_production_runtime import create_app


ENV = {
    "AUTOMATION_ENVIRONMENT": "production",
    "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1",
    "n8n_request_token": "execution-token",
}

SNAPSHOT = {
    "source_updated_at": "2026-08-24T00:00:00Z",
    "snapshot_complete": True,
    "comments": [
        {
            "zendesk_comment_id": "c1",
            "author_kind": "customer",
            "author_id": "end-user-1",
            "is_public": True,
            "is_initial": False,
            "created_at": "2026-08-24T01:00:00Z",
            "body": "here is my account type",
        }
    ],
    "trigger_comment_id": "c1",
}


class _FakeRepository:
    DEFAULT_CASE = {
        "account_case_id": "AC-123",
        "billing_ticket_id": "AC-123",
        "client_ticket_id": "123",
        "zendesk_ticket_id": "123",
        "processing_profile": "production",
        "automation_status": "automation",
        "route_family": "automated",
        "execution_action": "fraud_account",
        "route_classification": {"handler_binding_status": "active"},
        "created_at": "2026-08-23T00:00:00Z",
    }

    def __init__(self, account_case="default") -> None:
        self.account_case = dict(self.DEFAULT_CASE) if account_case == "default" else account_case
        self.synced: list[dict] = []

    def get_account_case_by_ticket_id(self, ticket_id):
        return dict(self.account_case) if self.account_case is not None else None

    def sync_account_case_comments(self, *, ticket_id, account_case_id, snapshot, synced_at):
        self.synced.append({"ticket_id": ticket_id})
        return {"status": "synced", "comment_count": len(snapshot.comments), "synced_at": synced_at}

    def begin_idempotent_request(self, scope, key, created_at=None, retry_failed=False):
        return {"created": True}

    def complete_idempotent_request(self, scope, key, response_payload=None, updated_at=None):
        return {"state": "completed"}


class CommentSyncEndpointTest(unittest.TestCase):
    def test_comment_sync_target_requires_token_and_reports_membership(self):
        with patch.dict(os.environ, ENV, clear=False):
            with TestClient(create_app()) as client:
                self.assertEqual(
                    client.get("/api/integrations/zendesk/account-cases/123/comment-sync-target").status_code,
                    401,
                )
                repository = _FakeRepository()
                with patch(
                    "backend.automation_production_runtime._ticket_repository",
                    return_value=repository,
                ):
                    found = client.get(
                        "/api/integrations/zendesk/account-cases/123/comment-sync-target",
                        headers={"X-N8n-Request-Token": "execution-token"},
                    )
                    self.assertEqual(found.status_code, 200, found.text)
                    payload = found.json()
                    self.assertTrue(payload["is_account_case"])
                    self.assertEqual(
                        payload["comments_endpoint"],
                        "/api/integrations/zendesk/account-cases/123/comments",
                    )

    def test_put_comments_syncs_snapshot_and_runs_trigger(self):
        with patch.dict(os.environ, ENV, clear=False):
            with TestClient(create_app()) as client:
                repository = _FakeRepository()
                trigger_payload = {"trigger_status": "processed", "trigger_comment_id": "c1"}
                with patch(
                    "backend.automation_production_runtime._ticket_repository",
                    return_value=repository,
                ), patch(
                    "backend.services.automation_account_reply_sync.process_zendesk_comment_trigger",
                    new_callable=AsyncMock,
                    return_value=trigger_payload,
                ) as trigger:
                    response = client.put(
                        "/api/integrations/zendesk/account-cases/123/comments",
                        json=SNAPSHOT,
                        headers={"X-N8n-Request-Token": "execution-token"},
                    )
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["status"], "synced")
                self.assertEqual(body["trigger_status"], "processed")
                self.assertEqual(repository.synced[0]["ticket_id"], "123")
                trigger.assert_awaited_once()
                self.assertEqual(trigger.call_args.kwargs["trigger_comment_id"], "c1")

    def test_put_comments_rejects_bad_snapshot_and_missing_case(self):
        with patch.dict(os.environ, ENV, clear=False):
            with TestClient(create_app()) as client:
                bad = client.put(
                    "/api/integrations/zendesk/account-cases/123/comments",
                    json={"comments": "nope"},
                    headers={"X-N8n-Request-Token": "execution-token"},
                )
                self.assertEqual(bad.status_code, 422)
                missing = _FakeRepository(account_case=None)
                with patch(
                    "backend.automation_production_runtime._ticket_repository",
                    return_value=missing,
                ), patch(
                    "backend.services.automation_account_reply_sync.process_zendesk_comment_trigger",
                    new_callable=AsyncMock,
                    return_value={"trigger_status": "ignored_no_trigger"},
                ):
                    not_found = client.put(
                        "/api/integrations/zendesk/account-cases/123/comments",
                        json=SNAPSHOT,
                        headers={"X-N8n-Request-Token": "execution-token"},
                    )
                self.assertEqual(not_found.status_code, 404)


class CommentTriggerTest(unittest.TestCase):
    def _snapshot(self, **comment_overrides):
        comment = {
            "zendesk_comment_id": "c1",
            "author_kind": "customer",
            "author_id": "end-user-1",
            "is_public": True,
            "is_initial": False,
            "created_at": "2026-08-24T01:00:00Z",
            "body": "here is my account type",
        }
        comment.update(comment_overrides)
        return NS(comments=[NS(**comment)])

    def test_agent_and_initial_comments_are_ignored_without_claim(self):
        repository = _FakeRepository()
        run = lambda **kw: __import__("asyncio").run(
            reply_module.process_zendesk_comment_trigger(repository=repository, **kw)
        )
        self.assertEqual(
            run(account_case=repository.account_case, snapshot=self._snapshot(author_kind="agent"), trigger_comment_id="c1")["trigger_status"],
            "ignored_agent_comment",
        )
        self.assertEqual(
            run(account_case=repository.account_case, snapshot=self._snapshot(is_initial=True), trigger_comment_id="c1")["trigger_status"],
            "ignored_initial_comment",
        )
        self.assertEqual(repository.synced, [])

    def test_engineer_case_branch_records_customer_comment_event(self):
        repository = _FakeRepository()
        repository.account_case["automation_status"] = "not_automated"
        saved: list[dict] = []

        def fake_save_engineer_case(engineer_case, new_messages=None, slack_events=None):
            saved.append({"slack_events": slack_events})

        repository.get_active_engineer_case = lambda ticket_id, include_client_messages=True: {
            "engineer_case_id": "123-1",
            "active_investigation": {"id": "inv-1"},
        }
        repository.save_engineer_case = fake_save_engineer_case
        import asyncio

        result = asyncio.run(
            reply_module.process_zendesk_comment_trigger(
                repository=repository,
                account_case=repository.account_case,
                snapshot=self._snapshot(),
                trigger_comment_id="c1",
            )
        )
        self.assertEqual(result["trigger_status"], "processed_engineer_case")
        self.assertEqual(result["engineer_case_id"], "123-1")
        self.assertEqual(saved[0]["slack_events"][0]["event_type"], "zendesk_customer_comment")


if __name__ == "__main__":
    unittest.main()
