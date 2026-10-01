"""PP-EN-QUICK scenario tests (scripted engine + fake relay skill runner)."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

from backend.services.automation_test_scenarios import (
    AutomationTestScenarioError,
    ScenarioEngine,
)
from scripts.testing.preproduction import scenarios as pp


APP_ID = pp.PP_APP_ID

_SERVER_REQUEST = {
    "request_id": "enr-AC-13900-v1",
    "request_version": 1,
    "zendesk_ticket_id": "13900",
    "relay_task_id": "task-42",
    "status": "dispatched",
    "ticket_valid": True,
}


class FakeEngine(ScenarioEngine):
    """Scripted DB/send responses and zero-length polling (mirrors ScriptedEngine)."""

    def __init__(self) -> None:
        super().__init__(
            smtp_host="smtp.test",
            smtp_port=465,
            sender="xieziling97@163.com",
            smtp_password="pw",
            imap_host="imap.test",
            imap_port=993,
            db_dsn="postgresql://example.invalid/test",
            poll_interval_seconds=0,
        )
        self.db_queue: list[tuple[str, list[dict] | None]] = []
        self.sent_emails: list[dict] = []
        self.events: list[tuple[str, dict]] = []

    def db_query(self, sql, params):
        matcher, result = self.db_queue.pop(0)
        assert matcher in sql, f"unexpected query: {sql} (expected matcher {matcher})"
        return result

    def emit(self, kind, data):
        self.events.append((kind, data))
        super().emit(kind, data)

    def send_email(self, subject, body, to_address, headers=None):
        self.sent_emails.append({"subject": subject, "body": body, "to": to_address})

    def imap_find_notification(self, zendesk_ticket_id, since_date):  # pragma: no cover
        raise AssertionError("email customer turns are not used by PP scenarios")

    def sleep(self, seconds):
        return None

    def wait_for(self, description, probe, timeout_seconds):
        return super().wait_for(description, probe, min(timeout_seconds, 3))


def _request_row(status: str) -> dict:
    return {
        "request_id": "enr-AC-13900-v1",
        "status": status,
        "dispatch_status": "created" if status == "dispatched" else "not_created",
        "app_id": APP_ID,
        "request_version": 1,
        "customer_email": "xieziling97@163.com",
        "target_params": {"typeId": 6, "region": 2, "maxSubscribeLoad": 10},
        "relay_task_id": "task-42",
    }


def _fake_fetch(payload=None, error: Exception | None = None):
    calls: list = []

    def fetch(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if error is not None:
            raise error
        return payload

    return fetch, calls


def _identity() -> dict:
    return {
        "base_url": "https://relay.example.test/api",
        "agent_id": "zac-agent",
        "username": "zac",
        "token": "client-token",
    }


ECS_AGENT_ID = "supportportal-preproduction"


def _request_message(
    message_id: str,
    *,
    request_id: str = "enr-AC-13900-v1",
    version: int = 1,
    delivery_status: str = "delivered",
    zendesk_ticket_id: str = "13900",
    with_receiver: bool = True,
) -> dict:
    message = {
        "message_id": message_id,
        "from_agent_id": ECS_AGENT_ID,
        "delivery_status": delivery_status,
        "parts": [{
            "kind": "text",
            "text": json.dumps({
                "schema_version": "enablement-relay-request-v1",
                "request_id": request_id,
                "request_version": version,
                "ticket_id": zendesk_ticket_id,
                "zendesk_ticket_id": zendesk_ticket_id,
                "app_id": APP_ID,
            }),
        }],
    }
    if with_receiver:
        message["to_agent_id"] = "zac-agent"
    return message


def _relay_task_response(
    *,
    request_id: str = "enr-AC-13900-v1",
    with_task_version: bool = True,
    nested: bool = True,
    delivery_status: str = "delivered",
    to_agent_id: str = "zac-agent",
    task_status: str = "open",
    zendesk_ticket_id: str = "13900",
) -> dict:
    """Canonical GET /tasks/{id} response: ``messages`` is a SIBLING of
    ``task`` (agentrelay-task-context-sync unwrapTask merge), matching the
    repo's own relay test fixtures."""
    task = {
        "task_id": "task-42",
        "current_message_id": "m-1",
        "turn_sequence": 3,
        "max_turns": 12,
        "status": task_status,
        "to_agent_id": to_agent_id,
        "requester_agent_id": ECS_AGENT_ID,
    }
    if with_task_version:
        task["task_version"] = 5
    messages = [
        _request_message(
            "m-0", request_id=request_id, zendesk_ticket_id=zendesk_ticket_id
        ),
        _request_message(
            "m-1", request_id=request_id, zendesk_ticket_id=zendesk_ticket_id,
            delivery_status=delivery_status,
        ),
    ]
    if nested:
        return {"task": task, "messages": messages}
    return {**task, "messages": messages}


