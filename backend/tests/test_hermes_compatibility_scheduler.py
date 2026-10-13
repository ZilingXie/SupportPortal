"""14037: legacy claims must preserve Hermes relay identity until readback."""
from copy import deepcopy
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

from backend import worker
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services import zendesk_comments
from backend.tests.test_worker_compatibility_scope import compatibility_fixture, projected_messages


def assert_scheduler_excludes_compatibility(repository):
    repository, _ = compatibility_fixture(auto=False, delivered=False, repository=repository)
    job = repository.get_account_reply_job("job-1")
    job.update(status="persona_queued", payload={"hermes_compatibility_only": True})
    repository.save_account_reply_job(job)
    legacy = deepcopy(job)
    legacy.update(job_id="legacy-control", payload={}, trigger_message_created_at="2026-10-13T10:00:01Z")
    repository.save_account_reply_job(legacy)
    claimed = repository.claim_account_reply_jobs(
        from_status="persona_queued", to_status="persona_preparing", due_only=False,
        now_value="2026-10-13T10:01:00Z", limit=1,
    )
    assert [item["job_id"] for item in claimed] == ["legacy-control"]
    assert repository.get_account_reply_job("job-1")["status"] == "persona_queued"


def assert_claimed_compatibility_survives_readback(repository, *, auto, stage, v8=False):
    repository, delivery = compatibility_fixture(auto=auto, delivered=False, repository=repository)
    job = repository.get_account_reply_job("job-1")
    prefix = "persona_v8" if v8 else "persona"
    if v8:
        from backend.services.account_reply_jobs import ACCOUNT_REPLY_PERSONA_PIPELINE
        job["payload"]["reply_pipeline"] = ACCOUNT_REPLY_PERSONA_PIPELINE
    job.update(status=f"{prefix}_queued" if stage == "preparing" else f"{prefix}_scheduled")
    repository.save_account_reply_job(job)
    # The legacy claim precedes Hermes ownership: its copy has a stale payload.
    claimed = repository.claim_account_reply_jobs(
        from_status=job["status"], to_status=f"{prefix}_{stage}", due_only=False,
        now_value="2026-10-13T10:01:00Z", limit=1,
    )[0]
    current = repository.get_account_reply_job("job-1")
    current["payload"]["hermes_compatibility_only"] = True
    current["payload"]["reply_owner"] = "hermes_persona"
    repository.save_account_reply_job(current)
    with patch.object(worker, "ticket_repository", repository), patch.object(
        worker, "render_automation_reply",
    ) as render, patch.object(worker, "add_ticket_comment") as send:
        entry = worker._prepare_account_reply_job_impl if stage == "preparing" else worker._publish_account_reply_job
        entry(claimed)
        saved = repository.get_account_reply_job("job-1")
        assert saved["status"] == f"{prefix}_queued"
        assert saved["payload"]["hermes_compatibility_only"] is True
        worker._process_claimed_account_reply_jobs(
            from_status=f"{prefix}_queued", to_status=f"{prefix}_preparing", due_only=False, limit=10,
        )
        render.assert_not_called()
        send.assert_not_called()
        assert repository.get_account_case("AC-COMPAT")["internal_email_send_status"] == "awaiting_public_reply"
        delivery["status"] = "outcome_unknown"
        release = repository.release_enablement_relay_requests_after_public_reply
        winners = []

        def observe_release(**kwargs):
            result = release(**kwargs)
            winners.extend(row["request_id"] for row in result)
            return result

        audits = [{"created_at": "2026-10-13T10:02:00Z", "events": [{
            "type": "Comment", "public": True, "id": "current-comment",
            "body": delivery["immutable_content"] + "\n\nZendesk agent footer",
        }]}]
        with patch.object(zendesk_comments, "_fetch_ticket_audits", return_value=(200, audits)), patch.object(
            repository, "release_enablement_relay_requests_after_public_reply", side_effect=observe_release,
        ):
            worker._deliver_hermes_zendesk_comment(delivery)
            worker._deliver_hermes_zendesk_comment(delivery)
        send.assert_not_called()
    assert winners == (["request-1"] if auto else [])
    assert len(projected_messages(repository)) == 1
    case = repository.get_account_case("AC-COMPAT")
    assert case["automation_status"] == "automation"
    assert case["internal_email_send_status"] == ("not_applicable" if auto else "pending")
    assert repository.list_account_zendesk_comment_deliveries_for_case("AC-COMPAT")[0]["zendesk_comment_id"] == "current-comment"


def assert_stale_claim_cannot_remove_ownership(repository):
    repository, _ = compatibility_fixture(auto=False, delivered=False, repository=repository)
    job = repository.get_account_reply_job("job-1")
    job["status"] = "persona_queued"
    repository.save_account_reply_job(job)
    claimed = repository.claim_account_reply_jobs(
        from_status="persona_queued", to_status="persona_preparing", due_only=False,
        now_value="2026-10-13T10:01:00Z", limit=1,
    )[0]
    current = repository.get_account_reply_job("job-1")
    current["payload"]["hermes_compatibility_only"] = True
    repository.save_account_reply_job(current)
    stale = deepcopy(claimed)
    stale["status"] = "persona_scheduled"
    assert repository.update_claimed_account_reply_job(
        stale, expected_status=claimed["status"], expected_claimed_at=claimed["claimed_at"],
        expected_attempt_count=claimed["attempt_count"],
    ) is None
    assert repository.get_account_reply_job("job-1")["payload"]["hermes_compatibility_only"] is True
    current["status"] = "persona_queued"
    assert repository.update_claimed_account_reply_job(
        current, expected_status=claimed["status"], expected_claimed_at=claimed["claimed_at"],
        expected_attempt_count=claimed["attempt_count"],
    ) is not None


def test_scheduler_excludes_compatibility_without_starving_legacy():
    assert_scheduler_excludes_compatibility(InMemoryTicketRepository())


@pytest.mark.parametrize("v8", [False, True])
@pytest.mark.parametrize("auto", [False, True])
@pytest.mark.parametrize("stage", ["preparing", "publishing"])
def test_claimed_compatibility_survives_readback(auto, stage, v8):
    assert_claimed_compatibility_survives_readback(InMemoryTicketRepository(), auto=auto, stage=stage, v8=v8)


def test_stale_claim_cannot_remove_ownership():
    assert_stale_claim_cannot_remove_ownership(InMemoryTicketRepository())
