# Account Follow-up Email Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a customer reply completes missing billing automation fields, generate a one-time response link, send the internal email, persist send/link status, and surface that state in `/account`.

**Architecture:** Reuse the existing billing automation email/token path from initial `/account` creation by extracting a small helper in `backend/main.py`. Keep token storage and invalidation semantics identical: save token before send, mark it used if send fails. Expose redacted email payload/status through existing billing ticket detail and render it in `ui/account-ui/app.js`.

**Tech Stack:** FastAPI backend, `TicketRepository`, existing billing automation services, vanilla JS account UI, pytest contract/API tests.

---

### Task 1: Backend Follow-up Email Trigger

**Files:**
- Modify: `backend/main.py`
- Test: `backend/tests/test_account_intake.py`

- [x] Write failing tests for `/api/account/billing-tickets/{id}/reply` when missing fields become complete: it sends internal email, persists redacted payload/status, stores a usable token, and invalidates token on send failure.
- [x] Run the targeted tests and confirm they fail on the current `demo_mode/not_sending` behavior.
- [x] Extract/reuse helper code to generate billing response token, build `/response?token=...` email, save token, send email, persist status/reason/payload, and invalidate token on send failure.
- [x] Run targeted account intake tests and confirm they pass.

### Task 2: Account Detail Status Visibility

**Files:**
- Modify: `ui/account-ui/app.js`
- Modify: `ui/account-ui/styles.css` if needed
- Test: `backend/tests/test_account_ui_contract.py`

- [x] Write failing UI contract assertions for rendering internal email send status, reason, and response link status from ticket detail.
- [x] Add compact detail rows/section showing `internal_email_send_status`, `internal_email_send_reason`, and whether an internal response link exists in `internal_email_payload`.
- [x] Run JS syntax and UI contract tests.

### Task 3: Verification and Finalization

- [x] Run targeted backend + UI tests.
- [x] Classify as `修复类`, stack-relevant because `backend/` and `ui/` changed.
- [ ] Finalize through `scripts/workflow/finalize_task_to_main.sh`.
- [ ] Restart lightweight stack from root `main`, verify `/health` build ref, and confirm live `/account/app.js` contains the new status markers.