def _happy_queue(engine: FakeEngine, *, outcome: str, write: bool) -> None:
    engine.db_queue = [
        ("FROM support_account_cases", [
            {
                "account_case_id": "AC-13900",
                "client_ticket_id": "13900",
                "zendesk_ticket_id": "13900",
                "title": engine.tagged("Enable media relay for our project"),
            }
        ]),
        ("WHERE account_case_id", [{"execution_action": "enablement"}]),
        ("FROM support_account_reply_jobs", [{
            "status": "published",
            "reply_intent": "submission_confirmation",
            "close_after_publish": None,
        }]),
        ("FROM support_account_zendesk_comment_deliveries", [{
            "status": "delivered",
            "is_public": True,
            "zendesk_comment_id": "53830000000001",
        }]),
        ("FROM support_enablement_relay_requests", [_request_row("gated")]),
        ("FROM support_enablement_relay_requests", [_request_row("dispatched")]),
        # Same-AppID conflict table query: no other active requests.
        ("WHERE app_id = %s AND request_id", []),
        ("FROM support_enablement_relay_results", [{
            "outcome": outcome,
            "write_attempted": write,
            "request_status": "applied",
        }]),
        ("FROM support_account_reply_jobs", [{
            "status": "published",
            "reply_intent": "enablement_archer_enabled",
            "close_after_publish": True,
        }]),
        ("FROM support_account_reply_jobs", [{
            "status": "published",
            "reply_intent": "enablement_archer_enabled",
            "close_after_publish": True,
            "content": (
                "Thank you for your patience. Media Relay is now enabled for your project. "
                "We are closing this ticket now; feel free to open a new ticket for anything else."
            ),
        }]),
        ("FROM support_account_zendesk_comment_deliveries", [{
            "status": "delivered",
            "is_public": True,
            "zendesk_comment_id": "53830000000002",
        }]),
        ("WHERE account_case_id", [{"zendesk_ticket_status": "solved"}]),
    ]


def _fake_runner(precheck_rec: str, outcome: str, write: bool, calls: list):
    def runner(engine, ctx, request_row, **kwargs):
        calls.append({"request_row": dict(request_row), "kwargs": kwargs})
        return {
            "approval_method": "test_auto_approve",
            "precheck_recommendation": precheck_rec,
            "result": {
                "schema_version": "enablement-relay-result-v1",
                "outcome": outcome,
                "write_attempted": write,
                "detail": "",
            },
        }

    return runner


