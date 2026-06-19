# Billing Response Link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the billing automation internal response-link loop: internal billing email contains a one-time `support.stellarix.space/response?token=...` link, the handler submits a structured result, the backend records an internal resolution event, and AI decides whether to notify the customer.

**Architecture:** Add a small billing response flow service for token generation/validation, form validation, and AI customer follow-up. Persist one-time tokens separately from billing tickets, expose a static `/response` page plus JSON APIs, and keep the source of truth as `billing_ticket_id` for this demo. Submit writes an internal resolution event first; customer notification is a second step driven by that event.

**Tech Stack:** FastAPI (`backend/main.py`), existing ticket repository abstraction (`backend/repositories/ticket_repository.py`), PostgreSQL schema doc/init SQL, vanilla static UI, existing `llm_factory.invoke_responses_text` with deterministic fallback, Python `unittest`/FastAPI `TestClient`.

---

## Confirmed Product Rules

- Use only `billing_ticket_id` in the response flow UI and link context; do not introduce a separate visible support ticket id in this flow.
- Token has no recipient/handler binding and no expiry for this demo.
- Token is one-time submit: after successful submit it cannot be submitted again.
- One shared UI for invoice/account-suspension/account-verification demo; no dynamic per-category form yet.
- Form fields:
  - `处理结果`: `已完成`, `拒绝处理`, `需要客户操作`
  - `是否通知客户`: `是`, `否`
  - `说明`: optional only when `已完成`; required for `拒绝处理` and `需要客户操作`
- Submit writes an `internal_resolution` event first. AI reads that event and decides whether to reply to the customer.
- Internal email should not list available actions; it only contains the response link and existing ticket summary.

## File Map

- Create `backend/services/billing_response_flow.py`
  - Token generation/hash helpers.
  - Response form validation.
  - Internal resolution event builder.
  - AI/fallback customer follow-up generation from event payload.
- Modify `backend/services/billing_automation.py`
  - Build internal emails with `Billing Ticket ID` and optional response link.
  - Stop exposing canonical support ticket id in this email body for the new flow.
- Modify `backend/main.py`
  - Mount `ui/billing-response-ui` at `/response`.
  - Create token before sending the internal email.
  - Add response lookup and submit API handlers.
  - On submit, persist event, mark token used, optionally append AI customer reply.
- Modify `backend/repositories/ticket_repository.py`
  - Add in-memory token persistence methods.
  - Add PostgreSQL table init and CRUD methods for one-time response tokens.
- Modify `backend/sql/ticket_storage.sql`
  - Document the token table schema and index.
- Create `ui/billing-response-ui/index.html`
  - Static shell for `/response?token=...`.
- Create `ui/billing-response-ui/app.js`
  - Fetch token context, render form, validate note rule, submit.
- Create `ui/billing-response-ui/styles.css`
  - Minimal responsive styling consistent with current account UI patterns.
- Add/modify tests:
  - Create `backend/tests/test_billing_response_flow.py`.
  - Modify `backend/tests/test_account_intake.py`.
  - Create `backend/tests/test_billing_response_ui_contract.py`.
  - Modify `backend/tests/test_repository_configuration.py` only if it asserts schema/table contracts.
- Update docs after implementation:
  - `docs/prompt_change_log.md` because customer follow-up AI prompt/behavior changes.
  - `docs/feature_list.md` because this is a major billing automation capability.
  - `docs/roadmap.html` to mark response-link tasks complete when implemented.

---

### Task 1: Repository Token Persistence

**Files:**
- Modify: `backend/repositories/ticket_repository.py`
- Modify: `backend/sql/ticket_storage.sql`
- Test: `backend/tests/test_billing_response_flow.py`

- [ ] **Step 1: Write failing tests for one-time token persistence**

Create `backend/tests/test_billing_response_flow.py` with tests for in-memory repository behavior first:

