# Billing Inbox Reply Poller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow backend service that reads unread Outlook Inbox replies whose subject contains `[Billing Request]`, records reply text, and marks successfully handled messages as read.

**Architecture:** Reuse the existing Microsoft Graph token/config helpers in `backend/services/billing_automation.py`. Keep the first version as a synchronous service function with dependency-free unit tests, then expose it through an opt-in worker background poller; do not add PDF handling yet.

**Tech Stack:** Python stdlib `urllib`, existing `unittest` suite, Microsoft Graph `/me/mailFolders/inbox/messages` and `/me/messages/{id}`.

---

### Task 1: Billing Reply Poller Service

**Files:**
- Modify: `backend/services/billing_automation.py`
- Modify: `backend/worker.py`
- Modify: `deployment/docker-compose.single-host.yml`
- Test: `backend/tests/test_billing_automation_email.py`
- Test: `backend/tests/test_worker.py`
- Test: `backend/tests/test_single_host_compose.py`

- [x] **Step 1: Write failing tests**

Add tests proving:
- unread Inbox messages with `[Billing Request]` are read, returned as structured reply records, and marked read;
- unread messages without the prefix are ignored and not marked read;
- handler failures leave messages unread.

- [x] **Step 2: Run the targeted test and verify it fails**

Run: `python3 -m unittest backend.tests.test_billing_automation_email -v`
Expected: FAIL because `poll_billing_request_replies` does not exist yet.

- [x] **Step 3: Implement the minimal service**

Add a dataclass for handled reply records, list unread Inbox messages with a Graph `$filter`, read matching message bodies, call an optional handler, record replies as JSONL in worker mode, and mark read only after success.

- [x] **Step 4: Run targeted verification**

Run: `python3 -m unittest backend.tests.test_billing_automation_email -v`
Expected: PASS.

- [x] **Step 5: Run syntax verification**

Run: `python3 -m py_compile backend/services/billing_automation.py backend/tests/test_billing_automation_email.py`
Expected: PASS.
