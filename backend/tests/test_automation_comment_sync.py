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

    def test_top_level_postgres_message_fields_count_as_already_asked(self):
        ticket = {
            "messages": [
                {
                    "role": "assistant",
                    "asked_field_keys": [" Account_Type ", "name"],
                    "meta": {"asked_field_keys": ["name", "office_address"]},
                }
            ]
        }

        self.assertEqual(
            reply_module._asked_field_keys(ticket),
            {"account_type", "name", "office_address"},
        )

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

    def test_engineer_case_branch_records_customer_context_and_only_notifies_slack(self):
        repository = _FakeRepository()
        repository.account_case["automation_status"] = "not_automated"
        saved: list[dict] = []

        def fake_save_engineer_case(engineer_case, new_messages=None, slack_events=None):
            saved.append(
                {
                    "engineer_case": engineer_case,
                    "new_messages": new_messages,
                    "slack_events": slack_events,
                }
            )

        repository.get_active_engineer_case = lambda ticket_id, include_client_messages=True: {
            "engineer_case_id": "123-1",
            "client_ticket_ref": {"ticket_id": "123"},
            "status": "investigating",
            "active_investigation": {
                "id": "inv-1",
                "state": "awaiting_final_approval",
                "draft_customer_reply": "stale draft",
                "messages": [],
            },
            "engineer_agent_state": {
                "conversation_version": 1,
                "draft_version": 4,
                "ready_to_reply": True,
                "final_approval_required": True,
            },
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
        self.assertEqual(result["trigger_status"], "processed_engineer_notification")
        self.assertEqual(result["engineer_case_id"], "123-1")
        self.assertEqual(result["conversation_version"], 2)
        self.assertEqual(result["draft_version"], 5)
        self.assertEqual(saved[0]["new_messages"][0]["role"], "customer")
        self.assertEqual(saved[0]["new_messages"][0]["content"], "here is my account type")
        self.assertEqual(saved[0]["engineer_case"]["draft_customer_reply"], "")
        self.assertNotIn("ready_to_reply", saved[0]["engineer_case"]["engineer_agent_state"])
        self.assertNotIn("final_approval_required", saved[0]["engineer_case"]["engineer_agent_state"])
        self.assertEqual(saved[0]["slack_events"][0]["event_type"], "zendesk_customer_comment")
        self.assertEqual(
            saved[0]["slack_events"][0]["message_text"],
            "Cx has added a new comment",
        )
        self.assertNotIn("account type", str(saved[0]["slack_events"][0]).lower())


    def test_failed_outcome_is_stored_failed_and_replays_after_handler_repair(self):
        # 13001 scenario: the suspension case's persisted handler was rewritten
        # to billing, the reply flow raised 409, and the failed outcome must be
        # stored replayable so the same comment re-runs after the repair.
        repository = _FakeRepository(
            {
                **_FakeRepository.DEFAULT_CASE,
                "execution_action": "account_suspension",
                "automation_handler": "billing",
            }
        )
        records: dict[str, dict] = {}

        def begin(scope, key, created_at=None, retry_failed=False):
            record = records.get(key)
            if record is None:
                records[key] = {"state": "processing", "payload": None}
                return {"created": True, "state": "processing"}
            if retry_failed and record["state"] == "failed":
                record.update(state="processing", payload=None)
                return {"created": True, "state": "processing"}
            return {
                "created": False,
                "state": record["state"],
                "response_payload": record["payload"],
            }

        def complete(scope, key, response_payload=None, updated_at=None):
            records[key] = {"state": "completed", "payload": response_payload}

        def fail(scope, key, response_payload=None, updated_at=None):
            records[key] = {"state": "failed", "payload": response_payload}

        repository.begin_idempotent_request = begin
        repository.complete_idempotent_request = complete
        repository.fail_idempotent_request = fail
        repository.get_active_engineer_case = lambda ticket_id, include_client_messages=True: None
        import asyncio

        with patch.object(
            reply_module,
            "process_account_customer_reply",
            new_callable=AsyncMock,
            side_effect=reply_module.ReplySyncError(
                409, "account case has no registered automation handler"
            ),
        ) as failed_process:
            first = asyncio.run(
                reply_module.process_zendesk_comment_trigger(
                    repository=repository,
                    account_case=repository.account_case,
                    snapshot=self._snapshot(),
                    trigger_comment_id="c1",
                )
            )
        self.assertEqual(first["trigger_status"], "failed")
        failed_process.assert_awaited_once()
        self.assertEqual(records["AC-123:c1"]["state"], "failed")

        repository.account_case["automation_handler"] = "account_suspension"
        with patch.object(
            reply_module,
            "process_account_customer_reply",
            new_callable=AsyncMock,
            return_value={
                "status": "processed",
                "internal_email_send_status": "sent",
                "ai_reply_status": "pending",
            },
        ) as replay_process:
            second = asyncio.run(
                reply_module.process_zendesk_comment_trigger(
                    repository=repository,
                    account_case=repository.account_case,
                    snapshot=self._snapshot(),
                    trigger_comment_id="c1",
                )
            )
        self.assertEqual(second["trigger_status"], "processed")
        replay_process.assert_awaited_once()
        self.assertEqual(records["AC-123:c1"]["state"], "completed")


if __name__ == "__main__":
    unittest.main()


class StatusSyncEndpointTest(unittest.TestCase):
    def _repo(self):
        repository = _FakeRepository()
        repository.updated: list[dict] = []
        repository.tickets = {"123": {"ticket_id": "123", "status": "open", "messages": []}}
        repository.active_engineer_case = {
            "engineer_case_id": "123-1",
            "active_investigation": {"id": "123-1-round-1", "state": "active"},
        }

        def update_status(**kwargs):
            repository.updated.append(kwargs)
            return {"status": "updated", "synced_at": "2026-08-24T00:00:00Z"}

        repository.update_account_case_zendesk_status = update_status
        repository.get_ticket = lambda ticket_id: repository.tickets.get(ticket_id)
        repository.get_active_engineer_case = lambda ticket_id, include_client_messages=True: (
            repository.active_engineer_case
        )
        saved_engineer: list[dict] = []
        repository.save_engineer_case = (
            lambda engineer_case, new_messages=None, slack_events=None: saved_engineer.append(
                {"case": engineer_case, "messages": new_messages, "events": slack_events}
            )
        )
        saved_tickets: list[dict] = []
        repository.save_ticket = lambda ticket, new_messages=None: saved_tickets.append(dict(ticket))
        repository.saved_engineer = saved_engineer
        repository.saved_tickets = saved_tickets
        return repository

    def test_status_endpoint_requires_token_and_validates_payload(self):
        with patch.dict(os.environ, ENV, clear=False):
            with TestClient(create_app()) as client:
                self.assertEqual(
                    client.put("/api/integrations/zendesk/account-cases/123/status", json={"zendesk_status": "solved"}).status_code,
                    401,
                )
                repository = self._repo()
                with patch(
                    "backend.automation_production_runtime._ticket_repository",
                    return_value=repository,
                ):
                    invalid = client.put(
                        "/api/integrations/zendesk/account-cases/123/status",
                        json={"zendesk_status": "bogus"},
                        headers={"X-N8n-Request-Token": "execution-token"},
                    )
                    self.assertEqual(invalid.status_code, 422)

    def test_solved_status_closes_engineer_case_and_resolves_ticket(self):
        with patch.dict(os.environ, ENV, clear=False):
            with TestClient(create_app()) as client:
                repository = self._repo()
                with patch(
                    "backend.automation_production_runtime._ticket_repository",
                    return_value=repository,
                ), patch(
                    "backend.services.automation_account_reply_sync.EngineerAssignmentService"
                ) as assignment:
                    response = client.put(
                        "/api/integrations/zendesk/account-cases/123/status",
                        json={"zendesk_status": "solved", "updated_at": "2026-08-24T02:00:00Z"},
                        headers={"X-N8n-Request-Token": "execution-token"},
                    )
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertTrue(body["is_account_case"])
                self.assertTrue(body["engineer_case_closed"])
                self.assertEqual(repository.updated[0]["zendesk_status"], "solved")
                self.assertEqual(repository.saved_engineer[0]["events"][0]["event_type"], "engineer_case_closed")
                self.assertEqual(repository.saved_tickets[0]["status"], "resolved")
                assignment.assert_called_once()
                assignment.return_value.resolve_case.assert_called_once_with("123-1", actor="zendesk_status_sync")

    def test_open_status_leaves_engineer_case_open(self):
        with patch.dict(os.environ, ENV, clear=False):
            with TestClient(create_app()) as client:
                repository = self._repo()
                with patch(
                    "backend.automation_production_runtime._ticket_repository",
                    return_value=repository,
                ):
                    response = client.put(
                        "/api/integrations/zendesk/account-cases/123/status",
                        json={"zendesk_status": "open"},
                        headers={"X-N8n-Request-Token": "execution-token"},
                    )
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()["engineer_case_closed"])
                self.assertEqual(repository.saved_engineer, [])


