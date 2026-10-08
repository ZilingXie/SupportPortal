import pytest
from unittest.mock import patch

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_failure_alerts import notify_account_human_takeover, notify_account_failure


@pytest.mark.parametrize("note,queue,release,cancel", [
    ("sent", "queued", "recorded", "completed"),
    ("skipped_inactive_handler", "skipped_inactive_handler", "unknown", "unknown"),
    ("skipped_not_production", "skipped_not_production", "skipped_not_production", "completed"),
    ("failed", "failed", "unknown", "failed:RuntimeError"),
    ("outcome_unknown", "outcome_unknown", "unknown", "unknown"),
])
@pytest.mark.parametrize("notifier", [notify_account_human_takeover, notify_account_failure])
def test_actual_alert_entry_reports_each_action_without_inventing_success(note, queue, release, cancel, notifier):
    sent = []
    repository = InMemoryTicketRepository()
    with patch("backend.services.account_failure_alerts.send_graph_mail", side_effect=lambda **kwargs: sent.append(kwargs)):
        result = notifier(repository=repository, incident_id="fixture-handoff", stage="agent.work", code="fixture_failure",
            ticket_id="123", job_id="fixture-job", attempts=3, detail="failure_reason=fixture_terminal",
            handoff={"internal_note_status":note,"route_back_status":queue,"ownership_release_status":release,"reply_cancellation_status":cancel},
            now="2026-10-08T10:00:00Z")
    assert result["status"] == "sent"
    body = sent[0]["body"]
    assert f"Internal note: {note}" in body and f"Queue return: {queue}" in body
    assert f"Ownership release: {release}" in body and f"Pending reply cancellation: {cancel}" in body
    assert "Cancelled reply jobs: unconfirmed" in body
    assert "fixture-job" in body and "fixture_terminal" in body and "3" in body
    assert "actions already ran" not in body and "waiting for pickup" not in body
