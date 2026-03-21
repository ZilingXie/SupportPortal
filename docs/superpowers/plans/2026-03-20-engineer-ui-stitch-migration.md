# Engineer UI Stitch Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the real `/engineer` surface to the `ui/stich_engineer_ui` design language, including login, ticket pool, and an independent ticket detail workspace.

**Architecture:** Keep the existing `/engineer` static mount and backend/API contract. Replace the current dark table + modal presentation with a hash-routed workspace: `#/tickets` for the pool and `#/tickets/:id` for the active ticket detail view. Reuse the existing data-fetching, ticket actions, and combobox behavior where possible, but recompose the DOM and CSS to match the approved `Intelligent Concierge` language in `design.md`.

**Tech Stack:** Vanilla JS, static HTML/CSS, existing engineer APIs, Podman Compose

---

### Task 1: Lock the new engineer UI contract

**Files:**
- Create: `backend/tests/test_engineer_ui_contract.py`
- Modify: `ui/engineer-ui/index.html`
- Modify: `ui/engineer-ui/app.js`
- Modify: `ui/engineer-ui/styles.css`

- [ ] **Step 1: Write the failing contract test**
- [ ] **Step 2: Run the engineer UI contract test and verify it fails**
- [ ] **Step 3: Encode the required shape: stitch login, ticket pool workspace, independent `#/tickets/:id` detail route, no `detail-modal`**
- [ ] **Step 4: Re-run the engineer UI contract test and verify it passes**

### Task 2: Rebuild the engineer shell and routing

**Files:**
- Modify: `ui/engineer-ui/index.html`
- Modify: `ui/engineer-ui/app.js`

- [ ] **Step 1: Create the persistent shell regions for login, pool, and detail workspace**
- [ ] **Step 2: Implement hash-based route parsing for `#/tickets` and `#/tickets/:id`**
- [ ] **Step 3: Replace modal opening/closing with route-driven detail workspace rendering**
- [ ] **Step 4: Verify engineer actions still call the existing APIs**

### Task 3: Apply the stitch visual language

**Files:**
- Modify: `ui/engineer-ui/styles.css`
- Modify: `ui/engineer-ui/index.html`
- Modify: `ui/engineer-ui/app.js`

- [ ] **Step 1: Migrate typography, surfaces, rails, top bar, and filter bar to `design.md` tokens**
- [ ] **Step 2: Convert the ticket pool from legacy table feel to card/list feel while keeping scanability**
- [ ] **Step 3: Convert the detail content into a split workspace with conversation, AI summary, mode controls, and reply composers**
- [ ] **Step 4: Verify loading, empty, and focus states remain clear**

### Task 4: Verify and deploy

**Files:**
- Test: `backend/tests/test_engineer_ui_contract.py`
- Test: `backend/tests/test_client_ui_contract.py`
- Test: `backend/tests/test_dashboard_ui_contract.py`
- Test: `backend/tests/test_dashboard_routes.py`
- Test: `backend/tests/test_rag_dashboard_contract.py`
- Test: `backend/tests/test_rag_service_client.py`

- [ ] **Step 1: Run the engineer contract and shared regression tests**
- [ ] **Step 2: Run `node --check ui/engineer-ui/app.js`**
- [ ] **Step 3: Restart containers with compose down, then up -d --build**
- [ ] **Step 4: Run compose `ps` and smoke-check `/engineer` assets**