```python
from __future__ import annotations

import os
import unittest

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from backend.repositories.ticket_repository import InMemoryTicketRepository


class BillingResponseTokenRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()

    def test_save_get_and_mark_billing_response_token_used_once(self) -> None:
        token = {
            "token_hash": "hash-1",
            "billing_ticket_id": "BT-TK-ACC-123456",
            "created_at": "2026-06-19T00:00:00+00:00",
            "used_at": None,
        }

        self.repository.save_billing_response_token(token)

        saved = self.repository.get_billing_response_token("hash-1")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved["billing_ticket_id"], "BT-TK-ACC-123456")
        self.assertIsNone(saved.get("used_at"))

        self.assertTrue(self.repository.mark_billing_response_token_used("hash-1", "2026-06-19T00:01:00+00:00"))
        self.assertFalse(self.repository.mark_billing_response_token_used("hash-1", "2026-06-19T00:02:00+00:00"))
        used = self.repository.get_billing_response_token("hash-1")
        assert used is not None
        self.assertEqual(used["used_at"], "2026-06-19T00:01:00+00:00")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest backend.tests.test_billing_response_flow.BillingResponseTokenRepositoryTests`

Expected: FAIL with `AttributeError` for missing token repository methods.

- [ ] **Step 3: Implement in-memory methods**

In `InMemoryTicketRepository.__init__`, add:

```python
self._billing_response_tokens: dict[str, dict[str, Any]] = {}
```

Add methods near existing billing ticket methods:

```python
def save_billing_response_token(self, token: dict[str, Any]) -> None:
    token_hash = str(token.get("token_hash") or "").strip()
    if not token_hash:
        raise ValueError("token_hash is required")
    saved = copy.deepcopy(token)
    saved.setdefault("created_at", _utc_now())
    saved.setdefault("used_at", None)
    self._billing_response_tokens[token_hash] = saved


def get_billing_response_token(self, token_hash: str) -> dict[str, Any] | None:
    item = self._billing_response_tokens.get(str(token_hash).strip())
    return copy.deepcopy(item) if item is not None else None


def mark_billing_response_token_used(self, token_hash: str, used_at: str) -> bool:
    item = self._billing_response_tokens.get(str(token_hash).strip())
    if item is None or item.get("used_at"):
        return False
    item["used_at"] = used_at
    return True
```

- [ ] **Step 4: Add PostgreSQL schema support**

In both `backend/sql/ticket_storage.sql` and PostgreSQL initialization in `backend/repositories/ticket_repository.py`, add a compact table:

```sql
CREATE TABLE IF NOT EXISTS support_billing_response_tokens (
    token_hash TEXT PRIMARY KEY,
    billing_ticket_id TEXT NOT NULL REFERENCES support_billing_tickets(billing_ticket_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_support_billing_response_tokens_ticket
    ON support_billing_response_tokens (billing_ticket_id, created_at DESC);
```

Add PostgreSQL repository methods with `used_at IS NULL` guard:

```sql
UPDATE support_billing_response_tokens
SET used_at = %s
WHERE token_hash = %s AND used_at IS NULL
```

Return `cur.rowcount == 1`.

- [ ] **Step 5: Run token tests**

Run: `python3 -m unittest backend.tests.test_billing_response_flow.BillingResponseTokenRepositoryTests`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/repositories/ticket_repository.py backend/sql/ticket_storage.sql backend/tests/test_billing_response_flow.py
git commit -m "feat: persist billing response tokens"
```

---

### Task 2: Billing Response Flow Service

**Files:**
- Create: `backend/services/billing_response_flow.py`
- Test: `backend/tests/test_billing_response_flow.py`

- [ ] **Step 1: Add failing service tests**

Append tests:

```python
from backend.services.billing_response_flow import (
    BillingResolutionValidationError,
    build_billing_internal_resolution_event,
    generate_billing_response_token,
    hash_billing_response_token,
    validate_billing_resolution_submission,
)


class BillingResponseFlowServiceTests(unittest.TestCase):
    def test_token_hash_does_not_equal_raw_token(self) -> None:
        raw = generate_billing_response_token()
        self.assertGreaterEqual(len(raw), 32)
        self.assertNotEqual(hash_billing_response_token(raw), raw)

    def test_completed_allows_empty_note(self) -> None:
        payload = validate_billing_resolution_submission(
            result="completed",
            notify_customer=True,
            note="",
        )
        self.assertEqual(payload["result"], "completed")
        self.assertTrue(payload["notify_customer"])
        self.assertEqual(payload["note"], "")

    def test_refused_requires_note(self) -> None:
        with self.assertRaises(BillingResolutionValidationError):
            validate_billing_resolution_submission(
                result="refused",
                notify_customer=True,
                note="",
            )

    def test_customer_action_required_requires_note(self) -> None:
        with self.assertRaises(BillingResolutionValidationError):
            validate_billing_resolution_submission(
                result="customer_action_required",
                notify_customer=True,
                note="",
            )

    def test_internal_resolution_event_shape(self) -> None:
        event = build_billing_internal_resolution_event(
            billing_ticket_id="BT-TK-ACC-123456",
            client_ticket_id="TK-ACC-123456",
            result="completed",
            notify_customer=False,
            note="",
            created_at="2026-06-19T00:00:00+00:00",
        )
        self.assertEqual(event["event"], "billing_internal_resolution_submitted")
        self.assertEqual(event["billing_ticket_id"], "BT-TK-ACC-123456")
        self.assertFalse(event["notify_customer"])
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m unittest backend.tests.test_billing_response_flow.BillingResponseFlowServiceTests`

Expected: FAIL because `backend.services.billing_response_flow` does not exist.

- [ ] **Step 3: Implement service constants and validation**

Create `backend/services/billing_response_flow.py`:

```python
from __future__ import annotations

