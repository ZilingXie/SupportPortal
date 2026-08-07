> **Recommendation: Sol high** - this is a cross-cutting, destructive-data-path feature spanning concurrent seed migration, PostgreSQL assignment, every publication entry point, complete Rerun cleanup, Admin UI, documentation, live deployment, and an idempotent Automated-only production operation.

# Automation Router Persona Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. Use `superpowers:subagent-driven-development` only if the user explicitly authorizes subagents; current collaboration policy does not authorize proactive delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Sid Precise, Sid Bright, and Sid Warm as independently managed Automation Router Personas, randomly pin one published Persona per Automation Case, reselect after complete Rerun, and perform one safe Rerun of only the Cases that were Automated at rollout start.

**Architecture:** Define the three presets once in `backend/services/account_admin.py` and make both repositories seed them idempotently under concurrent startup. Replace Ticket ID hashing with one uniform random choice at first assignment, persist the winning Persona/version atomically, and make the legacy published resolver reuse that same assignment contract. Extend the existing Rerun reset transaction to delete the assignment, then reuse the normal resolver when scheduling the new reply. Keep Persona styling thin: shared renderer facts/safety/language rules remain unchanged. Run the one-time Automated-only rollout through the existing single-Case endpoint with a persisted idempotency key per frozen Case so a process restart cannot repeat email/reply side effects.

**Tech Stack:** Python 3, FastAPI, in-memory and psycopg/PostgreSQL repositories, vanilla JavaScript Admin and Account UIs, `unittest`, required isolated-schema PostgreSQL integration tests, Podman lightweight single-host stack.

**Approved design:** `docs/superpowers/specs/2026-08-07-automation-router-personas-design.md`

**Task classification:** `功能类/重大行为变更` and stack-relevant. Update `design.md`, `docs/prompt_change_log.md`, `docs/feature_list.md`, and `docs/roadmap.html`; run post-merge lightweight stack verification.

---

## File Map

- `backend/services/account_admin.py` - canonical preset keys, display names, seed markers, approved thin instructions, shared signature, and compatibility aliases.
- `backend/repositories/ticket_repository.py` - idempotent in-memory/PostgreSQL preset seeding, random assignment, a read-only assignment lookup for rollout evidence, concurrency convergence, enable/disable serialization, and assignment deletion during complete Rerun.
- `backend/main.py` - fail-closed Persona-unavailable handling, Rerun assignment-deletion statistics, and backward-compatible per-Case Rerun idempotency keys.
- `backend/worker.py` - convert assignment-unavailable failures at delayed-reply and internal-email follow-up entry points into the existing Human Review path.
- `ui/workspace-ui/admin/app.js` - explain random assignment, pinning, version behavior, and complete-Rerun reselection in the existing Automation Persona workspace.
- `ui/account-ui/app.js` - expose the assignment deletion in Rerun result/confirmation copy without changing button scope.
- `scripts/rerun_automated_account_cases.py` - loopback-only, dry-run-first live operation that freezes the Automated Case ID set, executes idempotent single-Case Reruns sequentially, resumes safely, and writes restricted JSON/Markdown reports.
- `backend/tests/test_account_admin_features.py` - in-memory preset, lifecycle, random pinning, and reset behavior.
- `backend/tests/test_account_persona_postgres.py` - real PostgreSQL migration/idempotency and concurrent first-assignment coverage in an isolated schema.
- `backend/tests/test_repository_configuration.py` - SQL transaction contract for deleting the assignment with reset/audit.
- `backend/tests/test_account_intake.py` - end-to-end no-candidate fallback, Rerun statistics, idempotency, and new pinned reply-job assignment.
- `backend/tests/test_worker.py` - delayed reply and internal-email follow-up assignment failures remain fail-closed to Human Review.
- `backend/tests/test_repair_account_customer_name.py` - customer-name replacement jobs retain the Case's existing Persona/version.
- `backend/tests/test_agent_config.py`, `backend/tests/test_workspace_api.py` - Agent Config/API registry contracts for three Personas and preserved lifecycle history.
- `backend/tests/test_workspace_admin_ui_contract.py`, `backend/tests/test_account_ui_contract.py` - Admin and Account UI copy/contracts.
- `backend/tests/test_rerun_automated_account_cases.py` - operation snapshot, scope, stop/resume, permissions, and report behavior.
- `design.md`, `docs/prompt_change_log.md`, `docs/feature_list.md`, `docs/roadmap.html` - source-of-truth behavior and rollout status.

Do not modify `backend/services/automation_persona.py`: its shared facts-only rendering, language matching, greeting, exact Signature append, forbidden-value validation, and generation-failure behavior are already the desired common contract. Repository selection failures occur before that renderer, so `backend/main.py` and `backend/worker.py` still need explicit tests and narrow handling for the no-enabled/published-candidate condition.

### Task 1: Define and seed the three Persona presets

**Files:**
- Modify: `backend/services/account_admin.py:28`
- Modify: `backend/repositories/ticket_repository.py:1889`
- Modify: `backend/repositories/ticket_repository.py:5588`
- Test: `backend/tests/test_account_admin_features.py:536`
- Test: `backend/tests/test_agent_config.py:29`
- Test: `backend/tests/test_workspace_api.py:413`
- Create: `backend/tests/test_account_persona_postgres.py`

