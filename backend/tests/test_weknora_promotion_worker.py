from __future__ import annotations

import json
import os

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from unittest.mock import Mock, patch  # noqa: E402

from backend import worker  # noqa: E402
from backend.services.weknora_client import WeKnoraError  # noqa: E402
from backend.services.weknora_promotion_adapter import WeKnoraPromotionOutcome

ENABLED_ENV = {
    "HERMES_KNOWLEDGE_WORKFLOW_ENABLED": "1",
    "WEKNORA_PROMOTION_ENABLED": "1",
    "WEKNORA_BASE_URL": "http://weknora.test",
    "WEKNORA_API_TOKEN": "synthetic-token",
    "WEKNORA_API_CONTRACT_JSON": json.dumps({"health": {"path": "/health"}}),
}

TASK = {
    "promotion_id": "weknora:hermes_case_promotion:123-1:1:knowledge",
    "status": "queued",
    "candidate_type": "knowledge",
    "decision": "new",
    "candidate_payload": {
        "schema_version": "v1",
        "candidate_type": "knowledge",
        "decision": "new",
        "title": "T",
        "content": "C",
    },
}


class FakeAdapterClient:
    """Stands in for WeKnoraClient inside the worker drain."""

    def has_memory_identity(self) -> bool:
        return True

    def supports_conditional_update(self, candidate_type):
        return True

    def knowledge_create(self, *, title, content, idempotency_key, **kwargs):
        return {"object_id": "doc-1", "version": "2", "receipt": {"ok": True}}

    def knowledge_read(self, *, object_id):
        return {"object_id": object_id, "version": "2", "title": "T", "content": "C"}

    def memory_list(self, *, top_k=None):
        return []

    def memory_create(self, *, content, idempotency_key, **kwargs):
        return {"object_id": "mem-1", "version": "1", "receipt": {"ok": True}}


def _repository() -> Mock:
    repository = Mock()
    repository.list_weknora_promotions.return_value = [dict(TASK)]
    repository.claim_weknora_promotion.return_value = {**TASK, "status": "active"}
    return repository


def test_drain_is_disabled_without_explicit_env() -> None:
    repository = _repository()
    env = {key: value for key, value in ENABLED_ENV.items() if key != "WEKNORA_PROMOTION_ENABLED"}
    with patch.dict(os.environ, env, clear=False), patch.object(
        worker, "ticket_repository", repository
    ):
        self_count = worker._drain_weknora_promotions(limit=20)
    assert self_count == 0
    repository.list_weknora_promotions.assert_not_called()


def test_drain_completes_accepted_promotion() -> None:
    repository = _repository()
    with patch.dict(os.environ, ENABLED_ENV, clear=False), patch.object(
        worker, "ticket_repository", repository
    ), patch.object(worker, "WeKnoraClient", FakeAdapterClient):
        assert worker._drain_weknora_promotions(limit=20) == 1
    repository.complete_weknora_promotion.assert_called_once()
    kwargs = repository.complete_weknora_promotion.call_args.kwargs
    assert kwargs["status"] == "accepted"
    assert kwargs["weknora_object_id"] == "doc-1"
    assert kwargs["failure_code"] is None


def test_drain_is_disabled_when_governance_master_switch_off() -> None:
    """p2-186 acceptance round 2: the governance master switch gates promotion
    CONSUMPTION, not only production. With the switch off but a RESIDUAL
    WEKNORA_PROMOTION_ENABLED=1 and a configured client facing a queued
    candidate, the drain claims nothing and touches no external boundary."""
    repository = _repository()
    with patch.dict(os.environ, ENABLED_ENV, clear=False), patch.object(
        worker, "ticket_repository", repository
    ), patch.object(worker, "WeKnoraClient", FakeAdapterClient):
        import os as _os

        _os.environ.pop("HERMES_KNOWLEDGE_WORKFLOW_ENABLED", None)
        try:
            assert worker._drain_weknora_promotions(limit=20) == 0
        finally:
            _os.environ["HERMES_KNOWLEDGE_WORKFLOW_ENABLED"] = "1"
    repository.list_weknora_promotions.assert_not_called()
    repository.claim_weknora_promotion.assert_not_called()
    repository.complete_weknora_promotion.assert_not_called()


class _ExplodingAdapter:
    def __init__(self, client) -> None:
        pass

    def execute(self, task):
        raise RuntimeError("boom")


def test_adapter_crash_records_failed_without_losing_the_lease() -> None:
    repository = _repository()
    with patch.dict(os.environ, ENABLED_ENV, clear=False), patch.object(
        worker, "ticket_repository", repository
    ), patch.object(worker, "WeKnoraClient", FakeAdapterClient), patch.object(
        worker, "WeKnoraPromotionAdapter", _ExplodingAdapter
    ):
        assert worker._drain_weknora_promotions(limit=20) == 1
    kwargs = repository.complete_weknora_promotion.call_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["failure_code"] == "adapter_crashed"