import hashlib
import secrets
from typing import Any

BILLING_RESPONSE_RESULT_COMPLETED = "completed"
BILLING_RESPONSE_RESULT_REFUSED = "refused"
BILLING_RESPONSE_RESULT_CUSTOMER_ACTION_REQUIRED = "customer_action_required"
BILLING_RESPONSE_EVENT = "billing_internal_resolution_submitted"
BILLING_RESPONSE_AI_FOLLOWUP_EVENT = "billing_customer_followup_generated"

_VALID_RESULTS = {
    BILLING_RESPONSE_RESULT_COMPLETED,
    BILLING_RESPONSE_RESULT_REFUSED,
    BILLING_RESPONSE_RESULT_CUSTOMER_ACTION_REQUIRED,
}


class BillingResolutionValidationError(ValueError):
    pass


def generate_billing_response_token() -> str:
    return secrets.token_urlsafe(32)


def hash_billing_response_token(token: str) -> str:
    normalized = str(token or "").strip()
    if not normalized:
        raise BillingResolutionValidationError("token is required")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_billing_resolution_submission(*, result: str, notify_customer: bool, note: str | None) -> dict[str, Any]:
    normalized_result = " ".join(str(result or "").split()).strip().lower()
    normalized_note = str(note or "").strip()
    if normalized_result not in _VALID_RESULTS:
        raise BillingResolutionValidationError("invalid result")
    if normalized_result != BILLING_RESPONSE_RESULT_COMPLETED and not normalized_note:
        raise BillingResolutionValidationError("note is required for this result")
    return {
        "result": normalized_result,
        "notify_customer": bool(notify_customer),
        "note": normalized_note,
    }
```

- [ ] **Step 4: Implement event builder**

Add:

```python
def build_billing_internal_resolution_event(
    *,
    billing_ticket_id: str,
    client_ticket_id: str,
    result: str,
    notify_customer: bool,
    note: str,
    created_at: str,
) -> dict[str, Any]:
    return {
        "event": BILLING_RESPONSE_EVENT,
        "billing_ticket_id": billing_ticket_id,
        "ticket_id": client_ticket_id,
        "result": result,
        "notify_customer": notify_customer,
        "note": note,
        "created_at": created_at,
        "source": "billing_response_link",
    }
```

- [ ] **Step 5: Add customer follow-up generator tests**

Add tests that patch LLM failure and verify deterministic fallback:

```python
from unittest.mock import patch
from backend.services.billing_response_flow import build_customer_followup_from_resolution


def test_completed_followup_uses_note_when_present(self) -> None:
    text = build_customer_followup_from_resolution(
        result="completed",
        note="Detailed invoice has been sent to your email.",
        customer_message="Please send invoice.",
        title="Detailed invoice request",
    )
    self.assertIn("Detailed invoice has been sent", text)


def test_customer_action_followup_uses_note(self) -> None:
    text = build_customer_followup_from_resolution(
        result="customer_action_required",
        note="Please confirm the billing account ID.",
        customer_message="Please help.",
        title="Billing request",
    )
    self.assertIn("billing account ID", text)
```

- [ ] **Step 6: Implement deterministic fallback first**

```python
def build_customer_followup_from_resolution(
    *,
    result: str,
    note: str,
    customer_message: str,
    title: str,
) -> str:
    clean_note = " ".join(str(note or "").split()).strip()
    if result == BILLING_RESPONSE_RESULT_COMPLETED:
        detail = clean_note or "Your billing request has been processed."
        return f"Hi,\n\n{detail}\n\nPlease let us know if you need any further assistance."
    if result == BILLING_RESPONSE_RESULT_REFUSED:
        detail = clean_note or "We are unable to process this billing request based on the current information."
        return f"Hi,\n\n{detail}\n\nPlease let us know if you have additional information for review."
    detail = clean_note or "We need additional information from you before we can continue processing this billing request."
    return f"Hi,\n\n{detail}\n\nOnce we receive it, we will continue the review."
