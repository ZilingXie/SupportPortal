# AgentRelay Demo Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the leadership roadmap and Phase 1 demo script to present AgentRelay as the completed agent-to-agent communication foundation for SupportPortal.

**Architecture:** This is a documentation/static-page update. Keep `docs/roadmap.html` as the high-level delivery roadmap and `docs/roadmap/phase1.html` as the scrollable 3-minute talk track. Do not claim autonomous investigation is complete; present AgentRelay as completed communication infrastructure with remaining domain-agent integration work.

**Tech Stack:** Static HTML/CSS, existing roadmap contract tests, Python HTML parsing checks.

---

### Task 1: Update Phase 1 Demo Talk Track

**Files:**
- Modify: `docs/roadmap/phase1.html`

- [ ] **Step 1: Update timing and narrative**
  - Add AgentRelay to the 3-minute talk track between architecture and showcase.
  - Keep total story under 3 minutes.

- [ ] **Step 2: Add AgentRelay foundation section**
  - Insert a section after Big picture and before Role shift.
  - Include thread reuse, completion ownership, audit events, and MCP/API foundation.
  - Add clearly named image placeholders for the user to provide visuals.

- [ ] **Step 3: Upgrade showcase and dashboard wording**
  - Reframe showcase as evidence collection plus guardrail rejection.
  - Add A2A workflow metrics to dashboard copy.

### Task 2: Update Roadmap Summary

**Files:**
- Modify: `docs/roadmap.html`

- [ ] **Step 1: Move AgentRelay from distant Phase 3 language into foundation status**
  - Keep Phase 3 as autonomous investigation expansion, not communication-layer start.
  - Add AgentRelay-specific readiness wording where current roadmap discusses agent-to-agent.

- [ ] **Step 2: Keep wording bounded**
  - Say communication foundation is complete.
  - Say full Codex App meeting scenario and SupportPortal domain-agent integration remain next steps if mentioned.

### Task 3: Verify

**Files:**
- Test: `backend/tests/test_roadmap_contract.py`
- Test: `backend/tests/test_dashboard_routes.py`

- [ ] **Step 1: Run roadmap contract tests**
  - Run: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_roadmap_contract backend.tests.test_dashboard_routes`
  - Expected: PASS.

- [ ] **Step 2: Run static text checks**
  - Confirm `AgentRelay`, `thread reuse`, `completion ownership`, `reply.delivered`, and `agent-to-agent communication foundation` appear in the relevant pages.

- [ ] **Step 3: Run HTML parse check**
  - Parse both HTML files with Python `html.parser` and fail on parser exceptions.