class _NotConfiguredClient:
    def has_memory_identity(self) -> bool:
        return False

    def knowledge_create(self, **kwargs):
        raise WeKnoraError("not configured", failure_kind="not_configured")


def test_drain_records_config_errors_as_failed_not_retryable() -> None:
    repository = _repository()
    with patch.dict(os.environ, ENABLED_ENV, clear=False), patch.object(
        worker, "ticket_repository", repository
    ), patch.object(worker, "WeKnoraClient", _NotConfiguredClient):
        assert worker._drain_weknora_promotions(limit=20) == 1
    kwargs = repository.complete_weknora_promotion.call_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["failure_code"] == "weknora_write_not_configured"


def test_outcome_unknown_tasks_are_not_reclaimed() -> None:
    repository = _repository()
    repository.claim_weknora_promotion.return_value = None
    repository.list_weknora_promotions.return_value = [{**TASK, "status": "outcome_unknown"}]
    with patch.dict(os.environ, ENABLED_ENV, clear=False), patch.object(
        worker, "ticket_repository", repository
    ), patch.object(worker, "WeKnoraClient", FakeAdapterClient):
        assert worker._drain_weknora_promotions(limit=20) == 0
    repository.claim_weknora_promotion.assert_not_called()


def test_drain_makes_zero_external_calls_for_superseded_generation() -> None:
    """Review round 4, R4-2: the write boundary checks the generation BEFORE
    the adapter runs — a candidate whose frozen source version was superseded
    fails with zero external calls, and an approved-but-stale candidate is
    caught here too (the approval guard alone leaves the gap)."""
    superseded = {
        **TASK,
        "promotion_id": "weknora:knowledge_source_review:src:c1:knowledge:h",
        "source_type": "knowledge_source_review",
        "input_fingerprint": "v1",
    }
    repository = _repository()
    repository.list_weknora_promotions.return_value = [superseded]
    repository.claim_weknora_promotion.return_value = {**superseded, "status": "active"}
    repository.get_standalone_summary_task.return_value = {
        "source_type": "zendesk_ticket", "source_id": "T1", "source_version": "v1",
    }
    repository.latest_knowledge_source_version.return_value = "v2"
    with patch.dict(os.environ, ENABLED_ENV, clear=False), patch.object(
        worker, "ticket_repository", repository
    ), patch.object(worker, "WeKnoraClient", FakeAdapterClient), patch.object(
        worker.WeKnoraPromotionAdapter, "execute", side_effect=AssertionError(
            "the adapter must not run for a superseded generation"
        )
    ):
        assert worker._drain_weknora_promotions(limit=20) == 1
    repository.complete_weknora_promotion.assert_called_once()
    kwargs = repository.complete_weknora_promotion.call_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["failure_code"] == "source_input_diverged"
    assert "zero external writes" in kwargs["failure_detail"]


def test_drain_fails_closed_when_native_state_store_unavailable() -> None:
    """Review round 5, R5-1: a state-store build failure must surface as a
    visible failed promotion with ZERO external calls — the earlier
    `False or None` conversion silently skipped the check and wrote."""
    superseded = {
        **TASK,
        "promotion_id": "weknora:knowledge_source_review:src:c1:knowledge:h",
        "source_type": "knowledge_source_review",
        "input_fingerprint": "v1",
    }
    repository = _repository()
    repository.list_weknora_promotions.return_value = [superseded]
    repository.claim_weknora_promotion.return_value = {**superseded, "status": "active"}
    repository.get_standalone_summary_task.return_value = {
        "source_type": "zendesk_ticket", "source_id": "T1", "source_version": "v1",
    }
    repository.latest_knowledge_source_version.return_value = "v1"  # version unchanged
    import backend.services.automation_ecs_store as store_module

    def _broken_store(*args, **kwargs):
        raise RuntimeError("automation store unavailable")

    with patch.dict(os.environ, ENABLED_ENV, clear=False), patch.object(
        worker, "ticket_repository", repository
    ), patch.object(worker, "WeKnoraClient", FakeAdapterClient), patch.object(
        store_module, "create_automation_ecs_store", _broken_store
    ), patch.object(
        worker.WeKnoraPromotionAdapter, "execute", side_effect=AssertionError(
            "the adapter must not run when the state store is unavailable"
        )
    ):
        assert worker._drain_weknora_promotions(limit=20) == 1
    repository.complete_weknora_promotion.assert_called_once()
    kwargs = repository.complete_weknora_promotion.call_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["failure_code"] == "native_state_unavailable"


