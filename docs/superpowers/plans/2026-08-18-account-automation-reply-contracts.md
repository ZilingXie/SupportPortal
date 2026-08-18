# Account Automation Reply Contracts - Luna Max Implementation Plan

Status: `execution_ready`

- Project Task: `p1-50` (create in Stage 0)
- Branch: `codex/account-automation-reply-contracts`
- Worktree: `/Users/xieziling/Desktop/personal_proj/SupportPortal/.worktrees/account-automation-reply-contracts`
- Base commit: `902f3b5146b1acb4e5885541081fe10760b14bc7`
- Implementation model: Luna max
- PR strategy: one complete code PR after all coding stages pass; stage boundaries use local commits, not partial PRs
- Runtime strategy: merge first, restart the official stack, run one all-case rerun, then validate Cases `12839`, `12571`, and `12744`

## Objective

Make `/account` Automation replies and reruns enforce one customer-visible contract for the three active Automation subcategories:

- `fraud_account`
- `enablement`
- `account_suspension`

The completed behavior must guarantee the required customer information, prevent AI-generated signatures, use the same reply contract for Intake and rerun, and close tickets only at the explicitly authorized points.

## Immutable Scope

1. Keep the active Automation membership limited to `fraud_account`, `enablement`, and `account_suspension`.
2. Do not migrate Primary Category fields. Fraud and Suspension may retain `account_billing`; Enablement may retain `backend_operation`. Automation membership remains expressed by the existing route metadata.
3. Do not add feature flags, retries, compatibility layers, caches, migrations, or generic abstractions beyond demonstrated current contracts.
4. Do not use a fallback customer reply. Invalid or incomplete generated text must fail closed or move the Case to Human Review.
5. Do not use a browser. Validate through tests, authenticated local APIs, repository state, job state, and official-stack health/build evidence.
6. Do not run `supportportal-run-report`.
7. Do not use subagents or delegate the implementation.
8. Do not forge an internal employee's Enablement-completion email. The live Case validates the submission reply; the completion path is mandatory in Worker integration tests and may be live-checked only if a real explicit completion reply arrives.
9. Preserve the existing 6-10 minute persistent reply scheduling, stale-trigger cancellation, `asked_field_keys`, rerun delivery keys, `replace_existing_reply`, and outcome-unknown delivery fence.
10. Preserve the production Zendesk delivery-intent behavior introduced by base commit `902f3b5`: a customer reply must pass the final content/intent contract before `publish_account_reply()` can persist a production Zendesk delivery intent.

## Required Customer Behavior

### Fraud Account

1. Collect the existing required information using the current one-follow-up rule.
2. Send the internal handoff email.
3. Only after confirmed email delivery, tell the customer that the relevant team will contact them within 24 hours.
4. Do not automatically close the Fraud ticket.

### Account Suspension

1. First reply asks for the preferred contact email and whether the ticket email should be used.
2. The same first reply says:
   - the relevant team will contact the customer within 24 hours;
   - the ticket will close after contact confirmation and successful handoff;
   - the customer may reopen it if nobody contacts them within 24 hours.
3. Send the internal email only after an explicit, unambiguous email confirmation.
4. After internal email success, publish the closing reply.
5. Close the ticket only after that closing reply is durably published.
6. Ambiguous or conflicting confirmation, email failure, Persona failure, or reply publication failure must not close the ticket.

### Enablement

1. Preserve the fail-closed order: prepare the request, send the internal email, then publish the customer confirmation.
2. The customer confirmation says activation may take up to 24 hours and the change window is Monday-Friday or an unambiguous equivalent.
3. When an internal reply explicitly says the feature is enabled, activated, provisioned, or turned on, notify the customer and close the ticket atomically.
4. Negative forms such as `not enabled`, `unable to enable`, `cannot enable`, or `can't enable` must not close the ticket.

### All AI Replies

The final customer content must not contain an AI-generated signature or signoff block, including examples such as:

```text
Best,
Sid
Support Engineer 2
```

## Canonical Reply Contract

Use one canonical intent in `reply_facts["reply_intent"]`. A top-level payload intent, when retained for audit/compatibility, must equal the nested value.