```

- [ ] **Step 7: Add LLM wrapper with fallback**

Add a wrapper that attempts `invoke_responses_text` using existing model profile conventions, but returns deterministic fallback on empty text or exception. Keep it isolated so tests can patch it. This introduces a customer-facing AI behavior change, so Task 9 must update `docs/prompt_change_log.md`.

- [ ] **Step 8: Run service tests**

Run: `python3 -m unittest backend.tests.test_billing_response_flow`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/services/billing_response_flow.py backend/tests/test_billing_response_flow.py
git commit -m "feat: add billing response flow service"
```

---

### Task 3: Internal Email Response Link

**Files:**
- Modify: `backend/services/billing_automation.py`
- Modify: `backend/main.py`
- Test: `backend/tests/test_account_intake.py`

- [ ] **Step 1: Add failing email tests**

In `backend/tests/test_account_intake.py`, add or extend an invoice automation test:

```python
def test_billing_internal_email_includes_billing_ticket_id_and_response_link(self) -> None:
    captured_payloads: list[dict[str, str]] = []

    def fake_send(payload: dict[str, str]) -> dict[str, str]:
        captured_payloads.append(payload)
        return {"status": "sent", "reason": ""}

    with patch.object(main, "dispatch_event", AsyncMock()), patch("backend.main.send_billing_internal_email", side_effect=fake_send):
        response = self.client.post(
            "/account",
            json={
                "title": "Detailed invoice request",
                "question": "Please send detailed invoice. Issue date: 6 May 2026. Transaction ID: 1104245232004173824. Amount: USD 705.97.",
                "customer_email": "customer@example.com",
                "source": "account-ui",
            },
        )

    self.assertEqual(response.status_code, 200, response.text)
    payload = response.json()
    self.assertTrue(captured_payloads)
    body = captured_payloads[0]["body"]
    self.assertIn(f"Billing Ticket ID: {payload['billing_ticket_id']}", body)
    self.assertIn("https://support.stellarix.space/response?token=", body)
    self.assertNotIn("Available actions", body)
```

- [ ] **Step 2: Run test to verify failure**

Run: `python3 -m unittest backend.tests.test_account_intake.AccountIntakeApiTests.test_billing_internal_email_includes_billing_ticket_id_and_response_link`

Expected: FAIL because email currently says `Ticket ID` and has no response link.

- [ ] **Step 3: Generate token before email send**

In `backend/main.py`:

- Move `billing_ticket_id = f"BT-{ticket_id}"` before `build_billing_automation_result`.
- When `is_billing_automation_route` is true and fields are complete, generate raw token/hash and save token before sending email.
- Build link as `https://support.stellarix.space/response?token={raw_token}` for now. Prefer env override `BILLING_RESPONSE_PUBLIC_BASE_URL` only if simple; default must be `https://support.stellarix.space`.

Pseudo-code:

```python
billing_ticket_id = f"BT-{ticket_id}"
response_link = None
if billing_result.internal_email:
    raw_token = generate_billing_response_token()
    token_hash = hash_billing_response_token(raw_token)
    response_link = f"{_billing_response_base_url()}/response?token={raw_token}"
    await async_to_thread(ticket_repository.save_billing_response_token, {
        "token_hash": token_hash,
        "billing_ticket_id": billing_ticket_id,
        "created_at": now_iso(),
        "used_at": None,
    })
```

- [ ] **Step 4: Update email builder signature**

In `backend/services/billing_automation.py`, update `build_billing_automation_result` and `_build_internal_email` to accept:

```python
billing_ticket_id: str | None = None
response_link: str | None = None
```

Email body section should use:

```text
Billing Ticket ID: BT-...
Customer email: customer@example.com
```

And later:

```text
Please review and submit the handling result here:
https://support.stellarix.space/response?token=...

Please review and follow up as appropriate.
```

Do not include available action labels.

- [ ] **Step 5: Save email payload with token-free storage decision**

