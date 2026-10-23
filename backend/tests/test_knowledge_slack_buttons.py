"""Knowledge review Approve/Reject button contract tests (stage6-csd-10).

Covers the button click path end to end at the handler level: dispatch
bypasses the draft flow's environment/ticket/binding checks, the operator
is the server-verified Slack identity, superseded generations are refused
with zero state change, reject parks terminally, approve submits a
new-decision resolution built from the candidate, and repeated clicks look
like success. All fixtures are synthetic; no real Slack traffic.
"""

from __future__ import annotations

import os
import threading
import typing
from typing import Any

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

import pytest  # noqa: E402

from backend.repositories.knowledge_delivery_repository import (  # noqa: E402
    InMemoryKnowledgeDeliveryRepositoryMixin,
)
from backend.repositories.weknora_promotion_repository import (  # noqa: E402
    InMemoryWeKnoraPromotionRepositoryMixin,
)
from backend.services.automation_hermes_slack_actions import (  # noqa: E402
    handle_slack_hermes_action,
)

NOW = "2026-10-11T08:00:00+00:00"

ENV = {
    "ENGINEER_SLACK_CHANNEL_ID": "C-REVIEW",
    "ENGINEER_SLACK_TEAM_ID": "T-1",
    "ENGINEER_SLACK_BOT_USER_ID": "U-BOT-1",
    "ENGINEER_SLACK_ACCESS_TOKEN": "xoxb-test",
    "HERMES_KNOWLEDGE_WORKFLOW_ENABLED": "1",
    "KNOWLEDGE_SLACK_NOTIFY_ENABLED": "1",
}


class _Host(InMemoryKnowledgeDeliveryRepositoryMixin, InMemoryWeKnoraPromotionRepositoryMixin):
    def __init__(self) -> None:
        self._assignment_lock = threading.RLock()


class _StoreStub:
    def get_standalone_summary_task(self, task_id):
        return None

    def latest_knowledge_source_version(self, source_type, source_id):
        return None


def _promotion(**overrides: Any) -> dict:
    payload = {
        "schema_version": "v1",
        "candidate_type": "knowledge",
        "decision": "human_review",
        "title": "Region EU join failures",
        "content": "proposed body",
        "note": "no similar entry found",
    }
    task = {
        "source_type": "knowledge_source_review",
        "source_id": "knowledge-source-summary:csd_issue:ISS-1:v2:kb-1",
        "source_version": "v2",
        "content_hash": "c" * 64,
        "candidate_type": "knowledge",
        "decision": "human_review",
        "candidate_payload": payload,
        "review_run_id": "run-1",
        "summary_session_id": "s-1",
        "review_session_id": "r-1",
    }
    task.update(overrides)
    return task


def _parked(host: _Host, **overrides: Any) -> str:
    rows = host.enqueue_weknora_promotions([_promotion(**overrides)], now_value=NOW)
    promotion_id = str(rows[0]["promotion_id"])
    host.park_knowledge_candidate(promotion_id, reasons=["gate"], now_value=NOW)
    return promotion_id


