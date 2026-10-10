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
import re
import smtplib
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Callable

import psycopg

from backend.services import automation_test_mail

# imaplib does not know the non-standard RFC2971 ID command; 163 Coremail
# refuses SELECT/SEARCH (NO) unless it is sent before login.
imaplib.Commands["ID"] = ("NONAUTH", "AUTH", "SELECTED")

ZENDESK_TICKET_URL = "https://agoraio.zendesk.com/agent/tickets"
ZENDESK_SUPPORT_ADDRESS = "support@agoraio.zendesk.com"
ZENDESK_NOTIFICATION_DOMAIN = "agoraio.zendesk.com"
DEFAULT_SUBJECT_TAG = "[zac test] "
DEFAULT_TURN_TIMEOUT_MIN = 20
DEFAULT_APPROVAL_TIMEOUT_MIN = 45
DEFAULT_RELAY_TIMEOUT_MIN = 240
DEFAULT_POLL_INTERVAL_SECONDS = 20

ENABLEMENT_APP_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"

FRAUD_PARTIAL_INFO_BODY = (
    "Thanks. I only have part of the review information available right now.\n\n"
    "Account type: Enterprise\n"
    "Name: Zac Tester\n"
    "Contact email: zac.tester@example.com"
)

DETAILED_INVOICE_BODY = (
    "Hello Agora team,\n\n"
    "Please send me the detailed invoice for the transaction below.\n\n"
    "Issue date: 6 May 2026\n"
    "Transaction ID: 1104245232004173824\n"
    "Amount: USD 705.97\n\n"
    "Thank you."
)


def _enablement_request_body() -> str:
    return (
        "Hello Agora team,\n\n"
        "Please enable Media Relay from your end for our project.\n\n"
        f"App ID: {ENABLEMENT_APP_ID}\n\n"
        "We are building a live event platform and need Media Relay to bridge presenters "
        "between two channels. Thank you."
    )


# E3 full-lifecycle App IDs (user-provided fixtures, 2026-09-24):
# invalid is 31 hex chars (fails the 32-hex fullmatch), not_found is a valid
# format with no archer project behind it, valid is the enableable test
# project the Mac pilot really enables in the final leg.
E3_APPID_INVALID = "8cb7aea984c4457daad802e6960e247"
E3_APPID_NOT_FOUND = "8cb7aea984c4457daad802e6960e2475"
E3_APPID_VALID = "4b7634a0d0f1418b8135918292f6a507"

E3_NUDGE_BODY = (
    "Hi May,\n\n"
    "Thank you for the update.\n\n"
    "We understand the review and activation process. If there is any possibility of getting "
    "the Media Relay enablement completed sooner, we would really appreciate it, as we are "
    "currently working on this feature for our SDNXT live platform.\n\n"
    "We appreciate your help and look forward to your update."
)


def _ask_appid_content_check(content: str) -> str | None:
    """Acceptance check: the missing-App-ID ask must request the App ID."""
    if "app id" not in str(content or "").casefold():
        return "ask does not mention App ID"
    return None


def _knowledge_answer_content_check(content: str) -> str | None:
    """Acceptance check (turn 2): the in-session knowledge answer must
    answer the App ID question and carry the trusted reference block."""
    lowered = str(content or "").casefold()
    if "app id" not in lowered:
        return "answer does not mention App ID"
    if "references:" not in lowered:
        return "answer carries no trusted references block"
    if "docs.agora.io" not in lowered:
        return "answer references a non-docs source"
    return None


_NO_ACCELERATION_CLAIM_RE = re.compile(
    r"(?i)\b(?:will|can|we'll|we will|have)\s+(?:expedite|prioriti[sz]e|speed|accelerate|fast[- ]track)\b"
    r"|sooner than|right away|immediately"
)


def _progress_answer_content_check(content: str) -> str | None:
    """Acceptance check (turn 5): the progress answer states the recorded
    review status and promises no acceleration or completion date."""
    lowered = str(content or "").casefold()
    if not any(word in lowered for word in ("review", "in progress", "under review")):
        return "answer does not state the review status"
    claim = _NO_ACCELERATION_CLAIM_RE.search(str(content or ""))
    if claim:
        return f"acceleration promise: {claim.group(0)}"
    return None


def _appid_not_found_content_check(content: str) -> str | None:
    """Acceptance check (turn 6): the dedicated not-found reply asks the
    customer to check the App ID and promises no enablement."""
    lowered = str(content or "").casefold()
    if "app id" not in lowered:
        return "reply does not mention the App ID"
    if not any(word in lowered for word in ("not find", "not found", "no project", "check", "verify", "double-check")):
        return "reply does not ask the customer to check the App ID"
    if any(word in lowered for word in ("enabled", "activated", "turned on")):
        return "reply claims enablement"
    return None


def _submission_confirmation_content_check(content: str) -> str | None:
    """Acceptance check: confirmation must mention review and promise no deadline."""
    lowered = str(content or "").casefold()
    if "review" not in lowered:
        return "confirmation does not mention review"
    close_claim = _no_affirmative_close_claim(content)
    if close_claim:
        return close_claim
    return None


def _enablement_enabled_content_check(content: str) -> str | None:
    """Acceptance check: final reply must state enablement and close the case."""
    lowered = str(content or "").casefold()
    if "media relay" not in lowered:
        return "completion does not mention media relay"
    if not any(word in lowered for word in ("enabled", "activated", "turned on", "provisioned")):
        return "completion does not state enablement"
    if not any(word in lowered for word in ("clos", "archiv", "new ticket")):
        return "completion does not close the case"
    return None

_TWENTY_FOUR_HOURS_RE = re.compile(r"(?i)\b24\s*[- ]?\s*hours?\b|\b24h\b")
_CLOSE_CLAIM_WORD_RE = re.compile(r"(?i)\b(?:clos\w*|archiv\w*|reop\w*)\b")
_NEGATED_CLAUSE_RE = re.compile(r"(?i)\b(?:not|never|won't|don't|cannot|can't)\b")


def _no_affirmative_close_claim(content: str) -> str | None:
    """Acceptance check: no affirmative close/archive/reopen claim (negations pass)."""
    for clause in re.split(r"(?<=[.!?])\s+|[;\n]+", str(content or "").casefold()):
        clause = clause.strip()
        if not clause or "?" in clause:
            continue
        if _NEGATED_CLAUSE_RE.search(clause):
            continue
        if _CLOSE_CLAIM_WORD_RE.search(clause):
            return f"affirmative close claim: {clause[:80]}"
    return None


def _fraud_handoff_content_check(content: str) -> str | None:
    if not _TWENTY_FOUR_HOURS_RE.search(content):
        return "24-hour contact promise not stated"
    return None


def _suspension_first_reply_content_check(content: str) -> str | None:
    lowered = str(content or "").casefold()
    if "email" not in lowered:
        return "contact email question not stated"
    if not _TWENTY_FOUR_HOURS_RE.search(content):
        return "24-hour contact promise not stated"
    return _no_affirmative_close_claim(content)


def _suspension_closing_content_check(content: str) -> str | None:
    lowered = str(content or "").casefold()
    if not re.search(r"\b(?:handed|passed|escalated|forwarded|relevant team)\b", lowered):
        return "handoff confirmation not stated"
    if not _TWENTY_FOUR_HOURS_RE.search(content):
        return "24-hour contact promise not stated"
    return _no_affirmative_close_claim(content)