class EngineerSlackEndpointTest(unittest.TestCase):
    def _collab_repo(self):
        repository = _FakeRepository()
        repository.get_engineer_case = lambda case_id, include_client_messages=True: {
            "engineer_case_id": "123-1",
            "client_ticket_ref": {"ticket_id": "123"},
            "active_investigation": {"id": "123-1-round-1", "state": "active", "messages": []},
            "engineer_agent_state": {"conversation_version": 0, "draft_version": 0},
        }
        repository.get_ticket = lambda ticket_id: {
            "ticket_id": "123",
            "status": "open",
            "messages": [],
            "requester": "c@example.com",
            "customer_id": "c@example.com",
        }
        repository.fail_idempotent_request = lambda scope, key, response_payload=None, updated_at=None: None
        saved_cases: list[dict] = []
        repository.save_engineer_case = (
            lambda engineer_case, new_messages=None, slack_events=None, zendesk_delivery=None: saved_cases.append(
                {"case": engineer_case, "messages": new_messages, "events": slack_events, "delivery": zendesk_delivery}
            )
        )
        repository.save_ticket = lambda ticket, new_messages=None: None
        repository.saved_cases = saved_cases
        return repository

    def test_slack_endpoints_require_token_and_valid_payload(self):
        with patch.dict(os.environ, ENV, clear=False):
            with TestClient(create_app()) as client:
                self.assertEqual(
                    client.post(
                        "/api/integrations/slack/engineer-cases/messages", json={}
                    ).status_code,
                    401,
                )
                self.assertEqual(
                    client.post("/api/integrations/slack/engineer-cases/actions", json={}).status_code,
                    401,
                )
                repository = self._collab_repo()
                with patch(
                    "backend.automation_production_runtime._ticket_repository",
                    return_value=repository,
                ):
                    invalid = client.post(
                        "/api/integrations/slack/engineer-cases/messages",
                        json={"schema_version": 1, "event_id": "e1", "engineer_case_id": "123-1", "text": ""},
                        headers={"X-N8n-Request-Token": "execution-token"},
                    )
                    self.assertEqual(invalid.status_code, 422)

    def test_slack_message_runs_investigation_ai_round(self):
        with patch.dict(os.environ, ENV, clear=False):
            with TestClient(create_app()) as client:
                repository = self._collab_repo()
                with patch(
                    "backend.automation_production_runtime._ticket_repository",
                    return_value=repository,
                ), patch(
                    "backend.services.automation_engineer_collab.append_engineer_investigation_message",
                    return_value={
                        "active_investigation": {"id": "123-1-round-1", "draft_customer_reply": "draft reply"},
                        "new_internal_messages": [
                            {"role": "engineer_ai", "content": "analysis of the reply"}
                        ],
                    },
                ):
                    response = client.post(
                        "/api/integrations/slack/engineer-cases/messages",
                        json={
                            "schema_version": 1,
                            "event_id": "evt-1",
                            "engineer_case_id": "123-1",
                            "slack_user_id": "U1",
                            "text": "please investigate the quota mismatch",
                            "occurred_at": "2026-08-24T00:00:00Z",
                        },
                        headers={"X-N8n-Request-Token": "execution-token"},
                    )
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["status"], "processed")
                self.assertEqual(body["conversation_version"], 1)
                self.assertEqual(body["draft_version"], 1)
                self.assertEqual(repository.saved_cases[0]["events"][0]["event_type"], "engineer_ai_response")

    def test_thread_binding_requires_config(self):
        with patch.dict(os.environ, ENV, clear=False):
            with TestClient(create_app()) as client:
                with patch(
                    "backend.automation_production_runtime._ticket_repository",
                    return_value=self._collab_repo(),
                ), patch.dict(os.environ, {"ENGINEER_SLACK_TEAM_ID": "", "ENGINEER_SLACK_CHANNEL_ID": ""}, clear=False):
                    response = client.get(
                        "/api/integrations/slack/engineer-cases/thread-bindings/resolve",
                        params={"team_id": "T1", "channel_id": "C1", "thread_ts": "123.456"},
                        headers={"X-N8n-Request-Token": "execution-token"},
                    )
                self.assertEqual(response.status_code, 503)