- [ ] **Step 1: Write failing in-memory registry tests**

Add focused tests that map `list_account_personas()` by key instead of assuming index zero:

```python
def test_seeded_automation_personas_match_approved_presets(self) -> None:
    personas = {item["persona_key"]: item for item in self.repository.list_account_personas()}
    self.assertEqual(set(personas), {"default-support", "sid-bright", "sid-precise"})
    self.assertEqual(personas["default-support"]["display_name"], "Sid Warm")
    self.assertEqual(personas["sid-bright"]["display_name"], "Sid Bright")
    self.assertEqual(personas["sid-precise"]["display_name"], "Sid Precise")
    for persona in personas.values():
        published = next(
            version for version in persona["versions"]
            if version["version"] == persona["published_version"]
        )
        self.assertEqual(published["content"]["opener"], "")
        self.assertEqual(published["content"]["signature"], "Best,\nSid\nSupport Engineer 2")
```

Also assert the exact approved instruction for every preset, all three are enabled/published, and the existing draft/publish/rollback test still works against `default-support` without assuming that it is the only Persona.

- [ ] **Step 2: Run the new tests and confirm the expected failure**

Run:

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_admin_features.AccountAdminFeatureTests.test_seeded_automation_personas_match_approved_presets backend.tests.test_agent_config.AgentConfigTests -v
```

Expected: FAIL because only `default-support` exists and its display name/instruction are still `Default Support` and the legacy friendly instruction.

- [ ] **Step 3: Add one canonical preset catalog**

Replace the single mutable-looking default definition with immutable preset metadata in `backend/services/account_admin.py`:

```python
DEFAULT_PERSONA_KEY = "default-support"
DEFAULT_PERSONA_SIGNATURE = "Best,\nSid\nSupport Engineer 2"
ACCOUNT_PERSONA_PRESET_VERSION = "automation-persona-presets-v1"

ACCOUNT_PERSONA_PRESETS = (
    {
        "persona_key": DEFAULT_PERSONA_KEY,
        "display_name": "Sid Warm",
        "seed_marker": "Seeded Sid Warm preset v1",
        "content": {
            "instruction": SID_WARM_INSTRUCTION,
            "opener": "",
            "signature": DEFAULT_PERSONA_SIGNATURE,
        },
    },
    # sid-bright and sid-precise with the approved design text
)

DEFAULT_PERSONA_CONTENT = copy.deepcopy(ACCOUNT_PERSONA_PRESETS[0]["content"])
```

Keep `DEFAULT_PERSONA_KEY`, `DEFAULT_PERSONA_SIGNATURE`, and `DEFAULT_PERSONA_CONTENT` as compatibility exports. Use the exact instructions from the approved design; do not add facts, safety, language, greeting, or signature-generation rules to the thin instructions.

- [ ] **Step 4: Replace in-memory single seeding with catalog seeding**

Rename `_seed_default_account_persona()` to `_seed_account_persona_presets()` and build all three Persona rows plus one published version per key from `ACCOUNT_PERSONA_PRESETS`. Preserve the existing version object shape and deep-copy content. The in-memory repository is always fresh, so each preset begins at published version 1.

- [ ] **Step 5: Write failing PostgreSQL migration/idempotency tests**

Create `backend/tests/test_account_persona_postgres.py` using the isolated-schema pattern from `backend/tests/test_prompt_versioning_postgres.py`. Load the worktree-root `.env` with `python-dotenv` before reading the DSNs, without overriding explicitly exported test values. Guard the suite with `RUN_ACCOUNT_PERSONA_POSTGRES_TEST=true` and cover:

```python
def test_initialize_seeds_three_personas_idempotently(self) -> None:
    first = self.repository.list_account_personas()
    self.repository.initialize()
    second = self.repository.list_account_personas()
    self.assertEqual(first, second)

def test_existing_default_history_gets_one_warm_version(self) -> None:
    # Arrange a legacy published default version with no new seed marker.
    # Re-run initialize and assert old rows remain, one marked Warm version is
    # appended/published, and a second initialize does not append again.

def test_admin_publication_after_seed_is_not_republished_on_restart(self) -> None:
    # Publish a later admin draft, re-run initialize, and assert it remains current.

def test_concurrent_initialize_seeds_each_preset_once(self) -> None:
    # Initialize two repository instances against one schema concurrently and
    # assert one system seed marker/version exists per preset.
