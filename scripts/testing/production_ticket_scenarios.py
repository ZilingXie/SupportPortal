#!/usr/bin/env python3
"""Multi-turn Zendesk regression scenario driver for the production pipeline.

Each scenario plays a full customer conversation through the REAL channels:
customer turns are sent from the dedicated 163 mailbox (SMTP) and threaded
into the Zendesk ticket via the notification email's headers (IMAP); internal
enablement approval stays MANUAL — the driver pauses and polls until the
internal reply is processed. Assertions are structural (reply intents,
internal email status, suspension workflow state, Zendesk status), never
exact LLM wording.

Every run creates REAL Zendesk tickets. Subjects carry the [zac test] tag.

Usage:
    python3 scripts/testing/production_ticket_scenarios.py --list
    python3 scripts/testing/production_ticket_scenarios.py --check
    python3 scripts/testing/production_ticket_scenarios.py --scenario E1 [--yes]

Environment comes from the repo root .env: BILLING_AUTOMATION_SMTP_*
(send), imap.163.com (read, same credentials), PRODUCTION_TICKET_DB_DSN
(state polling).
"""

from __future__ import annotations

import argparse
import email
import email.policy
import imaplib
import json
import os
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = Path(os.environ.get("SUPPORTPORTAL_ENV_FILE") or REPO_ROOT / ".env")

# imaplib does not know the non-standard RFC2971 ID command; 163 Coremail
# refuses SELECT/SEARCH (NO) unless it is sent before login.
imaplib.Commands["ID"] = ("NONAUTH", "AUTH", "SELECTED")

ZENDESK_TICKET_URL = "https://agoraio.zendesk.com/agent/tickets"
ZENDESK_SUPPORT_ADDRESS = "support@agoraio.zendesk.com"
DEFAULT_SUBJECT_TAG = "[zac test] "
DEFAULT_TURN_TIMEOUT_MIN = 20
DEFAULT_APPROVAL_TIMEOUT_MIN = 45
POLL_INTERVAL_SECONDS = 20

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


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def log(message: str) -> None:
    print(f"[{now_utc().strftime('%H:%M:%S')}] {message}", flush=True)


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        raise SystemExit(f"missing .env at {ENV_PATH}")
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass
class StepResult:
    scenario: str
    step: str
    status: str  # PASS / FAIL
    detail: str = ""


@dataclass
class ScenarioContext:
    scenario_id: str
    subject: str = ""
    zendesk_ticket_id: str = ""
    account_case_id: str = ""
    client_ticket_id: str = ""
    turn_started_at: datetime = field(default_factory=now_utc)