def _outcome(response_status: str, **extra):
    payload = {
        "response_status": response_status,
        "route": "enablement",
        "automation_handler": "enablement",
        "execution_reason_code": None,
        "reply_job": None,
        "engineer_case_id": None,
        "internal_email_send_status": "not_applicable",
        "internal_email_send_reason": "",
        "route_status": "automated",
        "account_case": {},
    }
    payload.update(extra)
    return payload


class UsageCaptureAndPrepareTest(unittest.TestCase):
    def test_runtime_requests_route_without_preparation(self):
        from backend.services.automation_contracts import AutomationEnvironment, RouteResult

        route_result = RouteResult(
            request_id="req-noprep",
            idempotency_key="production:route:req-noprep",
            environment=AutomationEnvironment.PRODUCTION,
            case_id="AC-NOPREP",
            route={"execution_action": "enablement", "route_family": "automated"},
            automation={"eligible": True},
            action_plan={},
        )
        with patch.dict(os.environ, ENV, clear=False):
            with TestClient(create_app()) as client:
                with patch(
                    "backend.automation_production_runtime.call_route",
                    new_callable=AsyncMock,
                    return_value=route_result,
                ) as call, patch(
                    "backend.services.automation_account_intake.run_production_account_intake",
                    new_callable=AsyncMock,
                    return_value=_outcome("automation"),
                ), patch(
                    "backend.automation_production_runtime._ticket_repository",
                    return_value=object(),
                ):
                    response = client.post(
                        "/v1/cases",
                        json={"request_id": "req-noprep", "case_id": "AC-NOPREP", "zendesk_ticket_id": "123", "question": "hello"},
                        headers={"X-N8n-Request-Token": "execution-token"},
                    )
                self.assertEqual(response.status_code, 200, response.text)
                route_request = call.call_args.args[0]
                self.assertFalse(route_request.prepare)

    def test_reply_chain_flushes_captured_usage(self):
        import asyncio

        flushed: list[tuple] = []

        class _CaptureRepo(_FakeRepository):
            pass

        repository = _FakeRepository()
        repository.get_account_case = lambda case_id: {
            "account_case_id": "AC-123",
            "billing_ticket_id": "AC-123",
            "client_ticket_id": "123",
            "processing_profile": "production",
            "automation_status": "automation",
            "route_family": "automated",
            "execution_action": "fraud_account",
            "route_classification": {"handler_binding_status": "completed"},
            "collected_fields": {"account_type": "company"},
            "missing_fields": [],
            "automation_context": {},
            "created_at": "2026-08-23T00:00:00Z",
        }
        repository.get_ticket = lambda ticket_id: {"ticket_id": "123", "status": "open", "messages": [], "customer_id": "c@example.com"}
        repository.save_ticket = lambda ticket, new_messages=None: None
        repository.save_account_case = lambda case: None
        repository.save_account_route_execution = lambda execution: None
        not_routed = NS(
            decision=NS(
                execution_action="human_review_required",
                route="human_review_required",
                route_family="human_review",
                scope_label="account",
                reason="outside",
                confidence=0.9,
                matched_signals=[],
                semantic_intent=None,
                automation_eligibility=None,
                policy_decision=None,
                not_automated_reason="outside",
                risk_flags=[],
                evidence_spans=[],
                router_source="mock",
            ),
            classification={},
            prompt_snapshots={},
            stage_attempts=None,
        )
        with patch(
            "backend.services.automation_account_reply_sync.decide_account_route",
            return_value=not_routed,
        ), patch(
            "backend.services.account_admin.route_execution_from_decision",
            return_value={"ticket_id": "123"},
        ), patch(
            "backend.services.automation_account_reply_sync._apply_ownership_gate",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "backend.services.automation_account_reply_sync.should_run_reply_rag_fallback",
            return_value=False,
        ), patch(
            "backend.services.llm_usage_capture.begin_case_usage_capture",
            return_value=(NS(billing_ticket_id="AC-123", entries=[{"stage": "reply"}]), None),
        ) as begin, patch(
            "backend.services.llm_usage_capture.end_case_usage_capture",
        ) as end, patch(
            "backend.services.llm_usage_capture.flush_case_usage_capture",
            side_effect=lambda repo, capture: flushed.append((capture.billing_ticket_id, len(capture.entries))) or 1,
        ) as flush:
            outcome = asyncio.run(
                reply_module.process_account_customer_reply(
                    repository=repository,
                    billing_ticket_id="AC-123",
                    message="customer follow-up",
                    source="zendesk-comment",
                    message_source_id="c9",
                )
            )
        self.assertIn("messages", outcome)
        begin.assert_called_once_with(billing_ticket_id="AC-123")
        end.assert_called_once()
        flush.assert_called_once()
        self.assertEqual(flushed, [("AC-123", 1)])

    def test_split_reply_rag_failure_escalates_without_creating_reply_job(self):
        import asyncio

        repository = _FakeRepository()
        repository.get_account_case = lambda case_id: {
            "account_case_id": "AC-12992",
            "billing_ticket_id": "AC-12992",
            "client_ticket_id": "12992",
            "zendesk_ticket_id": "12992",
            "processing_profile": "production",
            "automation_status": "not_automated",
            "route": "rag",
            "execution_action": "rag",
            "automation_handler": None,
            "route_classification": {"superseded_automation_handler": None},
            "automation_context": {},
            "created_at": "2026-08-23T00:00:00Z",
        }
        repository.get_ticket = lambda ticket_id: {
            "ticket_id": "12992",
            "status": "open",
            "messages": [],
            "customer_id": "customer@example.com",
        }
        repository.save_ticket = lambda ticket, new_messages=None: None
        repository.save_account_case = lambda case: None
        repository.save_account_route_execution = lambda execution: None
        rag_route = NS(
            decision=NS(
                execution_action="rag",
                route="rag",
                route_family="rag_product_support",
                scope_label="support",
                reason="unexpected question",
                confidence=0.9,
                matched_signals=[],
                semantic_intent=None,
                automation_eligibility=None,
                policy_decision=None,
                not_automated_reason="unexpected question",
                risk_flags=[],
                evidence_spans=[],
                router_source="mock",
            ),
            classification={},
            prompt_snapshots={},
            stage_attempts=None,
        )
        with patch(
            "backend.services.automation_account_reply_sync.decide_account_route",
            return_value=rag_route,
        ), patch(
            "backend.services.account_admin.route_execution_from_decision",
            return_value={"ticket_id": "12992"},
        ), patch(
            "backend.services.automation_account_reply_sync._apply_ownership_gate",
            return_value=True,
        ), patch(
            "backend.services.automation_account_reply_sync.should_run_reply_rag_fallback",
            return_value=True,
        ), patch(
            "backend.services.automation_account_reply_sync.try_rag_fallback_answer",
            return_value=NS(
                kind="escalate",
                reason="ragflow_skill_authentication",
            ),
        ), patch(
            "backend.services.automation_account_reply_sync.escalate_unexpected_reply_to_human",
            return_value={
                "mode": "production",
                "internal_note_status": "sent",
                "route_back_status": "queued",
                "handoff_status": "queued",
            },
        ) as escalate, patch(
            "backend.services.automation_account_reply_sync._create_reply_job",
        ) as create_reply_job:
            outcome = asyncio.run(
                reply_module._process_account_customer_reply_impl(
                    repository=repository,
                    billing_ticket_id="AC-12992",
                    message="question outside the knowledge base",
                    source="zendesk-comment",
                    message_source_id="comment-12992",
                )
            )

        self.assertEqual(outcome["execution_action"], "rag")
        create_reply_job.assert_not_called()
        escalate.assert_called_once()
        escalation_call = escalate.call_args.kwargs
        self.assertEqual(escalation_call["ticket_id"], "12992")
        self.assertEqual(escalation_call["zendesk_ticket_id"], "12992")
        self.assertEqual(escalation_call["reason"], "ragflow_skill_authentication")
        self.assertEqual(escalation_call["account_case"]["automation_handler"], None)