| Intent | Behavior | Close after publish |
| --- | --- | --- |
| `request_missing_information` | Collect missing Fraud/Enablement information | No |
| `submission_confirmation` | Enablement request submitted | No |
| `fraud_handoff_confirmation` | Fraud handoff delivered | No |
| `account_suspension_contact_confirmation_request` | Ask for Suspension contact email | No |
| `account_suspension_handoff_and_close` | Suspension handoff delivered and closing reply | Yes |
| `enablement_completed_and_close` | Enablement explicitly completed | Yes |
| `resolution_update` | Internal update without explicit completion | No |

Rules:

- Derive `close_after_publish` from the canonical intent; callers must not independently decide closure.
- Reject top-level/nested intent conflicts before generation and again before publication.
- Treat the legacy `fraud_handoff_and_close` contract as invalid for any unpublished job. Move it to Human Review rather than closing a Fraud ticket.
- A failed validation must not call `publish_account_reply()`, because that repository operation now also persists production Zendesk delivery intent.

## Verified Starting State

The following facts were verified before this task worktree was created and must be cheaply rechecked before the affected stage:

- Root `main` and `origin/main` were synchronized at `902f3b5`.
- The root workspace was clean.
- Current active Automation membership already contains exactly the three requested subcategories.
- `automation-persona-v8` receives Enablement SLA/window facts but does not validate their presence in generated text.
- Model-generated tail signatures are currently accepted.
- Fraud currently persists a generic nested intent alongside a top-level closing intent and may close after publish.
- Full rerun does not implement the new Suspension two-stage execution path.
- The focused baseline previously ran 68 tests with 67 passing; the only error was the obsolete test asserting that Suspension rerun never executes.
- The live database snapshot contained 199 Account Cases, all staging and none production/Zendesk-linked. This is drift-prone and must be rechecked immediately before the full rerun.
- Case `12839` is a complete Enablement request whose current reply lacks the SLA/window and contains a signature.
- Case `12571` is Fraud and currently lacks `contact_information`; its live validation therefore requires one controlled staging customer reply after rerun.
- Case `12744` is an older `not_automated` Suspension Case with no AI reply; rerun must migrate it into the contact-confirmation workflow.

## Execution Protocol

At the start of every stage:

1. Read `/Users/xieziling/.codex/RTK.md`; prefix every shell command with `rtk`.
2. Confirm the current directory is the task worktree, the branch is `codex/account-automation-reply-contracts`, and root remains clean `main`.
3. Compare the current code with this plan. Stop and ask only if a material conflict changes scope, correctness, behavior, acceptance, or verification.
4. Keep changes inside the stage's allowed files unless an existing contract proves another file is required. Record any equivalent minor adjustment in the Stage Log.
5. Run the stage-specific tests.
6. Update this file with status, observed results, changed paths, local commit SHA, and the next resume point.
7. Create a local commit. Do not push or open a PR until Stage 4 passes.

Before the first runtime code edit, read and follow the complete `karpathy-guidelines` skill. Record that action in the Stage Log.

## Stage Status

- [x] Stage 0 - Task registry and implementation preflight
- [x] Stage 1 - Canonical reply contract and Persona output guarantees
- [x] Stage 2 - Fraud, Suspension, and Enablement normal flows
- [x] Stage 3 - Full rerun and recovery consistency
- [x] Stage 4 - Integrated verification, documentation, and finalize readiness
- [ ] Stage 5 - One PR, merge, official-stack restart, and build verification
- [ ] Stage 6 - One full rerun and three-Case live validation

Current stage: `Stage 5`

## Stage 0 - Task Registry And Preflight

### Allowed changes

- `docs/project/tasks/p1-50.json`
- `docs/projectoverview-data.js` only through the generator
- this plan file

### Actions

1. Recheck branch/worktree/root safety state.
2. Read `karpathy-guidelines` before later code edits.
3. Create `docs/project/tasks/p1-50.json` under:
   - Phase: `phase-1`
   - Module: `account-automation`
   - Function: `automation-execution-loop`
   - Title: `统一 Account Automation 客户回复与 Rerun 契约`
   - Status: `in_progress`
4. Add acceptance criteria matching this plan, with no claim of runtime completion.
5. Generate and check Project Overview.
6. Confirm the relevant symbols and tests still match the base assumptions.

### Verification

```bash
rtk python3 scripts/generate_project_overview.py --write
rtk python3 scripts/generate_project_overview.py --check
rtk git diff --check
```

