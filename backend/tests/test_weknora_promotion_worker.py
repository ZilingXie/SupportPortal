from __future__ import annotations

import json
import os

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from unittest.mock import Mock, patch  # noqa: E402

from backend import worker  # noqa: E402
from backend.services.weknora_client import WeKnoraError  # noqa: E402
from backend.services.weknora_promotion_adapter import WeKnoraPromotionOutcome

ENABLED_ENV = {
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