Store `internal_email_payload` as current payload. It will include the raw link, which contains the raw token. For demo this is acceptable only if acknowledged. If avoiding raw-token persistence is desired, store a redacted copy in `support_billing_tickets.internal_email_payload` and send the unredacted payload. Choose one and keep tests explicit.

Recommended for demo: store redacted copy:

```python
internal_email_payload = _redact_billing_response_token(dict(billing_result.internal_email))
```

- [ ] **Step 6: Run account intake tests**

Run: `python3 -m unittest backend.tests.test_account_intake.AccountIntakeApiTests`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/services/billing_automation.py backend/tests/test_account_intake.py
git commit -m "feat: add billing response link to internal email"
```

---

### Task 4: Response Lookup and Submit API

**Files:**
- Modify: `backend/main.py`
- Test: `backend/tests/test_account_intake.py`

- [ ] **Step 1: Add failing API tests**

Add tests for lookup, submit, validation, and one-time behavior:

```python
def _create_invoice_ticket_with_response_token(self) -> dict[str, object]:
    with patch.object(main, "dispatch_event", AsyncMock()), patch(
        "backend.main.send_billing_internal_email",
        return_value={"status": "sent", "reason": ""},
    ):
        response = self.client.post(
            "/account",
            json={
                "title": "Detailed invoice request",
                "question": "Please send detailed invoice. Issue date: 6 May 2026. Transaction ID: 1104245232004173824. Amount: USD 705.97.",
                "customer_email": "customer@example.com",
                "source": "account-ui",
            },
        )
    self.assertEqual(response.status_code, 200, response.text)
    return response.json()


def test_billing_response_lookup_returns_context_for_valid_token(self) -> None:
    payload = self._create_invoice_ticket_with_response_token()
    # Extract raw token from captured email or expose test helper; prefer captured email.
    token = self._extract_response_token_from_last_email()

    lookup = self.client.get(f"/api/billing-response?token={token}")

    self.assertEqual(lookup.status_code, 200, lookup.text)
    data = lookup.json()
    self.assertEqual(data["billing_ticket_id"], payload["billing_ticket_id"])
    self.assertEqual(data["customer_email"], "customer@example.com")
    self.assertFalse(data["submitted"])


def test_billing_response_submit_records_event_and_customer_reply(self) -> None:
    payload = self._create_invoice_ticket_with_response_token()
    token = self._extract_response_token_from_last_email()

    with patch.object(main, "dispatch_event", AsyncMock()):
        submit = self.client.post(
            "/api/billing-response/submit",
            json={"token": token, "result": "completed", "notify_customer": True, "note": ""},
        )

    self.assertEqual(submit.status_code, 200, submit.text)
    self.assertTrue(submit.json()["submitted"])

    events = self.repository.list_ticket_events(payload["ticket_id"], limit=20)
    self.assertTrue(any(e["event_type"] == "billing_internal_resolution_submitted" for e in events))
    ticket = self.repository.get_ticket(payload["ticket_id"])
    assert ticket is not None
    self.assertEqual(ticket["messages"][-1]["role"], "assistant")
    self.assertEqual(ticket["messages"][-1]["source"], "billing_response_ai")


def test_billing_response_submit_rejects_second_submit(self) -> None:
    payload = self._create_invoice_ticket_with_response_token()
    token = self._extract_response_token_from_last_email()
    first = self.client.post("/api/billing-response/submit", json={"token": token, "result": "completed", "notify_customer": False, "note": ""})
    self.assertEqual(first.status_code, 200, first.text)

    second = self.client.post("/api/billing-response/submit", json={"token": token, "result": "completed", "notify_customer": False, "note": ""})
    self.assertEqual(second.status_code, 409, second.text)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m unittest backend.tests.test_account_intake.AccountIntakeApiTests -k billing_response`

If `-k` is unavailable, run the whole class.

Expected: FAIL because APIs do not exist.

- [ ] **Step 3: Add request/response models**

In `backend/main.py` near existing account request models:

```python
class BillingResponseSubmitRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    result: str = Field(pattern="^(completed|refused|customer_action_required)$")
    notify_customer: bool
    note: str | None = Field(default=None, max_length=4000)