### Gate

The Task exists, Project Overview checks pass, and no runtime file has changed.

## Stage 1 - Canonical Reply Contract And Persona Guarantees

### Primary files and symbols

- `backend/services/account_reply_jobs.py`
  - intent constants
  - `create_account_reply_job`
  - the smallest shared contract validator needed by creation and Worker publication
- `backend/services/automation_persona.py`
  - `AUTOMATION_PERSONA_PROMPT_VERSION`
  - `build_account_automation_reply_facts`
  - `_normalize_ownership_facts`
  - `_assert_ownership_contract`
  - `render_automation_reply`
- `backend/worker.py`
  - `_prepare_account_reply_job`
  - `_publish_account_reply_job`
- `backend/tests/test_automation_persona.py`
- `backend/tests/test_account_reply_version_fence.py`
- the narrow Worker tests required for pre-publication failure behavior

### Actions

1. Add `fraud_handoff_confirmation`; remove new use of the legacy Fraud closing intent.
2. Canonicalize nested/top-level intent and derive closure from the canonical intent.
3. Fail closed on conflicts and on legacy unpublished Fraud-closing jobs.
4. Upgrade to `automation-persona-v9`.
5. Add intent-specific validators:
   - Enablement submission: 24-hour activation plus Monday-Friday window.
   - Fraud handoff: relevant team, contact/reach out, and 24 hours.
   - Suspension first reply: email question, ticket-email choice when available, 24 hours, close, reopen.
   - Suspension closing: handoff, 24 hours, close, reopen.
   - Enablement completion: enabled/activated and close.
6. Add deterministic removal of trailing signoff/signature blocks only. Do not alter ordinary body text containing words such as `best` or `regards`.
7. Revalidate content after signature removal. Invalid content goes to Human Review and is never passed to `publish_account_reply()`.
8. Preserve the new atomic Zendesk delivery-intent creation inside repository publication; do not create or claim a delivery intent for rejected content.

### Tests

Cover positive and negative validators, Case `12839`-style signature output, body-text false positives, nested/top-level conflicts, derived closure, legacy Fraud close rejection, and no repository publication/delivery intent on failure.

### Verification

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest \
  backend.tests.test_automation_persona \
  backend.tests.test_account_reply_version_fence \
  backend.tests.test_worker
rtk git diff --check
```

### Gate

The final renderer cannot return a contract-incomplete or signed reply, and the Worker cannot publish a conflicting or legacy Fraud-closing job.

## Stage 2 - Three Normal Automation Flows

### Primary files and symbols

- `backend/main.py`
  - `_automation_reply_facts`
  - Account Intake reply-job creation
  - Account customer-reply continuation
- `backend/services/account_suspension_automation.py`
  - `contact_confirmation_reply_facts`
  - `closing_reply_facts`
  - existing deterministic state transitions
- `backend/worker.py`
  - `_handle_non_billing_automation_reply`
- `backend/tests/test_account_intake.py`
- `backend/tests/test_worker.py`
- existing Fraud/Enablement/Suspension handler tests

### Actions

1. Fraud:
   - preserve existing collection and one-follow-up behavior;
   - send internal email before the customer handoff confirmation;
   - use `fraud_handoff_confirmation`;
   - never close after handoff.
2. Suspension:
   - first reply contains the complete email/24-hour/close/reopen contract;
   - send no internal email before explicit confirmation;
   - preserve Human Review for ambiguous/conflicting confirmation;
   - schedule the closing reply only after email success;
   - close only after durable reply publication.
3. Enablement:
   - preserve internal-email success before customer confirmation;
   - include the 24-hour activation and Monday-Friday window;
   - close only after an explicit positive internal completion reply;
   - keep negative replies open.
4. Ensure all job creation paths pass one consistent canonical intent.

### Verification

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest \
  backend.tests.test_account_verification_automation \
  backend.tests.test_enablement_automation \
  backend.tests.test_account_intake \
  backend.tests.test_worker
rtk git diff --check
```

### Gate

All three normal Intake/customer-reply workflows satisfy the customer contract and close only at the authorized points.

## Stage 3 - Full Rerun And Recovery Consistency

### Primary files and symbols

- `backend/services/account_full_reroute.py`
  - `AccountFullRerouteResult`
  - `reprocess_account_case`