```

Also test that a pre-existing non-system `sid-bright` or `sid-precise` key is not overwritten and produces an actionable warning.

- [ ] **Step 6: Implement idempotent PostgreSQL preset seeding**

Extract the current inline default Persona insertion/upgrade SQL into a focused `_ensure_account_persona_presets(cur)` helper called after the Persona tables are created. Serialize the helper across API/worker startup transactions with a table-level lock on the Persona registry; do not rely on an application-process mutex.

Required behavior:

1. Fresh database: insert all three rows and published v1 versions with the preset seed marker.
2. Existing `default-support`: retain every version, insert `MAX(version) + 1` with the Warm marker only when a `created_by = 'system'` version with that exact marker does not exist, supersede the current published version, publish Warm, update display name to `Sid Warm`, and enable it once as part of this initial rollout.
3. Subsequent initialize: marker exists, so do not insert, supersede, enable, rename, or republish anything; a later administrator publication or disablement remains current.
4. Missing `sid-bright`/`sid-precise`: seed once as enabled and published.
5. Existing non-system preset key: leave row and versions unchanged and log a warning; never silently overwrite.

The table lock plus unique version key must make simultaneous `initialize()` calls converge without duplicate preset versions or lost administrator state. Do not force `enabled = true` after the one marked rollout seed, because later administrator disablement must survive restart.

- [ ] **Step 7: Update API and Agent Config contracts**

Change `backend/tests/test_agent_config.py` and `backend/tests/test_workspace_api.py` to compare keys/display names as sets or maps, verify all three include the exact Signature, and retain independent draft/publish/rollback history. Do not introduce a new API or new content schema.

- [ ] **Step 8: Run Task 1 tests**

Run:

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_admin_features backend.tests.test_agent_config backend.tests.test_workspace_api -q
```

Expected: PASS.

Run the isolated PostgreSQL test when configured:

```bash
rtk env RUN_ACCOUNT_PERSONA_POSTGRES_TEST=true /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_persona_postgres -v
```

Expected: PASS with zero skips; the test drops its temporary schema in cleanup. A skipped test because `TICKET_DB_DSN` or `TICKET_DB_MIGRATION_DSN` is missing does not satisfy this gate. From the root `main` workspace, link its `.env` into the task workspace with `rtk bash scripts/workflow/link_worktree_env.sh <absolute-worktree-path>` before rerunning; do not copy or commit `.env`.

- [ ] **Step 9: Commit Task 1**

```bash
rtk git add backend/services/account_admin.py backend/repositories/ticket_repository.py backend/tests/test_account_admin_features.py backend/tests/test_agent_config.py backend/tests/test_workspace_api.py backend/tests/test_account_persona_postgres.py
rtk git commit -m "feat: seed Automation Router Persona presets"
```

### Task 2: Replace Ticket hashing with atomic random assignment

**Files:**
- Modify: `backend/services/account_admin.py:28`
- Modify: `backend/repositories/ticket_repository.py:2386`
- Modify: `backend/repositories/ticket_repository.py:10831`
- Modify: `backend/main.py:4108`
- Modify: `backend/worker.py:358`
- Modify: `backend/worker.py:652`
- Test: `backend/tests/test_account_admin_features.py:536`
- Test: `backend/tests/test_account_intake.py`
- Test: `backend/tests/test_worker.py`
- Test: `backend/tests/test_repair_account_customer_name.py`
- Test: `backend/tests/test_account_persona_postgres.py`

- [ ] **Step 1: Write failing random-pinning tests**

Add tests that patch the chooser rather than testing statistical distribution:

```python
def test_persona_assignment_uses_one_random_draw_and_stays_pinned(self) -> None:
    with patch("backend.repositories.ticket_repository.random.choice") as choose:
        choose.side_effect = lambda candidates: candidates[1]
        first = self.repository.resolve_account_persona("TK-RANDOM")
        choose.side_effect = lambda candidates: candidates[2]
        second = self.repository.resolve_account_persona("TK-RANDOM")
    self.assertEqual(first, second)
    self.assertEqual(choose.call_count, 1)
```

Add separate tests proving candidates must be enabled and point to a version whose status is actually `published`, disabled/unpublished entries are excluded, every candidate can be selected through a patched chooser, and `resolve_published_account_persona()` reuses the persisted assignment rather than bypassing it. Add `get_account_persona_assignment(ticket_id)` tests proving it returns the existing key/version without creating a missing assignment and without returning full Prompt content.

- [ ] **Step 2: Run the tests and confirm hashing causes failure**

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_admin_features -v
```

Expected: FAIL because the repository still imports `hashlib` inside the resolver and does not call `random.choice`.

- [ ] **Step 3: Implement one shared random chooser**

Import `random` at module scope and add a small helper:

```python
def _choose_account_persona(candidates: list[Any]) -> Any:
    if not candidates:
        raise AccountPersonaUnavailableError("no enabled published persona")
    return random.choice(candidates)