def _row(host: _Host, promotion_id: str) -> dict:
    return next(
        row for row in host.list_weknora_promotions()
        if row["promotion_id"] == promotion_id
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def _click(
    monkeypatch,
    host: _Host,
    action: str,
    *,
    promotion_id: str = "",
    slack_user_id: str = "U-9",
    operator_error: str = "",
    no_email: bool = False,
):
    import unittest.mock as mock

    import backend.services.engineer_slack as slack_module

    def _resolve(uid, bot_user_id=None, require_email=True):
        if operator_error:
            from backend.services.engineer_slack import SlackOperatorResolutionError

            raise SlackOperatorResolutionError(operator_error, "operator lookup failed")
        return {
            "slack_user_id": uid,
            "email": None if no_email else "engineer@example.com",
            "display_name": "E",
        }

    payload = {
        "interaction_id": "T-1:C-REVIEW:1700.001:" + action + ":1700.9",
        "action": action,
        "value": promotion_id,
        "slack_user_id": slack_user_id,
    }
    with mock.patch.object(slack_module, "resolve_slack_operator", _resolve):
        return handle_slack_hermes_action(
            _StoreStub(), host, payload, expected_environment="preproduction"
        )


def _deliveries(host: _Host, promotion_id: str) -> list[dict]:
    rows = host.list_weknora_deliveries() if hasattr(host, "list_weknora_deliveries") else []
    return [r for r in rows or [] if str(r.get("promotion_id") or "") == promotion_id]


# ----------------------------------------------------------------- dispatch


def test_button_dispatch_bypasses_environment_and_ticket_checks(monkeypatch) -> None:
    """A root review click carries no environment or zendesk identity."""
    host = _Host()
    promotion_id = _parked(host)

    result = _click(monkeypatch, host, "knowledge_reject", promotion_id=promotion_id)

    assert result["ok"] is True, result
    assert result["status"] == "knowledge_review_rejected"


def test_button_without_promotion_id_refused(monkeypatch) -> None:
    host = _Host()
    result = _click(monkeypatch, host, "knowledge_reject", promotion_id="")
    assert result["ok"] is False
    assert result["status_code"] == 422


def test_button_unknown_promotion_returns_404(monkeypatch) -> None:
    host = _Host()
    result = _click(monkeypatch, host, "knowledge_reject", promotion_id="kp-missing")
    assert result["ok"] is False
    assert result["status_code"] == 404


# ------------------------------------------------------------------ reject


def test_button_reject_parks_terminally_with_verified_operator(monkeypatch) -> None:
    host = _Host()
    promotion_id = _parked(host)

    result = _click(monkeypatch, host, "knowledge_reject", promotion_id=promotion_id)

    assert result["ok"] is True, result
    assert result["operator_email"] == "engineer@example.com"
    row = _row(host, promotion_id)
    assert row["status"] == "rejected"
    assert row["human_decision"] == "rejected"
    assert row["human_decided_by_email"] == "engineer@example.com"
    assert row["human_decided_slack_user_id"] == "U-9"


def test_button_repeat_click_after_reject_is_success_already(monkeypatch) -> None:
    host = _Host()
    promotion_id = _parked(host)

    first = _click(monkeypatch, host, "knowledge_reject", promotion_id=promotion_id)
    second = _click(monkeypatch, host, "knowledge_reject", promotion_id=promotion_id)

    assert first["ok"] is True
    assert second["ok"] is True, "a repeat click must look like success to the relay"
    assert second.get("already") == "knowledge_decision"


# ----------------------------------------------------------------- approve


def test_button_approve_submits_new_resolution_from_candidate(monkeypatch) -> None:
    host = _Host()
    promotion_id = _parked(host)
    captured: dict[str, Any] = {}
    real_decide = host.decide_weknora_promotion

    def _capture(pid, **kwargs):
        captured.update(kwargs, promotion_id=pid)
        return real_decide(pid, **kwargs)

    monkeypatch.setattr(host, "decide_weknora_promotion", _capture)

    result = _click(monkeypatch, host, "knowledge_approve", promotion_id=promotion_id)

    assert result["ok"] is True, result
    assert result["status"] == "knowledge_review_approved"
    assert captured["decision"] == "approve"
    assert captured["operator_email"] == "engineer@example.com"
    resolution = captured["resolution"]
    assert resolution["action"] == "new"
    assert resolution["content"] == "proposed body"
    assert resolution["title"] == "Region EU join failures"
    row = _row(host, promotion_id)
    assert row["status"] == "queued", "approve re-enters the write contract"
    assert row["human_decision"] == "approved"


def test_button_approve_without_candidate_content_refused(monkeypatch) -> None:
    host = _Host()
    promotion_id = _parked(
        host, candidate_payload={"title": "t", "content": "   "}
    )

    result = _click(monkeypatch, host, "knowledge_approve", promotion_id=promotion_id)

    assert result["ok"] is False
    assert result["status_code"] == 422
    assert _row(host, promotion_id)["status"] == "human_review", "zero state change"


# ------------------------------------------------------------ fail-closed


def test_button_operator_without_visible_email_still_decides(monkeypatch) -> None:
    """Scope gap fallback: a users.info-verified user without a visible
    email decides; the audit records the Slack identity, email stays NULL."""
    host = _Host()
    promotion_id = _parked(host)

    result = _click(
        monkeypatch, host, "knowledge_reject",
        promotion_id=promotion_id, no_email=True,
    )

    assert result["ok"] is True, result
    assert result["operator"] == "slack:U-9"
    row = _row(host, promotion_id)
    assert row["status"] == "rejected"
    assert row["human_decided_by_email"] is None, "no string-'None' leakage"
    assert row["human_decided_slack_user_id"] == "U-9"


def test_button_operator_without_visible_email_approves(monkeypatch) -> None:
    host = _Host()
    promotion_id = _parked(host)
    captured: dict = {}
    real_decide = host.decide_weknora_promotion

    def _capture(pid, **kwargs):
        captured.update(kwargs)
        return real_decide(pid, **kwargs)

    monkeypatch.setattr(host, "decide_weknora_promotion", _capture)
    result = _click(
        monkeypatch, host, "knowledge_approve",
        promotion_id=promotion_id, no_email=True,
    )
    assert result["ok"] is True, result
    assert captured["decided_by"] == "slack:U-9"
    assert captured["operator_email"] is None
    assert _row(host, promotion_id)["status"] == "queued"


def test_button_unverified_operator_refused_with_zero_state_change(monkeypatch) -> None:
    host = _Host()
    promotion_id = _parked(host)

    result = _click(
        monkeypatch, host, "knowledge_reject",
        promotion_id=promotion_id, operator_error="users_info_failed",
    )

    assert result["ok"] is False
    assert result["status_code"] == 403
    row = _row(host, promotion_id)
    assert row["status"] == "human_review", "zero state change"


def test_button_superseded_generation_refused(monkeypatch) -> None:
    host = _Host()
    promotion_id = _parked(host)

    import backend.services.hermes_knowledge_workflow as workflow

    monkeypatch.setattr(
        workflow, "weknora_promotion_generation_current",
        lambda repository, promotion, native_state_store=None: (
            False, "source version advanced"
        ),
    )

    result = _click(monkeypatch, host, "knowledge_approve", promotion_id=promotion_id)

    assert result["ok"] is False
    assert result["status_code"] == 409
    assert _row(host, promotion_id)["status"] == "human_review", "zero state change"


def test_button_reject_leaves_zero_weknora_deliveries(monkeypatch) -> None:
    host = _Host()
    promotion_id = _parked(host)

    _click(monkeypatch, host, "knowledge_reject", promotion_id=promotion_id)

    rows = typing.cast(list[dict], host.list_weknora_promotions())
    decided = next(r for r in rows if r["promotion_id"] == promotion_id)
    deliveries = host.list_delivery_rows(decided) if hasattr(host, "list_delivery_rows") else []
    assert all(
        str(d.get("status") or "") in {"invalidated", "retired", "cancelled", "failed"}
        or not str(d.get("status") or "")
        for d in deliveries or []
    ), "reject retires every non-terminal delivery"


def test_draft_flow_environment_check_still_enforced(monkeypatch) -> None:
    """The early knowledge dispatch must not weaken the draft flow checks."""
    payload = {
        "interaction_id": "T-1:C-REVIEW:1700.001:approve_draft:1700.9",
        "action": "approve_draft",
        "environment": "",
        "zendesk_ticket_id": "",
    }
    result = handle_slack_hermes_action(
        _StoreStub(), None, payload, expected_environment="preproduction"
    )
    assert result["ok"] is False
    assert "environment mismatch" in result["detail"]


# --------------------------------------------------------------- delivery


class _RecordingPoster:
    def __init__(self) -> None:
        self.posts = []

    def post_engineer_slack_event(self, event, thread_ts=None):
        self.posts.append({"event": dict(event), "thread_ts": thread_ts})
        return {
            "event_id": event.get("event_id"),
            "status": "delivered",
            "failure_code": None,
            "slack_channel_id": "C-REVIEW",
            "slack_message_ts": "1700.001",
            "slack_thread_ts": str(thread_ts or "") or "1700.001",
        }


def test_root_delivery_carries_buttons_and_change_line() -> None:
    from backend.services.engineer_slack import _action_blocks, _message_payload
    from backend.services.knowledge_slack_review import (
        deliver_knowledge_review_notification,
    )

    host = _Host()
    promotion_id = _parked(host)
    poster = _RecordingPoster()

    result = deliver_knowledge_review_notification(
        host, _row(host, promotion_id), slack_client=poster, now_value=NOW
    )

    assert result["status"] == "delivered"
    posted = poster.posts[0]
    event = posted["event"]
    assert event["action"] == "knowledge_review"
    assert event["knowledge_promotion_id"] == promotion_id
    assert "*Change:* new article" in event["message_text"]
    assert "Promotion ID" not in event["message_text"]
    assert "Source:" not in event["message_text"]
    # C1 root: the real payload builder must NOT attach a thread anchor.
    payload = _message_payload(event, thread_ts=posted["thread_ts"])
    assert "thread_ts" not in payload
    blocks = _action_blocks(event)
    assert blocks and blocks[0]["type"] == "actions"
    ids = [b["action_id"] for b in blocks[0]["elements"]]
    assert ids == ["knowledge_approve", "knowledge_reject"]
    assert all(b["value"] == promotion_id for b in blocks[0]["elements"])


def test_thread_redelivery_also_carries_buttons() -> None:
    """A re-delivery replying inside the bound thread keeps the buttons."""
    from backend.services.engineer_slack import _action_blocks
    from backend.services.knowledge_slack_review import (
        deliver_knowledge_review_notification,
    )

    host = _Host()
    promotion_id = _parked(host)
    first = _RecordingPoster()
    deliver_knowledge_review_notification(
        host, _row(host, promotion_id), slack_client=first, now_value=NOW
    )
    assert first.posts[0]["thread_ts"] is None, "first send roots a thread"

    # Redelivery window: operator maintenance (the preprod SQL reset) moves
    # a delivered row back to a reclaimable expired 'queued' claim while
    # KEEPING the thread binding and the original message ts — the worker's
    # crash-recovery path then re-sends as a thread reply.
    with host._assignment_lock:
        state_row = host._weknora_promotion_state()[promotion_id]
        state_row.update(
            slack_review_status="queued",
            slack_review_owner_token=None,
            slack_review_claimed_at="2026-10-11T07:00:00+00:00",
        )
    redeliver_poster = _RecordingPoster()
    result = deliver_knowledge_review_notification(
        host, _row(host, promotion_id), slack_client=redeliver_poster, now_value=NOW,
    )

    assert result["status"] == "delivered"
    posted = redeliver_poster.posts[0]
    assert posted["thread_ts"] == "1700.001", "re-delivery replies in the bound thread"
    assert posted["event"]["event_id"].endswith(":resend:2026-10-11T07:00:00+00:00"), (
        "a re-delivery after a successful post uses a per-round id keyed by "
        "the delivery claim (Slack's client_msg_id suppression is long-lived "
        "and swallows a reused id as a silent no-op)"
    )
    event = posted["event"]
    assert event["action"] == "knowledge_review"
    assert event["knowledge_promotion_id"] == promotion_id
    # C2 thread reply: the REAL payload builder must keep the thread anchor —
    # root event types may not silently spill an anchored reply into the
    # channel root (the fake poster only records the argument, so prove the
    # payload here).
    from backend.services.engineer_slack import _action_blocks, _message_payload

    payload = _message_payload(event, thread_ts=posted["thread_ts"])
    assert payload.get("thread_ts") == "1700.001"
    blocks = _action_blocks(event)
    assert blocks and blocks[0]["type"] == "actions"
    assert [b["action_id"] for b in blocks[0]["elements"]] == [
        "knowledge_approve", "knowledge_reject",
    ]