def _enablement_completion_content_check(content: str) -> str | None:
    lowered = str(content or "").casefold()
    if "media relay" not in lowered:
        return "media relay not mentioned"
    if not re.search(r"\b(?:enabled|activated|provisioned)\b|turned\s+on", lowered):
        return "enabled state not stated"
    if not re.search(r"\b(?:clos\w+|archiv\w+)\b", lowered):
        return "case closing not stated"
    if "new ticket" not in lowered:
        return "new-ticket invitation not stated"
    return None


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
    # Per-run binding watermarks (p2-178): every wait must observe NEW
    # entities for the current turn — a turn, reply job, relay request, or
    # delivered comment from a PREVIOUS leg must never satisfy the wait.
    # "seen" grows as waits observe entities; "baseline" snapshots it at each
    # customer-turn boundary, so two waits inside the SAME turn may observe
    # the same entity while a later turn cannot reuse it.
    seen_turn_ids: set = field(default_factory=set)
    seen_reply_job_ids: set = field(default_factory=set)
    seen_comment_ids: set = field(default_factory=set)
    baseline_turn_ids: set = field(default_factory=set)
    baseline_reply_job_ids: set = field(default_factory=set)
    baseline_comment_ids: set = field(default_factory=set)
    last_relay_request_version: int = 0
    bound_relay_request_id: str = ""
    # Current customer turn identity (R3): the posted Zendesk comment id and
    # its creation timestamp. Outcome waits bind to the turn/job this comment
    # TRIGGERED (turn.event_id / job.trigger_message_created_at), so a late
    # delivery from a previous turn can never satisfy the current turn.
    last_customer_comment_id: str = ""
    last_customer_comment_at: str = ""
    # Requester identity of the ticket (R5): customer-authored comments in the
    # ticket comment listing are the persisted trigger records a reply job's
    # timestamp must be uniquely attributed against.
    customer_requester_id: str = ""

    def stamp_turn_baseline(self) -> None:
        self.baseline_turn_ids = set(self.seen_turn_ids)
        self.baseline_reply_job_ids = set(self.seen_reply_job_ids)
        self.baseline_comment_ids = set(self.seen_comment_ids)


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
        processing_profile: str = "production",
        subject_tag: str = DEFAULT_SUBJECT_TAG,
        turn_timeout_min: int = DEFAULT_TURN_TIMEOUT_MIN,
        approval_timeout_min: int = DEFAULT_APPROVAL_TIMEOUT_MIN,
        relay_timeout_min: int = DEFAULT_RELAY_TIMEOUT_MIN,
        customer_turn_transport: str = "email",
        zendesk_auth: str = "",
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
        self.processing_profile = processing_profile
        self.subject_tag = subject_tag
        self.turn_timeout_min = turn_timeout_min
        self.approval_timeout_min = approval_timeout_min
        self.relay_timeout_min = relay_timeout_min
        self.customer_turn_transport = str(customer_turn_transport or "email").strip() or "email"
        self.zendesk_auth = str(zendesk_auth or "").strip()
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
        # The engine defaults to the production ticket DB contract. In the
        # api_production container TICKET_DB_DSN already IS production, and
        # PRODUCTION_TICKET_DB_DSN is present via the root .env everywhere;
        # prefer the explicit production key so a staging api process can
        # never drive scenarios against the staging DB. Preproduction runs
        # override AUTOMATION_TEST_DB_DSN + TICKET_DB_SCHEMA +
        # AUTOMATION_TEST_PROCESSING_PROFILE together.
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
            processing_profile=(
                str(os.getenv("AUTOMATION_TEST_PROCESSING_PROFILE") or "production").strip()
                or "production"
            ),
            subject_tag=str(
                os.getenv("AUTOMATION_TEST_TICKET_SUBJECT_TAG") or DEFAULT_SUBJECT_TAG
            ).strip(),
            turn_timeout_min=_int_env("AUTOMATION_TEST_TURN_TIMEOUT_MIN", DEFAULT_TURN_TIMEOUT_MIN),
            approval_timeout_min=_int_env(
                "AUTOMATION_TEST_APPROVAL_TIMEOUT_MIN", DEFAULT_APPROVAL_TIMEOUT_MIN
            ),
            relay_timeout_min=_int_env(
                "AUTOMATION_TEST_RELAY_TIMEOUT_MIN", DEFAULT_RELAY_TIMEOUT_MIN
            ),
            customer_turn_transport=(
                str(os.getenv("AUTOMATION_TEST_CUSTOMER_TURN_TRANSPORT") or "email").strip()
                or "email"
            ),
            zendesk_auth=str(os.getenv("AUTOMATION_TEST_ZENDESK_AUTH") or "").strip(),
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
        # Shared staged delivery: an accepted DATA submission is a success
        # even when the subsequent QUIT fails, and transport-level failures
        # raise with an unknown outcome instead of a plain SMTP error.
        automation_test_mail.deliver_smtp_message(
            message=message,
            host=self.smtp_host,
            port=self.smtp_port,
            username=self.sender,
            password=self.smtp_password,
            timeout=20,
        )
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
                # Only Zendesk notifications may drive the scenario engine:
                # the sender domain must be Zendesk and the reply target must
                # be the ticket's plus-address (or the plain support address).
                # Anything else is treated as an unrelated inbox message.
                sender_address = email.utils.parseaddr(str(parsed.get("From") or ""))[1].strip().lower()
                if not sender_address.endswith(f"@{ZENDESK_NOTIFICATION_DOMAIN}"):
                    continue
                reply_to_address = email.utils.parseaddr(
                    str(parsed.get("Reply-To") or parsed.get("From") or "")
                )[1].strip().lower()
                allowed_reply_to = {
                    ZENDESK_SUPPORT_ADDRESS.lower(),
                    f"support+{str(ticket_id).strip().lower()}@{ZENDESK_NOTIFICATION_DOMAIN}",
                }
                if reply_to_address not in allowed_reply_to:
                    continue
                return {
                    "message_id": str(parsed.get("Message-ID") or "").strip(),
                    "references": str(parsed.get("References") or "").strip(),
                    "reply_to": reply_to_address,
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
        ctx.stamp_turn_baseline()
        self.emit("ticket_started", {"subject": ctx.subject})
        self.send_email(ctx.subject, body, ZENDESK_SUPPORT_ADDRESS)

    def find_case(self, ctx: ScenarioContext):
        since = (ctx.turn_started_at - timedelta(minutes=5)).isoformat()

        def probe():
            rows = self.db_query(
                "SELECT account_case_id, client_ticket_id, zendesk_ticket_id, title "
                "FROM support_account_cases "
                "WHERE processing_profile = %s AND title = %s "
                "AND created_at >= %s ORDER BY created_at DESC LIMIT 1",
                (self.processing_profile, ctx.subject, since),
            )
            return rows[0] if rows else None

        case = self.wait_for(
            f"{self.processing_profile} case creation (n8n intake)", probe, self.turn_timeout_min * 60
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
            "internal_email_send_reason, "
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
                "SELECT job_id, status, payload->>'reply_intent' AS reply_intent, "
                "(payload->>'close_after_publish') AS close_after_publish "
                "FROM support_account_reply_jobs "
                "WHERE ticket_id = %s AND created_at >= %s "
                "ORDER BY created_at DESC LIMIT 1",
                (ctx.client_ticket_id, since),
            )
            if not rows:
                return None
            job = rows[0]
            job_id = str(job.get("job_id") or "")
            if job_id and job_id in ctx.baseline_reply_job_ids:
                return None
            if job["status"] in {"published", "failed", "manual_attention", "cancelled"}:
                return job
            return None

        job = self.wait_for(
            f"reply job (expect {'|'.join(sorted(expected_intents))})",
            probe,
            self.turn_timeout_min * 60,
        )
        ctx.seen_reply_job_ids.add(str(job.get("job_id") or ""))
        intent = str(job.get("reply_intent") or "")
        published = job["status"] == "published"
        ok = intent in expected_intents and published
        self.record(
            ctx, step, ok,
            f"intent={intent} status={job['status']} close={job.get('close_after_publish')}",
        )
        if not ok:
            raise AssertionError(f"unexpected reply job: intent={intent} status={job['status']}")
        return job

    def wait_published_reply_content(
        self,
        ctx: ScenarioContext,
        *,
        expected_intent: str,
        check: Callable[[str], str | None],
        step: str,
    ) -> str:
        """Wait for the latest published reply of an intent and run a
        scenario-side acceptance check on its content.

        These checks are acceptance-only: they validate live behaviour for the
        scenario run and are never wired back into the production publish
        gate.
        """
        since = (ctx.turn_started_at - timedelta(minutes=2)).isoformat()

        def probe():
            rows = self.db_query(
                "SELECT jobs.job_id, jobs.status, messages.content "
                "FROM support_account_reply_jobs jobs "
                "JOIN support_ticket_messages messages ON messages.ticket_id = jobs.ticket_id "
                "AND messages.meta->>'account_reply_job_id' = jobs.job_id "
                "WHERE jobs.ticket_id = %s AND jobs.created_at >= %s "
                "AND jobs.payload->>'reply_intent' = %s "
                "ORDER BY jobs.created_at DESC, messages.created_at DESC, messages.id DESC LIMIT 1",
                (ctx.client_ticket_id, since, expected_intent),
            )
            if not rows:
                return None
            job = rows[0]
            job_id = str(job.get("job_id") or "")
            if job_id and job_id in ctx.baseline_reply_job_ids:
                return None
            if job["status"] == "published":
                return job
            return None

        job = self.wait_for(
            f"published {expected_intent} reply content",
            probe,
            self.turn_timeout_min * 60,
        )
        ctx.seen_reply_job_ids.add(str(job.get("job_id") or ""))
        content = str(job.get("content") or "")
        problem = check(content)
        if problem:
            self.record(ctx, step, False, problem)
            raise AssertionError(f"invalid {expected_intent} reply content: {problem}")
        self.record(ctx, step, True, f"intent={expected_intent} content check passed")
        return content

    def reply_intent_count(self, ctx: ScenarioContext, reply_intent: str) -> int:
        rows = self.db_query(
            "SELECT COUNT(*) AS intent_count FROM support_account_reply_jobs "
            "WHERE ticket_id = %s AND payload->>'reply_intent' = %s",
            (ctx.client_ticket_id, reply_intent),
        )
        return int(rows[0].get("intent_count") or 0) if rows else 0

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

    def wait_event(
        self,
        ctx: ScenarioContext,
        event_type: str,
        step: str,
        timeout_min: int | None = None,
        expected_states: set[str] | None = None,
    ) -> None:
        since = (ctx.turn_started_at - timedelta(minutes=2)).isoformat()
        timeout = (timeout_min or self.turn_timeout_min) * 60

        def probe():
            rows = self.db_query(
                "SELECT id, payload FROM support_ticket_events "
                "WHERE ticket_id = %s AND event_type = %s AND created_at >= %s LIMIT 1",
                (ctx.client_ticket_id, event_type, since),
            )
            if not rows:
                return None
            if expected_states is None:
                return rows[0]
            payload = rows[0].get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            state = str((payload or {}).get("state") or "")
            return rows[0] if state in expected_states else None

        try:
            self.wait_for(f"event {event_type}", probe, timeout)
            self.record(ctx, step, True, event_type)
        except TimeoutError as exc:
            self.record(ctx, step, False, str(exc))
            raise

    def _zendesk_request(self, path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
        """Zendesk API call with the scenario's basic auth (API customer turns).

        The 163->Zendesk email hop is unreliable in the preproduction test
        setup (no requester notifications, plus-address replies never attach),
        so customer turns post a public comment authored by the requester
        directly via the API — the same event shape n8n comment sync consumes.
        """
        import base64
        import json as _json
        import urllib.request

        if not self.zendesk_auth:
            raise AutomationTestScenarioError(
                "zendesk_api customer turns require AUTOMATION_TEST_ZENDESK_AUTH"
            )
        url = f"https://agoraio.zendesk.com/api/v2{path}"
        data = _json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header(
            "Authorization",
            "Basic " + base64.b64encode(self.zendesk_auth.encode()).decode(),
        )
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
        return _json.loads(body) if body else {}

    def zendesk_customer_turn(self, ctx: ScenarioContext, body: str) -> dict:
        ctx.turn_started_at = now_utc()
        ctx.stamp_turn_baseline()
        ticket = self._zendesk_request(f"/tickets/{ctx.zendesk_ticket_id}.json")
        requester_id = (ticket.get("ticket") or {}).get("requester_id")
        if not requester_id:
            raise AutomationTestScenarioError(
                f"ticket {ctx.zendesk_ticket_id} has no requester_id"
            )
        response = self._zendesk_request(
            f"/tickets/{ctx.zendesk_ticket_id}.json",
            method="PUT",
            payload={
                "ticket": {
                    "comment": {
                        "body": body,
                        "public": True,
                        "author_id": requester_id,
                    }
                }
            },
        )
        comment_id = self._extract_created_comment_id(response)
        ctx.last_customer_comment_id = str(comment_id or "")
        audit = response.get("audit") if isinstance(response.get("audit"), dict) else {}
        ctx.last_customer_comment_at = str(audit.get("created_at") or "")
        ctx.customer_requester_id = str(requester_id or "")
        self.emit(
            "customer_turn_sent",
            {
                "transport": "zendesk_api",
                "zendesk_ticket_id": ctx.zendesk_ticket_id,
                "comment_id": comment_id,
            },
        )
        self.info(
            f"customer turn posted via Zendesk API as requester {requester_id} "
            f"(comment_id={comment_id})"
        )
        return {
            "transport": "zendesk_api",
            "requester_id": requester_id,
            "comment_id": comment_id,
        }

    @staticmethod
    def _extract_created_comment_id(response: dict) -> str:
        """Best-effort comment-id extraction from a Zendesk update response.

        Returns "" when the id cannot be located; scenario-side binding must
        treat an empty id as unverifiable rather than guessing.
        """
        if not isinstance(response, dict):
            return ""
        candidates = []
        audit = response.get("audit")
        if isinstance(audit, dict):
            for event in audit.get("events") or []:
                if isinstance(event, dict) and str(event.get("type") or "") == "Comment":
                    candidates.append(str(event.get("id") or ""))
        comment = (response.get("ticket") or {}).get("comment") if isinstance(response.get("ticket"), dict) else None
        if isinstance(comment, dict):
            candidates.append(str(comment.get("id") or ""))
        if isinstance(response.get("comment"), dict):
            candidates.append(str(response.get("comment").get("id") or ""))
        for candidate in candidates:
            if candidate:
                return candidate
        return ""

    def next_customer_turn(self, ctx: ScenarioContext, body: str) -> dict | None:
        if self.customer_turn_transport == "zendesk_api":
            return self.zendesk_customer_turn(ctx, body)
        ctx.turn_started_at = now_utc()
        ctx.stamp_turn_baseline()
        since_date = (ctx.turn_started_at - timedelta(days=1)).strftime("%d-%b-%Y")
        notification = None
        if ctx.zendesk_ticket_id:
            try:
                notification = self.wait_for(
                    "Zendesk notification email in IMAP inbox",
                    lambda: self.imap_find_notification(ctx.zendesk_ticket_id, since_date),
                    timeout_seconds=min(8 * 60, self.turn_timeout_min * 60),
                )
            except TimeoutError:
                self.info("Zendesk notification did not arrive; using plus-address fallback")
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

    def wait_manual_billing_invoice_reply(self, ctx: ScenarioContext) -> None:
        ctx.turn_started_at = now_utc()
        self.emit(
            "approval_required",
            {
                "zendesk_ticket_id": ctx.zendesk_ticket_id,
                "zendesk_ticket_url": f"{ZENDESK_TICKET_URL}/{ctx.zendesk_ticket_id}",
                "suggested_reply": "The detailed invoice is attached.",
                "internal_email_subject_prefix": "[Billing Request] Detailed invoice request",
                "attach_pdf": True,
                "timeout_min": self.approval_timeout_min,
            },
        )
        self.wait_event(
            ctx,
            "billing_internal_resolution_submitted",
            "internal invoice reply received",
            timeout_min=self.approval_timeout_min,
        )
        self.emit("approval_received", {})

    def wait_zendesk_delivery_delivered(self, ctx: ScenarioContext, step: str) -> None:
        def probe():
            rows = self.db_query(
                "SELECT status, zendesk_comment_id FROM support_account_zendesk_comment_deliveries "
                "WHERE account_case_id = %s AND target_status = 'solved' "
                "ORDER BY created_at DESC LIMIT 1",
                (ctx.account_case_id,),
            )
            if rows and str(rows[0].get("status") or "") == "delivered":
                return rows[0]
            return None

        try:
            row = self.wait_for(
                "zendesk delivery delivered (comment + PDF uploads)", probe, self.turn_timeout_min * 60
            )
            self.record(ctx, step, True, f"zendesk_comment_id={row.get('zendesk_comment_id')}")
        except TimeoutError as exc:
            self.record(ctx, step, False, str(exc))
            raise

    def wait_public_comment_delivered(self, ctx: ScenarioContext, step: str) -> None:
        """Wait for a delivered public comment with no ticket-status change.

        Preproduction confirmation replies deliver a public comment without a
        target_status transition (the ticket stays open while the enablement
        relay leg continues), so the solved-targeted wait above does not apply.
        """
        def probe():
            rows = self.db_query(
                "SELECT status, is_public, zendesk_comment_id "
                "FROM support_account_zendesk_comment_deliveries "
                "WHERE account_case_id = %s AND is_public = true "
                "ORDER BY created_at DESC LIMIT 1",
                (ctx.account_case_id,),
            )
            if not rows or str(rows[0].get("status") or "") != "delivered":
                return None
            comment_id = str(rows[0].get("zendesk_comment_id") or "")
            if comment_id and comment_id in ctx.baseline_comment_ids:
                return None
            return rows[0]

        try:
            row = self.wait_for(
                "public zendesk comment delivered", probe, self.turn_timeout_min * 60
            )
            ctx.seen_comment_ids.add(str(row.get("zendesk_comment_id") or ""))
            self.record(ctx, step, True, f"zendesk_comment_id={row.get('zendesk_comment_id')}")
        except TimeoutError as exc:
            self.record(ctx, step, False, str(exc))
            raise

    def wait_customer_reply_delivered(
        self, ctx: ScenarioContext, step: str,
        *, content_check: Callable[[str], str | None] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict:
        """Strict turn-outcome wait: a reply DELIVERED to the customer that
        was PRODUCED BY the current customer turn, whichever pipeline served
        it (hermes case draft, or persona reply job).

        Binding chain (R3/R4/R5): the customer comment posted for this turn binds
        the hermes turn via ``turn.event_id =
        zendesk:ticket:<id>:comment:<comment_id>``. For the reply-job path the
        ticket's customer-authored comments (the persisted trigger records,
        read from the Zendesk listing) are mapped to their created_at; a job
        binds ONLY when its ``trigger_message_created_at`` falls within ±1s of
        OUR comment's persisted created_at AND no other customer comment also
        falls within ±1s of it — a job from another comment (even inside the
        window) and same-second comments are refused, a missing comment id or
        unparseable created_at fails closed (never a local-clock fallback),
        and more than one distinct trigger in the window is an ambiguity we
        refuse to resolve. A DELIVERED comment from a previous turn arriving
        late never satisfies this wait, and superseded / cancelled / failed
        producers are rejected even when a delivery row exists for them.
        Terminal states stop the wait immediately instead of draining the
        turn timeout.
        """
        event_id = (
            f"zendesk:ticket:{ctx.zendesk_ticket_id}:comment:{ctx.last_customer_comment_id}"
            if ctx.last_customer_comment_id
            else ""
        )
        # Identity prerequisites (R4): the job path binds by the persisted
        # trigger_message_created_at, which only means anything against the
        # REAL created_at of the customer comment. Without the comment id or
        # a parseable created_at the wait fails closed — never a local-clock
        # fallback that would silently claim "bound".
        if not ctx.last_customer_comment_id:
            detail = (
                "cannot bind a reply to this turn: the posted customer comment id "
                "was not captured"
            )
            self.record(ctx, step, False, detail)
            raise AutomationTestScenarioError(detail)
        try:
            datetime.fromisoformat(
                ctx.last_customer_comment_at.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            detail = (
                "cannot bind a reply to this turn: the customer comment's persisted "
                "created_at is missing or unparseable; refusing local-clock fallback"
            )
            self.record(ctx, step, False, detail)
            raise AutomationTestScenarioError(detail)
        terminal_failure: list[str] = []

        def _customer_comment_clocks() -> dict[str, datetime]:
            """Persisted trigger records: the ticket's customer-authored
            comments (Zendesk listing, FULL pagination) mapped to their
            created_at. A reply job's trigger timestamp may only bind to our
            comment when NO other customer comment could have produced it —
            attribution is only valid over a confirmed-complete listing:
            pagination links are validated, malformed pages and unreadable
            listings fail instead of counting as 'no other comment'."""
            import urllib.parse

            comments_endpoint = f"/tickets/{ctx.zendesk_ticket_id}/comments.json"
            api_base = "https://agoraio.zendesk.com/api/v2"
            endpoint_parsed = urllib.parse.urlparse(api_base + comments_endpoint)
            next_url: str | None = f"{api_base}{comments_endpoint}?per_page=100"
            seen_urls: set[str] = set()
            clocks: dict[str, datetime] = {}
            pages = 0
            requester = str(ctx.customer_requester_id or "")
            while next_url is not None:
                parsed = urllib.parse.urlparse(next_url)
                if (
                    parsed.scheme != "https"
                    or parsed.netloc != endpoint_parsed.netloc
                    or parsed.path != endpoint_parsed.path
                    or next_url in seen_urls
                ):
                    raise AutomationTestScenarioError(
                        "cannot verify trigger identity: comment pagination link invalid"
                    )
                seen_urls.add(next_url)
                pages += 1
                if pages > 100:
                    raise AutomationTestScenarioError(
                        "cannot verify trigger identity: comment pagination did not terminate"
                    )
                try:
                    payload = self._zendesk_request(next_url[len(api_base):])
                except AutomationTestScenarioError:
                    raise
                except Exception as exc:  # noqa: BLE001 - listing is mandatory
                    raise AutomationTestScenarioError(
                        "cannot verify trigger identity: ticket comment listing "
                        f"unavailable ({type(exc).__name__})"
                    )
                entries = payload.get("comments") if isinstance(payload, dict) else None
                if not isinstance(entries, list) or any(
                    not isinstance(entry, dict) for entry in entries
                ):
                    raise AutomationTestScenarioError(
                        "cannot verify trigger identity: malformed comment listing page"
                    )
                for entry in entries:
                    cid = str(entry.get("id") or "")
                    author = str(entry.get("author_id") or "")
                    if requester and author and author != requester:
                        continue
                    if not cid:
                        continue
                    try:
                        clocks[cid] = datetime.fromisoformat(
                            str(entry.get("created_at") or "").replace("Z", "+00:00")
                        )
                    except ValueError:
                        raise AutomationTestScenarioError(
                            "cannot verify trigger identity: customer comment "
                            f"{cid} has an unparseable created_at"
                        )
                # Pagination terminator discipline (R7): the ONLY legitimate
                # end marker is next_page being JSON null; a missing key or
                # any non-string / blank value is a malformed response and
                # fails — it must never be read as "listing complete".
                if "next_page" not in payload:
                    raise AutomationTestScenarioError(
                        "cannot verify trigger identity: comment listing page "
                        "carries no next_page terminator"
                    )
                raw_next = payload.get("next_page")
                if raw_next is None:
                    next_url = None
                elif isinstance(raw_next, str):
                    if not raw_next.strip():
                        raise AutomationTestScenarioError(
                            "cannot verify trigger identity: blank comment "
                            "pagination link"
                        )
                    next_url = raw_next
                else:
                    raise AutomationTestScenarioError(
                        "cannot verify trigger identity: malformed comment "
                        f"pagination terminator (next_page is {type(raw_next).__name__})"
                    )
            return clocks

        try:
            clocks = _customer_comment_clocks()
        except AutomationTestScenarioError as exc:
            self.record(ctx, step, False, str(exc))
            raise
        if ctx.last_customer_comment_id not in clocks:
            detail = (
                "cannot verify trigger identity: our customer comment is not "
                "present in the ticket comment listing"
            )
            self.record(ctx, step, False, detail)
            raise AutomationTestScenarioError(detail)
        anchor = clocks[ctx.last_customer_comment_id]
        deadline = time.monotonic() + int(
            timeout_seconds
            if timeout_seconds is not None
            else getattr(self, "customer_reply_wait_timeout_seconds", None)
            or self.turn_timeout_min * 60
        )

        _TURN_TERMINAL = {"failed", "superseded", "cancelled", "human_review"}
        _JOB_TERMINAL = {"failed", "manual_attention", "cancelled"}
        _DRAFT_INVALID = {"superseded", "cancelled", "rejected", "prepare_failed"}

        def _bound_turn():
            if not event_id:
                return None
            rows = self.db_query(
                "SELECT turn_id, status, route FROM automation_hermes_agent_turns "
                "WHERE event_id = %s ORDER BY created_at DESC LIMIT 1",
                (event_id,),
            )
            return rows[0] if rows else None

        def _bound_draft_delivery(turn_id: str) -> dict | None:
            rows = self.db_query(
                "SELECT d.draft_id, d.status AS draft_status, d.content, "
                "dl.status AS delivery_status, dl.zendesk_comment_id "
                "FROM automation_hermes_case_drafts d "
                "LEFT JOIN support_account_zendesk_comment_deliveries dl "
                "ON dl.message_id = d.draft_id "
                "WHERE d.turn_id = %s ORDER BY d.created_at DESC LIMIT 1",
                (turn_id,),
            )
            if not rows:
                return None
            row = rows[0]
            draft_status = str(row.get("draft_status") or "")
            if draft_status in _DRAFT_INVALID:
                terminal_failure.append(
                    f"bound draft is {draft_status}; its delivery cannot answer "
                    f"this turn: {row.get('draft_id')}"
                )
                return None
            if str(row.get("delivery_status") or "") != "delivered":
                return None
            comment_id = str(row.get("zendesk_comment_id") or "")
            if not comment_id or comment_id in ctx.baseline_comment_ids:
                return None
            row["kind"] = "draft"
            row["content"] = row.get("content")
            return row

        def _bound_reply_job_delivery() -> dict | None:
            # Candidate jobs are those whose PERSISTED trigger timestamp
            # equals the customer comment's created_at within ±1s. A job
            # triggered by a different comment (even 3s away) is not a
            # candidate at all, and more than one distinct trigger inside
            # the window is an ambiguity we refuse to resolve.
            rows = self.db_query(
                "SELECT job_id, status, payload->>'reply_intent' AS reply_intent, "
                "trigger_message_created_at "
                "FROM support_account_reply_jobs "
                "WHERE ticket_id = %s AND trigger_message_created_at BETWEEN %s AND %s "
                "ORDER BY created_at DESC",
                (
                    ctx.client_ticket_id,
                    (anchor - timedelta(seconds=1)).isoformat(),
                    (anchor + timedelta(seconds=1)).isoformat(),
                ),
            )
            if not rows:
                return None
            distinct_triggers = {
                str(row.get("trigger_message_created_at")) for row in rows
            }
            if len(distinct_triggers) > 1:
                terminal_failure.append(
                    f"ambiguous reply-job binding: {len(distinct_triggers)} distinct "
                    f"trigger timestamps within ±1s of customer comment "
                    f"{ctx.last_customer_comment_id}; refusing to pick one"
                )
                return None
            for job in rows:
                job_id = str(job.get("job_id") or "")
                if not job_id or job_id in ctx.baseline_reply_job_ids:
                    continue
                status = str(job.get("status") or "")
                if status in _JOB_TERMINAL:
                    terminal_failure.append(
                        f"reply job ended {status} before delivery: {job}"
                    )
                    return None
                if status != "published":
                    continue
                trigger = job.get("trigger_message_created_at")
                try:
                    trigger_dt = (
                        trigger if isinstance(trigger, datetime)
                        else datetime.fromisoformat(str(trigger).replace("Z", "+00:00"))
                    )
                except ValueError:
                    trigger_dt = None
                if trigger_dt is None:
                    terminal_failure.append(
                        f"reply job trigger timestamp unparseable: {job}"
                    )
                    return None
                # Identity verification (R5): the trigger timestamp must map
                # to OUR comment and to no other customer comment. A window
                # containing only another comment's job fails here instead of
                # binding, and same-second comments are an ambiguity.
                colliding = sorted(
                    cid for cid, ts in clocks.items()
                    if cid != ctx.last_customer_comment_id
                    and abs((ts - trigger_dt).total_seconds()) <= 1
                )
                if colliding:
                    terminal_failure.append(
                        f"reply job trigger is attributable to another customer "
                        f"comment {colliding} (not comment "
                        f"{ctx.last_customer_comment_id}); refusing to bind"
                    )
                    return None
                messages = self.db_query(
                    "SELECT m.id, m.content FROM support_ticket_messages m "
                    "WHERE m.ticket_id = %s AND m.meta->>'account_reply_job_id' = %s "
                    "ORDER BY m.id DESC LIMIT 1",
                    (ctx.client_ticket_id, job_id),
                )
                if not messages:
                    continue
                deliveries = self.db_query(
                    "SELECT status, zendesk_comment_id "
                    "FROM support_account_zendesk_comment_deliveries "
                    "WHERE message_id = %s ORDER BY created_at DESC LIMIT 1",
                    (str(messages[0].get("id")),),
                )
                if not deliveries or str(deliveries[0].get("status") or "") != "delivered":
                    continue
                comment_id = str(deliveries[0].get("zendesk_comment_id") or "")
                if not comment_id or comment_id in ctx.baseline_comment_ids:
                    continue
                return {
                    "kind": "reply_job",
                    "job_id": job_id,
                    "reply_intent": job.get("reply_intent"),
                    "zendesk_comment_id": comment_id,
                    "content": messages[0].get("content"),
                }
            return None

        def _probe_once() -> dict | None:
            terminal_failure.clear()
            turn = _bound_turn()
            if turn is not None:
                status = str(turn.get("status") or "")
                if status in _TURN_TERMINAL:
                    terminal_failure.append(
                        f"hermes turn for this customer event is {status}; "
                        f"it can no longer deliver an answer: {turn}"
                    )
                    return None
                if status == "completed":
                    draft = _bound_draft_delivery(str(turn.get("turn_id") or ""))
                    if draft is not None:
                        return draft
            if terminal_failure:
                # A terminally invalid bound turn can no longer produce a
                # valid answer — do not fall through to the job path.
                return None
            job = _bound_reply_job_delivery()
            if job is not None:
                return job
            return None

        attempt = 0
        row = None
        while row is None:
            if self.should_cancel():
                raise ScenarioCancelled("customer reply delivered for the current turn")
            if time.monotonic() >= deadline:
                break
            row = _probe_once()
            if row is None:
                if terminal_failure:
                    break
                attempt += 1
                if attempt % 3 == 0:
                    self.emit(
                        "waiting",
                        {
                            "description": "customer reply delivered for the current turn",
                            "waited_seconds": attempt,
                            "last_error": "",
                        },
                    )
                self.sleep(self.poll_interval_seconds)
        if row is None:
            detail = terminal_failure[0] if terminal_failure else (
                f"timed out waiting for a delivery bound to customer comment "
                f"{ctx.last_customer_comment_id or '(unknown)'}"
            )
            self.record(ctx, step, False, detail)
            if terminal_failure:
                raise AutomationTestScenarioError(detail)
            raise TimeoutError(detail)
        ctx.seen_comment_ids.add(str(row.get("zendesk_comment_id") or ""))
        content = str(row.get("content") or "")
        ok = True
        detail = (
            f"kind={row.get('kind')} intent={row.get('reply_intent')} "
            f"comment={row.get('zendesk_comment_id')} "
            f"bound_to_comment={ctx.last_customer_comment_id or '(unknown)'}"
        )
        if content_check is not None:
            failure = content_check(content)
            if failure:
                ok = False
                detail += f"; content check failed: {failure}"
            else:
                detail += "; content check passed"
        self.record(ctx, step, ok, detail)
        if not ok:
            raise AssertionError(detail)
        return row

    def verify_relay_result_approval_binding(
        self, ctx: ScenarioContext, request_id: str, step: str,
        *, expected_outcomes: set[str],
        expected_report_digest: str | None = None,
    ) -> dict:
        """Verify the persisted relay result carries an approval reference
        bound to THIS application the same way the ECS worker gates enabled
        results (worker.py: action=approve_execution, matching request_id and
        request_version — both REQUIRED here, not optional — and a sha256
        report_digest).

        With ``expected_report_digest`` (from operator-supplied approval
        evidence) the digest is additionally cross-checked against that
        precheck report; without it the digest shape passes but
        ``digest_cross_checked`` stays False and NO human-approval claim may
        be derived from this check alone — server-side approval_ref does not
        encode the approval method (a test_auto_approve run persists the same
        shape), so "two human approvals proven" is a separate verdict the
        caller must ground in its own evidence.
        """
        rows = self.db_query(
            "SELECT res.outcome, res.write_attempted, res.approval_ref, "
            "res.relay_message_id, res.detail, res.readback, "
            "req.request_id, req.request_version "
            "FROM support_enablement_relay_results res "
            "JOIN support_enablement_relay_requests req "
            "ON req.request_id = res.request_id "
            "WHERE res.request_id = %s ORDER BY res.created_at DESC LIMIT 1",
            (request_id,),
        )
        if not rows:
            self.record(ctx, step, False, f"no relay result for {request_id}")
            raise AssertionError(f"no relay result for {request_id}")
        row = rows[0]
        outcome = str(row.get("outcome") or "")
        approval = row.get("approval_ref")
        if isinstance(approval, str):
            try:
                approval = json.loads(approval)
            except ValueError:
                approval = None
        problems = []
        if outcome not in expected_outcomes:
            problems.append(f"outcome={outcome} not in {sorted(expected_outcomes)}")
        request_version = row.get("request_version")
        if not isinstance(approval, dict):
            problems.append("approval_ref missing/not an object")
        else:
            if str(approval.get("action") or "") != "approve_execution":
                problems.append(f"approval action={approval.get('action')!r}")
            if str(approval.get("request_id") or "") != str(request_id):
                problems.append("approval request_id unbound")
            version = approval.get("request_version")
            if version is None or str(version) == "":
                problems.append("approval request_version missing (required)")
            elif request_version is None or str(request_version) == "":
                problems.append("request row version missing (required)")
            elif str(version) != str(request_version):
                problems.append(
                    f"approval request_version mismatch: {version} != {request_version}"
                )
            digest = str(approval.get("report_digest") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                problems.append("approval report_digest missing/not sha256")
            elif expected_report_digest is not None and digest != str(expected_report_digest):
                problems.append("approval report_digest does not match the precheck report")
        if problems:
            self.record(ctx, step, False, "; ".join(problems))
            raise AssertionError(f"relay result approval binding failed: {'; '.join(problems)}")
        digest_cross_checked = bool(
            expected_report_digest is not None
            and str(approval.get("report_digest")) == str(expected_report_digest)
        )
        self.record(
            ctx, step, True,
            f"outcome={outcome} request={request_id} "
            f"v{row.get('request_version')} digest={str(approval.get('report_digest'))[:12]}… "
            f"digest_cross_checked={digest_cross_checked}",
        )
        return {
            "binding_verified": True,
            "digest_cross_checked": digest_cross_checked,
            "outcome": outcome,
            "write_attempted": row.get("write_attempted"),
            "request_id": request_id,
            "request_version": row.get("request_version"),
            "report_digest": approval.get("report_digest"),
            "relay_message_id": row.get("relay_message_id"),
            "detail": row.get("detail"),
            "readback": row.get("readback"),
        }

    def wait_hermes_turn_direction(
        self, ctx: ScenarioContext, expected_direction: str, step: str,
        *, reason_contains: str | None = None, route_equals: str | None = None,
    ) -> dict:
        since = (ctx.turn_started_at - timedelta(minutes=2)).isoformat()

        def probe():
            rows = self.db_query(
                "SELECT turn_id, direction, route, direction_reason, status "
                "FROM automation_hermes_agent_turns "
                "WHERE zendesk_ticket_id = %s AND created_at >= %s "
                "ORDER BY created_at DESC LIMIT 1",
                (ctx.zendesk_ticket_id, since),
            )
            row = rows[0] if rows else None
            if not row:
                return None
            turn_id = str(row.get("turn_id") or "")
            if turn_id and turn_id in ctx.baseline_turn_ids:
                return None
            if str(row.get("status") or "") in {"completed", "failed", "human_review"}:
                return row
            return None

        row = self.wait_for(
            f"hermes turn direction={expected_direction}", probe, self.turn_timeout_min * 60
        )
        ctx.seen_turn_ids.add(str(row.get("turn_id") or ""))
        direction = str(row.get("direction") or "")
        route = str(row.get("route") or "")
        reason = str(row.get("direction_reason") or "")
        ok = direction == expected_direction and (
            reason_contains is None or reason_contains in reason
        ) and (route_equals is None or route == route_equals)
        self.record(
            ctx, step, ok, f"direction={direction} route={route} reason={reason}"
        )
        if not ok:
            raise AssertionError(
                f"unexpected hermes turn: direction={direction} route={route} reason={reason}"
            )
        return row

    def wait_hermes_message_action(
        self,
        ctx: ScenarioContext,
        expected_action: str,
        step: str,
        *,
        expected_direction: str = "automation",
        expected_route: str = "enablement",
    ) -> dict:
        """Wait for the message-action turn produced by this customer comment.

        The event id is the durable binding between the Zendesk comment and the
        Hermes turn. A recent/latest turn or a timestamp window is insufficient
        because a previous comment can complete while this wait is running.
        """
        event_id = (
            f"zendesk:ticket:{ctx.zendesk_ticket_id}:comment:{ctx.last_customer_comment_id}"
            if ctx.last_customer_comment_id
            else ""
        )
        if not event_id:
            detail = "cannot bind message-action turn: current customer comment id is missing"
            self.record(ctx, step, False, detail)
            raise AutomationTestScenarioError(detail)

        terminal_statuses = {
            "completed", "failed", "human_review", "cancelled", "superseded",
        }

        def _as_object(value: Any) -> dict[str, Any]:
            if isinstance(value, dict):
                return value
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    return {}
                return parsed if isinstance(parsed, dict) else {}
            return {}

        def probe():
            rows = self.db_query(
                "SELECT turn_id, turn_kind, status, direction, route, "
                "direction_reason, event_id, work_result "
                "FROM automation_hermes_agent_turns "
                "WHERE zendesk_ticket_id = %s AND event_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (ctx.zendesk_ticket_id, event_id),
            )
            if not rows:
                return None
            row = rows[0]
            turn_id = str(row.get("turn_id") or "")
            if turn_id and turn_id in ctx.baseline_turn_ids:
                return None
            if str(row.get("status") or "") in terminal_statuses:
                return row
            return None

        row = self.wait_for(
            f"hermes message_action={expected_action}",
            probe,
            self.turn_timeout_min * 60,
        )
        ctx.seen_turn_ids.add(str(row.get("turn_id") or ""))
        status = str(row.get("status") or "")
        direction = str(row.get("direction") or "")
        route = str(row.get("route") or "")
        turn_kind = str(row.get("turn_kind") or "")
        observed_event_id = str(row.get("event_id") or "")
        work_result = _as_object(row.get("work_result"))
        message_action = _as_object(work_result.get("message_action"))
        action = str(message_action.get("action") or "")
        reason_code = str(message_action.get("reason_code") or "")
        ok = (
            observed_event_id == event_id
            and turn_kind == "message_action"
            and status == "completed"
            and direction == expected_direction
            and route == expected_route
            and action == expected_action
        )
        detail = (
            f"turn={row.get('turn_id')} kind={turn_kind} status={status} "
            f"direction={direction} route={route} action={action} "
            f"reason_code={reason_code} event_id={observed_event_id}"
        )
        self.record(ctx, step, ok, detail)
        if not ok:
            raise AssertionError(f"unexpected Hermes message-action turn: {detail}")
        return row

    def wait_hermes_draft_delivered(
        self, ctx: ScenarioContext, step: str,
        *, content_check: Callable[[str], str | None] | None = None,
    ) -> dict:
        since = (ctx.turn_started_at - timedelta(minutes=2)).isoformat()

        def probe():
            rows = self.db_query(
                "SELECT d.status AS draft_status, d.content, dl.status AS delivery_status, "
                "dl.zendesk_comment_id "
                "FROM automation_hermes_case_drafts d "
                "LEFT JOIN support_account_zendesk_comment_deliveries dl "
                "ON dl.message_id = d.draft_id "
                "WHERE d.zendesk_ticket_id = %s AND d.created_at >= %s "
                "ORDER BY d.created_at DESC LIMIT 1",
                (ctx.zendesk_ticket_id, since),
            )
            row = rows[0] if rows else None
            if not row or str(row.get("delivery_status") or "") != "delivered":
                return None
            comment_id = str(row.get("zendesk_comment_id") or "")
            if comment_id and comment_id in ctx.baseline_comment_ids:
                # The -2min window can straddle the previous leg's delivery:
                # only a comment id from THIS turn satisfies the wait (rows
                # without an id are not bound — legacy scripted fixtures).
                return None
            return row

        row = self.wait_for(
            "hermes draft reply delivered", probe, self.turn_timeout_min * 60
        )
        ctx.seen_comment_ids.add(str(row.get("zendesk_comment_id") or ""))
        ok = True
        detail = (
            f"draft={row.get('draft_status')} comment={row.get('zendesk_comment_id')}"
        )
        if content_check is not None:
            failure = content_check(str(row.get("content") or ""))
            if failure:
                ok = False
                detail += f"; content check failed: {failure}"
            else:
                detail += "; content check passed"
        self.record(ctx, step, ok, detail)
        if not ok:
            raise AssertionError(detail)
        return row

    def emit_relay_approval_hint(self, ctx: ScenarioContext, *, timeout_min: int) -> None:
        self.emit(
            "approval_required",
            {
                "zendesk_ticket_url": f"{ZENDESK_TICKET_URL}/{ctx.zendesk_ticket_id}",
                "kind": "enablement_relay",
                "instruction": (
                    "The relay task is waiting on the Mac relay client: pick it up there and "
                    "perform the two approve_execution approvals bound to this request."
                ),
                "timeout_min": timeout_min,
            },
        )

    def wait_enablement_relay_request(
        self, ctx: ScenarioContext, step: str, *, after_version: int | None = None
    ) -> dict:
        """Wait for a relay request; bound to a version watermark so the
        corrected-App-ID leg can only match the NEW application (v+1)."""

        def probe():
            rows = self.db_query(
                "SELECT request_id, status, app_id, request_version "
                "FROM support_enablement_relay_requests "
                "WHERE ticket_id = %s ORDER BY created_at DESC LIMIT 1",
                (ctx.client_ticket_id,),
            )
            if not rows:
                return None
            version = int(rows[0].get("request_version") or 0)
            if after_version is not None and version <= int(after_version):
                return None
            return rows[0]

        try:
            row = self.wait_for(
                "enablement relay request created", probe, self.turn_timeout_min * 60
            )
            ctx.last_relay_request_version = int(row.get("request_version") or 0)
            ctx.bound_relay_request_id = str(row.get("request_id") or "")
            self.record(
                ctx, step, True,
                f"request={row.get('request_id')} status={row.get('status')} "
                f"app_id={row.get('app_id')} v{row.get('request_version')}",
            )
            return row
        except TimeoutError as exc:
            self.record(ctx, step, False, str(exc))
            raise

    def relay_request_count(self, ctx: ScenarioContext) -> int:
        rows = self.db_query(
            "SELECT COUNT(*) AS n FROM support_enablement_relay_requests "
            "WHERE ticket_id = %s",
            (ctx.client_ticket_id,),
        )
        return int(rows[0].get("n") or 0) if rows else 0

    def wait_bound_relay_request_active(
        self, ctx: ScenarioContext, request_id: str, step: str
    ) -> dict:
        """Assert the bound relay request is still in an active (pending)
        state — the progress reply must never create, release, or finish it."""

        def probe():
            rows = self.db_query(
                "SELECT request_id, status, request_version "
                "FROM support_enablement_relay_requests "
                "WHERE request_id = %s",
                (request_id,),
            )
            if not rows:
                return None
            if str(rows[0].get("status") or "") in {
                "gated", "dispatch_pending", "dispatching", "dispatched",
            }:
                return rows[0]
            return None

        try:
            row = self.wait_for(
                f"relay request {request_id} still active", probe, self.turn_timeout_min * 60
            )
            self.record(
                ctx, step, True,
                f"request={row.get('request_id')} status={row.get('status')} "
                f"v{row.get('request_version')}",
            )
            return row
        except TimeoutError as exc:
            self.record(ctx, step, False, str(exc))
            raise

    def wait_enablement_relay_result(
        self, ctx: ScenarioContext, expected_outcomes: set[str], step: str,
        *, request_id: str | None = None,
    ) -> dict:
        """Wait for a relay result; bound to the given request when provided,
        so an older application's result can never satisfy this leg."""

        def probe():
            if request_id:
                rows = self.db_query(
                    "SELECT res.outcome, res.write_attempted, req.status AS request_status "
                    "FROM support_enablement_relay_results res "
                    "JOIN support_enablement_relay_requests req ON req.request_id = res.request_id "
                    "WHERE res.request_id = %s ORDER BY res.created_at DESC LIMIT 1",
                    (request_id,),
                )
            else:
                rows = self.db_query(
                    "SELECT res.outcome, res.write_attempted, req.status AS request_status "
                    "FROM support_enablement_relay_results res "
                    "JOIN support_enablement_relay_requests req ON req.request_id = res.request_id "
                    "WHERE req.ticket_id = %s ORDER BY res.created_at DESC LIMIT 1",
                    (ctx.client_ticket_id,),
                )
            return rows[0] if rows else None

        try:
            row = self.wait_for(
                f"enablement relay result (expect {'|'.join(sorted(expected_outcomes))})",
                probe,
                self.relay_timeout_min * 60,
            )
            outcome = str(row.get("outcome") or "")
            ok = outcome in expected_outcomes
            self.record(
                ctx, step, ok,
                f"outcome={outcome} write_attempted={row.get('write_attempted')} "
                f"request_status={row.get('request_status')}",
            )
            if not ok:
                raise AssertionError(f"unexpected relay result outcome: {outcome}")
            return row
        except TimeoutError as exc:
            self.record(ctx, step, False, str(exc))
            raise

    def wait_next_customer_visible_reply(self, ctx: ScenarioContext, step: str) -> dict | None:
        """Discovery wait: observe whatever the system produces for the latest
        customer turn (reply job or hermes agent turn) without pinning a contract.

        Used while pinning new turn contracts (E3 nudge / not-found turns): the
        observed outcome is recorded in the step detail and returned so the
        caller can assert on it afterwards. Times out like a normal turn.
        """
        since = (ctx.turn_started_at - timedelta(minutes=2)).isoformat()

        def probe():
            jobs = self.db_query(
                "SELECT job_id, status, payload->>'reply_intent' AS reply_intent, created_at "
                "FROM support_account_reply_jobs "
                "WHERE ticket_id = %s AND created_at >= %s "
                "ORDER BY created_at DESC LIMIT 1",
                (ctx.client_ticket_id, since),
            )
            if (
                jobs
                and (
                    not str(jobs[0].get("job_id") or "")
                    or str(jobs[0].get("job_id") or "") not in ctx.baseline_reply_job_ids
                )
                and str(jobs[0].get("status") or "") in {
                    "published", "failed", "manual_attention", "cancelled",
                }
            ):
                found = {"kind": "reply_job", **jobs[0]}
                ctx.seen_reply_job_ids.add(str(jobs[0].get("job_id") or ""))
                return found
            turns = self.db_query(
                "SELECT turn_id, status, direction, created_at "
                "FROM automation_hermes_agent_turns "
                "WHERE zendesk_ticket_id = %s AND created_at >= %s "
                "ORDER BY created_at DESC LIMIT 1",
                (ctx.zendesk_ticket_id, since),
            )
            if (
                turns
                and (
                    not str(turns[0].get("turn_id") or "")
                    or str(turns[0].get("turn_id") or "") not in ctx.baseline_turn_ids
                )
            ):
                found = {"kind": "hermes_turn", **turns[0]}
                ctx.seen_turn_ids.add(str(turns[0].get("turn_id") or ""))
                return found
            return None

        try:
            row = self.wait_for(
                "next customer-visible product (reply job or hermes turn)",
                probe,
                self.turn_timeout_min * 60,
            )
            detail = (
                f"kind={row.get('kind')} intent={row.get('reply_intent')} "
                f"status={row.get('status')} direction={row.get('direction')}"
            )
            self.record(ctx, step, True, detail)
            self.info(f"[{ctx.scenario_id}] observed product: {detail}")
            return row
        except TimeoutError as exc:
            self.record(ctx, step, False, str(exc))
            raise

    # -- scenarios ---------------------------------------------------------

    def run_e1p(self) -> None:
        """Preproduction enablement auto chain (hermes engine + archer mode).

        Verified against golden ticket 13605 on r20260920-e11abda: no internal
        handoff email (relay auto path marks internal_email_send_status
        not_applicable), the confirmation reply publishes automatically, and
        the public Zendesk comment delivery has no target_status change.
        """
        ctx = ScenarioContext("E1P")
        self.start_ticket(
            ctx,
            "Please enable Media Relay for our project",
            _enablement_request_body(),
        )
        self.find_case(ctx)
        self.wait_case_field(ctx, "execution_action", "enablement", "routed to enablement")
        self.wait_case_field(
            ctx, "internal_email_send_status", "not_applicable",
            "enablement auto path (no internal handoff email)",
        )
        self.wait_reply_intent(ctx, {"submission_confirmation"}, "submission confirmation reply")
        self.wait_public_comment_delivered(ctx, "confirmation comment delivered to Zendesk")

    def run_e3(self) -> None:
        """Preproduction enablement full lifecycle (E3).

        Walks the whole customer-visible arc of the hermes auto/relay chain
        under the p2-178 contracts: missing App ID ask -> in-session RAG
        knowledge answer -> invalid App ID -> valid-format submit (relay
        review) -> progress nudge answered from the bound relay request ->
        archer project_not_found (dedicated not-found reply) -> corrected
        App ID (new request version) -> real enablement + solved. Turn 6/7
        contracts beyond the local pinned implementation still need the
        first live run for final confirmation (see the runbook).
        """
        if self.customer_turn_transport != "zendesk_api":
            # Fail BEFORE any ticket is created: the 163 email path is broken
            # in the preproduction test setup (no requester notifications,
            # plus-address replies never attach to the ticket).
            raise AutomationTestScenarioError(
                "E3 requires AUTOMATION_TEST_CUSTOMER_TURN_TRANSPORT=zendesk_api "
                "and AUTOMATION_TEST_ZENDESK_AUTH (the email transport is not "
                "usable for preproduction customer turns)"
            )
        ctx = ScenarioContext("E3")
        self.start_ticket(
            ctx,
            "Enable media relay for our project",
            "Hello Agora team,\n\n"
            "I want to enable media relay.\n\n"
            "Thanks.",
        )
        self.find_case(ctx)
        self.wait_case_field(ctx, "execution_action", "enablement", "routed to enablement")
        # Turn 1 contract (probe 13733, r20260924): the missing-App-ID ask is an
        # agent-drafted reply — no reply job; delivery lands via the hermes
        # draft pipeline within seconds.
        self.wait_hermes_draft_delivered(
            ctx, "ask for App ID draft delivered (turn 1)",
            content_check=_ask_appid_content_check,
        )
        self.next_customer_turn(
            ctx,
            "What is the App ID? I am not sure where to find it in the console.",
        )
        # Turn 2 contract (fixed-task/message-action): the customer comment
        # reuses the locked enablement task and is answered by a reply-only
        # message_action. It must not create a new relay application.
        self.wait_hermes_message_action(
            ctx,
            "answer_related_question",
            "knowledge question answered by message_action (turn 2)",
        )
        self.wait_hermes_draft_delivered(
            ctx, "knowledge answer delivered with references (turn 2)",
            content_check=_knowledge_answer_content_check,
        )
        self.wait_case_field(
            ctx, "automation_status", "automation",
            "case stays automation-owned after the knowledge answer (turn 2)",
        )
        relay_requests_after_turn2 = self.relay_request_count(ctx)
        self.record(
            ctx, "knowledge answer creates no relay application (turn 2)",
            relay_requests_after_turn2 == 0,
            f"relay_request_count={relay_requests_after_turn2}",
        )
        self.next_customer_turn(ctx, f"My App ID is {E3_APPID_INVALID}")
        self.wait_reply_intent(
            ctx, {"enablement_appid_invalid"}, "invalid App ID rejected (turn 3)"
        )
        self.wait_case_field(
            ctx, "internal_email_send_reason", "appid_invalid_format",
            "invalid App ID case marker",
        )
        self.next_customer_turn(ctx, f"Sorry, typo. My App ID is {E3_APPID_NOT_FOUND}")
        self.wait_reply_intent(
            ctx, {"submission_confirmation"}, "submission confirmation (turn 4)"
        )
        self.wait_published_reply_content(
            ctx,
            expected_intent="submission_confirmation",
            check=_submission_confirmation_content_check,
            step="confirmation content mentions review without a deadline promise",
        )
        turn4_request = self.wait_enablement_relay_request(
            ctx, "relay request created after confirmation"
        )
        turn4_request_id = str(turn4_request.get("request_id") or "")
        self.wait_public_comment_delivered(ctx, "confirmation comment delivered to Zendesk")

        self.next_customer_turn(ctx, E3_NUDGE_BODY)
        # Turn 5 contract (fixed-task/message-action): a polite review nudge
        # produces a reply-only progress action from the BOUND relay request's
        # actual state — no acceleration promise, new application, or release.
        self.wait_hermes_message_action(
            ctx,
            "report_progress",
            "review nudge answered by message_action (turn 5)",
        )
        self.wait_bound_relay_request_active(
            ctx, turn4_request_id,
            "nudge leaves the bound relay application untouched (turn 5)",
        )
        relay_requests_after_turn5 = self.relay_request_count(ctx)
        self.record(
            ctx, "nudge creates no second relay application (turn 5)",
            relay_requests_after_turn5 == 1,
            f"relay_request_count={relay_requests_after_turn5}",
        )
        self.wait_hermes_draft_delivered(
            ctx, "progress answer delivered (turn 5)",
            content_check=_progress_answer_content_check,
        )
        self.wait_case_field(
            ctx, "automation_status", "automation",
            "case stays automation-owned after the nudge (turn 5)",
        )

        self.emit_relay_approval_hint(ctx, timeout_min=self.relay_timeout_min)
        self.wait_enablement_relay_result(
            ctx, {"project_not_found"}, "relay result: project not found (turn 6 leg)",
            request_id=turn4_request_id,
        )
        # Turn 6 contract (p2-178 implementation): a clean project_not_found
        # result answers the customer with the dedicated not-found reply and
        # keeps the case automation-owned for the corrected-App-ID turn.
        self.wait_reply_intent(
            ctx, {"enablement_appid_not_found"},
            "dedicated project-not-found reply (turn 6)",
        )
        self.wait_published_reply_content(
            ctx,
            expected_intent="enablement_appid_not_found",
            check=_appid_not_found_content_check,
            step="not-found content asks to double-check the App ID, no enablement claim",
        )

        self.next_customer_turn(ctx, f"Thanks. The correct App ID is {E3_APPID_VALID}")
        self.wait_reply_intent(
            ctx, {"submission_confirmation"}, "re-submission confirmation (turn 7)"
        )
        # The corrected App ID must open a NEW application version, never
        # reuse the not-found v1 request.
        turn7_request = self.wait_enablement_relay_request(
            ctx, "corrected App ID opens a new request version (turn 7)",
            after_version=int(turn4_request.get("request_version") or 0),
        )
        turn7_request_id = str(turn7_request.get("request_id") or "")
        self.record(
            ctx, "corrected submission creates a distinct request (turn 7)",
            turn7_request_id != turn4_request_id,
            f"v1={turn4_request_id} v2={turn7_request_id}",
        )
        self.emit_relay_approval_hint(ctx, timeout_min=self.relay_timeout_min)
        self.wait_enablement_relay_result(
            ctx, {"enabled", "already_satisfied"}, "relay result: enabled (turn 7 leg)",
            request_id=turn7_request_id,
        )
        self.wait_reply_intent(
            ctx, {"enablement_archer_enabled"}, "enablement completion reply (turn 7)"
        )
        self.wait_published_reply_content(
            ctx,
            expected_intent="enablement_archer_enabled",
            check=_enablement_enabled_content_check,
            step="completion content states enablement and closes the case",
        )
        self.wait_zendesk_delivery_delivered(ctx, "ticket solved delivery")
        self.wait_case_field(ctx, "zendesk_ticket_status", "solved", "ticket solved + case closed")

    def run_e1(self) -> None:
        ctx = ScenarioContext("E1")
        self.start_ticket(
            ctx,
            "Please enable Media Relay for our project",
            _enablement_request_body(),
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
        self.wait_published_reply_content(
            ctx,
            expected_intent="enablement_completed_and_close",
            check=_enablement_completion_content_check,
            step="completion reply content published",
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
        self.wait_published_reply_content(
            ctx,
            expected_intent="enablement_completed_and_close",
            check=_enablement_completion_content_check,
            step="completion reply content published",
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
        self.next_customer_turn(ctx, FRAUD_PARTIAL_INFO_BODY)
        self.wait_case_field(ctx, "internal_email_send_status", "sent", "internal handoff email sent")
        self.wait_reply_intent(ctx, {"fraud_handoff_confirmation"}, "24h handoff reply published")
        self.wait_published_reply_content(
            ctx,
            expected_intent="fraud_handoff_confirmation",
            check=_fraud_handoff_content_check,
            step="fraud handoff reply states the 24-hour promise",
        )
        self.wait_event(
            ctx,
            "zendesk_fraud_review_handoff",
            "assigned to fraud reviewer",
            expected_states={"assigned", "already_assigned"},
        )
        row = self.case_row(ctx)
        self.record(
            ctx, "ticket NOT auto-solved",
            str(row.get("zendesk_ticket_status") or "") not in {"solved", "closed"},
            f"zendesk_ticket_status={row.get('zendesk_ticket_status')!r}",
        )
        missing_request_count = self.reply_intent_count(ctx, "request_missing_information")
        self.record(
            ctx,
            "missing information requested exactly once",
            missing_request_count == 1,
            f"request_missing_information_count={missing_request_count}",
        )
        if missing_request_count != 1:
            raise AssertionError(
                "unexpected missing-information request count: "
                f"{missing_request_count}"
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
        self.wait_case_field(ctx, "internal_email_send_status", "sent", "internal handoff email sent")
        self.wait_reply_intent(
            ctx, {"account_suspension_handoff_and_close"}, "closing reply published"
        )
        self.wait_published_reply_content(
            ctx,
            expected_intent="account_suspension_handoff_and_close",
            check=_suspension_closing_content_check,
            step="first suspension reply confirms handoff + 24h, no close claim",
        )
        self.wait_event(
            ctx,
            "zendesk_fraud_review_handoff",
            "assigned to suspension reviewer",
            expected_states={"assigned", "already_assigned"},
        )
        row = self.case_row(ctx)
        self.record(
            ctx, "ticket NOT auto-solved",
            str(row.get("zendesk_ticket_status") or "") not in {"solved", "closed"},
            f"zendesk_ticket_status={row.get('zendesk_ticket_status')!r}",
        )
        self.wait_suspension_state(ctx, "closed", "suspension workflow closed")

    def run_d1(self) -> None:
        ctx = ScenarioContext("D1")
        self.start_ticket(
            ctx,
            "Please send the detailed invoice for transaction 1104245232004173824",
            DETAILED_INVOICE_BODY,
        )
        self.find_case(ctx)
        row = self.case_row(ctx)
        self.record(
            ctx, "routed to detailed_invoice",
            row.get("execution_action") == "detailed_invoice",
            f"execution_action={row.get('execution_action')!r}",
        )
        self.wait_case_field(ctx, "internal_email_send_status", "sent", "internal invoice email sent")
        self.wait_reply_intent(ctx, {"submission_confirmation"}, "submission confirmation reply")
        self.wait_manual_billing_invoice_reply(ctx)
        self.wait_reply_intent(
            ctx, {"detailed_invoice_completed_and_close"}, "completion reply (with PDF) published"
        )
        self.wait_zendesk_delivery_delivered(ctx, "zendesk public comment + PDF delivered")
        self.wait_case_field(ctx, "zendesk_ticket_status", "solved", "ticket solved + case closed")

    SCENARIOS: dict[str, dict[str, Any]] = {
        "E3": {
            "label": "Enablement full lifecycle (preproduction)",
            "description": (
                "missing App ID ask → in-session RAG knowledge answer → invalid App ID → "
                "review submit → progress nudge answered from the bound relay request → "
                "project not found (dedicated reply) → corrected App ID (new request "
                "version) → real enablement + solved (requires the Mac relay client, two "
                "approve_execution approvals, and the zendesk_api customer-turn channel)"
            ),
            "run": run_e3,
        },
        "E1P": {
            "label": "Enablement auto (preproduction)",
            "description": (
                "AppID ticket → auto enablement route → confirmation reply published "
                "+ Zendesk delivered (no internal email, no manual approval)"
            ),
            "run": run_e1p,
        },
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
            "description": "ask once → provide partial info → handoff + assign reviewer, NOT solved",
            "run": run_f1,
        },
        "S1": {
            "label": "Account suspension",
            "description": "ask contact email → confirm → closing reply + assign reviewer + notify email, NOT solved",
            "run": run_s1,
        },
        "D1": {
            "label": "Detailed invoice + PDF",
            "description": "full invoice data → internal email → manual reply with PDF → public comment attachment + solved",
            "run": run_d1,
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

    def smtp_connectivity_check(self) -> dict[str, str]:
        """Verify the ticket-creation SMTP channel alone (no customer-visible
        traffic). Needed explicitly when the selected customer-turn channel
        is the Zendesk API, because ``connectivity_check`` then skips SMTP
        even though ticket creation still rides on this mailbox."""
        with smtplib.SMTP_SSL(
            self.smtp_host, self.smtp_port, timeout=15, context=ssl.create_default_context()
        ) as server:
            server.login(self.sender, self.smtp_password)
        return {"smtp": "ok"}

    def connectivity_check(self) -> dict[str, str]:
        """Verify DB plus the SELECTED customer-turn channel without sending
        any customer-visible traffic."""
        results: dict[str, str] = {}
        rows = self.db_query("SELECT COUNT(*) AS n FROM support_account_cases", ())
        results["db"] = f"ok (support_account_cases rows={rows[0]['n']})"
        if self.customer_turn_transport == "zendesk_api":
            # The selected channel posts public comments through the Zendesk
            # API as the requester; validate its credentials with a read-only
            # lookup instead of touching the 163 mailbox path.
            me = self._zendesk_request("/users/me.json")
            results["zendesk_api"] = (
                f"ok (authenticated as {str((me.get('user') or {}).get('email') or 'unknown')})"
            )
        else:
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