```

Define the narrow domain exception in `backend/services/account_admin.py`. Do not seed the PRNG, hash the Ticket ID, add weights, or maintain allocation state.

- [ ] **Step 4: Make in-memory assignment atomic**

Wrap the read-candidate-select-write sequence in `_assignment_lock`. If an assignment already exists, return its historical version even if that Persona is now disabled or superseded. If none exists, choose once, store it, and return a deep copy.

Make `resolve_published_account_persona()` a compatibility alias to `resolve_account_persona()` so Rerun recovery and customer-name repair cannot silently choose another voice for an already assigned Case. Implement the read-only lookup under the same lock; return only `ticket_id`, `persona_key`, `version`, and `assigned_at`, or `None`.

- [ ] **Step 5: Make PostgreSQL concurrent resolvers converge**

Inside one transaction:

1. Read and return an existing assignment first.
2. Load enabled/published candidates and choose one in Python.
3. Insert with `ON CONFLICT (ticket_id) DO NOTHING`.
4. Re-select the joined assignment row and return the persisted winner.

The second concurrent transaction may choose a different candidate, but it must block on/lose the unique insert and return the first committed row. Do not use a global Round Robin row or advisory lock.

Make the PostgreSQL compatibility method delegate to the persisted resolver as well. Add the same read-only assignment lookup without candidate selection or insert side effects.

- [ ] **Step 6: Add the real concurrency test**

In `backend/tests/test_account_persona_postgres.py`, create one Ticket and use `ThreadPoolExecutor(max_workers=2)` plus a barrier. Force the two chooser calls toward different candidates, call `resolve_account_persona()` concurrently, then assert both results have the same key/version and the assignment table contains exactly one row for that Ticket.

Also serialize the last-enabled guard: wrap the in-memory enable/disable count-and-update in `_assignment_lock`; in PostgreSQL, lock the Persona registry rows/table before count-and-update. Reuse the resolver's eligible-candidate predicate, including the joined version's `status = 'published'`, rather than counting a stale `published_version` pointer. Start with exactly two eligible Personas, disable both concurrently, and assert exactly one operation is rejected and at least one eligible candidate remains.

- [ ] **Step 7: Preserve fail-closed Human Review behavior at every resolver call site**

Catch only `AccountPersonaUnavailableError`, not database/network/programming exceptions:

- initial `/account` Automation intake must save a Human Review Case with no internal email or customer reply job;
- complete Rerun must persist the affected Case as Human Review, create no reply job, and expose the reason in terminal route evidence rather than failing the whole job or returning an unhandled 500;
- delayed reply preparation and Billing/Enablement/Quota internal-email follow-up rendering must call the existing `_move_automation_reply_to_human_review` or Case-update path and publish no customer copy.

Add focused `backend/tests/test_account_intake.py` and `backend/tests/test_worker.py` tests for these paths. Existing assignments remain usable even if their Persona is later disabled, so this fallback applies only when a new assignment is required and no eligible candidate exists.

Add positive pinning tests for all publication paths touched by the resolver contract: delayed reply preparation, Billing/Enablement/Quota internal-email follow-up, and `backend/scripts/repair_account_customer_name.py` must reuse an existing key/version even when the patched random chooser would now select another Persona. The recovery script continues to call the compatibility resolver and therefore shares the repository-level pinning test; do not add a second selection implementation.

- [ ] **Step 8: Run Task 2 tests**

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_admin_features -q
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_intake backend.tests.test_worker backend.tests.test_repair_account_customer_name -q
rtk env RUN_ACCOUNT_PERSONA_POSTGRES_TEST=true /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_persona_postgres -v
```

Expected: PASS with the PostgreSQL suite reporting zero skips; no test relies on a particular random distribution.

- [ ] **Step 9: Commit Task 2**

```bash
rtk git add backend/services/account_admin.py backend/repositories/ticket_repository.py backend/main.py backend/worker.py backend/tests/test_account_admin_features.py backend/tests/test_account_intake.py backend/tests/test_worker.py backend/tests/test_repair_account_customer_name.py backend/tests/test_account_persona_postgres.py
rtk git commit -m "feat: randomly pin Automation Personas"
```

### Task 3: Clear and reassign Persona during complete Rerun

**Files:**
- Modify: `backend/repositories/ticket_repository.py:1977`
- Modify: `backend/repositories/ticket_repository.py:10255`
- Modify: `backend/main.py:4999`
- Modify: `backend/main.py:5298`
- Test: `backend/tests/test_account_admin_features.py:92`
- Test: `backend/tests/test_repository_configuration.py:260`
- Test: `backend/tests/test_account_intake.py:376`

- [ ] **Step 1: Write failing reset tests**

Before each reset, create a real assignment with `resolve_account_persona(ticket_id)`. Extend expected counts:

```python
self.assertEqual(counts["persona_assignments_deleted"], 1)
```

Resolve again after reset with a patched chooser and assert a new assignment is persisted. Allow the new key to equal the previous key; the assertion is about a new draw/timestamp and restored persistence boundary, not forced difference.

Add a no-assignment case that returns `persona_assignments_deleted == 0`.

- [ ] **Step 2: Add failing PostgreSQL SQL-contract assertions**

Update the fake reset cursor in `backend/tests/test_repository_configuration.py` to handle:

```sql
DELETE FROM support_account_persona_assignments
WHERE ticket_id = %s
RETURNING ticket_id
```

Assert the assignment delete and workspace audit insert occur within the same one-commit transaction, and an audit failure rolls both back.

- [ ] **Step 3: Add failing Rerun job assertions**

In the existing completed Automation Rerun test, seed an old assignment and assert:

- job `persona_assignments_deleted == 1`;
- the newly created reply-job payload contains the post-reset Persona key/version;
- the repository now resolves that same stored assignment;
- Cases rerouted out of Automation remain unassigned after reset.

