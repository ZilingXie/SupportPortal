"""Stage-4 Slack review contract tests (p2-195).

Covers: source-only root threads (C1), case-bound thread replies (C2),
idempotent retries (C3), verified operator identity (C4), mention + thread
binding + disambiguation (C5/C6/C7), decisions (C8/C9), and the master
switch (C10). All fixtures are synthetic; no real Slack traffic.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

import pytest  # noqa: E402

from backend.repositories.knowledge_delivery_repository import (  # noqa: E402
    InMemoryKnowledgeDeliveryRepositoryMixin,
)
from backend.repositories.weknora_promotion_repository import (  # noqa: E402
    InMemoryWeKnoraPromotionRepositoryMixin,
)
from backend.services.engineer_slack import (  # noqa: E402
    SlackOperatorResolutionError,
    build_knowledge_review_root_event,
    resolve_slack_operator,
)
from backend.services.knowledge_slack_review import (  # noqa: E402
    deliver_knowledge_review_notification,
    knowledge_review_event_id,
)

NOW = "2026-10-11T08:00:00+00:00"
CHANNEL = "C-REVIEW"
BOT = "U-BOT-1"

ENV = {
    "ENGINEER_SLACK_CHANNEL_ID": CHANNEL,
    "ENGINEER_SLACK_TEAM_ID": "T-1",
    "ENGINEER_SLACK_BOT_USER_ID": BOT,
    "ENGINEER_SLACK_ACCESS_TOKEN": "xoxb-test",
    "HERMES_KNOWLEDGE_WORKFLOW_ENABLED": "1",
    "KNOWLEDGE_SLACK_NOTIFY_ENABLED": "1",
}


class _Host(InMemoryKnowledgeDeliveryRepositoryMixin, InMemoryWeKnoraPromotionRepositoryMixin):
    def __init__(self) -> None:
        self._assignment_lock = threading.RLock()


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


class _FakeSlackPoster:
    """Records posts; scriptable outcomes."""

    def __init__(self, *, outcome="ok", thread_ts_root="1700.001"):
        self.posts = []
        self.outcome = outcome
        self.thread_ts_root = thread_ts_root

    def post_engineer_slack_event(self, event, thread_ts=None):
        self.posts.append({"event": dict(event), "thread_ts": thread_ts})
        if self.outcome == "ok":
            return {
                "event_id": event.get("event_id"),
                "status": "delivered",
                "failure_code": None,
                "slack_channel_id": CHANNEL,
                "slack_message_ts": self.thread_ts_root,
                "slack_thread_ts": str(thread_ts or "") or self.thread_ts_root,
            }
        from backend.services.engineer_slack import EngineerSlackDeliveryError

        if self.outcome == "timeout":
            raise EngineerSlackDeliveryError("engineer_slack_request_failed", outcome_unknown=True)
        raise EngineerSlackDeliveryError("channel_not_found", outcome_unknown=False)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ENGINEER_SLACK_OUTBOUND_ENABLED", raising=False)


# ------------------------------------------------------------- C1/C2/C3


def test_source_only_candidate_creates_root_thread_and_binding() -> None:
    host = _Host()
    promotion_id = _parked(host)  # no slack lineage at all
    poster = _FakeSlackPoster()

    result = deliver_knowledge_review_notification(host, _row(host, promotion_id), slack_client=poster, now_value=NOW)

    assert result["status"] == "delivered"
    assert len(poster.posts) == 1
    posted = poster.posts[0]
    assert posted["thread_ts"] is None, "a source-only candidate must ROOT in the review channel"
    assert posted["event"]["event_type"] == "knowledge_review_required"
    assert posted["event"]["event_id"] == knowledge_review_event_id(promotion_id)
    row = _row(host, promotion_id)
    assert row["slack_review_status"] == "delivered"
    assert row["slack_channel_id"] == CHANNEL
    assert row["slack_thread_ts"] == "1700.001", "root message ts becomes the thread binding"
    assert row["slack_review_message_ts"] == "1700.001"
    assert row["status"] == "human_review", "delivery never completes the review"


def test_retry_after_delivery_never_posts_a_second_message() -> None:
    host = _Host()
    promotion_id = _parked(host)
    poster = _FakeSlackPoster()

    first = deliver_knowledge_review_notification(host, _row(host, promotion_id), slack_client=poster, now_value=NOW)
    assert first["status"] == "delivered"
    second = deliver_knowledge_review_notification(host, _row(host, promotion_id), slack_client=poster, now_value=NOW)
    assert second["status"] == "delivered" and second["reason"] == "already_delivered"
    assert len(poster.posts) == 1, "C3: the same event id must never double-post"


def test_retry_after_outcome_unknown_reuses_the_same_event_id() -> None:
    host = _Host()
    promotion_id = _parked(host)
    poster = _FakeSlackPoster(outcome="timeout")

    first = deliver_knowledge_review_notification(host, _row(host, promotion_id), slack_client=poster, now_value=NOW)
    assert first["status"] == "outcome_unknown"
    row = _row(host, promotion_id)
    assert row["slack_review_status"] == "outcome_unknown"
    assert row["status"] == "human_review", "an unknown outcome never loses the candidate"

    poster.outcome = "ok"
    second = deliver_knowledge_review_notification(host, row, slack_client=poster, now_value=NOW)
    assert second["status"] == "delivered"
    assert {p["event"]["event_id"] for p in poster.posts} == {
        knowledge_review_event_id(promotion_id)
    }, "C3: retry reuses the deterministic event id"


def test_explicit_slack_failure_records_failed_and_keeps_review() -> None:
    host = _Host()
    promotion_id = _parked(host)
    poster = _FakeSlackPoster(outcome="channel_not_found")

    result = deliver_knowledge_review_notification(host, _row(host, promotion_id), slack_client=poster, now_value=NOW)
    assert result["status"] == "failed" and result["failure_code"] == "channel_not_found"
    row = _row(host, promotion_id)
    assert row["slack_review_status"] == "failed"
    assert row["slack_review_failure_code"] == "channel_not_found"
    assert row["status"] == "human_review"


def test_case_bound_candidate_replies_to_its_thread() -> None:
    host = _Host()
    promotion_id = _parked(
        host,
        slack_channel_id=CHANNEL,
        slack_thread_ts="999.500",
        engineer_case_id="123-1",
    )
    poster = _FakeSlackPoster()

    result = deliver_knowledge_review_notification(host, _row(host, promotion_id), slack_client=poster, now_value=NOW)

    assert result["status"] == "delivered"
    posted = poster.posts[0]
    assert posted["thread_ts"] == "999.500", "C2: an existing binding replies, never re-roots"


def test_case_bound_channel_mismatch_refuses_send() -> None:
    host = _Host()
    promotion_id = _parked(
        host,
        slack_channel_id="C-OTHER",
        slack_thread_ts="999.500",
        engineer_case_id="123-1",
    )
    poster = _FakeSlackPoster()

    result = deliver_knowledge_review_notification(host, _row(host, promotion_id), slack_client=poster, now_value=NOW)
    assert result["status"] == "failed" and result["failure_code"] == "thread_channel_mismatch"
    assert poster.posts == [], "no message leaves on a mismatched binding"


def test_notify_switch_off_sends_nothing_and_preserves_state() -> None:
    host = _Host()
    promotion_id = _parked(host)
    poster = _FakeSlackPoster()

    import unittest.mock as mock

    with mock.patch.dict(os.environ, {"KNOWLEDGE_SLACK_NOTIFY_ENABLED": "0"}):
        result = deliver_knowledge_review_notification(
            host, _row(host, promotion_id), slack_client=poster, now_value=NOW
        )
    assert result["status"] == "skipped" and result["reason"] == "notify_disabled"
    assert poster.posts == []
    row = _row(host, promotion_id)
    assert row["status"] == "human_review" and row["slack_review_status"] is None


# --------------------------------------------------------------- C4


class _FakeUsersInfo:
    def __init__(self, *, ok=True, user=None, error=None, timeout=False):
        self.ok = ok
        self.user = user
        self.error = error
        self.timeout = timeout

    def __call__(self, url, **kwargs):
        import io

        if self.timeout:
            raise TimeoutError("users.info timeout")
        body = {"ok": self.ok}
        if self.error:
            body["error"] = self.error
        if self.user is not None:
            body["user"] = self.user
        return io.BytesIO(json.dumps(body).encode())


def _identity(user_id="U-9", email="engineer@example.com"):
    return {"id": user_id, "profile": {"email": email, "real_name": "Engineer"}}


def _resolve_with(monkeypatch, responder, user_id="U-9", **kwargs):
    import unittest.mock as mock

    with mock.patch("urllib.request.urlopen", side_effect=responder):
        return resolve_slack_operator(user_id, **kwargs)


def test_resolve_operator_success(monkeypatch) -> None:
    identity = _resolve_with(monkeypatch, _FakeUsersInfo(user=_identity()))
    assert identity["slack_user_id"] == "U-9"
    assert identity["email"] == "engineer@example.com"
    assert identity["display_name"] == "Engineer"


def test_resolve_operator_user_not_found(monkeypatch) -> None:
    with pytest.raises(SlackOperatorResolutionError) as exc:
        _resolve_with(monkeypatch, _FakeUsersInfo(ok=False, error="user_not_found"))
    assert exc.value.reason == "user_not_found"


def test_resolve_operator_api_timeout(monkeypatch) -> None:
    with pytest.raises(SlackOperatorResolutionError) as exc:
        _resolve_with(monkeypatch, _FakeUsersInfo(timeout=True))
    assert exc.value.reason == "slack_api_failed"


def test_resolve_operator_empty_email(monkeypatch) -> None:
    with pytest.raises(SlackOperatorResolutionError) as exc:
        _resolve_with(monkeypatch, _FakeUsersInfo(user={"id": "U-9", "profile": {}}))
    assert exc.value.reason == "empty_email"


def test_resolve_operator_id_mismatch(monkeypatch) -> None:
    with pytest.raises(SlackOperatorResolutionError) as exc:
        _resolve_with(monkeypatch, _FakeUsersInfo(user=_identity(user_id="U-OTHER")))
    assert exc.value.reason == "id_mismatch"


def test_resolve_operator_bot_self_refused(monkeypatch) -> None:
    with pytest.raises(SlackOperatorResolutionError) as exc:
        _resolve_with(monkeypatch, _FakeUsersInfo(user=_identity(user_id=BOT)), user_id=BOT)
    assert exc.value.reason == "bot_message"


def test_decide_persists_verified_identity(monkeypatch) -> None:
    host = _Host()
    promotion_id = _parked(host)

    result = host.decide_weknora_promotion(
        promotion_id,
        decision="reject",
        decided_by="engineer@example.com",
        decided_at=NOW,
        operator_email="engineer@example.com",
        operator_slack_user_id="U-9",
    )
    assert result["status"] == "rejected"
    row = _row(host, promotion_id)
    assert row["human_decided_by_email"] == "engineer@example.com"
    assert row["human_decided_slack_user_id"] == "U-9"


# --------------------------------------------------- C5/C6/C7/C8 inbound


def _inbound_payload(text="knowledge reject", *, raw_text=None, thread_ts="1700.001",
                     user="U-9", channel=CHANNEL, bot=BOT):
    return {
        "team_id": "T-1",
        "channel_id": channel,
        "thread_ts": thread_ts,
        "slack_user_id": user,
        "text": text,
        "raw_text": raw_text if raw_text is not None else f"<@{bot}> {text}",
        "bot_user_id": bot,
        "source_event_id": "evt-1",
        "message_ts": "1700.002",
    }


class _StoreStub:
    def get_standalone_summary_task(self, task_id):
        return None

    def latest_knowledge_source_version(self, source_type, source_id):
        return None


def _handle(monkeypatch, host, payload):
    from backend.services.automation_hermes_slack_actions import (
        handle_slack_knowledge_review_message,
    )
    import unittest.mock as mock

    import backend.services.engineer_slack as slack_module

    with mock.patch.object(
        slack_module, "resolve_slack_operator",
        lambda uid, bot_user_id=None: {
            "slack_user_id": uid, "email": "engineer@example.com", "display_name": "E",
        },
    ):
        return handle_slack_knowledge_review_message(
            _StoreStub(), payload,
            expected_team_id="T-1", expected_channel_id=CHANNEL,
            repository=host,
        )


def _bind_thread(host, promotion_id, thread_ts="1700.001"):
    host.mark_knowledge_slack_review_queued(
        promotion_id, event_id=knowledge_review_event_id(promotion_id), now_value=NOW
    )
    host.complete_knowledge_slack_review(
        promotion_id, status="delivered", slack_channel_id=CHANNEL,
        slack_thread_ts=thread_ts, slack_review_message_ts=thread_ts, now_value=NOW,
    )


def test_source_only_reject_decides_root_thread_candidate(monkeypatch) -> None:
    host = _Host()
    promotion_id = _parked(host)
    _bind_thread(host, promotion_id)

    result = _handle(monkeypatch, host, _inbound_payload("knowledge reject"))
    assert result["ok"] is True, result
    assert result["status"] == "knowledge_review_rejected"
    assert result["operator_email"] == "engineer@example.com"
    row = _row(host, promotion_id)
    assert row["status"] == "rejected"
    assert row["human_decided_by_email"] == "engineer@example.com"


def test_missing_mention_evidence_is_refused(monkeypatch) -> None:
    host = _Host()
    promotion_id = _parked(host)
    payload = _inbound_payload("knowledge reject")
    payload["raw_text"] = ""  # forwarding chain without mention evidence
    result = _handle(monkeypatch, host, payload)
    assert result["ok"] is False
    assert "raw message text" in result["detail"]
    row = _row(host, promotion_id)
    assert row["status"] == "human_review", "C5: zero state change"


def test_mention_of_another_bot_is_refused(monkeypatch) -> None:
    host = _Host()
    _parked(host)
    payload = _inbound_payload("knowledge reject", raw_text="<@U-OTHER-BOT> knowledge reject")
    result = _handle(monkeypatch, host, payload)
    assert result["ok"] is False
    assert "mention" in result["detail"]


def test_wrong_thread_finds_no_candidate(monkeypatch) -> None:
    host = _Host()
    _parked(host)  # never bound to any thread
    result = _handle(monkeypatch, host, _inbound_payload("knowledge reject", thread_ts="9999.999"))
    assert result["ok"] is False
    assert result["status_code"] == 404


def test_multiple_pending_candidates_on_one_thread_refuse(monkeypatch) -> None:
    host = _Host()
    first = _parked(host)
    second = _parked(host, source_id="knowledge-source-summary:csd_issue:ISS-1:v2:kb-2")
    for promotion_id in (first, second):
        _bind_thread(host, promotion_id)
    result = _handle(monkeypatch, host, _inbound_payload("knowledge reject"))
    assert result["ok"] is False
    assert result["status_code"] == 409
    assert "disambiguate" in result["detail"]


def test_non_knowledge_text_returns_none_for_legacy_fallback(monkeypatch) -> None:
    host = _Host()
    result = _handle(monkeypatch, host, _inbound_payload("check the audio session"))
    assert result is None, "non-commands fall back to the legacy unbound behaviour"


def test_bot_self_message_cannot_decide(monkeypatch) -> None:
    host = _Host()
    _parked(host)
    payload = _inbound_payload("knowledge reject", user=BOT)
    result = _handle(monkeypatch, host, payload)
    assert result["ok"] is False
    assert result["status_code"] == 403


def test_targeted_approve_requires_target_and_base_version(monkeypatch) -> None:
    host = _Host()
    promotion_id = _parked(host)
    _bind_thread(host, promotion_id)
    missing_target = _handle(
        monkeypatch, host, _inbound_payload("knowledge approve supplement Full body without flags")
    )
    assert missing_target["ok"] is False and "target" in missing_target["detail"]
    row = _row(host, promotion_id)
    assert row["status"] == "human_review", "C9: zero delivery on a refused decision"

    ok = _handle(
        monkeypatch, host,
        _inbound_payload("knowledge approve merge target=doc-1 base_version=3 Complete merged body"),
    )
    assert ok["ok"] is True, ok
    row = _row(host, promotion_id)
    assert row["status"] == "queued"
    assert row["candidate_payload"]["target_object_id"] == "doc-1"
    assert row["candidate_payload"]["base_version"] == "3"
    assert row["human_decided_by_email"] == "engineer@example.com"


def test_root_event_builder_requires_promotion_identity() -> None:
    event = build_knowledge_review_root_event(
        event_id="knowledge-review:p1", promotion_id="p1", message_text="body"
    )
    assert event["event_type"] == "knowledge_review_required"
    assert event["promotion_id"] == "p1"
    with pytest.raises(ValueError):
        build_knowledge_review_root_event(event_id="", promotion_id="p1", message_text="b")


# ------------------- stage-4 review fixes F1-F4 (concurrency + mention) ----


def test_concurrent_delivery_claims_send_exactly_once() -> None:
    """F1: two concurrent deliver() calls must produce exactly ONE post."""
    import concurrent.futures

    host = _Host()
    promotion_id = _parked(host)
    poster = _FakeSlackPoster()
    promotion = _row(host, promotion_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                deliver_knowledge_review_notification,
                host, promotion, slack_client=poster, now_value=NOW,
            )
            for _ in range(2)
        ]
        results = [future.result() for future in futures]

    statuses = sorted(result["status"] for result in results)
    assert statuses == ["delivered", "in_flight"], statuses
    assert len(poster.posts) == 1, "exactly one Slack post wins the claim"
    row = _row(host, promotion_id)
    assert row["slack_review_status"] == "delivered"


def test_stale_retry_against_in_flight_delivery_posts_nothing() -> None:
    """F1: a retry holding a STALE promotion copy (status not yet visible)
    must not post while another delivery owns the claim."""
    host = _Host()
    promotion_id = _parked(host)
    poster = _FakeSlackPoster()
    stale = _row(host, promotion_id)  # captured BEFORE any delivery

    first = deliver_knowledge_review_notification(host, stale, slack_client=poster, now_value=NOW)
    assert first["status"] == "delivered"
    # second call still uses the stale copy (slack_review_status is None)
    second = deliver_knowledge_review_notification(host, stale, slack_client=poster, now_value=NOW)
    assert second["status"] in {"in_flight", "delivered"}
    assert len(poster.posts) == 1


def test_mention_at_end_is_refused() -> None:
    """F2: the mention must be the PREFIX, not anywhere in the text."""
    host = _Host()
    promotion_id = _parked(host)
    _bind_thread(host, promotion_id)
    result = _handle(
        monkeypatch_fixture(), host,
        _inbound_payload("knowledge reject", raw_text="knowledge reject <@U-BOT-1>"),
    )
    assert result["ok"] is False
    assert "must start with a mention" in result["detail"]
    row = _row(host, promotion_id)
    assert row["status"] == "human_review", "F2: zero state change"


def monkeypatch_fixture():
    import pytest as _pytest

    class _MP:
        def __init__(self):
            self._undos = []

        def setattr(self, obj, name, value):
            old = getattr(obj, name)
            self._undos.append(lambda: setattr(obj, name, old))
            setattr(obj, name, value)

    return _MP()


def test_ticket_bound_command_without_raw_text_is_refused(monkeypatch) -> None:
    """F3: the case-bound path requires the SAME mention evidence — a bare
    command on a bound thread no longer decides."""
    from backend.services.automation_hermes_slack_actions import (
        _try_knowledge_review_command,
    )

    host = _Host()
    promotion_id = _parked(
        host,
        slack_channel_id=CHANNEL,
        slack_thread_ts="999.500",
        engineer_case_id="123-1",
        client_ticket_id="123",
    )
    import unittest.mock as mock

    import backend.services.engineer_slack as slack_module

    with mock.patch.object(
        slack_module, "resolve_slack_operator",
        lambda uid, bot_user_id=None: {
            "slack_user_id": uid, "email": "engineer@example.com", "display_name": "E",
        },
    ):
        result = _try_knowledge_review_command(
            _StoreStub(), "123", "knowledge reject",
            channel_id=CHANNEL, thread_ts="999.500", repository=host,
            slack_user_id="U-9", raw_text="", bot_user_id="",
        )
    assert result is not None and result["ok"] is False
    assert "raw message text" in result["detail"]
    row = _row(host, promotion_id)
    assert row["status"] == "human_review", "F3: zero state change on missing evidence"


def test_ticket_bound_command_with_mention_prefix_decides(monkeypatch) -> None:
    """F3 positive: with the raw mention evidence the bound path decides."""
    from backend.services.automation_hermes_slack_actions import (
        _try_knowledge_review_command,
    )

    host = _Host()
    promotion_id = _parked(
        host,
        slack_channel_id=CHANNEL,
        slack_thread_ts="999.500",
        engineer_case_id="123-1",
        client_ticket_id="123",
    )
    import unittest.mock as mock

    import backend.services.engineer_slack as slack_module

    with mock.patch.object(
        slack_module, "resolve_slack_operator",
        lambda uid, bot_user_id=None: {
            "slack_user_id": uid, "email": "engineer@example.com", "display_name": "E",
        },
    ):
        result = _try_knowledge_review_command(
            _StoreStub(), "123", "knowledge reject",
            channel_id=CHANNEL, thread_ts="999.500", repository=host,
            slack_user_id="U-9",
            raw_text=f"<@{BOT}> knowledge reject", bot_user_id=BOT,
        )
    assert result is not None and result["ok"] is True, result
    row = _row(host, promotion_id)
    assert row["status"] == "rejected"
    assert row["human_decided_by_email"] == "engineer@example.com"


def test_plain_message_without_raw_text_falls_back_to_unbound(monkeypatch) -> None:
    """F4: a NON-knowledge message without raw_text must return None (the
    caller keeps the legacy ignored_unbound behaviour), never a 422."""
    host = _Host()
    result = _handle(monkeypatch, host, _inbound_payload("hello"))
    payload = _inbound_payload("hello")
    payload["raw_text"] = ""
    result = _handle(monkeypatch, host, payload)
    assert result is None, "non-knowledge messages fall back, no 422"