class ScenarioDriver:
    def __init__(self, env: dict[str, str], args: argparse.Namespace) -> None:
        self.env = env
        self.smtp_host = env.get("BILLING_AUTOMATION_SMTP_HOST", "")
        self.smtp_port = int(env.get("BILLING_AUTOMATION_SMTP_PORT", "465"))
        self.sender = env.get("BILLING_AUTOMATION_SMTP_USERNAME", "")
        self.smtp_password = env.get("BILLING_AUTOMATION_SMTP_PASSWORD", "")
        self.imap_host = env.get("AUTOMATION_TEST_IMAP_HOST", "imap.163.com")
        self.imap_port = int(env.get("AUTOMATION_TEST_IMAP_PORT", "993"))
        self.db_dsn = env.get("PRODUCTION_TICKET_DB_DSN", "")
        self.db_schema = env.get("TICKET_DB_SCHEMA", "supportportal").strip() or "supportportal"
        self.subject_tag = env.get("AUTOMATION_TEST_TICKET_SUBJECT_TAG", DEFAULT_SUBJECT_TAG).strip()
        self.args = args
        self.results: list[StepResult] = []

        missing = [
            name
            for name, value in (
                ("BILLING_AUTOMATION_SMTP_HOST", self.smtp_host),
                ("BILLING_AUTOMATION_SMTP_USERNAME", self.sender),
                ("BILLING_AUTOMATION_SMTP_PASSWORD", self.smtp_password),
                ("PRODUCTION_TICKET_DB_DSN", self.db_dsn),
            )
            if not value
        ]
        if missing:
            raise SystemExit(f"missing required .env keys: {', '.join(missing)}")

    # -- infrastructure -------------------------------------------------

    def record(self, ctx: ScenarioContext, step: str, ok: bool, detail: str = "") -> None:
        status = "PASS" if ok else "FAIL"
        self.results.append(StepResult(ctx.scenario_id, step, status, detail))
        log(f"[{ctx.scenario_id}] {step}: {status}" + (f" — {detail}" if detail else ""))

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

    def wait_for(self, description: str, probe, timeout_seconds: int):
        """Poll probe() until it returns non-None or the timeout expires."""
        deadline = time.monotonic() + timeout_seconds
        last_error = ""
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            try:
                value = probe()
                if value is not None:
                    return value
                last_error = ""
            except Exception as exc:  # noqa: BLE001 - keep polling on transient errors
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt % 3 == 0:
                waited = int(timeout_seconds - (deadline - time.monotonic()))
                suffix = f" (last error: {last_error})" if last_error else ""
                log(f"… waiting for {description} ({waited}s elapsed){suffix}")
            time.sleep(POLL_INTERVAL_SECONDS)
        raise TimeoutError(f"timed out waiting for {description}; {last_error or 'condition never met'}")

    def send_email(self, subject: str, body: str, to_address: str, headers: dict[str, str] | None = None) -> None:
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
        log(f"email sent → {to_address} | {subject}")

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

    # -- pipeline actions -------------------------------------------------

    def tagged(self, subject: str) -> str:
        if self.subject_tag and not subject.startswith(self.subject_tag):
            return f"{self.subject_tag}{subject}"
        return subject

    def start_ticket(self, ctx: ScenarioContext, subject: str, body: str) -> None:
        ctx.subject = self.tagged(subject)
        ctx.turn_started_at = now_utc()
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
            "production case creation (n8n intake)", probe, self.args.turn_timeout_min * 60
        )
        ctx.account_case_id = case["account_case_id"]
        ctx.client_ticket_id = case["client_ticket_id"]
        ctx.zendesk_ticket_id = str(case["zendesk_ticket_id"] or "")
        log(
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
            self.wait_for(
                f"{field_name}={expected}", probe, self.args.turn_timeout_min * 60
            )
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
            self.args.turn_timeout_min * 60,
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
            self.wait_for(f"suspension state={expected}", probe, self.args.turn_timeout_min * 60)
            self.record(ctx, step, True, f"state={expected}")
        except TimeoutError as exc:
            self.record(ctx, step, False, str(exc))
            raise

    def wait_event(self, ctx: ScenarioContext, event_type: str, step: str) -> None:
        since = (ctx.turn_started_at - timedelta(minutes=2)).isoformat()

        def probe():
            rows = self.db_query(
                "SELECT id FROM support_ticket_events "
                "WHERE ticket_id = %s AND event_type = %s AND created_at >= %s LIMIT 1",
                (ctx.client_ticket_id, event_type, since),
            )
            return rows[0] if rows else None

        try:
            self.wait_for(f"event {event_type}", probe, self.args.turn_timeout_min * 60)
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
            timeout_seconds=min(8 * 60, self.args.turn_timeout_min * 60),
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
            log("no notification found in inbox; using plus-address fallback")
            self.send_email(
                f"Re: {ctx.subject}",
                body,
                f"support+{ctx.zendesk_ticket_id}@agoraio.zendesk.com",
            )

    def wait_manual_approval(self, ctx: ScenarioContext, feature_label: str) -> None:
        print("\n" + "=" * 72)
        print("MANUAL APPROVAL REQUIRED")
        print(
            f"  Zendesk ticket : {ZENDESK_TICKET_URL}/{ctx.zendesk_ticket_id}\n"
            f"  Reply (from YOUR mailbox) to the internal email whose subject starts\n"
            f"  with \"[Enablement Request] {feature_label}\" and include a completion\n"
            f"  sentence such as:\n"
            f"      {feature_label} is enabled for this app.\n"
            f"  Waiting up to {self.args.approval_timeout_min} minutes…"
        )
        print("=" * 72 + "\n", flush=True)
        ctx.turn_started_at = now_utc()
        self.wait_event(
            ctx,
            "enablement_internal_resolution_received",
            "internal approval received",
        )

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
        self.record(
            ctx, "routed to enablement",
            self.case_row(ctx).get("execution_action") == "enablement",
            f"execution_action={self.case_row(ctx).get('execution_action')!r}",
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
        self.record(
            ctx, "routed to fraud_account",
            self.case_row(ctx).get("execution_action") == "fraud_account",
            f"execution_action={self.case_row(ctx).get('execution_action')!r}",
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
        self.record(
            ctx, "routed to account_suspension",
            self.case_row(ctx).get("execution_action") == "account_suspension",
            f"execution_action={self.case_row(ctx).get('execution_action')!r}",
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

    SCENARIOS = {
        "E1": ("enablement happy path (AppID provided)", run_e1),
        "E2": ("enablement missing AppID + RAG fallback + completion", run_e2),
        "F1": ("fraud review: ask info -> provide -> handoff + assign, no solve", run_f1),
        "S1": ("account suspension: confirm email -> closing + solve", run_s1),
    }

    # -- reporting -----------------------------------------------------------

    def report(self) -> bool:
        print("\n================ SCENARIO RESULTS ================")
        all_ok = True
        for result in self.results:
            mark = "✓" if result.status == "PASS" else "✗"
            print(f"  {mark} [{result.scenario_id}] {result.step}" + (f" — {result.detail}" if result.detail else ""))
            all_ok = all_ok and result.status == "PASS"
        tickets = sorted({r.scenario for r in self.results})
        print(f"\n  scenarios run: {', '.join(tickets)}")
        print("  clean up test tickets in Zendesk afterwards (subjects tagged [zac test]).")
        print("==================================================")
        return all_ok


def check_mode(driver: ScenarioDriver) -> None:
    log("DB: connecting…")
    rows = driver.db_query("SELECT COUNT(*) AS n FROM support_account_cases", ())
    log(f"DB: ok (support_account_cases rows={rows[0]['n']})")
    log(f"SMTP: login {driver.smtp_host}:{driver.smtp_port} as {driver.sender}…")
    with smtplib.SMTP_SSL(
        driver.smtp_host, driver.smtp_port, timeout=15, context=ssl.create_default_context()
    ) as server:
        server.login(driver.sender, driver.smtp_password)
    log("SMTP: ok")
    log(f"IMAP: login {driver.imap_host}:{driver.imap_port}…")
    with driver.imap_connect() as imap:
        status, _ = imap.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError(f"IMAP select INBOX failed: {status}")
    log("IMAP: ok")
    log("all channels reachable; no emails were sent.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=[*ScenarioDriver.SCENARIOS, "all"])
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--check", action="store_true", help="verify DB/SMTP/IMAP reachability only")
    parser.add_argument("--turn-timeout-min", type=int, default=DEFAULT_TURN_TIMEOUT_MIN)
    parser.add_argument("--approval-timeout-min", type=int, default=DEFAULT_APPROVAL_TIMEOUT_MIN)
    args = parser.parse_args()

    if args.list or (not args.scenario and not args.check):
        for sid, (desc, _) in ScenarioDriver.SCENARIOS.items():
            print(f"  {sid}: {desc}")
        return 0

    driver = ScenarioDriver(load_env(), args)
    if args.check:
        check_mode(driver)
        return 0

    selected = list(ScenarioDriver.SCENARIOS) if args.scenario == "all" else [args.scenario]
    if not args.yes:
        print(
            "This will send REAL emails from "
            f"{driver.sender} and create REAL Zendesk tickets:\n  {', '.join(selected)}"
        )
        answer = input("Continue? [yes/N] ").strip().lower()
        if answer != "yes":
            print("aborted.")
            return 1

    for sid in selected:
        log(f"========== scenario {sid} ==========")
        try:
            ScenarioDriver.SCENARIOS[sid][1](driver)
        except (TimeoutError, AssertionError) as exc:
            log(f"scenario {sid} aborted: {exc}")

    return 0 if driver.report() else 2


if __name__ == "__main__":
    sys.exit(main())