- `backend/main.py`
  - `_resume_account_rerun_side_effect`
  - `_run_account_rerun_post_commit_side_effects`
  - `_run_account_full_reroute_job`
- rerun/recovery tests

### Actions

1. Add the active `account_suspension` implementation to full reroute.
2. Fresh Suspension rerun must rebuild the contact workflow:
   - never treat the first problem-description message as contact confirmation;
   - use a later explicit confirmation when present;
   - ask for confirmation when absent;
   - move ambiguous/conflicting history to Human Review.
3. Persist enough deterministic rerun state for reply-only recovery to rebuild the same facts, canonical intent, and closure decision.
4. Enablement rerun must use the SLA/window contract.
5. Fraud rerun must use the 24-hour handoff intent and remain open.
6. Preserve `:rerun:<job_id>`, recipient resolution before Commit, sent-email evidence, `replace_existing_reply`, and outcome-unknown/manual-confirmation behavior.
7. Do not resend a completed internal handoff during reply-only recovery.

### Verification

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest \
  backend.tests.test_account_full_reroute \
  backend.tests.test_account_reroute_dispatch \
  backend.tests.test_account_rerun_recovery \
  backend.tests.test_account_intake
rtk git diff --check
```

### Gate

Intake, fresh rerun, and reply-only recovery generate the same canonical customer reply and closure behavior for each Automation type.

## Stage 4 - Integrated Verification And Documentation

### Documentation changes

- `docs/feature_list.md`
  - remove the obsolete statement that non-fraud Suspension is not automated;
  - describe the three active Automation workflows concisely.
- `docs/prompt_change_log.md`
  - add the `automation-persona-v9` entry, reason, affected files, expected behavior, and actual verification.
- `docs/project/tasks/p1-50.json`
  - add implementation/test evidence and mark `done` only after the automated acceptance criteria pass.
- `docs/projectoverview-data.js`
  - regenerate; never edit manually.
- this plan file
  - record completed coding stages, commands, results, and final local commit.

### Full targeted suite

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest \
  backend.tests.test_automation_routing \
  backend.tests.test_automation_persona \
  backend.tests.test_account_reply_version_fence \
  backend.tests.test_account_verification_automation \
  backend.tests.test_enablement_automation \
  backend.tests.test_account_full_reroute \
  backend.tests.test_account_reroute_dispatch \
  backend.tests.test_account_intake \
  backend.tests.test_worker \
  backend.tests.test_account_rerun_recovery
```

### Static and documentation checks

```bash
rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile \
  backend/main.py \
  backend/worker.py \
  backend/services/automation_persona.py \
  backend/services/account_reply_jobs.py \
  backend/services/account_suspension_automation.py \
  backend/services/account_full_reroute.py
rtk python3 scripts/verify_feature_list.py
rtk python3 scripts/generate_project_overview.py --write
rtk python3 scripts/generate_project_overview.py --check
rtk git diff --check
```

### Gate

All targeted tests and checks pass on the final task branch, the plan is updated through Stage 4, and the diff contains no unrelated changes.

## Stage 5 - One PR And Official Stack

Before finalization, recheck task/root/worktree ownership and run the fresh verification through the repository workflow:

```bash
rtk bash scripts/workflow/finalize_task_to_main.sh \
  codex/account-automation-reply-contracts \
  --verify "rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_automation_routing backend.tests.test_automation_persona backend.tests.test_account_reply_version_fence backend.tests.test_account_verification_automation backend.tests.test_enablement_automation backend.tests.test_account_full_reroute backend.tests.test_account_reroute_dispatch backend.tests.test_account_intake backend.tests.test_worker backend.tests.test_account_rerun_recovery" \
  --pr-title "Fix Account Automation reply and rerun contracts" \
  --commit-message "Fix Account Automation reply and rerun contracts"
```

After the script merges, synchronizes root `main`, updates CodeGraph, and removes the task worktree/local branch, run from root `main`:

```bash
rtk bash scripts/workflow/inspect_single_host_stack_mode.sh
rtk bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote
rtk curl -fsS http://127.0.0.1:8080/health
```

Required evidence:

- PR number and merged commit
- final root `main` SHA
- official project `deployment`
- no auxiliary stack, or successful documented cleanup
- image/runtime/`health.app_build.ref` match final root `main`
- running reply jobs record `automation-persona-v9`