Also assert the single-Case terminal audit payload contains `persona_assignments_deleted`, so UI statistics and durable audit evidence cannot diverge.

- [ ] **Step 4: Implement assignment deletion in both repositories**

For in-memory reset:

- add `persona_assignments_deleted` to `empty_result` and returned counts;
- snapshot `_account_persona_assignments` for rollback;
- pop the Ticket assignment inside `_assignment_lock`;
- restore the snapshot if any later reset/audit operation fails.

For PostgreSQL reset:

- delete the assignment in the existing transaction after locking the Ticket and before rerouting state is returned;
- count `RETURNING` rows;
- include the count in the audit payload automatically passed through `counts`.

- [ ] **Step 5: Propagate Rerun statistics**

Add `persona_assignments_deleted: 0` to `_enqueue_account_rerun_job()`, map the reset result in `_run_account_full_reroute_job()`, and include it in `_record_account_rerun_terminal_audit()`. The existing call to `resolve_published_account_persona()` now uses Task 2's compatibility alias, so after reset it performs one random draw and persists it before `_create_account_reply_job()` pins the content.

- [ ] **Step 6: Run Task 3 tests**

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_admin_features backend.tests.test_repository_configuration backend.tests.test_account_intake -q
```

Expected: PASS, including single-Case customer-message retention and full-batch AI-only cleanup contracts.

- [ ] **Step 7: Commit Task 3**

```bash
rtk git add backend/repositories/ticket_repository.py backend/main.py backend/tests/test_account_admin_features.py backend/tests/test_repository_configuration.py backend/tests/test_account_intake.py
rtk git commit -m "feat: reselect Persona during complete rerun"
```

### Task 4: Explain random Persona behavior in Admin and Rerun UI

**Files:**
- Modify: `design.md:357`
- Modify: `ui/workspace-ui/admin/app.js:1147`
- Modify: `ui/account-ui/app.js:1728`
- Test: `backend/tests/test_workspace_admin_ui_contract.py:79`
- Test: `backend/tests/test_account_ui_contract.py:1`

- [ ] **Step 1: Update the canonical UI/behavior rule before UI code**

Extend `design.md` section 6.7 to state that Automation Router can contain multiple independently versioned thin Personas; new Automation Cases randomly select one enabled/published Persona, pin the version, and complete Rerun clears/reselects it. Preserve the rules that Signature is independently edited and Persona is not configured per Behavior. This changes behavior documentation only and introduces no new visual token or component exception.

- [ ] **Step 2: Write failing UI contract assertions**

Update the Admin fixture to contain all three Persona records and assert the rendered Persona workspace contains all names plus the meaning of random assignment and pinning. Add Account UI contract markers for `persona_assignments_deleted` and the confirmation that complete Rerun clears the pinned Persona.

- [ ] **Step 3: Run UI contracts and confirm the new copy is absent**

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_workspace_admin_ui_contract backend.tests.test_account_ui_contract -v
```

Expected: FAIL on the new random-assignment/pinned-version markers.

- [ ] **Step 4: Add compact Admin explanation**

Replace the current single sentence under `Automation Persona` with concise copy equivalent to:

```text
New Automation Cases are randomly assigned one enabled, published Persona.
The selected version stays pinned until a complete Rerun. Publishing or
disabling a Persona affects new assignments only.
```

Keep the existing Persona list, create form, Draft/Publish/Rollback/Enable controls, Signature editor, and layout. Do not add weights, a chart, a manual Case selector, or per-Behavior Persona controls.

- [ ] **Step 5: Add Rerun observability copy**

Append `${persona_assignments_deleted} old Persona assignments deleted` to the terminal Rerun summary. Add a confirmation bullet that the pinned Persona is cleared and selected again only if the rerun produces a new Automation reply. Do not rename or change the scope of either Rerun button.

- [ ] **Step 6: Validate JavaScript and UI tests**

```bash
rtk node --check ui/workspace-ui/admin/app.js
rtk node --check ui/account-ui/app.js
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_workspace_admin_ui_contract backend.tests.test_account_ui_contract -q
```

Expected: all commands PASS.

- [ ] **Step 7: Commit Task 4**

```bash
rtk git add design.md ui/workspace-ui/admin/app.js ui/account-ui/app.js backend/tests/test_workspace_admin_ui_contract.py backend/tests/test_account_ui_contract.py
rtk git commit -m "feat: explain Automation Persona assignment"
```

### Task 5: Add the dry-run-first Automated-only rollout runner

**Files:**
- Modify: `backend/main.py:4999`
- Create: `scripts/rerun_automated_account_cases.py`
- Modify: `backend/tests/test_account_intake.py`
- Create: `backend/tests/test_rerun_automated_account_cases.py`

- [ ] **Step 1: Write failing operation tests around injected HTTP calls**

Design the script as testable functions plus a small CLI. Use a fake/injected JSON request function and fake sleep. Cover:

- pagination freezes and deduplicates only `route_status == "automated"` Case IDs before any POST;
- dry-run reads existing assignments without creating them, writes baseline/progress/report files, and sends no POST;
- apply is rejected unless it resumes an existing dry-run directory;
- non-loopback `--base-url` values are rejected;
- apply posts only `/api/account/cases/{case_id}/rerun`, never the all-Cases POST;
- every frozen Case gets a deterministic per-operation `Idempotency-Key` before its first POST;
- retrying the same key returns/polls the original job and never schedules a second background rerun;
- jobs run sequentially and each reaches a terminal state before the next POST;
- three consecutive retryable 503/storage-start failures stop and preserve remaining IDs;
- a polling timeout stops immediately and resume polls the same persisted/idempotently recovered job;
- isolated terminal Case failures are recorded and processing continues;
- resume skips already terminal IDs and keeps the original frozen target set;
- an exclusive operation lock rejects two concurrent `--resume --apply` processes;
- changed `app_build.ref` or enabled/published Persona key/version/content fingerprint blocks apply;
- output directory mode is `0700` and JSON/Markdown files are `0600`.

- [ ] **Step 2: Run the new tests and confirm the module is missing**

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_rerun_automated_account_cases -v
```

Expected: FAIL with missing script/module.

- [ ] **Step 3: Add backward-compatible single-Case Rerun idempotency**

Accept an optional, length-limited `Idempotency-Key` header only on `POST /api/account/cases/{account_case_id}/rerun`. Pass it into `_enqueue_account_rerun_job()`, persist it in the job payload, and check for the same key plus canonical target Case before the global active-job check so a retry can recover its own queued/running job. Return the existing job without adding another background task; reject reuse for a different Case. Calls without the header, including the current UI and all-Cases endpoint, retain existing behavior.

Add API tests proving duplicate requests add only one background task and return the same `job_id`, while different keys produce the normal fresh rerun after the prior job is terminal. Do not use the key to attach to an unrelated active job.

- [ ] **Step 4: Implement the snapshot and report data model**

Use Python standard-library HTTP/file APIs plus an injected repository reader for exact assignment evidence. Expose:

```python
snapshot_automated_cases(client, assignment_reader, persona_reader) -> dict[str, Any]
run_frozen_cases(client, assignment_reader, operation, *, sleep) -> dict[str, Any]
write_operation_files(operation_dir, payload) -> None
render_comparison_markdown(payload) -> str
```

The repository reader must open the configured PostgreSQL repository without calling `initialize()`, call Task 2's read-only `get_account_persona_assignment()`, and reduce the result to key/version/timestamp. It must never call `resolve_account_persona()` during dry-run, and the CLI must close the repository in `finally`. Record the normalized loopback base URL, current `/health` `app_build.ref`, and exact enabled/published candidate fingerprint `(persona_key, published_version, canonical_content_sha256)`; store only the hash, not Prompt content. Require the same base URL/build/fingerprint at apply time and require the candidate set to be exactly `default-support`, `sid-bright`, and `sid-precise`, with each hash matching the approved instruction/opener/Signature. Verify every frozen API Case resolves to the same canonical Ticket in that repository before allowing apply; reject an in-memory/mismatched repository because a separate process cannot observe the live stack's assignments there.

CLI contract:

```text
--base-url http://localhost:8080
--output-root /tmp
--apply
--resume /tmp/supportportal-automation-persona-rerun-<UTC>
--poll-interval-seconds 5
--job-timeout-seconds 1200
```

Without `--apply`, create the restricted operation directory, freeze baseline IDs/details, and exit after writing the dry-run report. With `--resume`, load the immutable baseline and progress instead of querying a new target set.

Reject `--apply` without `--resume`. Restrict `--base-url` to loopback hosts, acquire an exclusive lock file for every resume/apply process, and recheck build/Persona fingerprints before the first POST and after any resumed stop.

- [ ] **Step 5: Implement safe sequential execution**

For each frozen Case:

1. Persist the Case's `starting` state and a non-sensitive idempotency key derived as SHA-256 of the operation UUID plus canonical Case ID.
2. POST its existing single-Case endpoint with that key; a retry must return the same job.
3. Persist the returned `job_id` immediately.
4. Poll `/api/account/rerun-jobs/{job_id}` until `completed`, `completed_with_errors`, or `failed`.
5. Fetch the Case detail and the exact read-only stored assignment after terminal state.
6. Record old/new route, old/new Persona key/version, email counts, reply counts, and error.
7. Atomically rewrite progress/report files after every state transition and terminal Case.

Treat HTTP 409 as an external active-job stop, not permission to attach to an unrelated job. Retry the same idempotency key for explicit retryable 503/storage-start failures and stop after three consecutive failures. Never include customer message bodies, customer names, email addresses, credentials, full Persona prompts, or Persona assignment content in the operation report.

- [ ] **Step 6: Run runner and endpoint tests plus syntax validation**

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_rerun_automated_account_cases backend.tests.test_account_intake -q
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile scripts/rerun_automated_account_cases.py
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
rtk git add backend/main.py scripts/rerun_automated_account_cases.py backend/tests/test_account_intake.py backend/tests/test_rerun_automated_account_cases.py
rtk git commit -m "feat: add Automated-only Persona rollout runner"
```

