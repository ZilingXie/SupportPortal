# Phase 1 Roadmap Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a scrollable Phase 1 leadership-demo page linked from the roadmap and served from `support.stellarix.space`.

**Architecture:** Keep the page as static HTML under `docs/roadmap/phase1.html`, link it from the existing `docs/roadmap.html` Phase 1 card, and expose both pages through narrow FastAPI static routes. Nginx proxies `/roadmap.html` and `/roadmap/...` to the API using the same no-cache static-page behavior as other UI surfaces.

**Tech Stack:** Static HTML/CSS/JS, FastAPI `StaticFiles`, Nginx reverse proxy, Python unittest smoke tests.

---

### Task 1: Static Page And Entry Link

**Files:**
- Create: `docs/roadmap/phase1.html`
- Modify: `docs/roadmap.html`

- [ ] Add a scrollable Phase 1 presentation page with the leadership narrative.
- [ ] Add a Phase 1 link in `docs/roadmap.html` that works for local `file://` preview and hosted `/roadmap/phase1.html`.

### Task 2: Hosted Static Routes

**Files:**
- Modify: `backend/main.py`
- Modify: `deployment/nginx/supportportal.conf`
- Modify: `backend/tests/test_dashboard_routes.py`

- [ ] Mount `docs` at a private/static route and redirect `/roadmap.html` to the existing roadmap file.
- [ ] Ensure `/roadmap/phase1.html` is served from `docs/roadmap/phase1.html`.
- [ ] Add route smoke tests for route definitions and expected files.

### Task 3: Verification And Deploy

- [ ] Run targeted route/static tests.
- [ ] Validate HTML structure for both pages.
- [ ] Finalize the task to `main`.
- [ ] Deploy `main` to `support.stellarix.space`.
- [ ] Verify external URLs for `roadmap.html` and `roadmap/phase1.html`.