class PpEnQuickTests(unittest.TestCase):
    def test_happy_path_real_write_with_binding_and_reply(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        calls: list = []
        fetch, fetch_calls = _fake_fetch(dict(_SERVER_REQUEST))
        posts: list = []
        gets: list = []

        def post(url, *, method="GET", payload=None, token="", headers=None):
            posts.append({"url": url, "method": method, "payload": payload, "headers": headers, "token": token})
            return {"message_id": "m-9"}

        def get(url, **kwargs):
            gets.append(url)
            assert url.endswith("/tasks/task-42")
            return _relay_task_response()

        report = pp.run_pp_en_quick(
            engine,
            skill_runner=_fake_runner("execute", "enabled", True, calls),
            relay_base="https://preprod.example.test/automation/preproduction",
            relay_token="intake-token",
            relay_client_identity=_identity(),
            ecs_agent_id=ECS_AGENT_ID,
            fetch_json=fetch,
            post_json=post,
            get_json=get,
            workdir=self._workdir(),
        )
        self.assertTrue(engine.all_passed())
        self.assertEqual(report["relay_outcome"], "enabled")
        self.assertTrue(report["archer_write_attempted"])
        self.assertEqual(report["relay_task_id"], "task-42")
        # Binding verification: task current-message GET + server readback.
        self.assertEqual(len(fetch_calls), 1)
        self.assertIn("/v1/enablement-relay/requests/enr-AC-13900-v1", fetch_calls[0]["url"])
        self.assertEqual(fetch_calls[0]["token"], "intake-token")
        self.assertEqual(
            len(gets), 2,
            "one fetch inside the unified binding probe (polls until delivered), one for the reply",
        )
        # Result reply: real mutation contract — nested task envelope unwrapped,
        # current message id included, fencing from the fresh GET, and EXACTLY
        # the six server-allowed fields (protocol_v06 rejects unknown keys,
        # so "task_id" must not be sent even though the URL carries it).
        self.assertEqual(len(posts), 1)
        payload = posts[0]["payload"]
        self.assertEqual(
            set(payload),
            {"actor_agent_id", "message_id", "turn_sequence", "expected_task_version",
             "idempotency_key", "parts"},
        )
        self.assertEqual(payload["actor_agent_id"], "zac-agent")
        self.assertEqual(payload["message_id"], "m-1")
        self.assertEqual(payload["turn_sequence"], 3)
        self.assertEqual(payload["expected_task_version"], 5)
        self.assertEqual(payload["parts"][0]["kind"], "text")
        self.assertEqual(
            json.loads(payload["parts"][0]["text"])["schema_version"],
            "enablement-relay-result-v1",
        )
        self.assertEqual(posts[0]["headers"]["X-AgentRelay-Agent-Id"], "zac-agent")
        self.assertEqual(posts[0]["token"], "client-token")
        step_names = [s["step"] for s in report["steps"]]
        self.assertIn("inbox binding verified (server readback + same-AppID table)", step_names)
        self.assertIn("result replied to relay task (enablement-relay-result-v1)", step_names)

    def test_current_message_mismatch_stops_before_skill(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        engine.db_queue = engine.db_queue[:6]
        # The dispatched task's current message carries a DIFFERENT request.
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        calls: list = []

        def get(url, **kwargs):
            return _relay_task_response(request_id="enr-OTHER-v9")

        with self.assertRaises(AutomationTestScenarioError):
            pp.run_pp_en_quick(
                engine,
                skill_runner=_fake_runner("execute", "enabled", True, calls),
                relay_base="https://preprod.example.test/automation/preproduction",
                relay_token="intake-token",
                relay_client_identity=_identity(),
                ecs_agent_id=ECS_AGENT_ID,
                fetch_json=fetch,
                get_json=get,
                workdir=self._workdir(),
            )
        self.assertEqual(calls, [], "skill must never run on a current-message mismatch")

    def test_pending_message_waits_then_refuses_on_timeout(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        engine.db_queue = engine.db_queue[:6]
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        calls: list = []

        def get(url, **kwargs):
            return _relay_task_response(delivery_status="pending")

        with self.assertRaises(AutomationTestScenarioError) as ctx:
            pp.run_pp_en_quick(
                engine,
                skill_runner=_fake_runner("execute", "enabled", True, calls),
                relay_base="https://preprod.example.test/automation/preproduction",
                relay_token="intake-token",
                relay_client_identity=_identity(),
                ecs_agent_id=ECS_AGENT_ID,
                fetch_json=fetch,
                get_json=get,
                workdir=self._workdir(),
            )
        # A freshly dispatched message is legitimately pending: the scenario
        # WAITS for delivery (transient) and only refuses after the timeout.
        self.assertIn("never reached delivery_status=delivered", str(ctx.exception))
        self.assertEqual(calls, [], "no pilot write while the current message is undelivered")

    def test_pending_message_delivers_during_wait_and_proceeds(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, outcome="already_satisfied", write=False)
        calls: list = []
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        state = {"polls": 0}

        def get(url, **kwargs):
            state["polls"] += 1
            # First poll: still pending (dispatch beat). Then delivered.
            return _relay_task_response(
                delivery_status="delivered" if state["polls"] > 1 else "pending"
            )

        report = pp.run_pp_en_quick(
            engine,
            skill_runner=_fake_runner("already_satisfied", "already_satisfied", False, calls),
            relay_base="https://preprod.example.test/automation/preproduction",
            relay_token="intake-token",
            relay_client_identity=_identity(),
            ecs_agent_id=ECS_AGENT_ID,
            fetch_json=fetch,
            post_json=lambda *a, **k: {"ok": True},
            get_json=get,
            workdir=self._workdir(),
        )
        self.assertTrue(engine.all_passed())
        self.assertEqual(report["relay_outcome"], "already_satisfied")

    def test_malformed_fencing_between_legal_polls_fails_closed(self) -> None:
        """Acceptance round 7: legal pending → malformed fencing → legal
        delivered must END as broken with zero skill calls — the structural
        error must not be masked by later legal responses."""
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        engine.db_queue = engine.db_queue[:6]
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        calls: list = []
        polls = {"n": 0}

        def get(url, **kwargs):
            polls["n"] += 1
            if polls["n"] == 1:
                return _relay_task_response(delivery_status="pending")
            if polls["n"] == 2:
                malformed = _relay_task_response(delivery_status="pending")
                malformed["task"]["turn_sequence"] = "not-a-number"
                return malformed
            return _relay_task_response(delivery_status="delivered")

        with self.assertRaises(AutomationTestScenarioError) as ctx:
            pp.run_pp_en_quick(
                engine,
                skill_runner=_fake_runner("execute", "enabled", True, calls),
                relay_base="https://preprod.example.test/automation/preproduction",
                relay_token="intake-token",
                relay_client_identity=_identity(),
                ecs_agent_id=ECS_AGENT_ID,
                fetch_json=fetch,
                get_json=get,
                workdir=self._workdir(),
            )
        self.assertIn("missing fencing fields", str(ctx.exception))
        self.assertEqual(calls, [], "structural fencing error must not be masked")

    def test_malformed_request_version_between_legal_polls_fails_closed(self) -> None:
        """Acceptance round 8: legal pending → message with a malformed
        request_version → legal delivered must END as broken with zero skill
        calls — int() inside the binding check must not raise a swallowed
        ValueError."""
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        engine.db_queue = engine.db_queue[:6]
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        calls: list = []
        polls = {"n": 0}

        def get(url, **kwargs):
            polls["n"] += 1
            if polls["n"] == 1:
                return _relay_task_response(delivery_status="pending")
            if polls["n"] == 2:
                malformed = _relay_task_response(delivery_status="pending")
                # Swap in a current message whose request_version is a string.
                swapped = _request_message(
                    "m-2", request_id="enr-AC-13900-v1", zendesk_ticket_id="13900"
                )
                swapped["parts"] = [{
                    "kind": "text",
                    "text": json.dumps({
                        "schema_version": "enablement-relay-request-v1",
                        "request_id": "enr-AC-13900-v1",
                        "request_version": "not-an-int",
                        "ticket_id": "13900",
                        "zendesk_ticket_id": "13900",
                        "app_id": APP_ID,
                    }),
                }]
                malformed["task"]["current_message_id"] = "m-2"
                malformed["messages"].append(swapped)
                return malformed
            return _relay_task_response(delivery_status="delivered")

        with self.assertRaises(AutomationTestScenarioError) as ctx:
            pp.run_pp_en_quick(
                engine,
                skill_runner=_fake_runner("execute", "enabled", True, calls),
                relay_base="https://preprod.example.test/automation/preproduction",
                relay_token="intake-token",
                relay_client_identity=_identity(),
                ecs_agent_id=ECS_AGENT_ID,
                fetch_json=fetch,
                get_json=get,
                workdir=self._workdir(),
            )
        self.assertIn("malformed; binding unverifiable", str(ctx.exception))
        self.assertEqual(calls, [], "structural version error must not be masked")

    def test_fencing_rejects_non_integer_and_boolean_values(self) -> None:
        # 3.5 truncates to 3 under int(); True coerces to 1 — both must be
        # rejected as structural fencing errors (real skill _safe_int).
        for bad in (3.5, True):
            engine = FakeEngine()
            _happy_queue(engine, outcome="enabled", write=True)
            engine.db_queue = engine.db_queue[:6]
            fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
            calls: list = []

            def get(url, **kwargs):
                response = _relay_task_response(delivery_status="pending")
                response["task"]["turn_sequence"] = bad
                return response

            with self.assertRaises(AutomationTestScenarioError) as ctx:
                pp.run_pp_en_quick(
                    engine,
                    skill_runner=_fake_runner("execute", "enabled", True, calls),
                    relay_base="https://preprod.example.test/automation/preproduction",
                    relay_token="intake-token",
                    relay_client_identity=_identity(),
                    ecs_agent_id=ECS_AGENT_ID,
                    fetch_json=fetch,
                    get_json=get,
                    workdir=self._workdir(),
                )
            self.assertIn("missing fencing fields", str(ctx.exception))
            self.assertEqual(calls, [])

    def test_transport_failure_keeps_waiting_then_proceeds(self) -> None:
        """Transport-level read failures stay transient: wait, then proceed."""
        engine = FakeEngine()
        _happy_queue(engine, outcome="already_satisfied", write=False)
        calls: list = []
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        polls = {"n": 0}

        def get(url, **kwargs):
            polls["n"] += 1
            if polls["n"] == 1:
                raise OSError("temporary network outage")
            return _relay_task_response(delivery_status="delivered")

        report = pp.run_pp_en_quick(
            engine,
            skill_runner=_fake_runner("already_satisfied", "already_satisfied", False, calls),
            relay_base="https://preprod.example.test/automation/preproduction",
            relay_token="intake-token",
            relay_client_identity=_identity(),
            ecs_agent_id=ECS_AGENT_ID,
            fetch_json=fetch,
            post_json=lambda *a, **k: {"ok": True},
            get_json=get,
            workdir=self._workdir(),
        )
        self.assertTrue(engine.all_passed())
        self.assertEqual(report["relay_outcome"], "already_satisfied")

    def _stale_binding_run(self, second_response: dict, calls: list):
        """Drive the wait with a binding that CHANGES after the first poll."""
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        engine.db_queue = engine.db_queue[:6]
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        polls = {"n": 0}

        def get(url, **kwargs):
            polls["n"] += 1
            if polls["n"] == 1:
                return _relay_task_response(delivery_status="pending")
            return second_response

        with self.assertRaises(AutomationTestScenarioError) as ctx:
            pp.run_pp_en_quick(
                engine,
                skill_runner=_fake_runner("execute", "enabled", True, calls),
                relay_base="https://preprod.example.test/automation/preproduction",
                relay_token="intake-token",
                relay_client_identity=_identity(),
                ecs_agent_id=ECS_AGENT_ID,
                fetch_json=fetch,
                get_json=get,
                workdir=self._workdir(),
            )
        return ctx.exception

    def test_request_swapped_during_wait_refuses(self) -> None:
        calls: list = []
        # The current message becomes a NEW dispatch message carrying a
        # different request: the verified binding no longer holds.
        swapped = _relay_task_response(request_id="enr-OTHER-v2", delivery_status="delivered")
        swapped["task"]["current_message_id"] = "m-2"
        swapped["messages"].append(_request_message("m-2", request_id="enr-OTHER-v2"))
        error = self._stale_binding_run(swapped, calls)
        # The unified probe re-verifies the full message binding on every
        # poll: a swapped current message fails the request-identity check.
        self.assertIn("does not match this application", str(error))
        self.assertEqual(calls, [], "no pilot write on a swapped binding")

    def test_task_turned_terminal_during_wait_refuses(self) -> None:
        calls: list = []
        terminal = _relay_task_response(delivery_status="delivered", task_status="completed")
        error = self._stale_binding_run(terminal, calls)
        self.assertIn("became terminal", str(error))
        self.assertEqual(calls, [])

    def test_turn_transferred_during_wait_refuses(self) -> None:
        calls: list = []
        transferred = _relay_task_response(delivery_status="delivered", to_agent_id="someone-else")
        error = self._stale_binding_run(transferred, calls)
        self.assertIn("turn moved away", str(error))  # wrapped in "binding verification failed:"
        self.assertEqual(calls, [])

    def test_turn_not_ours_stops_before_pilot(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        engine.db_queue = engine.db_queue[:6]
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        calls: list = []

        def get(url, **kwargs):
            return _relay_task_response(to_agent_id="someone-else")

        with self.assertRaises(AutomationTestScenarioError) as ctx:
            pp.run_pp_en_quick(
                engine,
                skill_runner=_fake_runner("execute", "enabled", True, calls),
                relay_base="https://preprod.example.test/automation/preproduction",
                relay_token="intake-token",
                relay_client_identity=_identity(),
                ecs_agent_id=ECS_AGENT_ID,
                fetch_json=fetch,
                get_json=get,
                workdir=self._workdir(),
            )
        self.assertIn("turn moved away from this client", str(ctx.exception))
        self.assertEqual(calls, [])

    def test_message_ticket_mismatch_stops_before_skill(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        engine.db_queue = engine.db_queue[:6]
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        calls: list = []

        def get(url, **kwargs):
            # Same request id/version but bound to another Zendesk ticket.
            return _relay_task_response(zendesk_ticket_id="99999")

        with self.assertRaises(AutomationTestScenarioError) as ctx:
            pp.run_pp_en_quick(
                engine,
                skill_runner=_fake_runner("execute", "enabled", True, calls),
                relay_base="https://preprod.example.test/automation/preproduction",
                relay_token="intake-token",
                relay_client_identity=_identity(),
                ecs_agent_id=ECS_AGENT_ID,
                fetch_json=fetch,
                get_json=get,
                workdir=self._workdir(),
            )
        self.assertIn("zendesk_ticket_id", str(ctx.exception))
        self.assertEqual(calls, [])

    def test_receiver_missing_fails_closed(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        engine.db_queue = engine.db_queue[:6]
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        calls: list = []

        def get(url, **kwargs):
            task = _relay_task_response()
            task["messages"][1].pop("to_agent_id")
            return task

        with self.assertRaises(AutomationTestScenarioError) as ctx:
            pp.run_pp_en_quick(
                engine,
                skill_runner=_fake_runner("execute", "enabled", True, calls),
                relay_base="https://preprod.example.test/automation/preproduction",
                relay_token="intake-token",
                relay_client_identity=_identity(),
                ecs_agent_id=ECS_AGENT_ID,
                fetch_json=fetch,
                get_json=get,
                workdir=self._workdir(),
            )
        self.assertIn("receiver", str(ctx.exception))
        self.assertEqual(calls, [])

    def test_message_ticket_fields_missing_fails_closed(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        engine.db_queue = engine.db_queue[:6]
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        calls: list = []

        def get(url, **kwargs):
            task = _relay_task_response()
            # Strip both ticket fields from the current message: an
            # un-verifiable ticket binding must refuse, not pass.
            text = json.dumps({
                "schema_version": "enablement-relay-request-v1",
                "request_id": "enr-AC-13900-v1",
                "request_version": 1,
                "app_id": APP_ID,
            })
            task["messages"][1]["parts"] = [{"kind": "text", "text": text}]
            return task

        with self.assertRaises(AutomationTestScenarioError) as ctx:
            pp.run_pp_en_quick(
                engine,
                skill_runner=_fake_runner("execute", "enabled", True, calls),
                relay_base="https://preprod.example.test/automation/preproduction",
                relay_token="intake-token",
                relay_client_identity=_identity(),
                ecs_agent_id=ECS_AGENT_ID,
                fetch_json=fetch,
                get_json=get,
                workdir=self._workdir(),
            )
        self.assertIn("missing; ticket binding unverifiable", str(ctx.exception))
        self.assertEqual(calls, [])

    def test_ecs_identity_missing_fails_closed(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        engine.db_queue = engine.db_queue[:6]
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        calls: list = []

        with self.assertRaises(AutomationTestScenarioError) as ctx:
            pp.run_pp_en_quick(
                engine,
                skill_runner=_fake_runner("execute", "enabled", True, calls),
                relay_base="https://preprod.example.test/automation/preproduction",
                relay_token="intake-token",
                relay_client_identity=_identity(),
                ecs_agent_id="",  # unavailable ECS identity
                fetch_json=fetch,
                get_json=lambda *a, **k: _relay_task_response(),
                workdir=self._workdir(),
            )
        self.assertIn("sender cannot be verified", str(ctx.exception))
        self.assertEqual(calls, [])

    def test_missing_fencing_fails_closed(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        engine.db_queue = engine.db_queue[:6]
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        calls: list = []

        def get(url, **kwargs):
            return _relay_task_response(with_task_version=False)

        with self.assertRaises(AutomationTestScenarioError) as ctx:
            pp.run_pp_en_quick(
                engine,
                skill_runner=_fake_runner("execute", "enabled", True, calls),
                relay_base="https://preprod.example.test/automation/preproduction",
                relay_token="intake-token",
                relay_client_identity=_identity(),
                ecs_agent_id=ECS_AGENT_ID,
                fetch_json=fetch,
                get_json=get,
                workdir=self._workdir(),
            )
        self.assertIn("missing fencing", str(ctx.exception))
        self.assertEqual(calls, [])

    def test_listener_redacts_live_output(self) -> None:
        from scripts.testing.preproduction import __main__ as cli

        printed: list = []
        listener = cli._build_listener(printed.append, app_id=APP_ID, email="xieziling97@163.com")
        # A shared-engine record() emits the step detail with the full App ID.
        listener("step", {
            "step": "relay request created",
            "status": "PASS",
            "detail": f"request=enr-1 status=gated app_id={APP_ID} v1",
        })
        listener("info", {"message": f"linked case for xieziling97@163.com app {APP_ID}"})
        rendered = "\n".join(printed)
        self.assertNotIn(APP_ID, rendered)
        self.assertNotIn("xieziling97@163.com", rendered)
        self.assertIn(pp.redact_app_id(APP_ID), rendered)

    def test_already_satisfied_zero_writes(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, outcome="already_satisfied", write=False)
        calls: list = []
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        report = pp.run_pp_en_quick(
            engine,
            skill_runner=_fake_runner("already_satisfied", "already_satisfied", False, calls),
            relay_base="https://preprod.example.test/automation/preproduction",
            relay_token="intake-token",
            relay_client_identity=_identity(),
            ecs_agent_id=ECS_AGENT_ID,
            fetch_json=fetch,
            post_json=lambda *a, **k: {"ok": True},
            get_json=lambda *a, **k: _relay_task_response(),
            workdir=self._workdir(),
        )
        self.assertTrue(engine.all_passed())
        self.assertFalse(report["archer_write_attempted"])
        self.assertEqual(report["relay_outcome"], "already_satisfied")

    def test_binding_mismatch_stops_before_skill(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        engine.db_queue = engine.db_queue[:6]
        server = dict(_SERVER_REQUEST)
        server["zendesk_ticket_id"] = "99999"  # bound to a different ticket
        fetch, _ = _fake_fetch(server)
        calls: list = []
        with self.assertRaises(AutomationTestScenarioError):
            pp.run_pp_en_quick(
                engine,
                skill_runner=_fake_runner("execute", "enabled", True, calls),
                relay_base="https://preprod.example.test/automation/preproduction",
                relay_token="intake-token",
                relay_client_identity=_identity(),
                ecs_agent_id=ECS_AGENT_ID,
                fetch_json=fetch,
                get_json=lambda *a, **k: _relay_task_response(),
                workdir=self._workdir(),
            )
        self.assertEqual(calls, [], "skill must never run on a binding mismatch")
        self.assertNotIn(
            "relay auto-approval executed (test_auto_approve, real pilot leg)",
            [s.step for s in engine.steps],
        )

    def test_same_appid_conflict_pauses(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, outcome="enabled", write=True)
        # Same-AppID query returns another active request instead of [].
        engine.db_queue[6] = (
            "WHERE app_id = %s AND request_id",
            [{"request_id": "enr-OTHER", "status": "dispatched", "zendesk_ticket_id": "13950"}],
        )
        fetch, _ = _fake_fetch(dict(_SERVER_REQUEST))
        calls: list = []
        with self.assertRaises(AutomationTestScenarioError) as ctx:
            pp.run_pp_en_quick(
                engine,
                skill_runner=_fake_runner("execute", "enabled", True, calls),
                relay_base="https://preprod.example.test/automation/preproduction",
                relay_token="intake-token",
                relay_client_identity=_identity(),
                ecs_agent_id=ECS_AGENT_ID,
                fetch_json=fetch,
                get_json=lambda *a, **k: _relay_task_response(),
                workdir=self._workdir(),
            )
        self.assertIn("same-AppID conflict", str(ctx.exception))
        self.assertEqual(calls, [])

    def test_skill_runner_passes_file_ref_approval(self) -> None:
        workdir = self._workdir()
        request_row = _request_row("dispatched")
        captured: list[list[str]] = []

        def fake_run(args, env):
            captured.append(args)
            if "precheck" in args:
                return {"recommendation": "execute", "report_digest": "sha256:abc"}
            return {"outcome": "enabled", "write_attempted": True}

        with patch.object(pp, "_run_skill_subprocess", side_effect=fake_run):
            result = pp.default_skill_runner(
                None,
                None,
                request_row,
                skill_script=self._skill_stub(),
                pilot_bin="pilot",
                relay_base="https://preprod.example.test",
                relay_token="intake-token",
                workdir=workdir,
            )
        self.assertEqual(result["result"]["outcome"], "enabled")
        execute_args = captured[1]
        self.assertIn("--approval-ref", execute_args)
        self.assertTrue(execute_args[execute_args.index("--approval-ref") + 1].startswith("@"))
        self.assertIn("--relay-task-id", execute_args)
        self.assertEqual(
            execute_args[execute_args.index("--relay-task-id") + 1], "task-42"
        )

    def test_request_file_shape(self) -> None:
        workdir = self._workdir()
        request_file = pp.build_relay_request_file(
            _request_row("dispatched"), workdir, zendesk_ticket_id="13900"
        )
        payload = json.loads(request_file.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "enablement-relay-request-v1")
        self.assertEqual(payload["app_id"], APP_ID)
        self.assertEqual(payload["zendesk_ticket_id"], "13900")
        self.assertEqual(payload["target_params"], {"typeId": 6, "region": 2, "maxSubscribeLoad": 10})

    def test_request_row_zendesk_ticket_id_wins_over_fallback(self) -> None:
        workdir = self._workdir()
        row = _request_row("dispatched")
        row["zendesk_ticket_id"] = "13900"
        request_file = pp.build_relay_request_file(row, workdir, zendesk_ticket_id="ignored")
        payload = json.loads(request_file.read_text(encoding="utf-8"))
        self.assertEqual(payload["zendesk_ticket_id"], "13900")

    def test_redact_report_covers_steps(self) -> None:
        report = {
            "steps": [
                {"step": "relay request", "detail": f"app_id={APP_ID} for xieziling97@163.com"},
            ],
            "aborted": f"boom {APP_ID}",
        }
        redacted = pp.redact_report(report, app_id=APP_ID, email="xieziling97@163.com")
        serialized = json.dumps(redacted)
        self.assertNotIn(APP_ID, serialized)
        self.assertNotIn("xieziling97@163.com", serialized)

    def test_redaction_helpers(self) -> None:
        self.assertNotIn(APP_ID, pp.redact_app_id(APP_ID))
        self.assertNotIn("xieziling97", pp.redact_email("xieziling97@163.com"))

    def test_cli_forces_preprod_db_env(self) -> None:
        from scripts.testing.preproduction import __main__ as cli

        env = {
            "TICKET_DB_SCHEMA": "supportportal",
            "AUTOMATION_TEST_PROCESSING_PROFILE": "production",
            "AUTOMATION_TEST_DB_DSN": "postgresql://legacy/production",
        }
        with patch.dict(os.environ, env), patch.object(
            cli, "_ssm_value", return_value="postgresql://preprod/db"
        ) as ssm:
            cli._ensure_preprod_db_env()
            self.assertEqual(os.environ["TICKET_DB_SCHEMA"], "supportportal_preproduction")
            self.assertEqual(os.environ["AUTOMATION_TEST_PROCESSING_PROFILE"], "preproduction")
            self.assertEqual(os.environ["AUTOMATION_TEST_DB_DSN"], "postgresql://preprod/db")
        ssm.assert_called_once_with("/supportportal/preproduction/automation-db-dsn")

    def test_ssm_value_strips_env_aws_credentials(self) -> None:
        """The .env static keys (zac-support) must not hijack aws CLI
        subprocesses: environment credentials outrank the default SSO
        profile, so _ssm_value strips them and falls back to user/Zac."""
        from types import SimpleNamespace

        from scripts.testing.preproduction import __main__ as cli

        env = {
            "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_SESSION_TOKEN": "token",
            "AWS_REGION": "us-east-1",
        }
        captured: list = []

        def fake_run(args, **kwargs):
            captured.append(kwargs.get("env"))
            return SimpleNamespace(returncode=0, stdout="postgresql://preprod/db\n", stderr="")

        with patch.dict(os.environ, env), patch.object(
            cli.subprocess, "run", side_effect=fake_run
        ):
            value = cli._ssm_value("/supportportal/preproduction/automation-db-dsn")
        self.assertEqual(value, "postgresql://preprod/db")
        child_env = captured[0]
        self.assertNotIn("AWS_ACCESS_KEY_ID", child_env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", child_env)
        self.assertNotIn("AWS_SESSION_TOKEN", child_env)
        self.assertEqual(child_env.get("AWS_REGION"), "us-east-1")

    def test_relay_env_uses_ecs_api_base_and_intake_token(self) -> None:
        """SKILL.md contract: SUPPORTPORTAL_RELAY_API_BASE is the SupportPortal
        API base and the token is the intake bearer — NOT the AgentRelay server
        (that identity lives in the client env file)."""
        from scripts.testing.preproduction import __main__ as cli

        with patch.dict(os.environ, {}, clear=False), patch.object(
            cli,
            "_ssm_value",
            return_value="intake-token-value",
        ) as ssm:
            os.environ.pop("SUPPORTPORTAL_RELAY_API_BASE", None)
            os.environ.pop("SUPPORTPORTAL_RELAY_TOKEN", None)
            base, token = cli._ensure_relay_env()
        self.assertEqual(base, cli.PREPROD_API_BASE)
        self.assertIn("/automation/preproduction", base)
        self.assertEqual(token, "intake-token-value")
        ssm.assert_called_once_with("/supportportal/preproduction/automation-intake-shared-token")
        os.environ.pop("SUPPORTPORTAL_RELAY_API_BASE", None)
        os.environ.pop("SUPPORTPORTAL_RELAY_TOKEN", None)

    def _skill_stub(self):
        import tempfile
        from pathlib import Path

        d = Path(tempfile.mkdtemp(prefix="pp-skill-"))
        script = d / "relay_enablement.py"
        script.write_text("# stub\n")
        return script

    def _workdir(self):
        import tempfile
        from pathlib import Path

        return Path(tempfile.mkdtemp(prefix="pp-test-"))


if __name__ == "__main__":
    unittest.main()