### Task 6: Update design, Prompt log, feature list, and Roadmap

**Files:**
- Modify: `docs/prompt_change_log.md`
- Modify: `docs/feature_list.md:34`
- Modify: `docs/feature_list.md:98`
- Modify: `docs/roadmap.html:1467`
- Modify: `docs/roadmap.html:2082`

- [ ] **Step 1: Verify the canonical design rule is already present**

Confirm Task 4 updated `design.md` section 6.7 before the UI implementation and that it still states multiple independently versioned thin Personas, random enabled/published selection, pinned versions, complete-Rerun reselection, separately edited Signature, and no per-Behavior Persona.

- [ ] **Step 2: Append the required Prompt change-log entry**

Add a 2026-08-07 entry with:

- subsystem: `/account` Automation Persona registry and assignment;
- version: `automation-persona-presets-v1`; shared renderer/model configuration unchanged;
- exact three styles and shared Signature;
- reason for thin Persona separation and random assignment;
- affected files/config;
- expected behavior, including pinning and complete-Rerun reselection;
- final verification commands and live markers.

- [ ] **Step 3: Update the major feature record**

Replace the stable-hash wording in `docs/feature_list.md` with one short capability sentence describing three independently managed Persona presets, random first assignment, pinned versions, and complete-Rerun reselection. Use the exact same sentence in the relevant `Client 端` and `Ticket Dashboard` completed lists without adding implementation details; this file has no Admin category.

- [ ] **Step 4: Update Roadmap rollout state**

Update the Phase 2 Persona, Rerun, Admin, done-summary, and architecture text to remove stable Ticket hashing and document the three presets, random/pinned lifecycle, assignment reset, and the operator-controlled Automated-only rollout step. Record the shipped capability only; do not claim the one-time data operation has completed and do not put `/tmp` report paths into tracked Roadmap content. The post-deploy operation result belongs in the final acceptance report.

- [ ] **Step 5: Verify documentation contracts**

```bash
rtk python3 scripts/verify_feature_list.py
rtk rg -n "automation-persona-presets-v1|Sid Precise|Sid Bright|Sid Warm|random" design.md docs/prompt_change_log.md docs/feature_list.md docs/roadmap.html
rtk git diff --check
```

Expected: feature-list validation passes; old stable-Persona allocation wording is gone from current-state docs; no whitespace errors.

- [ ] **Step 6: Commit Task 6**

```bash
rtk git add docs/prompt_change_log.md docs/feature_list.md docs/roadmap.html
rtk git commit -m "docs: record Automation Persona rollout"
```

### Task 7: Run final verification and merge the feature

**Files:**
- Verify all files changed in Tasks 1-6

- [ ] **Step 1: Confirm branch/worktree safety before finalization**

From the task worktree, run and report:

```bash
rtk git status --short --branch
rtk git branch -vv
rtk git worktree list --porcelain
```

Expected: current worktree is the task's `codex/*` branch, root workspace is clean `main`, and only task-owned changes exist.

- [ ] **Step 2: Run the consolidated targeted backend suite**

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_admin_features backend.tests.test_repository_configuration backend.tests.test_account_intake backend.tests.test_worker backend.tests.test_repair_account_customer_name backend.tests.test_agent_config backend.tests.test_workspace_api backend.tests.test_workspace_admin_ui_contract backend.tests.test_account_ui_contract backend.tests.test_rerun_automated_account_cases -q
```

Expected: PASS.

- [ ] **Step 3: Run real PostgreSQL assignment/migration verification**

```bash
rtk env RUN_ACCOUNT_PERSONA_POSTGRES_TEST=true /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_persona_postgres -v
```

Expected: PASS with zero skips; temporary schema removed even on failure. If either DSN is unavailable, stop and restore/link the root `.env` rather than accepting a skipped suite.

- [ ] **Step 4: Run frontend, docs, and diff checks**

```bash
rtk node --check ui/workspace-ui/admin/app.js
rtk node --check ui/account-ui/app.js
rtk python3 scripts/verify_feature_list.py
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile scripts/rerun_automated_account_cases.py
rtk git diff --check origin/main...HEAD
```

Expected: all PASS.

- [ ] **Step 5: Finalize directly to `main`**

Use `scripts/workflow/finalize_task_to_main.sh` with the stable task branch and a `--verify` command that reruns the consolidated unit/UI/docs/syntax checks after refreshing against latest `origin/main`. The isolated PostgreSQL suite must also be rerun against that refreshed HEAD and report zero skips before invoking finalization. The script pushes, opens/reuses the PR, squash-merges, fast-forwards root `main`, and removes only this task's workspace/local branch.

The current finalize script does not itself run CodeGraph. After it succeeds, run the required `rtk codegraph sync` explicitly from the root `main` workspace and report its result before live stack verification.

If the PR is immediately `CLEAN` and GitHub rejects `--auto` because it can merge directly, reuse the same PR and perform the documented direct squash-merge recovery; do not create another PR.

### Task 8: Restart the official stack and verify the live feature

**Files:**
- No tracked changes unless live verification exposes a defect

If live verification exposes a product defect, do not edit root `main`. Stop the rollout, create a new dedicated fix worktree through the project workflow, merge the fix, and restart verification from the new merged commit before any data operation.

- [ ] **Step 1: Inspect and normalize the official stack**

From root `main`:

```bash
rtk bash scripts/workflow/inspect_single_host_stack_mode.sh
```

If it reports auxiliary `deploymentlw`, report it and run:

```bash
rtk bash scripts/workflow/cleanup_single_host_aux_stack.sh
```

- [ ] **Step 2: Restart with the required lightweight path**

```bash
rtk bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote
```

Expected: build and startup succeed without falling back to the prior image.

- [ ] **Step 3: Verify build provenance and health**

```bash
rtk bash scripts/workflow/inspect_single_host_stack_mode.sh
rtk curl -fsS http://localhost:8080/health
```

Expected: `status` is healthy and `app_build.ref` matches root `main`.

- [ ] **Step 4: Verify the live Persona registry and UI marker**

Use an authenticated Admin API request or an in-container repository read to assert exactly these preset keys are enabled and published: `default-support`, `sid-bright`, `sid-precise`. Verify each display name, published instruction, blank opener, exact shared Signature, and system seed marker against the approved design; log only key/version/content SHA-256, not credentials or full Persona content. Confirm the live Admin JS contains the random-assignment/pinned-version copy and the Account JS contains the assignment-deletion statistic.

### Task 9: Execute the one-time Automated-only Rerun

**Files:**
- Runtime artifacts only: `/tmp/supportportal-automation-persona-rerun-<UTC>/`

- [ ] **Step 1: Freeze the live target set in dry-run mode**

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python scripts/rerun_automated_account_cases.py --base-url http://localhost:8080 --output-root /tmp
```

