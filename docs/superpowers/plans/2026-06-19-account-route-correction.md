# Account Route Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator manually correct a mis-routed `/account` billing ticket's full route tuple, persist the correction as a recordable case, and add a "Route errors" filter (corrected-by-me + low router confidence) plus a summary panel in `/account` for systematic analysis.

**Architecture:** Add a `support_billing_route_corrections` table (in-memory + PostgreSQL) that stores the original AI route tuple, the corrected route tuple, corrector, reason, and timestamp alongside the billing ticket. Persist the active billing ticket route tuple columns (`scope_label`, `route_family`, `execution_action`, `tooling_profile`) in PostgreSQL so corrections survive reloads. Add a `POST /api/account/billing-tickets/{id}/route-correction` endpoint that validates the new tuple against the router contract, writes the correction record, and updates the billing ticket's active route fields. Record a `route_corrected` ticket event. Extend the list/detail API to surface correction state and low-confidence flag. Add a "Route errors" filter chip and a summary panel in `ui/account-ui` that aggregates predicted-vs-corrected counts.

**Tech Stack:** FastAPI (`backend/main.py`), ticket repository abstraction (`backend/repositories/ticket_repository.py`), `backend/services/support_router.py` route contract (`_route_contract_for_scope`), PostgreSQL schema doc/init SQL, vanilla static UI (`ui/account-ui`), Python `unittest`/FastAPI `TestClient`.

---

## Confirmed Product Rules

- The correctable field is the **full route tuple**: `scope_label` + `route_family` + `execution_action` + `tooling_profile`. The original AI tuple is preserved verbatim in the correction record for analysis.
- The corrector picks `scope_label` first; the backend derives `route_family` / `execution_action` / `tooling_profile` from `_route_contract_for_scope` so the corrected tuple is always internally consistent. The corrector also supplies a free-text `execution_action` override (selected from the valid actions for the chosen scope) and an optional `note`.
- A route may be corrected only once per billing ticket. Re-correcting overwrites the corrected tuple and appends a new event but does not create a second correction row (one row per ticket, updated in place). Keep the original AI tuple immutable after the first correction, and keep the *first* corrected tuple as `first_corrected_*` for stability analysis.
- "Route error" = `(has route correction)` **OR** `(router_confidence < INTENT_ROUTER_CONFIDENCE_THRESHOLD)`. The filter is a single chip "Route errors" alongside the existing All / Automation / Not automated chips.
- The summary panel appears only when the "Route errors" filter is active. It shows: total error cases, corrected count, low-confidence count, and a breakdown of predicted→corrected route transitions (top transitions only).
- Correction does **not** re-run billing automation or re-send internal email. It only updates the routing classification fields and records the case. The customer reply and automation_status are left as-is (correcting the route is a classification fix, not a re-execution).
- Low-confidence detection uses the same threshold env as the router: `INTENT_ROUTER_CONFIDENCE_THRESHOLD` (default `0.7`). The billing ticket already stores `route_confidence` (from `decision.confidence`) and `intent_router_model_confidence`; use `route_confidence` for the flag since that is what the router settled on.

## Valid Route Tuples (from `backend/services/support_router.py:_route_contract_for_scope`)

These are the only valid `(scope_label, execution_action)` pairs the corrector may choose. The backend derives `route_family` and `tooling_profile` from them.

| scope_label | execution_action (route) | route_family | tooling_profile |
|---|---|---|---|
| `ticket_resolution` | `resolve_ticket` | `ticket_resolution` | `deterministic_resolution` |
| `billing` | `account_suspension` | `billing_automation` | `deterministic_billing_intake` |
| `billing` | `detailed_invoice` | `billing_automation` | `deterministic_billing_intake` |
| `billing` | `account_verification` | `billing_automation` | `deterministic_billing_intake` |
| `billing` | `human_review_required` | `billing_review` | `deterministic_billing_intake` |
| `billing` | `refuse` | `fallback_or_refuse` | `no_agora_docs_refusal` |
| `agora_technical` | `rag` | `agora_docs_rag` | `agora_docs_only` |
| `agora_non_technical` | `web_search` | `web_company_info` | `official_web_search` |
| `agora_non_technical` | `refuse` | `web_company_info` | `no_agora_docs_refusal` |
| `small_talk` | `controlled_response` | `general_chat` | `controlled_acknowledgement` |
| `small_talk` | `refuse` | `general_chat` | `no_agora_docs_refusal` |
| `non_agora` | `refuse` | `fallback_or_refuse` | `no_agora_docs_refusal` |

## File Map

- Create `backend/services/route_correction.py`
  - Valid route tuple dictionary mirroring `_route_contract_for_scope`.
  - `validate_route_correction(...)` returning normalized scope/action + derived family/tooling.
  - `RouteCorrectionValidationError`.
- Modify `backend/repositories/ticket_repository.py`
  - Add in-memory `_billing_route_corrections: dict[str, dict[str, Any]]` keyed by `billing_ticket_id`.
  - Add `save_billing_route_correction`, `get_billing_route_correction`, `list_billing_route_corrections`.
  - Add PostgreSQL persistence for billing ticket active route tuple columns: `scope_label`, `route_family`, `execution_action`, `tooling_profile`.
  - Add PostgreSQL table init (`support_billing_route_corrections`) + CRUD methods.
  - Add to `TicketRepository` Protocol.
- Modify `backend/sql/ticket_storage.sql`
  - Document the billing ticket active route tuple columns and the `support_billing_route_corrections` table schema/index.
- Modify `backend/main.py`
  - Add `BillingRouteCorrectionRequest` model.
  - Add `POST /api/account/billing-tickets/{billing_ticket_id}/route-correction`.
  - Extend list/detail view models with `route_corrected` + `route_error` + correction fields.
  - Add `GET /api/account/route-errors/summary`.
- Modify `ui/account-ui/app.js`
  - Add "Route errors" filter chip + state.
  - Add route correction control in detail view (scope select → action select → note → submit).
  - Add summary panel when "Route errors" filter active.
- Modify `ui/account-ui/styles.css`
  - Styles for correction control + summary panel (reuse existing tokens).
- Add/modify tests:
  - Create `backend/tests/test_route_correction.py`.
  - Modify `backend/tests/test_account_intake.py`.
  - Modify `backend/tests/test_account_ui_contract.py`.
  - Modify `backend/tests/test_repository_configuration.py`.
- Update docs after implementation:
  - `docs/feature_list.md` (major routing/analysis capability).
  - `docs/roadmap.html` (required for this task; update the relevant route-quality/account-analysis lane or add a concise roadmap marker if no lane exists).

---

### Task 1: Route Correction Service and Tuple Dictionary

**Files:**
- Create: `backend/services/route_correction.py`
- Test: `backend/tests/test_route_correction.py`

- [ ] **Step 1: Write failing tests for tuple validation**

Create `backend/tests/test_route_correction.py`:

```python
from __future__ import annotations

import os
import unittest

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from backend.services.route_correction import (
    RouteCorrectionValidationError,
    VALID_ROUTE_TUPLES,
    validate_route_correction,
)


class RouteCorrectionValidationTests(unittest.TestCase):
    def test_valid_billing_detailed_invoice_derives_full_tuple(self) -> None:
        result = validate_route_correction(scope_label="billing", execution_action="detailed_invoice")
        self.assertEqual(result["scope_label"], "billing")
        self.assertEqual(result["execution_action"], "detailed_invoice")
        self.assertEqual(result["route_family"], "billing_automation")
        self.assertEqual(result["tooling_profile"], "deterministic_billing_intake")

    def test_valid_agora_technical_rag(self) -> None:
        result = validate_route_correction(scope_label="agora_technical", execution_action="rag")
        self.assertEqual(result["route_family"], "agora_docs_rag")
        self.assertEqual(result["tooling_profile"], "agora_docs_only")

    def test_invalid_scope_rejected(self) -> None:
        with self.assertRaises(RouteCorrectionValidationError):
            validate_route_correction(scope_label="unknown_scope", execution_action="rag")

    def test_invalid_action_for_scope_rejected(self) -> None:
        with self.assertRaises(RouteCorrectionValidationError):
            validate_route_correction(scope_label="billing", execution_action="rag")

    def test_whitespace_and_case_normalized(self) -> None:
        result = validate_route_correction(scope_label="  Billing  ", execution_action="DETAILED_INVOICE")
        self.assertEqual(result["scope_label"], "billing")
        self.assertEqual(result["execution_action"], "detailed_invoice")

    def test_note_normalized_and_optional(self) -> None:
        result = validate_route_correction(
            scope_label="billing",
            execution_action="human_review_required",
            note="  refund dispute  ",
        )
        self.assertEqual(result["note"], "refund dispute")
        empty = validate_route_correction(
            scope_label="billing",
            execution_action="human_review_required",
        )
        self.assertEqual(empty["note"], "")

    def test_valid_tuple_dictionary_matches_contract(self) -> None:
        # Sanity: every tuple in VALID_ROUTE_TUPLES has all four keys.
        for item in VALID_ROUTE_TUPLES:
            self.assertIn("scope_label", item)
            self.assertIn("execution_action", item)
            self.assertIn("route_family", item)
            self.assertIn("tooling_profile", item)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest backend.tests.test_route_correction`

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.services.route_correction'`.

