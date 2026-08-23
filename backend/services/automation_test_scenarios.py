"""Multi-turn Zendesk regression scenario engine for the production pipeline.

Shared by the /automation/test console (background thread per run, state
persisted in automation_test_scenario_runs) and the CLI wrapper in
scripts/testing/production_ticket_scenarios.py.

Each scenario plays a full customer conversation through the REAL channels:
customer turns are sent from the dedicated 163 mailbox (SMTP) and threaded
into the Zendesk ticket via the notification email's headers (IMAP); internal
enablement approval stays MANUAL — the engine pauses (status surfaced via the
listener) until the internal reply is processed. Assertions are structural
(reply intents, internal email status, suspension workflow state, Zendesk
status), never exact LLM wording.

Every run creates REAL Zendesk tickets. Subjects carry the [zac test] tag.

All I/O goes through instance methods (db_query / send_email /
imap_find_notification / sleep) so tests can subclass and script them.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import json
import os
import smtplib
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Callable

import psycopg

# imaplib does not know the non-standard RFC2971 ID command; 163 Coremail
# refuses SELECT/SEARCH (NO) unless it is sent before login.
imaplib.Commands["ID"] = ("NONAUTH", "AUTH", "SELECTED")

ZENDESK_TICKET_URL = "https://agoraio.zendesk.com/agent/tickets"
ZENDESK_SUPPORT_ADDRESS = "support@agoraio.zendesk.com"
DEFAULT_SUBJECT_TAG = "[zac test] "
DEFAULT_TURN_TIMEOUT_MIN = 20
DEFAULT_APPROVAL_TIMEOUT_MIN = 45
DEFAULT_POLL_INTERVAL_SECONDS = 20

ENABLEMENT_APP_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"

FRAUD_FULL_INFO_BODY = (
    "Thanks. Here is the review information.\n\n"
    "Company Information:\n"
    "- Company: Zac Test Labs Inc.\n"
    "- Registration country: United States\n"
    "- Registered address: 100 Test Avenue, San Jose, CA\n\n"
    "Contact Information:\n"
    "- Name: Zac Tester\n"
    "- Email: zac.tester@example.com\n"
    "- Phone: +1 555 010 8888\n\n"
    "Use Case:\n"
    "We build a live-streaming classroom product and use Agora real-time video and "
    "audio to connect teachers with students in small groups.\n\n"
    "Payment Information:\n"
    "Usage is covered by corporate credit-card top-ups managed by our finance team."
)


class AutomationTestScenarioError(RuntimeError):
    """Raised when the scenario engine is unusable (bad/missing config)."""


class ScenarioCancelled(Exception):
    """Raised inside run_scenario when the caller requested cancellation."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ScenarioStep:
    step: str
    status: str  # PASS / FAIL
    detail: str = ""
    at: str = field(default_factory=lambda: now_utc().isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {"step": self.step, "status": self.status, "detail": self.detail, "at": self.at}


@dataclass
class ScenarioContext:
    scenario_id: str
    subject: str = ""
    zendesk_ticket_id: str = ""
    account_case_id: str = ""
    client_ticket_id: str = ""
    turn_started_at: datetime = field(default_factory=now_utc)


class ScenarioEngine:
    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int,
        sender: str,
        smtp_password: str,
        imap_host: str,
        imap_port: int,
        db_dsn: str,
        db_schema: str = "supportportal",
        subject_tag: str = DEFAULT_SUBJECT_TAG,
        turn_timeout_min: int = DEFAULT_TURN_TIMEOUT_MIN,
        approval_timeout_min: int = DEFAULT_APPROVAL_TIMEOUT_MIN,
        poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
        listener: Callable[[str, dict[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sender = sender
        self.smtp_password = smtp_password
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.db_dsn = db_dsn
        self.db_schema = db_schema
        self.subject_tag = subject_tag
        self.turn_timeout_min = turn_timeout_min
        self.approval_timeout_min = approval_timeout_min
        self.poll_interval_seconds = poll_interval_seconds
        self.listener = listener
        self.should_cancel = should_cancel or (lambda: False)
        self.steps: list[ScenarioStep] = []

    # -- construction ----------------------------------------------------

    @classmethod
    def from_env(
        cls,
        listener: Callable[[str, dict[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> "ScenarioEngine":
        smtp_host = str(os.getenv("BILLING_AUTOMATION_SMTP_HOST") or "").strip()
        sender = str(os.getenv("BILLING_AUTOMATION_SMTP_USERNAME") or "").strip()
        smtp_password = str(os.getenv("BILLING_AUTOMATION_SMTP_PASSWORD") or "").strip()
        # The engine always targets the production ticket DB. In the
        # api_production container TICKET_DB_DSN already IS production, and
        # PRODUCTION_TICKET_DB_DSN is present via the root .env everywhere;
        # prefer the explicit production key so a staging api process can
        # never drive scenarios against the staging DB.
        db_dsn = (
            str(os.getenv("AUTOMATION_TEST_DB_DSN") or "").strip()
            or str(os.getenv("PRODUCTION_TICKET_DB_DSN") or "").strip()
            or str(os.getenv("TICKET_DB_DSN") or "").strip()
        )
        missing = [
            name
            for name, value in (
                ("BILLING_AUTOMATION_SMTP_HOST", smtp_host),
                ("BILLING_AUTOMATION_SMTP_USERNAME", sender),
                ("BILLING_AUTOMATION_SMTP_PASSWORD", smtp_password),
                ("PRODUCTION_TICKET_DB_DSN", db_dsn),
            )
            if not value
        ]
        if missing:
            raise AutomationTestScenarioError(
                f"scenario engine is not configured: missing {', '.join(missing)}"
            )

        def _int_env(name: str, default: int) -> int:
            try:
                parsed = int(str(os.getenv(name) or "").strip())
            except (TypeError, ValueError):
                return default
            return parsed if parsed > 0 else default

        return cls(
            smtp_host=smtp_host,
            smtp_port=_int_env("BILLING_AUTOMATION_SMTP_PORT", 465),
            sender=sender,
            smtp_password=smtp_password,
            imap_host=str(os.getenv("AUTOMATION_TEST_IMAP_HOST") or "imap.163.com").strip(),
            imap_port=_int_env("AUTOMATION_TEST_IMAP_PORT", 993),
            db_dsn=db_dsn,
            db_schema=str(os.getenv("TICKET_DB_SCHEMA") or "supportportal").strip() or "supportportal",
            subject_tag=str(
                os.getenv("AUTOMATION_TEST_TICKET_SUBJECT_TAG") or DEFAULT_SUBJECT_TAG
            ).strip(),
            turn_timeout_min=_int_env("AUTOMATION_TEST_TURN_TIMEOUT_MIN", DEFAULT_TURN_TIMEOUT_MIN),
            approval_timeout_min=_int_env(
                "AUTOMATION_TEST_APPROVAL_TIMEOUT_MIN", DEFAULT_APPROVAL_TIMEOUT_MIN
            ),
            poll_interval_seconds=_int_env(
                "AUTOMATION_TEST_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS
            ),
            listener=listener,
            should_cancel=should_cancel,
        )

    # -- observability -----------------------------------------------------

    def emit(self, kind: str, data: dict[str, Any]) -> None:
        if self.listener is not None:
            self.listener(kind, data)

    def info(self, message: str) -> None:
        self.emit("info", {"message": message})

    def record(self, ctx: ScenarioContext, step: str, ok: bool, detail: str = "") -> None:
        entry = ScenarioStep(step, "PASS" if ok else "FAIL", detail)
        self.steps.append(entry)
        self.emit("step", entry.as_dict())
        self.info(f"[{ctx.scenario_id}] {step}: {entry.status}" + (f" — {detail}" if detail else ""))
        if not ok:
            # A failed expectation aborts the scenario: later waits would only
            # produce confusing timeouts.
            raise AssertionError(f"{step} failed: {detail}")

    # -- I/O (instance methods so tests can script them) ---------------------

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def db_query(self, sql: str, params: tuple) -> list[dict]:
        with psycopg.connect(
            self.db_dsn,
            connect_timeout=10,
            options=f"-c search_path={self.db_schema},public",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                names = [item.name for item in cursor.description]
                return [dict(zip(names, row)) for row in cursor.fetchall()]

    def send_email(
        self, subject: str, body: str, to_address: str, headers: dict[str, str] | None = None
    ) -> None:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to_address
        message["Subject"] = subject
        for key, value in (headers or {}).items():
            message[key] = value
        message.set_content(body)
        with smtplib.SMTP_SSL(
            self.smtp_host, self.smtp_port, timeout=20, context=ssl.create_default_context()
        ) as server:
            server.login(self.sender, self.smtp_password)
            server.send_message(message)
        self.info(f"email sent → {to_address} | {subject}")

    def imap_connect(self) -> imaplib.IMAP4_SSL:
        imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        # 163 IMAP requires the RFC2971 ID command BEFORE login, otherwise
        # it answers NO to every SELECT/SEARCH.
        try:
            imap._simple_command(
                "ID", '("name" "supportportal-scenario-driver" "contact" "xieziling97@163.com")'
            )
        except Exception:  # noqa: BLE001 - ID is advisory on other servers
            pass
        imap.login(self.sender, self.smtp_password)
        return imap

    def imap_find_notification(self, ticket_id: str, since_date: str) -> dict[str, str] | None:
        with self.imap_connect() as imap:
            status, _ = imap.select("INBOX", readonly=True)
            if status != "OK":
                raise RuntimeError(f"IMAP select INBOX failed: {status}")
            status, data = imap.search(None, f'(SINCE "{since_date}" SUBJECT "{ticket_id}")')
            if status != "OK" or not data or not data[0]:
                return None
            message_ids = data[0].split()
            for num in reversed(message_ids[-5:]):
                status, fetched = imap.fetch(num, "(RFC822.HEADER)")
                if status != "OK" or not fetched or not fetched[0]:
                    continue
                raw = fetched[0][1]
                parsed = email.message_from_bytes(raw, policy=email.policy.default)
                subject = str(parsed.get("Subject") or "")
                # Only Zendesk notifications reference the ticket id in subject.
                if "zendesk" not in str(parsed.get("From") or "").lower() and ticket_id not in subject:
                    continue
                return {
                    "message_id": str(parsed.get("Message-ID") or "").strip(),
                    "references": str(parsed.get("References") or "").strip(),
                    "reply_to": str(parsed.get("Reply-To") or parsed.get("From") or "").strip(),
                    "subject": subject,
                }
        return None

    # -- polling ------------------------------------------------------------

    def wait_for(self, description: str, probe: Callable[[], Any], timeout_seconds: int):
        """Poll probe() until it returns non-None, the timeout expires, or cancelled."""
        deadline = time.monotonic() + timeout_seconds
        last_error = ""
        attempt = 0
        while time.monotonic() < deadline:
            if self.should_cancel():
                raise ScenarioCancelled(description)
            attempt += 1
            try:
                value = probe()
                if value is not None:
                    return value
                last_error = ""
            except ScenarioCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - keep polling on transient errors
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt % 3 == 0:
                waited = int(timeout_seconds - (deadline - time.monotonic()))
                suffix = f" (last error: {last_error})" if last_error else ""
                self.emit("waiting", {"description": description, "waited_seconds": waited, "last_error": last_error})
                self.info(f"… waiting for {description} ({waited}s elapsed){suffix}")
            self.sleep(self.poll_interval_seconds)
        raise TimeoutError(f"timed out waiting for {description}; {last_error or 'condition never met'}")

    # -- pipeline actions -----------------------------------------------------

    def tagged(self, subject: str) -> str:
        if self.subject_tag and not subject.startswith(self.subject_tag):
            return f"{self.subject_tag}{subject}"
        return subject

    def start_ticket(self, ctx: ScenarioContext, subject: str, body: str) -> None:
        ctx.subject = self.tagged(subject)
        ctx.turn_started_at = now_utc()
        self.emit("ticket_started", {"subject": ctx.subject})
        self.send_email(ctx.subject, body, ZENDESK_SUPPORT_ADDRESS)

    def find_case(self, ctx: ScenarioContext):
        since = (ctx.turn_started_at - timedelta(minutes=5)).isoformat()

        def probe():
            rows = self.db_query(
                "SELECT account_case_id, client_ticket_id, zendesk_ticket_id, title "
                "FROM support_account_cases "
                "WHERE processing_profile = 'production' AND title = %s "
                "AND created_at >= %s ORDER BY created_at DESC LIMIT 1",
                (ctx.subject, since),
            )
            return rows[0] if rows else None

        case = self.wait_for(
            "production case creation (n8n intake)", probe, self.turn_timeout_min * 60
        )
        ctx.account_case_id = case["account_case_id"]
        ctx.client_ticket_id = case["client_ticket_id"]
        ctx.zendesk_ticket_id = str(case["zendesk_ticket_id"] or "")
        self.emit(
            "ticket_linked",
            {
                "subject": ctx.subject,
                "zendesk_ticket_id": ctx.zendesk_ticket_id,
                "account_case_id": ctx.account_case_id,
                "client_ticket_id": ctx.client_ticket_id,
            },
        )
        self.info(
            f"case linked: {ctx.account_case_id} | zendesk #{ctx.zendesk_ticket_id} "
            f"({ZENDESK_TICKET_URL}/{ctx.zendesk_ticket_id})"
        )

    def case_row(self, ctx: ScenarioContext) -> dict:
        rows = self.db_query(
            "SELECT execution_action, automation_status, internal_email_send_status, "
            "zendesk_ticket_status, automation_context "
            "FROM support_account_cases WHERE account_case_id = %s",
            (ctx.account_case_id,),
        )
        return rows[0] if rows else {}

    def wait_case_field(self, ctx: ScenarioContext, field_name: str, expected: str, step: str) -> None:
        def probe():
            row = self.case_row(ctx)
            if str(row.get(field_name) or "") == expected:
                return row
            return None

        try:
            self.wait_for(f"{field_name}={expected}", probe, self.turn_timeout_min * 60)
            self.record(ctx, step, True, f"{field_name}={expected}")
        except TimeoutError as exc:
            row = self.case_row(ctx)
            self.record(
                ctx, step, False,
                f"{exc}; current {field_name}={row.get(field_name)!r}",
            )
            raise

    def wait_reply_intent(self, ctx: ScenarioContext, expected_intents: set[str], step: str) -> dict:
        since = (ctx.turn_started_at - timedelta(minutes=2)).isoformat()

        def probe():
            rows = self.db_query(
                "SELECT status, payload->>'reply_intent' AS reply_intent, "
                "(payload->>'close_after_publish') AS close_after_publish "
                "FROM support_account_reply_jobs "
                "WHERE ticket_id = %s AND created_at >= %s "
                "ORDER BY created_at DESC LIMIT 1",
                (ctx.client_ticket_id, since),
            )
            if not rows:
                return None
            job = rows[0]
            if job["status"] in {"published", "failed", "manual_attention", "cancelled"}:
                return job
            return None

        job = self.wait_for(
            f"reply job (expect {'|'.join(sorted(expected_intents))})",
            probe,
            self.turn_timeout_min * 60,
        )
        intent = str(job.get("reply_intent") or "")
        published = job["status"] == "published"
        ok = intent in expected_intents and published
        self.record(
            ctx, step, ok,
            f"intent={intent} status={job['status']} close={job.get('close_after_publish')}",
        )
        if not ok:
            raise AssertionError(f"unexpected reply job: intent={intent} status={job['status']}")

    def wait_suspension_state(self, ctx: ScenarioContext, expected: str, step: str) -> None:
        def probe():
            row = self.case_row(ctx)
            context = row.get("automation_context") or {}
            if isinstance(context, str):
                try:
                    context = json.loads(context)
                except json.JSONDecodeError:
                    context = {}
            workflow = context.get("account_suspension_contact_workflow") or {}
            if str(workflow.get("state") or "") == expected:
                return workflow
            return None

        try:
            self.wait_for(f"suspension state={expected}", probe, self.turn_timeout_min * 60)
            self.record(ctx, step, True, f"state={expected}")
        except TimeoutError as exc:
            self.record(ctx, step, False, str(exc))
            raise

    def wait_event(self, ctx: ScenarioContext, event_type: str, step: str, timeout_min: int | None = None) -> None:
        since = (ctx.turn_started_at - timedelta(minutes=2)).isoformat()
        timeout = (timeout_min or self.turn_timeout_min) * 60

        def probe():
            rows = self.db_query(
                "SELECT id FROM support_ticket_events "
                "WHERE ticket_id = %s AND event_type = %s AND created_at >= %s LIMIT 1",
                (ctx.client_ticket_id, event_type, since),
            )
            return rows[0] if rows else None

        try:
            self.wait_for(f"event {event_type}", probe, timeout)
            self.record(ctx, step, True, event_type)
        except TimeoutError as exc:
            self.record(ctx, step, False, str(exc))
            raise

    def next_customer_turn(self, ctx: ScenarioContext, body: str) -> None:
        ctx.turn_started_at = now_utc()
        since_date = (ctx.turn_started_at - timedelta(days=1)).strftime("%d-%b-%Y")
        notification = self.wait_for(
            "Zendesk notification email in IMAP inbox",
            lambda: self.imap_find_notification(ctx.zendesk_ticket_id, since_date),
            timeout_seconds=min(8 * 60, self.turn_timeout_min * 60),
        ) if ctx.zendesk_ticket_id else None
        if notification:
            headers = {}
            if notification["message_id"]:
                headers["In-Reply-To"] = notification["message_id"]
                references = " ".join(
                    part for part in (notification["references"], notification["message_id"]) if part
                )
                headers["References"] = references
            self.send_email(
                notification["subject"], body, notification["reply_to"], headers
            )
        else:
            # Blind fallback: Zendesk plus-addressing routes to the ticket.
            self.info("no notification found in inbox; using plus-address fallback")
            self.send_email(
                f"Re: {ctx.subject}",
                body,
                f"support+{ctx.zendesk_ticket_id}@agoraio.zendesk.com",
            )

    def wait_manual_approval(self, ctx: ScenarioContext, feature_label: str) -> None:
        ctx.turn_started_at = now_utc()
        self.emit(
            "approval_required",
            {
                "zendesk_ticket_id": ctx.zendesk_ticket_id,
                "zendesk_ticket_url": f"{ZENDESK_TICKET_URL}/{ctx.zendesk_ticket_id}",
                "feature_label": feature_label,
                "suggested_reply": f"{feature_label} is enabled for this app.",
                "internal_email_subject_prefix": f"[Enablement Request] {feature_label}",
                "timeout_min": self.approval_timeout_min,
            },
        )
        self.wait_event(
            ctx,
            "enablement_internal_resolution_received",
            "internal approval received",
            timeout_min=self.approval_timeout_min,
        )
        self.emit("approval_received", {})

    # -- scenarios ---------------------------------------------------------

    def run_e1(self) -> None:
        ctx = ScenarioContext("E1")
        self.start_ticket(
            ctx,
            "Please enable Media Relay for our project",
            "Hello Agora team,\n\n"
            "Please enable Media Relay from your end for our project.\n\n"
            f"App ID: {ENABLEMENT_APP_ID}\n\n"
            "We are building a live event platform and need Media Relay to bridge presenters "
            "between two channels. Thank you.",
        )
        self.find_case(ctx)
        row = self.case_row(ctx)
        self.record(
            ctx, "routed to enablement",
            row.get("execution_action") == "enablement",
            f"execution_action={row.get('execution_action')!r}",
        )
        self.wait_case_field(ctx, "internal_email_send_status", "sent", "internal handoff email sent")
        self.wait_reply_intent(ctx, {"submission_confirmation"}, "submission confirmation reply")
        self.wait_manual_approval(ctx, "Media Relay")
        self.wait_reply_intent(
            ctx, {"enablement_completed_and_close"}, "completion reply published"
        )
        self.wait_case_field(ctx, "zendesk_ticket_status", "solved", "ticket solved + case closed")

    def run_e2(self) -> None:
        ctx = ScenarioContext("E2")
        self.start_ticket(
            ctx,
            "Could you enable Media Relay for our project",
            "Hello Agora team,\n\n"
            "Could you enable Media Relay for our project? We need it to bridge presenters "
            "between two channels.",
        )
        self.find_case(ctx)
        self.wait_reply_intent(ctx, {"request_missing_information"}, "asks for App ID")
        self.next_customer_turn(ctx, "What is the App ID? I don't know where to find it.")
        self.wait_reply_intent(ctx, {"rag_fallback_answer"}, "RAG fallback answers the question")
        self.next_customer_turn(ctx, f"Found it. My App ID is {ENABLEMENT_APP_ID}.")
        self.wait_case_field(ctx, "internal_email_send_status", "sent", "internal handoff email sent")
        self.wait_reply_intent(ctx, {"submission_confirmation"}, "submission confirmation reply")
        self.wait_manual_approval(ctx, "Media Relay")
        self.wait_reply_intent(
            ctx, {"enablement_completed_and_close"}, "completion reply published"
        )
        self.wait_case_field(ctx, "zendesk_ticket_status", "solved", "ticket solved + case closed")

    def run_f1(self) -> None:
        ctx = ScenarioContext("F1")
        self.start_ticket(
            ctx,
            "Account flagged for suspicious activity",
            "Hello,\n\n"
            "Our Agora account was flagged for suspicious activity and is blocked. "
            "Please help us get it reviewed.",
        )
        self.find_case(ctx)
        row = self.case_row(ctx)
        self.record(
            ctx, "routed to fraud_account",
            row.get("execution_action") == "fraud_account",
            f"execution_action={row.get('execution_action')!r}",
        )
        self.wait_reply_intent(ctx, {"request_missing_information"}, "asks for review information")
        self.next_customer_turn(ctx, FRAUD_FULL_INFO_BODY)
        self.wait_case_field(ctx, "internal_email_send_status", "sent", "internal handoff email sent")
        self.wait_reply_intent(ctx, {"fraud_handoff_confirmation"}, "24h handoff reply published")
        self.wait_event(ctx, "zendesk_fraud_review_handoff", "assigned to fraud reviewer")
        row = self.case_row(ctx)
        self.record(
            ctx, "ticket NOT auto-solved",
            str(row.get("zendesk_ticket_status") or "") not in {"solved", "closed"},
            f"zendesk_ticket_status={row.get('zendesk_ticket_status')!r}",
        )

    def run_s1(self) -> None:
        ctx = ScenarioContext("S1")
        self.start_ticket(
            ctx,
            "Account suspended after balance ran out",
            "Hello,\n\n"
            "Our Agora account is suspended and the console says the account has been stopped "
            "after our balance ran out. We topped up yesterday but the account is still not "
            "accessible.\n\n"
            "Please help restore the account.",
        )
        self.find_case(ctx)
        row = self.case_row(ctx)
        self.record(
            ctx, "routed to account_suspension",
            row.get("execution_action") == "account_suspension",
            f"execution_action={row.get('execution_action')!r}",
        )
        self.wait_suspension_state(ctx, "awaiting_contact_confirmation", "asks for contact email")
        self.wait_reply_intent(
            ctx, {"account_suspension_contact_confirmation_request"}, "contact confirmation reply"
        )
        self.next_customer_turn(ctx, "Yes, please use xieziling97@163.com for the relevant team.")
        self.wait_case_field(ctx, "internal_email_send_status", "sent", "internal handoff email sent")
        self.wait_reply_intent(
            ctx, {"account_suspension_handoff_and_close"}, "closing reply with 24h commitment"
        )
        self.wait_case_field(ctx, "zendesk_ticket_status", "solved", "ticket solved + case closed")
        self.wait_suspension_state(ctx, "closed", "suspension workflow closed")

    SCENARIOS: dict[str, dict[str, Any]] = {
        "E1": {
            "label": "Enablement happy path",
            "description": "AppID provided → confirmation → manual approval → enabled + solved",
            "run": run_e1,
        },
        "E2": {
            "label": "Enablement missing App ID + RAG",
            "description": "ask App ID → what-is-AppID (RAG fallback) → provide → approval → solved",
            "run": run_e2,
        },
        "F1": {
            "label": "Fraud review",
            "description": "ask info → provide → handoff email + 24h reply + assign reviewer, NOT solved",
            "run": run_f1,
        },
        "S1": {
            "label": "Account suspension",
            "description": "ask contact email → confirm → handoff + closing reply + solved",
            "run": run_s1,
        },
    }

    ACTIVE_STATUSES = ("queued", "running", "waiting_approval")

    def run_scenario(self, scenario_id: str) -> list[ScenarioStep]:
        scenario = self.SCENARIOS.get(scenario_id)
        if scenario is None:
            raise AutomationTestScenarioError(f"unknown scenario: {scenario_id}")
        self.steps = []
        scenario["run"](self)
        return list(self.steps)

    def all_passed(self) -> bool:
        return bool(self.steps) and all(step.status == "PASS" for step in self.steps)

    # -- connectivity --------------------------------------------------------

    def connectivity_check(self) -> dict[str, str]:
        """Verify DB/SMTP/IMAP reachability without sending anything."""
        results: dict[str, str] = {}
        rows = self.db_query("SELECT COUNT(*) AS n FROM support_account_cases", ())
        results["db"] = f"ok (support_account_cases rows={rows[0]['n']})"
        with smtplib.SMTP_SSL(
            self.smtp_host, self.smtp_port, timeout=15, context=ssl.create_default_context()
        ) as server:
            server.login(self.sender, self.smtp_password)
        results["smtp"] = "ok"
        with self.imap_connect() as imap:
            status, _ = imap.select("INBOX", readonly=True)
            if status != "OK":
                raise RuntimeError(f"IMAP select INBOX failed: {status}")
        results["imap"] = "ok"
        return results