Expected: prints the absolute restricted operation directory, creates `baseline.json`, `progress.json`, and `comparison.md`, submits zero POST requests, and records only Cases whose pre-operation `route_status` is `automated`.

- [ ] **Step 2: Resolve the exact destructive targets**

Read the generated summary and verify:

- frozen IDs are deduplicated;
- frozen count matches the dry-run Automated list count;
- no Account & Billing, Technical, Non-technical, Conversation, or Human Review Case appears;
- the enabled/published candidate fingerprint contains exactly the three approved Persona keys, versions, and content hashes;
- baseline `app_build.ref` equals the merged root `main` commit;
- no unrelated Account rerun job is active.

If any gate fails, stop before `--apply`, preserve the operation directory, and report the exact discrepancy.

- [ ] **Step 3: Apply using the frozen operation directory**

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python scripts/rerun_automated_account_cases.py --base-url http://localhost:8080 --resume /tmp/supportportal-automation-persona-rerun-<UTC> --apply
```

Expected: sequential single-Case Reruns only; applicable internal emails may resend; old non-customer history is deleted under the existing single-Case contract; each new Automation reply pins one random Persona.

- [ ] **Step 4: Verify operation completion**

Acceptance gates:

- every frozen Case has a terminal result or explicit resumable failure;
- every submitted Case used one persisted idempotency key and maps to exactly one rerun job;
- no Case outside the frozen set has the operation's rerun audit/job ID;
- every newly published Automation reply records one of the three Persona keys and its version;
- Cases rerouted out of Automation have no forced Persona reply;
- report includes old/new Persona distribution, same-Persona redraws, route changes, emails, replies, and errors;
- operation directory remains `0700`, report files remain `0600`.

If the runner stops after three consecutive retryable failures or a poll timeout, do not start a new baseline. Fix the external condition and resume the same operation directory.

### Task 10: Final acceptance report

- [ ] **Step 1: Report implementation and rollout evidence**

Include:

- merged PR URL and root `main` commit;
- targeted and PostgreSQL test results;
- lightweight restart path;
- `/health` status and `app_build.ref`;
- live Persona registry keys/versions and UI marker;
- absolute Automated-only operation report path;
- frozen count, terminal success/failure/recovery counts, Persona distribution, emails, replies, and route changes;
- any resumable failures or residual risks.

## Final Acceptance Criteria

- Three approved thin Personas exist, are independently versioned, and initially share the exact Sid Signature.
- First assignment is a simple uniform random draw from enabled/published Personas and is persisted atomically.
- Existing Case replies, delayed jobs, Outlook follow-ups, recovery, and name repair reuse the pinned Persona/version.
- Complete Rerun deletes the assignment, permits the same Persona to be drawn again, and pins the new result only when a new Automation reply is needed.
- No Ticket ID hash, Round Robin state, weights, per-Behavior Persona, or per-message reselection remains.
- Persona generation facts/safety/language behavior is unchanged; both generation failures and a missing eligible Persona remain fail-closed to Human Review with no customer copy.
- Admin and Account UIs explain the new lifecycle without changing normal Rerun scope.
- Only the frozen pre-operation Automated Cases are rerun during rollout; per-Case idempotency prevents duplicate side effects across retries/resume, and complete restricted local evidence records the exact stored assignments.
