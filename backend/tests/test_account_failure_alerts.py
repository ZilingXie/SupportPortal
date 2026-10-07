from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_failure_alerts import notify_account_failure


def test_account_failure_alert_is_idempotent_and_redacted():
    repository = InMemoryTicketRepository()
    sent = []

    def mail(**kwargs):
        sent.append(kwargs)

    first = notify_account_failure(
        repository=repository,
        incident_id="incident-1",
        stage="intent_classifier",
        code="account_ai_invocation_exhausted",
        ticket_id="TK-1",
        account_case_id="AC-TK-1",
        attempts=4,
        detail="customer@example.com bearer abcdefghijklmnopqrstuvwxyz1234567890",
        mail_sender=mail,
        now="2026-08-13T00:00:00Z",
    )
    second = notify_account_failure(
        repository=repository,
        incident_id="incident-1",
        stage="intent_classifier",
        code="account_ai_invocation_exhausted",
        attempts=4,
        detail="again",
        mail_sender=mail,
        now="2026-08-13T00:01:00Z",
    )
    assert first["status"] == "sent"
    assert second["status"] == "already_claimed"
    assert len(sent) == 1
    assert sent[0]["to_address"] == "xieziling@agora.io"
    assert "customer@example.com" not in sent[0]["body"]
    assert "abcdefghijklmnopqrstuvwxyz1234567890" not in sent[0]["body"]


def test_failed_alert_claim_can_be_retried():
    repository = InMemoryTicketRepository()
    calls = iter([ValueError("missing Graph mail config"), None])

    def mail(**_kwargs):
        value = next(calls)
        if value:
            raise value

    first = notify_account_failure(
        repository=repository,
        incident_id="incident-2",
        stage="persona",
        code="account_ai_invocation_exhausted",
        mail_sender=mail,
        now="2026-08-13T00:00:00Z",
    )
    second = notify_account_failure(
        repository=repository,
        incident_id="incident-2",
        stage="persona",
        code="account_ai_invocation_exhausted",
        mail_sender=mail,
        now="2026-08-13T00:01:00Z",
    )
    assert first["status"] == "delivery_failed"
    assert second["status"] == "sent"


def test_account_rerun_alert_includes_redacted_summary():
    repository = InMemoryTicketRepository()
    sent = []

    notify_account_failure(
        repository=repository,
        incident_id="rerun-incident",
        stage="preflight",
        code="llm_canary_failed",
        job_id="account-rerun-job",
        detail="model request failed for customer@example.com token=secret-token-value",
        summary={
            "build_ref": "build-123",
            "status": "failed",
            "degraded": True,
            "processed": 1,
            "succeeded": 0,
            "failed": 1,
            "remaining": 146,
            "failed_case_id": "AC-SYNTH-001",
            "failed_stage": "preflight",
        },
        mail_sender=lambda **kwargs: sent.append(kwargs),
        now="2026-08-13T00:00:00Z",
    )

    body = sent[0]["body"]
    assert "Rerun summary:" in body
    assert "Processed: 1" in body
    assert "Remaining: 146" in body
    assert "customer@example.com" not in body
    assert "secret-token-value" not in body


def test_account_rerun_stable_reason_code_is_not_redacted_as_token():
    repository = InMemoryTicketRepository()
    sent = []

    notify_account_failure(
        repository=repository,
        incident_id="rerun-code-incident",
        stage="email_config",
        code="account_internal_email_recipient_missing",
        mail_sender=lambda **kwargs: sent.append(kwargs),
        now="2026-08-16T00:00:00Z",
    )

    assert "Code: account_internal_email_recipient_missing" in sent[0]["body"]