## Stage 6 - One Full Rerun And Live Cases

Do not use a browser. Use an authenticated local API client that loads the bootstrap Admin credentials from `.env` into process memory. Never print the password, access token, customer email, or internal recipient.

### Preflight

1. Confirm no active rerun job.
2. Freeze and record the current Account Case count.
3. Confirm all Cases remain staging and the production/Zendesk-linked count remains zero. If this has changed, stop before POST and report the new side-effect scope.
4. Confirm the internal recipient configuration for all active handlers resolves without printing addresses.
5. Confirm Cases `12839`, `12571`, and `12744` still exist and have no concurrent revision change.

### Full rerun

Call exactly once:

```text
POST /api/account/rerun-jobs
```

Poll the returned job through:

```text
GET /api/account/rerun-jobs/{job_id}
```

Required terminal state:

- `scope = all_cases`
- `status = completed`
- `processed = total`
- `failed = 0`
- `failed_case_ids = []`
- `remaining = 0`
- `degraded = false`

If the result is failed or unknown, do not launch another full rerun. Inspect the existing checkpoint and delivery evidence. Use `/resume` only when the persisted job exposes a safe retry mode; outcome-unknown email delivery requires manual confirmation.

### Case 12839 - Enablement

- Internal email status is `sent`.
- Latest active AI reply contains the 24-hour activation SLA.
- It contains Monday-Friday or an unambiguous equivalent change window.
- It contains no tail signature or `Sid / Support Engineer 2` block.
- Ticket remains open until a real positive internal completion reply.
- Automated Worker tests prove positive completion closes and negative completion does not.

### Case 12571 - Fraud

This Case currently lacks `contact_information`.

1. Verify the rerun first asks for the missing contact information.
2. Through the local Account API, submit one controlled staging customer reply using the Case's existing ticket email without printing it.
3. Wait for the persistent reply job.
4. Verify internal email status is `sent`.
5. Verify the final reply says the relevant team will contact the customer within 24 hours.
6. Verify no signature.
7. Verify canonical intent is `fraud_handoff_confirmation`, no `close_after_publish` exists, and the ticket remains open.

### Case 12744 - Account Suspension

After rerun, verify:

- Primary Category may remain `account_billing`.
- `subcategory = account_suspension`.
- `route_status = automated`.
- `automation_handler = account_suspension`.
- workflow state is `awaiting_contact_confirmation`.
- first reply asks for preferred email and whether the ticket email should be used.
- first reply contains relevant team, 24 hours, close, and reopen.
- first reply has no signature.
- no internal email has been sent and the ticket remains open.

Then submit this controlled staging response through the local Account API:

```text
Yes, please use the email address on this ticket.
```

Verify:

- exactly one internal email is sent;
- closing intent is `account_suspension_handoff_and_close`;
- closing reply contains handoff, 24 hours, close, and reopen;
- closing reply has no signature;
- only after reply publication does the ticket become `resolved` and workflow state become `closed`.

## Failure Rules

1. Any material conflict with this plan stops the affected stage before mutation and is reported to the user.
2. A failed stage remains incomplete; update `Resume From Here` with the exact failing command and observed error.
3. Do not hide failures with fallback content, retries, or manual database edits.
4. Do not rerun a command that may have produced an email or customer reply until persisted state establishes whether the side effect occurred.
5. If live validation finds a code defect after the main PR merges, report `状态：已合并，运行验证未完成`, create a new task worktree from current `main`, fix only the demonstrated defect, and use a new repair PR. Do not reuse the removed task branch.

## Completion Contract

Report `状态：已完成` only when:

- all Stage 4 tests/checks passed;
- the single implementation PR merged successfully;
- root `main` is synchronized;
- the original task worktree and local branch were removed;
- official-stack restart, health, build provenance, and v9 runtime marker passed;
- the all-case rerun was executed exactly once and completed successfully;
- Cases `12839`, `12571`, and `12744` passed their live acceptance checks;
- no Fraud ticket was auto-closed;
- no signed AI reply was published;
- no outcome-unknown email was resent.

## Stage Log