```

- [ ] **Step 4: Add lookup endpoint**

Implement:

```python
@app.get("/api/billing-response")
def get_billing_response_context(token: str) -> dict[str, Any]:
    token_hash = hash_billing_response_token(token)
    saved_token = ticket_repository.get_billing_response_token(token_hash)
    if saved_token is None:
        raise HTTPException(status_code=404, detail="invalid response link")
    billing_ticket = ticket_repository.get_billing_ticket(saved_token["billing_ticket_id"])
    if billing_ticket is None:
        raise HTTPException(status_code=404, detail="billing ticket not found")
    return {
        "billing_ticket_id": billing_ticket["billing_ticket_id"],
        "submitted": bool(saved_token.get("used_at")),
        "customer_email": _billing_customer_email(billing_ticket),
        "title": billing_ticket.get("title") or "",
        "question": billing_ticket.get("question") or "",
        "collected_fields": billing_ticket.get("collected_fields") or {},
    }
```

Do not expose raw support ticket id.

- [ ] **Step 5: Add submit endpoint**

Implement happy path in order:

1. Hash token.
2. Load token.
3. Reject missing token with 404.
4. Reject already used token with 409.
5. Load billing ticket.
6. Validate form with `validate_billing_resolution_submission`.
7. Mark token used with atomic repository method.
8. Build and record `billing_internal_resolution_submitted` event against `client_ticket_id` internally.
9. If `notify_customer` is false, return submitted with no AI reply.
10. If true, generate customer follow-up, append assistant message to canonical ticket, record `billing_customer_followup_generated`, dispatch websocket events.

Important ordering: event first, AI follow-up second.

- [ ] **Step 6: Add status updates**

Update billing ticket `automation_status` on submit:

- `completed` + notify yes: `customer_notified`
- `completed` + notify no: `resolved_without_customer_notification`
- `refused`: `customer_notified` if notify yes, else `resolved_without_customer_notification`
- `customer_action_required`: `waiting_customer_action` if notify yes, else `internal_resolution_submitted`

Save updated billing ticket.

- [ ] **Step 7: Run API tests**

Run: `python3 -m unittest backend.tests.test_account_intake.AccountIntakeApiTests`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/main.py backend/tests/test_account_intake.py
git commit -m "feat: submit billing internal resolutions"
```

---

### Task 5: Static `/response` UI

**Files:**
- Create: `ui/billing-response-ui/index.html`
- Create: `ui/billing-response-ui/app.js`
- Create: `ui/billing-response-ui/styles.css`
- Modify: `backend/main.py`
- Test: `backend/tests/test_billing_response_ui_contract.py`

- [ ] **Step 1: Write failing UI contract tests**

Create `backend/tests/test_billing_response_ui_contract.py`:

```python
from __future__ import annotations

from pathlib import Path
import unittest


class BillingResponseUiContractTests(unittest.TestCase):
    def test_backend_mounts_response_ui(self) -> None:
        source = Path("backend/main.py").read_text(encoding="utf-8")
        self.assertIn("BILLING_RESPONSE_DIR", source)
        self.assertIn('app.mount("/response", StaticFiles(directory=BILLING_RESPONSE_DIR, html=True), name="billing-response-ui")', source)

    def test_response_ui_contains_required_form_fields(self) -> None:
        app = Path("ui/billing-response-ui/app.js").read_text(encoding="utf-8")
        index = Path("ui/billing-response-ui/index.html").read_text(encoding="utf-8")
        styles = Path("ui/billing-response-ui/styles.css").read_text(encoding="utf-8")

        for term in ["completed", "refused", "customer_action_required", "notify_customer", "note"]:
            with self.subTest(term=term):
                self.assertIn(term, app)
        self.assertIn("/api/billing-response", app)
        self.assertIn("/api/billing-response/submit", app)
        self.assertIn("Submit handling result", index)
        self.assertIn("billing_ticket_id", app)
        self.assertIn("response-card", styles)
```

- [ ] **Step 2: Run test to verify failure**

Run: `python3 -m unittest backend.tests.test_billing_response_ui_contract`

Expected: FAIL because files/mount do not exist.

- [ ] **Step 3: Mount static UI**

In `backend/main.py`:

```python
BILLING_RESPONSE_DIR = UI_DIR / "billing-response-ui"
...
if BILLING_RESPONSE_DIR.exists():
    app.mount("/response", StaticFiles(directory=BILLING_RESPONSE_DIR, html=True), name="billing-response-ui")
```

- [ ] **Step 4: Create `index.html`**

Use a minimal shell:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Submit handling result</title>
  <link rel="stylesheet" href="/response/styles.css" />
</head>
<body>
  <main id="app" class="response-shell"></main>
  <script src="/response/app.js" defer></script>