def test_timeout_alert_is_terminal_and_not_resent():
    import socket as socket_module

    repository = InMemoryTicketRepository()
    mail_calls = []

    def mail(**_kwargs):
        mail_calls.append("called")
        raise socket_module.timeout("timed out after accept")

    first = notify_account_failure(
        repository=repository,
        incident_id="incident-timeout",
        stage="persona",
        code="account_ai_invocation_exhausted",
        ticket_id="TK-T",
        attempts=2,
        mail_sender=mail,
        now="2026-09-15T00:00:00Z",
    )
    second = notify_account_failure(
        repository=repository,
        incident_id="incident-timeout",
        stage="persona",
        code="account_ai_invocation_exhausted",
        ticket_id="TK-T",
        attempts=2,
        mail_sender=mail,
        now="2026-09-15T00:01:00Z",
    )
    assert first["status"] == "delivery_outcome_unknown"
    # Terminal completion: the same incident reported again must not resend.
    assert second["status"] == "already_claimed"
    assert len(mail_calls) == 1


def test_http_5xx_alert_is_outcome_unknown():
    import urllib.error

    repository = InMemoryTicketRepository()

    def mail(**_kwargs):
        raise urllib.error.HTTPError(
            "https://graph.microsoft.com/v1.0/me/sendMail", 503, "unavailable", hdrs=None, fp=None
        )

    result = notify_account_failure(
        repository=repository,
        incident_id="incident-5xx",
        stage="persona",
        code="account_ai_invocation_exhausted",
        mail_sender=mail,
        now="2026-09-15T00:00:00Z",
    )
    assert result["status"] == "delivery_outcome_unknown"


def test_bad_status_line_alert_is_terminal_outcome_unknown():
    import http.client

    repository = InMemoryTicketRepository()
    mail_calls = []

    def mail(**_kwargs):
        mail_calls.append("called")
        raise http.client.BadStatusLine("")

    first = notify_account_failure(
        repository=repository,
        incident_id="incident-badstatus",
        stage="persona",
        code="account_ai_invocation_exhausted",
        mail_sender=mail,
        now="2026-09-15T00:00:00Z",
    )
    second = notify_account_failure(
        repository=repository,
        incident_id="incident-badstatus",
        stage="persona",
        code="account_ai_invocation_exhausted",
        mail_sender=mail,
        now="2026-09-15T00:01:00Z",
    )
    assert first["status"] == "delivery_outcome_unknown"
    assert second["status"] == "already_claimed"
    assert len(mail_calls) == 1


def test_failure_alert_reports_unknown_context_instead_of_defaults():
    """AC-13898: absent job/attempt context must read as <unknown>, never a
    fabricated "<none>" job or a defaulted attempt count of 0."""
    from backend.services.account_failure_alerts import build_account_failure_alert

    _subject, body = build_account_failure_alert(
        incident_id="account-automation:AC-13898:hermes_tool:turn_terminal_failure:hermes_run_failed",
        stage="hermes_tool",
        code="turn_terminal_failure:hermes_run_failed",
        ticket_id="13898",
        account_case_id="AC-13898",
    )
    assert "Job: <unknown>" in body
    assert "Attempts: <unknown>" in body
    assert "Attempts: 0" not in body
    assert "Job: <none>" not in body
    assert "Environment: <unknown>" in body
    assert "Turn: <unknown>" in body
    assert "Run: <unknown>" in body
    assert "Failed phase: <unknown>" in body


def test_failure_alert_carries_real_context_fields():
    from backend.services.account_failure_alerts import build_account_failure_alert

    _subject, body = build_account_failure_alert(
        incident_id="incident-ctx",
        stage="hermes_tool",
        code="turn_terminal_failure:hermes_run_failed",
        ticket_id="13999",
        account_case_id="AC-13999",
        job_id="job-cb528543c1f447a39fb6e4bcc92b1616",
        attempts=1,
        environment="preproduction",
        turn_id="turn-de53f1d25a6a4f7cbf19d5deb9dd23fb",
        run_id="run_4213b9e35fe3469e9761402c091aea65",
        failed_phase="work",
        detail="paused; requires manual continuation [failure_reason=session_persistence_failed:io]",
    )
    assert "Job: job-cb528543c1f447a39fb6e4bcc92b1616" in body
    assert "Attempts: 1" in body
    assert "Environment: preproduction" in body
    assert "Turn: turn-de53f1d25a6a4f7cbf19d5deb9dd23fb" in body
    assert "Run: run_4213b9e35fe3469e9761402c091aea65" in body
    assert "Failed phase: work" in body
    assert "failure_reason=session_persistence_failed:io" in body