| Stage | Status | Changed paths | Verification | Local commit | Notes |
| --- | --- | --- | --- | --- | --- |
| 0 | completed | `docs/project/tasks/p1-50.json`, `docs/projectoverview-data.js`, this plan | `python3 scripts/generate_project_overview.py --write`; `python3 scripts/generate_project_overview.py --check`; `git diff --check` all passed | `bbbb70c689d2aa4818024ad3583631dc0c9e74b8` | Created Task `p1-50`; generator accepts `active` (not the plan shorthand `in_progress`) and `test`/`document` evidence types; no runtime files changed. karpathy-guidelines was read in full before runtime edits. |
| 1 | completed | `backend/services/account_reply_jobs.py`, `backend/services/automation_persona.py`, `backend/worker.py`, related Persona/version-fence/Worker tests | `python -m unittest backend.tests.test_automation_persona backend.tests.test_account_reply_version_fence backend.tests.test_worker` (116 passed); `py_compile` and `git diff --check` passed | `d6707564b0688e4a5e82e9e4a647529926dd6106` | Added canonical intent/derived closure contract, rejected intent conflicts and legacy Fraud close jobs, upgraded to `automation-persona-v9`, added intent-specific content checks and deterministic trailing-signature removal, and blocked invalid content before `publish_account_reply()`. |
| 2 | completed | `backend/main.py`, `backend/services/account_suspension_automation.py`, `backend/services/automation_persona.py`, `backend/worker.py`, `backend/tests/test_account_intake.py`, `backend/tests/test_account_verification_automation.py`, `backend/tests/test_worker.py` | `python -m unittest backend.tests.test_account_verification_automation backend.tests.test_enablement_automation backend.tests.test_account_intake backend.tests.test_worker` (277 passed); `py_compile` and `git diff --check` passed | `79383988a5dfbf6077fd6c3cf674a40392bc243a` | Fraud now uses `fraud_handoff_confirmation` and remains open; Suspension uses explicit confirmation, durable closing-reply gating, and Human Review for ambiguity/failure; Enablement completion accepts only explicit positive completion and validates the closing reply. Active membership compatibility tests now assert inactive legacy routes have no side effects. The Suspension negative parser was narrowed so uncertainty such as `not sure` is not treated as an explicit negative. |
| 3 | completed | `backend/services/account_full_reroute.py`, `backend/main.py`, `backend/tests/test_account_full_reroute.py`, `backend/tests/test_account_reroute_dispatch.py` | `python -m unittest backend.tests.test_account_full_reroute backend.tests.test_account_reroute_dispatch backend.tests.test_account_rerun_recovery backend.tests.test_account_intake` (211 passed); `py_compile` and `git diff --check` passed | `bb0b912349d12b5e3d683bdeb1b93e7a9ac0af60` | Added active Suspension full-rerun reconstruction: the first customer message cannot confirm contact, later explicit confirmation drives handoff, and ambiguous/conflicting history enters Human Review. Persisted rerun intent/workflow now rebuilds Suspension contact/closing replies during recovery; closing reply is scheduled only after email success, and reply-only recovery does not resend completed handoffs. The rerun dispatch test sets a non-secret placeholder `TICKET_DB_DSN` before importing `backend.main`, so the Plan command runs without external environment setup. |
| 4 | completed | `docs/feature_list.md`, `docs/prompt_change_log.md`, `docs/project/tasks/p1-50.json`, `docs/projectoverview-data.js`, this plan | Integrated suite: `367 tests passed`; `py_compile` passed; `python3 scripts/verify_feature_list.py` passed; `python3 scripts/generate_project_overview.py --write` and `--check` passed; `git diff --check` passed | `341a99cdad7f1a793a940a6eb3f19e61a3d0a91f` (pre-Stage 4 metadata); Stage 4 changes are pending commit | Synchronized the feature list to the three active Automation workflows, appended the `automation-persona-v9` prompt/model change record, marked Task `p1-50` done after automated acceptance evidence, and regenerated Project Overview. Stage 5 finalization and post-merge live validation remain. |
| 5 | pending |  |  |  | One merged implementation PR |
| 6 | pending | runtime only |  | n/a | Full rerun and three live Cases |

## Resume From Here

Start at Stage 5 in the existing task worktree. Do not recreate the branch or worktree. Recheck current Git state and root cleanliness, commit the Stage 4 documentation/registry changes, then run the exact `finalize_task_to_main.sh` command from this Plan. Stage 4 automated verification is green; after merge, restart and validate the official stack before the single Stage 6 full rerun.