</body>
</html>
```

- [ ] **Step 5: Create `app.js`**

Implement:

- Read `token` from query string.
- GET `/api/billing-response?token=...`.
- Render invalid, already-submitted, loading, or form states.
- Form options:
  - `completed` label `已完成`
  - `refused` label `拒绝处理`
  - `customer_action_required` label `需要客户操作`
  - `notify_customer` boolean radio labels `是` / `否`
- Client-side rule: note required when result is `refused` or `customer_action_required`.
- POST `/api/billing-response/submit`.
- Success page says submitted; no second submit.

- [ ] **Step 6: Create `styles.css`**

Keep it small, responsive, and visually aligned with current account UI without adding a new design system.

- [ ] **Step 7: Run UI contract test**

Run: `python3 -m unittest backend.tests.test_billing_response_ui_contract`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/main.py ui/billing-response-ui backend/tests/test_billing_response_ui_contract.py
git commit -m "feat: add billing response UI"
```

---

### Task 6: AI Follow-up Safety and Event Semantics

**Files:**
- Modify: `backend/services/billing_response_flow.py`
- Modify: `backend/main.py`
- Test: `backend/tests/test_billing_response_flow.py`
- Test: `backend/tests/test_account_intake.py`

- [ ] **Step 1: Add tests for no-notify behavior**

Test submit with `notify_customer=false`:

- Internal resolution event exists.
- No assistant message is appended after submit.
- Return payload says `customer_notified: false`.

- [ ] **Step 2: Add tests for refused/customer action note enforcement through API**

Test `refused` with empty note returns `400` and does not mark token used.

Test `customer_action_required` with empty note returns `400` and does not mark token used.

- [ ] **Step 3: Add tests for AI fallback event**

When `notify_customer=true`, verify second event:

```python
self.assertTrue(any(e["event_type"] == "billing_customer_followup_generated" for e in events))
```

- [ ] **Step 4: Implement API error mapping**

Map `BillingResolutionValidationError` to HTTP 400.

Ensure validation happens before token is marked used.

- [ ] **Step 5: Implement AI follow-up event payload**

Record:

```python
{
    "event": "billing_customer_followup_generated",
    "billing_ticket_id": billing_ticket_id,
    "ticket_id": client_ticket_id,
    "resolution_result": result,
    "notify_customer": True,
    "customer_reply": reply_text,
    "source": "billing_response_ai",
    "created_at": now_iso(),
}
```

- [ ] **Step 6: Run focused tests**

Run: `python3 -m unittest backend.tests.test_billing_response_flow backend.tests.test_account_intake.AccountIntakeApiTests`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/services/billing_response_flow.py backend/tests/test_billing_response_flow.py backend/tests/test_account_intake.py
git commit -m "feat: generate billing customer followups from resolution events"
```

---

### Task 7: Account/Billing Detail Visibility

**Files:**
- Modify: `backend/main.py`
- Modify: `ui/account-ui/app.js` only if useful for demo visibility
- Test: `backend/tests/test_account_intake.py`
- Test: `backend/tests/test_account_ui_contract.py` only if UI changes

- [ ] **Step 1: Add detail API expectations**

Ensure `GET /api/account/billing-tickets/{billing_ticket_id}` returns enough to show the internal resolution status after submit:

- `automation_status`
- latest customer/assistant messages
- `internal_email_send_status`
- optionally latest resolution event summary if adding it to the view model

- [ ] **Step 2: Decide if account UI needs visible response status**

YAGNI default: do not change `ui/account-ui` unless demo requires operators to see the result there. The `/response` success page and canonical ticket messages are enough for the first flow.

- [ ] **Step 3: Run account UI contract if modified**

Run: `python3 -m unittest backend.tests.test_account_ui_contract`

Expected: PASS.

- [ ] **Step 4: Commit if changed**

```bash
git add backend/main.py ui/account-ui/app.js backend/tests/test_account_intake.py backend/tests/test_account_ui_contract.py
git commit -m "feat: expose billing response status in account detail"
```

Skip commit if no changes are needed.

---

### Task 8: Documentation and Roadmap Updates

**Files:**
- Modify: `docs/prompt_change_log.md`
- Modify: `docs/feature_list.md`
- Modify: `docs/roadmap.html`
- Test: `backend/tests/test_roadmap_contract.py`
- Test: `scripts/verify_feature_list.py`

- [ ] **Step 1: Update prompt change log**

Add an entry dated `2026-06-19`:

- Area: Billing automation response follow-up.
- Version: `billing-internal-resolution-followup-v1`.
- Summary: AI customer follow-up is triggered from structured internal resolution events.
- Reason: close the billing internal handling loop without parsing free-form email replies.
- Affected files: `backend/services/billing_response_flow.py`, `backend/main.py`.
- Expected behavior: notify/no-notify follows handler choice; refused/customer-action require explanation.
- Verification: focused unit/API tests.

- [ ] **Step 2: Update feature list**

Add/move one short major feature entry under the appropriate category, likely `Ticket Dashboard` or a billing/account category if present. Keep the file's existing category order and short-sentence rule.

Example sentence:

```text
Billing automation supports one-time internal response links that trigger AI customer follow-up from structured handling results.
```

- [ ] **Step 3: Update roadmap**

In `docs/roadmap.html`, mark these billing tasks done:

- `billing-response-link`
- `billing-internal-resolution-event`

Update the Billing tab review/done text to say the demo response-link flow is implemented.

- [ ] **Step 4: Run docs tests**

Run:

```bash
python3 scripts/verify_feature_list.py
python3 -m unittest backend.tests.test_roadmap_contract
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/prompt_change_log.md docs/feature_list.md docs/roadmap.html
git commit -m "docs: record billing response link rollout"
```

---

### Task 9: End-to-End Verification

**Files:**
- No code changes expected.

- [ ] **Step 1: Run focused backend tests**

```bash
python3 -m unittest \
  backend.tests.test_billing_response_flow \
  backend.tests.test_account_intake \
  backend.tests.test_billing_response_ui_contract \
  backend.tests.test_account_ui_contract \
  backend.tests.test_repository_configuration \
  backend.tests.test_roadmap_contract