# R16-2: the knowledge_review action is in the Slack whitelist and the
# notification actually builds a valid payload and issues an HTTP call.
def test_slack_notification_builds_and_delivers(monkeypatch) -> None:
    from backend.services.engineer_slack import (
        build_knowledge_review_event,
        notify_knowledge_review_candidate,
        _SLACK_ACTIONS,
    )

    assert "knowledge_review" in _SLACK_ACTIONS

    event = build_knowledge_review_event(
        event_id="evt-1",
        engineer_case_id="123-1",
        promotion_id="weknora:test:1",
        candidate_type="knowledge",
        decision="replace",
        statement="Region EU join failures",
        proposed_content="Full proposed body for review",
        target_object_id="doc-7",
        base_version="5",
        rationale="evidence gap",
    )
    assert event["event_type"] == "knowledge_review_required"
    assert "Full proposed body" in event["message_text"]
    assert "doc-7" in event["message_text"]

    # Mock the HTTP boundary; verify the call is actually issued.
    import urllib.request
    from unittest.mock import patch as mock_patch

    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append({"url": request.full_url, "data": request.data})
        return type("R", (), {
            "__enter__": lambda s: type("r", (), {"read": lambda: b'{"ok":true,"ts":"123.456","channel":"C1"}'}),
            "__exit__": lambda s, *a: False,
        })()

    with mock_patch.dict(os.environ, {
        "ENGINEER_SLACK_ACCESS_TOKEN": "xoxb-test",
        "ENGINEER_SLACK_TEAM_ID": "T1",
        "ENGINEER_SLACK_CHANNEL_ID": "C1",
    }, clear=False), mock_patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
        result = notify_knowledge_review_candidate(
            engineer_case_id="123-1",
            promotion_id="weknora:test:1",
            candidate={"candidate_type": "knowledge", "decision": "replace",
                       "statement": "test", "content": "body"},
            slack_thread_ts="123.456",
        )
    assert result is not None, "the notification must deliver (not silently None)"
    assert len(calls) == 1, "exactly one Slack HTTP call"
    assert b"Knowledge review required" in calls[0]["data"]


# R16-3: standalone promotions (native Hermes case, no legacy EngineerCase)
# with Slack thread lineage also trigger the notification.
def test_standalone_promotion_with_slack_thread_notifies(monkeypatch) -> None:
    standalone = {
        **TASK,
        "promotion_id": "weknora:knowledge_source_review:src:c1:knowledge:h",
        "source_type": "knowledge_source_review",
        "engineer_case_id": None,  # native case: no legacy id
        "client_ticket_id": "13801",
        "slack_channel_id": "C-NATIVE",
        "slack_thread_ts": "1696900000.000100",  # lineage IS present
    }
    repository = Mock()
    repository.list_weknora_promotions.return_value = [standalone]
    repository.claim_weknora_promotion.return_value = {**standalone, "status": "active"}
    repository.get_standalone_summary_task.return_value = {
        "source_type": "zendesk_ticket", "source_id": "13801", "source_version": "v1",
    }
    repository.latest_knowledge_source_version.return_value = "v1"
    notify_calls = []

    with patch.dict(os.environ, ENABLED_ENV, clear=False), patch.object(
        worker, "ticket_repository", repository
    ), patch.object(worker, "WeKnoraClient", FakeAdapterClient), patch.object(
        worker.WeKnoraPromotionAdapter, "execute",
        return_value=WeKnoraPromotionOutcome(status="human_review", failure_code="review_requested_human_review"),
    ), patch.object(
        worker, "_notify_knowledge_review", side_effect=lambda c: notify_calls.append(c),
    ):
        assert worker._drain_weknora_promotions(limit=20) == 1
    assert len(notify_calls) == 1, "standalone promotion with Slack thread must notify"
    assert notify_calls[0].get("slack_thread_ts") == "1696900000.000100"


# R16-3: sources with neither case id nor Slack thread (bare CSD/article
# without a case binding) do NOT attempt notification.
def test_threadless_source_does_not_notify(monkeypatch) -> None:
    threadless = {
        **TASK,
        "promotion_id": "weknora:knowledge_source_review:bare:c1:knowledge:h",
        "source_type": "knowledge_source_review",
        "engineer_case_id": None,
        "client_ticket_id": None,
        "slack_channel_id": None,
        "slack_thread_ts": None,  # no lineage
    }
    repository = Mock()
    repository.list_weknora_promotions.return_value = [threadless]
    repository.claim_weknora_promotion.return_value = {**threadless, "status": "active"}
    repository.get_standalone_summary_task.return_value = {
        "source_type": "csd_issue", "source_id": "CSD-77", "source_version": "v1",
    }
    repository.latest_knowledge_source_version.return_value = "v1"
    notify_calls = []

    with patch.dict(os.environ, ENABLED_ENV, clear=False), patch.object(
        worker, "ticket_repository", repository
    ), patch.object(worker, "WeKnoraClient", FakeAdapterClient), patch.object(
        worker.WeKnoraPromotionAdapter, "execute",
        return_value=WeKnoraPromotionOutcome(status="human_review", failure_code="review_requested_human_review"),
    ), patch.object(
        worker, "_notify_knowledge_review", side_effect=lambda c: notify_calls.append(c),
    ):
        assert worker._drain_weknora_promotions(limit=20) == 1
    assert len(notify_calls) == 0, "no notification surface — queue API is the review path"