- [ ] **Step 3: Implement the tuple dictionary and validation**

Create `backend/services/route_correction.py`:

```python
from __future__ import annotations

from typing import Any

# Valid (scope_label, execution_action) -> (route_family, tooling_profile).
# Mirrors backend/services/support_router.py:_route_contract_for_scope so a
# corrected route tuple is always internally consistent with the router contract.
VALID_ROUTE_TUPLES: list[dict[str, str]] = [
    {"scope_label": "ticket_resolution", "execution_action": "resolve_ticket",
     "route_family": "ticket_resolution", "tooling_profile": "deterministic_resolution"},
    {"scope_label": "billing", "execution_action": "account_suspension",
     "route_family": "billing_automation", "tooling_profile": "deterministic_billing_intake"},
    {"scope_label": "billing", "execution_action": "detailed_invoice",
     "route_family": "billing_automation", "tooling_profile": "deterministic_billing_intake"},
    {"scope_label": "billing", "execution_action": "account_verification",
     "route_family": "billing_automation", "tooling_profile": "deterministic_billing_intake"},
    {"scope_label": "billing", "execution_action": "human_review_required",
     "route_family": "billing_review", "tooling_profile": "deterministic_billing_intake"},
    {"scope_label": "billing", "execution_action": "refuse",
     "route_family": "fallback_or_refuse", "tooling_profile": "no_agora_docs_refusal"},
    {"scope_label": "agora_technical", "execution_action": "rag",
     "route_family": "agora_docs_rag", "tooling_profile": "agora_docs_only"},
    {"scope_label": "agora_non_technical", "execution_action": "web_search",
     "route_family": "web_company_info", "tooling_profile": "official_web_search"},
    {"scope_label": "agora_non_technical", "execution_action": "refuse",
     "route_family": "web_company_info", "tooling_profile": "no_agora_docs_refusal"},
    {"scope_label": "small_talk", "execution_action": "controlled_response",
     "route_family": "general_chat", "tooling_profile": "controlled_acknowledgement"},
    {"scope_label": "small_talk", "execution_action": "refuse",
     "route_family": "general_chat", "tooling_profile": "no_agora_docs_refusal"},
    {"scope_label": "non_agora", "execution_action": "refuse",
     "route_family": "fallback_or_refuse", "tooling_profile": "no_agora_docs_refusal"},
]

_TUPLE_INDEX: dict[tuple[str, str], dict[str, str]] = {
    (item["scope_label"], item["execution_action"]): dict(item) for item in VALID_ROUTE_TUPLES
}

_VALID_SCOPES = sorted({item["scope_label"] for item in VALID_ROUTE_TUPLES})


class RouteCorrectionValidationError(ValueError):
    pass


def valid_scopes() -> list[str]:
    return list(_VALID_SCOPES)


def valid_actions_for_scope(scope_label: str) -> list[str]:
    normalized = _normalize(scope_label)
    return [
        item["execution_action"]
        for item in VALID_ROUTE_TUPLES
        if item["scope_label"] == normalized
    ]


def validate_route_correction(
    *,
    scope_label: str,
    execution_action: str,
    note: str | None = None,
) -> dict[str, Any]:
    normalized_scope = _normalize(scope_label)
    normalized_action = _normalize(execution_action)
    match = _TUPLE_INDEX.get((normalized_scope, normalized_action))
    if match is None:
        if normalized_scope not in {item["scope_label"] for item in VALID_ROUTE_TUPLES}:
            raise RouteCorrectionValidationError(f"invalid scope_label: {scope_label!r}")
        raise RouteCorrectionValidationError(
            f"invalid execution_action {execution_action!r} for scope_label {normalized_scope!r}"
        )
    normalized_note = " ".join(str(note or "").split()).strip()
    return {
        "scope_label": match["scope_label"],
        "execution_action": match["execution_action"],
        "route_family": match["route_family"],
        "tooling_profile": match["tooling_profile"],
        "note": normalized_note,
    }


def _normalize(value: str) -> str:
    return " ".join(str(value or "").split()).strip().lower()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest backend.tests.test_route_correction`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/route_correction.py backend/tests/test_route_correction.py
git commit -m "feat: add route correction tuple validation"
```

---

### Task 2: Repository Persistence for Route Corrections

**Files:**
- Modify: `backend/repositories/ticket_repository.py`
- Modify: `backend/sql/ticket_storage.sql`
- Test: `backend/tests/test_route_correction.py`
- Test: `backend/tests/test_repository_configuration.py`

- [ ] **Step 1: Add failing repository tests**

Append to `backend/tests/test_route_correction.py`:

```python
from backend.repositories.ticket_repository import InMemoryTicketRepository


class BillingRouteCorrectionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.billing_ticket_id = "BT-TK-ACC-123456"
        self.repository.save_billing_ticket({
            "billing_ticket_id": self.billing_ticket_id,
            "client_ticket_id": "TK-ACC-123456",
            "source": "manual",
            "title": "t",
            "question": "q",
            "automation_status": "automation",
            "route": "detailed_invoice",
            "scope_label": "billing",
            "route_family": "billing_automation",
            "route_confidence": 0.42,
        })

    def _correction(self) -> dict[str, object]:
        return {
            "billing_ticket_id": self.billing_ticket_id,
            "client_ticket_id": "TK-ACC-123456",
            "original_scope_label": "billing",
            "original_route_family": "billing_automation",
            "original_execution_action": "detailed_invoice",
            "original_tooling_profile": "deterministic_billing_intake",
            "original_route_reason": "billing_invoice_request",
            "original_route_confidence": 0.42,
            "corrected_scope_label": "billing",
            "corrected_route_family": "billing_review",
            "corrected_execution_action": "human_review_required",
            "corrected_tooling_profile": "deterministic_billing_intake",
            "first_corrected_scope_label": "billing",
            "first_corrected_route_family": "billing_review",
            "first_corrected_execution_action": "human_review_required",
            "first_corrected_tooling_profile": "deterministic_billing_intake",
            "corrector": "operator",
            "note": "refund dispute",
            "created_at": "2026-06-19T00:00:00+00:00",
            "updated_at": "2026-06-19T00:00:00+00:00",
            "correction_count": 1,
        }

    def test_save_get_and_list_correction(self) -> None:
        correction = self._correction()
        self.repository.save_billing_route_correction(correction)
        fetched = self.repository.get_billing_route_correction(self.billing_ticket_id)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["corrected_execution_action"], "human_review_required")
        listed = self.repository.list_billing_route_corrections(limit=10)
        self.assertEqual(len(listed), 1)

    def test_resave_overwrites_but_preserves_first_corrected(self) -> None:
        self.repository.save_billing_route_correction(self._correction())
        updated = self._correction()
        updated["original_execution_action"] = "human_review_required"
        updated["corrected_execution_action"] = "refuse"
        updated["corrected_route_family"] = "fallback_or_refuse"
        updated["note"] = "actually non-agora"
        updated["correction_count"] = 2
        self.repository.save_billing_route_correction(updated)
        fetched = self.repository.get_billing_route_correction(self.billing_ticket_id)
        assert fetched is not None
        self.assertEqual(fetched["corrected_execution_action"], "refuse")
        # original_* is the first AI-predicted tuple and is immutable across resave.
        self.assertEqual(fetched["original_execution_action"], "detailed_invoice")
        # first_corrected_* preserved across resave.
        self.assertEqual(fetched["first_corrected_execution_action"], "human_review_required")
        self.assertEqual(fetched["correction_count"], 2)

    def test_get_missing_returns_none(self) -> None:
        self.assertIsNone(self.repository.get_billing_route_correction("BT-MISSING"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest backend.tests.test_route_correction.BillingRouteCorrectionRepositoryTests`

Expected: FAIL with `AttributeError` for missing correction methods.

- [ ] **Step 3: Add in-memory methods**

In `backend/repositories/ticket_repository.py`:

In `InMemoryTicketRepository.__init__`, add:

```python
self._billing_route_corrections: dict[str, dict[str, Any]] = {}
```

Add methods near the existing billing response token methods (after `mark_billing_response_token_used`):

```python
def save_billing_route_correction(self, correction: dict[str, Any]) -> None:
    billing_ticket_id = str(correction.get("billing_ticket_id") or "").strip()
    if not billing_ticket_id:
        raise ValueError("billing_ticket_id is required")
    saved = copy.deepcopy(correction)
    saved.setdefault("created_at", _utc_now())
    saved.setdefault("updated_at", saved["created_at"])
    saved.setdefault("correction_count", 1)
    # Preserve first_corrected_* across overwrites.
    existing = self._billing_route_corrections.get(billing_ticket_id)
    if existing is not None:
        for key in (
            "first_corrected_scope_label",
            "first_corrected_route_family",
            "first_corrected_execution_action",
            "first_corrected_tooling_profile",
        ):
            saved[key] = existing.get(key)
        for key in (
            "original_scope_label",
            "original_route_family",
            "original_execution_action",
            "original_tooling_profile",
            "original_route_reason",
            "original_route_confidence",
        ):
            saved[key] = existing.get(key)
        saved["created_at"] = existing.get("created_at") or saved["created_at"]
        saved["correction_count"] = max(
            int(existing.get("correction_count") or 0) + 1,
            int(saved.get("correction_count") or 0),
        )
    else:
        for key in (
            "first_corrected_scope_label",
            "first_corrected_route_family",
            "first_corrected_execution_action",
            "first_corrected_tooling_profile",
        ):
            saved.setdefault(key, saved.get(key.replace("first_corrected_", "corrected_")))
    self._billing_route_corrections[billing_ticket_id] = saved

def get_billing_route_correction(self, billing_ticket_id: str) -> dict[str, Any] | None:
    item = self._billing_route_corrections.get(str(billing_ticket_id).strip())
    return copy.deepcopy(item) if item is not None else None

def list_billing_route_corrections(self, limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = _safe_positive_int(limit, 100)
    items = sorted(
        self._billing_route_corrections.values(),
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )
    return [copy.deepcopy(item) for item in items[:safe_limit]]
```

Also clear corrections in `delete_all_billing_tickets`:

```python
def delete_all_billing_tickets(self) -> int:
    count = len(self._billing_tickets)
    self._billing_tickets.clear()
    self._billing_response_tokens.clear()
    self._billing_route_corrections.clear()
    return count
```

- [ ] **Step 4: Add PostgreSQL billing-ticket tuple columns and correction schema to SQL doc**

In `backend/sql/ticket_storage.sql`, add the billing-ticket active route tuple columns to `support_billing_tickets`:

```sql
    scope_label TEXT,
    route_family TEXT,
    execution_action TEXT,
    tooling_profile TEXT,
```

Then, after the `support_billing_response_tokens` block, add:

```sql
CREATE TABLE IF NOT EXISTS support_billing_route_corrections (
    billing_ticket_id TEXT PRIMARY KEY REFERENCES support_billing_tickets(billing_ticket_id) ON DELETE CASCADE,
    client_ticket_id TEXT NOT NULL REFERENCES support_tickets(ticket_id) ON DELETE CASCADE,
    original_scope_label TEXT,
    original_route_family TEXT,
    original_execution_action TEXT,
    original_tooling_profile TEXT,
    original_route_reason TEXT,
    original_route_confidence REAL,
    corrected_scope_label TEXT NOT NULL,
    corrected_route_family TEXT NOT NULL,
    corrected_execution_action TEXT NOT NULL,
    corrected_tooling_profile TEXT NOT NULL,
    first_corrected_scope_label TEXT NOT NULL,
    first_corrected_route_family TEXT NOT NULL,
    first_corrected_execution_action TEXT NOT NULL,
    first_corrected_tooling_profile TEXT NOT NULL,
    corrector TEXT NOT NULL DEFAULT 'operator',
    note TEXT NOT NULL DEFAULT '',
    correction_count INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_support_billing_route_corrections_updated
    ON support_billing_route_corrections (updated_at DESC);
```

- [ ] **Step 5: Add PostgreSQL init + CRUD methods**

In `PostgresTicketRepository.initialize`, add `scope_label`, `route_family`, `execution_action`, and `tooling_profile` to the `support_billing_tickets` create table statement. Also add `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements for all four columns so existing databases migrate in place.

Update `PostgresTicketRepository.save_billing_ticket` so the INSERT, VALUES tuple, and ON CONFLICT update persist all four fields.

After the `support_billing_response_tokens` index creation (around line 2533), add the table creation:

```python
cur.execute(
    sql.SQL(
        """
        CREATE TABLE IF NOT EXISTS {} (
            billing_ticket_id TEXT PRIMARY KEY REFERENCES {}(billing_ticket_id) ON DELETE CASCADE,
            client_ticket_id TEXT NOT NULL REFERENCES {}(ticket_id) ON DELETE CASCADE,
            original_scope_label TEXT,
            original_route_family TEXT,
            original_execution_action TEXT,
            original_tooling_profile TEXT,
            original_route_reason TEXT,
            original_route_confidence REAL,
            corrected_scope_label TEXT NOT NULL,
            corrected_route_family TEXT NOT NULL,
            corrected_execution_action TEXT NOT NULL,
            corrected_tooling_profile TEXT NOT NULL,
            first_corrected_scope_label TEXT NOT NULL,
            first_corrected_route_family TEXT NOT NULL,
            first_corrected_execution_action TEXT NOT NULL,
            first_corrected_tooling_profile TEXT NOT NULL,
            corrector TEXT NOT NULL DEFAULT 'operator',
            note TEXT NOT NULL DEFAULT '',
            correction_count INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    ).format(
        self._table("support_billing_route_corrections"),
        self._table("support_billing_tickets"),
        self._table("support_tickets"),
    )
)
cur.execute(
    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (updated_at DESC)").format(
        sql.Identifier("idx_support_billing_route_corrections_updated"),
        self._table("support_billing_route_corrections"),
    )
)
```

Then add PostgreSQL CRUD methods near the other billing methods (after `mark_billing_response_token_used`, ~line 4895). Use an aliased upsert that preserves the immutable `original_*` tuple and `first_corrected_*` tuple from the existing row:

```python
def save_billing_route_correction(self, correction: dict[str, Any]) -> None:
    billing_ticket_id = str(correction.get("billing_ticket_id") or "").strip()
    if not billing_ticket_id:
        raise ValueError("billing_ticket_id is required")
    client_ticket_id = str(correction.get("client_ticket_id") or "").strip()
    if not client_ticket_id:
        raise ValueError("client_ticket_id is required")
    created_at = correction.get("created_at") or _utc_now()
    updated_at = correction.get("updated_at") or created_at

    def _operation(conn: psycopg.Connection[Any]) -> None:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} AS corrections (
                            billing_ticket_id, client_ticket_id,
                            original_scope_label, original_route_family,
                            original_execution_action, original_tooling_profile,
                            original_route_reason, original_route_confidence,
                            corrected_scope_label, corrected_route_family,
                            corrected_execution_action, corrected_tooling_profile,
                            first_corrected_scope_label, first_corrected_route_family,
                            first_corrected_execution_action, first_corrected_tooling_profile,
                            corrector, note, correction_count, created_at, updated_at
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            COALESCE(NULLIF(%s,''), %s),
                            COALESCE(NULLIF(%s,''), %s),
                            COALESCE(NULLIF(%s,''), %s),
                            COALESCE(NULLIF(%s,''), %s),
                            %s,%s,%s,%s,%s
                        )
                        ON CONFLICT (billing_ticket_id) DO UPDATE SET
                            original_scope_label = corrections.original_scope_label,
                            original_route_family = corrections.original_route_family,
                            original_execution_action = corrections.original_execution_action,
                            original_tooling_profile = corrections.original_tooling_profile,
                            original_route_reason = corrections.original_route_reason,
                            original_route_confidence = corrections.original_route_confidence,
                            corrected_scope_label = EXCLUDED.corrected_scope_label,
                            corrected_route_family = EXCLUDED.corrected_route_family,
                            corrected_execution_action = EXCLUDED.corrected_execution_action,
                            corrected_tooling_profile = EXCLUDED.corrected_tooling_profile,
                            first_corrected_scope_label = corrections.first_corrected_scope_label,
                            first_corrected_route_family = corrections.first_corrected_route_family,
                            first_corrected_execution_action = corrections.first_corrected_execution_action,
                            first_corrected_tooling_profile = corrections.first_corrected_tooling_profile,
                            corrector = EXCLUDED.corrector,
                            note = EXCLUDED.note,
                            correction_count = corrections.correction_count + 1,
                            updated_at = EXCLUDED.updated_at
                        """
                    ).format(self._table("support_billing_route_corrections")),
                    (
                        billing_ticket_id, client_ticket_id,
                        correction.get("original_scope_label"),
                        correction.get("original_route_family"),
                        correction.get("original_execution_action"),
                        correction.get("original_tooling_profile"),
                        correction.get("original_route_reason"),
                        float(correction["original_route_confidence"]) if correction.get("original_route_confidence") is not None else None,
                        correction.get("corrected_scope_label"),
                        correction.get("corrected_route_family"),
                        correction.get("corrected_execution_action"),
                        correction.get("corrected_tooling_profile"),
                        correction.get("first_corrected_scope_label"), correction.get("corrected_scope_label"),
                        correction.get("first_corrected_route_family"), correction.get("corrected_route_family"),
                        correction.get("first_corrected_execution_action"), correction.get("corrected_execution_action"),
                        correction.get("first_corrected_tooling_profile"), correction.get("corrected_tooling_profile"),
                        str(correction.get("corrector") or "operator").strip(),
                        str(correction.get("note") or "").strip(),
                        int(correction.get("correction_count") or 1),
                        created_at, updated_at,
                    ),
                )

    self._run_with_connection_retry("save_billing_route_correction", _operation)

def get_billing_route_correction(self, billing_ticket_id: str) -> dict[str, Any] | None:
    def _operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT * FROM {} WHERE billing_ticket_id = %s").format(
                    self._table("support_billing_route_corrections")
                ),
                (str(billing_ticket_id).strip(),),
            )
            rows = cur.fetchall()
            if not rows:
                return None
            col_names = [desc[0] for desc in cur.description]
            return dict(zip(col_names, rows[0]))

    return self._run_with_connection_retry("get_billing_route_correction", _operation)

def list_billing_route_corrections(self, limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = _safe_positive_int(limit, 100)

    def _operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "SELECT * FROM {} ORDER BY updated_at DESC LIMIT %s"
                ).format(self._table("support_billing_route_corrections")),
                (safe_limit,),
            )
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, row)) for row in cur.fetchall()]

    return self._run_with_connection_retry("list_billing_route_corrections", _operation)
```

- [ ] **Step 6: Add methods to `TicketRepository` Protocol**

In the `TicketRepository` Protocol class, after `mark_billing_response_token_used` (~line 824), add:

```python
def save_billing_route_correction(self, correction: dict[str, Any]) -> None:
    ...

def get_billing_route_correction(self, billing_ticket_id: str) -> dict[str, Any] | None:
    ...

def list_billing_route_corrections(self, limit: int = 100) -> list[dict[str, Any]]:
    ...
```

- [ ] **Step 7: Run repository tests**

Run: `python3 -m unittest backend.tests.test_route_correction`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/repositories/ticket_repository.py backend/sql/ticket_storage.sql backend/tests/test_route_correction.py
git commit -m "feat: persist billing route corrections"
```

---

### Task 3: Route Correction API Endpoint

**Files:**
- Modify: `backend/main.py`
- Test: `backend/tests/test_account_intake.py`

- [ ] **Step 1: Add failing API tests**

Append to `backend/tests/test_account_intake.py` (inside `AccountIntakeApiTests`):

```python
def _create_invoice_ticket_for_correction(self) -> dict[str, object]:
    with patch.object(main, "dispatch_event", AsyncMock()), patch(
        "backend.main.send_billing_internal_email",
        return_value={"status": "skipped_config_missing", "reason": "missing BILLING_AUTOMATION_SMTP_PASSWORD"},
    ):
        response = self.client.post(
            "/account",
            json={
                "title": "Refund dispute on invoice",
                "question": "I was charged USD 705.97 wrongly, this is a refund dispute. Transaction ID: 1104245232004173824.",
                "customer_email": "customer@example.com",
                "source": "account-ui",
            },
        )
    self.assertEqual(response.status_code, 200, response.text)
    return response.json()

def test_route_correction_updates_tuple_and_records_event(self) -> None:
    payload = self._create_invoice_ticket_for_correction()
    billing_ticket_id = str(payload["billing_ticket_id"])

    with patch.object(main, "dispatch_event", AsyncMock()):
        response = self.client.post(
            f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
            json={
                "scope_label": "billing",
                "execution_action": "human_review_required",
                "note": "customer explicitly asked for refund -> human review",
                "corrector": "operator",
            },
        )

    self.assertEqual(response.status_code, 200, response.text)
    result = response.json()
    self.assertEqual(result["route"], "human_review_required")
    self.assertEqual(result["route_family"], "billing_review")
    self.assertEqual(result["tooling_profile"], "deterministic_billing_intake")
    self.assertTrue(result["route_corrected"])
    self.assertTrue(result["route_error"])

    events = self.repository.list_ticket_events(str(payload["ticket_id"]))
    self.assertTrue(any(e["event_type"] == "route_corrected" for e in events))

def test_route_correction_rejects_invalid_tuple(self) -> None:
    payload = self._create_invoice_ticket_for_correction()
    billing_ticket_id = str(payload["billing_ticket_id"])
    response = self.client.post(
        f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
        json={"scope_label": "billing", "execution_action": "rag"},
    )
    self.assertEqual(response.status_code, 400, response.text)

def test_route_correction_404_for_missing_ticket(self) -> None:
    response = self.client.post(
        "/api/account/billing-tickets/BT-DOES-NOT-EXIST/route-correction",
        json={"scope_label": "billing", "execution_action": "human_review_required"},
    )
    self.assertEqual(response.status_code, 404, response.text)

def test_route_correction_resave_preserves_first_corrected(self) -> None:
    payload = self._create_invoice_ticket_for_correction()
    billing_ticket_id = str(payload["billing_ticket_id"])
    with patch.object(main, "dispatch_event", AsyncMock()):
        self.client.post(
            f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
            json={"scope_label": "billing", "execution_action": "human_review_required"},
        )
        second = self.client.post(
            f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
            json={"scope_label": "non_agora", "execution_action": "refuse"},
        )
    self.assertEqual(second.status_code, 200, second.text)
    correction = self.repository.get_billing_route_correction(billing_ticket_id)
    assert correction is not None
    self.assertEqual(correction["corrected_execution_action"], "refuse")
    self.assertEqual(correction["first_corrected_execution_action"], "human_review_required")
    self.assertEqual(correction["correction_count"], 2)
```

- [ ] **Step 2: Run test to verify failure**

Run: `python3 -m unittest backend.tests.test_account_intake.AccountIntakeApiTests -k route_correction`

Expected: FAIL because the endpoint does not exist.

- [ ] **Step 3: Add request model and correction builder helpers**

In `backend/main.py`:

Add import near the billing response flow import:

```python
from backend.services.route_correction import (
    RouteCorrectionValidationError,
    validate_route_correction,
)
```

Add request model near `BillingResponseSubmitRequest`:

```python
class BillingRouteCorrectionRequest(BaseModel):
    scope_label: str = Field(min_length=1, max_length=80)
    execution_action: str = Field(min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=4000)
    corrector: str | None = Field(default="operator", max_length=160)
```

- [ ] **Step 4: Implement the endpoint**

Add after `reply_to_billing_ticket`:

```python
@app.post("/api/account/billing-tickets/{billing_ticket_id}/route-correction")
async def correct_billing_route(
    billing_ticket_id: str,
    request: BillingRouteCorrectionRequest,
) -> dict[str, Any]:
    billing_ticket = await async_to_thread(ticket_repository.get_billing_ticket, billing_ticket_id)
    if billing_ticket is None:
        raise HTTPException(status_code=404, detail="billing ticket not found")

    client_ticket_id = str(billing_ticket.get("client_ticket_id") or "").strip()
    if not client_ticket_id:
        raise HTTPException(status_code=400, detail="billing ticket has no linked support ticket")

    try:
        correction = validate_route_correction(
            scope_label=request.scope_label,
            execution_action=request.execution_action,
            note=request.note,
        )
    except RouteCorrectionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    timestamp = now_iso()
    original_route_confidence = billing_ticket.get("route_confidence")

    existing = await async_to_thread(ticket_repository.get_billing_route_correction, billing_ticket_id)
    correction_record = {
        "billing_ticket_id": billing_ticket_id,
        "client_ticket_id": client_ticket_id,
        "original_scope_label": (existing or {}).get("original_scope_label") or billing_ticket.get("scope_label"),
        "original_route_family": (existing or {}).get("original_route_family") or billing_ticket.get("route_family"),
        "original_execution_action": (existing or {}).get("original_execution_action") or billing_ticket.get("route") or billing_ticket.get("execution_action"),
        "original_tooling_profile": (existing or {}).get("original_tooling_profile") or billing_ticket.get("tooling_profile"),
        "original_route_reason": (existing or {}).get("original_route_reason") or billing_ticket.get("route_reason"),
        "original_route_confidence": (existing or {}).get("original_route_confidence") if existing else original_route_confidence,
        "corrected_scope_label": correction["scope_label"],
        "corrected_route_family": correction["route_family"],
        "corrected_execution_action": correction["execution_action"],
        "corrected_tooling_profile": correction["tooling_profile"],
        "first_corrected_scope_label": (existing or {}).get("first_corrected_scope_label") or correction["scope_label"],
        "first_corrected_route_family": (existing or {}).get("first_corrected_route_family") or correction["route_family"],
        "first_corrected_execution_action": (existing or {}).get("first_corrected_execution_action") or correction["execution_action"],
        "first_corrected_tooling_profile": (existing or {}).get("first_corrected_tooling_profile") or correction["tooling_profile"],
        "corrector": str(request.corrector or "operator").strip(),
        "note": correction["note"],
        "correction_count": (int((existing or {}).get("correction_count") or 0) + 1) if existing else 1,
        "created_at": (existing or {}).get("created_at") or timestamp,
        "updated_at": timestamp,
    }
    await async_to_thread(ticket_repository.save_billing_route_correction, correction_record)

    # Update billing ticket active route fields to the corrected tuple.
    billing_ticket["scope_label"] = correction["scope_label"]
    billing_ticket["route_family"] = correction["route_family"]
    billing_ticket["route"] = correction["execution_action"]
    billing_ticket["execution_action"] = correction["execution_action"]
    billing_ticket["tooling_profile"] = correction["tooling_profile"]
    billing_ticket["updated_at"] = timestamp
    await async_to_thread(ticket_repository.save_billing_ticket, billing_ticket)

    event = {
        "event": "route_corrected",
        "ticket_id": client_ticket_id,
        "billing_ticket_id": billing_ticket_id,
        "original_scope_label": correction_record["original_scope_label"],
        "original_route_family": correction_record["original_route_family"],
        "original_execution_action": correction_record["original_execution_action"],
        "corrected_scope_label": correction["scope_label"],
        "corrected_route_family": correction["route_family"],
        "corrected_execution_action": correction["execution_action"],
        "corrected_tooling_profile": correction["tooling_profile"],
        "note": correction["note"],
        "corrector": correction_record["corrector"],
        "created_at": timestamp,
    }
    await async_to_thread(ticket_repository.record_event, client_ticket_id, event["event"], event)
    await dispatch_event(["engineer", "dashboard"], event)

    view_model = _build_account_ticket_view_model(billing_ticket)
    canonical_ticket = await async_to_thread(ticket_repository.get_ticket, client_ticket_id)
    view_model["messages"] = canonical_ticket.get("messages", []) if canonical_ticket else []
    view_model["route_corrected"] = True
    view_model["route_error"] = True
    view_model["route_correction"] = _public_route_correction(correction_record)
    return {
        **billing_ticket,
        **view_model,
    }
```

- [ ] **Step 5: Add correction view-model helper and route-error helpers**

Add module-level helpers near `_build_account_ticket_view_model`:

```python
def _route_error_confidence_threshold() -> float:
    """Same threshold env as the router (support_router._safe_float_env).

    Resolved per call so operator env overrides take effect without restart.
    Default 0.7 matches DEFAULT_INTENT_ROUTER_CONFIDENCE_THRESHOLD.
    """
    raw = (os.getenv("INTENT_ROUTER_CONFIDENCE_THRESHOLD") or "").strip()
    if not raw:
        return 0.7
    try:
        value = float(raw)
    except ValueError:
        return 0.7
    return value if value > 0 else 0.7


def _route_is_low_confidence(billing_ticket: dict[str, Any]) -> bool:
    raw = billing_ticket.get("route_confidence")
    if raw is None:
        return False
    try:
        return float(raw) < _route_error_confidence_threshold()
    except (TypeError, ValueError):
        return False


def _public_route_correction(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "corrected_scope_label": record.get("corrected_scope_label"),
        "corrected_route_family": record.get("corrected_route_family"),
        "corrected_execution_action": record.get("corrected_execution_action"),
        "corrected_tooling_profile": record.get("corrected_tooling_profile"),
        "first_corrected_scope_label": record.get("first_corrected_scope_label"),
        "first_corrected_route_family": record.get("first_corrected_route_family"),
        "first_corrected_execution_action": record.get("first_corrected_execution_action"),
        "first_corrected_tooling_profile": record.get("first_corrected_tooling_profile"),
        "corrector": record.get("corrector") or "operator",
        "note": record.get("note") or "",
        "correction_count": int(record.get("correction_count") or 1),
        "updated_at": record.get("updated_at"),
    }
```

`os` is already imported at the top of `backend/main.py`. Keep this in sync with the router's `INTENT_ROUTER_CONFIDENCE_THRESHOLD` env (default `0.7`) so the route-error flag never diverges from what the router actually considered low-confidence.

- [ ] **Step 6: Run API tests**

Run: `python3 -m unittest backend.tests.test_account_intake.AccountIntakeApiTests`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/tests/test_account_intake.py
git commit -m "feat: add billing route correction endpoint"
```

---

### Task 4: Route Error Flag in List and Detail View Models

**Files:**
- Modify: `backend/main.py`
- Test: `backend/tests/test_account_intake.py`

- [ ] **Step 1: Add failing tests for list/detail route_error + route_corrected**

Append to `backend/tests/test_account_intake.py`:

```python
def test_list_billing_tickets_includes_route_error_and_corrected_flags(self) -> None:
    payload = self._create_invoice_ticket_for_correction()
    billing_ticket_id = str(payload["billing_ticket_id"])
    # Force a low-confidence ticket for the list flag.
    ticket = self.repository.get_billing_ticket(billing_ticket_id)
    assert ticket is not None
    ticket["route_confidence"] = 0.3
    self.repository.save_billing_ticket(ticket)

    response = self.client.get("/api/account/billing-tickets?limit=50")
    self.assertEqual(response.status_code, 200, response.text)
    items = response.json()["tickets"]
    matched = [item for item in items if item.get("billing_ticket_id") == billing_ticket_id]
    self.assertTrue(matched)
    self.assertTrue(matched[0]["route_error"])
    self.assertFalse(matched[0]["route_corrected"])

def test_detail_includes_correction_after_correction(self) -> None:
    payload = self._create_invoice_ticket_for_correction()
    billing_ticket_id = str(payload["billing_ticket_id"])
    with patch.object(main, "dispatch_event", AsyncMock()):
        self.client.post(
            f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
            json={"scope_label": "billing", "execution_action": "human_review_required", "note": "x"},
        )
    detail = self.client.get(f"/api/account/billing-tickets/{billing_ticket_id}")
    self.assertEqual(detail.status_code, 200, detail.text)
    data = detail.json()
    self.assertTrue(data["route_corrected"])
    self.assertTrue(data["route_error"])
    self.assertEqual(data["route_correction"]["corrected_execution_action"], "human_review_required")
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m unittest backend.tests.test_account_intake.AccountIntakeApiTests -k route_error`

Expected: FAIL because view models do not yet include the flags.

- [ ] **Step 3: Extend list endpoint to attach correction + route_error**

Modify `list_billing_tickets` to batch-load corrections and compute flags. Load all corrections (uncapped) and index by `billing_ticket_id` so every list row gets the correct flag regardless of page size:

```python
@app.get("/api/account/billing-tickets")
def list_billing_tickets(limit: int = 30) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 100))
    tickets = ticket_repository.list_billing_tickets(limit=safe_limit)
    corrections = {
        str(item.get("billing_ticket_id") or "").strip(): item
        for item in ticket_repository.list_billing_route_corrections(limit=1000)
    }
    items: list[dict[str, Any]] = []
    for item in tickets:
        view_model = _build_account_ticket_view_model(item)
        billing_ticket_id = str(item.get("billing_ticket_id") or "").strip()
        correction = corrections.get(billing_ticket_id)
        view_model["billing_ticket_id"] = billing_ticket_id
        view_model["client_ticket_id"] = item.get("client_ticket_id")
        view_model["route_corrected"] = correction is not None
        view_model["route_error"] = correction is not None or _route_is_low_confidence(item)
        if correction is not None:
            view_model["route_correction"] = _public_route_correction(correction)
        items.append(view_model)
    return {"tickets": items, "billing_tickets": items, "count": len(items)}
```

- [ ] **Step 4: Extend detail endpoint to attach correction + route_error**

Modify `get_billing_ticket` to load the correction record. After building `view_model` and loading the canonical ticket, add:

```python
    # Use the resolved record's billing_ticket_id, not the raw path param, so
    # the TK-... client-id fallback path still finds the correction row.
    resolved_billing_ticket_id = str(ticket.get("billing_ticket_id") or "").strip()
    correction = ticket_repository.get_billing_route_correction(resolved_billing_ticket_id)
    view_model["route_corrected"] = correction is not None
    view_model["route_error"] = correction is not None or _route_is_low_confidence(ticket)
    if correction is not None:
        view_model["route_correction"] = _public_route_correction(correction)
    return {
        **ticket,
        **view_model,
    }
```

Note: `get_billing_ticket` (main.py:3219) resolves the record from either a `BT-...` id OR a client `TK-...` id via the `get_billing_ticket_by_client_ticket_id` fallback, so always look up the correction by the resolved record's `billing_ticket_id`, never the raw path param.

- [ ] **Step 5: Run tests**

Run: `python3 -m unittest backend.tests.test_account_intake.AccountIntakeApiTests`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_account_intake.py
git commit -m "feat: surface route error and correction flags in account API"
```

---

### Task 5: Route Errors Summary Endpoint

**Files:**
- Modify: `backend/main.py`
- Test: `backend/tests/test_account_intake.py`

- [ ] **Step 1: Add failing summary test**

Append to `backend/tests/test_account_intake.py`:

```python
def test_route_errors_summary_aggregates_corrected_and_low_confidence(self) -> None:
    # Ticket A: corrected.
    payload_a = self._create_invoice_ticket_for_correction()
    with patch.object(main, "dispatch_event", AsyncMock()):
        self.client.post(
            f"/api/account/billing-tickets/{payload_a['billing_ticket_id']}/route-correction",
            json={"scope_label": "billing", "execution_action": "human_review_required", "note": "refund"},
        )
    # Ticket B: low confidence only (not corrected).
    payload_b = self._create_invoice_ticket_for_correction()
    ticket_b = self.repository.get_billing_ticket(str(payload_b["billing_ticket_id"]))
    assert ticket_b is not None
    ticket_b["route_confidence"] = 0.3
    self.repository.save_billing_ticket(ticket_b)

    response = self.client.get("/api/account/route-errors/summary?limit=50")
    self.assertEqual(response.status_code, 200, response.text)
    data = response.json()
    self.assertGreaterEqual(data["total"], 2)
    self.assertGreaterEqual(data["corrected_count"], 1)
    self.assertGreaterEqual(data["low_confidence_count"], 1)
    transitions = {t["transition"]: t["count"] for t in data["transitions"]}
    self.assertIn("detailed_invoice -> human_review_required", transitions)
```

- [ ] **Step 2: Run test to verify failure**

Run: `python3 -m unittest backend.tests.test_account_intake.AccountIntakeApiTests -k route_errors_summary`

Expected: FAIL because the endpoint does not exist.

- [ ] **Step 3: Implement the summary endpoint**

Add after `list_billing_tickets`:

```python
@app.get("/api/account/route-errors/summary")
def get_route_errors_summary(limit: int = 100) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 500))
    tickets = ticket_repository.list_billing_tickets(limit=safe_limit)
    corrections = {
        str(item.get("billing_ticket_id") or "").strip(): item
        for item in ticket_repository.list_billing_route_corrections(limit=safe_limit)
    }
    error_items = [
        item
        for item in tickets
        if str(item.get("billing_ticket_id") or "").strip() in corrections
        or _route_is_low_confidence(item)
    ]
    corrected_count = 0
    low_confidence_count = 0
    transition_counts: dict[str, int] = {}
    for item in error_items:
        billing_ticket_id = str(item.get("billing_ticket_id") or "").strip()
        correction = corrections.get(billing_ticket_id)
        if correction is not None:
            corrected_count += 1
            original = str(correction.get("original_execution_action") or item.get("route") or "unknown").strip()
            corrected = str(correction.get("corrected_execution_action") or "unknown").strip()
            transition = f"{original} -> {corrected}"
            transition_counts[transition] = transition_counts.get(transition, 0) + 1
        if _route_is_low_confidence(item):
            low_confidence_count += 1
    transitions = [
        {"transition": key, "count": value}
        for key, value in sorted(transition_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return {
        "total": len(error_items),
        "corrected_count": corrected_count,
        "low_confidence_count": low_confidence_count,
        "transitions": transitions,
    }
```

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest backend.tests.test_account_intake.AccountIntakeApiTests`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_account_intake.py
git commit -m "feat: add route errors summary endpoint"
```

---

### Task 6: Account UI Correction Control and Filter

**Files:**
- Modify: `ui/account-ui/app.js`
- Modify: `ui/account-ui/styles.css`
- Test: `backend/tests/test_account_ui_contract.py`

- [ ] **Step 1: Write failing UI contract tests**

In `backend/tests/test_account_ui_contract.py`, add tests asserting the new controls and endpoints are referenced:

```python
def test_account_ui_has_route_correction_control_and_filter(self) -> None:
    app = Path("ui/account-ui/app.js").read_text(encoding="utf-8")
    for term in [
        "route_corrected",
        "route_error",
        "route_correction",
        "/route-correction",
        "route-errors",
        "Route errors",
        "submitRouteCorrection",
        "scope_label",
        "execution_action",
    ]:
        with self.subTest(term=term):
            self.assertIn(term, app)

def test_account_ui_styles_include_correction_panel(self) -> None:
    styles = Path("ui/account-ui/styles.css").read_text(encoding="utf-8")
    self.assertIn("route-correction", styles)
    self.assertIn("route-error-summary", styles)
```

- [ ] **Step 2: Run test to verify failure**

Run: `python3 -m unittest backend.tests.test_account_ui_contract`

Expected: FAIL because the UI does not yet reference these terms.

- [ ] **Step 3: Add filter state and Route errors chip**

In `ui/account-ui/app.js`, extend the `state` object:

```javascript
const state = {
  view: "create",
  title: "",
  question: "",
  customerEmail: "",
  source: "manual",
  isSubmitting: false,
  history: [],
  activeItem: null,
  error: "",
  composerToolbarState: buildDefaultComposerToolbarState(),
  statusFilter: "all",
  replyMessage: "",
  isSubmittingReply: "",
  replyError: "",
  // Route correction state.
  correctionScope: "",
  correctionAction: "",
  correctionNote: "",
  isSubmittingCorrection: false,
  correctionError: "",
  routeErrorSummary: null,
};
```

Add the route tuple options near the top (after `routeClass`):

```javascript
const ROUTE_TUPLE_OPTIONS = [
  { scope: "billing", action: "account_suspension", label: "Billing / Account suspension" },
  { scope: "billing", action: "detailed_invoice", label: "Billing / Detailed invoice" },
  { scope: "billing", action: "account_verification", label: "Billing / Account verification" },
  { scope: "billing", action: "human_review_required", label: "Billing / Human review required" },
  { scope: "billing", action: "refuse", label: "Billing / Refuse" },
  { scope: "agora_technical", action: "rag", label: "Agora technical / RAG" },
  { scope: "agora_non_technical", action: "web_search", label: "Agora non-technical / Web search" },
  { scope: "agora_non_technical", action: "refuse", label: "Agora non-technical / Refuse" },
  { scope: "small_talk", action: "controlled_response", label: "Small talk / Controlled response" },
  { scope: "small_talk", action: "refuse", label: "Small talk / Refuse" },
  { scope: "non_agora", action: "refuse", label: "Non-Agora / Refuse" },
  { scope: "ticket_resolution", action: "resolve_ticket", label: "Ticket resolution / Resolve" },
];

function routeTupleOptions() {
  return ROUTE_TUPLE_OPTIONS.map(
    (o) => `<option value="${o.scope}|${o.action}">${escapeHtml(o.label)}</option>`
  ).join("");
}
```

Update `renderFilterControls` to add the Route errors chip:

```javascript
function renderFilterControls() {
  const filters = [
    { value: "all", label: "All" },
    { value: "automation", label: "Automation" },
    { value: "not_automated", label: "Not automated" },
    { value: "route_errors", label: "Route errors" },
  ];
  return `
    <div class="filter-chips">
      ${filters
        .map(
          (f) => `
        <button
          class="filter-chip ${state.statusFilter === f.value ? "filter-chip--active" : ""}"
          type="button"
          data-action="set-filter"
          data-value="${escapeHtml(f.value)}"
        >${escapeHtml(f.label)}</button>
      `
        )
        .join("")}
    </div>
  `;
}
```

Update `matchesFilter`:

```javascript
function matchesFilter(item) {
  const itemStatus = item.status || item.automation_status || "not_automated";
  if (state.statusFilter === "all") return true;
  if (state.statusFilter === "automation") return isAutomationStatus(itemStatus);
  if (state.statusFilter === "not_automated") return !isAutomationStatus(itemStatus);
  if (state.statusFilter === "route_errors") return !!item.route_error;
  return true;
}
```

- [ ] **Step 4: Add correction control to detail view**

In `renderDetailView`, insert a route correction section after the `Route result` meta row block (after the `route_reason` row and before the `created_at` row is fine; place a dedicated section after the meta-grid). Add a helper to render the correction control:

```javascript
function renderRouteCorrectionControl() {
  const item = state.activeItem;
  if (!item) return "";
  const corrected = !!item.route_corrected;
  const correction = item.route_correction || {};
  const selectedValue = state.correctionScope && state.correctionAction
    ? `${state.correctionScope}|${state.correctionAction}`
    : "";
  return `
    <div class="detail-section route-correction">
      <div class="detail-section-title">Route correction</div>
      ${
        corrected
          ? `<p class="correction-status">Corrected to <strong>${escapeHtml(correction.corrected_execution_action || "")}</strong> (${escapeHtml(correction.corrected_scope_label || "")}) by ${escapeHtml(correction.corrector || "")}. Re-correct below if needed.</p>`
          : `<p class="correction-status">If AI routed this ticket incorrectly, pick the correct route tuple.</p>`
      }
      <div class="correction-form">
        <select class="correction-select" data-correction-select ${state.isSubmittingCorrection ? "disabled" : ""}>
          <option value="">Select correct route…</option>
          ${routeTupleOptions()}
        </select>
        <textarea
          class="correction-note"
          placeholder="Why is this the correct route? (optional)"
          data-correction-note
          ${state.isSubmittingCorrection ? "disabled" : ""}
        >${escapeHtml(state.correctionNote)}</textarea>
        <div class="correction-actions">
          <button
            class="primary-button primary-button--small"
            type="button"
            data-action="submit-correction"
            ${state.isSubmittingCorrection ? "disabled" : ""}
          >
            <span class="material-symbols-outlined">check</span>
            ${state.isSubmittingCorrection ? "Saving…" : "Save correction"}
          </button>
        </div>
        ${
          state.correctionError
            ? `<div class="error-banner"><span class="material-symbols-outlined">error</span>${escapeHtml(state.correctionError)}</div>`
            : ""
        }
      </div>
    </div>
  `;
}
```

Call `${renderRouteCorrectionControl()}` inside `renderDetailView` after the meta-grid `</div>` and before the `missingFields` section.

- [ ] **Step 5: Add submit handler and select binding**

Add:

```javascript
async function submitRouteCorrection() {
  const item = state.activeItem;
  if (!item) return;
  const value = (state.correctionScope && state.correctionAction)
    ? `${state.correctionScope}|${state.correctionAction}`
    : "";
  if (!value) {
    state.correctionError = "Select a correct route first.";
    render();
    return;
  }
  const billingTicketId = item.billing_ticket_id || item.ticket_id || "";
  if (!billingTicketId) {
    state.correctionError = "Missing billing ticket id.";
    render();
    return;
  }

  state.isSubmittingCorrection = true;
  state.correctionError = "";
  render();

  try {
    const response = await fetch(`/api/account/billing-tickets/${encodeURIComponent(billingTicketId)}/route-correction`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scope_label: state.correctionScope,
        execution_action: state.correctionAction,
        note: state.correctionNote || null,
        corrector: "operator",
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Route correction failed.");
    }
    state.activeItem = payload;
    state.correctionScope = "";
    state.correctionAction = "";
    state.correctionNote = "";
    showToast("Route corrected");
    await fetchTickets();
    if (state.statusFilter === "route_errors") {
      await fetchRouteErrorSummary();
    }
  } catch (err) {
    state.correctionError = err instanceof Error ? err.message : "Route correction failed.";
  } finally {
    state.isSubmittingCorrection = false;
    render();
  }
}

async function fetchRouteErrorSummary() {
  try {
    const response = await fetch("/api/account/route-errors/summary?limit=100");
    if (!response.ok) {
      state.routeErrorSummary = null;
      return;
    }
    state.routeErrorSummary = await response.json();
  } catch {
    state.routeErrorSummary = null;
  }
  render();
}
```

Add a summary renderer:

```javascript
function renderRouteErrorSummary() {
  if (state.statusFilter !== "route_errors") return "";
  const summary = state.routeErrorSummary;
  if (!summary) return "";
  const transitions = (summary.transitions || [])
    .slice(0, 8)
    .map(
      (t) => `<li><strong>${escapeHtml(t.transition)}</strong>: ${escapeHtml(String(t.count))}</li>`
    )
    .join("");
  return `
    <div class="route-error-summary">
      <div class="detail-section-title">Route error summary</div>
      <div class="summary-stats">
        <span class="summary-stat">Total errors: <strong>${escapeHtml(String(summary.total || 0))}</strong></span>
        <span class="summary-stat">Corrected: <strong>${escapeHtml(String(summary.corrected_count || 0))}</strong></span>
        <span class="summary-stat">Low confidence: <strong>${escapeHtml(String(summary.low_confidence_count || 0))}</strong></span>
      </div>
      ${transitions ? `<ul class="summary-transitions">${transitions}</ul>` : ""}
    </div>
  `;
}
```

Insert `${renderRouteErrorSummary()}` at the top of `renderHistorySidebar()` (after the `renderFilterControls()` calls inside the branches, or as the first thing after the filter chips when the filter is active). Simplest: render it directly above the history list inside `renderHistorySidebar` when `state.statusFilter === "route_errors"`:

```javascript
function renderHistorySidebar() {
  const visibleItems = state.history.filter(matchesFilter);
  const summaryHtml = renderRouteErrorSummary();
  if (!state.history.length) {
    return `
      <div class="history-empty">
        <span class="material-symbols-outlined">receipt_long</span>
        <p>No tickets yet</p>
      </div>
    `;
  }
  if (!visibleItems.length) {
    return `
      ${renderFilterControls()}
      ${summaryHtml}
      <div class="history-empty">
        <span class="material-symbols-outlined">filter_alt_off</span>
        <p>No tickets match this filter</p>
      </div>
    `;
  }
  return `
    ${renderFilterControls()}
    ${summaryHtml}
    <div class="history-section-title">Recent tickets</div>
    ${visibleItems.map(/* ... existing item rendering unchanged ... */).join("")}
  `;
}
```

Keep the existing per-item rendering body intact; only wrap the summary insertion around it.

- [ ] **Step 6: Wire up bindings**

In `bind()`:

```javascript
const correctionSelect = document.querySelector("[data-correction-select]");
if (correctionSelect) {
  correctionSelect.value = state.correctionScope && state.correctionAction
    ? `${state.correctionScope}|${state.correctionAction}`
    : "";
  correctionSelect.addEventListener("change", (event) => {
    const raw = String(event.target.value || "");
    const [scope, action] = raw.split("|");
    state.correctionScope = scope || "";
    state.correctionAction = action || "";
  });
}
const correctionNote = document.querySelector("[data-correction-note]");
if (correctionNote) {
  correctionNote.addEventListener("input", (event) => {
    state.correctionNote = event.target.value;
  });
}
document.querySelectorAll("[data-action='submit-correction']").forEach((el) => {
  el.addEventListener("click", submitRouteCorrection);
});
```

In the filter chip click handler, when the filter changes to `route_errors`, fetch the summary:

```javascript
if (filterBtn) {
  state.statusFilter = filterBtn.dataset.value || "all";
  if (state.statusFilter === "route_errors") {
    void fetchRouteErrorSummary();
  } else {
    state.routeErrorSummary = null;
  }
  render();
  return;
}
```

Reset correction state when opening a ticket:

```javascript
async function openTicket(ticketId) {
  const detail = await fetchTicketDetail(ticketId);
  if (!detail) {
    showToast("Failed to load ticket details.");
    return;
  }
  state.activeItem = detail;
  state.view = "detail";
  state.replyMessage = "";
  state.replyError = "";
  state.correctionScope = "";
  state.correctionAction = "";
  state.correctionNote = "";
  state.correctionError = "";
  render();
}
```

- [ ] **Step 7: Add CSS**

In `ui/account-ui/styles.css`, append. The correction select/note reuse the existing `.input`/`.textarea` `border: 0` + `outline` pattern (see `styles.css:443-468`); do not invent a `--outline` token that does not exist in this file:

```css
/* --- Route correction --- */

.route-correction {
  margin-top: 12px;
}

.correction-status {
  margin: 0 0 12px 0;
  color: var(--ink-muted);
  font-size: 13px;
}

.correction-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.correction-select {
  width: 100%;
  min-height: 48px;
  padding: 0 14px;
  border: 0;
  border-radius: 16px;
  background: var(--surface-container-low);
  color: var(--ink);
  font-size: 14px;
  outline: 2px solid transparent;
  outline-offset: 2px;
}

.correction-select:focus {
  outline-color: rgba(0, 100, 147, 0.24);
}

.correction-select:disabled {
  opacity: 0.6;
}

.correction-note {
  width: 100%;
  min-height: 56px;
  padding: 12px 14px;
  border: 0;
  border-radius: 16px;
  background: var(--surface-container-low);
  color: var(--ink);
  font-size: 14px;
  line-height: 1.5;
  resize: vertical;
  outline: 2px solid transparent;
  outline-offset: 2px;
}

.correction-note:focus {
  outline-color: rgba(0, 100, 147, 0.24);
}

.correction-note:disabled {
  opacity: 0.6;
}

.correction-actions {
  display: flex;
  gap: 8px;
}

/* --- Route error summary --- */

.route-error-summary {
  border-radius: 14px;
  padding: 12px;
  background: var(--surface-container-low);
  margin-bottom: 8px;
}

.summary-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 12px;
  color: var(--ink-muted);
  margin-bottom: 6px;
}

.summary-transitions {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--ink-soft);
}
```

- [ ] **Step 8: Run UI contract test**

Run: `python3 -m unittest backend.tests.test_account_ui_contract`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add ui/account-ui/app.js ui/account-ui/styles.css backend/tests/test_account_ui_contract.py
git commit -m "feat: add route correction control and error summary to account UI"
```

---

### Task 7: Repository Configuration Test

**Files:**
- Modify: `backend/tests/test_repository_configuration.py`

- [ ] **Step 1: Add contract assertions**

Add a sibling test inside the existing `RepositoryConfigurationTests` class (the same class that holds `test_ticket_storage_contract_includes_billing_ticket_table` at `backend/tests/test_repository_configuration.py:315`) to assert the new table:

```python
def test_ticket_storage_contract_includes_billing_route_corrections(self) -> None:
    sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
    repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")
    self.assertIn("CREATE TABLE IF NOT EXISTS support_billing_route_corrections", sql_source)
    self.assertIn("corrected_execution_action TEXT NOT NULL", sql_source)
    self.assertIn("first_corrected_execution_action TEXT NOT NULL", sql_source)
    self.assertIn("idx_support_billing_route_corrections_updated", sql_source)
    self.assertIn("def save_billing_route_correction", repo_source)
    self.assertIn("def get_billing_route_correction", repo_source)
    self.assertIn("def list_billing_route_corrections", repo_source)
    self.assertIn("support_billing_route_corrections", repo_source)
```

- [ ] **Step 2: Run test**

Run: `python3 -m unittest backend.tests.test_repository_configuration.RepositoryConfigurationTests.test_ticket_storage_contract_includes_billing_route_corrections`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_repository_configuration.py
git commit -m "test: assert billing route corrections schema contract"
```

---

### Task 8: Documentation Updates

**Files:**
- Modify: `docs/feature_list.md`
- Modify: `docs/roadmap.html`
- Test: `scripts/verify_feature_list.py`

- [ ] **Step 1: Update feature list**

Read `docs/feature_list.md` first to match its category order and sentence style. Add one short major-feature entry under the appropriate category (Ticket Dashboard or billing/account category if present). Example sentence:

```text
Account intake supports manual route correction of the full route tuple and a Route errors filter with summary panel for systematic mis-routing analysis.
```

- [ ] **Step 2: Update roadmap**

Read `docs/roadmap.html` and update the relevant routing-analysis, route-quality, or account-intake lane. If no exact lane exists, add a concise roadmap marker under the closest account/intake/dashboard section. This task explicitly requires updating `docs/roadmap.html`.

- [ ] **Step 3: Run feature list verification**

Run: `python3 scripts/verify_feature_list.py`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/feature_list.md docs/roadmap.html
git commit -m "docs: record account route correction feature"
```

---

### Task 9: End-to-End Verification

**Files:**
- No code changes expected.

- [ ] **Step 1: Run focused backend tests**

```bash
python3 -m unittest \
  backend.tests.test_route_correction \
  backend.tests.test_account_intake \
  backend.tests.test_account_ui_contract \
  backend.tests.test_repository_configuration \
  backend.tests.test_support_router
```

Expected: all tests pass.

- [ ] **Step 2: Run feature list verification**

```bash
python3 scripts/verify_feature_list.py
```

Expected: PASS.

- [ ] **Step 3: Manual API smoke**

Minimum smoke scenario using TestClient or running stack:

1. POST `/account` with a refund-dispute invoice message (routes to `detailed_invoice` automation by default).
2. Confirm `route_error` is `true` only if confidence is low; otherwise `false`.
3. POST `/api/account/billing-tickets/{id}/route-correction` with `{scope_label: "billing", execution_action: "human_review_required", note: "refund dispute"}`.
4. Confirm response `route_corrected=true`, `route_error=true`, `route="human_review_required"`.
5. Confirm `GET /api/account/billing-tickets/{id}` returns the correction object.
6. Confirm `GET /api/account/route-errors/summary` lists the transition `detailed_invoice -> human_review_required`.
7. Confirm a second correction to `non_agora/refuse` updates `corrected_*` but preserves `first_corrected_execution_action="human_review_required"` and `correction_count=2`.

- [ ] **Step 4: Stack relevance classification**

This touches `backend/`, `ui/`, and runtime API surface, so classify as `功能类/重大行为变更` and stack-relevant.

- [ ] **Step 5: Pre-finalization verification**

Run the exact focused verification command from Step 1 plus `python3 scripts/verify_feature_list.py` immediately before finalization.

- [ ] **Step 6: Finalize through repository workflow**

Use:

```bash
scripts/workflow/finalize_task_to_main.sh <branch> --verify "python3 -m unittest backend.tests.test_route_correction backend.tests.test_account_intake backend.tests.test_account_ui_contract backend.tests.test_repository_configuration backend.tests.test_support_router && python3 scripts/verify_feature_list.py"
```

- [ ] **Step 7: Post-merge live stack verification**

Because this is stack-relevant, after merge from root `main`:

```bash
bash scripts/workflow/inspect_single_host_stack_mode.sh
bash scripts/workflow/restart_single_host_lightweight_stack.sh
curl -fsS http://localhost:8000/health
```

Then verify task-specific live markers:

- `GET /api/account/route-errors/summary` returns 200 with `total` field.
- `/account` serves the account UI and `app.js` includes `route-correction`.
- `/health` `app_build.ref` matches merged `main` commit.

---

## Implementation Notes and Guardrails

- Do not re-run billing automation or re-send the internal email on correction. Correction only updates routing classification fields.
- Do not allow a correction that is not in `VALID_ROUTE_TUPLES`; reject with HTTP 400 before touching any state.
- Do not create multiple correction rows per billing ticket; one row, updated in place, with `first_corrected_*` preserved and `correction_count` incremented.
- Do not infer `route_family`/`tooling_profile` on the client; the backend derives them from the validated `(scope_label, execution_action)` pair so the corrected tuple is always consistent with `_route_contract_for_scope`.
- Do not treat low-confidence alone as "corrected"; `route_corrected` is strictly `correction record exists`. `route_error` is the union (corrected OR low confidence).
- Use `route_confidence` (the router's settled confidence stored on the billing ticket) for the low-confidence flag, not `intent_router_model_confidence`, because the latter may be `None` when the router fell back to deterministic. Resolve the threshold from `INTENT_ROUTER_CONFIDENCE_THRESHOLD` (same env as the router, default 0.7) per call so the flag never diverges from the router's notion of low confidence.
- Always look up the correction record by the resolved billing ticket's `billing_ticket_id`, never the raw path param, because `GET /api/account/billing-tickets/{id}` accepts either a `BT-...` id or a `TK-...` client id.
- Keep the correction select as a single `<select>` of `scope|action` pairs to avoid invalid combinations on the client; the backend still validates.
- Preserve existing filter behavior; the new `route_errors` chip is additive.
- Update `docs/roadmap.html` as part of this task; prefer an existing routing/account analysis lane, otherwise add one concise marker in the closest existing section.