```

Expected: all tests pass.

- [ ] **Step 2: Run feature list verification**

```bash
python3 scripts/verify_feature_list.py
```

Expected: PASS.

- [ ] **Step 3: Manual API smoke in local TestClient or running stack**

Minimum smoke scenario:

1. POST `/account` with detailed invoice fields complete.
2. Confirm internal email payload contains `https://support.stellarix.space/response?token=...`.
3. GET `/api/billing-response?token=...`.
4. POST `/api/billing-response/submit` with `{result: "completed", notify_customer: true, note: ""}`.
5. Confirm second POST with same token returns 409.
6. Confirm canonical ticket has assistant message source `billing_response_ai`.
7. Confirm ticket events include `billing_internal_resolution_submitted` and `billing_customer_followup_generated`.

- [ ] **Step 4: Stack relevance classification**

This implementation touches `backend/`, `ui/`, and runtime routing, so classify as `功能类/重大行为变更` and stack-relevant.

- [ ] **Step 5: Pre-finalization verification**

Run the exact focused verification command from Step 1 plus `python3 scripts/verify_feature_list.py` immediately before finalization.

- [ ] **Step 6: Finalize through repository workflow**

Use:

```bash
scripts/workflow/finalize_task_to_main.sh <branch> --verify "python3 -m unittest backend.tests.test_billing_response_flow backend.tests.test_account_intake backend.tests.test_billing_response_ui_contract backend.tests.test_account_ui_contract backend.tests.test_repository_configuration backend.tests.test_roadmap_contract && python3 scripts/verify_feature_list.py"
```

- [ ] **Step 7: Post-merge live stack verification**

Because this is stack-relevant, after merge from root `main`:

```bash
bash scripts/workflow/inspect_single_host_stack_mode.sh
bash scripts/workflow/restart_single_host_lightweight_stack.sh
curl -fsS http://localhost:8000/health
```

Then verify task-specific live markers:

- `/response` serves `Submit handling result`.
- `/response/app.js` includes `customer_action_required`.
- `/health` `app_build.ref` matches merged `main` commit.

---

## Implementation Notes and Guardrails

- Do not parse reply emails in this implementation.
- Do not add token expiry or recipient validation yet.
- Do not create separate UI variants by billing category yet.
- Do not expose raw support ticket id in `/response` UI; use `billing_ticket_id` only.
- Do not let failed validation consume a one-time token.
- Do not send a customer reply when `notify_customer=false`.
- For `refused` and `customer_action_required`, require `note` before token consumption.
- AI prompt/fallback must not invent missing reasons or customer actions; if note is empty for a non-completed result, validation must reject before AI runs.
- If LLM generation fails, use deterministic fallback text and record the error in the follow-up event payload.
