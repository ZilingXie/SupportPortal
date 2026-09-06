# Prompt Change Log

## 2026-09-06 - Persona publishes directly after deterministic safety checks (p2-144)

- Area or subsystem: Account Automation Persona generation, reply-job audit metadata, and Worker publication.
- Prompt or model version: `automation-persona-v27` -> `automation-persona-v28`; independent `automation-persona-review-v2` removed; model profile unchanged (`automation_persona`, currently `gpt-5.6-luna/low`).
- Reason: Production Case 13314 produced a usable Fraud missing-information reply plan but the independent semantic Reviewer rejected both bounded rounds with `intent_policy_violation`. The Reviewer was enforcing soft completeness and wording preferences as a publication gate, creating false Human Review transitions after the Persona had already received authoritative structured facts.
- Behavior change: Route and handlers continue to build `reply_facts`; one pinned Persona writes the complete body with a single provider attempt. Missing-information labels, Enablement timing, Fraud/Suspension commitments, redundancy, and phrasing remain prompt guidance and no longer block publication. Only a deterministic hard safety or format failure can return enumerated feedback to the same Persona for one complete rewrite; a second safety failure or any model failure transitions to Human Review before public delivery.
- Publication boundary: Worker persists generation attempts and safety status/issue codes, removes stale `persona_review_*` metadata on rerender, rechecks account contracts plus source-external identifiers at publication, and never changes the persisted content. Greeting, RAG references, attachments, and transport formatting remain application-owned envelopes.
- Historical boundary: published v27 jobs are unchanged; unpublished stale jobs use the existing prompt-version fence; manual-attention Case 13314 is not replayed or modified.

## 2026-09-06 - ECS deploy waits for the requested task revision (p1-53)

- Area or subsystem: ECS release deployment and rollback convergence gate.
- Prompt or model version: No prompt, model, provider, or Prompt Release content change.
- Reason: Immediately after `update-service`, the stock ECS stable waiter can still observe the old `1/1/0` deployment and return before the requested task-definition revision is running.
- Tooling change: Forward rollout and rollback now require the service pointer and its sole completed primary deployment to match the requested task-definition ARN at `1/1/0`. A failed rollout stops immediately; other transitional states poll every five seconds for at most fifteen minutes.
- Verification: Executable shell harnesses prove an old stable revision is rejected and the requested completed revision passes. The focused deploy/contract suite and shell syntax checks are required before finalization.

## 2026-09-06 - CodeBuild release and environment-accurate Preproduction runtime (p1-53)

- Area or subsystem: ECS release tooling, Prompt Release deployment gate, Preproduction Account runtime identity, and Hermes activation configuration.
- Prompt or model version: No prompt text, model, provider, or reasoning-effort change. Prompt Release content fingerprint validation is preserved across the new registry-backed Manifest v2 path.
- Reason: Build each fixed main commit once on native linux/amd64 infrastructure, validate it in an isolated Production-equivalent Preproduction environment, then promote identical digests without rebuilding or copying environment data.
- Behavioral boundary: Preproduction keeps its own path/schema/roles/namespace/Prompt target/secrets/logs/heartbeats while using the Production Account side-effect and Suspension direct-handoff contracts. n8n remains the sole ticket-admission controller. Production Hermes can be explicitly disabled with endpoint/key/callback secrets physically removed; real Hermes activation requires all three environment-specific secrets.
- Verification boundary: Repository tests and Terraform validation prove generation and fail-closed contracts. CodeBuild, AWS infrastructure, Hermes state migration, live health, and business cases require separate post-merge evidence and are not claimed by this entry.

## 2026-09-05 - Persona and Reviewer isolate the current Account intent policy (p2-144)

- Area or subsystem: Account Automation Persona generation and semantic review.
- Prompt or model version: `automation-persona-v26` -> `automation-persona-v27`; `automation-persona-review-v1` -> `automation-persona-review-v2`; model profile unchanged (`automation_persona`, currently `gpt-5.6-luna/low`).
- Reason: ECS Case 13302 successfully produced a RAG answer for the customer's App ID question, but both bounded Persona/Reviewer rounds were rejected with `intent_policy_violation`. The RAG policy was being concatenated with unconditional Submission Confirmation and Missing-information policies, whose status and next-step requirements contradicted the RAG-only source contract. Case 13299 had shown the same first-round issue before passing its rewrite.
- Behavior change: Account prompts now contain only shared first-person/data-integrity constraints plus exactly one current-intent policy. Missing-information and Submission Confirmation policies are mutually exclusive; Enablement submission adds its 24-hour/weekdays requirements to the Submission policy; RAG, Fraud, Suspension, completion, and unmatched intents do not inherit unrelated rules. The generic prompt asks for status, missing information, and next step only when supplied and applicable.
- Reviewer boundary: Reviewer v2 receives only `current_intent_policy`, not the shared prompt or policies for other intents. Generation/review call budgets, structured feedback, second-round Human Review, immutable Persona body, deterministic safety floor, Worker publication, and RAG reference assembly are unchanged.
- Verification: focused Persona policy tests include a sanitized Case 13302 fixture (`what is appid?` plus the observed App ID explanation), six intent families, unmatched and non-Account scope boundaries, current-policy parity between Persona and Reviewer, and generic applicability wording. The planned Persona/Worker/RAG/comment-sync/ECS/version-fence regression completed with 255 passed + 108 subtests.

## 2026-09-05 - ECS release gate owns Hermes schema bootstrap and mock activation (p2-146)

- Area or subsystem: ECS Production release tooling, Account runtime schema preflight, and Hermes Case Workflow activation.
- Prompt or model version: No prompt, model, provider, or reasoning-effort change; Hermes contracts remain v1 and the only runnable producer remains `mock`.
- Reason: PR #1069 added an opt-in PostgreSQL-only workflow, but the sole Production deploy command neither provisioned its tables nor had an approved way to set `HERMES_CASE_WORKFLOW_MODE`. Publishing the image alone would leave the feature disabled, while a manual task-definition edit would bypass immutable-release and rollback gates.
- Tooling and behavior change: Explicit `--bootstrap-account-schema --hermes-case-workflow-mode mock` now renders a one-off API-image bootstrap task using the existing migration SecureString reference, runs it before any Service update, injects mock mode only into API/Worker, and verifies the public release marker. Default deploy behavior stays disabled; mock without bootstrap fails before AWS writes.
- Security and rollback: Migration DSN values never enter argv, logs, Manifest, Promotion Record, checkpoint, or evidence. The additive schema remains after a later Service rollback; old images ignore the new tables. A failed bootstrap updates no Service and its temporary task definition is deregistered. Resume validates the prior bootstrap family/task pair, stops any still-running task, deregisters its revision, clears stale markers, and then retries the idempotent migration.
- Verification: renderer, secret-reference, explicit-mode, schema-table and deploy-order tests pass; shell syntax and the broader ECS/Account regression suite are required before finalization.

## 2026-09-05 - Hermes Case Workflow uses typed mock output before Persona (p2-146)

- Area or subsystem: Production technical Engineer Case investigation, Summary Guardrail, Persona assembly, and Slack human actions.
- Prompt or model version: No model prompt, model, provider, or reasoning-effort change; shared contracts are `HermesTurnRequest v1`, `HermesInvestigationOutput v1`, `human authority v1`, and `CaseKnowledgePromotion v1`, with mock producer contract `v1`.
- Reason: Replace the legacy deterministic opening investigation message with a durable, version-fenced Hermes Case Workflow while keeping real Hermes and AgentRelay outside this task.
- Tooling and behavior change: In explicit `mock` mode, the persisted opening/feedback/reopen turn produces exact text `Investigation result: test` through the same typed output application handler used by the callback receiver. `Summarize` runs before Persona; exact `reason=test` bypasses only the normal Summary Guardrail decision and leaves deterministic customer-reply guardrail, final human approval, Zendesk revision/readback, and delivery idempotency intact. Default mode is `disabled`, and unknown modes fail closed.
- Affected files or config: Hermes workflow/repository contracts, Account intake and Zendesk status sync, Engineer Slack collaboration/delivery, ECS callback/release health, Worker mock producer, and their focused tests. No external Hermes, AgentRelay, Slack, Zendesk, prompt store, or model configuration was changed.
- Expected behavior change: A technical Case can retain one Engineer Case, Slack thread, conversation/session and PostgreSQL investigation ledger across feedback and reopen episodes; stale summary/authority/draft/approve actions cannot publish. The mock summary lacks proof, so the existing deterministic guardrail blocks final approval until valid evidence exists.
- Verification: The consolidated planned and adjacent suite completed with 324 passed, 2 explicitly deselected unrelated Engineer multi-agent baseline failures, and 63 subtests. Disposable PostgreSQL integration completed with 7 passed and no skips. Review also verified expired-lease recovery, atomic close authority, Persona retry after a persisted Summary Guardrail, and explicit sanitization attestation.

## 2026-09-05 - Persona body becomes immutable with bounded semantic review (p2-144)

- Area or subsystem: Account Automation Persona generation, customer-reply validation, and Worker publication.
- Prompt or model version: `automation-persona-v25` -> `automation-persona-v26`; new `automation-persona-review-v1`; model profile unchanged (`automation_persona`, currently `gpt-5.6-luna/low`).
- Reason: ECS Case 13292 received one correct Persona draft containing `we’ll ... within 24 hours`, but the Worker-side ASCII apostrophe detector missed Unicode `’ll` and appended a second 24-hour commitment. The defect exposed a wider ownership problem: deterministic post-processing could change Persona prose after generation.
- Behavior change: Persona now generates every semantic sentence after the application greeting. A separate Reviewer checks required facts, contradictions, duplication, unsupported claims, and the intent policy; a failed first review supplies structured feedback to the same pinned Persona for one complete rewrite. The second failed review, invalid Reviewer payload, or invocation failure goes to Human Review before publication. Worker no longer appends Suspension/Enablement/Missing-information prose or strips model greetings.
- Safety boundary: code retains fail-closed checks for empty/signed output, forbidden or invented values, misleading future state, App ID error overclaim, and affirmative Suspension close/reopen claims. Common apostrophes are normalized only in a temporary validation copy, so `'`, `‘`, `’`, `ʼ`, and `＇` have the same negation semantics while customer text remains unchanged.
- Audit and budget: normal success is one Persona call plus one Reviewer call; one rewrite is at most four logical calls. Reply jobs persist only the passed body and review status/rounds/model/prompt/issue codes; rejected drafts and free-text feedback are not persisted, and `persona_contract_repair` is retired for new renders.
- Verification: focused Account AI, Persona, Worker, intake, ECS, version-fence, scenario, composer, RAG, and engineer-collaboration regression: 464 passed + 120 subtests, including an exact two-generation/two-review call-budget assertion.

## 2026-09-04 - Prompt Release activation skips schema DDL

- Area or subsystem: Prompt Release activation in the ECS Production deploy gate.
- Prompt or model versions: Prompt content, release identity, models, and Persona v25 are unchanged.
- Reason: the Production runtime database identity has the DML permissions required to activate an existing Prompt Release but intentionally does not own the wider ticket schema. The activation CLI ran `repository.initialize()` before its idempotent release update, so a healthy ECS rollout ended with `must be owner of table support_tickets` before activation logic could read the already-active target release.
- Tooling change: `activate` now follows the existing `sync` and `validate` schema boundary and operates only on the pre-provisioned Prompt tables. Missing or incompatible schema state still fails closed through validation or activation DML; no migration credential or broader database permission is introduced.
- Verification: focused CLI regression asserts an already-active release is returned idempotently without calling `initialize()`, alongside the existing Prompt Release and deployment-gate suites.

## 2026-09-03 - Prompt Release sync does not run target schema DDL (p1-53)

- Area or subsystem: Prompt Release source-to-target replication and ECS Production deploy gate.
- Prompt or model versions: Prompt content, release identity, models, and Persona v25 are unchanged.
- Reason: after the source-read-only fix, the authorized ECS deploy reached target initialization with the existing Worker runtime DSN. That identity has the required table-level read/write permissions but intentionally lacks database-level `CREATE SCHEMA`; `target_repository.initialize()` therefore failed before Release replication or ECS rollout.
- Tooling change: `sync` no longer initializes either repository. It requires both schemas to be provisioned by the normal migration/bootstrap owner, reads and validates the source Release, then performs catalog and Release DML on the target. A missing or incompatible target schema still fails closed through the first actual target operation.
- Security boundary: the deploy continues to reuse the current Worker `AUTOMATION_DB_DSN` secret without introducing a migration credential or broader database permission into the release process.
- Verification: focused CLI regression asserts neither source nor target `initialize()` runs, while the existing sync suite proves target catalog synchronization, same-ID identity checks, idempotency, version remap, deferred activation, and target activation.

## 2026-09-03 - Prompt Release sync keeps the source repository read-only (p1-53)

- Area or subsystem: Prompt Release source-to-target replication and ECS Production deploy gate.
- Prompt or model versions: Prompt content, release identity, models, and Persona v25 are unchanged.
- Reason: the first authorized ECS deploy reached `prompt_release sync` with the intended read-only source database account, but the CLI called `initialize()` on every non-validate source command. PostgreSQL therefore attempted `CREATE TABLE IF NOT EXISTS` in the source `supportportal` schema and failed before target synchronization or ECS rollout.
- Tooling change: `sync` now follows the same source initialization boundary as `validate`: it reads and validates the existing source Release without schema writes. The target repository still initializes explicitly before catalog and Release replication, so target bootstrap behavior is unchanged.
- Failure contract: source read or validation failures and target initialization/sync failures remain fail-closed before ECS service updates. No elevated source credential or deployment bypass is introduced.
- Verification: focused CLI regression asserts source `initialize()` is never called while target `initialize()` is called exactly once, alongside the existing idempotency, same-ID content/build identity, target-local version remap, and deferred activation cases.

## 2026-09-03 - Suspension first reply drops the category word; reviewer notify email removed (p2-143)

- Area or subsystem: Suspension handoff Persona contract and reply facts; suspension reviewer assignment notification.
- Prompt or model version: `automation-persona-v24` → `automation-persona-v25`.
- Summary: the suspension first reply must now refer to the request simply as "this request" — the contract adds an explicit rule ("do not name the account suspension category in the reply") and `closing_reply_facts.performed_actions` was neutralized to "Submitted the request for internal review." so the model no longer echoes the category from facts (AC-13258 rendered "this account suspension request"). Companion behavior change in the same task: the p2-141 suspension reviewer notification email (sent after reviewer assignment via `_notify_suspension_reviewer_by_email`, with `reviewer_notify_email` workflow state and `zendesk_reviewer_notify_email` events) was removed entirely — the internal handoff email already reaches the same recipients (to=suhrid, cc=xieziling) minutes earlier and assignment structurally never happens without a sent handoff email, so the extra copy was always redundant (AC-13258 sent three emails to xieziling: classification notice + handoff + reviewer copy). Existing `reviewer_notify_email` fields on historical cases are left in place (no longer written); the S1 live scenario no longer waits for the notify event.
- Reason: user decisions 2026-09-03 after AC-13258 — the reply should say "Thank you for submitting this request." without naming the category, and the third email (reviewer copy) was redundant noise.
- Affected files or config: `backend/services/automation_persona.py` (contract wording, v25), `backend/services/account_suspension_automation.py` (`closing_reply_facts`), `backend/worker.py` (notify function/call/import removed), `backend/services/automation_test_scenarios.py` (S1), tests (`test_automation_persona.py`, `test_worker.py`, `test_automation_test_scenarios.py`).
- Expected behavior change: new production suspension first replies contain no "suspension"/category wording while keeping the three points (thanks / internal review / we-24h); after assignment no extra email is sent and no `zendesk_reviewer_notify_email` events are recorded — owners receive exactly two emails (classification notice + handoff). Assignment, pending (no solve), and the rest of the one-shot chain are unchanged.
- Verification: persona/worker/intake/full_reroute/reroute_dispatch/scenarios suites green (worker 120, persona 63, scenarios 20, plus 226 across intake/reroute); new negative assertion (rendered content contains no "suspension"), notify cases rewritten to assert no send/no state/no events; official-stack restart and v25 marker to follow merge.

## 2026-09-03 - Suspension first reply simplified to brief three-point wording (p2-142)

- Area or subsystem: Suspension handoff Persona contract (first customer reply of the p2-140 one-shot flow), closing reply facts, deterministic repair sentence.
- Prompt or model version: `automation-persona-v23` → `automation-persona-v24`.
- Summary: the suspension handoff contract now asks for three brief points in the customer's own words — thank the customer for submitting the request, state that it is being reviewed internally, and commit that we will get back to them within 24 hours — with an explicit "keep the reply brief (two or three short natural sentences)" instruction and a short style reference. The "handed to the relevant team / someone from that team will contact you" requirement and its long style example are gone; the reviewer handoff still happens operationally. `closing_reply_facts` wording moved to the same we-perspective ("Submitted the suspension request for internal review." / "We will get back to the customer within 24 hours."), and the deterministic repair sentence became "We will get back to you within 24 hours." The runtime safety floor is unchanged (no gate was added or tightened): the brief reply passes as-is, the closure-claim ban, forbidden values, signature checks, and the repair mechanism all behave exactly as in v23. This also removes the old tension between the relevant-team phrasing and the first-person ownership rule.
- Reason: user decision 2026-09-03 — the expected first reply is "Thank you for submitting this request. We are reviewing it internally and will get back to you within 24 hours."; gates must stay loose, the key points just need to be expressed.
- Affected files or config: `backend/services/automation_persona.py` (contract text, `_SUSPENSION_HANDOFF_CONTRACT_SENTENCE`, version), `backend/services/account_suspension_automation.py` (`closing_reply_facts` wording), tests (`test_automation_persona.py`, `test_worker.py`, `test_account_intake.py` fake render).
- Expected behavior change: new production suspension tickets receive a brief three-point first reply (thanks / internal review / we-24h) instead of the longer received-and-handed-over wording; natural variants still pass on the first attempt; missing 24-hour wording is still repaired deterministically; the rest of the one-shot chain (email → job → assign reviewer → pending, no close) is untouched.
- Verification: persona/worker suites 185 passed + 74 subtests (new case asserts the brief reply passes without repair and the new contract points are in the system prompt); intake/full_reroute/reroute_dispatch 226 passed + 15 subtests; official-stack restart and v24 marker check to follow merge.

## 2026-09-03 - Suspension one-shot direct handoff, natural commitment validator, greeting comma (p2-140)

- Area or subsystem: Account suspension production intake (Main `/account`, `processing_profile=production`), suspension closing Persona contract, greeting rendering.
- Prompt or model version: `automation-persona-v21` → `automation-persona-v22`.
- Summary: production suspension intake skips the contact-confirmation stage entirely (direct handoff: strict email gate → internal handoff email with the ticket email as contact address → single closing reply job after the email succeeds → reviewer assignment after publication; later customer replies are a no-op). The suspension closing validator was rewritten for natural phrasing: the 24-hour commitment facts (modal/contact-action/time-window) may now span sentences instead of sharing one clause, with a narrow negative guard (a negated or questioning clause pairing commitment facts still fails) plus a new closure-claim guard (a positive clause promising close/reopen/archive on the ticket fails; negated sentences like "you don't need to reopen" pass). A deterministic repair appends the standard commitment sentence when the model only missed it (recorded in the job payload as `persona_contract_repair`; negated/questioning promises and closure claims are never repaired). The handoff contract now asks the model to acknowledge receipt of the request (prompt-level only, not runtime-validated). The configured greeting restores the comma (`Hi {name},`), reverting the p2-126 comma-drop decision. Legacy two-stage paths (staging intake, ECS intake, awaiting-contact tickets, their rerun/reroute branches, contact contract and validator) are kept unchanged.
- Reason: user decisions 2026-09-03 — no more asking the customer for an email on suspension tickets, no closing, first reply should say the request was received with the 24-hour contact promise, then internal email plus assignment to Suhrid; contract validation should only require the key points so natural wording no longer fails into human review; restore "Hi X," greeting.
- Affected files or config: `backend/main.py` (email gate, direct-handoff builder, pre-email job exclusion, post-email facts/intent alignment, durable workflow transitions, rerun marker branch), `backend/services/account_suspension_automation.py` (new `direct_handoff_workflow`), `backend/services/account_full_reroute.py` (direct-handoff reroute), `backend/services/automation_persona.py` (validator rewrite, closure guard, deterministic repair, greeting comma, contract tweak, v22), `backend/worker.py` (repair signal in payload), plus tests and docs (regression runbook, ECS status report, feature list).
- Expected behavior change: new production suspension tickets complete in one shot and fail closed (human review, no customer output) on missing/invalid ticket email, internal-email failure/unknown outcome, or reply-job creation failure. Natural 24-hour commitment variants pass validation on the first attempt; genuine negations/questions, missing commitments, and customer-facing close/reopen claims still fail. All persona-rendered replies greet as `Hi {name},`. Staging/ECS entries and legacy awaiting tickets keep the two-stage behavior.
- Verification: new validator contract cases (natural variants pass, negation/question/missing-window/closure claims fail, deterministic repair with 1 call vs 4-retry refusal), production direct one-shot end-to-end (email-before-job ordering, single aligned handoff job, workflow states, no-op follow-up), email gate boundaries (blank/case/`a@b`/valid), email-failure fail-closed, direct rerun/reroute (including no-email fail-closed); suites green: persona 61, worker 120, intake 177, full_reroute 15, reroute_dispatch 34, slack/scenarios/ECS-intake 49, composer/version-fence/route/verification 165.

## 2026-09-03 - Production reply validation lowered to a safety floor (p2-141)

- Area or subsystem: Account Automation Persona publication contracts for all major intents; greeting name projection; persona pass-through; suspension reviewer notification.
- Prompt or model version: `automation-persona-v22` → `automation-persona-v23` (supersedes the same-day v22 from p2-140's suspension-scoped relaxation; owner picked the broader safety-floor boundary afterwards). Model configuration unchanged.
- Summary: all wording-level blocking regexes are removed from `validate_account_reply_contract`: the fraud/suspension handoff triple (will + contact verb + "24 hours" in one clause) and the p2-140 cross-sentence commitment requirement, the suspension contact interrogative form, the enablement submission 24h/change-window clauses, the enablement completion positive enabled/closing/media-relay requirements, the missing-information layout and label-completeness contract, the ownership clause family, and the appid-invalid/not-found sentence requirements. Business points stay as prompt guidance ("must express … in your own words"). The retained blocking safety floor: contract normalization and intent/close derivation, empty response, signature/greeting strip, generation-time forbidden values and engineer source-value bounds, misleading future enable/archive claims, appid-error overclaim bans, the missing-information invented-timeframe ban, and the suspension affirmative close/archive/reopen claim ban (subject-bound, negation-aware). The deterministic assemblies stay: missing-information, enablement-submission, and the p2-140 suspension closing sentence append (a merely missing or negated commitment is repaired by appending; a closure claim still fails).
- Reason: user decision after Case 13225 — the wording-level contracts forced the model into repeated regeneration to satisfy regex shapes (the root of stiff/unnatural replies), and the final check should only require the points to be expressed, not exact sentence shapes.
- Companion behavior changes in the same task: greeting names now resolve message-first via `resolve_customer_greeting_name` (latest customer-comment author → case name → requester → "Customer", per-candidate validity) on both stacks; author name/kind persist into message meta; the ECS worker passes the route-pinned `payload.persona` verbatim into all four reply-job exits (no re-resolve); suspension closing assigns the reviewer and then sends a reviewer notification email (Graph) with a persisted `reviewer_notify_email` state and a `zendesk_reviewer_notify_email` event; route_preparation's suspension first draft drops its close/reopen sentence; live-scenario acceptance checks (E1/E2/F1/S1) are decoupled from the production validator and rebuilt for the p2-140 one-shot direct-handoff suspension flow (S1 no longer asserts `solved`, adds assign + notify-email assertions).
- Affected files or config: `backend/services/automation_persona.py`, `backend/services/automation_account_reply_sync.py`, `backend/main.py`, `backend/worker.py`, `backend/automation_ecs_worker.py`, `backend/services/automation_engineer_collab.py`, `backend/services/route_preparation.py`, `backend/services/automation_test_scenarios.py`, plus tests.
- Expected behavior change: natural-language replies pass generation validation without retries; a reply that omits a business point (e.g. the 24-hour promise) is no longer auto-rejected (the suspension closing append still restores the commitment sentence deterministically); only safety-floor violations fail closed. Suspension replies never claim closing/archiving/reopening affirmatively.
- Known trade-off (owner-accepted): point-omission risk moves from validated to prompt-trust and must be observed via controlled tickets (E1/E2/F1/S1 content checks cover it in live scenarios).
- Verification: pending — targeted suites in p2-141.

## 2026-09-02 - Suspension terminal action moves to reviewer handoff without closing (p2-138)

- Area or subsystem: Account suspension contact workflow terminal reply, review handoff, and Persona wording contracts.
- Prompt or model version: `automation-persona-v20` → `automation-persona-v21`.
- Summary: the suspension closing intent (`account_suspension_handoff_and_close`, legacy name kept for persisted jobs) no longer derives `close_after_publish`: the closing reply publishes without solving the ticket, and the shared post-delivery handoff assigns the ticket to the reviewer (env `ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID`) exactly like the fraud confirmation, marking the case `human_review_required` and closing the contact workflow. Wording contracts for both the first contact ask and the closing reply dropped the mandatory close/reopen statements; the closing reply now only requires the 24-hour contact commitment, and ownership facts moved from `case_closed` to the fraud-style `internal_handoff_sent` family.
- Reason: user decision — suspension cases should end with the ticket assigned to the reviewer (Suhrid) for human follow-up instead of being auto-solved, matching the fraud terminal flow and its "relevant team will reach out within 24 hours" message.
- Affected files or config: `backend/services/account_reply_jobs.py`, `backend/services/automation_account_reply_sync.py`, `backend/services/automation_account_intake.py`, `backend/main.py`, `backend/worker.py`, `backend/services/automation_persona.py`, `backend/services/account_suspension_automation.py`, plus tests.
- Expected behavior change: both `/production` and `/automation/production` (shared code path) now deliver the suspension closing reply, keep the ticket open, assign it to the reviewer, and set the case to human review; fraud handoff behavior is unchanged. Neither suspension stage announces closing/reopening any more.
- Verification: intake full-chain case updated to assert no-close publication, no close/reopen wording, and open ticket; new worker handoff case covers suspension public delivery → reviewer assignment with workflow closure; version-fence, reroute, dispatch, ECS-worker shell, persona, and slack suites green (510+ tests; the one remaining failure is the pre-existing p2-123 baseline case, failing on root main as well).

## 2026-09-02 - Suspension contact confirmation confirms on any non-empty reply (p2-136)

- Area or subsystem: Account suspension contact-confirmation workflow (first-stage gate before closing reply + handoff).
- Prompt or model version: prompt text and model configuration unchanged; deterministic confirmation gate behavior relaxed.
- Summary: `suspension_contact_confirmation` no longer requires the customer reply to carry exactly one address or an affirmative phrase — any non-empty reply confirms. The contact address prefers an address from the reply that differs from the ticket email, then the first address in the reply, then the ticket email. The four fail-closed branches (multiple_contact_emails, conflicting_email_confirmation, different_email_required, ambiguous_contact_confirmation) are removed together with the affirmative/negative regexes.
- Reason: production Case AC-13225 — the customer clearly designated a contact address ("account email is business@kira.art, contact me with owen@kira.art") yet the multi-address rule forced human review; the user decided the gate should trust any customer reply.
- Affected files or config: `backend/services/account_suspension_automation.py`, `backend/tests/test_account_verification_automation.py`.
- Expected behavior change: suspension cases confirm and proceed to closing reply + handoff + close on any customer reply (empty messages still wait; non-awaiting states still ignored). Replies that previously escalated (multi-address, ambiguous, negative phrasing) now confirm, with the contact address derived by preference order.
- Verification: confirmation unit suite rewritten for the new semantics (18 passed); worker/intake/reroute/full-reroute/ecs-intake/slack/route-contract regression 376 passed + 33 subtests.

## 2026-09-02 - Archer Enablement success reply drops region/load disclosure (p2-134)

- Area or subsystem: Account Automation Persona rendering for Archer-backed Media Relay Enablement.
- Prompt version: `automation-persona-v19` remains unchanged.
- Summary: The `enablement_archer_enabled` reply facts no longer inject `region`/`max_subscribe_load`, the intent policy now requires only that Media Relay is already enabled (with an explicit instruction not to mention regions, subscribe load, capacity numbers, or internal configuration details), and the deterministic contract validator no longer rejects replies that omit the oversea region or the load-50 statement. Feature mention, completed tense, and closure semantics are still enforced; recoverable-error intents and the failure path are unchanged.
- Reason: Ticket 13218 acceptance feedback - customers should be told Media Relay is enabled, not the internal region/capacity configuration.
- Version-fence decision: the previously published 13218 job is terminal and never re-rendered; no queued jobs exist for this intent, so the global Persona version fence stays unchanged (same decision as the 2026-09-01 p2-134 entry).
- Privacy boundary: raw App IDs remain forbidden values and are excluded from Persona source facts and prompts.

## 2026-09-02 - Persona reply naturalization: style-reference prompts and targeted contract relaxation (p2-135)

- Area or subsystem: Account Automation Persona rendering prompts and reply publication contracts for all major intents.
- Prompt or model version: `automation-persona-v19` -> `automation-persona-v20`; `engineer-guided-persona-v2` -> `engineer-guided-persona-v3` (shared base style section changed). Model configuration unchanged.
- Summary: the Persona system prompt now mandates first-person ownership ("speak in first person (I/we)", never present an internal team as the party contacting the customer) and natural sentence rhythm, and each intent policy carries a required-facts + style-reference example labeled "match the tone and rhythm, do not copy the wording" (seeded by the owner-approved rewrite of production Case 13200's reply). Publication contracts relaxed in step: the Fraud/Suspension handoff commitment now accepts any natural sentence pairing a contact action with the exact 24-hour promise instead of the single verbatim sentence; the enablement completion contract no longer enforces a canned acknowledgement pattern or the new-ticket guidance regex; its future-tense ban narrowed to targeted misleading forms (`will be enabled/archived` without an immediacy marker in the same clause) and the closing clause now also accepts customer vocabulary ("closing this case") alongside "archiving".
- Reason: production Case 13200's completed-Enablement reply read as a canned checklist ("Thank you for your patience. ... We'll archive this case now. If you have any further questions or need additional help, please open a new ticket."). Root cause was double: checklist-style prompt policies ("State that X. Say that Y.") and wording-level validation regexes that punish any natural phrasing into human review, so the model converged on the safest template. Owner confirmed the target style and the exact keep/relax boundary: safety floors (forbidden values, guided-source, overclaim), the missing-information layout contract (≤2 inline / ≥3 bullets / each label exactly once), and the ownership contract all stay; style-level checks go.
- Affected files or config: `backend/services/automation_persona.py`, `backend/tests/test_automation_persona.py`, `backend/tests/test_worker.py`.
- Expected behavior change: completed/Archer-enablement, fraud/suspension handoff, and enablement submission replies render in a warm first-person voice with varied sentence structure (e.g. "Thanks for waiting on this one — I'm happy to confirm ... I'm closing this case now, but if any questions come up later, feel free to open a new ticket ..."), while natural paraphrases of the 24-hour contact commitment pass validation. Missing-information asks still reject any SLA/duration promise and keep the deterministic inline/bullet layout; appid-error overclaim bans, forbidden identifier values, guided-source bounds, ownership delegation bans, and the Archer three-fact requirements (Media Relay / oversea / load 50) are unchanged and still fail closed to human review.
- Verification: focused persona suite `53 passed, 42 subtests passed`; worker suite alone `119 passed, 17 subtests passed`; combined intake/comment-sync/version-fence/archer regression `412 passed, 74 subtests passed` with one pre-existing order-dependent failure (`test_non_ecs_worker_keeps_legacy_rag_service_executor`) reproduced identically on clean main and passing when run in isolation.

## 2026-09-01 - Enablement Archer outcome policies without version-fence advance (p2-134)

- Area or subsystem: Account Automation Persona rendering for Archer-backed Media Relay Enablement.
- Prompt version: `automation-persona-v19` remains unchanged.
- Summary: Added intent-specific policies and validators for `enablement_archer_enabled`, `enablement_appid_invalid`, and `enablement_appid_not_found`. Success must state current Media Relay enablement, oversea region, maximum subscribe load 50, immediate archival, and new-ticket guidance. Recoverable errors request a corrected App ID and forbid enablement, handoff, SLA, or closure claims.
- Version-fence decision: these intents have no historical jobs or payloads, so existing v19 jobs do not require re-rendering and the global Persona version fence is intentionally unchanged.
- Privacy boundary: raw App IDs remain forbidden values and are excluded from Persona source facts and prompts.

This file is the canonical log for every prompt-related or model-related change in this repository.

For each new entry, record:
- Date
- Area or subsystem
- Prompt or model version
- Summary
- Reason
- Affected files or config
- Expected behavior change
- Verification

## 2026-09-01 - ECS Fraud shared builder uses Account Extractor profile (p2-110, correction to p2-114)

- Area or subsystem: ECS Account Automation Fraud field extraction on customer comment replies.
- Prompt or model version: prompt content unchanged; the shared `build_account_verification_automation_result` default scenario changes from `intent_router` to the existing `account_extractor` profile (`gpt-5.6-luna`, low reasoning, 30-second default timeout).
- Summary: the shared Fraud automation builder now preserves the field extractor's approved Account Extractor model contract instead of overriding it with the client intent-router profile. Explicit callers may still pass a different scenario; old EC2 `/production` keeps its explicit `account_route` scenario unchanged.
- Reason: Case 13190 supplied a valid requested field, but ECS used the stale shared-builder override (`gpt-5.4-mini`, low, 8-second runtime profile) left behind when p2-114 migrated the underlying extractor. Extraction became uncertain and exposed the separate missing-reconciliation defect.
- Affected files or config: `backend/services/account_verification_automation.py`, focused Account verification and ECS comment-sync tests. No prompt text, Prompt Release, environment value, or credential changes.
- Expected behavior change: ECS Fraud replies use the approved Account Extractor quality/timeout profile; valid partial field replies remain in the one-follow-up handoff path. Extraction failures still fail closed through Human Review rather than being treated as successful extraction.
- Verification: related Account/ECS/Persona/RAG/model-profile regression passed (`430 passed`, `91 subtests passed`), including direct default-scenario capture and deterministic extraction of `office_address=Shanghai` from the 13190-shaped reply.

## 2026-09-01 - Enablement submission fixed contract deterministic assembly (p2-131)

- Area or subsystem: Account Automation Persona rendering for Enablement `submission_confirmation` replies.
- Prompt or model version: `automation-persona-v18` -> `automation-persona-v19`; prompt text and model configuration unchanged, application-owned reply assembly changed.
- Summary: the application now appends any missing positive `up to 24 hours` SLA and `Monday-Friday` change-window clauses before the existing final Account reply validation. Already-valid model clauses are preserved without duplication. A negative or question-form contract mention is rejected before completion, so deterministic text cannot hide contradictory model output.
- Reason: production Case 13176 collected the App ID and sent the real Enablement internal email, but all four Persona candidates failed the fixed SLA/change-window contract. The submission confirmation job moved to manual attention and the Case entered Human Review without a public customer reply. Fixed business facts already present in `reply_facts` must not depend on stochastic model wording.
- Affected files or config: `backend/services/automation_persona.py`, `backend/tests/test_automation_persona.py`, `backend/tests/test_worker.py`.
- Expected behavior change: an Enablement submission candidate that supplies valid acknowledgement and first-person ownership language but omits one or both fixed contract facts succeeds after one model call with the missing clauses assembled by the application. Contradictory/question-form contract language, ownership violations, signatures, forbidden identifiers, and all other publication fences remain fail closed. Other reply intents are unchanged.
- Verification: focused Enablement submission contract tests passed (`6 passed`, including 5 subtests); Persona/Account AI/version-fence/Worker regression passed (`185 passed`, 50 subtests); Enablement intake, field extraction, completion classifier, comment sync, Account intake, and ECS Worker regression passed (`276 passed`, 52 subtests).

## 2026-08-31 - Payment-card gate no longer misfires on E.164 phone numbers (p2-127)

- Area or subsystem: Account verification / fraud_account field extraction sensitive-payment pre-check.
- Prompt or model version: prompt text and model configuration unchanged; extractor tooling behavior (`fraud-account-fields-v4` chain) narrowed for phone-shaped number runs.
- Summary: the card-candidate regex lookbehind changed from `(?<!\d)` to `(?<![\d+])`, so an E.164 phone number such as "+86 15112080608" no longer enters Luhn validation as a 13-digit card candidate. Detection and redaction share the regex, so contact numbers also stop being redacted to [REDACTED PAYMENT CARD] before the model prompt.
- Reason: production Case AC-13157 — the customer supplied the requested seven fields including "Official contact number +86 15112080608"; the digit run 8615112080608 happened to pass Luhn, tripped the sensitive gate before the LLM call, forced human review, and blocked the fraud handoff (customer reply + reviewer assignment) entirely.
- Affected files or config: `backend/services/account_verification_field_extractor.py`, `backend/tests/test_account_verification_automation.py`.
- Expected behavior change: fraud replies containing phone numbers in `+<country-code>` form proceed to normal field extraction and the fraud handoff loop (reply to customer, assign reviewer, human-review status without closing); genuine card numbers, CVV, credentials, and bank accounts still fail closed; un-prefixed 13-digit runs remain card candidates.
- Verification: new unit case asserts the AC-13157 input reaches the model with a non-sensitive outcome and that real card numbers still return payment_card; the existing fails-closed case and the FraudReviewHandoff suite pass unchanged (183 tests green across worker/intake/fence/persona).

## 2026-08-28 - Persona greeting format and RAG fallback greeting name lookup (p2-126)

- Area or subsystem: Automation Persona greeting rendering and Account reply RAG fallback reply facts.
- Prompt or model version: `automation-persona-v17` → `automation-persona-v18`.
- Summary: the deterministic Persona greeting changed from `Hi, {first_name}` to `Hi {first_name}` (no comma) across all automation replies, and the `rag_fallback_answer` reply facts now resolve the greeting name via account case `customer_name` → Zendesk comment `author_name` hint → empty (persona "Customer" fallback). The split-stack RAG fallback branch also now builds full reply facts (previously it passed a nonexistent `draft_content` argument and always failed into `account_reply_job_creation_failed`).
- Reason: production Case AC-13099's RAG fallback reply greeted "Hi, Customer" because the API path filled `customer_first_name` from the ticket requester (always an email address, rejected downstream) and the split-stack path never passed a name source; the user also requested the comma-free greeting format.
- Affected files or config: `backend/services/automation_persona.py`, `backend/main.py`, `backend/services/automation_account_reply_sync.py`.
- Expected behavior change: automation replies greet "Hi Ziling" (no comma); RAG fallback replies use the customer's real name whenever the account case or the triggering Zendesk comment provides one; the split-stack RAG answer branch creates persona-pipeline jobs instead of failing.
- Verification: persona/worker suites updated to v18 (162 passed); comment-sync suite covers the name lookup chain (case name wins over comment hint, hint fills when case name is absent, 20 passed); intake suite asserts the RAG facts carry the case name (239 passed across intake/fence/enablement/reroute).

## 2026-08-28 - Enablement feature alias canonicalization and Persona forbidden-value deadlock fix (p2-123)

- Area or subsystem: Enablement Account Automation feature normalization, internal-email feature label, Persona facts projection, and the completion-reply forbidden-value gate.
- Prompt or model version: prompt text and model configuration unchanged; `automation-persona-v17` behavior boundary widened only for feature wording.
- Summary: the customer wording "cross platform streaming" (confirmed to mean Media Relay) now canonicalizes to `media_relay`, so the Enablement display name resolves to "Media Relay" in the customer-visible facts projection and the internal handoff email subject/Feature field. The forbidden-value gate keeps banning the raw feature label only when a canonical display name exists to replace it; without one the customer's own wording is allowed. Enablement completion notes are redacted against the forbidden values before entering `source_facts`, and the Enablement facts projection now strips `ticket_id`, `account_case_id`, and `customer_email` for every intent, matching the non-Enablement projection.
- Reason: production Case AC-13085 failed `automation_persona_forbidden_value` on all four attempts and escalated to Human Review. The field-extraction model stored the customer wording as `requested_feature`, no canonical display name resolved, the raw wording became a forbidden value while the projection removed every legal feature name, and the completion contract forced the Persona to name the enabled feature - a deterministic deadlock.
- Affected files or config: `backend/services/enablement_automation.py`, `backend/services/automation_persona.py`, `backend/worker.py`.
- Expected behavior change: Media Relay enablement requests phrased as cross platform streaming route, display, and reply as Media Relay; unknown feature wordings no longer deadlock the completion reply (the Persona may refer generically or reuse the customer wording); identifier redaction for App IDs, emails, and ticket references is unchanged and now also applied to completion notes before the Persona sees them.
- Verification: AC-13085 replay unit tests cover canonical alias normalization, identifier-free projections, note sanitization, a Persona reply using "Media Relay" passing the completion contract, the raw wording still rejected when a canonical name exists, and customer wording allowed when it does not; enablement/persona/worker/intake/reroute suites passed 578 tests with two pre-existing baseline failures on `main` unrelated to this change.

## 2026-08-26 - Engineer customer-comment AI trigger boundary (p2-68, p2-113)

- Area or subsystem: Production Non automated Engineer Case Zendesk comment sync and Slack collaboration.
- Prompt or model version: prompt text and model configuration unchanged.
- Summary: customer comment sync no longer invokes Engineer AI or creates an automatic Draft. It records the customer message in the active investigation, invalidates stale Draft/Guardrail/final approval state, and emits only the content-free Slack notification `Cx has added a new comment`. The next valid Slack `@bot` guidance remains the explicit AI trigger and reads the updated Case context.
- Reason: Slack users must control when a new customer-facing Draft is generated; customer comments should notify the assigned thread without causing an unsolicited model turn or exposing customer content in Slack outbox payloads.
- Affected files or config: `backend/main.py`, `backend/services/automation_account_reply_sync.py`, `backend/services/investigation_flow.py`, and the Zendesk comment-sync n8n contract.
- Expected behavior change: Automated Account Cases retain their existing comment-trigger behavior. Active Non-automated Engineer Cases return `processed_engineer_notification`, queue one notification-only event, and wait for a later Slack mention before generating a Draft.
- Verification: targeted `/production` and `/automation/production` comment-sync tests assert context persistence, stale-state invalidation, version fencing, no Engineer AI call, one fixed Slack event, and no customer content in its payload.

## 2026-08-26 - Slack Engineer-guided Persona replies v1 (p2-68)

- Area or subsystem: Production Non automated Engineer Case Slack collaboration.
- Prompt or model version: new scoped `engineer-guided-persona-v1`; shared `automation-persona-v14` and its model profile are unchanged.
- Summary: added the `engineer_guided_reply` intent. The Persona preserves the human's Slack guidance while polishing tone and organization. The guidance is the only authority for customer-facing technical claims, instructions, versions, URLs, steps, and commitments; bounded public Case context may only determine language, greeting, reference resolution, and contradiction avoidance.
- Safety behavior: App IDs, email addresses, support IDs, and URLs may appear only when the exact value exists in the persisted Slack guidance. Newly invented values, signed output, empty output, unavailable Persona, and model failures remain fail closed and do not create a publish action.
- Approval behavior: the action handler verifies that the source message exists in the active investigation and that its persisted Slack event ID matches before the human source can replace investigation proof. The existing investigation proof contract is unchanged for every other source mode.
- Verification: integrated Persona, Guardrail, Slack API/workflow, Zendesk comment-sync, and worker suite passed 312 tests and 37 subtests; live evidence is recorded in task `p2-68` after deployment.

## 2026-08-25 - Fraud missing-information deterministic rendering v14

- Area or subsystem: `/production` Fraud Account missing-information customer reply and shared Automation Persona renderer
- Prompt or model version: `automation-persona-v13` -> `automation-persona-v14`; model configuration unchanged
- Summary: Fraud Account and its `account_verification` compatibility alias now ask the Persona model only for a warm acknowledgement paragraph. The model input carries the missing-field count but omits the field names; the application deterministically appends the 1-2 inline request or 3+ Markdown bullet list and the first-person ownership sentence before the existing publication contract runs.
- Reason: Production Case AC-13000 loaded the correct seven-field extractor Prompt and identified exactly three missing fields, but all four Persona candidates failed the exact-label bullet contract and the Case entered Human Review without a customer reply. Exact field labels and layout are application-owned data and must not depend on stochastic model formatting.
- Affected files or config:
  - `backend/services/automation_persona.py`
  - `backend/tests/test_automation_persona.py`
  - `backend/tests/test_worker.py`
  - `docs/project/tasks/p2-101.json`
- Expected behavior change:
  - Fraud replies with one or two missing fields place every expected label in one application-rendered sentence; three or more fields use one application-rendered `- ` bullet per label in source order.
  - The Persona preserves its warm voice in the acknowledgement but cannot omit, rename, merge, reorder, or renumber the required fields because their names are absent from its prompt.
  - The final assembled reply still passes the existing missing-information, ownership, grounding, signature, and publication fences. Other Account intents and behaviors keep their existing Persona rendering.
  - Rejected legacy or malformed layouts retain the stable failure code and now include a content-free structural detail such as `numbered_list_detected` or `missing_expected_label`; failed customer-facing candidates are not persisted.
- Verification:
  - Persona and Account AI tests cover the AC-13000 field set, Fraud/account_verification aliases, 1/2/3+ thresholds, hidden prompt labels, invalid preamble retries, v14 metadata, and sanitized failure details.
  - Worker and version-fence tests cover deterministic content persistence, `persona_v8_scheduled`, current-version publication, and v13-to-v14 re-render behavior.

## 2026-08-25 - Production API Prompt runtime service label

- Area or subsystem: `/production` API startup and deployment Prompt runtime verification
- Prompt or model version: Prompt content, release ids, and model configuration unchanged
- Summary: API startup now uses `PROMPT_RUNTIME_SERVICE` for the `prompt_runtime_loaded` service label, with the existing `api` default preserved. This aligns `api_production` logs with its configured `api-production` identity and the eight-runtime deployment gate.
- Reason: EC2 build `beb07bc9ad71` loaded candidate `pr-192a51684adc` successfully in both databases and served healthy `/health` and `/production/`, but the gate searched for `service=api-production` while `backend.main` hard-coded `service=api`; verification timed out after 90 seconds and correctly rolled back to build `48ca775d09ad`.
- Affected files or config:
  - `backend/main.py`
  - `backend/tests/test_startup_repository_fallbacks.py`
- Expected behavior change:
  - The main API continues logging `service=api` by default.
  - The production API logs `service=api-production`, so deployment can distinguish and verify its loaded Prompt Release without weakening any image, build, release, health, or restart gate.
- Verification:
  - Startup contract covers the configured production service label in schema-check mode.
  - Prompt runtime, deployment-gate, and Compose service-label tests pass; Python compilation and `git diff --check` pass.
  - EC2 deployment completed on build `76d22d5ae1a3`: all eight Prompt runtimes loaded active release `pr-c9b3a291ecf1`, production workers remained stable with `RestartCount=0`, and main/production database validation returned `loaded`.

## 2026-08-25 - Prompt Release target-local version remapping

- Area or subsystem: managed Prompt Release cross-database sync and `/production` deployment
- Prompt or model version: Prompt content, release ids, and model configuration unchanged
- Summary: Cross-database sync now treats `content_sha256` as the portable Prompt identity and maps each source release item to the matching target-local version. When the same numeric version contains different target history, sync preserves that history and allocates the next target-local version; when the same content already exists under another local version, sync reuses it without duplication.
- Reason: The staging and production databases maintain independent version sequences. Production deployment failed before stopping the old stack because `account-account-billing-router-system v1` had different legitimate content in each database and the prior sync incorrectly treated equal version numbers as a cross-database identity contract.
- Affected files or config:
  - `backend/repositories/ticket_repository.py`
  - `backend/scripts/prompt_release.py`
  - in-memory and PostgreSQL Prompt Release sync tests
- Expected behavior change:
  - Candidate sync preserves the target active release and stores remapped versions as drafts until explicit activation.
  - Activation resolves the same release content in both databases even when their local version integers differ.
  - Existing target history is never overwritten; exact content matches remain idempotent; tampered payload hashes still fail closed before deployment cutover.
- Verification:
  - `backend.tests.test_prompt_versioning` and `backend.tests.test_deploy_ec2`: 63 passed.
  - Python compilation and `git diff --check` passed.
  - PostgreSQL collision coverage is implemented with an isolated random schema and must run near the database on EC2 before production deployment.

## 2026-08-25 - Fraud Account v4 deployment validation gate

- Area or subsystem: managed Prompt Release validation and `/production` deployment
- Prompt or model version: `fraud-account-fields-v4`; Prompt text and model configuration unchanged
- Summary: Added a fail-closed deployment gate that parses the selected Fraud Account Prompt `## Output` JSON, requires exactly the seven canonical fields, rejects the four legacy fields, and requires the selected content SHA-256 to match the current code Prompt.
- Reason: Stored Prompt Release hashes prove database integrity but did not prevent a structurally valid, internally hashed stale Prompt from being deployed. The gate must fail before the healthy old stack is stopped.
- Affected files or config:
  - `backend/services/prompt_versioning.py`
  - `deployment/deploy_ec2.sh`
  - Prompt versioning and deployment contract tests
- Expected behavior change:
  - Candidate validation and production Prompt sync reject missing, legacy, structurally drifted, or code-divergent Fraud Account Prompt content.
  - Deployments with a valid current v4 Prompt continue unchanged; no customer Case is processed by the gate.
- Verification:
  - Prompt versioning tests cover current v4 success, malformed field structure, legacy fields, and content-hash divergence from code.
  - Deployment tests cover pre-stop validation, eight-runtime provenance, worker restart stability, and post-activation production readback.

## 2026-08-24 - Fraud Account field extractor schema contract v4

- Area or subsystem: `/account` Fraud Account field extraction managed Prompt
- Prompt or model version: `fraud-account-fields-v4`; model configuration unchanged
- Summary: Aligned the managed extractor Prompt output example with the seven canonical required fields used by the parser and removed the legacy four-field/contact-information terminology.
- Reason: The parser accepts only `account_type`, `name`, `office_address`, `contact_number`, `contact_email`, `use_case_description`, and `console_configuration`, while the prior Prompt example still instructed the model to emit legacy keys that were discarded as missing evidence.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/account_verification_field_extractor.py` (canonical contract reference)
  - `backend/services/account_route_pipeline.py` (managed catalog reference)
  - `backend/tests/test_account_verification_automation.py`
  - `backend/tests/test_agent_config.py`
- Expected behavior change:
  - The managed Prompt emits exactly the seven canonical field keys and catalog version `fraud-account-fields-v4`.
  - Existing grounding, optional-field, and sensitive-payment fail-closed behavior remains unchanged.
- Verification:
  - Structural Prompt contract test parses the `## Output` JSON and rejects missing or legacy keys.
  - Targeted Account Verification, Agent Config, Intake, Persona, and Account AI regression suites: 227 passed, 24 subtests passed; 4 existing FastAPI deprecation warnings.

## 2026-08-24 - Fraud Account missing-information Persona format v12

- Area or subsystem: `/account` Fraud Account customer reply and shared Automation Persona renderer
- Prompt or model version: `automation-persona-v12`; model configuration unchanged
- Summary: Replaced the 3+ missing-information numbered-list instruction with a Markdown-style bullet-list rule, clarified the 1-2 inline sentence rule, and tightened the missing-information wording toward a warm first-person support voice. Added deterministic output validation so 1-2 missing fields must appear in one sentence and 3+ fields must each appear on their own `-` bullet line.
- Reason: The v11 prompt allowed numbered lists and relied on model compliance; the resulting Zendesk reply concatenated seven numbered fields into one hard-to-scan paragraph and sounded mechanical. The requested product rule is inline for 1-2 fields and bullets for 3+.
- Affected files or config:
  - `backend/services/automation_persona.py`
  - `backend/tests/test_automation_persona.py`
  - `docs/project/tasks/p2-101.json`
- Expected behavior change:
  - Fraud Account replies with one or two missing fields use a natural inline sentence.
  - Replies with three or more missing fields use one `-` bullet per missing field; numbered or concatenated formats fail validation and are retried within the existing four-call Account AI budget.
  - Persona ownership/grounding and publication fences remain unchanged; exhausted invalid output remains Human Review.
- Verification:
  - `pytest -q backend/tests/test_automation_persona.py backend/tests/test_account_ai_execution.py`: 38 passed, 13 subtests passed.
  - Python compilation and `git diff --check` passed in the task worktree.

## 2026-08-23 - detailed_invoice automation activation + completion persona contract (routing-gate change, no prompt text change)

- Area or subsystem: Account route gates; Automation Persona reply contract
- Prompt or model version: prompts unchanged; `AUTOMATION_PERSONA_PROMPT_VERSION` unchanged; router prompts already list `billing.detailed_invoice` / `detailed_invoice_requested`
- Summary: `ACTIVE_AUTOMATION_SUBCATEGORIES` now includes `detailed_invoice` (plus the account_billing stage binding and stored-case metadata), so LLM routing outputs of `detailed_invoice_requested` now resolve to an automated route instead of human review. New reply intent `detailed_invoice_completed_and_close` gets a Persona completion policy (state the invoice has been provided — attached to the message when facts say attachments are included — and that the ticket is closing).
- Reason: p2-104 — user approved activating detailed invoice automation and forwarding the internal-email reply PDF to the Zendesk case; completion replies ride the reply-job publication pipeline and need the close contract.
- Affected files or config:
  - `backend/services/automation_routing.py` (active set)
  - `backend/services/account_route_pipeline.py` (account_billing stage automation binding)
  - `backend/services/account_reply_jobs.py` (intent + close sets)
  - `backend/services/automation_persona.py` (ownership facts normalization + completion policy)
- Expected behavior change:
  - detailed_invoice LLM/classifier decisions flip from human_review to automated (handler billing) across layered and legacy router paths.
  - Internal-email completion replies for detailed_invoice render with the new completion policy and close after publish (Zendesk solve + local close).
- Verification:
  - `backend.tests.test_automation_routing`, `backend.tests.test_account_route_pipeline`, `backend.tests.test_account_reply_version_fence`, `backend.tests.test_automation_persona` all green in the p2-104 worktree.

## 2026-08-17 - Documentation-only container verification boundary

- Area or subsystem: Agent workflow and local single-host verification
- Prompt or model version: Repository agent rules; model configuration unchanged
- Summary: Documentation-only changes now have an explicit no-rebuild and no-restart rule, including Project Overview files and tests that only validate documentation.
- Reason: Project Overview files are served by the backend as well as opened directly from the workspace, which made the broad stack-relevant rule easy to misapply to documentation maintenance.
- Affected files or config:
  - `AGENTS.md`
  - `CLAUDE.md`
  - `docs/agent_workflow_details.md`
- Expected behavior change:
  - Diffs limited to documentation, agent instruction files, and their documentation-only tests use workspace-level verification and never trigger container rebuild or restart.
  - Mixed diffs determine live-stack verification from runtime changes only.
- Verification:
  - Text-level rule checks and `git diff --check`; no container restart.

## 2026-08-22 - Project-level planning-with-files skill

- Area or subsystem: Codex project tooling and long-running task planning
- Prompt or model version: `planning-with-files` v3.11.1; application prompt/model configuration unchanged
- Summary: Added the upstream `planning-with-files` skill under `.codex/skills/` so large multi-step tasks can explicitly opt into persistent `task_plan.md`, `findings.md`, and `progress.md` files. The skill does not require planning files for small fixes.
- Reason: Cross-cutting migrations and investigations can outlive a context window, while small repairs should keep the normal lightweight workflow.
- Affected files or config:
  - `.codex/skills/planning-with-files/`
- Expected behavior change:
  - Large tasks may invoke `planning-with-files` and use its templates/scripts from the repository.
  - Small fixes continue without creating planning files or changing the application runtime.
  - Existing project `.codex/config.toml` and global Codex hooks are not overwritten by this change.
- Verification:
  - Skill metadata, bundled scripts/templates, shell syntax, Python compilation, and `git diff --check` pass; no application stack restart required.

## 2026-08-20 - Enablement internal-reply completion LLM adjudicator

- Area or subsystem: Account Automation internal email reply loop (worker reply poller)
- Prompt or model version: `enablement-completion-classifier-v1`; new scenario `enablement_completion_classifier` (default gpt-5.4-mini, reasoning effort low, temperature 0.0, timeout 8s, max retries 1, forced JSON output)
- Summary: Enablement internal replies that the English completion regex rejects are now re-adjudicated by a single LLM classification call ("does this reply explicitly confirm the feature is NOW enabled") before falling to the resolution-update path. The regex verdict stays authoritative: regex hits skip the LLM entirely, and any classifier failure (disabled, missing credentials, invocation error, invalid JSON) falls back to the regex verdict. `ENABLEMENT_COMPLETION_CLASSIFIER_ENABLED=false` restores regex-only behavior without a deploy.
- Reason: Internal replies in Chinese or with typos (e.g. "已开通", "enbaled") were missed by the English-only regex, so customers received a reversed duplicate reply (resolution update followed by the 24-hour submission promise) and the ticket never auto-closed.
- Affected files or config:
  - `backend/services/enablement_completion_classifier.py` (new)
  - `backend/services/llm_profiles.py`
  - `backend/worker.py`
  - `deployment/docker-compose.single-host.yml` (worker_aux, worker_aux_production)
  - `.env.example`
  - `backend/tests/test_enablement_completion_classifier.py` (new), `backend/tests/test_worker.py`, `backend/tests/test_single_host_compose.py`
- Expected behavior change:
  - Non-English/typo completion replies are upgraded to the completion path (cancel pending submission reply, queue the completed-and-close reply) when the LLM confirms; the decision source (regex/llm/regex_fallback with reason) is logged and recorded on the `enablement_internal_resolution_received` event.
  - Fail-closed: LLM unavailability degrades to exactly the previous regex-only behavior; the classifier never raises and never downgrades a regex completion.
- Verification:
  - `TICKET_DB_DSN='postgresql://example.invalid/test' SENTIMENT_PROVIDER=legacy OPENAI_API_KEY= python -m unittest backend.tests.test_enablement_completion_classifier backend.tests.test_worker backend.tests.test_single_host_compose` (8 + 93 + compose contract, all green; empty API key proves no live LLM dependency in tests) and `python3 -m py_compile backend/worker.py backend/services/enablement_completion_classifier.py backend/services/llm_profiles.py`.

## 2026-08-17 - Fraud Account field grounding verification

- Area or subsystem: `/account` Fraud Account field extractor and managed Account routing prompts
- Prompt or model version: `fraud-account-fields-v3`
- Summary: Added one independent LLM verification pass when Fraud Account field evidence is uncertain or cannot be grounded. A unique exact customer quote may repair an incorrect source message id; unsupported verifier output, conflicting evidence, or unsafe payment data remains Human Review with stable grounding reason codes.
- Reason: Cases such as `#12800` could be classified as Fraud Account but stop before the internal handoff when the first extraction returned uncertain evidence. The extractor must distinguish a correctable evidence-reference error from a genuinely ungrounded result without using deterministic App ID or field extraction.
- Affected files or config:
  - `backend/services/account_verification_field_extractor.py`
  - `backend/services/prompts/account_routing.py`
  - `backend/tests/test_account_verification_automation.py`
  - `backend/tests/test_agent_config.py`
- Expected behavior change:
  - Correctable source-message references are repaired only when the quoted customer text matches exactly one customer message.
  - Verification failure stays `uncertain`/Human Review and records `verification_conflict`, `source_message_not_found`, `quote_mismatch`, `low_confidence`, or `sensitive_data` as applicable.
  - Missing and ambiguous field objects remain valid outcomes and do not trigger grounding failure solely because they omit evidence.
  - Field values remain concise summaries; only the source quote must be exact.
- Verification:
  - 24 Account Verification/Automation and reroute/state-repair tests passed.
  - Prompt catalog contract and `git diff --check` passed.

## 2026-08-14 - Account Automation concise ownership replies

- Area or subsystem: `/account` Automation Persona, Account reply facts, and Account reply poller deployment boundary
- Prompt or model version: `automation-persona-v8`; model configuration unchanged
- Summary: Account submission confirmations now provide semantic ownership state instead of third-person customer-copy sentences. The Persona generates a concise first-person acknowledgement that says Support is reviewing the request with the internal team and will update the customer when there is progress.
- Reason: Persona v7 received `The assigned Support Engineer...` as a fact and reproduced it as a mechanical status report. A production v6 worker also consumed a shared-PostgreSQL job created by the local stack, so the two environments did not enforce the same reply contract.
- Affected files or config:
  - `backend/services/automation_persona.py`
  - `backend/tests/test_automation_persona.py`
  - `backend/tests/test_account_intake.py`
  - `backend/tests/test_worker.py`
  - `deployment/docker-compose.single-host.local-lightweight.yml`
  - `deployment/docker-compose.single-host.local-db.yml`
  - `backend/tests/test_single_host_compose.py`
  - `docs/roadmap/phase2.html`
- Expected behavior change:
  - `submission_confirmation` uses first-person ownership, concise acknowledgement, update commitment, and patience wording without exposing semantic field names.
  - Third-person Support Engineer narration and internal-team delegation fail closed to Human Review.
  - Unpublished v6/v7 Account Persona jobs are rendered once with v8; published historical replies are unchanged.
  - Remote PostgreSQL local-lightweight workers do not claim Account reply jobs; the deployed full stack is the single poller owner. Local-DB lightweight tests keep the poller enabled.
  - `/client` and shared legacy routing/reply behavior remain unchanged.
- Verification:
  - Targeted Persona, Account intake, worker rerender, compose contract, Python syntax, and `git diff --check` tests.

## 2026-08-14 - Account Automation ownership-first customer replies

- Area or subsystem: `/account` Automation Persona, shared Automation reply facts, delayed reply worker, and Phase 2 Automation Scope
- Prompt or model version: `automation-persona-v7`; model configuration unchanged
- Summary: Registered Account Automation outcomes now use a shared reply-state contract. Submission confirmations describe the Support Engineer actively coordinating with the internal team and proactively updating the customer; missing-field replies promise continued ownership after the customer responds; resolution updates preserve the newest internal result.
- Reason: The previous facts told the Persona that the internal team would follow up, which made the customer-facing reply appear to transfer ownership away from Support. The internal team is a collaborator, not the customer's contact owner.
- Affected files or config:
  - `backend/services/automation_persona.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/tests/test_automation_persona.py`
  - `backend/tests/test_account_intake.py`
  - `backend/tests/test_worker.py`
  - `docs/roadmap/phase2.html`
- Expected behavior change:
  - `submission_confirmation` uses first-person ownership and never tells the customer that an internal team will contact them.
  - Explicit delegation wording fails closed to the existing Human Review path; the system does not replace it with a fixed template.
  - Unpublished reply jobs generated under the previous Persona version are rendered once with v7 before publication, without resending internal email or creating another reply job.
  - `/client`, the shared legacy router, published historical replies, routing labels, and Automation lifecycle state remain unchanged.
- Verification:
  - Targeted Automation Persona, Account intake, worker, rerun, and legacy router regression tests; `python3 -m py_compile`; `git diff --check`.

## 2026-08-13 - TS-05 Account failure stop, alert, and human handoff

- Area or subsystem: `/account` layered routing, Account field extraction, Automation Persona, reply worker, and rerun failure handling
- Prompt or model version: Account-only `account_route` profile; no fallback provider/model is permitted; maximum three retries after the initial call
- Summary: Account processing now treats exhausted AI/API calls, invalid structured output, extractor/Persona failures, and unexpected internal execution failures as system failures. The Case is persisted as `human_review_required`, pending customer reply jobs are cancelled, and one redacted incident alert is sent to `xieziling@agora.io` through the existing Graph mail path.
- Reason: Unexpected failures must not be silently converted to a legacy fallback or an invented customer response. Human operators need a durable Case state and a direct owner-visible signal.
- Affected files or config:
  - `backend/services/account_ai_execution.py`
  - `backend/services/account_failure_alerts.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/repositories/ticket_repository.py`
  - Account field extractor and Persona modules
- Expected behavior change:
  - Account AI makes at most four actual attempts (initial call plus three retries) and never selects DeepSeek, a backup model, or another provider.
  - Exhaustion stops automation without customer reply generation; failure metadata remains on the Account Case and failed intake idempotency records can replay without duplicating the Case or incident alert.
  - Alert content excludes customer bodies, email addresses, tokens, and prompts; failed mail delivery releases the alert claim for a later retry.
- Verification:
  - Account intake, AI execution, alert, route, worker, repository, and rerun targeted tests; `python3 -m py_compile`; `git diff --check`.

## 2026-08-10 - Account Admin taxonomy, managed Prompts, and Automation Workflow catalog

- Area or subsystem: `/workspace/admin/#agent-config` and Automated Cases Admin surfaces
- Prompt or model version: `account-layered-router-v7` / `account-backend-operation-v1`; model configuration unchanged
- Summary: Synchronized Admin Agent Config with the v7 layered `/account` taxonomy. Account & Billing owns Account Suspension,
  Fraud Account, Detailed Invoice, and Other; Automation/Backend Operation owns Enablement, Quota, and diagnostic Unregistered.
  Managed route Prompts now expose their active versions, and the Admin Automation Workflow catalog includes registered
  billing-owned automated outcomes as well as Enablement, Quota, and the Human Review fallback.
- Reason: Operators must see the same ownership and execution boundaries used by `/account`, without treating every
  Account & Billing label as an Automation Router child or hiding automated billing workflows from the Admin view.
- Affected files or config:
  - `backend/services/account_admin.py`
  - `backend/services/agent_config.py`
  - `backend/services/account_route_pipeline.py`
  - `ui/workspace-ui/admin/app.js`
  - `backend/tests/test_workspace_admin_ui_contract.py`
  - `backend/tests/test_account_admin_features.py`
  - `backend/tests/test_agent_config.py`
  - `backend/tests/test_workspace_api.py`
- Expected behavior change:
  - Agent Config shows Intent Classifier, Agora Router, Account & Billing Router, Backend Operation Router, and Automation Router hierarchy.
  - Prompt management marks the `/account` routing stages as managed and keeps Prompt version metadata visible.
  - Automation Workflow shows Fraud Account, Detailed Invoice, Enablement, Quota, and Unregistered with handler,
    route-family, status, and lifecycle steps.
  - Automated Cases keeps the default automated route view but supports category filtering and displays Category,
    Subcategory, Handler, Route status, and Automation status.
- Verification:
  - `/tmp/supportportal-account-route-venv/bin/python -m pytest -q backend/tests/test_workspace_admin_ui_contract.py backend/tests/test_account_admin_features.py backend/tests/test_agent_config.py backend/tests/test_workspace_api.py` (64 passed)
  - `/tmp/supportportal-account-route-venv/bin/python -m py_compile backend/services/account_admin.py backend/services/agent_config.py backend/services/account_route_pipeline.py`
  - `node --check ui/workspace-ui/admin/app.js`

## 2026-08-10 - Account route taxonomy v7 and Backend Operations stage

- Area or subsystem: `/account` Intent Classifier, Agora Router, Account & Billing Router, and Backend Operations Router
- Prompt or model version: `account-layered-router-v7` / `account-backend-operation-v1`; model configuration unchanged
- Summary: Split explicit backend operations from Account & Billing classification. Account & Billing now owns
  `account_suspension`, `fraud_account`, `detailed_invoice`, and `other`; Backend Operations owns `enablement`,
  `quota`, and diagnostic `unregistered`.
- Reason: Detailed invoice and fraud-review requests have a concrete next action and should enter the existing
  billing automation contract directly, while feature enablement and quota operations need a separate expandable
  taxonomy. Unregistered remains visible for discovering future automation candidates.
- Affected files or config:
  - `backend/services/account_route_pipeline.py`
  - `backend/services/prompts/account_routing.py`
  - `backend/services/account_billing_handlers.py`
  - `backend/services/support_router.py`
  - `backend/tests/test_account_route_pipeline.py`
- Expected behavior change:
  - New `/account` routes emit `backend_operation` for Enablement/Quota/Unregistered and emit Account & Billing
    subcategories directly for Detailed Invoice/Fraud Account.
  - Registered Detailed Invoice, Fraud Account, Enablement, and Quota routes are marked automated; Account
    Suspension, Account & Billing Other, and Backend Operations Unregistered fail closed to Human Review.
  - The old `automation` Agora payload remains an input-only compatibility alias; the shared `/client` router is
    not changed.
- Verification:
  - `rtk /tmp/supportportal-account-route-venv/bin/python -m pytest -q backend/tests/test_account_route_pipeline.py backend/tests/test_agent_config.py`
  - `rtk git diff --check`

## 2026-08-07 - Automation Persona presets and random pinned assignment

- Area or subsystem: `/account` Automation Persona registry and assignment
- Prompt or model version: `automation-persona-presets-v1`; shared Automation Persona renderer and model configuration unchanged
- Summary: Added three independently versioned thin Persona presets: `Sid Precise`, `Sid Bright`, and `Sid Warm`. They share the exact multiline Signature `Best,\nSid\nSupport Engineer 2` while keeping their precise, bright, or warm writing-style guidance separate.
- Reason: Operators need to manage and compare distinct writing styles without duplicating factual or behavioral instructions per Automation Behavior. Random first assignment across eligible Personas avoids a deterministic Ticket-ID allocation while a pinned version keeps each Case voice consistent.
- Affected files or config:
  - `backend/services/account_admin.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/sql/ticket_storage.sql`
  - `backend/main.py`
  - `backend/worker.py`
  - `ui/workspace-ui/admin/app.js`, `ui/workspace-ui/admin/index.html`, `ui/workspace-ui/admin/styles.css`
  - `ui/account-ui/app.js`, `ui/account-ui/index.html`, `ui/account-ui/styles.css`
  - `scripts/ops/rerun_automated_account_cases.py`, `scripts/rerun_automated_account_cases.py`, `scripts/recover_account_rerun.py`
- Expected behavior change:
  - The registry initializes the three presets with independent version histories and preserves later operator-managed publish, rollback, enable, and disable decisions.
  - The first customer-visible Automation reply selects randomly from enabled Personas with a published version and atomically pins the selected Persona key and exact version; later intake, delayed, recovery, and Outlook follow-up paths reuse that assignment.
  - A complete single-Case or batch Rerun clears the existing assignment before routing again. A Case that remains Automated selects independently when it next needs a reply and may select the same Persona again.
  - If no enabled and published Persona is available, generation fails closed to Human Review without fallback customer copy.
  - A dry-run-first, resumable operator tool can freeze and rerun only Cases that are Automated at the start of the one-time rollout; deployment does not execute that data operation automatically.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_reroute_dispatch backend.tests.test_rerun_automated_account_cases backend.tests.test_startup_repository_fallbacks -q` (68 passed)
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_repository_configuration -q` (104 passed)
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_ui_contract -q` (22 passed)
  - `RUN_ACCOUNT_REROUTE_POSTGRES_TEST=true rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_reroute_jobs_postgres -v` (13 passed, 0 skipped)
  - `rtk python3 scripts/verify_feature_list.py`
  - `rtk rg -n "automation-persona-presets-v1|Sid Precise|Sid Bright|Sid Warm|random" design.md docs/prompt_change_log.md docs/feature_list.md docs/roadmap.html`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_roadmap_contract.RoadmapContractTests.test_billing_and_route_plans_reflect_meeting_next_steps backend.tests.test_roadmap_contract.RoadmapContractTests.test_existing_product_lanes_and_architecture_still_render -q` (2 passed)
  - `rtk git diff --check`
  - Pending post-merge live verification: `/health.status` is healthy and `app_build.ref` equals merged `main`; the live registry contains exactly enabled/published `default-support`, `sid-bright`, and `sid-precise` with the expected versions and content hashes; Admin serves the random-assignment/pinned-version copy; Account serves the assignment-deletion statistic. The one-time live data operation has not been run.

## 2026-08-07 - Internal automation email light-only Outlook compatibility

- Area or subsystem: `/account` Automation internal handoff HTML template
- Prompt or model version: `internal-handoff-v3` / deterministic domain renderers; model configuration unchanged
- Summary: Removed the explicit dark palette and dark-mode negotiation from the shared internal handoff template. New HTML uses a stable Light-only content canvas with inline and `bgcolor` fallbacks, while preserving the existing plain-text body and Microsoft Graph Outlook handoff.
- Reason: Outlook for Mac applied a second dark-mode transformation to the v2 deep blue-black palette, rendering the email as a hazy medium-gray surface in dark theme.
- Affected files or config:
  - `backend/services/internal_email_template.py`
  - `backend/tests/test_internal_email_template.py`
  - `backend/services/internal_email_payload.py` (shared version gate; no algorithm change)
  - `backend/tests/test_internal_email_payload.py`
  - `design.md`
- Expected behavior change:
  - New internal Fraud, Invoice, Enablement, Account Verification, and Quota handoffs use `internal-handoff-v3`, declare Light-only support, and no longer emit `prefers-color-scheme: dark` or dark `[data-ogsc]` rules.
  - Unsent v1/v2 HTML payloads rebuild to v3 while preserving delivery keys and attempt metadata; sent payloads are never rewritten or resent.
  - Existing Outlook handoff transport, subject, recipient routing, customer data escaping, and plain-text fallback remain unchanged.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_internal_email_template.py backend/tests/test_internal_email_payload.py backend/tests/test_billing_automation_email.py backend/tests/test_enablement_automation.py backend/tests/test_quota_automation.py backend/tests/test_account_verification_automation.py -k 'not all_account_automation_subcategories_are_explicitly_registered'` (54 passed, 1 deselected, 16 subtests passed)
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/internal_email_template.py backend/services/internal_email_payload.py`
  - Full related test selection also reproduces the pre-existing `account_suspension` registration failure on unchanged `main`; it is unrelated to email rendering.
  - A synthetic `.eml` was generated without customer data, but automated Outlook for Mac dark/light screenshot verification was blocked because Computer Use permissions were not granted in this environment.

## 2026-08-06 - Account Router structured-output retry and failure audit

- Area or subsystem: `/account` layered Intent Classifier, Agora Router, Account & Billing Router, and Automation Router
- Prompt or model version: `account-layered-router-v6`; model configuration unchanged
- Summary: Enabled JSON object output for every Account routing stage, added application-level stage schema validation, and added one local repair retry for malformed JSON or invalid output contracts. Added stage attempt counts, stable failure types, recovery status, model/provider metadata, and redacted output fingerprints to route execution audit.
- Reason: Case `#12572` was correctly classified as Account & Billing in earlier executions, but one malformed Intent Classifier response during `bulk_latest_reroute` was collapsed to `invalid_intent_output` and overwrote the valid route.
- Affected files or config:
  - `backend/services/account_route_pipeline.py`
  - `backend/services/account_admin.py`
  - `backend/services/account_case_reroute.py`
  - `backend/main.py`
  - `backend/tests/test_account_route_pipeline.py`
  - `backend/tests/test_account_admin_features.py`
  - `backend/tests/test_account_case_reroute.py`
  - `backend/tests/test_account_intake.py`
  - `backend/tests/fixtures/account_route_golden_cases.json`
- Expected behavior change:
  - A contract failure is retried once for the current stage only; successful upstream stages are not repeated and valid low-confidence output is not retried.
  - Two failed contract attempts fail closed to Human Review while retaining the parent-layer label (`Uncertain`, `Agora / Uncategorized`, `Account & Billing / Other`, or `Automation / Unregistered`). The previous route is audit-only and never restored as the current label.
  - Account APIs expose stable stage failure types, attempt counts, recovery flags, and failure family; raw model output is not returned and audit excerpts are redacted and bounded.
  - `/client` and the legacy shared router remain unchanged.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_account_route_pipeline.py backend/tests/test_account_admin_features.py backend/tests/test_account_case_reroute.py backend/tests/test_account_full_reroute.py`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_account_intake.py::AccountIntakeApiTests::test_account_case_view_exposes_route_failure_diagnostics`
  - Case `#12572` single-case rerun and live Account/Admin API verification after merge.

## 2026-08-05 - Atomic Outlook automation replies and Persona v6 safety gate

- Area or subsystem: `/account` Automation Outlook reply processing and Automation Persona
- Prompt or model version: `automation-persona-v6`; model configuration unchanged
- Summary: Added lease-based, owner-checked Outlook reply claims and a single-transaction completion path for the customer message, Account case, resolution/follow-up events, and completed claim. Added deterministic pre-extractor redaction and post-extractor/final-reply forbidden-value checks. Added `cross_channel_media_relay -> Media Relay` canonical display mapping.
- Reason: Case `#12555` was processed twice for the same Microsoft Graph message because the prior history scan and separate persistence transactions were not atomic. Internal resolution notes could also expose identifiers or the customer's misspelled raw feature label to the Persona pipeline.
- Affected files or config:
  - `backend/repositories/ticket_repository.py`
  - `backend/sql/ticket_storage.sql`
  - `backend/sql/migrations/2026_08_05_automation_reply_claims.sql`
  - `backend/worker.py`
  - `backend/services/billing_automation.py`
  - `backend/services/automation_persona.py`
  - `backend/services/enablement_automation.py`
  - `deployment/docker-compose.single-host.yml`
- Expected behavior change:
  - Only one worker can process a Graph reply while its lease is active; stale or failed claims can be reclaimed, but an old owner cannot commit.
  - A completed duplicate is marked read without generating another customer reply. An in-progress or failed attempt remains unread for retry.
  - Customer message, Account case state, reply events, and completed claim commit atomically with partial unique indexes as a second guard.
  - Billing PDF retries reuse a deterministic attachment ID instead of creating another asset.
  - App IDs, ticket IDs, Account case IDs, emails, and raw feature labels are removed before extraction and rejected if they appear in extracted facts or the final reply; unsafe output moves to manual attention.
  - `worker_query` explicitly disables both Automation Outlook poller flags so `.env` cannot create a second poller owner.
- Verification:
  - `rtk uv run --with 'psycopg[binary]' --with psycopg-pool --with fastapi --with 'pydantic==2.11.7' --with python-dotenv --with httpx python -m unittest backend.tests.test_automation_reply_claims backend.tests.test_automation_persona backend.tests.test_billing_automation_email backend.tests.test_single_host_compose backend.tests.test_repository_configuration <nine targeted worker reply tests> -v` (162 passed)
  - `RUN_POSTGRES_INTEGRATION=1 uv run --with 'psycopg[binary]' --with psycopg-pool python -m unittest backend.tests.test_automation_reply_claims_postgres -v` against an isolated temporary schema (1 passed; schema removed in `finally`)

## 2026-08-05 - Customer-safe Enablement confirmation facts

- Area or subsystem: `/account` Enablement reply facts and Automation Persona
- Prompt or model version: `automation-persona-v5`
- Summary: Added a deterministic customer-visible projection for Enablement facts. Canonical `media_relay` is rendered as `Media Relay`; customer-provided App IDs and raw feature labels are excluded from submission confirmations while remaining available to internal handoff and audit paths.
- Reason: Case `#12596` repeated an App ID the customer had already supplied and copied the misspelled label `channel media rele` into the confirmation reply.
- Affected files or config:
  - `backend/services/enablement_automation.py`
  - `backend/services/automation_persona.py`
  - `backend/worker.py`
  - `backend/tests/test_enablement_automation.py`
  - `backend/tests/test_automation_persona.py`
- Expected behavior change:
  - Enablement submission confirmations use `Media Relay` when the canonical feature key is `media_relay`, without repeating the App ID or the customer's raw/misspelled feature label.
  - Unknown feature keys are not silently corrected; the customer reply can refer to the request generically.
  - Resolution updates retain safe status facts but still exclude App IDs and raw feature labels.
  - Internal Outlook handoff and persisted `collected_fields` continue to contain the original App ID and raw label.
- Verification:
  - `rtk uv run --with pytest --with 'psycopg[binary]' python -m pytest backend/tests/test_automation_persona.py backend/tests/test_enablement_automation.py -q` (26 passed, 9 subtests)
  - `rtk uv run --with pytest --with 'psycopg[binary]' python -m pytest backend/tests/test_automation_persona.py backend/tests/test_enablement_automation.py backend/tests/test_enablement_field_extractor.py backend/tests/test_enablement_repair.py backend/tests/test_account_intake.py -k 'enablement or automation_persona' -q` (47 passed, 11 subtests)
  - `rtk uv run --with pytest --with 'psycopg[binary]' python -m pytest backend/tests/test_worker.py -k 'enablement_delivery or persona_reply_facts_are_rendered_before_scheduling or deleted_account_reply_job or enablement_resolution_reply_uses_canonical_feature_key' -q` (8 passed)

## 2026-08-05 - Managed Prompt retirement and pre-stop Release validation

- Area or subsystem: managed Prompt Catalog, Prompt Release runtime, EC2 deployment, and targeted Account reply recovery
- Prompt or model version: `prompt-release-v2` lifecycle; prompt text and model configuration unchanged
- Summary: Added explicit managed-prompt retirement/reactivation state, projected deployment candidates onto the current code catalog, and validated candidate snapshots before stopping the healthy stack. Added a PII-redacted, single-ticket customer-name repair command that reuses existing reply facts and queues a delayed Persona replacement without resending internal email.
- Reason: Removing a managed prompt from code left its database key in every later release, so strict startup validation rejected the new image and automatic deployment rolled back. Completed Account intake idempotency also made N8n replay unsuitable for repairing one already-created case.
- Affected files or config:
  - `backend/repositories/ticket_repository.py`
  - `backend/sql/ticket_storage.sql`
  - `backend/services/prompt_runtime.py`
  - `backend/services/prompt_versioning.py`
  - `backend/scripts/prompt_release.py`
  - `deployment/deploy_ec2.sh`
  - `backend/services/account_reply_jobs.py`
  - `backend/scripts/repair_account_customer_name.py`
- Expected behavior change:
  - Removed code-catalog keys remain available in historical Prompt Releases but are hidden from current managed-prompt APIs and excluded from new candidates.
  - A catalog key-set change creates a candidate even without scheduled prompt edits; reintroduced keys reuse their last activated content through a scheduled version.
  - Candidate hash/catalog failures abort deployment before `docker compose down`, leaving the current stack running; activation retains strict exact-catalog validation.
  - The targeted Account repair preserves the current published reply until its replacement publishes, keeps prior reply facts/asked fields/delivery identity, recalculates the 6-10 minute delay, and records no customer name or email value in output or audit events.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_prompt_versioning backend.tests.test_repair_account_customer_name -v` (21 tests)
  - `RUN_PROMPT_POSTGRES_TEST=true` PostgreSQL integration suite against an isolated temporary schema (2 tests)
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_deploy_ec2 -v`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m compileall -q backend`
  - `rtk bash -n deployment/deploy_ec2.sh`
  - `rtk git diff --check`

## 2026-08-03 - Customer-aware, natural Automation greetings

- Area or subsystem: `/account` intake, Automation reply facts, and Automation Persona
- Prompt or model version: `automation-persona-v4`
- Summary: Added the persisted `customer_name` intake field, deterministic first-name greetings with a `Customer` fallback, and a less templated support-writing instruction for Persona-generated reply bodies.
- Reason: Automation replies should address the customer personally and read like an experienced support engineer instead of beginning with robotic status text.
- Affected files or config:
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/services/automation_persona.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/sql/ticket_storage.sql`
  - `backend/sql/migrations/2026_08_03_account_customer_name.sql`
- Expected behavior change:
  - `customer_name="Jack Gold"` is persisted on the Account case and becomes the exact greeting `Hi Jack,`; missing, placeholder, URL-like, or email-like values become `Hi Customer,`.
  - The application owns the greeting and Signature while the Persona writes only the intervening body in a warm, natural voice without labels, fragments, canned status wording, or repetitive corporate filler.
  - Existing cases without `customer_name` remain compatible and use the fallback greeting.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_automation_persona backend.tests.test_account_intake.AccountIntakeApiTests.test_account_intake_routes_enablement_and_sends_internal_request backend.tests.test_account_intake.AccountIntakeApiTests.test_account_case_summary_is_no_store_and_excludes_customer_detail_fields backend.tests.test_worker.WorkerResilienceTests.test_enablement_delivery_retry_replaces_malformed_cancelled_confirmation backend.tests.test_worker.WorkerResilienceTests.test_published_persona_content_is_reused_on_retry backend.tests.test_worker.WorkerResilienceTests.test_persona_failure_moves_reply_job_to_human_review_without_sending backend.tests.test_repository_configuration -q`
  - `rtk git diff --check`

## 2026-08-03 - Separately managed Automation Persona Signature

- Area or subsystem: `/account` Automation Persona and Workspace Admin Agent Config
- Prompt or model version: `automation-persona-v3` / default Persona signature schema
- Summary: Moved the customer signature out of the Persona instruction and replaced the single-line `signoff_name` editor with a manually managed multiline `Signature` field.
- Reason: Operators need exact control over closings, names, and job titles without asking the LLM to infer or rewrite them.
- Affected files or config:
  - `backend/services/account_admin.py`
  - `backend/services/automation_persona.py`
  - `backend/repositories/ticket_repository.py`
  - `ui/workspace-ui/admin/app.js`
  - `ui/workspace-ui/admin/styles.css`
- Expected behavior change:
  - The Persona LLM generates the localized message body without a signature; the application appends the published multiline Signature unchanged.
  - New Persona drafts store `signature`; existing versions with only `signoff_name` remain readable and render as `Best Regards,` plus the saved name.
  - Untouched system default Personas are upgraded to a new published version containing `Best,`, `Sid`, and `Support Engineer 2` on separate lines.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_automation_persona backend.tests.test_account_admin_features.AccountAdminFeatureTests.test_persona_draft_publish_assignment_and_rollback_are_versioned backend.tests.test_account_admin_features.AccountAdminFeatureTests.test_persona_opener_and_reply_execution_are_auditable backend.tests.test_workspace_admin_ui_contract backend.tests.test_workspace_api.WorkspaceApiTests.test_account_persona_api_publishes_and_rolls_back_without_overwriting_history backend.tests.test_repository_configuration -q`
  - `rtk node --check ui/workspace-ui/admin/app.js`
  - `rtk git diff --check`

## 2026-08-03 - Default Automation Persona identity and signature

- Area or subsystem: `/account` Automation Persona
- Prompt or model version: `default-support-v2`
- Summary: Changed the default Persona to Sid, a friendly and helpful support agent who matches the customer's language and always signs every customer-facing reply as Sid.
- Reason: The default voice should be approachable and consistent, while making the support identity and signature requirement explicit.
- Affected files or config:
  - `backend/services/account_admin.py`
  - `backend/repositories/ticket_repository.py`
- Expected behavior change:
  - New Automation replies using the default Persona are written in the customer's language with a friendly, helpful tone and a Sid signature.
  - Existing databases still using the untouched system-seeded v1 default are upgraded to v2; custom Persona versions and historical pinned assignments remain unchanged.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_admin_features.AccountAdminFeatureTests.test_persona_draft_publish_assignment_and_rollback_are_versioned backend.tests.test_repository_configuration -q`
  - `rtk git diff --check`

## 2026-07-30 - Account route taxonomy v2 and stable reason codes

- Area or subsystem: `/account` Intent Classifier, Agora Router, and Automation Router
- Prompt or model version: `account-intent-v2` / `account-agora-v2` / `account-automation-v3`
- Summary: Simplified the first classifier to Conversation, Agora, or Uncertain; added Account & Billing and Uncategorized to the Agora taxonomy; replaced Automation ambiguity with Unregistered; and added controlled terminal and per-stage reason codes.
- Reason: The previous support-scope and mixed categories over-routed clear Agora technical, billing, and backend-operation requests to Human Review and made missing taxonomy coverage indistinguishable from low-confidence classification.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/account_route_pipeline.py`
  - `backend/services/account_admin.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/main.py`
  - `ui/account-ui/app.js`
- Expected behavior change:
  - Account Cases use an Agora prior; third-party comparisons and integrations remain Agora-related, while wholly unrelated or indecipherable requests are Uncertain.
  - Agora Router no longer emits mixed or unclear and selects one primary category with optional additional intents.
  - Automation requires grounded backend action, target, and evidence before entering Automation Router; confirmed operations without a registered subcategory are Automation / Unregistered.
  - API and Admin diagnostics expose `route_reason_code` and `stage_reason_codes` as stable fields.
- Verification:
  - `rtk python3 -m unittest backend.tests.test_account_route_pipeline backend.tests.test_account_ui_contract backend.tests.test_workspace_admin_ui_contract`
  - `rtk python3 scripts/verify_feature_list.py`

## 2026-08-03 - Automation facts extraction and unified Persona rendering

- Area or subsystem: `/account` Automation Behavior, delayed reply jobs, Outlook resolution follow-up, and client automation route replies
- Prompt or model version: `automation-persona-v2` / `detailed-invoice-fields-v2`
- Summary: Removed customer-copy generation from the real Automation Behavior path. LLM field extractors now produce structured intake or resolution facts, while the assigned Automation Persona generates the final customer message immediately before publication.
- Reason: Multiple behavior-specific customer-copy prompts created overlapping instructions and inconsistent voice. A single Persona should own final wording after deterministic routing, field collection, internal actions, and send timing are complete.
- Affected files or config:
  - `backend/services/automation_persona.py`
  - `backend/services/detailed_invoice_field_extractor.py`
  - `backend/services/prompts/account_routing.py`
  - `backend/services/support_router.py`
  - `backend/services/account_full_reroute.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/services/account_verification_automation.py`
  - `backend/services/billing_automation.py`
  - `backend/services/enablement_automation.py`
  - `backend/services/quota_automation.py`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
- Expected behavior change:
  - Intake replies persist structured `reply_facts`; no base customer message is generated before the delayed send job.
  - Billing, Enablement, Quota, and Fraud Account internal replies use an LLM facts extractor followed by one Persona render.
  - Detailed Invoice uses the managed LLM field extractor for issue date, transaction ID, amount, and currency values.
  - A missing Persona assignment, unavailable credentials, failed extraction, or empty Persona response stops the send and marks the case for Human Review; no fallback customer copy is sent.
  - A successful render is persisted on the reply job and reused on retries, preventing a second Persona call for the same publication.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_automation_persona backend.tests.test_account_full_reroute backend.tests.test_account_verification_automation backend.tests.test_billing_automation_email backend.tests.test_enablement_automation backend.tests.test_quota_automation backend.tests.test_worker.WorkerResilienceTests.test_published_persona_content_is_reused_on_retry backend.tests.test_worker.WorkerResilienceTests.test_persona_failure_moves_reply_job_to_human_review_without_sending -q` (49 tests)
  - `rtk python -m py_compile backend/main.py backend/worker.py backend/services/*.py`
  - `rtk git diff --check`
  - `rtk python3 scripts/verify_feature_list.py`

## 2026-07-30 - Account Verification grounded intake and safe follow-up

- Area or subsystem: `/account` Account Verification Automation
- Prompt or model version: `account-verification-fields-v1` / `account-verification-follow-up-v1`
- Summary: Replaced Billing regex field collection for Account Verification with a managed LLM extractor for Company Information, Contact Information, Use Case, and safe Payment Information, plus a contextual one-time follow-up composer.
- Reason: Customer information is expressed in varied natural language, while Website and similar optional fields were incorrectly blocking handoff. Payment intake also needed explicit protection against collecting credentials.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/account_verification_field_extractor.py`
  - `backend/services/account_verification_automation.py`
  - `backend/services/account_route_pipeline.py`
  - `backend/services/agent_config.py`
- Expected behavior change:
  - Only the four required information groups influence Account Verification completeness; Website, App ID, and contact email are optional.
  - No-payment, free-tier, and not-applicable statements satisfy Payment Information without requesting transaction or payment-instrument data.
  - Missing groups receive one contextual follow-up. A later reply proceeds to internal handoff with explicit missing groups instead of asking again.
  - Sensitive card, security-code, credential, and bank-account content is blocked before LLM extraction and sends the Case to Human Review; generated follow-ups are validated before scheduling.
- Verification:
  - `rtk pytest -q backend/tests/test_account_verification_automation.py backend/tests/test_account_route_pipeline.py backend/tests/test_agent_config.py backend/tests/test_repository_configuration.py`
  - `rtk python3 scripts/verify_feature_list.py`

## 2026-07-28 - Separate Account Suspension automation routing

- Area or subsystem: `/account` Automation Router / Billing Automation
- Prompt or model version: `account-automation-v2`
- Summary: Restored `account_suspension` as a registered Automation subcategory instead of normalizing it to `account_verification`. Added separate definitions and examples for suspension review/access restoration versus submission of account-verification materials.
- Reason: Account suspension and account verification use different required fields, internal handoff content, and operational outcomes, so merging them loses routing and execution meaning.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/account_route_pipeline.py`
  - `backend/services/automation_routing.py`
  - `backend/services/route_correction.py`
  - `backend/services/support_router.py`
  - `backend/services/account_admin.py`
  - `.env.example`
  - `ui/account-ui/app.js`
  - `backend/repositories/ticket_repository.py`
  - `backend/sql/migrations/2026_07_28_split_account_suspension.sql`
- Expected behavior change:
  - Requests to review a suspended/disabled account or restore access route to `Automation / Account Suspension` and the Billing suspension flow.
  - Requests to submit company, use-case, or contact materials for verification remain `Automation / Account Verification`.
  - Route correction and Agent Config expose both subcategories; unknown or ambiguous operations continue to fail closed.
  - Historical Automated Cases are restored only when stored semantic fields explicitly identify `billing.account_suspension`; ambiguous verification history is unchanged.
- Verification:
  - `rtk pytest -q backend/tests/test_account_route_pipeline.py backend/tests/test_automation_routing.py backend/tests/test_route_correction.py backend/tests/test_account_intake.py backend/tests/test_account_admin_features.py backend/tests/test_account_ui_contract.py backend/tests/test_repository_configuration.py`
  - `rtk python3 scripts/verify_feature_list.py`

## 2026-07-23 - Deployment-bound Prompt version management

- Area or subsystem: Agent Config, shared LLM Prompt runtime, EC2 deployment
- Prompt or model version: `prompt-release-v1`
- Summary: Added immutable Prompt versions with Draft, Scheduled, Active, history, Diff, Restore, and deployment-bound Releases. Prompt changes do not hot reload; one Release is loaded by all five LLM runtime services during `deployment/deploy_ec2.sh`.
- Reason: Prompt edits need reviewable history and atomic rollout without allowing processes to observe different versions or allowing an editor save to change production immediately.
- Affected files or config:
  - `backend/services/agent_config.py`, `backend/services/prompt_versioning.py`, `backend/services/prompt_runtime.py`
  - `backend/repositories/ticket_repository.py`, `backend/sql/ticket_storage.sql`, `backend/main.py`
  - Prompt-consuming backend services and worker entry points
  - `backend/scripts/prompt_release.py`
  - `deployment/deploy_ec2.sh`, `deployment/docker-compose.single-host.yml`, `scripts/ops/auto_deploy_ec2.sh`
  - `ui/workspace-ui/admin/`
- Expected behavior change:
  - Saving or scheduling a Prompt never changes a running process. Scheduled versions become Active only after the next successful daily deployment.
  - `api`, `rag_api`, `rag_worker`, `worker_query`, and `worker_aux` must load the same validated Release and expose/emit its identity for deployment verification.
  - Failed build, startup, internal/external health, service verification, or activation restores the previous image, build ref, and Prompt Release.
  - The daily automation runs a full deployment even when Git has no new commit, allowing scheduled Prompt changes to ship.
  - Completion audit follow-up: activation transport failures are reconciled against the committed Active Release before rollback; the Admin Diff highlights changed lines and preserves unsaved operator input after conflicts.
- Verification:
  - `rtk python3 -m unittest backend.tests.test_deploy_ec2 backend.tests.test_auto_deploy_ec2 -v`
  - Temporary PostgreSQL 16 schema: `RUN_PROMPT_POSTGRES_TEST=true python -m unittest backend.tests.test_prompt_versioning_postgres -v`
  - Podman project image: `python -m unittest backend.tests.test_prompt_versioning backend.tests.test_agent_config -v`
  - Podman project image: `python -m unittest backend.tests.test_workspace_api.WorkspaceApiTests.test_prompt_version_api_manages_next_deploy_without_changing_active_runtime -v`
  - `rtk python3 -m unittest backend.tests.test_workspace_admin_ui_contract backend.tests.test_single_host_compose -v`

## 2026-07-22 - Account-only delayed reply standard process

- Area or subsystem: `/account` routing, AI reply scheduling, and Billing missing-field collection
- Prompt or model version: `account-standard-reply-v1` / existing Persona assignment
- Summary: Every `/account` ticket now routes first and schedules an AI reply for account-only publication after a persisted random 6–10 minute delay. Billing reply generation receives the normalized fields already requested in the ticket and deterministically excludes them from later questions.
- Reason: Account replies must not look instantaneous or leave the account system, and customers must not be repeatedly asked for information they already declined or omitted.
- Affected files or config:
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/services/billing_automation.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/sql/ticket_storage.sql`
  - `ui/account-ui/app.js`
  - `ui/account-ui/styles.css`
  - `backend/tests/test_account_intake.py`
  - `backend/tests/test_account_ui_contract.py`
  - `design.md`
  - `docs/feature_list.md`
  - `docs/roadmap.html`
  - `docs/roadmap/phase2.html`
- Expected behavior change:
  - New tickets and customer follow-ups immediately persist their timestamp and route result, while assistant messages remain absent until their stored scheduled time.
  - AI replies are marked `visibility=account_only`; no customer-source delivery adapter is invoked. Existing internal Outlook handoff continues when Billing fields are complete.
  - A newer customer message cancels the older unpublished job and schedules one replacement against the latest conversation.
  - Every normalized Billing field can appear in an AI question only once per ticket. Later replies retain the missing-field state without repeating the request.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_account_intake.py backend/tests/test_account_ui_contract.py backend/tests/test_account_admin_features.py -q`
  - `rtk node --check ui/account-ui/app.js`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/main.py backend/worker.py backend/repositories/ticket_repository.py backend/services/billing_automation.py`

## 2026-07-03 - Billing reply PDF attachment forwarding

- Area or subsystem: Account billing automation — Outlook inbox reply poller
- Prompt or model version: `billing-inbox-reply-followup-v3`
- Summary: Changed PDF handling for `[Billing Request]` replies from OCR-derived note input to direct customer-visible PDF attachment forwarding.
- Reason: Detailed invoice replies often include the invoice as a PDF attachment; the customer follow-up must attach that PDF instead of only producing text that says the invoice was emailed separately.
- Affected files or config:
  - `backend/services/billing_automation.py`
  - `backend/services/billing_response_flow.py`
  - `backend/services/asset_storage.py`
  - `backend/worker.py`
  - `deployment/docker-compose.single-host.yml`
  - `.env.example`
  - `backend/tests/test_billing_automation_email.py`
  - `backend/tests/test_billing_response_flow.py`
  - `backend/tests/test_single_host_compose.py`
  - `backend/tests/test_worker.py`
  - `docs/feature_list.md`
  - `docs/roadmap.html`
- Expected behavior change:
  - The poller downloads non-inline PDF attachments from matching unread Inbox replies without requiring or calling PaddleOCR.
  - The worker stores downloaded PDFs in the existing asset storage, attaches them to the customer-facing assistant message, and marks those assets attached after saving.
  - Detailed invoice customer follow-up text says the invoice is attached when a PDF is attached, instead of claiming it was sent to the customer's email.
  - If attachment storage or the handler fails, the Graph message is not marked read so the poller can retry.
- Verification:
  - RED: `rtk python3 -m unittest backend.tests.test_billing_automation_email.BillingAutomationEmailTests.test_poll_billing_request_replies_downloads_pdf_attachments_without_ocr_before_marking_read backend.tests.test_billing_automation_email.BillingAutomationEmailTests.test_poll_billing_request_replies_leaves_pdf_message_unread_when_handler_fails_after_download -v` failed before implementation because the poller required `PADDLEOCR_API_TOKEN`.
  - RED: `rtk bash -lc 'podman run --rm -v "$PWD":/app -w /app localhost/supportportal-app:f0f8f9f90055 python -m unittest backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_attaches_pdf_to_customer_message_without_ocr -v'` failed before implementation because the worker did not store or attach PDFs.
  - `rtk python3 -m unittest backend.tests.test_billing_automation_email backend.tests.test_billing_response_flow backend.tests.test_single_host_compose.SingleHostComposeTests.test_worker_aux_service_defaults_sentiment_provider_and_queue -v`
  - `rtk bash -lc 'podman run --rm -v "$PWD":/app -w /app localhost/supportportal-app:f0f8f9f90055 python -m unittest backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_generates_customer_followup backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_skips_duplicate_graph_message backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_rejects_empty_body_before_marking_read backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_uses_pdf_ocr_text_when_body_is_empty backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_attaches_pdf_to_customer_message_without_ocr -v'`
  - `rtk python3 -m py_compile backend/services/billing_automation.py backend/services/billing_response_flow.py backend/services/asset_storage.py backend/worker.py backend/tests/test_billing_automation_email.py backend/tests/test_billing_response_flow.py backend/tests/test_single_host_compose.py backend/tests/test_worker.py`

## 2026-07-03 - Billing reply PDF OCR follow-up input

- Area or subsystem: Account billing automation — Outlook inbox reply poller
- Prompt or model version: `billing-inbox-reply-followup-v2`
- Summary: Extended `[Billing Request]` reply handling so PDF attachments are OCR'd with AIStudio PaddleOCR and appended to the internal billing resolution note used for customer follow-up generation.
- Reason: Billing owners may reply with approval or invoice details inside PDF attachments; those details must be available to the automated customer follow-up path before the Graph message is marked read.
- Affected files or config:
  - `backend/services/billing_automation.py`
  - `backend/worker.py`
  - `deployment/docker-compose.single-host.yml`
  - `.env.example`
  - `backend/tests/test_billing_automation_email.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_single_host_compose.py`
  - `docs/feature_list.md`
  - `docs/roadmap.html`
- Expected behavior change:
  - The poller checks Graph `hasAttachments` for matching unread Inbox replies and downloads non-inline PDF attachments only.
  - PDF bytes are submitted to `PaddleOCR-VL-1.6`; completed JSONL markdown text is attached to `BillingRequestReply`.
  - OCR failure or missing OCR token raises before the Graph message is marked read, allowing retry after configuration or service recovery.
  - The worker now accepts PDF OCR text as billing reply content when the email body is empty.
- Verification:
  - RED: `rtk python3 -m unittest backend.tests.test_billing_automation_email.BillingAutomationEmailTests.test_poll_billing_request_replies_ocr_reads_pdf_attachments_before_marking_read backend.tests.test_billing_automation_email.BillingAutomationEmailTests.test_poll_billing_request_replies_leaves_pdf_message_unread_when_ocr_fails -v` failed before implementation because replies had no attachment OCR fields and PDF messages were still marked read.
  - `rtk python3 -m unittest backend.tests.test_billing_automation_email -v`
  - `rtk bash -lc 'podman run --rm -v "$PWD":/app -w /app localhost/supportportal-app:be60bf5a8fb3 python -m unittest backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_generates_customer_followup backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_skips_duplicate_graph_message backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_rejects_empty_body_before_marking_read backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_uses_pdf_ocr_text_when_body_is_empty -v'`
  - `rtk python3 -m py_compile backend/services/billing_automation.py backend/worker.py backend/tests/test_billing_automation_email.py backend/tests/test_worker.py`

## 2026-07-02 - Billing inbox reply customer follow-up

- Area or subsystem: Account billing automation — Outlook inbox reply poller
- Prompt or model version: `billing-inbox-reply-followup-v1`
- Summary: Changed the billing Outlook reply poller from record-only handling to automatic customer follow-up generation for `[Billing Request]` replies that reference a `TK-...` ticket.
- Reason: Internal billing replies processed from the company mailbox must continue the customer-facing automation flow instead of only being written to a JSONL audit record and marked read.
- Affected files or config:
  - `backend/worker.py`
  - `backend/tests/test_worker.py`
  - `docs/prompt_change_log.md`
  - `docs/feature_list.md`
- Expected behavior change:
  - The worker parses the client ticket id from billing reply subjects such as `Re: [Billing Request] ... Ticket TK-...`.
  - Matching replies are recorded, transformed into a customer-facing billing follow-up, saved as an assistant message on the original client ticket, and reflected on the linked billing ticket as `customer_notified`.
  - Empty reply bodies, missing ticket ids, or missing linked tickets fail the handler before the Graph message is marked read, so the poller can retry after correction.
- Verification:
  - RED/diagnosis: live `TK-ACC-A3377C` showed the prior poller recorded one matching email but did not append any customer-facing follow-up to the ticket.
  - `docker run --rm -v /home/ubuntu/SupportPortal/.worktrees/billing-email-reply-autoprocess:/app -w /app localhost/supportportal-app:unknown python -m unittest backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_generates_customer_followup backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_rejects_empty_body_before_marking_read -v`
  - `docker run --rm -v /home/ubuntu/SupportPortal/.worktrees/billing-email-reply-autoprocess:/app -w /app localhost/supportportal-app:unknown python -m unittest backend.tests.test_billing_automation_email backend.tests.test_worker.WorkerResilienceTests.test_billing_reply_poller_is_disabled_by_default backend.tests.test_worker.WorkerResilienceTests.test_billing_reply_poller_enabled_from_env backend.tests.test_worker.WorkerResilienceTests.test_start_billing_reply_poller_starts_daemon_thread_when_enabled backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_generates_customer_followup backend.tests.test_worker.WorkerResilienceTests.test_handle_billing_request_reply_rejects_empty_body_before_marking_read -v`
  - `python3 -m py_compile backend/worker.py backend/tests/test_worker.py`

## 2026-07-02 - Billing request subject routing prefix

- Area or subsystem: Deterministic billing intake internal email handoff
- Prompt or model version: `billing-automation-email-v4`
- Summary: Added a `[Billing Request]` subject prefix to billing automation internal handoff emails so Outlook rules can route replies into the `billing_automation` mailbox folder.
- Reason: Internal billing replies with attachments should be isolated from the general Inbox and prepared for a folder-scoped reply worker.
- Affected files or config:
  - `backend/services/billing_automation.py`
  - `backend/tests/test_support_router.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Internal handoff subjects now begin with `[Billing Request]` for account suspension, account verification, and detailed invoice requests.
  - The lower-level Graph send function still sends the subject provided by callers unchanged; only billing automation's internal payload builder adds the routing prefix.
- Verification:
  - RED: `rtk python3 -m unittest backend.tests.test_support_router.SupportRouterTests.test_resolve_support_message_builds_account_suspension_internal_email_when_ready backend.tests.test_support_router.SupportRouterTests.test_resolve_support_message_builds_detailed_invoice_internal_email_when_ready backend.tests.test_support_router.SupportRouterTests.test_resolve_support_message_records_billing_email_send_status_when_ready -v` failed before implementation because subjects did not include `[Billing Request]`.
  - `rtk python3 -m unittest backend.tests.test_support_router.SupportRouterTests.test_resolve_support_message_builds_account_suspension_internal_email_when_ready backend.tests.test_support_router.SupportRouterTests.test_resolve_support_message_builds_detailed_invoice_internal_email_when_ready backend.tests.test_support_router.SupportRouterTests.test_resolve_support_message_records_billing_email_send_status_when_ready -v`
  - `rtk python3 -m unittest backend.tests.test_billing_automation_email backend.tests.test_support_router -v`

## 2026-07-01 - Billing automation Outlook Graph sender

- Area or subsystem: Deterministic billing intake internal email handoff
- Prompt or model version: `billing-automation-email-v3`
- Summary: Replaced the billing automation internal email sender path with Microsoft Graph `/me/sendMail` for the company Outlook mailbox `ai-support-agent@agora.io`, and disabled legacy personal SMTP sending.
- Reason: Automated `/account` cases must send internal handoff emails from the company Outlook mailbox instead of a personal mailbox after customer information is confirmed.
- Affected files or config:
  - `backend/services/billing_automation.py`
  - `backend/tests/test_billing_automation_email.py`
  - `backend/tests/test_support_router.py`
  - `.env.example`
  - `.gitignore`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Internal billing email payloads default to `ai-support-agent@agora.io` as the sender.
  - `send_billing_internal_email()` only sends when Graph config and a usable token cache are available; missing Graph config or token cache records `skipped_config_missing`.
  - Legacy `BILLING_AUTOMATION_SMTP_*` settings are no longer used for billing automation, even if present.
  - `.msgraph/` is ignored so local Graph token caches are not committed.
- Verification:
  - RED: `rtk python3 -m unittest backend.tests.test_billing_automation_email -v` failed before implementation because the default sender was `xieziling97@163.com`, Graph sendMail was not called, and legacy SMTP still sent.
  - `rtk python3 -m unittest backend.tests.test_billing_automation_email -v`
  - `rtk python3 -m unittest backend.tests.test_support_router -v`
  - `rtk uv run --with fastapi --with 'pydantic==2.11.7' --with python-dotenv --with httpx --with 'psycopg[binary]' python -m unittest backend.tests.test_account_intake.AccountIntakeApiTests.test_account_intake_sends_internal_email_via_async_to_thread backend.tests.test_account_intake.AccountIntakeApiTests.test_billing_automation_reply_sends_internal_email_when_fields_complete backend.tests.test_account_intake.AccountIntakeApiTests.test_billing_automation_reply_invalidates_response_token_when_email_fails -v`
  - `rtk python3 -m py_compile backend/services/billing_automation.py backend/tests/test_billing_automation_email.py backend/tests/test_support_router.py`

## 2026-06-25 - Billing response link customer reply generation

- Area or subsystem: Account billing automation — `/response` internal handling result flow
- Prompt or model version: `billing-resolution-customer-reply-v1`
- Summary: Added a customer-facing billing reply prompt for response-link submissions so the internal handling note is treated as source material instead of being sent directly to the customer.
- Reason: Real response-link submissions can contain internal status notes such as "已经通过邮件发送给客户"; these notes must be transformed into customer-facing language before the AI notifies the customer.
- Affected files or config:
  - `backend/main.py`
  - `backend/services/billing_response_flow.py`
  - `ui/billing-response-ui/app.js`
  - `backend/tests/test_account_intake.py`
  - `backend/tests/test_billing_response_flow.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - When `notify_customer=true`, `/api/billing-response/submit` now gives the original customer context, selected result, and internal resolution details to the `billing_reply` LLM profile and sends the generated customer-facing reply.
  - The internal resolution details are no longer copied verbatim into customer messages; deterministic scenario replies remain as fallback when the model is unavailable or returns an unusable reply.
  - The response-link UI now labels the textarea as internal handling details and warns that AI will rewrite it before customer notification.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_billing_response_flow.py backend/tests/test_account_intake.py backend/tests/test_billing_response_ui_contract.py -q`

## 2026-06-24 - Account verification missing-field reply tone

- Area or subsystem: Account billing automation — customer-facing account verification intake replies
- Prompt or model version: `billing-account-verification-missing-fields-v2` + `billing-reply-humanizer-v1`
- Summary: Updated account verification missing-field replies to reuse the client-style email framing (`Hi {requester}, ... Sid`), ask for one or two missing fields inline, switch to a details list for three or more fields, and add a narrow `billing_reply` model profile for optional tone polishing with `BILLING_REPLY_TEMPERATURE` defaulting to `0.5`.
- Reason: `TK-ACC-6856BF` showed the previous deterministic missing-field template was too rigid and always rendered a generic list, even when the customer only needed to provide one item such as use case.
- Affected files or config:
  - `backend/services/billing_automation.py`
  - `backend/services/llm_profiles.py`
  - `backend/main.py`
  - `backend/services/support_router.py`
  - Env: `BILLING_REPLY_MODEL`, `BILLING_REPLY_REASONING_EFFORT`, `BILLING_REPLY_TEMPERATURE`, `BILLING_REPLY_TIMEOUT_SECONDS`, `BILLING_REPLY_MAX_RETRIES`
- Expected behavior change:
  - Account verification replies now greet the customer when a usable requester is available, ask only for the missing account verification fields, and end with `Thanks in advance!` followed by `Sid`.
  - When one field is missing, Sid asks inline for that field; when two fields are missing, Sid asks inline with `and`; when three or more are missing, Sid renders a concise details list ordered as `Use Case`, `Address`, `Phone number`, then remaining fields.
  - If configured model credentials are present, the billing reply humanizer may polish tone at higher temperature while guardrails reject outputs that remove required fields, escalation wording, greeting, or sign-off.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_support_router.py backend/tests/test_account_intake.py backend/tests/test_llm_profiles.py -q`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/billing_automation.py backend/services/llm_profiles.py backend/main.py backend/services/support_router.py`

## 2026-06-20 - Enable local GraphRAG KG model-provider config

- Area or subsystem: Client AI RAG retrieval — KG auxiliary GraphRAG provider config
- Prompt or model version: `kg-graphrag-local-provider-config-v1`
- Summary: Added end-to-end KG LLM/embedding provider configuration for the local GraphRAG runtime. `KG_LLM_*` and `KG_EMBEDDING_*` envs now override the runtime explicitly; when unset, the runtime falls back to the existing DeepSeek chat and SiliconFlow embedding envs. Vendored GraphRAG now accepts `embedding_api_key` and passes it to `OpenAIEmbedderConfig` instead of hardcoding `ollama`.
- Reason: The local online RAG+KG path needs to use the same configured OpenAI-compatible providers as the rest of SupportPortal, otherwise enabling the KG flag can silently construct a graph runtime with an unusable embedding credential.
- Affected files or config:
  - `.env.example`
  - `.env.local.example`
  - `backend/services/kg_graphrag_runtime.py`
  - `deployment/docker-compose.single-host.local-lightweight.yml`
  - `vendor/cusmem/graphiti_rag/config.py`
  - `vendor/cusmem/graphiti_rag/config_loader.py`
  - `vendor/cusmem/graphiti_rag/graph_rag.py`
  - Env: `KG_LLM_API_KEY` / `KG_LLM_BASE_URL` / `KG_LLM_MODEL`, `KG_EMBEDDING_API_KEY` / `KG_EMBEDDING_MODEL` / `KG_EMBEDDING_BASE_URL` / `KG_EMBEDDING_DIM`.
- Expected behavior change:
  - Local lightweight RAG+KG can construct the vendored GraphRAG runtime using configured LLM and embedding providers when `RAG_KG_AUXILIARY_ENABLED=true`.
  - KG-specific envs take precedence; existing DeepSeek/SiliconFlow envs are reused when KG-specific envs are blank.
  - Production/default `.env` remains gated with `RAG_KG_AUXILIARY_ENABLED=false`.
- Verification:
  - `rtk python -m pytest backend/tests/test_vendor_graphrag_config.py backend/tests/test_kg_graphrag_runtime.py::TestFactoryGating::test_graphrag_config_from_env_falls_back_to_existing_model_envs backend/tests/test_kg_graphrag_runtime.py::TestFactoryGating::test_kg_specific_env_overrides_shared_model_envs -q`
  - `rtk python -m py_compile backend/services/kg_graphrag_runtime.py vendor/cusmem/graphiti_rag/config.py vendor/cusmem/graphiti_rag/config_loader.py vendor/cusmem/graphiti_rag/graph_rag.py`

## 2026-06-19 - Wire live GraphRAG KG runtime client (tooling-mode/provider)

- Area or subsystem: Client AI RAG retrieval — KG auxiliary runtime backend
- Prompt or model version: `kg-graphrag-runtime-client-v1`
- Summary: Added a live `KgRuntimeClient` (`GraphRagKgRuntimeClient` + `GraphitiSearchBackend`) backed by the vendored cusmem GraphRAG graph and installed it best-effort at `rag_api` startup via `maybe_install_default_kg_client()`. The three online KG hooks (query expansion / rerank boost / structured fact) can now consult a real knowledge graph instead of always degrading against the `KgRuntimeDisabled` no-op.
- Reason: Close the last runtime gap in the RAG+KG plan — the hooks and offline ingest existed but no real backend was ever registered, so KG was a no-op even with the flag on. This wires the graph in while keeping RAG as the citation source of truth and preserving the degrade-to-pure-RAG guarantees.
- Affected files or config:
  - `backend/services/kg_graphrag_runtime.py` (new)
  - `backend/rag_api.py`
  - `backend/tests/test_kg_graphrag_runtime.py`
  - Env: `RAG_KG_AUXILIARY_ENABLED` (gate, default off), `KG_NEO4J_URI`/`KG_NEO4J_USER`/`KG_NEO4J_PASSWORD` (fallback `NEO4J_*`), `KG_SEARCH_NUM_RESULTS`, `KG_LLM_*` / `KG_EMBEDDING_*`.
- Expected behavior change:
  - With `RAG_KG_AUXILIARY_ENABLED=true` AND a reachable Neo4j-backed KG graph, the Client AI online RAG path consults KG for entity-link expansion, rerank boost (scoped to RAG candidates), and structured facts (scoped to selected RAG chunks, non-citable). Provenance-less hits are dropped.
  - With the flag off (default) or no graph configured, the no-op client stays installed and behavior is identical to the pure-RAG chain. KG never adds citations or new chunks; any KG step timing out/failing degrades to pure RAG.
- Verification:
  - `rtk uv run --with redis python -m unittest backend.tests.test_kg_graphrag_runtime backend.tests.test_kg_runtime`
  - `rtk uv run --with redis python -m unittest backend.tests.test_roadmap_contract`

## 2026-06-19 - Billing internal resolution follow-up

- Area or subsystem: Billing automation response follow-up
- Prompt or model version: `billing-internal-resolution-followup-v1`
- Summary: Added the structured internal resolution loop where a billing handler submits a one-time response-link form, the backend records `billing_internal_resolution_submitted`, and the customer follow-up path records `billing_customer_followup_generated` when customer notification is requested.
- Reason: Close the billing internal handling loop without parsing free-form email replies, while preserving an auditable event boundary before any customer-facing reply is generated.
- Affected files or config:
  - `backend/main.py`
  - `backend/services/billing_response_flow.py`
  - `backend/services/billing_automation.py`
  - `ui/billing-response-ui/`
- Expected behavior change:
  - Internal emails include a one-time `/response?token=...` link for structured billing handling results.
  - `completed` allows an empty note; `refused` and `customer_action_required` require a note before token consumption.
  - `notify_customer=false` writes only the internal resolution event; `notify_customer=true` appends a `billing_response_ai` customer message and writes a follow-up event.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_intake backend.tests.test_billing_response_flow backend.tests.test_billing_response_ui_contract backend.tests.test_roadmap_contract`

## 2026-06-18 - Roadmap maintenance rule for major changes

- Area or subsystem: Agent workflow / roadmap maintenance
- Prompt or model version: `roadmap-maintenance-rule-v1`
- Summary: Renamed the QBR tracker maintenance surface to `docs/roadmap.html` and updated Codex/Claude/review instructions so `功能类/重大行为变更` must keep the Roadmap overall rollout / `整体落地进度` current when delivery status, phase gates, or tracked capabilities materially change.
- Reason: Major SupportPortal changes need one landing-oriented Roadmap page in addition to the feature list, so reviewers can catch stale phase status and rollout plans during implementation review.
- Affected files or config:
  - `AGENTS.md`
  - `CLAUDE.md`
  - `.codex/skills/review-implemented-plan/SKILL.md`
  - `docs/roadmap.html`
  - `docs/qbr_plan.html`
  - `backend/tests/test_roadmap_contract.py`
- Expected behavior change:
  - Future major or feature-level changes should update `docs/roadmap.html` when they affect overall delivery progress, phase gates, or tracked capability status.
  - Review handoffs now include an explicit Roadmap freshness check for `功能类/重大行为变更`.
- Verification:
  - `rtk python3 -m unittest backend.tests.test_roadmap_contract -v`
  - `rtk rg -n "docs/roadmap.html|功能类/重大行为变更|整体落地" AGENTS.md CLAUDE.md .codex/skills/review-implemented-plan/SKILL.md docs/prompt_change_log.md`

## 2026-06-18 - Router threshold audit and fallback observability

- Area or subsystem: Routing / LLM intent router observability
- Prompt or model version: N/A (behavioral change to routing pipeline, no prompt change)
- Summary: Added router audit fields to `SupportRouteDecision`, `SupportResolution`, and `TicketExecutionResult` to distinguish fallback reasons: `below_confidence_threshold`, `missing_credentials`, `llm_invocation_failed`, `invalid_json`, `invalid_payload`, `route_fail_open`. `router_source` now uses `"conservative_fallback"` (was implicit `"deterministic"`) for final fallback; only `"llm_semantic"` counts as model decision. Audit fields are propagated through route events, billing API responses, and runtime execution payloads.
- Reason: Previously `conservative_agora_technical_fallback` could be triggered by LLM confidence below threshold, LLM invocation failure, missing credentials, or invalid responses — all indistinguishable in dashboards/QBR, leading to misreading fallback as model judgment.
- Affected files or config:
  - `backend/services/support_router.py` — added `_LlmRouteAttempt`, audit fields on `SupportRouteDecision` and `SupportResolution`, `_llm_route_decision` returns failure metadata, `decide_support_route` populates audit fields
  - `backend/services/client_ticket_agent_runtime.py` — `_build_default_rag_route_decision` marks `route_fail_open`, `TicketExecutionResult` carries audit fields, route agent events include audit payload
  - `backend/main.py` — billing/account event and API response include audit fields
  - `docs/qbr_plan.html` — `routing-threshold-audit` moved to done, Routing tab note updated with fallback rate conventions
- Expected behavior change:
  - `router_source` on fallback decisions now reads `"conservative_fallback"` (was `"deterministic"`)
  - `intent_router_fallback_reason` populated on every fallback; `intent_router_failure_type` and `_failure_source` set on LLM failures
  - `_build_default_rag_route_decision` (runtime fail-open) now correctly reports `router_source="conservative_fallback"` with `fallback_reason="route_fail_open"`
  - Dashboard/QBR can now separately count: LLM semantic pass rate, below-threshold fallback rate, LLM failure fallback rate, runtime fail-open rate
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_support_router backend.tests.test_support_router_semantic_billing -v`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_intake backend.tests.test_qbr_plan_contract -v`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_client_ticket_agent_runtime.ClientTicketAgentRuntimeContractTests.test_resolved_confirmation_route_failure_falls_back_to_resolution_for_engineer_guidance_reply -v`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/support_router.py backend/services/client_ticket_agent_runtime.py backend/main.py backend/tests/test_support_router.py backend/tests/test_support_router_semantic_billing.py backend/tests/test_account_intake.py`

## 2026-06-17 - KG schema bridge feeds into Graphiti extraction prompts

- Area or subsystem: KG offline ingest / GraphRAG extraction
- Prompt or model version: `supportportal_official_docs_v1` schema via `kg_graphrag_adapter.convert_schema_to_cusmem_mapping()`
- Summary:
  - The SupportPortal `KgSchema` (9 entities + 10 edges, strict mode) is now converted into vendored cusmem entity_types/edge_types Pydantic models via `convert_schema_to_cusmem_mapping()` and `load_graph_schema_from_mapping()`.
  - When the adapter builds vendored `Chunk` objects for `GraphRAG.ingest_chunks()`, the cusmem `Extractor` receives these entity/edge types and passes them to `graphiti.add_episode()`, where they enter the Graphiti extraction prompts (entity extraction + edge extraction LLM calls).
  - Schema mode (strict) is preserved so unknown entity/edge types are rejected by Graphiti's schema validation during extraction.
- Reason:
  - PR2 wires the schema bridge so the official-docs KG schema controls what entity types and edge types the LLM can extract, consistent with the v1 strict-mode schema contract.
- Affected files or config:
  - `backend/services/kg_graphrag_adapter.py` (new — `convert_schema_to_cusmem_mapping()`)
  - `vendor/cusmem/graphiti_rag/schema_loader.py` (modified — `load_graph_schema_from_mapping()`)
  - `vendor/cusmem/graphiti_rag/components.py` (modified — Extractor passes entity_types/edge_types from schema)
  - `docs/prompt_change_log.md` (this entry)
- Expected behavior change:
  - KG extraction prompts now include the official-docs entity types (Product, API, Feature, ErrorCode, Symptom, Solution, Limitation, Platform, Version) and edge types (PROVIDES_API, SUPPORTS_PLATFORM, etc.) with their descriptions.
  - Schema hash changes (e.g. when prompt-facing descriptions or edge constraints change) are tracked in episode metadata so downstream can detect schema drift.
  - No change to the customer-facing RAG answer prompts; this only affects the Graphiti extraction prompt path.
- Verification:
  - `rtk pytest backend/tests/test_kg_graphrag_adapter.py backend/tests/test_kg_offline_ingest.py backend/tests/test_kg_supportportal_contracts.py backend/tests/test_kg_schema.py backend/tests/test_kg_official_docs_scope.py backend/tests/test_qbr_plan_contract.py -q` (79 passed)
  - `cd vendor/cusmem && rtk uv run --with pytest --with pyyaml python -m pytest tests/test_supportportal_chunk_ingest.py tests/test_schema_loader.py tests/test_core_pipeline.py -q` (16 passed, 2 pytest config warnings)
  - `rtk uv run --with ruff ruff check backend/services/kg_*.py backend/tests/test_kg_*.py scripts/kg_ingest_official_doc_chunks.py` (All checks passed)
  - `cd vendor/cusmem && rtk uv run --with ruff ruff check graphiti_rag/components.py graphiti_rag/pipeline.py graphiti_rag/graph_rag.py graphiti_rag/ingest_state.py graphiti_rag/schema_loader.py graphiti_rag/config.py graphiti_rag/config_loader.py graphiti_core/graphiti.py graphiti_core/nodes.py graphiti_core/models/nodes/node_db_queries.py tests/test_supportportal_chunk_ingest.py tests/test_schema_loader.py` (All checks passed)

## 2026-06-17 - Review process moved into project skill

- Area or subsystem: Agent collaboration instructions and workflow prompts
- Prompt or model version: `review-implemented-plan-skill-v1`
- Summary: Moved the completed-implementation review/finalization process out of `AGENTS.md` and `CLAUDE.md` into the project-local `review-implemented-plan` skill, triggered by phrases such as `实现了计划，你来review一下` or similar post-implementation review requests.
- Reason: Keep hot-path agent files shorter while preserving the full review ownership, fix, verification, finalization, and cleanup workflow behind an explicit skill trigger.
- Affected files or config:
  - `.codex/skills/review-implemented-plan/SKILL.md`
  - `.codex/skills/review-implemented-plan/agents/openai.yaml`
  - `AGENTS.md`
  - `CLAUDE.md`
  - `docs/agent_workflow_details.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Codex and Claude Code load the review process only when a completed implementation, finished plan, worker handoff, or local diff review request triggers the skill.
  - `AGENTS.md` and `CLAUDE.md` retain only a short skill pointer and no longer duplicate the review/finalization process.
  - Reasonix remains handoff-only and waits for Codex or Claude Code review.
- Verification:
  - `python3 /Users/xieziling/.codex/skills/.system/skill-creator/scripts/quick_validate.py .codex/skills/review-implemented-plan`
  - `git diff --check`
  - `rg -n "实现了计划，你来review一下|review this implementation|review-implemented-plan|completed implementation|worker handoff" .codex/skills/review-implemented-plan AGENTS.md CLAUDE.md docs/agent_workflow_details.md`
  - Python consistency check that `AGENTS.md` and `CLAUDE.md` no longer contain duplicated review-process ownership steps.

## 2026-06-16 - KG official docs schema v1

- Area or subsystem: KG / GraphRAG extraction schema
- Prompt or model version: `supportportal_official_docs_v1`
- Summary: Added the first SupportPortal official-docs KG schema with 9 entity types (`Product`, `API`, `Feature`, `ErrorCode`, `Symptom`, `Solution`, `Limitation`, `Platform`, `Version`) and 10 strict edge types for future GraphRAG entity/relationship extraction.
- Reason: The RAG+KG plan requires schema-flexible extraction to be narrowed before any GraphRAG adapter or runtime KG auxiliary signal is enabled.
- Affected files or config:
  - `backend/config/kg/supportportal_official_docs_v1.yaml`
  - `backend/services/kg_schema.py`
  - `backend/tests/test_kg_schema.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - No online behavior changes in PR1 because the schema is not yet wired to GraphRAG, Neo4j, or RAG runtime.
  - Future KG extraction using this schema will be constrained to the documented official-doc entity and edge types instead of open-ended schema-flexible extraction.
- Verification:
  - `rtk pytest backend/tests/test_kg_official_docs_scope.py backend/tests/test_kg_supportportal_contracts.py backend/tests/test_kg_schema.py backend/tests/test_qbr_plan_contract.py -q` (54 passed)
  - `rtk uv run --with ruff ruff check backend/services/kg_*.py backend/tests/test_kg_*.py` (All checks passed)

## 2026-06-16 - Codex and Claude Code review parity

- Area or subsystem: Agent collaboration instructions and workflow prompts
- Prompt or model version: `agent-review-parity-v1`
- Summary: Changed the collaboration rules so Codex and Claude Code are peer repository agents that may review, modify code/docs, verify, commit, create or reuse PRs, finalize, and clean up when assigned; Reasonix remains the handoff-only implementation worker that waits for Codex or Claude Code review.
- Reason: The previous rules treated Claude Code as an implementation worker that had to stop before commit and wait for Codex review. The updated workflow allows both Codex and Claude Code to own review and code changes, while preserving Reasonix as the review-gated worker path.
- Affected files or config:
  - `AGENTS.md`
  - `CLAUDE.md`
  - `REASONIX.md`
  - `docs/agent_workflow_details.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Claude Code no longer has a default stop-before-commit or `/codex:review` handoff requirement.
  - Codex and Claude Code can both review Reasonix handoffs and continue through finalization when assigned.
  - Reasonix still stops before commit/PR/merge/finalization/cleanup and waits for Codex or Claude Code review.
- Verification:
  - `git diff --check`
  - `rg -n "Claude Code|Reasonix|handoff|review|commit|finalize|peer repository agents|handoff-only" AGENTS.md CLAUDE.md REASONIX.md docs/agent_workflow_details.md`
  - Python consistency check for Claude/Reasonix boundary wording.

## 2026-06-15 - LLM semantic router + policy gate

- Area or subsystem: Intent router prompt and routing architecture
- Prompt or model version: `semantic-router-intent-taxonomy-v1`
- Summary: Router prompt changed from scope-only classification to semantic intent + policy-aware structured output. New output schema includes `semantic_intent` (billing.account_suspension / billing.detailed_invoice / billing.refund_or_dispute), `recommended_action`, `automation_eligibility`, `evidence_spans`, and `risk_flags`. Added `_apply_policy_gate()` that splits routing ("what is this?") from automation eligibility ("can this be automated?"). Expanded billing deterministic whitelist to cover `Account temporarily suspended`, `account has been suspended`, `suspended due to insufficient balance`, and other real-world expressions. Added `billing_review` route family for billing cases that need human review.
- Reason: TK-ACC-68BAC7 (`Account temporarily suspended`) was being routed to `web_search` because the deterministic regex didn't cover "temporarily suspended" variations and the LLM classified it as `agora_non_technical`. The new architecture ensures billing/account-status semantics are correctly classified and separates semantic intent from automation policy.
- Affected files or config:
  - `backend/services/support_router.py` — Extended `SupportRouteDecision` with 7 new fields, added `_apply_policy_gate()`, updated `_llm_route_decision` and `_build_route_decision`
  - `backend/services/support_router_prompt.py` — Added billing few-shot examples for account_suspension, detailed_invoice, refund/dispute
  - `backend/services/prompts/router.py` — Updated system prompt with Billing Intent Taxonomy, Automation Eligibility rules
  - `backend/services/billing_automation.py` — Expanded `_ACCOUNT_SUSPENSION_PATTERNS` with 3 new patterns
  - `backend/main.py` — Added semantic fields to account intake API response, billing ticket, and event
  - `backend/sql/ticket_storage.sql` — Added semantic routing audit columns to `support_billing_tickets`
  - `backend/repositories/ticket_repository.py` — Persists semantic routing audit columns for Postgres billing tickets
  - `backend/tests/test_account_intake.py` — Covers `billing_review` staying `not_automated`
  - `backend/tests/test_repository_configuration.py` — Covers billing ticket semantic routing storage contract
  - `backend/tests/test_support_router_semantic_billing.py` — New golden regression test file (12 cases)
  - `backend/tests/test_qbr_plan_contract.py` — Updated Routing rules contract terms
  - `docs/qbr_plan.html` — Updated Routing tab with new architecture
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - `Account temporarily suspended` and similar account access/balance suspension cases now route to `billing_automation` or `billing_review`, not `web_search`.
  - LLM `billing.detailed_invoice` responses that use `recommended_action=automation_candidate` are normalized to `billing_automation/detailed_invoice` by the policy gate instead of falling through to `refuse`.
  - `billing_review/human_review_required` stays `automation_status=not_automated` in account intake.
  - Billing semantic intent is visible even when automation is blocked (`not_automated_reason` always populated).
  - LLM output is validated and bounded by a policy gate: refund/dispute/legal cases are never automated.
  - New optional fields on route decision: `semantic_intent`, `automation_eligibility`, `policy_decision`, `not_automated_reason`, `risk_flags`, `evidence_spans`, `router_source`.
  - Existing RAG / web_search / refuse routes continue to work unchanged.
- Verification:
  - `python3 -m unittest backend.tests.test_support_router` (50 tests)
  - `python3 -m unittest backend.tests.test_support_router_semantic_billing` (12 tests)
  - `python3 -m unittest backend.tests.test_qbr_plan_contract` (7 tests)

## 2026-06-15 - Agent preflight on-demand rules

- Area or subsystem: Agent collaboration instructions and workflow prompts
- Prompt or model version: `agent-preflight-on-demand-v1`
- Summary: Changed project-level agent preflight from fixed startup checks to on-demand triggers for AgentMemory, skill files, CodeGraph status, and Git/worktree state while explicitly preserving platform skill trigger requirements.
- Reason: The previous hot-path instructions still encouraged repeated preflight reads even after the context split, increasing token usage and risking unnecessary pauses. The new wording keeps safety checks for repo edits/finalization while avoiding default memory, skill, CodeGraph-status, and Git-status reads for ordinary chat or planning.
- Affected files or config:
  - `AGENTS.md`
  - `CLAUDE.md`
  - `REASONIX.md`
  - `docs/agent_workflow_details.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Agents no longer treat AgentMemory lookup, `using-superpowers` file reads, `codegraph_status`, or Git/worktree status as fixed project-level startup steps.
  - Applicable platform or host skill triggers remain mandatory; on-demand preflight must not be interpreted as permission to skip matching skills.
  - Git/worktree status checks remain required for repo-tracked edits, resume/finalize/cleanup, and workspace-safety decisions.
- Verification:
  - `git diff --check`
  - `rg -n "superpowers|skill|using-superpowers|must use|按需|skip|跳过" AGENTS.md docs/agent_workflow_details.md CLAUDE.md REASONIX.md`
  - `rg -n "AgentMemory|agentmemory|codegraph_status|git status|git worktree list" AGENTS.md docs/agent_workflow_details.md`
  - `rg -n "root workspace|\.worktrees|not blockers|finalize_task_to_main|CodeGraph|rtk|PR-only|squash" AGENTS.md docs/agent_workflow_details.md`
  - Python size/headings check for `AGENTS.md`.

## 2026-06-15 - Agent instruction context split

- Area or subsystem: Agent collaboration instructions and workflow prompts
- Prompt or model version: `agent-workflow-context-split-v1`
- Summary: Condensed `AGENTS.md` into hot-path rules and moved low-frequency branch, finalization, verification, worker handoff, and logging details into `docs/agent_workflow_details.md`; added Claude Code and Reasonix references to the new details file.
- Reason: The full workflow instructions were consuming too much context on every agent turn while many details are only needed for specific workflow edge cases.
- Affected files or config:
  - `AGENTS.md`
  - `CLAUDE.md`
  - `REASONIX.md`
  - `docs/agent_workflow_details.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Agents load a shorter `AGENTS.md` for common tasks and consult `docs/agent_workflow_details.md` only when triggered by finalization, stack verification, worker handoff, logging, or workflow edge cases.
  - Existing root-main, `.worktrees`, PR/squash-merge, CodeGraph, logging, and worker-boundary requirements remain unchanged.
- Verification:
  - `git diff --check`
  - `rg -n "docs/agent.md|legacy compatibility" AGENTS.md docs/agent_workflow_details.md`
  - `rg -n "root workspace|\.worktrees|not a blocker|finalize_task_to_main|rag_change_log|prompt_change_log|feature_list" AGENTS.md docs/agent_workflow_details.md`
  - Python size/headings check for `AGENTS.md` and migrated detail sections.

## 2026-06-12 - Account intake routing behaviour change: unified ticket ID, no automation execution

- Area or subsystem: Account-side ticket intake, support routing
- Prompt or model version: `account-intake-v2`
- Summary: Unified external ticket identity to a single `ticket_id` across `/account` API, billing ticket view-model, and account UI. Changed billing route behaviour so `detailed_invoice` and `account_suspension` routes now mark `automation_status = "automation"` without executing the full billing automation pipeline (no `resolve_support_message()`, no customer reply generation, no missing-field collection, no internal email send). Non-billing routes continue to record `not_automated` with full route metadata.
- Reason: Deterministic routing change — account intake should separate route classification from automation execution. External ticket identity should be a single canonical `ticket_id` rather than exposing `support_ticket_id` as a second identity.
- Affected files or config:
  - `backend/main.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/tests/test_account_intake.py`
  - `backend/tests/test_account_ui_contract.py`
  - `ui/account-ui/app.js`
  - `ui/account-ui/styles.css`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - `POST /account` no longer returns `support_ticket_id`; only `ticket_id` and `billing_ticket_id`.
  - `detailed_invoice` and `account_suspension` requests now receive `status = "automation"`, empty `customer_reply`, empty `missing_fields`, and `internal_email_send_status = "not_applicable"`.
  - Non-whitelist account submissions remain `not_automated` while preserving the route result and route metadata.
  - `/api/account/billing-tickets/{id}` now supports direct lookup by canonical `ticket_id` (e.g. `TK-ACC-XXXXXX`) in addition to `BT-...` billing ticket IDs.
  - Account UI shows a single `Ticket ID` row in detail view, uses `ticket_id` as the primary key for history navigation, and displays `Automation` / `Not automated` status labels.
- Verification:
  - `python -m unittest backend.tests.test_account_intake backend.tests.test_account_ui_contract`
  - `node --check ui/account-ui/app.js`

## 2026-06-10 - Account billing intake endpoint

- Area or subsystem: Account-side ticket intake, support routing, and deterministic billing workflow
- Prompt or model version: `account-billing-intake-v1`
- Summary: Added a `/account` intake path that creates client tickets from HTTP or manual UI submissions, routes `title + question`, and runs the existing billing automation process for `detailed_invoice` and `account_suspension`.
- Reason: Account-side requests should preserve the client ticket experience while letting billing whitelist cases enter the same field collection, escalation acknowledgement, and internal email workflow.
- Affected files or config:
  - `backend/main.py`
  - `backend/tests/test_account_intake.py`
  - `backend/tests/test_account_ui_contract.py`
  - `ui/account-ui/index.html`
  - `ui/account-ui/app.js`
  - `ui/account-ui/styles.css`
  - `docs/billing_automation_plan.html`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - `POST /account` creates a client ticket before routing and returns `status`, `route`, `ticket_id`, `customer_reply`, `missing_fields`, and `internal_email_send_status`.
  - `detailed_invoice` and `account_suspension` requests use existing billing route/process behavior, including missing-field prompts and internal email send metadata.
  - Non-whitelist account submissions remain as tickets with `not_automated` status and do not send internal email.
  - `/account` serves a client-style manual submission UI backed by the same endpoint.
- Verification:
  - RED: `backend.tests.test_account_intake` failed with 404 before `POST /account` existed.
  - RED: `backend.tests.test_account_ui_contract` failed because `ui/account-ui` files did not exist.
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_intake backend.tests.test_account_ui_contract`
  - `node --check ui/account-ui/app.js`

## 2026-06-10 - Engineer case auto HITL review

- Area or subsystem: Engineer investigation closure and AI learning feedback
- Prompt or model version: `engineer-hitl-auto-review-v1`
- Summary: Added a closed-case AI review step that generates structured `support_engineer_hitl_feedback` records after an engineer approves and closes an investigation.
- Reason: Engineer AI learning feedback should come from the full closed investigation history, not from a manual form during active investigation.
- Affected files or config:
  - `backend/services/engineer_hitl_review.py`
  - `backend/main.py`
  - `backend/tests/test_engineer_hitl_review.py`
  - `ui/engineer-ui/app.js`
  - `ui/engineer-ui/styles.css`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_engineer_ui_contract.py`
  - `docs/engineer_ai_evolution_plan.html`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Approving an engineer investigation now records one deterministic `hitl_auto_<engineer_case_id>` learning review candidate.
  - The engineer UI no longer exposes manual HITL feedback fields; active cases show a pending-after-close message and closed cases show the read-only auto review.
  - The review remains an eval/memory candidate only and is not written to long-term memory.
- Verification:
  - RED: New backend test failed before implementation because `build_engineer_auto_hitl_feedback` did not exist.
  - RED: New UI contract test failed before implementation because the feedback panel still rendered the old manual form.
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_engineer_hitl_review backend.tests.test_investigation_flow.InvestigationFlowTests.test_confirmation_approve_sends_customer_reply_and_closes_investigation backend.tests.test_engineer_ui_contract.EngineerUiContractTests.test_engineer_detail_shows_read_only_auto_hitl_review -v`

## 2026-06-10 - Billing destination email variables

- Area or subsystem: Deterministic billing intake templates and internal email handoff
- Prompt or model version: `billing-automation-email-v2`
- Summary: Split billing automation internal email destinations into `BILLING_AUTOMATION_ACCOUNT_SUSPENSION_EMAIL` and `BILLING_AUTOMATION_DETAILED_INVOICE_EMAIL`, while keeping the generic destination as a fallback.
- Reason: Account suspension and detailed invoice handoffs may need separate routing later, but both should currently send to `xieziling@agora.io`.
- Affected files or config:
  - `backend/services/billing_automation.py`
  - `backend/tests/test_support_router.py`
  - `.env.example`
  - `docs/billing_automation_plan.html`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - `account_suspension` internal email payloads read `BILLING_AUTOMATION_ACCOUNT_SUSPENSION_EMAIL` first.
  - `detailed_invoice` internal email payloads read `BILLING_AUTOMATION_DETAILED_INVOICE_EMAIL` first.
  - If an action-specific destination is unset, the payload falls back to `BILLING_AUTOMATION_INTERNAL_EMAIL`, then the built-in `xieziling@agora.io` default.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_support_router`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/billing_automation.py backend/tests/test_support_router.py`
  - `rg -n "BILLING_AUTOMATION_ACCOUNT_SUSPENSION_EMAIL|BILLING_AUTOMATION_DETAILED_INVOICE_EMAIL|xieziling@agora.io|xieziling97@163.com" backend docs .env.example`

## 2026-06-10 - Engineer investigation opening evidence context

- Area or subsystem: Engineer investigation handoff and evidence tool routing
- Prompt or model version: `engineer-evidence-orchestration-v1`
- Summary: New engineer investigations now receive sanitized internal-first evidence summaries in the opening engineer AI context, with official fallback evidence included separately when available.
- Reason: When Client AI cannot safely answer from official docs or a case is explicitly escalated, Engineer AI should begin from internal/non-official evidence while keeping customer-facing drafts free of internal source details.
- Affected files or config:
  - `backend/services/engineer_evidence_tools.py`
  - `backend/services/investigation_flow.py`
  - `backend/services/engineer_agent.py`
  - `backend/main.py`
  - `backend/tests/test_engineer_evidence_tools.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - Engineer opening messages include internal evidence and official fallback summaries when the server-side evidence builder attaches them to the handoff packet.
  - Internal evidence citations and source details stay out of the serialized handoff payload used for customer-safe context.
  - Existing follow-up messages in an already active investigation do not rerun the engineer evidence search.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_engineer_evidence_tools.py backend/tests/test_investigation_flow.py::InvestigationFlowTests::test_start_investigation_attaches_engineer_evidence_to_opening_context backend/tests/test_investigation_flow.py::InvestigationFlowTests::test_main_engineer_evidence_builder_uses_ticket_handoff_context backend/tests/test_investigation_flow.py::InvestigationFlowTests::test_engineer_case_context_preserves_customer_identity_for_evidence_search -q`

## 2026-06-10 - Billing internal email sender configuration

- Area or subsystem: Deterministic billing intake templates and internal email handoff
- Prompt or model version: `billing-automation-email-v1`
- Summary: Added SMTP-backed internal email sending for completed billing automation cases, with default recipient `xieziling@agora.io`, default sender `xieziling97@163.com`, and explicit send-status metadata.
- Reason: Billing automation should move from preparing an internal email payload to attempting the internal handoff when required fields are complete, while failing closed when SMTP credentials are missing.
- Affected files or config:
  - `backend/services/billing_automation.py`
  - `backend/services/support_router.py`
  - `backend/tests/test_support_router.py`
  - `docs/billing_automation_plan.html`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Completed `account_suspension` and `detailed_invoice` cases now prepare internal emails from `xieziling97@163.com` to `xieziling@agora.io`.
  - When `BILLING_AUTOMATION_SMTP_PASSWORD` is configured, the backend sends via SMTP SSL using `smtp.163.com:465` by default.
  - When SMTP credentials or email payload fields are missing, the backend does not send and records `skipped_config_missing` with the reason in route metadata.
- Verification:
  - RED: New billing email tests failed before implementation because `send_billing_internal_email` did not exist and route metadata did not include send status.
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_support_router`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/billing_automation.py backend/services/support_router.py backend/tests/test_support_router.py`
  - `rg -n "xieziling@agora.io|xieziling97@163.com|billing_internal_email_send_status|BILLING_AUTOMATION_SMTP_PASSWORD" backend docs`

## 2026-06-10 - Billing automation route items

- Area or subsystem: Intent routing and deterministic billing intake templates
- Prompt or model version: `billing-automation-route-v1`
- Summary: Added deterministic `billing` route items for `account_suspension` and `detailed_invoice`, with fixed missing-field prompts, escalation acknowledgement templates, and internal email payload templates.
- Reason: Billing automation v1 should only handle two strict whitelist cases, collect required fields, tell the customer the case was escalated to the internal team, and prepare an internal email without using RAG or free-form model generation.
- Affected files or config:
  - `backend/services/billing_automation.py`
  - `backend/services/support_router.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/main.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Account suspension messages now route to `billing / account_suspension / deterministic_billing_intake`.
  - Detailed invoice requests now route to `billing / detailed_invoice / deterministic_billing_intake`, while amount disputes and refund requests stay out of the detailed-invoice route.
  - Billing routes skip RAG and review, ask only for missing required fields, and when fields are complete return a customer escalation acknowledgement plus a prepared internal email payload.
- Verification:
  - RED: Newly added support-router tests failed before implementation because billing messages fell through to the LLM router and billing decisions resolved as `refuse`.
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_support_router backend.tests.test_client_ticket_agent_runtime.ClientTicketAgentRuntimeContractTests.test_billing_detailed_invoice_route_skips_rag_and_prepares_internal_email`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/billing_automation.py backend/services/support_router.py backend/services/client_ticket_agent_runtime.py backend/main.py backend/tests/test_support_router.py backend/tests/test_client_ticket_agent_runtime.py`
  - `python3 scripts/verify_feature_list.py`

## 2026-06-09 - RAG access-aware AI evidence routing

- Area or subsystem: Client RAG executor and engineer evidence tools
- Prompt or model version: `rag-access-routing-v1`
- Summary: Client-side AI RAG execution now forces official-only retrieval, while engineer evidence search runs non-official retrieval first and only uses official fallback when internal evidence is insufficient or official semantics are requested.
- Reason: Model-visible evidence must match the intended access boundary: customers get official-doc-grounded answers, engineers can reason from internal/non-official knowledge without leaking it into customer drafts.
- Affected files or config:
  - `backend/services/rag_executor.py`
  - `backend/services/engineer_evidence_tools.py`
  - `backend/services/rag_service_client.py`
  - `backend/rag_api.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Client AI cannot receive technical/internal RAG chunks through the normal RAG executor.
  - Engineer evidence tooling prefers internal/non-official evidence and adds official evidence only as a fallback or semantics check.
- Verification:
  - Targeted pytest covered client official-only executor behavior, internal RAG access mode forwarding, and engineer non-official-first / official-fallback ordering.

## 2026-05-27 - control-cc orchestrator review packets

- Area or subsystem: Project-local Codex-to-Claude Code delegation workflow
- Prompt or model version: `control-cc-v5`
- Summary: Reframed Control CC around Codex as orchestrator and Claude Code as implementation owner, added runner-generated review packets for low-token Codex triage, compacted heartbeat output, and softened Claude Code implementation constraints while preserving safety and review gates.
- Reason: The workflow should save Codex tokens and avoid over-constraining Claude Code, while maintaining quality through artifacts, quality scoring, targeted diff review, and fresh verification.
- Affected files or config:
  - `.codex/skills/control-cc/SKILL.md`
  - `.codex/skills/control-cc/agents/openai.yaml`
  - `.codex/skills/control-cc/references/payload-schema.md`
  - `.codex/skills/control-cc/references/review-checklist.md`
  - `.codex/skills/control-cc/references/task-plan-schema.md`
  - `.codex/skills/control-cc/scripts/run_cc_plan.py`
  - `.codex/skills/control-cc/scripts/test_run_cc_plan.py`
  - `.claude/skills/control-cc-worker/SKILL.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - New Control CC runs default to Orchestrator Mode: Codex plans and reviews, while Claude Code owns implementation inside task or candidate worktrees without default budget caps or write-scope gates.
  - `run_cc_plan.py` writes `review_packet.json` with changed files, diff stat, artifact/temp files, non-ASCII additions, debug/TODO markers, changelog signals, optional root workspace status, and a short worker-result excerpt.
  - Compact runner output exposes heartbeat count and last heartbeat instead of the full heartbeat list, keeping Codex waiting/review context small.
  - Codex reads the review packet before long logs or full diffs and expands review only for flagged risks, high-risk tasks, or insufficient worker evidence.
- Verification:
  - `python3 -m py_compile .codex/skills/control-cc/scripts/*.py`
  - `python3 .codex/skills/control-cc/scripts/test_run_cc_plan.py`
  - `python3 .codex/skills/control-cc/scripts/test_run_repair_worker.py`
  - `rg -n "review_packet|heartbeat_count|Orchestrator Mode|quality_score|control-cc-v5" .codex/skills/control-cc .claude/skills/control-cc-worker docs/prompt_change_log.md`
  - `git diff --check`

## 2026-05-27 - Agentic RAG planner FTS restoration

- Area or subsystem: RAG agent planner
- Prompt or model version: `rag-agent-planner-tool-contract-v3`
- Summary: Restored `p_fts` and `s_fts` in the planner prompt's allowed tool list so planner output can select PostgreSQL FTS as a supplemental lexical retrieval tool for online agentic retrieval.
- Reason: Online answer quality is the priority; FTS provides valuable supplemental lexical retrieval. The planner tool contract is updated to allow FTS, and documentation/telemetry contract now requires separate FTS attribution to prevent benchmark/dashboard misattribution.
- Affected files or config:
  - `backend/services/prompts/rag_agent_planner.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_prompt_modules.py`
  - `docs/rag_retrieval_chain.md`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Agentic planner prompts advertise vector, BM25, and FTS retrieval tools.
  - Planner can intentionally select `p_fts` and `s_fts` for query classes where supplemental lexical retrieval improves answer quality.
  - FTS execution produces distinct telemetry rows that benchmark/dashboard consumers must attribute separately from BM25 and vector.
- Verification:
  - RED: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python - <<'PY' ... assert 'p_fts' in build_rag_agent_planner_user_prompt(...) ... PY` failed on the previous implementation after the tool-order assertion showed `['p_bm25']`.
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_qa.py backend/services/prompts/rag_agent_planner.py`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_prompt_modules.py::PromptModuleTests::test_rag_agent_planner_prompt_is_sectioned_and_ticket_context_aware -q`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py -q`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py -q`
  - `rtk git diff --check`

## 2026-05-27 - Agentic RAG planner FTS removal

- Area or subsystem: RAG agent planner
- Prompt or model version: `rag-agent-planner-tool-contract-v2`
- Summary: Removed `p_fts` and `s_fts` from the planner prompt's allowed tool list so planner output cannot intentionally select PostgreSQL FTS for online agentic retrieval.
- Reason: The canonical online RAG chain is vector + BM25, with FTS removed from the online main path; leaving FTS in planner tool choices made benchmark, dashboard, and trace analysis attribute FTS effects to BM25/vector retrieval.
- Affected files or config:
  - `backend/services/prompts/rag_agent_planner.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_retrieval_chain.md`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Agentic planner prompts advertise only vector and BM25 retrieval tools.
  - If a stale or mocked planner response still includes `p_fts` or `s_fts`, backend tool filtering drops those tools before execution.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_qa.py backend/services/prompts/rag_agent_planner.py`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_prompt_modules.py::PromptModuleTests::test_rag_agent_planner_prompt_is_sectioned_and_ticket_context_aware -q`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py -q`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py -q`
  - `rtk git diff --check`

## 2026-05-26 - control-cc low-token waiting and quality scoring

- Area or subsystem: Project-local Codex-to-Claude Code delegation workflow
- Prompt or model version: `control-cc-v4`
- Summary: Added runner heartbeat monitoring, availability retries, `claude_unavailable` reporting, low-token Codex waiting guidance, and mandatory post-worker `quality_score` review.
- Reason: Codex should avoid spending tokens while Claude Code is still working, should recover cleanly when Claude Code is unavailable, and should give consistent quality feedback after reviewing delegated results.
- Affected files or config:
  - `.codex/skills/control-cc/SKILL.md`
  - `.codex/skills/control-cc/agents/openai.yaml`
  - `.codex/skills/control-cc/references/review-checklist.md`
  - `.codex/skills/control-cc/scripts/run_cc_plan.py`
  - `.codex/skills/control-cc/scripts/test_run_cc_plan.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - `run_cc_plan.py` records heartbeat entries while Claude Code is running without requiring Codex to poll logs or diffs.
  - Startup failures, abnormal exits, invalid JSON, and empty-result availability failures retry at 10-second intervals up to 3 times before `claude_unavailable`.
  - Permission denials and worker `Blocked` results do not trigger availability retries.
  - Codex review reports must include `quality_score: X/10`; scores below 8 require reasons and a follow-up recommendation.
- Verification:
  - `python3 -m py_compile .codex/skills/control-cc/scripts/*.py`
  - `python3 .codex/skills/control-cc/scripts/test_run_cc_plan.py`
  - `python3 .codex/skills/control-cc/scripts/test_run_repair_worker.py`
  - `rg -n "heartbeat-sec|max-unavailable-retries|claude_unavailable|quality_score|< 8" .codex/skills/control-cc docs/prompt_change_log.md`
  - `git diff --check`

## 2026-05-26 - control-cc plan-driven delegation

- Area or subsystem: Project-local Codex-to-Claude Code delegation workflow
- Prompt or model version: `control-cc-v3`
- Summary: Reworked Control CC around Codex-authored implementation plans, lightweight Claude Code plan execution, detached candidate worktrees for isolated parallel work, and Codex-owned patch integration and final review.
- Reason: The previous workflow over-constrained Claude Code with small packet scoring, strict result formatting, and hard write gates; the desired flow gives Claude Code more implementation autonomy while preserving clean worktrees, failure visibility, Codex diff review, and targeted verification.
- Affected files or config:
  - `AGENTS.md`
  - `.codex/skills/control-cc/SKILL.md`
  - `.codex/skills/control-cc/agents/openai.yaml`
  - `.codex/skills/control-cc/references/escalation-policy.md`
  - `.codex/skills/control-cc/references/payload-schema.md`
  - `.codex/skills/control-cc/references/review-checklist.md`
  - `.codex/skills/control-cc/references/task-plan-schema.md`
  - `.codex/skills/control-cc/scripts/candidate_worktree.py`
  - `.codex/skills/control-cc/scripts/run_cc_plan.py`
  - `.codex/skills/control-cc/scripts/test_run_cc_plan.py`
  - `.claude/skills/control-cc-worker/SKILL.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Goal-only requests become one PR-sized implementation plan on a real task worktree.
  - Goal-plus-multiple-PR requests are sorted and completed as real PR slices one at a time.
  - Independent plans inside a PR may run in detached candidate worktrees, export patches, and be integrated sequentially by Codex.
  - New Claude Code plan runs use `run_cc_plan.py`, which accepts concise natural-language worker reports and records diff/report artifacts without enforcing the older strict output contract.
- Verification:
  - `python3 -m py_compile .codex/skills/control-cc/scripts/run_cc_plan.py .codex/skills/control-cc/scripts/candidate_worktree.py .codex/skills/control-cc/scripts/*.py`
  - `python3 .codex/skills/control-cc/scripts/test_run_cc_plan.py`
  - `python3 .codex/skills/control-cc/scripts/test_run_repair_worker.py`
  - `! rg -n "atomic writing packet|packet scoring|six-heading|score_packet.py|review_worker_result.py|strict write-scope|write-scope" .codex/skills/control-cc/SKILL.md`
  - `rg -n "control-cc-v3" docs/prompt_change_log.md`
  - `git diff --check`

## 2026-05-25 - control-cc hard gates and worker packet scoring

- Area or subsystem: Project-local Codex-to-Claude Code delegation workflow
- Prompt or model version: `control-cc-v2`
- Summary: Added executable packet scoring, runner hard gates for oversized packets/read-only probes/write scopes/task plan paths, a worker diff review gate, and a first-class `/control-cc-worker` Claude Code skill while keeping `/repair-worker` as a compatibility entry.
- Reason: The previous workflow relied too much on Markdown guidance, allowing Codex to hand Claude Code oversized PR slices or fall back to Codex implementation instead of enforcing atomic worker packets.
- Affected files or config:
  - `.codex/skills/control-cc/SKILL.md`
  - `.codex/skills/control-cc/agents/openai.yaml`
  - `.codex/skills/control-cc/references/payload-schema.md`
  - `.codex/skills/control-cc/references/review-checklist.md`
  - `.codex/skills/control-cc/references/task-plan-schema.md`
  - `.codex/skills/control-cc/scripts/run_cc_worker.py`
  - `.codex/skills/control-cc/scripts/run_repair_worker.py`
  - `.codex/skills/control-cc/scripts/score_packet.py`
  - `.codex/skills/control-cc/scripts/review_worker_result.py`
  - `.codex/skills/control-cc/scripts/test_run_repair_worker.py`
  - `.codex/skills/control-cc/scripts/verify_claude_cli_flow.py`
  - `.claude/skills/control-cc-worker/SKILL.md`
  - `.claude/skills/repair-worker/SKILL.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Codex must turn PR slices into scored worker packets before dispatching writing workers.
  - Oversized writing packets, dirty baselines, read-only probes with edit tools, read-only probe diffs, unsafe repo-local task plans, and write-scope violations fail in the runner before Codex accepts the result.
  - New Claude Code payloads use `/control-cc-worker`; old `/repair-worker` payloads remain compatible.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile .codex/skills/control-cc/scripts/run_cc_worker.py .codex/skills/control-cc/scripts/run_repair_worker.py .codex/skills/control-cc/scripts/score_packet.py .codex/skills/control-cc/scripts/review_worker_result.py .codex/skills/control-cc/scripts/verify_claude_cli_flow.py .codex/skills/control-cc/scripts/test_run_repair_worker.py`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python .codex/skills/control-cc/scripts/test_run_repair_worker.py`
  - `rtk rg -n "score_packet.py|review_worker_result.py|run_cc_worker.py|/control-cc-worker|task-plan-schema" .codex/skills/control-cc .claude/skills docs/prompt_change_log.md`
  - `rtk git diff --check`

## 2026-05-25 - control-cc delegated code workflow rename

- Area or subsystem: Project-local Codex-to-Claude Code delegation workflow
- Prompt or model version: `control-cc-v1`
- Summary: Renamed the project-local Codex delegation skill from `cost-optimized-repair` to `control-cc`, broadened the trigger from repair-only work to delegated code work, and documented temporary task plan files for Claude Code worker handoff.
- Reason: The previous repair framing made feature implementation, logic optimization, and refactor tasks ambiguous and encouraged oversized PR-slice payloads instead of controlled atomic worker packets.
- Affected files or config:
  - `AGENTS.md`
  - `.gitignore`
  - `.codex/skills/control-cc/SKILL.md`
  - `.codex/skills/control-cc/agents/openai.yaml`
  - `.codex/skills/control-cc/references/escalation-policy.md`
  - `.codex/skills/control-cc/references/payload-schema.md`
  - `.codex/skills/control-cc/scripts/verify_claude_cli_flow.py`
  - `.claude/skills/repair-worker/SKILL.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Codex loads `control-cc` for repairs, feature slices, refactors, logic optimization, tests, and code-adjacent docs that can be safely delegated to Claude Code.
  - Codex creates temporary worker task plans outside tracked repo paths and deletes successful plans after review and verification.
  - High-complexity slices should be recursively split or probed before direct Codex implementation.
- Verification:
  - `rtk rg -n "name: control-cc|Use when the user asks for control-cc|Temporary Task Plan|\\.codex/skills/control-cc|Control CC Worker" AGENTS.md .codex/skills/control-cc .claude/skills/repair-worker docs/prompt_change_log.md`
  - `rtk rg -n "cost-optimized-repair|Cost-Optimized Repair|Cost Optimized Repair|cost-optimized repair" AGENTS.md .codex/skills/control-cc .claude/skills/repair-worker`
  - `rtk .venv/bin/python -m py_compile .codex/skills/control-cc/scripts/run_repair_worker.py .codex/skills/control-cc/scripts/verify_claude_cli_flow.py .codex/skills/control-cc/scripts/test_run_repair_worker.py`
  - `rtk .venv/bin/python .codex/skills/control-cc/scripts/test_run_repair_worker.py`
  - `rtk git diff --check`

## 2026-05-25 - cost-optimized repair mandatory agent dispatch

- Area or subsystem: Project-local Codex-to-Claude repair delegation workflow
- Prompt or model version: `repair-worker-dispatch-v4`
- Summary: Clarified that Codex must decompose every delegated repair request before implementation and dispatch one or more Claude Code agents for each PR-sized slice, with simultaneous dispatch for safely isolated independent subtasks.
- Reason: The previous dispatch guidance described safe parallelism but did not make self-decomposition plus one-or-more Claude Code agent execution explicit enough for both detailed multi-PR plans and short repair requests.
- Affected files or config:
  - `.codex/skills/cost-optimized-repair/SKILL.md`
  - `.codex/skills/cost-optimized-repair/agents/openai.yaml`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Codex first decomposes user requirements even when the user only gives a brief bug report.
  - Every delegated PR slice launches at least one Claude Code implementation agent unless delegation is unsafe, preflight failed, or the worker failure limit has been reached.
  - Independent isolated subtasks in one PR slice should run through simultaneous Claude Code agents; non-isolated write work remains one worker.
- Verification:
  - `rtk rg -n "must decompose|one or more Claude Code agent payloads|Dispatch at least one Claude Code implementation agent|Start multiple Claude Code agents simultaneously|dispatch one or more Claude Code agents" .codex/skills/cost-optimized-repair/SKILL.md .codex/skills/cost-optimized-repair/agents/openai.yaml`
  - `rtk git diff --check`

## 2026-05-25 - cost-optimized repair dispatch preflight and PR slicing

- Area or subsystem: Project-local Codex-to-Claude repair delegation workflow
- Prompt or model version: `repair-worker-dispatch-v3`
- Summary: Added a mandatory Claude Code CLI preflight, PR-sized task decomposition rules, safe parallel-agent dispatch guidance, and worker payload metadata for PR slices, parallel groups, and write scopes.
- Reason: Broad repair plans can contain several PR-level changes, and Codex needs a deterministic guard that stops immediately when Claude Code is unavailable while avoiding oversized payloads and unsafe concurrent writes to one worktree.
- Affected files or config:
  - `.codex/skills/cost-optimized-repair/SKILL.md`
  - `.codex/skills/cost-optimized-repair/references/payload-schema.md`
  - `.codex/skills/cost-optimized-repair/agents/openai.yaml`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Codex now verifies Claude Code with a fixed non-interactive smoke test before delegated repair work and stops if that preflight fails.
  - Multi-PR plans are processed one PR slice at a time instead of being bundled into one broad worker payload.
  - Within a PR slice, Codex may start multiple Claude Code agents only for read-only probes or independent isolated write scopes; overlapping write workers stay sequential.
- Verification:
  - `rtk claude --bare -p 'Smoke test only. Reply exactly: CLAUDE_CODE_OK' --output-format json --permission-mode bypassPermissions --tools Read --model opus --effort low --no-session-persistence`
  - `rtk rg -n "Claude Code Preflight|Task Decomposition|CLAUDE_CODE_OK|pr_slice|parallel_group|write_scope" .codex/skills/cost-optimized-repair/SKILL.md .codex/skills/cost-optimized-repair/references/payload-schema.md`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile .codex/skills/cost-optimized-repair/scripts/run_repair_worker.py .codex/skills/cost-optimized-repair/scripts/verify_claude_cli_flow.py .codex/skills/cost-optimized-repair/scripts/test_run_repair_worker.py`
  - `rtk git diff --check`

## 2026-05-25 - cost-optimized repair worker output contract

- Area or subsystem: Project-local Codex-to-Claude repair delegation workflow
- Prompt or model version: `repair-worker-output-contract-v2`
- Summary: Tightened the repair-worker final output contract, added explicit payload-level final-output instructions, and documented compact runner reports for Codex review.
- Reason: Claude worker rounds could complete the code work but fail the runner contract with small status-format drift such as non-canonical `## Result` text, forcing Codex takeover and extra review tokens.
- Affected files or config:
  - `.claude/skills/repair-worker/SKILL.md`
  - `.codex/skills/cost-optimized-repair/SKILL.md`
  - `.codex/skills/cost-optimized-repair/references/payload-schema.md`
  - `.codex/skills/cost-optimized-repair/scripts/run_repair_worker.py`
  - `.codex/skills/cost-optimized-repair/scripts/verify_claude_cli_flow.py`
  - `.codex/skills/cost-optimized-repair/scripts/test_run_repair_worker.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Claude worker prompts now show `Fixed` as the single example status instead of a multi-option placeholder.
  - Worker payloads repeat the exact final output contract so the result status is less likely to drift.
  - Codex can rely on compact runner output for the normal review path and open full logs only when needed.
- Verification:
  - `python3 .codex/skills/cost-optimized-repair/scripts/test_run_repair_worker.py`
  - `python3 -m py_compile .codex/skills/cost-optimized-repair/scripts/run_repair_worker.py .codex/skills/cost-optimized-repair/scripts/verify_claude_cli_flow.py .codex/skills/cost-optimized-repair/scripts/test_run_repair_worker.py`
  - `git diff --check`

## 2026-05-19 - How-to RAG code example contract

- Area or subsystem: Client-facing RAG answer generation and customer reply formatting
- Prompt or model version: `rag-answer-howto-code-example-v1`
- Summary: Added a How-to code-example contract to the RAG answer prompt and paired it with answer-stage supplementation from selected evidence chunks when a grounded code block is available but the model omits it.
- Reason: How-to / onboarding / usage answers are more actionable when supported code examples are shown as fenced code blocks, but the system must not invent SDK/API snippets when the selected chunks only support prose guidance.
- Affected files or config:
  - `backend/services/prompts/rag_answer.py`
  - `backend/services/rag_qa.py`
  - `backend/services/customer_reply_composer.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Supported How-to answers should include a fenced Markdown code block when selected RAG chunks contain a relevant authoritative code sample.
  - Prose-only evidence still produces prose-only grounded steps instead of synthesized code.
  - Customer reply numbered steps no longer duplicate existing numeric prefixes.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_prompt_modules backend.tests.test_rag_qa backend.tests.test_client_ticket_agent_runtime`
  - `python3 -m py_compile backend/services/prompts/rag_answer.py backend/services/rag_qa.py backend/services/customer_reply_composer.py`

## 2026-05-18 - Request body evidence analyzer scene

- Area or subsystem: RAG request body/API configuration evidence extraction
- Prompt or model version: `request_body_evidence_v1` with model scene `request_body_analyzer`
- Summary: Added a JSON-only analyzer prompt and LLM profile for extracting endpoint hints, request body keys, nested field paths, field values, question need, and schema evidence goals from request body/API config questions.
- Reason: Request body questions need a low-cost extraction stage that improves schema retrieval clues without asking the model to answer, judge correctness, or invent fields.
- Affected files or config:
  - `backend/services/prompts/request_body_evidence.py`
  - `backend/services/llm_profiles.py`
  - `backend/services/rag_request_body_evidence.py`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_llm_profiles.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - When credentials are available, request body/API config questions can use `REQUEST_BODY_ANALYZER_MODEL` (default `gpt-5.4-mini`) with low reasoning and a 6 second timeout to extract schema retrieval clues.
  - If the analyzer is unavailable, times out, or returns invalid JSON, deterministic rule extraction remains the fallback.
  - Ordinary natural-language how-to questions should not be classified as request-body/API-config questions by this scene.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_rag_request_body_evidence backend.tests.test_llm_profiles backend.tests.test_prompt_modules backend.tests.test_single_host_compose`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_request_body_evidence.py backend/services/prompts/request_body_evidence.py backend/services/llm_profiles.py`

## 2026-05-18 - RAG answer API/config JSON example tightening

- Area or subsystem: Client-facing RAG answer generation
- Prompt or model version: `rag-answer-api-config-quality-v2`
- Summary: Tightened the API/configuration answer contract so supported API/config answers must include a minimal JSON or configuration example rather than a prose-only answer.
- Reason: Live answer-chain verification showed the first API/config guard was loaded and stable, but an API behavior case could still produce a grounded prose answer without a payload example. The intended behavior is fail-closed unless the prompt can build an example from verbatim chunk evidence.
- Affected files or config:
  - `backend/services/prompts/rag_answer.py`
  - `backend/tests/test_prompt_modules.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - API/configuration RAG answers that are marked sufficient should include a minimal JSON or configuration example built only from verbatim field names, enum values, value formats, and nesting in the cited chunks.
  - If that minimal example cannot be built from exact context evidence, the answer model should set `insufficient_evidence=true` instead of returning a prose-only configuration answer.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_prompt_modules.PromptModuleTests.test_rag_answer_prompt_guides_api_config_answers_without_case_specific_fields`

## 2026-05-18 - RAG answer API/config quality guard

- Area or subsystem: Client-facing RAG answer generation
- Prompt or model version: `rag-answer-api-config-quality-v1`
- Summary: Added a generic API/configuration answer contract to the RAG answer prompt so configuration and API questions include a supported one-sentence mechanism, use only verbatim field names and nesting from context chunks, and provide minimal JSON/config examples only when the exact fields are present in evidence.
- Reason: TK-198-style configuration questions need executable, schema-accurate answers. The previous prompt emphasized grounding but did not explicitly require payload examples, field-name fidelity, or a short explanation of why the observed behavior occurs.
- Affected files or config:
  - `backend/services/prompts/rag_answer.py`
  - `backend/tests/test_prompt_modules.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - RAG answers for REST API, SDK configuration, JSON payload, or parameter questions should include a minimal supported config example when context chunks provide the exact field names, enum values, value formats, and nesting.
  - The answer model should not infer field names from naming conventions and should fail closed with `insufficient_evidence=true` when exact field names or nesting are missing.
  - Unexpected-behavior answers should explain the supported mechanism or configuration reason in one sentence without claiming an unsupported root cause.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_prompt_modules.PromptModuleTests.test_rag_answer_prompt_guides_api_config_answers_without_case_specific_fields`

## 2026-04-29 - product portfolio customer reply formatting

- Area or subsystem: Client support routing and non-technical product-portfolio web-search answering
- Prompt or model version: `web-search-v4 + customer-reply-email-composer-v1`
- Summary: Tightened product-portfolio web-search output requirements to return body-only Markdown bullet lists and wrapped final customer-facing `web_search` answers in the shared email-style greeting and signoff.
- Reason: A broadcasting product-overview question returned a dense inline paragraph without `Hi there` / requester greeting and without readable bullet points for Agora products.
- Affected files or config:
  - `backend/services/prompts/web_search.py`
  - `backend/services/support_router.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Product-portfolio web-search prompts now ask for answer-body content only, leaving salutation and signoff to the backend composer.
  - Broadcasting-oriented product answers preserve Markdown bullet lists with one product or service per line, grouped under core products, major services or add-ons, and supporting tools when source coverage supports those groups.
  - Final customer-facing `web_search` answers now use `Hi {requester|there},`, the standard grounded-answer opener, and `Best Regards,\nSid`.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_prompt_modules backend.tests.test_support_router backend.tests.test_client_ticket_agent_runtime`
  - `python3 -m py_compile backend/services/prompts/web_search.py backend/services/support_router.py backend/services/client_ticket_agent_runtime.py`

## 2026-04-28 - DeepSeek fallback model routing for core LLM calls

- Area or subsystem: Cross-provider LLM factory and model profile routing
- Prompt or model version: `deepseek-fallback-v1`
- Summary: Added an OpenAI-compatible DeepSeek fallback provider for eligible core text and JSON LLM calls, using `deepseek-v4-pro` after OpenAI primary and same-provider fallback candidates are unavailable.
- Reason: Core SupportPortal generation paths need a provider-level fallback when GPT is unavailable because missing keys, timeouts, rate limits, server errors, or unavailable OpenAI models should not fail every non-tool LLM scene if DeepSeek credentials are configured.
- Affected files or config:
  - `backend/services/llm_profiles.py`
  - `backend/services/llm_factory.py`
  - `backend/services/support_router.py`
  - `backend/services/auto_deploy_report.py`
  - `backend/services/product_selection.py`
  - `backend/services/rag_sufficiency_judge.py`
  - `backend/services/engineer_agent.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_context_budget.py`
  - `backend/services/knowledge_ingestion.py`
  - `backend/services/query_understanding.py`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_llm_factory.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_token_usage.py`
  - `backend/tests/test_single_host_compose.py`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - Eligible OpenAI Responses and Chat Completions scenes keep GPT as primary, then fall back to `deepseek:deepseek-v4-pro` only for provider/key/transport/rate-limit/server/model-unavailable failures.
  - OpenAI-only tool payloads, web search, input guardrails, benchmark judge runs, non-retryable request errors, and caller-side business validation failures do not silently switch providers.
  - LLM results now expose the actual provider so telemetry and traces can record `deepseek:deepseek-v4-pro` instead of misclassifying fallback output as OpenAI.
- Verification:
  - `python3 -m py_compile backend/services/llm_profiles.py backend/services/llm_factory.py backend/services/support_router.py backend/services/auto_deploy_report.py backend/services/product_selection.py backend/services/rag_sufficiency_judge.py backend/services/engineer_agent.py backend/services/troubleshooting_intake.py backend/services/rag_qa.py backend/services/rag_context_budget.py backend/services/knowledge_ingestion.py backend/services/query_understanding.py backend/tests/test_llm_factory.py backend/tests/test_llm_profiles.py backend/tests/test_single_host_compose.py`
  - `python3 -m unittest backend.tests.test_llm_factory backend.tests.test_llm_profiles backend.tests.test_token_usage backend.tests.test_single_host_compose`
  - `python3 -m unittest backend.tests.test_auto_deploy_report backend.tests.test_product_selection backend.tests.test_rag_sufficiency_judge backend.tests.test_knowledge_ingestion backend.tests.test_support_router`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_rag_context_budget backend.tests.test_query_understanding backend.tests.test_rag_qa.RagQaHybridTests.test_run_rag_query_exact_error_lookup_uses_light_path_fast_answer_profile_then_falls_back_to_main_model backend.tests.test_rag_qa.RagQaHybridTests.test_run_rag_query_uses_shared_packed_evidence_for_answer_and_trace`

## 2026-04-17 - client ticket titles normalize to English

- Area or subsystem: Client ticket title generation and `/client2` draft title presentation
- Prompt or model version: `ticket-title-english-v2`
- Summary: Changed ticket-title generation so new client-ticket subjects are always normalized into concise English titles, even when the first customer message or explicit `subject` is non-English, and aligned `/client2` draft titles to keep a fixed `New ticket` placeholder until the backend subject arrives.
- Reason: `/client2` was showing a front-end temporary title derived from the first message while backend title generation deliberately followed the customer language, which caused mixed temporary titles and persisted Chinese subjects when the product expectation is a stable English ticket title.
- Affected files or config:
  - `backend/services/ticket_title.py`
  - `backend/main.py`
  - `ui/client2-ui/app.js`
  - `ui/client2-ui/index.html`
  - `backend/tests/test_ticket_title.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_client2_ui_contract.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - New client-ticket subjects now resolve to concise English titles regardless of the original customer language.
  - Explicit `subject` values on new client tickets are normalized into English before persistence unless they are already valid concise English titles.
  - `/client2` no longer generates a local temporary title from the first message; draft and pre-sync title surfaces stay on `New ticket` until backend sync returns the persisted subject.
- Verification:
  - `TOKENIZERS_PARALLELISM=false /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_client2_ui_contract -q`
  - `TOKENIZERS_PARALLELISM=false /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_ticket_title -q`
  - `TOKENIZERS_PARALLELISM=false /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_fix_ticket_subject_cli -q`
  - `TOKENIZERS_PARALLELISM=false /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python - <<'PY'`
    `import os, sys, pytest`
    `code = pytest.main(['-q', 'backend/tests/test_investigation_flow.py'])`
    `sys.stdout.flush(); sys.stderr.flush(); os._exit(code)`
    `PY`
  - `node --check ui/client2-ui/app.js`

## 2026-04-16 - unify engineer-facing AI identity to Sid

- Area or subsystem: Engineer investigation reply persona and cross-surface user-visible AI naming
- Prompt or model version: `engineer-investigation-reply-v6`
- Summary: Unified the remaining engineer-side and dashboard-side user-visible AI identity from `Case Buddy` to `Sid`, so all customer- and engineer-facing AI labels now use the same public name.
- Reason: The product still exposed two user-visible AI names across `/client`, `/engineer`, and `/dashboard`, which made the assistant identity feel inconsistent even though the same support agent experience was being presented.
- Affected files or config:
  - `ui/engineer-ui/app.js`
  - `ui/engineer-ui/index.html`
  - `ui/dashboard-ui/app.js`
  - `ui/dashboard-ui/index.html`
  - `backend/services/engineer_agent.py`
  - `backend/services/prompts/engineer_investigation_reply.py`
  - `backend/tests/test_engineer_ui_contract.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Engineer internal thread `engineer_ai` messages now render as `Sid` instead of `Case Buddy`.
  - Dashboard ticket detail uses `Sid` for the internal AI author label as well as the public assistant label.
  - The engineer investigation reply prompt persona is now `Sid`, removing the previous `Case Buddy` / `Sid` split from user-visible naming.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_engineer_ui_contract.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_dashboard_ui_contract.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_prompt_modules.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_client_ui_contract.py`

## 2026-04-15 - case buddy current-issue facts exclude candidate answers

- Area or subsystem: Engineer investigation reply prompt and Case Buddy current-issue fact curation
- Prompt or model version: `engineer-investigation-reply-v5`
- Summary: Tightened the engineer investigation reply prompt so `known_facts` must stay limited to current customer reports and verified evidence, while the engineer-agent fallback/normalization logic and engineer detail UI now strip candidate-answer-like facts from the Case Buddy `Current issue` block.
- Reason: `Current issue` was leaking `Sid candidate answer` text instead of presenting a clean problem summary plus current known information, which made the opening engineer handoff harder to scan and mixed unverified draft guidance with actual facts.
- Affected files or config:
  - `backend/services/prompts/engineer_investigation_reply.py`
  - `backend/services/engineer_agent.py`
  - `ui/engineer-ui/app.js`
  - `ui/engineer-ui/styles.css`
  - `ui/engineer-ui/index.html`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_engineer_ui_contract.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - `Current issue` in engineer detail now renders as one summary paragraph plus remaining fact bullets, instead of one flat bullet list.
  - `Sid candidate answer`, `Candidate answer ...`, and `The current candidate answer ...` no longer appear in `known_facts` for new or normalized engineer-agent state.
  - The investigation reply prompt now explicitly tells the model not to write candidate answers or draft recommendations into `known_facts`.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_engineer_ui_contract.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_investigation_flow.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_worker.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_prompt_modules.py`

## 2026-04-14 - engineer investigation reply transport fallback

- Area or subsystem: Engineer investigation reply model fallback
- Prompt or model version: `engineer-investigation-reply-v2`
- Summary: Added transport-failure model fallback for engineer investigation reply generation so the scene now retries `gpt-5.4` and then degrades to `gpt-5.4-mini` before fail-closing the engineer turn.
- Reason: Engineers were still hitting the generic `I couldn't prepare a customer-safe reply...` fallback when the primary investigation-reply model timed out, even though the request content was valid and a smaller fallback model could still complete the structured reply.
- Affected files or config:
  - `backend/services/llm_factory.py`
  - `backend/services/llm_profiles.py`
  - `backend/tests/test_llm_factory.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - `engineer_investigation_reply` now tries `gpt-5.4-mini` after the primary `gpt-5.4` candidate exhausts its retry budget on retryable transport or server-side failures.
  - Engineer investigation turns should surface a normal drafted reply more often during transient model instability instead of immediately falling back to the generic fail-closed message.
  - Non-retryable request errors and genuinely unavailable fallback models still fail closed.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_llm_profiles.py backend/tests/test_llm_factory.py backend/tests/test_investigation_flow.py`
  - Added regression coverage for both direct `llm_factory` fallback-on-timeout behavior and the end-to-end engineer investigation message path.

## 2026-04-13 - engineer identity refresh for Case Buddy, Jack, and Sid

- Area or subsystem: Engineer investigation reply persona and cross-surface assistant naming
- Prompt or model version: `engineer-investigation-reply-v2`
- Summary: Renamed the internal engineer-thread AI persona from `Engineer AI` to `Case Buddy`, aligned the engineer demo identity and default `engineer_id` to `Jack`, and renamed every public customer-thread `assistant` author label to `Sid` across client, engineer, and dashboard surfaces.
- Reason: The engineer workflow needed clearer role separation between the internal investigation assistant, the logged-in engineer identity, and the public-facing support assistant so the UI and prompt persona stop mixing `Engineer AI`, generic `AI`, and `Engineer`.
- Affected files or config:
  - `ui/engineer-ui/app.js`
  - `ui/engineer-ui/index.html`
  - `ui/dashboard-ui/app.js`
  - `ui/client-ui/app.js`
  - `backend/main.py`
  - `backend/services/engineer_agent.py`
  - `backend/services/prompts/engineer_investigation_reply.py`
  - `backend/tests/test_client_ui_contract.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_engineer_ui_contract.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Internal engineer-thread messages now render `Case Buddy` for `engineer_ai` and `jack` for `engineer`, while the engineer login/demo identity shows `Jack / jack` and engineer actions default to `engineer_id=Jack`.
  - Public customer-thread `assistant` messages now render as `Sid` in client chat, engineer-side `Customer Timeline`, and dashboard ticket detail message cards.
  - The dedicated engineer investigation reply prompt now uses the `Case Buddy` persona and explicitly tells the model to self-reference as `Sid` if a customer-facing draft names the assistant.
- Verification:
  - `node --check ui/client-ui/app.js && node --check ui/dashboard-ui/app.js && node --check ui/engineer-ui/app.js`
  - `python3 -m py_compile backend/main.py backend/services/engineer_agent.py backend/services/prompts/engineer_investigation_reply.py backend/tests/test_client_ui_contract.py backend/tests/test_dashboard_ui_contract.py backend/tests/test_engineer_ui_contract.py backend/tests/test_prompt_modules.py backend/tests/test_investigation_flow.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_client_ui_contract backend.tests.test_dashboard_ui_contract backend.tests.test_engineer_ui_contract backend.tests.test_prompt_modules backend.tests.test_investigation_flow -q`

## 2026-04-08 - real_case batch diagnostics become the default local SupportPortal verification flow

- Area or subsystem: Agent workflow and local SupportPortal diagnostic skills
- Prompt or model version: `supportportal-diagnostic-real-case-batch-v1`
- Summary: Switched the local `supportportal-route-timing-report` and `supportportal-answer-chain-report` skills from single-message defaults to batch-first execution over the repo `real_case/real_user_questions.txt`, updated their agent metadata to advertise batch verification, and added an `AGENTS.md` rule that timing optimizations must run route timing while answer-quality optimizations must run answer-chain verification.
- Reason: Single-message diagnostics were too easy to skip or tailor to one happy-path query. Making `real_case` batch runs the default gives post-change verification a broader, repeatable question set and makes the required diagnostic skill explicit in repo workflow rules.
- Affected files or config:
  - `AGENTS.md`
  - `real_case/real_user_questions.txt`
  - `docs/prompt_change_log.md`
  - `/Users/xieziling/.codex/skills/supportportal-route-timing-report/SKILL.md`
  - `/Users/xieziling/.codex/skills/supportportal-route-timing-report/agents/openai.yaml`
  - `/Users/xieziling/.codex/skills/supportportal-route-timing-report/scripts/run_route_timing_report.py`
  - `/Users/xieziling/.codex/skills/supportportal-answer-chain-report/SKILL.md`
  - `/Users/xieziling/.codex/skills/supportportal-answer-chain-report/agents/openai.yaml`
  - `/Users/xieziling/.codex/skills/supportportal-answer-chain-report/scripts/run_answer_chain_report.py`
- Expected behavior change:
  - Running either diagnostic skill with no `--message` now reads the active repo/worktree `real_case/real_user_questions.txt` and executes all non-empty questions.
  - `--message` remains available as an explicit single-case override, and `--real-case-file` allows swapping the batch input file.
  - Both skills now print one merged stdout report with a case summary plus per-case details instead of defaulting to one single-question report.
  - Repo workflow instructions now require route timing verification after performance/timing work and answer-chain verification after answer-quality work, with both required when a task affects both dimensions.
- Verification:
  - `python3 /Users/xieziling/.codex/skills/supportportal-route-timing-report/scripts/run_route_timing_report.py --help`
  - `python3 /Users/xieziling/.codex/skills/supportportal-answer-chain-report/scripts/run_answer_chain_report.py --help`
  - `python3 -m py_compile /Users/xieziling/.codex/skills/supportportal-route-timing-report/scripts/run_route_timing_report.py /Users/xieziling/.codex/skills/supportportal-answer-chain-report/scripts/run_answer_chain_report.py`
  - Inline monkeypatch harnesses that asserted the old single-case defaults failed before the change and that both wrappers now execute `4` real-case questions plus emit `## Case 总表`
  - `python3 /Users/xieziling/.codex/skills/supportportal-route-timing-report/scripts/run_route_timing_report.py`
  - `python3 /Users/xieziling/.codex/skills/supportportal-answer-chain-report/scripts/run_answer_chain_report.py`
  - Verification result:
    - Both wrappers now expose batch-first CLI help, support `--message` override plus `--real-case-file`, and resolve the Python runtime from the root workspace `.venv` when invoked from a worktree.
    - The repo workflow instructions now explicitly name both diagnostic skills and when each one is required.
    - The batch harnesses turned green after the change: both wrappers executed all `4` real-case questions and emitted merged stdout reports with `## Case 总表`.
    - Live route timing verification produced `/tmp/supportportal_route_timing_real_case_20260408.md` with `success_count=1` and `failure_count=3`; the first FAQ case (`how to join channel`) returned a full timing report with `answer_route=rag` and `question_to_final_answer_ms=61839.97`, while the other real-case questions exposed existing timeout/restart instability in the current compose environment.
    - Live answer-chain verification produced `/tmp/supportportal_answer_chain_real_case_20260408.md` with `success_count=1` and `failure_count=3`; the first FAQ case returned a full grounded-answer chain report, and the other cases surfaced the same environment/worker instability while still proving the batch report and health-failure reporting paths work.

## 2026-04-07 - Query-planner FAQ class for vector-first how-to retrieval

- Area or subsystem: Client AI technical RAG retrieval planning and trace explainability
- Prompt or model version: `rag-agent-planner-how-to-faq-v1`
- Summary: Expanded the RAG agent planner prompt contract to allow a dedicated `how_to_faq` query class, aligned runtime routing so short usage/how-to questions use the non-light-path vector-first profile, and exposed the chosen answer profile and planner flags in live trace/reporting outputs.
- Reason: The existing planner/runtime split treated `"How to join channel"` as a lean lexical question, which pushed a short FAQ through the slower BM25-first path and hid the final execution profile in the live trace summary when post-answer artifacts arrived late.
- Affected files or config:
  - `backend/services/prompts/rag_agent_planner.py`
  - `backend/services/rag_qa.py`
  - `backend/repositories/knowledge_repository.py`
  - `scripts/trace_client_ticket_route.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - Planner outputs may now explicitly choose `query_class="how_to_faq"` for short usage/how-to questions instead of forcing them into `lexical_exact` or broader configuration buckets.
  - Runtime retrieval for `how_to_faq` stays on the main answer profile, keeps vector setup enabled, and only introduces BM25 during recovery or vector-unavailable fallback.
  - Live traces and scorecard detail now expose `query_class`, `light_path_used`, `vector_setup_skipped`, `answer_profile_used`, and `answer_profile_fallback_used`, making the effective prompt/model path visible without reading raw DB telemetry.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_trace_client_ticket_route_cli backend.tests.test_rag_agentic backend.tests.test_rag_qa backend.tests.test_prompt_modules backend.tests.test_rag_scorecard_repository`
  - Verification result:
    - Prompt-adjacent regression coverage passed inside the targeted `161`-test suite.
    - The updated planner prompt surface is exercised by prompt-module assertions and by agentic/runtime tests that now expect `how to join channel` to classify as `how_to_faq` with `light_path_used=false` and `vector_setup_skipped=false`.

## 2026-04-05 - Troubleshooting intake prompt supports answer-mode clarification

- Area or subsystem: Client AI insufficient-evidence intake review
- Prompt or model version: `troubleshooting-intake-answer-clarify-v1`
- Summary: Expanded the troubleshooting-intake prompt so the review stage can now distinguish troubleshooting investigations from non-troubleshooting how-to clarification, explicitly use `desired_outcome` and `blocked_step_or_error` for answer-mode follow-up, and mark answer-mode cases ready for engineer handoff once those fields are known.
- Reason: The previous prompt only supported investigation-field gathering, so non-troubleshooting `rag_insufficient_evidence` cases either opened engineer immediately or returned no clarify guidance. The fail-closed customer RAG change needs a prompt contract that can ask goal/blocker questions without forcing every how-to miss into troubleshooting intake fields.
- Affected files or config:
  - `backend/services/prompts/troubleshooting_intake.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - The intake review prompt can now ask natural-language clarification for non-troubleshooting how-to misses instead of only asking troubleshooting field lists.
  - Answer-mode clarify outputs may populate `known_information` / `missing_information` with `desired_outcome` and `blocked_step_or_error`.
  - Once those answer-mode clarify fields are present and the query still needs human follow-up, the runtime can reuse that state and open engineer with the clarified customer context.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_troubleshooting_intake backend.tests.test_client_ticket_agent_runtime backend.tests.test_ticket_orchestrator backend.tests.test_rag_api backend.tests.test_rag_qa`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_worker backend.tests.test_investigation_flow backend.tests.test_prompt_modules backend.tests.test_rag_service_client backend.tests.test_rag_evidence_summary`
  - Verification result:
    - Prompt-adjacent regression coverage passed in the focused 77-test suite and the broader 83-test integration suite.
    - The new prompt contract is exercised by answer-mode clarify regressions and by worker/investigation flow tests that preserve `client_intake_state` across customer follow-up turns.

## 2026-04-02 - Query-understanding prompt surface for client RAG retrieval planning

## 2026-04-03 - Lower-latency default reasoning for client RAG answers

- Area or subsystem: Client AI technical RAG answer generation
- Prompt or model version: `rag-answer-reasoning-latency-v1`
- Summary: Lowered the default `rag_answer` reasoning effort from `high` to `medium`, and introduced a separate `RAG_COMPLEX_ANSWER_REASONING_EFFORT` override so complex troubleshooting/comparison questions can still opt back into `high` reasoning without forcing simple FAQ/how-to queries through the slower path.
- Reason: The client-facing RAG answer stage was overpaying latency on straightforward grounded questions such as `how to join channel`. Splitting the default and complex reasoning tiers keeps the common path faster while preserving headroom for genuinely harder questions.
- Affected files or config:
  - `backend/services/llm_profiles.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_rag_qa.py`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - Standard grounded RAG answers now default to `reasoning_effort="medium"`.
  - Complex/troubleshooting/comparison answer paths can promote themselves to `RAG_COMPLEX_ANSWER_REASONING_EFFORT` instead of forcing every query through the higher-latency setting.
  - Runtime config now exposes both `RAG_ANSWER_REASONING_EFFORT` and `RAG_COMPLEX_ANSWER_REASONING_EFFORT`, making the fast path and escalation path independently tunable.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_llm_profiles backend.tests.test_rag_qa`
  - `podman exec deployment_rag_api_1 python -c "from backend.services.llm_profiles import resolve_model_profile, RAG_ANSWER_SCENARIO; print(resolve_model_profile(RAG_ANSWER_SCENARIO).reasoning_effort)"`
  - `podman exec deployment_rag_api_1 python -c "import os; print(os.getenv('RAG_COMPLEX_ANSWER_REASONING_EFFORT'), os.getenv('RAG_ANSWER_REASONING_EFFORT'))"`
  - Verification result:
    - Focused runtime/profile regression passed in the targeted unittest suite included in the larger 91-test run.
    - `deployment_rag_api_1` reported `medium` for `resolve_model_profile(RAG_ANSWER_SCENARIO).reasoning_effort`.
    - The container environment exposed `RAG_COMPLEX_ANSWER_REASONING_EFFORT=high` and `RAG_ANSWER_REASONING_EFFORT=medium`, matching the intended default/escalation split.

- Area or subsystem: Client AI technical RAG retrieval planning
- Prompt or model version: `query-understanding-v1-en`
- Summary: Added a dedicated query-understanding prompt surface for self-query planning, retrieval-oriented rewrite/enhancement, and limited decomposition. These prompts are modularized under `backend/services/prompts/query_understanding.py` and are designed for English-only V1 query planning while leaving a profile/registry seam for future locale- or product-specific prompt sets.
- Reason: The existing client AI flow had a strong route-to-skill seam and a post-RAG sufficiency gate, but it still sent raw customer text straight into retrieval. The new prompt builders formalize the retrieval-planning boundary so future self-query parsing or model-backed query planning can use explicit field definitions, few-shot examples, and safe fallback rules without changing the external ticket API.
- Affected files or config:
  - `backend/services/prompts/__init__.py`
  - `backend/services/prompts/query_understanding.py`
  - `backend/services/query_understanding.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_query_understanding.py`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - Query-understanding prompts now define a structured retrieval-plan contract with explicit `hard_filters` and `soft_signals`.
  - Rewrite/enhancement prompts now explicitly say they are retrieval-only and must not change user intent.
  - Decomposition prompts now explicitly restrict splitting to genuinely multi-part technical requests and cap subqueries to three.
  - No model selection or temperature defaults were changed in this entry; these prompts are introduced as modular builders and future runtime hooks.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_query_understanding.py backend/tests/test_prompt_modules.py backend/tests/test_rag_qa.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_rag_evidence_summary.py backend/tests/test_rag_service_client.py backend/tests/test_rag_scorecard_repository.py backend/tests/test_rag_reset.py backend/tests/test_knowledge_repository_bm25.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/rag_api.py backend/repositories/knowledge_repository.py backend/services/prompts/__init__.py backend/services/prompts/query_understanding.py backend/services/query_understanding.py backend/services/rag_benchmark_runner.py backend/services/rag_evidence_summary.py backend/services/rag_qa.py backend/tests/test_prompt_modules.py backend/tests/test_query_understanding.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_rag_qa.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `ln -sfn /Users/xieziling/Desktop/personal_proj/SupportPortal/.env /Users/xieziling/.config/superpowers/worktrees/SupportPortal/client-ai-query-understanding-v1/.env`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - `podman exec deployment_rag_api_1 python -c "import json; from backend.services.query_understanding import understand_rag_query; result = understand_rag_query('Compare BuildTokenWithUid vs BuildTokenWithUidAndPrivilege for Node.js, and how do wildcard tokens fit in.'); print(json.dumps({'query_profile': result.query_profile, 'canonical_terms': result.canonical_terms, 'hard_filters': result.retrieval_plan.hard_filters, 'soft_signals': result.retrieval_plan.soft_signals, 'rewritten_queries': result.rewritten_queries, 'decomposition_subqueries': result.decomposition_subqueries, 'fallback_mode': result.fallback_mode}, ensure_ascii=False))"`
  - Verification result:
    - Focused query-understanding/RAG regression suite passed: `106 passed`.
    - Full backend suite passed: `375 passed, 10 warnings`.
    - `py_compile` completed without errors for all touched Python files.
    - Rebuilt containers came back `Up` and host `/health` returned `status=ok`, `ticket_storage=postgres`, `knowledge_storage=postgres`, `rag_service=ok`.
    - Runtime query-understanding smoke inside `deployment_rag_api_1` returned an English profile with glossary normalization, `language=nodejs` hard filtering, soft signals for authentication/wildcard handling, one rewrite, and capped decomposition subqueries.

## 2026-04-01 - Client AI Prompt V2 modularization

- Area or subsystem: Client AI routing, non-technical web search, RAG answer generation, and RAG sufficiency judging
- Prompt or model version: `prompt-v2-modularized`
- Summary: Extracted the client AI prompt text into a dedicated `backend/services/prompts/` package and upgraded the router, web search, RAG answer, and RAG sufficiency prompts to a shared V2 format with explicit role locking, sectioned inputs, fallback instructions, and compact few-shot examples.
- Reason: The previous prompt setup was uneven. RAG prompting was relatively strong, but router and web-search prompting were flatter and more dependent on code-level fallbacks. Standardizing all four prompt surfaces reduces hallucination risk, makes routing behavior easier to reason about, and creates a stable place to track future prompt/model iterations.
- Affected files or config:
  - `AGENTS.md`
  - `backend/services/prompts/__init__.py`
  - `backend/services/prompts/router.py`
  - `backend/services/prompts/web_search.py`
  - `backend/services/prompts/rag_answer.py`
  - `backend/services/prompts/rag_sufficiency.py`
  - `backend/services/support_router_prompt.py`
  - `backend/services/support_router.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_sufficiency_prompt.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_rag_prompt_guards.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_rag_sufficiency_judge.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - Router prompt now explicitly says it only classifies and uses a stronger troubleshooting-to-`agora_technical` ambiguity policy.
  - Web search prompt is now explicitly source-grounded, official-source-first, and has a stronger `INSUFFICIENT` fallback contract.
  - RAG answer prompt now uses a sectioned template and few-shot examples while preserving the exact insufficient-evidence reply and the existing safe cross-platform guardrails.
  - RAG sufficiency prompt now explicitly says it only judges, never rewrites, and must choose `investigate` when in doubt.
  - No model names or model configuration values were changed in this entry.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_prompt_modules.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_prompt_guards.py backend/tests/test_rag_sufficiency_judge.py backend/tests/test_support_router.py backend/tests/test_ticket_orchestrator.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/prompts/__init__.py backend/services/prompts/router.py backend/services/prompts/web_search.py backend/services/prompts/rag_answer.py backend/services/prompts/rag_sufficiency.py backend/services/support_router_prompt.py backend/services/support_router.py backend/services/rag_qa.py backend/services/rag_sufficiency_prompt.py backend/services/rag_sufficiency_judge.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `scripts/workflow/link_worktree_env.sh /Users/xieziling/.config/superpowers/worktrees/SupportPortal/client-ai-prompt-v2`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `podman exec deployment_api_1 python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=10).read().decode())"`
  - `podman exec deployment_api_1 python -c "import json, urllib.request; payload=json.dumps({'customer_id':'prompt-smoke-web-4','message':'Who is Agora\\'s CEO?'}).encode(); req=urllib.request.Request('http://127.0.0.1:8000/api/tickets/query', data=payload, headers={'Content-Type':'application/json'}, method='POST'); print(urllib.request.urlopen(req, timeout=30).read().decode())"`
  - `podman exec deployment_api_1 python -c "import json, urllib.request; payload=json.dumps({'customer_id':'prompt-smoke-rag-4','message':'How do I join a channel?'}).encode(); req=urllib.request.Request('http://127.0.0.1:8000/api/tickets/query', data=payload, headers={'Content-Type':'application/json'}, method='POST'); print(urllib.request.urlopen(req, timeout=30).read().decode())"`

## 2026-04-02

- Area or subsystem: Client AI model selection, RAG answer generation, RAG sufficiency judging, engineer helper generation, benchmark judges, knowledge metadata enrichment, and next-prototype routes
- Prompt or model version: `model-routing-v1`
- Summary: Centralized model defaults behind shared LLM profile/factory helpers, moved the main RAG and engineer helper generation paths onto OpenAI Responses, upgraded web-search, RAG answer, and RAG sufficiency defaults to the requested GPT-5.4 family, switched benchmark judges to a provider-qualified multi-vendor panel, and removed the unused emotion-reply LLM helper so the client entry flow remains rule-based only.
- Reason: Model selection had drifted across multiple files and APIs. A single scene-aware profile layer makes future prompt/model changes auditable, keeps scene defaults aligned with product requirements, and removes dead LLM code that no longer participates in the client flow.
- Affected files or config:
  - `.env.example`
  - `backend/main.py`
  - `backend/services/emotion_reply.py`
  - `backend/services/knowledge_ingestion.py`
  - `backend/services/llm_factory.py`
  - `backend/services/llm_profiles.py`
  - `backend/services/rag_benchmark.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_sufficiency_judge.py`
  - `backend/services/support_router.py`
  - `backend/tests/test_emotion_reply.py`

  - `backend/tests/test_knowledge_ingestion.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_next_prototype_model_contract.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_sufficiency_judge.py`
  - `deployment/docker-compose.single-host.yml`
  - `ui/client-ui/next-prototype/app/api/chat/route.ts`
  - `ui/client-ui/next-prototype/app/api/generate-title/route.ts`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - `agora_non_technical -> web_search` now defaults to `gpt-5.4`.
  - `agora_technical -> rag answer` now defaults to `gpt-5.4` with `reasoning=high` through the shared Responses wrapper.
  - The post-RAG sufficiency judge now defaults to `gpt-5.4`.
  - Engineer helper generation now defaults to `gpt-5.4` with `reasoning=high`.
  - Knowledge-ingestion metadata enrichment now defaults to `gpt-5.4-mini`.
  - Benchmark judges now default to a provider-qualified panel of `openai:gpt-5.4`, `siliconflow:Qwen/Qwen3.5-397B-A17B`, and `siliconflow:deepseek-ai/DeepSeek-V3.2`.
  - The legacy `generate_emotion_reply(...)` LLM path has been removed because the production client entry flow now relies only on `build_initial_ack(...)`.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_profiles.py backend/tests/test_rag_sufficiency_judge.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_emotion_reply.py backend/tests/test_next_prototype_model_contract.py backend/tests/test_support_router.py backend/tests/test_rag_qa.py backend/tests/test_knowledge_ingestion.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/main.py backend/rag_api.py backend/services/llm_profiles.py backend/services/llm_factory.py backend/services/support_router.py backend/services/rag_sufficiency_judge.py backend/services/rag_qa.py backend/services/knowledge_ingestion.py backend/services/rag_benchmark.py backend/services/rag_benchmark_runner.py backend/services/emotion_reply.py backend/tests/test_llm_profiles.py backend/tests/test_rag_sufficiency_judge.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_emotion_reply.py backend/tests/test_next_prototype_model_contract.py backend/tests/test_knowledge_ingestion.py backend/tests/test_rag_qa.py`
  - `scripts/workflow/link_worktree_env.sh /Users/xieziling/.config/superpowers/worktrees/SupportPortal/client-ai-model-priority`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - `curl -sS -X POST http://localhost:8080/api/tickets/query -H 'Content-Type: application/json' -d '{"customer_id":"model-priority-web-smoke","message":"Who is Agora'\''s CEO?"}'`
  - `curl -sS -X POST http://localhost:8080/api/tickets/query -H 'Content-Type: application/json' -d '{"customer_id":"model-priority-rag-smoke","message":"How do I join a channel?"}'`

## 2026-04-02

- Area or subsystem: RAG query-understanding prompts, query-expansion model activation, and English dictionary-backed retrieval planning
- Prompt or model version: `query-expansion-v2`
- Summary: Upgraded the pre-RAG query-understanding stage from heuristic-only rewrites to a hybrid expansion pipeline that can consume structured glossary/symptom hits, call the dedicated query-expansion model for self-query/rewrite/decomposition, and cache those LLM planning results behind Redis-aware query-expansion cache keys.
- Reason: The previous query-understanding stage was recall-limited because it relied mostly on hardcoded heuristics and markdown term matching. Activating the self-query/rewrite/decomposition prompts at runtime and grounding them with curated dictionary hits makes retrieval planning more expressive without expanding the customer-visible answer surface.
- Affected files or config:
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/services/llm_profiles.py`
  - `backend/services/prompts/query_understanding.py`
  - `backend/services/query_expansion_cache.py`
  - `backend/services/query_understanding.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_qa.py`
  - `backend/rag_api.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_query_understanding.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_qa.py`
  - `dictionary/agora_glossary_en.json`
  - `dictionary/troubleshooting_lexicon_en.json`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - Query-understanding now has a dedicated `gpt-5.4-mini` scene for self-query/rewrite/decomposition planning.
  - The runtime no longer relies only on markdown glossary parsing; it can load structured glossary and troubleshooting lexicon snapshots and inject only matched dictionary hits into the planning prompts.
  - Query-expansion planning can be cached with Redis using normalized-query/profile/version/model keys so repeated support questions do not always pay the LLM planning cost.
  - No customer-facing API shape or answer prompt contract changed in this entry.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_profiles.py backend/tests/test_query_understanding.py backend/tests/test_rag_qa.py backend/tests/test_rag_benchmark_runner.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`

## 2026-04-02

- Area or subsystem: RAG context compression prompt and compression-model activation
- Prompt or model version: `rag-context-compression-v1`
- Summary: Added a dedicated evidence-compression prompt module and enabled a new `rag_context_compression` model scene so oversized or redundant reranked candidates can be packed into a tighter evidence bundle before answer generation and sufficiency judging.
- Reason: After Query Expansion V2, retrieval recall improved enough that prompt budget and context dilution became the next bottleneck. A formal compression prompt with a dedicated small-model scene keeps packed evidence concise, query-focused, and citation-preserving without changing the customer-facing answer contract.
- Affected files or config:
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/services/llm_profiles.py`
  - `backend/services/prompts/__init__.py`
  - `backend/services/prompts/rag_context_compression.py`
  - `backend/services/rag_context_budget.py`
  - `backend/services/rag_evidence_summary.py`
  - `backend/services/rag_qa.py`
  - `backend/rag_api.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_rag_context_budget.py`
  - `backend/tests/test_rag_evidence_summary.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - RAG can now estimate context budget before answer generation and only trigger compression when the raw evidence set is too large, too repetitive, or otherwise low-density.
  - The compression prompt returns a JSON evidence pack that preserves supporting chunk ids, condensed evidence text, and query-focused facts.
  - The same packed evidence is reused by both the answer model and the post-RAG sufficiency judge, reducing answer/judge drift.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_context_budget.py backend/tests/test_rag_qa.py backend/tests/test_rag_evidence_summary.py backend/tests/test_prompt_modules.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_llm_profiles.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/rag_api.py backend/services/llm_profiles.py backend/services/prompts/rag_context_compression.py backend/services/rag_context_budget.py backend/services/rag_evidence_summary.py backend/services/rag_benchmark_runner.py backend/services/rag_qa.py backend/tests/test_rag_context_budget.py backend/tests/test_rag_evidence_summary.py backend/tests/test_prompt_modules.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_rag_qa.py backend/tests/test_llm_profiles.py`

- Area or subsystem: Benchmark diagnostics, run strategy snapshots, and RAG dashboard visualization payloads
- Prompt or model version: `benchmark-diagnostic-visibility-v1`
- Summary: Expanded benchmark run profiles and case traces so dashboard views can expose answer/judge model selections, query-understanding toggles, expansion settings, rerank windows, judge disagreement, and candidate-funnel diagnostics for every benchmark run and case detail.
- Reason: Prompt and model changes are only auditable if each benchmark run records the actual active scene/model configuration and surfaces it in the review UI. The previous payloads carried too little prompt/model context to explain regressions or compare runs confidently.
- Affected files or config:
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_dashboard_contract.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/styles.css`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - Benchmark runs now expose a richer strategy snapshot that includes answer model, judge models, query-understanding switches, retrieval windows, rerank windows, and future context-budget markers.
  - Case detail payloads now surface query-understanding hits, filter provenance, candidate-funnel counts, and judge disagreement without changing any client-facing ticket API.
  - The RAG dashboard can now visualize benchmark run history, run comparison, and run-level diagnostic distributions from the same benchmark session payload.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_benchmark.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_rag_scorecard_repository.py backend/tests/test_rag_benchmark_session.py backend/tests/test_run_rag_benchmark_session_cli.py backend/tests/test_dashboard_ui_contract.py backend/tests/test_rag_dashboard_contract.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_benchmark_runner.py backend/repositories/knowledge_repository.py`
  - `node --check ui/dashboard-ui/rag/app.js`

- Area or subsystem: RAG model normalization, provider-aware usage ledger, and sufficiency judge evidence alignment
- Prompt or model version: `gpt54-ledger-and-shared-packed-evidence-v1`
- Summary: Normalized GPT defaults to the GPT-5.4 family, extended usage tracking to provider-qualified ledgers with future-ready token fields, and updated the sufficiency-judge prompt path so the judge consumes the same packed evidence envelope as the answer model.
- Reason: Prompt/model evaluations were still using partial cost estimates and a slimmer judge evidence view than answer generation, which undermined both quality diagnostics and cost transparency. The system also needed a stable token schema that can later absorb prompt-token, cached-token, and reasoning-token fields without another persistence reset.
- Affected files or config:
  - `backend/services/llm_profiles.py`
  - `backend/services/prompts/rag_sufficiency.py`
  - `backend/services/rag_sufficiency_prompt.py`
  - `backend/services/rag_sufficiency_judge.py`
  - `backend/services/query_understanding.py`
  - `backend/services/rag_context_budget.py`
  - `backend/services/rag_qa.py`
  - `backend/services/token_usage.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_rag_sufficiency_judge.py`
  - `backend/tests/test_token_usage.py`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - GPT-backed RAG scenes now default to `gpt-5.4` or `gpt-5.4-mini`, with no lingering `gpt-4.*` default path in the benchmark/cost model profiles.
  - Usage ledgers now carry provider, model, input/output/prompt/completion/cached/reasoning/tool/embedding token slots, plus unknown-cost markers for unpriced models.
  - Sufficiency judgment now reviews the same packed evidence context that answer generation saw, reducing answer/judge drift while keeping the conservative investigate policy unchanged.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_profiles.py backend/tests/test_token_usage.py backend/tests/test_rag_sufficiency_judge.py backend/tests/test_prompt_modules.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/llm_profiles.py backend/services/prompts/rag_sufficiency.py backend/services/rag_sufficiency_prompt.py backend/services/rag_sufficiency_judge.py backend/services/query_understanding.py backend/services/rag_context_budget.py backend/services/rag_qa.py backend/services/token_usage.py`

- Area or subsystem: RAG token-only telemetry, GPT-family fallback cleanup, and dashboard summary wording
- Prompt or model version: `gpt54-token-only-observability-v1`
- Summary: Kept GPT-backed RAG defaults inside the GPT-5.4 family, removed active cost presentation from benchmark/ticket dashboard flows, and retained future-ready usage-ledger fields so later prompt/model changes can add cached or reasoning token views without another schema rewrite.
- Reason: The next benchmark cycle needs trustworthy token telemetry and execution truth first. Presenting stale or partial pricing would create noise, while leaving older GPT fallback defaults in place would blur model-family comparisons.
- Affected files or config:
  - `backend/rag_api.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_qa.py`
  - `backend/services/token_usage.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_rag_benchmark_session.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_token_usage.py`
  - `ui/dashboard-ui/app.js`
  - `ui/dashboard-ui/rag/app.js`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - Active benchmark and ticket dashboards now show token-only usage summaries rather than mixed token/cost summaries.
  - The RAG answer fallback chain now stays inside the GPT-5.4 family by default.
  - Usage ledgers still carry future-ready token slots such as cached/reasoning/tool tokens even though the current UI only surfaces input/output/embedding totals.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_token_usage.py backend/tests/test_rag_scorecard_repository.py backend/tests/test_dashboard_ui_contract.py backend/tests/test_rag_benchmark_session.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/token_usage.py backend/repositories/knowledge_repository.py backend/rag_api.py backend/services/rag_benchmark_runner.py backend/services/rag_qa.py`
  - `node --check ui/dashboard-ui/rag/app.js`
  - `node --check ui/dashboard-ui/app.js`
- Area or subsystem: EC2 auto deploy daily report and docker-log AI diagnostics
- Prompt or model version: `auto-deploy-report-v1`
- Summary: Added a dedicated `auto_deploy_report` LLM scene for the EC2 auto-deploy日报 path, so every scheduled run can email a Chinese health summary with docker status, suspicious raw log excerpts, and an AI risk review without reusing product-facing engineer prompts.
- Reason: The old automation only emailed on failure and had no model-driven log inspection. The new daily report needs a separate low-cost, low-reasoning scene so ops reporting can evolve independently from client/product LLM behavior and still degrade safely when AI is unavailable.
- Affected files or config:
  - `backend/services/auto_deploy_report.py`
  - `backend/services/llm_profiles.py`
  - `scripts/ops/auto_deploy_ec2.sh`
  - `scripts/ops/build_auto_deploy_report.py`
  - `.env.example`
  - `deployment/systemd/auto-deploy.env.example`
  - `backend/tests/test_auto_deploy_report.py`
  - `backend/tests/test_auto_deploy_ec2.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_bootstrap_auto_deploy_ec2.py`
  - `docs/deploy_single_host_ec2.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - `supportportal-auto-deploy.service` now attempts to send one SES daily report on every run instead of only sending mail on failure.
  - The report subject now uses `SupportPortal Report <M/D>` on success and `[Failed] SupportPortal Report <M/D>` on failure.
  - When `OPENAI_API_KEY` is present, the report includes AI analysis of recent docker logs using the dedicated `auto_deploy_report` scene; otherwise the email still sends with an explicit unavailable note.
- Verification:
  - `python3 -m unittest backend.tests.test_bootstrap_auto_deploy_ec2 backend.tests.test_auto_deploy_report backend.tests.test_auto_deploy_ec2 backend.tests.test_llm_profiles backend.tests.test_workflow_scripts backend.tests.test_single_host_compose`
  - `bash -n scripts/ops/auto_deploy_ec2.sh scripts/ops/bootstrap_auto_deploy_ec2.sh deployment/deploy_ec2.sh`
  - `python3 -m py_compile backend/services/auto_deploy_report.py scripts/ops/build_auto_deploy_report.py backend/services/llm_profiles.py`
  - `git diff --check`

## 2026-04-04 - Simple FAQ prompt bypass for answer-first RAG

- Area or subsystem: Client AI technical RAG query-understanding, intent routing, and agentic retrieval planning
- Prompt or model version: `rag-simple-faq-light-path-v1`
- Summary: Added a simple lexical FAQ bypass so short `lexical_exact` questions such as `how to join channel` no longer invoke the query-understanding LLM stages or the agent planner. These requests now stay on a deterministic BM25/FTS-first path and rely on existing answer generation only after grounded evidence is already selected. The same query family now also bypasses the intent-router LLM when the route is an obvious `join channel` technical FAQ.
- Reason: The old prompt-driven planning path was spending too much latency budget on rewrites, decomposition, planner calls, vector retrieval, and rerank for very small FAQ-style requests. That made answerable questions miss the client timeout window and fall into engineer-ticket recovery even when grounded evidence already existed. In the live async path, transient sufficiency-judge failures could also override a valid grounded answer and even successful sufficiency-judge calls were adding another avoidable LLM hop after RAG had already finished.
- Affected files or config:
  - `backend/services/query_understanding.py`
  - `backend/services/rag_qa.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/tests/test_query_understanding.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - Obvious `join channel` FAQ prompts no longer call the intent-router prompt before being routed to Agora technical RAG.
  - Short `lexical_exact` FAQ queries bypass the query-understanding LLM rewrite/decomposition stages and return `fallback_mode="light_path"` with zero rewrite latency.
  - The agent planner prompt is no longer called for those same simple FAQ queries.
  - Generic grounded FAQ answers that already meet the evidence-quality keep rules now skip the sufficiency-judge prompt entirely instead of paying for a second LLM pass after answer generation.
  - Transient sufficiency-judge errors no longer force a handoff for generic grounded FAQ answers that already meet the evidence-quality keep rules.
  - Prompt/model selection for answer generation is unchanged; only the decision to invoke retrieval-planning prompts is narrowed to harder queries that benefit from them, and post-check failures on generic FAQ answers now degrade to answer-first behavior instead of investigation-first behavior.
- Verification:
  - `/Users/xieziling/.config/superpowers/venvs/SupportPortal-rag-join-channel-fix-min/bin/python -m unittest backend.tests.test_query_understanding backend.tests.test_rag_agentic backend.tests.test_rag_qa backend.tests.test_ticket_orchestrator`
  - `/Users/xieziling/.config/superpowers/venvs/SupportPortal-rag-join-channel-fix-min/bin/python -m py_compile backend/services/query_understanding.py backend/services/rag_qa.py backend/services/ticket_orchestrator.py`
  - `/Users/xieziling/.config/superpowers/venvs/SupportPortal-rag-join-channel-fix-min/bin/python - <<'PY' ... support_rag_query_runs WHERE ticket_id='T-1E51CE' ... PY` showed `query_understanding_meta.agent_iterations[0].tool_names == ['p_bm25', 'p_fts']`

## 2026-04-04 - Client-side ack model session and optimistic async ticket query

- Area or subsystem: Client AI initial acknowledgement, optimistic ticket query path, and async route/RAG cancellation behavior
- Prompt or model version: `client-ack-realtime-v1`
- Summary: Added a browser-side transient ack flow backed by a short-lived OpenAI Realtime client secret. The ack instructions are now isolated to a one-sentence acknowledgement prompt with no technical guidance, no engineer handoff promises, and no citations. On the backend, async-eligible `/api/tickets/query` requests no longer generate a server-side ack or run synchronous route analysis before returning; they now return immediately and let the worker resolve route and RAG in parallel.
- Reason: The initial client-visible response was being delayed by backend persistence plus routing work, even though the ack itself did not require server-side grounding. Splitting the ack into a client-side transient model call and removing synchronous route work from the first HTTP response reduces first-response latency while preserving server authority for the final grounded answer.
- Affected files or config:
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_service_client.py`
  - `backend/worker.py`
  - `ui/client-ui/app.js`
  - `deployment/docker-compose.single-host.yml`
  - `.env.example`
  - `backend/tests/test_client_ui_contract.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_rag_api.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_single_host_compose.py`
  - `backend/tests/test_worker.py`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Expected behavior change:
  - The first visible client ack is now transient UI state generated client-side, with a static fallback if the short-lived session is unavailable.
  - Async-eligible support queries no longer return a server-authored ack in `/api/tickets/query`.
  - Async-eligible support queries no longer run synchronous route analysis before the first HTTP response returns.
  - Worker-side route analysis can now cancel in-flight RAG best-effort, and cancelled RAG work is no longer surfaced as a normal `rag_unavailable` failure.
  - Route-analysis failure now defaults to the optimistic Agora technical RAG path instead of blocking the final answer.
- Verification:
  - `python3 -m py_compile backend/main.py backend/worker.py backend/rag_api.py backend/services/rag_qa.py backend/services/rag_service_client.py`
  - `python3 -m unittest backend.tests.test_client_ui_contract backend.tests.test_single_host_compose backend.tests.test_rag_service_client`
  - Container-backed backend verification pending after compose rebuild in this task.

## 2026-04-04 - Realtime ack session schema fix and realtime-capable default model

- Area or subsystem: Client AI transient acknowledgement session bootstrap
- Prompt or model version: `client-ack-realtime-v2`
- Summary: Updated the OpenAI Realtime client-secret request payload to the current session schema by switching the session fields from `modalities` / `max_response_output_tokens` to `output_modalities` / `max_output_tokens`, and changed the default transient ack model from `gpt-5-nano` to the Realtime-capable `gpt-realtime-mini`. The browser-side `response.create` event now uses `output_modalities` as well.
- Reason: The previous payload shape was rejected by OpenAI with HTTP 400, which forced every client ack request onto the static fallback path even when an API key was configured. The previous default model name was also not a Realtime default, so keeping it as the out-of-the-box value made the direct-browser ack flow brittle.
- Affected files or config:
  - `backend/main.py`
  - `ui/client-ui/app.js`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_client_ui_contract.py`
  - `backend/tests/test_single_host_compose.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - OpenAI client-secret bootstrap for transient client ack now follows the current Realtime session schema.
  - New environments default to a Realtime-capable ack model instead of a non-Realtime text model name.
  - Browser-side `response.create` requests ask for text output using `output_modalities`.
  - Teams can still override `CLIENT_ACK_MODEL` if they have another browser-safe Realtime alias.
- Verification:
  - `python3 -m py_compile backend/main.py`
  - `/Users/xieziling/.config/superpowers/venvs/SupportPortal-rag-join-channel-fix-min/bin/python -m unittest backend.tests.test_client_ui_contract backend.tests.test_single_host_compose`
  - `/Users/xieziling/.config/superpowers/venvs/SupportPortal-rag-join-channel-fix-min/bin/python - <<'PY' ... /api/client/ack/session smoke ... PY`

## 2026-04-04 - Client ack moved from Realtime session bootstrap to `gpt-5.4-nano`

- Area or subsystem: Client AI transient acknowledgement
- Prompt or model version: `client-ack-text-v1`
- Summary: Replaced the browser-side Realtime transient ack flow with a frontend-triggered, backend-issued text ack endpoint. The new endpoint calls `gpt-5.4-nano` through the Responses API with `reasoning_effort=none`, a short one-sentence acknowledgement prompt, and a small output cap. The client now gives the model a 1500 ms window before falling back to localized static copy.
- Reason: The Realtime flow still depended on browser-safe session bootstrap plus websocket delivery, and in practice it frequently failed to produce text before the user-visible fallback. The new text ack path keeps the ack model-generated while avoiding client-side Realtime transport complexity and preserving a bounded first-response budget.
- Affected files or config:
  - `backend/main.py`
  - `backend/services/llm_profiles.py`
  - `ui/client-ui/app.js`
  - `ui/client-ui/index.html`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_client_ui_contract.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_single_host_compose.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Client send flow now calls `/api/client/ack` instead of `/api/client/ack/session`.
  - The first transient ack is model-generated when the `gpt-5.4-nano` request returns within 1500 ms.
  - If the ack request times out, fails, or returns empty text, the client shows localized static fallback copy after the 1500 ms window.
  - Late model ack responses no longer overwrite fallback copy that is already visible.
  - The legacy Realtime session endpoint remains available, but the real client send flow no longer opens a Realtime websocket for transient ack generation.
- Verification:
  - `/Users/xieziling/.config/superpowers/venvs/SupportPortal-rag-join-channel-fix-min/bin/python -m unittest backend.tests.test_llm_profiles backend.tests.test_investigation_flow backend.tests.test_client_ui_contract backend.tests.test_single_host_compose`

- Date: 2026-04-04
- Area or subsystem: Client session product selection, router prompt, and RAG answer prompt
- Prompt or model version: `rag-v4-product-scope`
- Summary: Added product-scoped prompt context for new client sessions so the router and RAG answer prompt both receive the same persisted product selection (`Audio/Video Calling` or `Cloud Recording`) at request time, while legacy sessions without a product stay on the generic prompt path.
- Reason: The client now requires users to choose the product before the first message, and that selection needs to steer prompt behavior consistently across synchronous answers, async worker execution, and internal `/internal/rag/query` calls.
- Affected files or config:
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/services/prompts/rag_answer.py`
  - `backend/services/prompts/router.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_service_client.py`
  - `backend/services/support_products.py`
  - `backend/services/support_router.py`
  - `backend/services/support_router_prompt.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/worker.py`
  - `ui/client-ui/app.js`
  - `ui/client-ui/index.html`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - New and empty client sessions must select a product before the first send, and that product is reused as prompt context for route classification and docs-grounded answer generation.
  - `Audio/Video Calling` questions bias prompt scope toward RTC/channel/join/publish/subscribe support language, while `Cloud Recording` questions bias prompt scope toward recording lifecycle and recording API language.
  - Legacy non-empty sessions without a stored product still use the generic Agora technical prompt path and are not blocked.
- Verification:
  - `uv run --with pytest --with fastapi --with pydantic --with python-dotenv --with python-multipart --with redis --with httpx --with 'psycopg[binary]' python -m pytest -q backend/tests/test_client_ui_contract.py backend/tests/test_investigation_flow.py backend/tests/test_repository_configuration.py backend/tests/test_rag_service_client.py backend/tests/test_support_router.py backend/tests/test_rag_api.py backend/tests/test_ticket_orchestrator.py backend/tests/test_worker.py backend/tests/test_prompt_modules.py`
  - `python3 scripts/verify_feature_list.py`
  - `python3 -m py_compile backend/main.py backend/rag_api.py backend/services/prompts/rag_answer.py backend/services/prompts/router.py backend/services/rag_qa.py backend/services/rag_service_client.py backend/services/support_products.py backend/services/support_router.py backend/services/support_router_prompt.py backend/services/ticket_orchestrator.py backend/worker.py`
  - `node --check ui/client-ui/app.js`
  - `git diff --check`

- Date: 2026-04-04
- Area or subsystem: Product-scoped RAG planner, RAG answer, and troubleshooting intake prompts
- Prompt or model version: `rag-v5-product-troubleshooting-intake`
- Summary: Upgraded product-scoped prompt wiring so Audio/Video Calling and Cloud Recording now inject explicit product role text into the RAG planner and answer prompts, and added a dedicated troubleshooting intake prompt/model path that asks customers for missing investigation identifiers before opening engineer tickets.
- Reason: Product selection should not only bias answer phrasing; it also needs to steer retrieval planning and troubleshooting intake so symptom-style issues collect the minimum product-specific investigation data instead of escalating too early.
- Affected files or config:
  - `backend/rag_api.py`
  - `backend/services/llm_profiles.py`
  - `backend/services/prompts/rag_agent_planner.py`
  - `backend/services/prompts/rag_answer.py`
  - `backend/services/prompts/troubleshooting_intake.py`
  - `backend/services/rag_qa.py`
  - `backend/services/support_products.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/services/troubleshooting_intake.py`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Product-scoped RAG planner prompts now plan retrieval as Agora support for the selected product instead of using a generic retrieval role.
  - Product-scoped RAG answer prompts now answer as Agora support for the selected product instead of only receiving a generic product scope section.
  - When RAG returns `rag_insufficient_evidence`, troubleshooting-style issues now use a dedicated intake prompt/model path to decide whether to ask for `channel_name` / `problematic_uid` / `issue_timestamp` or `sid` / `issue_timestamp` before engineer escalation.
- Verification:
  - `uv run --with pytest --with fastapi --with pydantic --with python-dotenv --with python-multipart --with redis --with httpx --with 'psycopg[binary]' python -m pytest -q backend/tests/test_prompt_modules.py backend/tests/test_ticket_orchestrator.py backend/tests/test_troubleshooting_intake.py backend/tests/test_investigation_flow.py backend/tests/test_worker.py backend/tests/test_llm_profiles.py backend/tests/test_rag_qa.py backend/tests/test_rag_api.py`
  - `python3 scripts/verify_feature_list.py`
  - `python3 -m py_compile backend/rag_api.py backend/services/llm_profiles.py backend/services/prompts/rag_agent_planner.py backend/services/prompts/rag_answer.py backend/services/prompts/troubleshooting_intake.py backend/services/rag_qa.py backend/services/support_products.py backend/services/ticket_orchestrator.py backend/services/troubleshooting_intake.py`
- `git diff --check`

- Date: 2026-04-04
- Area or subsystem: Client acknowledgement experiment routing and benchmark instrumentation
- Prompt or model version: `client-ack-experiment-v1`
- Summary: Added runtime-selectable client acknowledgement experiment modes so the UI can compare the existing `gpt-5.4-nano` proxy-text acknowledgement against a browser Realtime WebRTC acknowledgement path (`gpt-realtime-mini`) and optionally shadow-run the non-visible path for latency benchmarking.
- Reason: We need measurable evidence before changing the default acknowledgement path, including comparable latency, fallback-hit rate, and empty/error rate across the safe proxy-text and browser-Realtime implementations.
- Affected files or config:
  - `backend/main.py`
  - `ui/client-ui/app.js`
  - `ui/client-ui/index.html`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `CLIENT_ACK_EXPERIMENT_MODE`
  - `CLIENT_ACK_FALLBACK_TIMEOUT_MS`
  - `CLIENT_ACK_BENCHMARK_ENABLED`
- Expected behavior change:
  - The default client acknowledgement path remains `proxy_text`, still using `gpt-5.4-nano` with `reasoning_effort=none`.
  - Runtime config can switch the visible path to `browser_realtime` or enable `dual_shadow`, where the UI keeps rendering the primary path while recording benchmark metrics for the hidden Realtime path.
  - The client now records normalized acknowledgement timing metrics and can post benchmark samples plus aggregate reports without changing durable ticket history.
- Verification:
  - `source /tmp/supportportal-finalize-venv/bin/activate && python -m unittest backend.tests.test_client_ui_contract backend.tests.test_investigation_flow backend.tests.test_single_host_compose backend.tests.test_llm_profiles`
  - `node --check ui/client-ui/app.js`

- Date: 2026-04-04
- Area or subsystem: Client acknowledgement prompt delivery
- Prompt or model version: `client-ack-proxy-only-v2`
- Summary: Removed the client acknowledgement experiment modes and reverted the UI to a single proxy-text acknowledgement path that always calls `/api/client/ack` with `gpt-5.4-nano`, while increasing the proxy timeout and UI fallback budget from `1.25s/1500ms` to `2.0s/2000ms`.
- Reason: The experiment modes added dead paths and extra runtime/config surface area without improving the acknowledgement SLA, so the client needs one predictable proxy-only path with a slightly larger budget before falling back to static copy.
- Affected files or config:
  - `backend/main.py`
  - `backend/services/llm_profiles.py`
  - `ui/client-ui/app.js`
  - `ui/client-ui/index.html`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `CLIENT_ACK_TIMEOUT_SECONDS`
  - `CLIENT_ACK_FALLBACK_TIMEOUT_MS`
- Expected behavior change:
  - Client send flows no longer fetch runtime ack config, request Realtime sessions, or post benchmark samples.
  - The only transient model ack path is `/api/client/ack`, using `gpt-5.4-nano` with `reasoning_effort=none`.
  - The UI now waits up to `2000ms` for the proxy model ack before rendering localized static fallback copy.
- Verification:
  - `source /tmp/supportportal-finalize-venv/bin/activate && python -m unittest backend.tests.test_llm_profiles backend.tests.test_single_host_compose backend.tests.test_investigation_flow backend.tests.test_client_ui_contract`
  - `python3 -m py_compile backend/main.py backend/services/llm_profiles.py`
  - `node --check ui/client-ui/app.js`
  - `git diff --check`

- Date: 2026-04-04
- Area or subsystem: Client acknowledgement fallback behavior
- Prompt or model version: `client-ack-proxy-only-v3`
- Summary: Increased the client-side fallback threshold for transient `gpt-5.4-nano` acknowledgements from `2000ms` to `3000ms` while keeping the proxy-text path and model configuration unchanged.
- Reason: Live ack requests are landing slightly above two seconds, so the UI needs a longer wait budget before falling back to static acknowledgement copy.
- Affected files or config:
  - `ui/client-ui/app.js`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `docs/prompt_change_log.md`
  - `CLIENT_ACK_FALLBACK_TIMEOUT_MS`
- Expected behavior change:
  - The client now waits up to `3000ms` before rendering localized static fallback acknowledgement text.
  - Successful `gpt-5.4-nano` acknowledgements between `2000ms` and `3000ms` can now render as transient model ack messages instead of being preempted by fallback.
- Verification:
  - `source /tmp/supportportal-finalize-venv/bin/activate && python -m unittest backend.tests.test_client_ui_contract backend.tests.test_single_host_compose`
  - `node --check ui/client-ui/app.js`
  - `git diff --check`

- Date: 2026-04-04
- Area or subsystem: RAG answer generation for light-path lexical FAQ queries
- Prompt or model version: `rag-v5-light-path-fast-answer`
- Summary: Added a light-path answer-model fast lane so agentic lexical FAQ queries that already pass the round-one judge try `gpt-5.4-mini` with `low` reasoning first, then automatically fall back to the existing primary `rag_answer` model if the mini response is ungrounded, lacks valid citations, or returns invalid JSON.
- Reason: The simple lexical FAQ path already has tight evidence constraints and does not need the full latency cost of the default answer model on every request, but it still needs the same grounded-answer JSON contract and safe fallback behavior when the lighter model underperforms.
- Affected files or config:
  - `backend/services/rag_qa.py`
  - `backend/rag_api.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_api.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - `lexical_exact` light-path queries that reach `judge=answer_now` now attempt answer generation with `gpt-5.4-mini` and `reasoning_effort=low`.
  - If that mini response has invalid JSON, invalid citations, or `insufficient_evidence=true` despite grounded overlap, the same request automatically retries on the main `rag_answer` model before any extractive fallback or escalation decision is exposed.
  - RAG diagnostics now record which answer profile actually served the response and whether the fast-answer fallback path was used.
- Verification:
  - `/Users/xieziling/.config/superpowers/worktrees/SupportPortal/rag-latency-opt/.venv/bin/python -m unittest backend.tests.test_rag_qa backend.tests.test_rag_api backend.tests.test_knowledge_repository_bm25`

- Date: 2026-04-04
- Area or subsystem: Client ticket main-agent runtime prompt/config routing
- Prompt or model version: `client-ticket-agents-v1`
- Summary: Introduced agent-namespaced prompt/model configuration for the explicit `main agent` runtime so `route agent`, `rag agent`, and `review agent` can resolve their own model profiles while still falling back to the existing legacy scenario env names.
- Reason: The new runtime needs independently tunable route, web-search, answer, post-check, and intake behavior without breaking current deployments that still rely on legacy `INTENT_ROUTER_*`, `RAG_ANSWER_*`, `RAG_SUFFICIENCY_JUDGE_*`, and `TROUBLESHOOTING_INTAKE_*` environment names.
- Affected files or config:
  - `backend/services/llm_profiles.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `docs/prompt_change_log.md`
  - `ROUTE_AGENT_ROUTER_*`
  - `ROUTE_AGENT_WEB_SEARCH_*`
  - `RAG_AGENT_PLANNER_*`
  - `RAG_AGENT_QUERY_EXPANSION_*`
  - `RAG_AGENT_ANSWER_*`
  - `RAG_AGENT_CONTEXT_COMPRESSION_*`
  - `REVIEW_AGENT_POSTCHECK_*`
  - `REVIEW_AGENT_INTAKE_*`
- Expected behavior change:
  - The client ticket flow runs `route agent` and `rag agent` in parallel and only waits on `review agent` for high-risk grounded answers or `rag_insufficient_evidence`.
  - Agent-named envs now override the older scenario env names, while legacy names continue to work as compatibility fallbacks.
  - The client-ticket execution path now converges on the explicit main-agent runtime instead of a separate legacy ticket orchestrator.
- Verification:
  - `/Users/xieziling/.config/superpowers/worktrees/SupportPortal/client-agent-runtime/.venv/bin/python -m unittest backend.tests.test_client_ticket_agent_runtime backend.tests.test_llm_profiles backend.tests.test_worker -q`
  - `python3 -m py_compile backend/main.py backend/worker.py backend/services/client_ticket_agent_runtime.py backend/services/llm_profiles.py`
  - `git diff --check`

- Date: 2026-04-04
- Area or subsystem: Client acknowledgement timeout budget
- Prompt or model version: `client-ack-proxy-only-v4`
- Summary: Unified the transient client acknowledgement budget to `5.0s/5000ms` and preserved late `gpt-5.4-nano` overwrites by keeping the ack request alive after the static fallback appears.
- Reason: Live client ack requests are frequently landing between three and five seconds, so the frontend fallback and backend model timeout need the same longer budget to increase the chance that users see model-generated acknowledgement text.
- Affected files or config:
  - `ui/client-ui/app.js`
  - `backend/services/llm_profiles.py`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `docs/prompt_change_log.md`
  - `CLIENT_ACK_TIMEOUT_SECONDS`
  - `CLIENT_ACK_FALLBACK_TIMEOUT_MS`
- Expected behavior change:
  - The client now waits up to `5000ms` before rendering localized static fallback acknowledgement text.
  - The backend now gives the `gpt-5.4-nano` client-ack scenario up to `5.0s` by default instead of `2.0s`.
  - If the static fallback has already rendered and a later non-empty model acknowledgement arrives, the model text overwrites the fallback instead of being dropped.
- Verification:
  - `python3 -m unittest backend.tests.test_client_ui_contract backend.tests.test_llm_profiles backend.tests.test_single_host_compose -q`
  - `node --check ui/client-ui/app.js`
  - `python3 -m py_compile backend/services/llm_profiles.py`
  - `git diff --check`

- Date: 2026-04-04
- Area or subsystem: Client acknowledgement rendering precedence
- Prompt or model version: `client-ack-proxy-only-v5`
- Summary: Stopped rendering server-side placeholder acknowledgements ahead of the client ack path so the UI now waits for the `gpt-5.4-nano` ack first and only falls back to the static template after the full `5000ms` threshold.
- Reason: The UI could still show a rule-based `server_ack` template immediately on sync paths, which made users see template copy before the model acknowledgement even though the fallback budget had already been moved to five seconds.
- Affected files or config:
  - `ui/client-ui/app.js`
  - `backend/tests/test_client_ui_contract.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - `POST /api/tickets/query` responses marked with `ack_source=server_ack` no longer render that placeholder directly into the local transcript.
  - If no real assistant reply has arrived yet, the client ack request remains alive after the sync request finishes so the GPT acknowledgement can still render before fallback.
  - Static fallback acknowledgement text only appears after the `5000ms` timeout, and only when no model acknowledgement or real assistant answer has already arrived.
- Verification:
  - `python3 -m unittest backend.tests.test_client_ui_contract backend.tests.test_llm_profiles backend.tests.test_single_host_compose -q`
  - `node --check ui/client-ui/app.js`
  - `git diff --check`

- Date: 2026-04-05
- Area or subsystem: Client ticket agent runtime hard cutover and config deprecation warnings
- Prompt or model version: `client-ticket-agents-v2`
- Summary: Removed the client-ticket runtime mode switch, hard-cut the serving path to the explicit `main agent` runtime, and surfaced deprecated legacy env aliases as startup/health warnings while keeping agent-namespaced prompt/model configs authoritative.
- Reason: The repo no longer needs two ticket-side orchestration stacks, and keeping the runtime-mode flag plus silent legacy env fallbacks made the active execution path harder to reason about and observe.
- Affected files or config:
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/services/llm_profiles.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `backend/tests/test_ticket_routing.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `backend/tests/test_llm_profiles.py`
  - `docs/prompt_change_log.md`
  - `ROUTE_AGENT_*`
  - `RAG_AGENT_*`
  - `REVIEW_AGENT_*`
- Expected behavior change:
  - `POST /api/tickets/query` now always executes through the `main agent` path and reports only `main_agent_async`, `main_agent_sync`, or `active_investigation_followup`.
  - Legacy scenario env aliases still resolve for one compatibility window, but `/health.config_warnings` and startup logs now explicitly report which deprecated env names are in use.
  - The benchmark runner now adapts offline RAG output into the same shared main-agent execution contracts instead of composing a separate ticket-side orchestration stack.
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_client_ticket_agent_runtime backend.tests.test_ticket_orchestrator backend.tests.test_ticket_routing backend.tests.test_trace_client_ticket_route_cli backend.tests.test_llm_profiles backend.tests.test_rag_benchmark_runner backend.tests.test_investigation_flow backend.tests.test_worker -q`
  - `./.venv/bin/python -m py_compile backend/services/client_ticket_agent_runtime.py backend/services/ticket_orchestrator.py backend/services/rag_benchmark_runner.py backend/services/llm_profiles.py backend/main.py backend/worker.py scripts/trace_client_ticket_route.py`
  - `git diff --check`

- Date: 2026-04-05
- Area or subsystem: Client acknowledgement prompt tone
- Prompt or model version: `client-ack-concierge-v1`
- Summary: Tightened the client ack system prompt so the `gpt-5.4-nano` acknowledgement is explicitly generated in a concierge-style voice while preserving the existing one-sentence, non-technical, no-escalation constraints.
- Reason: The previous prompt produced acknowledgements that were correct but too generic and operational; the client-facing first touch should feel more polished and concierge-like without changing routing or fallback behavior.
- Affected files or config:
  - `backend/main.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Client ack generations should sound warmer and more polished by default instead of purely transactional.
  - The model is still constrained to exactly one acknowledgement sentence in the user's language.
  - The client ack still avoids technical guidance, source citations, and engineer-escalation promises.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_investigation_flow.InvestigationFlowTests.test_client_ack_prompt_instructions_require_concierge_style backend.tests.test_investigation_flow.InvestigationFlowTests.test_client_ack_returns_model_text_and_latency -q`
  - `python3 -m py_compile backend/main.py backend/tests/test_investigation_flow.py`
  - `git diff --check`

- Date: 2026-04-05
- Area or subsystem: Client new-session welcome copy
- Prompt or model version: `client-empty-session-welcome-v2`
- Summary: Removed the empty-session hero block above the welcome bubble and updated the transient welcome copy to a title-cased Agora Support greeting.
- Reason: The new-session screen was visually redundant with both a hero and a welcome bubble, and the welcome sentence needed the revised product-facing wording.
- Affected files or config:
  - `ui/client-ui/app.js`
  - `ui/client-ui/styles.css`
  - `ui/client-ui/index.html`
  - `backend/tests/test_client_ui_contract.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Empty draft sessions no longer render the icon/title/description hero block before product selection.
  - The first visible assistant-style message in a new session now reads `Thank you for contacting Agora Support! How may I help you today?`
  - The welcome bubble remains transient-only and still disappears once the first real user message exists.
- Verification:
  - `python -m unittest backend.tests.test_client_ui_contract`
  - `node --check ui/client-ui/app.js`
  - `git diff --check`

- Date: 2026-04-22
- Area or subsystem: Client front-door input guardrail
- Prompt or model version: `input-guardrail-front-door-v1-default-disabled`
- Summary: Default-disabled the front-door OpenAI input guardrail at admission time behind `INPUT_GUARDRAIL_ENABLED`, so the existing route, RAG, review, and investigation chain resumes unchanged unless the feature flag is explicitly turned on.
- Reason: `TK-174` and `TK-175` showed that normal technical questions could be blocked at the front door, so the safest rollback is to bypass guardrail evaluation globally by default while preserving the implementation and tests for explicit re-enable.
- Affected files or config:
  - `backend/main.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/prompt_change_log.md`
  - `INPUT_GUARDRAIL_ENABLED`
- Expected behavior change:
  - `/api/tickets/query` now skips the front-door input guardrail by default and continues directly into the existing main route and runtime chain.
  - Normal technical questions should no longer return `answer_route=guardrail` or `processing_mode=input_guardrail_blocked` unless `INPUT_GUARDRAIL_ENABLED=true`.
  - Guardrail-specific block behavior remains available for future explicit re-enable, and dedicated tests still cover the enabled blocking path.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_investigation_flow -q`
  - `bash scripts/workflow/inspect_single_host_stack_mode.sh`
  - `bash scripts/workflow/restart_single_host_lightweight_stack.sh`
  - `curl -fsS http://127.0.0.1:8080/health`
  - `python3 /Users/xieziling/.codex/skills/supportportal-run-report/scripts/run_supportportal_run_report.py`

- Date: 2026-04-22
- Area or subsystem: Client support routing and non-technical web-search answering
- Prompt or model version: `router-v2 + web-search-v3`
- Summary: Added a product-portfolio routing path for Agora product-overview questions and upgraded the non-technical web-search prompt so broadcasting-related product inquiries lead with `Broadcast Streaming` versus `Interactive Live Streaming`, grouped official product coverage, and no Console-first guidance.
- Reason: `TK-165` showed that product-portfolio questions about broadcasting were falling into technical-docs RAG and being answered with Console usage guidance instead of official product-overview guidance.
- Affected files or config:
  - `backend/services/support_router.py`
  - `backend/services/support_router_prompt.py`
  - `backend/services/prompts/router.py`
  - `backend/services/prompts/web_search.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Messages asking what Agora products exist, which Agora product fits a scenario, or requesting broadcasting-oriented product guidance now fast-path to `agora_non_technical -> web_search` with reason `agora_product_portfolio`.
  - Product-portfolio web-search answers now stay on official Agora product pages, explain `Broadcast Streaming` versus `Interactive Live Streaming` first when broadcasting is mentioned, and then summarize grouped products or add-ons instead of redirecting to Console.
  - If the customer asks to connect with someone, the answer keeps the product overview as the main body and only adds a short official sales-contact CTA at the end.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_support_router.py backend/tests/test_prompt_modules.py backend/tests/test_client_ticket_agent_runtime.py`

- Date: 2026-04-22
- Area or subsystem: Client front-door input guardrail
- Prompt or model version: `input-guardrail-front-door-v1`
- Summary: Added a dedicated OpenAI Agents SDK front-door input guardrail scene and blocking response path ahead of ticket subject generation and the existing route/main-agent chain, with a unified safe-restate reply for blocked turns.
- Reason: The ticket admission path needed a conservative boundary check for jailbreak or prompt injection attempts, obvious abuse, PII, and clearly invalid or dangerous inputs without changing the existing route agent, RAG, review, or investigation workflow for normal technical questions.
- Affected files or config:
  - `backend/main.py`
  - `backend/services/openai_input_guardrail.py`
  - `backend/services/llm_profiles.py`
  - `backend/tests/test_openai_input_guardrail.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Every `/api/tickets/query` turn now runs a blocking front-door guardrail before `derive_subject()`, route decisions, RAG, review, async queueing, investigation refresh, or deferred sentiment tagging.
  - Normal technical questions such as `how to join channel` still pass through to the existing route and RAG flow when the guardrail allows them.
  - Blocked turns now persist only a sanitized placeholder customer message plus one unified safe-restate reply, with route metadata under `answer_route=guardrail` and category-specific `route_reason` values like `input_guardrail_pii`.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_openai_input_guardrail backend.tests.test_llm_profiles backend.tests.test_investigation_flow -q`

- Date: 2026-04-17
- Area or subsystem: Client query-understanding rewrite and answer-first fallback policy
- Prompt or model version: `query-rewrite-v1 + client_accuracy_first overlay`
- Summary: Added a client-only query-rewrite prompt overlay that keeps onboarding/how-to intent in natural language, avoids glossary-only keyword bags, and pairs with answer-first client fallback rules for grounded how-to/configuration answers.
- Reason: `TK-140` produced an unnatural rewrite (`Go Agora SDK join channel channel name same channel`) and then fell through to clarify/investigation because the client path treated weakly grounded onboarding questions like troubleshooting intake instead of answer-first guidance.
- Affected files or config:
  - `backend/services/prompts/query_understanding.py`
  - `backend/services/query_understanding.py`
  - `backend/services/rag_qa.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/tests/test_query_understanding.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Client how-to/onboarding/configuration questions should keep intent-preserving rewrites instead of collapsing into glossary bags.
  - Long `join channel` onboarding questions now continue to qualify for generic-join deterministic guidance even when they do not start with `how to`.
  - When the client path has enough grounded support for a how-to answer, it responds first and only appends light follow-up requests as a fallback instead of jumping straight to investigation intake.
- Verification:
  - `/Users/xieziling/.config/superpowers/worktrees/SupportPortal/client-rag-accuracy-first/.venv/bin/python -m unittest backend.tests.test_query_understanding backend.tests.test_rag_qa backend.tests.test_ticket_orchestrator backend.tests.test_client_ticket_agent_runtime`
  - Live `$supportportal-run-report` verification on `TK-140` and the client real-case batch was run after the merged stack served the new build; results are summarized in the final task report.

- Date: 2026-04-16
- Area or subsystem: Client product selection and empty-session entry flow
- Prompt or model version: `product-selection-v1`
- Summary: Added a dedicated product-selection prompt/model scene that infers `Audio/Video Calling (RTC)` vs `Cloud Recording` from the customer message, removed the client welcome bubble/manual selector, and asks an email-style confirmation only when the product is still ambiguous.
- Reason: Manual product selection blocked the first message and tied product-aware prompting to a transient UI control instead of the conversation itself, which made the product context brittle for existing tickets and follow-up turns.
- Affected files or config:
  - `backend/services/prompts/product_selection.py`
  - `backend/services/product_selection.py`
  - `backend/services/llm_profiles.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `ui/client-ui/app.js`
  - `ui/client-ui/styles.css`
  - `ui/client-ui/index.html`
  - `backend/tests/test_client_ui_contract.py`
  - `backend/tests/test_product_selection.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_worker.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Empty client sessions no longer render a welcome email bubble or manual product selector before the first question.
  - Technical turns without a stored product now run through a dedicated product-selection scene before route/RAG/intake prompts receive product context.
  - Explicit customer corrections can switch the stored product mid-ticket, and ambiguous technical turns now get an email-style confirmation asking `Audio/Video Calling (RTC)` vs `Cloud Recording`.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_client_ui_contract backend.tests.test_repository_configuration backend.tests.test_investigation_flow backend.tests.test_product_selection backend.tests.test_llm_profiles backend.tests.test_prompt_modules backend.tests.test_worker`
  - `node --check ui/client-ui/app.js`
  - `python3 scripts/verify_feature_list.py`
  - `python3 /Users/xieziling/.codex/skills/supportportal-run-report/scripts/run_supportportal_run_report.py`

- Date: 2026-04-16
- Area or subsystem: Client durable reply formatting and engineer investigation follow-up drafting
- Prompt or model version: `customer-reply-email-composer-v1`, `engineer-investigation-reply-v7`
- Summary: Standardized durable customer-facing replies into formal email-style messages with localized greeting/signoff, requester-aware personalization, and shared formatting across direct RAG answers, clarification requests, investigation updates, and engineer approval replies.
- Reason: The client assistant was returning terse chat-style wording such as one-line troubleshooting instructions, while the desired customer experience is a polished email-style response that still preserves grounded technical content and existing citations/approval flows.
- Affected files or config:
  - `backend/services/customer_reply_composer.py`
  - `backend/services/rag_qa.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/investigation_flow.py`
  - `backend/services/engineer_agent.py`
  - `backend/services/prompts/engineer_investigation_reply.py`
  - `backend/services/rag_service_client.py`
  - `backend/rag_api.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `ui/client-ui/app.js`
  - `backend/tests/test_customer_reply_composer.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_client_ui_contract.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Durable client-facing assistant replies now render as email-style messages with `Hi {name|there},`, a contextual polite opener, grounded body text and steps, and `Best Regards,` followed by `Sid` for English.
  - Non-English durable replies keep the customer language and use the localized salutation/signoff instead of forcing English formatting.
  - `/api/tickets/query` and `/internal/rag/query` now accept an optional `requester` display name so customer greetings can use the real name when available without exposing raw `customer_id` or email-like identifiers.
  - Engineer investigation follow-up drafts now ask for a final sendable email reply by prompt contract, and backend normalization preserves approval safety for older/plain drafts by wrapping them into the same email format before send.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_customer_reply_composer backend.tests.test_troubleshooting_intake backend.tests.test_client_ticket_agent_runtime backend.tests.test_rag_service_client backend.tests.test_worker backend.tests.test_prompt_modules backend.tests.test_rag_qa.RagQaHybridTests.test_build_answer_text_formats_email_style_response backend.tests.test_rag_qa.RagQaHybridTests.test_run_rag_query_short_black_screen_guidance_uses_deterministic_answer_profile backend.tests.test_investigation_flow.InvestigationFlowTests.test_default_public_investigation_reply_uses_email_style backend.tests.test_investigation_flow.InvestigationFlowTests.test_engineer_internal_message_uses_investigation_reply_model_and_records_metadata backend.tests.test_investigation_flow.InvestigationFlowTests.test_confirmation_approve_sends_customer_reply_and_closes_investigation backend.tests.test_investigation_flow.InvestigationFlowTests.test_build_query_task_includes_execution_snapshot_fields backend.tests.test_client_ui_contract.ClientUiContractTests.test_client_query_payload_includes_requester_name -q`
  - `python3 -m py_compile backend/services/customer_reply_composer.py backend/services/rag_qa.py backend/services/troubleshooting_intake.py backend/services/client_ticket_agent_runtime.py backend/services/investigation_flow.py backend/services/engineer_agent.py backend/services/prompts/engineer_investigation_reply.py backend/services/rag_service_client.py backend/rag_api.py backend/main.py backend/worker.py`

- Date: 2026-04-16
- Area or subsystem: Troubleshooting intake customer-facing clarification
- Prompt or model version: `troubleshooting-intake-v3`
- Summary: Rewrote missing-information clarification guidance to use appreciative customer-facing openings, forbid `Known so far` / internal-evaluation phrasing, and ask only for the still-missing troubleshooting or answer-mode fields.
- Reason: `TK-122` and related follow-ups were surfacing mechanical recap-style replies and internal terms such as `grounded answer`, which were not customer-facing and also encouraged structured detail turns to overwrite the original issue symptom.
- Affected files or config:
  - `backend/services/prompts/troubleshooting_intake.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Investigation-mode clarification replies now start with a short thanks, ask only for the remaining fields, and avoid recapping known information back to the customer.
  - Partial timestamp follow-ups now ask only for the missing timestamp part, such as `issue timezone` or `issue time and timezone`, instead of falling back to `full issue timestamp`.
  - Answer-mode clarification replies no longer mention `grounded answer`, `support evidence`, or similar internal evaluation language.
  - Structured investigation follow-ups such as `channel name..., uid..., happened on...` preserve the previously known `issue_symptom` instead of overwriting it with the follow-up sentence.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_troubleshooting_intake backend.tests.test_client_ticket_agent_runtime backend.tests.test_investigation_flow`

- Date: 2026-04-15
- Area or subsystem: Client ticket route classification for gratitude follow-ups
- Prompt or model version: `intent-router-ticket-resolution-v1`
- Summary: Added a dedicated `ticket_resolution` routing scope, removed gratitude phrases from lexical `small_talk` hints, and taught the router prompt to use ticket context to distinguish “resolve this case” confirmations from generic thanks.
- Reason: `TK-114` misclassified `got it, thanks` as `small_talk`, while `TK-113` could reopen investigation after `it worked, thanks!` because the router had no explicit contract for customer-confirmed resolution after a substantive support reply.
- Affected files or config:
  - `backend/services/prompts/router.py`
  - `backend/services/support_router_prompt.py`
  - `backend/services/support_router.py`
  - `backend/services/ticket_resolution.py`
  - `backend/tests/test_support_router.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Gratitude-only follow-ups can now route to `ticket_resolution` when recent context shows a substantive client-visible support reply and there are no remaining-problem signals.
  - Generic thanks without resolution context still stays in general chat instead of falling into Agora technical fallback.
  - The router prompt now explicitly distinguishes `ticket_resolution` from `small_talk`.
- Verification:
  - `python -m unittest backend.tests.test_support_router backend.tests.test_client_ticket_agent_runtime`
  - `python -m py_compile backend/services/ticket_resolution.py backend/services/support_router.py backend/services/support_router_prompt.py backend/services/prompts/router.py backend/services/client_ticket_agent_runtime.py`
  - `podman run --rm -v /Users/xieziling/.config/superpowers/worktrees/SupportPortal/ticket-resolution-gratitude-followups:/app -w /app localhost/supportportal-app:latest python -m unittest backend.tests.test_investigation_flow.InvestigationFlowTests.test_ticket_query_customer_resolved_confirmation_returns_resolved_and_records_auto_close_event backend.tests.test_investigation_flow.InvestigationFlowTests.test_ticket_query_active_engineer_case_resolution_closes_case_without_refreshing_investigation backend.tests.test_investigation_flow.InvestigationFlowTests.test_ticket_query_engineer_guidance_confirmation_resolves_when_route_agent_fails`

- Date: 2026-04-14
- Area or subsystem: Engineer investigation reply gate
- Prompt or model version: `engineer-investigation-reply-v4`
- Summary: Relaxed the engineer investigation gate so symptom-level customer replies can move to approval when there is verifiable proof plus a concrete next step, even if the engineer did not provide an explicit conclusion.
- Reason: The previous gate treated `conclusion` as a universal hard blocker, which kept symptom-level workaround replies in `active` even when the evidence and customer-safe action were already sufficient.
- Affected files or config:
  - `backend/services/prompts/engineer_investigation_reply.py`
  - `backend/services/engineer_agent.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_prompt_modules.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - `proof + next step` is now enough to approve a symptom-level customer draft when the proof anchors are verifiable and the wording stays conservative.
  - Missing `conclusion` no longer blocks symptom-level approval by itself.
  - Root-cause replies still require explicit, defensible conclusion wording and will fail closed if a no-conclusion draft overstates the root cause.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_investigation_flow.py backend/tests/test_prompt_modules.py`
  - `python3 -m py_compile backend/services/engineer_agent.py backend/services/prompts/engineer_investigation_reply.py backend/tests/test_investigation_flow.py backend/tests/test_prompt_modules.py`
  - `git diff --check`
  - `python3 /Users/xieziling/.codex/skills/supportportal-run-report/scripts/run_supportportal_run_report.py`

- Date: 2026-04-14
- Area or subsystem: Engineer investigation reply gating
- Prompt or model version: `engineer-investigation-reply-v3`
- Summary: Relaxed the engineer investigation reply gate so verified symptom-level evidence plus a conservative workaround can move to approval, while still rejecting customer drafts that overstate an unconfirmed root cause.
- Reason: `TK-106-1` showed that the previous gate treated optional root-cause classification and environment diagnostics as hard blockers even when the logs already supported a safe symptom-level customer reply.
- Affected files or config:
  - `backend/services/prompts/engineer_investigation_reply.py`
  - `backend/services/engineer_agent.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Engineer AI can now approve a customer-safe draft when the engineer provides traceable logs/errors, a symptom-level conclusion, and a conservative workaround or retest step, even if the exact root-cause category is still unconfirmed.
  - Optional diagnostics such as browser/OS/version, surrounding log context, permission status, and later root-cause classification are tracked as advisory follow-ups instead of hard blockers when the reply stays at symptom level.
  - If a symptom-scope draft or conclusion overstates the root cause, the backend forces the investigation back to `active` and asks the engineer to rewrite it at symptom level.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_investigation_flow.py backend/tests/test_prompt_modules.py backend/tests/test_llm_profiles.py`
  - `python3 -m py_compile backend/services/engineer_agent.py backend/services/prompts/engineer_investigation_reply.py backend/tests/test_investigation_flow.py backend/tests/test_prompt_modules.py backend/tests/test_llm_profiles.py`

- Date: 2026-04-13
- Area or subsystem: Engineer investigation reply drafting and approval gate
- Prompt or model version: `engineer-investigation-reply-v2`
- Summary: Tightened the engineer investigation reply prompt to require an explicit `reply_readiness` review object, then wired backend/UI approval gating to that validated readiness so customer drafts only become approvable when conclusion, proof, and solution-or-next-step are all explicit and internally supported.
- Reason: `TK-085-1` showed that Engineer AI could over-trust vague engineer conclusions, prematurely move to approval, and duplicate intake facts in the opening handoff without enough internal evidence.
- Affected files or config:
  - `backend/services/prompts/engineer_investigation_reply.py`
  - `backend/services/engineer_agent.py`
  - `backend/services/investigation_flow.py`
  - `backend/main.py`
  - `ui/engineer-ui/app.js`
  - `ui/engineer-ui/styles.css`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_engineer_ui_contract.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Engineer reply turns must now return structured readiness fields for conclusion, proof, proof anchors, and solution-or-next-step before the backend will allow `awaiting_confirmation`.
  - If proof is missing, proof anchors are unverifiable, or no actionable next step exists, Engineer AI falls back to `active`, clears the customer draft, and explicitly asks the engineer for the missing evidence.
  - The engineer UI now shows approval controls only when backend-validated readiness is true, while still surfacing the current draft, critique, blockers, and extracted conclusion/proof/action review details.
  - Investigation opening summaries now prefer structured intake facts and only append customer-note text when it contributes information not already captured by intake.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_prompt_modules.py backend/tests/test_investigation_flow.py backend/tests/test_engineer_ui_contract.py`
  - `node --check ui/engineer-ui/app.js`
  - `python3 -m py_compile backend/main.py backend/services/engineer_agent.py backend/services/investigation_flow.py backend/services/prompts/engineer_investigation_reply.py`

- Date: 2026-04-13
- Area or subsystem: Client grounded-answer guardrail and rendered references
- Prompt or model version: `client-grounded-answer-reference-guard-v1`
- Summary: Tightened the customer-answer guardrail so uncited technical replies from review/intake can no longer be sent as if they were grounded answers, and updated the client reply formatting contract to emit Markdown-friendly grounded answers with a bottom `References` section.
- Reason: `TK-087` showed that the runtime could surface a review/intake-generated technical reply with no citations, and the client bubble rendered numbered steps and code fences as plain text even when the answer content itself was acceptable.
- Affected files or config:
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/main.py`
  - `ui/client-ui/app.js`
  - `ui/client-ui/styles.css`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_client_ui_contract.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Customer-visible technical answers now require non-empty citations; otherwise the runtime falls back to explicit clarification or handoff wording instead of a docs-like answer.
  - The initial `/api/tickets/query` response now includes grounded `sources/citations` immediately when the answer is available, so the client does not flash an uncited first paint.
  - Client assistant bubbles render a safe Markdown subset for paragraphs, numbered lists, bullet lists, inline code, and fenced code blocks, and citations render as one-per-line links under a `References` heading.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_client_ticket_agent_runtime backend.tests.test_investigation_flow backend.tests.test_rag_qa backend.tests.test_rag_agentic backend.tests.test_client_ui_contract backend.tests.test_ticket_orchestrator`
  - `python3 -m py_compile backend/services/client_ticket_agent_runtime.py backend/services/rag_qa.py backend/main.py`
  - `node --check ui/client-ui/app.js`
  - `git diff --check`
  - lightweight stack rebuild in the official `deployment` local-lightweight profile plus `$supportportal-run-report` live replay against `real_case/real_user_questions.txt`, with `how to join channel` now returning a cited grounded answer and code block formatting in `/tmp/supportportal-traces/TK-TRACE-07855E45E3.json`

- Date: 2026-04-13
- Area or subsystem: Client citation-first answer chain for grounded FAQs and troubleshooting
- Prompt or model version: `client-cited-answer-precedence-v1`
- Summary: Changed the customer reply policy so grounded answers with non-empty citations are answered first and any remaining diagnostic question is appended as one deterministic follow-up sentence, instead of letting review/intake clarification override the cited answer.
- Reason: `how to enable the dual stream` and similar cases could already ground against authoritative docs in diagnostics, but the main runtime still prioritized clarification workflow branches and suppressed the cited customer answer.
- Affected files or config:
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/rag_service_client.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_service_client.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - If the online RAG resolution has a non-empty answer and non-empty `citations`, the customer now receives that grounded answer even when troubleshooting or feature-enable flows still want one small follow-up field.
  - Any extra customer prompt after a cited answer is appended as one short deterministic sentence, not generated free-form as a second technical answer.
  - Uncited or insufficient resolutions still fail closed to pure clarification or handoff wording and cannot masquerade as grounded documentation-backed answers.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_rag_service_client backend.tests.test_client_ticket_agent_runtime backend.tests.test_ticket_orchestrator backend.tests.test_rag_agentic backend.tests.test_rag_qa`
  - `python3 -m py_compile backend/services/rag_qa.py backend/services/rag_service_client.py backend/tests/test_rag_agentic.py backend/tests/test_rag_qa.py backend/tests/test_rag_service_client.py`
  - `$supportportal-run-report --message "how to enable the dual stream"` with `/tmp/supportportal-traces/TK-TRACE-E7E1A61DB2.json` showing `answer_route=rag`, `route_reason=grounded_answer`, `workflow_action=answer_customer`, and non-empty citations
  - `$supportportal-run-report` against `real_case/real_user_questions.txt`, with `/tmp/supportportal-traces/TK-TRACE-BC63976623.json` and `/tmp/supportportal-traces/TK-TRACE-5B23410A88.json` confirming cited grounded answers for both `how to join channel` and `how to enable the dual stream`

- Date: 2026-04-08
- Area or subsystem: Docs/API semantics answer generation
- Prompt or model version: `rag-answer-api-semantics-deterministic-v1`
- Summary: Added a deterministic docs-grounded answer path for `api_semantics_mismatch` so pinned child evidence can resolve `uid=0` and `time=0` semantics directly from the selected docs sections without using the generic multi-retry RAG answer prompt.
- Reason: `TK-080` still timed out or failed closed after retrieval because the generic answer generation path retried into `insufficient_evidence` even when the exact `Disband a channel` and `Create rule > Request parameters` chunks were already present.
- Affected files or config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_agentic.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - `api_semantics_mismatch` child queries that have the required docs sections selected can now resolve immediately with a deterministic two-sentence explanation and citations instead of entering the slower general answer/retry chain.
  - Full `TK-080`-style multi-question requests can aggregate the `uid=0` and `time=0` explanations without falling back to troubleshooting intake prompts.
  - When the deterministic path is used, telemetry now shows `generation_mode=api_semantics_deterministic`.
- Verification:
  - `source /tmp/supportportal-finalize-venv/bin/activate && python -m unittest backend.tests.test_rag_agentic.RagAgenticTests.test_build_api_semantics_grounded_answer_resolves_uid_zero_disband_conflict backend.tests.test_rag_agentic.RagAgenticTests.test_build_api_semantics_grounded_answer_resolves_time_zero_non_persistent_rule backend.tests.test_rag_agentic.RagAgenticTests.test_run_rag_query_agentic_single_uses_api_semantics_grounded_answer_without_llm backend.tests.test_rag_agentic.RagAgenticTests.test_apply_api_semantics_latency_budget_caps_bm25_candidate_window`
  - Direct full-message `run_rag_query(...)` replay for the `TK-080` customer text returned both docs-backed explanations with `needs_human=false` and no troubleshooting-intake follow-up.

- Date: 2026-04-08
- Area or subsystem: Client grounded-answer post-check gating
- Prompt or model version: `grounded-postcheck-api-semantics-bypass-v1`
- Summary: Grounded `api_semantics_mismatch` answers with citations now skip the generic grounded-answer post-check so deterministic docs/API explanations are not re-routed into troubleshooting intake when the customer message contains words like `issue`, `error`, or `failed`.
- Reason: The first live replay after the deterministic docs-answer path still produced a customer clarification because the generic high-risk gate forced post-check, and a post-check error then collapsed into `rag_post_check_error` despite the RAG answer already being correct.
- Affected files or config:
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Grounded docs/API semantics answers now stay on the direct answer path.
  - `TK-080`-style tickets should no longer ask for `channel_name` or `issue_timestamp` once the deterministic docs-backed answer is available.
  - Review telemetry for these grounded semantics answers will show `review_agent.status=skipped` with `reason=low_risk_grounded_answer`.
- Verification:
  - `source /tmp/supportportal-finalize-venv/bin/activate && python -m unittest backend.tests.test_client_ticket_agent_runtime`
  - `python3 -m py_compile backend/services/client_ticket_agent_runtime.py backend/tests/test_client_ticket_agent_runtime.py`

- Date: 2026-04-08
- Area or subsystem: Docs/API semantics review and clarification path
- Prompt or model version: `troubleshooting-intake-v3` / `rag-answer-api-semantics-v1`
- Summary: Added a deterministic docs/API semantics clarification path so tickets that compare official docs against observed API behavior stay in answer-mode review, use anchor-aware RAG evidence, and preserve real timeout or insufficient-evidence reasons instead of defaulting to troubleshooting intake fields.
- Reason: `TK-080` style tickets were being misread as troubleshooting investigations, which caused timeouts to degrade into `channel_name` / `issue_timestamp` follow-up questions instead of clarifying the documented API semantics.
- Affected files or config:
  - `backend/services/api_semantics.py`
  - `backend/services/support_router.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/rag_qa.py`
  - `backend/rag_api.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Docs/API mismatch questions with official docs URLs, endpoint paths, and parameter behavior differences now route directly to Agora technical RAG without waiting on the intent-router LLM.
  - When RAG cannot fully resolve a docs/API semantics question, the follow-up asks only for docs/API scope details such as platform, SDK family, docs page, or API version.
  - Multi-question docs/API tickets use numbered fanout child queries and preserve child-level timeout diagnostics in telemetry.
- Verification:
  - `source /tmp/supportportal-finalize-venv/bin/activate && python -m unittest backend.tests.test_support_router backend.tests.test_troubleshooting_intake backend.tests.test_client_ticket_agent_runtime backend.tests.test_rag_agentic backend.tests.test_rag_qa`
  - `python3 -m py_compile backend/services/api_semantics.py backend/services/support_router.py backend/services/troubleshooting_intake.py backend/services/client_ticket_agent_runtime.py backend/services/rag_qa.py backend/rag_api.py`
  - `git diff --check`

- Date: 2026-04-08
- Area or subsystem: Ticket title runtime observability and local single-host rebuild guardrails
- Prompt or model version: `ticket-title-runtime-guard-v1`
- Summary: Added a root-main-only local single-host restart script, exposed `app_build.ref` / `app_build.built_at` on API and RAG API `/health`, and introduced `scripts/fix_ticket_subject.py` so the canonical `ticket_title` helper can repair stale long subjects like `TK-079`.
- Reason: `TK-079` proved that the short-title change had merged in source control but the live local API was still serving the old `normalized[:100]` subject path because the shared `localhost/supportportal-app:latest` tag had been overwritten by a stale checkout build.
- Affected files or config:
  - `backend/services/app_build.py`
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/Dockerfile`
  - `deployment/docker-compose.single-host.yml`
  - `scripts/workflow/restart_single_host_stack.sh`
  - `scripts/fix_ticket_subject.py`
  - `docs/deploy_single_host_ec2.md`
  - `backend/tests/test_app_build.py`
  - `backend/tests/test_fix_ticket_subject_cli.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_rag_api.py`
  - `backend/tests/test_single_host_compose.py`
  - `backend/tests/test_workflow_scripts.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Local operators can compare `/health.app_build.ref` against the expected commit before trusting any runtime behavior.
  - The canonical local rebuild path is now `scripts/workflow/restart_single_host_stack.sh`, which refuses to run from a dirty or stale non-root checkout.
  - One-off ticket subject repairs can reuse the same `derive_ticket_title()` helper that new tickets use, instead of editing `support_tickets.subject` by hand.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_app_build backend.tests.test_fix_ticket_subject_cli backend.tests.test_single_host_compose backend.tests.test_workflow_scripts backend.tests.test_investigation_flow backend.tests.test_rag_api`
  - `curl http://127.0.0.1:8080/health`
  - `podman exec deployment_api_1 python -c "import backend.main; print(backend.main.derive_subject.__code__.co_names)"`
  - `./scripts/fix_ticket_subject.py --ticket-id TK-079 --apply`
  - `GET /api/tickets` showed `TK-079.subject = "Ban User Privileges API behavior mismatch"` and a new synthetic `TK-TITLE-SMOKE-081930` ticket was created with the same compact short-title style.

- Date: 2026-04-08
- Area or subsystem: Client ticket title generation
- Prompt or model version: `ticket-title-v1`
- Summary: Replaced the new-ticket subject fallback with a short issue-label generator so first-message titles stop using raw 100-character truncation and instead produce compact labels such as technical object plus mismatch/symptom.
- Reason: Real ticket `TK-078` showed that the old `subject` behavior was not a summary at all; it copied the first customer paragraph prefix into every new ticket, which made client and dashboard rows noisy and hard to scan.
- Affected files or config:
  - `backend/services/ticket_title.py`
  - `backend/services/llm_profiles.py`
  - `backend/main.py`
  - `backend/tests/test_ticket_title.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_llm_profiles.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - New tickets created through `/api/tickets/query` without an explicit `subject` now try a `gpt-5.4-nano` short-title prompt with `reasoning_effort=none`, `temperature=0`, and a `2.0s` timeout.
  - If the model output is empty, too long, includes greetings or URLs, or looks like a raw prefix copy, the backend falls back to deterministic title compaction instead of persisting the first 100 characters of the customer message.
  - Existing tickets and explicit caller-provided `subject` values remain unchanged.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_ticket_title`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_llm_profiles`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_investigation_flow`

- Date: 2026-04-08
- Area or subsystem: Dashboard ticket summary context
- Prompt or model version: `dashboard-ticket-summary-v2`
- Summary: Expanded the shared dashboard ticket summary context so client-rooted dashboard summaries include linked sub ticket counts, active-state context, and the latest linked sub ticket updates when a main ticket has one or more engineer-side cases.
- Reason: The ticket dashboard now renders one row per client ticket, so summary generation must stay rooted in the main ticket while still reflecting investigation progress from any linked sub tickets.
- Affected files or config:
  - `backend/main.py`
  - `backend/tests/test_dashboard_ticket_routes.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `ui/dashboard-ui/app.js`
  - `ui/dashboard-ui/styles.css`
  - `ui/dashboard-ui/index.html`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Dashboard summary generation now incorporates linked sub ticket progress instead of only the root ticket's direct investigation fields.
  - If the canonical summary endpoint is unavailable or returns empty content, the dashboard fallback summary still mentions linked sub ticket counts and the latest linked engineer update.
  - Investigating, escalated, communicating, and resolved dashboard detail views remain client-ticket-rooted while surfacing sub ticket progress in the summary narrative.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_dashboard_ticket_routes`
  - `python3 -m unittest backend.tests.test_dashboard_ui_contract`
  - `node --check ui/dashboard-ui/app.js`
  - `python3 -m py_compile backend/main.py`

- Date: 2026-04-07
- Area or subsystem: RAG answer grounding retry for RTC how-to FAQs
- Prompt or model version: `rag-answer-v3`
- Summary: Extended the RAG answer user prompt with a dedicated citation-grounding retry mode that tells the model to cite both materially used supporting chunks, up to two citations, when a how-to answer relies on both an implementation step chunk and a token/authentication chunk.
- Reason: `TK-075` showed that the model could produce a grounded RTC join answer from two valid chunks but still cite only one, which weakened answer completeness and lost the second reference even when the evidence was already present.
- Affected files or config:
  - `backend/services/prompts/rag_answer.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - When a generic RTC `join channel` answer already has two materially supporting chunks in final context, a first-pass single-citation answer now triggers one stricter grounding retry.
  - The retry asks for both the implementation-step chunk and the token/authentication chunk when both are actually used, without forcing unrelated citations.
  - Queries that truly only have one supporting chunk can still return a single citation.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_rag_qa.py backend/tests/test_rag_agentic.py`
  - Added regression asserting that a first-pass single-citation `how to join channel` answer retries and returns two RTC citations when both supporting chunks are present.

- Date: 2026-04-22
- Area or subsystem: Troubleshooting intake prompt contract
- Prompt or model version: `troubleshooting-intake-v3`
- Summary: Tightened the troubleshooting intake system prompt for short follow-up example requests so when recent context already anchors the technical topic, the model must not ask the customer to restate the topic and may only ask for missing example scope such as platform or SDK.
- Reason: `TK-171` showed that a second-turn request like `Can you share a code example?` could still trigger broad clarify prompts about the customer's goal or blocker even though the earlier `join channel` topic was already clear.
- Affected files or config:
  - `backend/services/prompts/troubleshooting_intake.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Short example/sample/snippet follow-ups with clear recent context should preserve the inherited topic instead of asking the customer to restate it.
  - If clarification is still necessary for those follow-ups, the prompt should narrow to missing example scope such as platform or SDK.
  - Generic clarify replies like `what you're trying to achieve`, `what error or blocker you're seeing`, or `join / publish / both` should no longer be emitted for anchored follow-up example requests.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_rag_qa.py backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_troubleshooting_intake.py`

- Date: 2026-04-05
- Area or subsystem: Engineer investigation reply drafting
- Prompt or model version: `engineer-investigation-reply-v1`
- Summary: Added a dedicated post-engineer investigation reply prompt and model scene so engineer follow-up turns now use structured Responses output from `gpt-5.4` with `reasoning_effort=medium`, instead of copying raw engineer notes into the customer draft.
- Reason: Engineer AI follow-up drafts were too literal and reused the broader engineer-helper profile, which led to stiff customer wording and the wrong reasoning profile for investigation reply turns.
- Affected files or config:
  - `backend/services/prompts/engineer_investigation_reply.py`
  - `backend/services/prompts/__init__.py`
  - `backend/services/llm_profiles.py`
  - `backend/services/engineer_agent.py`
  - `backend/services/investigation_flow.py`
  - `backend/main.py`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_single_host_compose.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - After an engineer message or revise note, Engineer AI now calls a dedicated investigation-reply scene with full ticket, handoff, investigation-thread, and agent-state context.
  - The model can either keep the investigation `active` with another internal engineer-facing request or move to `awaiting_confirmation` with a customer-safe draft reply.
  - Invalid, empty, or malformed model output now fails closed with no synthetic customer draft, and the investigation approval path requires an existing generated draft before sending.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_profiles.py backend/tests/test_prompt_modules.py backend/tests/test_investigation_flow.py backend/tests/test_engineer_ui_contract.py backend/tests/test_single_host_compose.py backend/tests/test_worker.py -q`
  - `python3 -m py_compile backend/main.py backend/services/engineer_agent.py backend/services/investigation_flow.py backend/services/llm_profiles.py backend/services/prompts/__init__.py backend/services/prompts/engineer_investigation_reply.py backend/tests/test_llm_profiles.py backend/tests/test_prompt_modules.py backend/tests/test_investigation_flow.py backend/tests/test_engineer_ui_contract.py backend/tests/test_single_host_compose.py`
  - `node --check ui/engineer-ui/app.js`
  - `podman exec -i engineerreplymanual_api python - <<'PY' ... /api/engineer/tickets/{ticket_id}/investigation/messages smoke for both channel-name follow-up and direct-fix reply paths ... PY`

- Date: 2026-04-05
- Area or subsystem: Troubleshooting intake prompt contract
- Prompt or model version: `troubleshooting-intake-v2`
- Summary: Tightened the troubleshooting intake system prompt so investigation-mode responses may only mark `ready_for_engineer_ticket=true` when every required field is present, must enumerate all missing investigation fields, and must return a non-empty customer reply whenever intake is still incomplete.
- Reason: The backend now treats troubleshooting intake as the shared gate for both `rag_insufficient_evidence` and `grounded_postcheck` escalations, so the prompt contract must stop the LLM from prematurely approving engineer handoff when product-specific identifiers are still missing.
- Affected files or config:
  - `backend/services/prompts/troubleshooting_intake.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Investigation-mode intake replies should always ask for the missing product fields before engineer escalation.
  - An LLM response can no longer force `ready_for_engineer_ticket=true` while `channel name`, `problematic uid`, `issue timestamp`, `sid`, or other required fields are still absent.
  - Troubleshooting follow-up turns keep using deterministic field completeness as the source of truth even when the prompt/model returns malformed readiness metadata.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_troubleshooting_intake.py`
  - `git diff --check`

- Date: 2026-05-21
- Area or subsystem: RAG answer model timeout configuration
- Prompt or model version: `rag-answer-timeout-default-v2`
- Summary: Increased the default `RAG_REQUEST_TIMEOUT_SECONDS` for the single-host RAG API from `20.0` to `40.0` and documented the same default in `.env.example`.
- Reason: `TK-216` exhausted the RAG request deadline while handling a generic join-channel question, causing engineer escalation instead of the `TK-208` style docs-grounded answer.
- Affected files or config:
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_single_host_compose.py`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Deployments that do not explicitly override `RAG_REQUEST_TIMEOUT_SECONDS` give RAG up to 40 seconds before deadline-exhausted handling.
  - Prompt text, model names, reasoning effort, and answer schema are unchanged.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_single_host_compose.py -q`

- Date: 2026-04-05
- Area or subsystem: Client new-session welcome copy and product selector layout
- Prompt or model version: `client-empty-session-welcome-v3`
- Summary: Reworded the transient welcome bubble to explicitly request product selection and removed the separate product explainer card so the selector now sits directly beneath the greeting.
- Reason: The product explainer card repeated information already conveyed by the welcome copy and added unnecessary vertical weight before the first action.
- Affected files or config:
  - `ui/client-ui/app.js`
  - `ui/client-ui/styles.css`
  - `ui/client-ui/index.html`
  - `backend/tests/test_client_ui_contract.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Empty draft sessions now greet users with `Thank you for contacting Agora Support! We’re here to help. Before we begin, please select the product you need support with.`
  - The product selector renders immediately below the welcome bubble without the extra kicker/title/description card.
  - The welcome bubble remains transient-only and still disappears once the first real user message exists.
- Verification:
  - `python -m unittest backend.tests.test_client_ui_contract`
  - `node --check ui/client-ui/app.js`
  - `git diff --check`

- Date: 2026-04-22
- Area or subsystem: Intent routing and ticket-title normalization
- Prompt or model version: `intent-router-fastpath-v1` / `ticket-title-canonical-fastpath-v1`
- Summary: Added a deterministic pre-LLM route for short symptom-led troubleshooting prompts and a canonical ticket-title shortcut for high-confidence issue labels, so black-screen questions no longer depend on route-model/title-model drift to reach the docs-grounded answer path.
- Reason: `TK-176` showed that the route model could classify `I got black screen, what should I do?` as `non_agora / general_it_support`, and the title helper could accept `Black Screen After Startup`, together causing a `route_flip` cancellation and refusal fallback instead of the `TK-124` style RAG answer.
- Affected files or config:
  - `backend/services/support_router.py`
  - `backend/services/ticket_title.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_ticket_title.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Short troubleshooting questions with approved symptom markers such as `black screen`, `blank screen`, `no audio`, `no video`, `join failed`, `disconnect`, or `network quality` now route straight to `agora_technical / rag` when the latest message is a question or follow-up and carries no explicit public-info, product-portfolio, or general-IT signal.
  - Explicit general IT requests such as computer blue screens, printers, Outlook, Excel, and office Wi-Fi remain deterministic `non_agora / refuse` cases instead of competing with the troubleshooting fast path.
  - High-confidence canonical symptom tickets now resolve to fixed English labels like `Black screen issue` before the title model runs, so model outputs such as `Black Screen After Startup` no longer become the saved subject for those cases.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_support_router.py backend/tests/test_ticket_title.py backend/tests/test_client_ticket_agent_runtime.py`

- Date: 2026-04-23
- Area or subsystem: Engineer investigation reply recovery and context hygiene
- Prompt or model version: `engineer-investigation-reply-v8`
- Summary: Added an explicit instruction to ignore earlier unverified root-cause wording when regenerating customer drafts, while the backend now auto-recovers symptom-level customer replies whenever proof and workaround are sufficient but the model still overstates the root cause in its draft.
- Reason: `TK-179-1` showed that verified Web SDK evidence (`no input frame received` plus a conservative `try a different device` workaround) was still rejected because a prior `the camera is broken` hypothesis polluted the investigation context and the mixed LLM output kept the case in `active`.
- Affected files or config:
  - `backend/services/prompts/engineer_investigation_reply.py`
  - `backend/services/engineer_agent.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_prompt_modules.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - When the investigation evidence only supports `symptom_and_workaround_only`, stale root-cause guesses from earlier engineer turns are no longer repeated in the summary context sent back to the reply model.
  - If the model returns a mixed output where `reply_readiness` is symptom-safe but `draft_customer_reply` still overstates the root cause, the backend now rewrites the draft to symptom-level wording and keeps the case in `awaiting_confirmation` instead of forcing another engineer loop.
  - Unsupported root-cause claims still remain blocked when proof is missing, proof anchors are unverifiable, the next step is absent, or the reply truly depends on `root_cause_confirmed`.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_investigation_flow.InvestigationFlowTests.test_engineer_internal_message_allows_symptom_level_workaround_without_explicit_conclusion backend.tests.test_investigation_flow.InvestigationFlowTests.test_engineer_internal_message_rejects_missing_conclusion_when_reply_scope_claims_root_cause backend.tests.test_investigation_flow.InvestigationFlowTests.test_engineer_internal_message_recovers_symptom_scope_when_draft_overstates_root_cause backend.tests.test_investigation_flow.InvestigationFlowTests.test_engineer_internal_message_sanitizes_prior_unverified_root_cause_from_prompt_context backend.tests.test_investigation_flow.InvestigationFlowTests.test_engineer_internal_message_rejects_unverifiable_proof_anchors`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_prompt_modules.PromptModuleTests.test_engineer_investigation_reply_prompt_is_sectioned_and_json_only`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/engineer_agent.py backend/services/prompts/engineer_investigation_reply.py backend/tests/test_investigation_flow.py backend/tests/test_prompt_modules.py`
  - `git diff --check`

- Date: 2026-05-19
- Area or subsystem: Troubleshooting intake and answer-mode clarification contract
- Prompt or model version: `troubleshooting-intake-v4-answer-contract`
- Summary: Tightened answer-mode clarification so the model can no longer introduce `example_request` / platform-SDK scope for ordinary answer questions, while deterministic intake now treats a clear how-to goal as sufficient customer context instead of asking for an unrelated blocker.
- Reason: `TK-207` showed a broader failure mode where `rag_completed_with_insufficient_evidence` could become a customer-facing platform/SDK question even though the customer had already stated the Cloud Recording goal and the missing piece was evidence quality rather than customer context.
- Affected files or config:
  - `backend/services/troubleshooting_intake.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Long how-to / direct-answer questions with an extractable goal no longer ask the customer for a generic error or blocker when RAG evidence is insufficient.
  - The intake model cannot reclassify a normal answer request as an example request unless deterministic follow-up inheritance already identified it as one.
  - Customer clarification text that asks for platform/SDK is rejected unless `platform_or_sdk` is actually a missing answer-mode field.
  - When no safe customer clarification remains and the answer lacks sufficient grounding, the runtime opens engineer intake instead of sending unsupported or mismatched guidance.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_troubleshooting_intake backend.tests.test_client_ticket_agent_runtime`

- Date: 2026-05-25
- Area or subsystem: RAG answer prompt and usage-configuration generation
- Prompt or model version: `rag-answer-usage-configuration-code-v1`
- Summary: Added a usage/configuration code-example policy to the RAG answer prompt and wired answer generation to provide evidence-supported languages plus config-example evidence availability.
- Reason: PR4 requires usage/configuration answers to include minimal code or configuration examples when the retrieved chunks support exact API names, fields, parameters, call order, or config shape, without allowing the model to invent unsupported examples.
- Affected files or config:
  - `backend/services/prompts/rag_answer.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - `usage_configuration` prompts now prefer a customer-requested language only when retrieved evidence supports that language.
  - When no language is requested, generation gets a deterministic evidence-supported language choice derived from ticket ID, customer ID, and effective question.
  - If chunks do not provide code, JSON, API parameter, request-body schema, or explicit field-list evidence, the prompt tells the model not to include a fenced code block and to use insufficient evidence when needed.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_prompt_modules.py backend/tests/test_rag_qa.py backend/tests/test_rag_prompt_guards.py -q -k 'usage_configuration_code_language or usage_configuration_answer_prompt_receives_selected_evidence_language or config_examples_when_field_evidence_has_no_language_tag or supports_config_example_without_language_tag or receives_config_evidence_without_language or weak_config_words or language_metadata_alone or rag_answer_prompt_guides or generic_join'`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_qa.py backend/services/prompts/rag_answer.py`

- Date: 2026-06-11
- Area or subsystem: Vendored cusmem GraphRAG source
- Prompt or model version: `vendor-cusmem-import-v1`
- Summary: Added a sanitized vendored cusmem source tree containing Graphiti/GraphRAG prompt modules, model configuration examples, schemas, and tests for future integration research.
- Reason: SupportPortal needs the external project available locally before any adapter work; this import keeps the code reviewable while avoiding runtime wiring and excluding local secrets or private-address scripts.
- Affected files or config:
  - `.gitignore`
  - `vendor/cusmem/`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - No production prompt or model behavior changes; the vendored prompt/model files are not imported by the SupportPortal runtime in this task.
- Verification:
  - `git diff --check origin/main..HEAD`
  - Excluded-file check confirmed omitted local scripts, logs, copied PDF, comparison JSON files, and spreadsheet outputs are absent from `vendor/cusmem/`.
  - Changed-scope secret scan confirmed no disallowed real-secret patterns in `.gitignore`, changelogs, or `vendor/cusmem/`.
  - Private-address scan confirmed `103.151.172.84` and `neo4j@openspg` are absent from `vendor/cusmem/`.
  - `python3 -m compileall -q vendor/cusmem`

- Date: 2026-06-12
- Area or subsystem: Engineer escalation opening context
- Prompt or model version: `engineer-summary-packet-v1`
- Summary: Added deterministic Summary Agent packet input to the engineer investigation opening request while preserving legacy Engineer Agent handoff fields.
- Reason: Client escalations need a stable summary packet that carries the customer context, current clues, missing information, and redaction boundary into the Engineer ticket before Plan Agent is introduced.
- Affected files or config:
  - `backend/services/engineer_summary_agent.py`
  - `backend/services/engineer_agent.py`
  - `backend/services/investigation_flow.py`
  - `backend/main.py`
  - `backend/tests/test_engineer_summary_agent.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/qbr_plan.html`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
- Expected behavior change:
  - Escalated Engineer tickets now receive an `engineer_handoff_packet` with Summary Agent metadata, structured engineer-ticket input, missing information, and a customer redaction boundary.
  - The first Engineer Request prefers the Summary Agent opening summary and requested action, then appends missing information that the engineer should confirm.
  - Existing Engineer Agent fallback and revise paths continue to read legacy handoff fields from the same packet.
- Verification:
  - `rtk python3 -m unittest backend.tests.test_engineer_summary_agent -v`
  - `rtk python3 -m unittest backend.tests.test_qbr_plan_contract -v`
  - `rtk python3 -m unittest backend.tests.test_engineer_multi_agent -v`
  - `rtk python3 -m unittest backend.tests.test_engineer_hitl_review -v`
  - `rtk python3 scripts/verify_feature_list.py`
  - `rtk python3 -m py_compile backend/services/engineer_summary_agent.py backend/services/engineer_agent.py backend/services/investigation_flow.py backend/main.py backend/tests/test_engineer_summary_agent.py`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_investigation_flow -v`

## 2026-06-17 - Semantic router account verification intake

- Area or subsystem: Support Router / Billing Automation
- Prompt or model version: `semantic-router-account-verification-v1`
- Summary: Added `billing.account_verification` intent to the LLM router taxonomy for suspicious activity, fraud/risk review, company verification, and account reactivation verification scenarios. Downgraded weak deterministic gratitude signal to prevent long billing/account messages from being misrouted as `small_talk`. Added semantic-first mode to `decide_support_route()` for `/account` endpoint. Updated billing automation to support `account_verification` action with field extraction and real email sending.
- Reason: `TK-ACC-C31612` was misrouted as `small_talk` because a long account verification message ending with "thank you" triggered the deterministic gratitude check before LLM classification could run. The `/account` endpoint now uses semantic-first routing to prevent weak deterministic signals from preempting LLM intent classification.
- Affected files or config:
  - `backend/services/support_router.py` — added `semantic_first` parameter, downgraded gratitude check, added `billing.account_verification` policy gate rules
  - `backend/services/billing_automation.py` — added `BILLING_ACTION_ACCOUNT_VERIFICATION`, expanded field aliases, use_case section parsing, optional app_id extraction, account_verification email flow
  - `backend/main.py` — `create_account_intake()` now uses `semantic_first=True`, supports `account_verification` route, sends real email instead of demo_mode
  - `backend/services/prompts/router.py` — added `billing.account_verification` to intent taxonomy and automation eligibility rules
  - `backend/services/support_router_prompt.py` — added few-shot example for account verification
  - `backend/tests/test_support_router_semantic_billing.py` — added account_verification and gratitude downgrade tests
  - `backend/tests/test_account_intake.py` — updated demo_mode expectations to real email
  - `backend/tests/test_qbr_plan_contract.py` — added QBR contract assertions for new terms
  - `docs/prompt_change_log.md`
  - `docs/qbr_plan.html`
- Expected behavior change:
  - `/account` endpoint now runs LLM semantic routing before deterministic fast path, preventing weak signals like trailing "thank you" from misrouting billing messages
  - Long messages (>20 words) with billing/account/fraud keywords no longer match the gratitude fast path even when ending with "thank you"
  - Suspicious activity, fraud review, company verification, and reactivation verification requests are classified as `billing.account_verification` and routed to `billing_automation/account_verification`
  - `billing.account_verification` is automation-eligible when no refund/dispute/legal risk flags are present
  - Real email sending replaces demo_mode for billing automation; SMTP missing config results in `skipped_config_missing`
  - Short pure gratitude messages still route to `small_talk/controlled_response`
- Verification:
  - `rtk pytest backend/tests/test_support_router_semantic_billing.py -v` (17 passed)
  - `rtk pytest backend/tests/test_support_router.py -v` (50 passed)
  - `rtk python -m py_compile backend/services/support_router.py backend/services/billing_automation.py backend/main.py`

## 2026-07-21 - Account router audit and Persona Prompt registry

- Area or subsystem: `/account` routing and customer reply Persona
- Prompt or model version: `account-router-v1` / `default-support-v1`
- Summary: Versioned the semantic router Prompt, persisted the exact route Prompt snapshot, and added Admin-managed Persona drafts, publishing, immutable history, rollback, and stable per-ticket assignment.
- Follow-up: Added optional Persona reply opener policy and a dedicated customer-reply execution audit containing the exact Persona/version and effective structured Prompt used for each `/account` reply.
- Reason: Admins need to audit how an account case was routed and safely manage the voice used for account customer replies without changing historical executions.
- Affected files or config:
  - `backend/services/account_admin.py`
  - `backend/services/support_router.py`
  - `backend/main.py`
  - `backend/repositories/ticket_repository.py`
  - `ui/workspace-ui/admin/`
- Expected behavior change:
  - New `/account` routes record the router version and exact system/user Prompt used; deterministic and legacy cases are represented without fabricated Prompt snapshots.
  - Account customer replies receive a persisted Persona assignment; published Persona changes apply only to tickets that have not yet been assigned.
  - Deterministic replies apply the published opener/signoff policy, and every emitted account reply records its effective Persona Prompt for audit.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_account_admin_features backend.tests.test_account_intake backend.tests.test_workspace_api backend.tests.test_workspace_admin_ui_contract`

## 2026-07-24 - Enablement automation routing

- Area or subsystem: Support Router / Automation Enablement
- Prompt or model version: `account-router-v2`
- Summary: Added the `enablement` scope and `enablement.feature_activation` intent for explicit requests that Agora activate a named backend feature. Added positive Media Relay and negative SDK configuration examples, while a deterministic policy gate requires concrete feature evidence and an explicit backend activation request.
- Reason: Repeated Media Relay activation tickets should enter the Automated Case flow, while how-to, configuration, authentication, and troubleshooting questions must remain on the Agora technical RAG route.
- Affected files or config:
  - `backend/services/prompts/router.py`
  - `backend/services/support_router_prompt.py`
  - `backend/services/support_router.py`
  - `backend/services/account_admin.py`
- Expected behavior change:
  - Explicit requests such as “please enable Media Relay from your end” route to `Automation / enablement`.
  - “How do I enable or configure Media Relay?” remains `agora_technical / rag`.
  - Vague activation requests without a concrete feature fail closed to the non-automated technical route even if the model recommends Enablement.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_support_router_enablement backend.tests.test_support_router_semantic_billing -v`
  - `rtk python3 scripts/verify_feature_list.py`

## 2026-07-27 - Account layered route pipeline

- Area or subsystem: `/account` Route Agent
- Prompt or model version: `account-intent-v1` / `account-agora-v1` / `account-automation-v1`
- Summary: Replaced the single `/account` semantic-first Prompt with three scoped classifiers: Intent Classifier, Agora Router, and Automation Router. The shared Client Router remains unchanged.
- Reason: The Automation taxonomy will continue to grow; separating conversation/scope, Agora route, and registered Automation subcategory decisions keeps each Prompt narrow while preserving examples and fail-closed behavior.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/account_route_pipeline.py`
  - `backend/main.py`
  - `backend/services/agent_config.py`
  - `ACCOUNT_ROUTER_MODE`
  - `ACCOUNT_ROUTER_CONFIDENCE_THRESHOLD`
- Expected behavior change:
  - `/account` records primary and secondary labels from the layered result; mixed, unclear, low-confidence, invalid, and unregistered outputs route to Human Review.
  - Only registered Automation routes execute Billing or Enablement handlers. Other Account routes are classification-only in this phase.
  - Ordinary Account replies rerun the full pipeline. A reply supplying new required fields continues an active Automation handler before reclassification.
  - `/client`, background workers, and shared `decide_support_route()` callers retain their existing route behavior.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_account_route_pipeline.py backend/tests/test_account_intake.py backend/tests/test_agent_config.py backend/tests/test_account_ui_contract.py backend/tests/test_workspace_admin_ui_contract.py backend/tests/test_repository_configuration.py -q`
  - `rtk python3 scripts/verify_feature_list.py`

## 2026-07-29 - Enablement AI field extraction

- Area or subsystem: `/account` Enablement Automation
- Prompt or model version: `account-enablement-fields-v1`
- Summary: Added a managed Enablement Field Extractor Prompt that uses the complete customer-authored Account Case history to extract App ID and requested feature with exact source grounding. App IDs no longer have length, character-set, or prefix requirements, and missing-field follow-ups are generated from context.
- Reason: Enablement Case 12488 included an App ID in natural wording that the previous regex parser did not recognize, causing an unnecessary repeat request. Identifier descriptions vary and should be interpreted by the model rather than encoded as expanding parser patterns.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/enablement_field_extractor.py`
  - `backend/services/enablement_automation.py`
  - `backend/services/account_route_pipeline.py`
  - `backend/services/agent_config.py`
  - `backend/main.py`
  - Admin Agent Config Prompt registry
- Expected behavior change:
  - The Enablement handler consumes only structured, source-grounded fields and does not reparse customer text.
  - `missing` generates one contextual App ID follow-up without prescribing a format; repeated requests are suppressed.
  - `uncertain`, ambiguous, low-confidence, or ungrounded extraction fails closed to Human Review, sends no internal email, and cancels any pending automated follow-up.
  - The new pipeline is invoked only by `/account`; `/client`, background workers, and shared `decide_support_route()` behavior remain unchanged.
- Verification:
  - `rtk env PYTHONPATH=/tmp/supportportal-test-deps-enablement-extractor-20260729 pytest -q backend/tests/test_account_intake.py backend/tests/test_enablement_field_extractor.py backend/tests/test_enablement_automation.py backend/tests/test_enablement_repair.py backend/tests/test_agent_config.py backend/tests/test_prompt_versioning.py` (131 passed, 14 subtests passed)
  - `rtk env PYTHONPATH=/tmp/supportportal-test-deps-enablement-extractor-20260729 pytest -q backend/tests/test_account_route_pipeline.py backend/tests/test_account_ui_contract.py backend/tests/test_workspace_admin_ui_contract.py backend/tests/test_account_admin_features.py backend/tests/test_single_host_compose.py` (task-related tests passed)

## 2026-07-29 - Enablement customer reply composer

- Area or subsystem: `/account` Enablement Automation Outlook reply handling
- Prompt or model version: `enablement-customer-reply-v1` / `enablement_reply`
- Summary: Added an LLM composer that identifies the newest internal Enablement resolution in a full Outlook reply thread and rewrites it as a concise customer-facing update.
- Reason: The previous template inserted the complete internal email body verbatim, exposing signatures, quoted messages, internal identifiers, and contact details to the customer.
- Affected files or config:
  - `backend/services/enablement_automation.py`
  - `backend/services/llm_profiles.py`
  - `ENABLEMENT_REPLY_MODEL`
  - `ENABLEMENT_REPLY_REASONING_EFFORT`
  - `ENABLEMENT_REPLY_TEMPERATURE`
  - `ENABLEMENT_REPLY_TIMEOUT_SECONDS`
  - `ENABLEMENT_REPLY_MAX_RETRIES`
- Expected behavior change:
  - The model preserves only facts from the newest human-authored internal resolution and does not copy the Outlook quote chain.
  - Customer replies exclude signatures, staff names, email headers, internal instructions, Case/Ticket/App IDs, email addresses, physical addresses, and community links.
  - Missing model credentials, invocation failures, empty/oversized output, and detected leakage fail closed so the poller retries instead of sending the internal thread to the customer.
- Verification:
  - `rtk python3 -m unittest backend.tests.test_enablement_automation backend.tests.test_llm_profiles`
  - `rtk uv run --with pytest --with 'psycopg[binary]' python -m pytest backend/tests/test_worker.py -q -k 'enablement_request_reply'`

## 2026-07-29 - Enablement field verification and delivery truthfulness

- Area or subsystem: `/account` Enablement field intake and internal request delivery
- Prompt or model version: `account-enablement-fields-v2` / existing `intent_router` profile
- Summary: Strengthened the Enablement field extractor so App IDs have no format constraint, concrete feature names must replace pronouns, and missing App ID or generic feature results receive an independent second LLM extraction pass over the complete customer history.
- Reason: A one-pass extraction incorrectly asked Case 12494 for an App ID already present and stored `it` as Case 12495's requested feature. Case 12495 also claimed submission even though its internal email destination had been captured as empty.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/enablement_field_extractor.py`
  - `backend/services/enablement_automation.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/services/enablement_repair.py`
  - `ENABLEMENT_AUTOMATION_INTERNAL_EMAIL`
  - `ENABLEMENT_DELIVERY_RETRY_POLL_INTERVAL_SECONDS`
- Expected behavior change:
  - The system asks for an App ID only after two evidence-grounded LLM passes confirm it is absent.
  - Pronouns such as `it` cannot become the requested feature; unresolved or conflicting results fail closed to Human Review.
  - The Enablement destination is resolved at send time, failed/config-missing deliveries are retried by the auxiliary worker, and customer submission confirmation is queued only after the internal email is sent.
  - Delivery and confirmation use a stable per-Case delivery key to avoid duplicate customer confirmation jobs.
- Verification:
  - `rtk uv run --with pytest --with 'psycopg[binary]' python -m pytest backend/tests/test_enablement_field_extractor.py backend/tests/test_enablement_automation.py backend/tests/test_enablement_repair.py backend/tests/test_account_intake.py backend/tests/test_worker.py backend/tests/test_agent_config.py backend/tests/test_single_host_compose.py -q`
## 2026-07-30 - Enablement misspelled feature grounding verification

- Area or subsystem: `/account` Enablement field intake
- Prompt or model version: `account-enablement-fields-v3` / existing `intent_router` profile
- Summary: Added an independent verification pass when the extractor normalizes or corrects a feature label that is not an exact substring of its cited customer quote. The verifier must preserve customer misspellings in `original_label` while keeping the corrected capability only in the canonical field.
- Reason: Case 12513 clearly requested Channel Media Relay enablement, but the customer's `rele` misspelling was silently corrected in the extracted label and then rejected by exact grounding validation, incorrectly sending the Case to Human Review.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/enablement_field_extractor.py`
- Expected behavior change:
  - Explicit Enablement requests with misspelled feature names can remain `Automation / Enablement` when a second LLM pass returns an exact customer-authored label and quote.
  - Canonical feature values may correct spelling, while audit labels retain the customer's original wording.
  - A second ungrounded result still fails closed to Human Review.
  - Troubleshooting requests such as Case 12458 remain `Agora Technical`; feature-name typo tolerance does not change Agora Router priority.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_enablement_field_extractor.py backend/tests/test_account_route_pipeline.py -q`
## 2026-07-30 - Account Quota Automation routing and field extraction

- Area or subsystem: `/account` Agora Router, Automation Router, and Quota handler
- Prompt or model version: `account-agora-v3`, `account-automation-v4`, `account-quota-fields-v1`, `quota-customer-reply-v1`
- Summary: Registered `Automation / Quota` for account-level quota review, concurrency increases, and Big Event capacity notifications. Added grounded LLM extraction for products, App IDs, requested capacity, and event details, plus a customer-facing internal-resolution composer.
- Reason: Case 12512 requested RTC, RTM, and Chat concurrency review and increases before a major campaign, but the prior taxonomy stopped at `Agora / Uncategorized` or `Automation / Unregistered` because quota was not registered.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/account_route_pipeline.py`
  - `backend/services/quota_field_extractor.py`
  - `backend/services/quota_automation.py`
  - `QUOTA_AUTOMATION_INTERNAL_EMAIL`
- Expected behavior change:
  - Concrete quota/capacity operations and Big Event notifications route to `Automation / Quota`; concurrency calculation, troubleshooting, and pricing remain Technical or Account & Billing.
  - Quota intake asks once for missing operational details, then sends the available grounded information without changing `route_status=automated`.
  - Internal Quota replies are rewritten for customers and never copied verbatim.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_quota_field_extractor.py backend/tests/test_quota_automation.py backend/tests/test_account_route_pipeline.py backend/tests/test_account_intake.py backend/tests/test_worker.py -q`

## 2026-07-31 - Account latest-route enforcement and historical reroute

- Area or subsystem: `/account` Intent Classifier, Agora Router, and Automation Router invocation mode
- Prompt or model version: `account-layered-router-v2` with the existing `account-intent-v2`, `account-agora-v3`, and `account-automation-v4` prompts
- Summary: Account creation, ordinary replies, and active-handler probes now explicitly require the current layered pipeline. Added a classification-only batch reroute that rewrites historical route fields and audit executions without replaying Automation handlers.
- Reason: Historical `account-layered-router-v1` records used `intent_class=support_request`; the current label mapper interpreted that removed class as Human Review even when canonical route fields still identified a registered Automation.
- Affected files or config:
  - `backend/services/account_route_pipeline.py`
  - `backend/services/account_case_reroute.py`
  - `backend/scripts/reroute_account_cases.py`
  - `backend/main.py`
- Expected behavior change:
  - New `/account` cases cannot be switched to legacy or shadow routing by `ACCOUNT_ROUTER_MODE`; credential-unavailable fallback remains available but is normalized into the current v2 schema.
  - Historical cases can be rerouted with current prompts while preserving customer replies, collected fields, internal email records, and existing handler lifecycle state.
  - Newly discovered Automation routes are marked `classification_only`, so the batch cannot send mail or customer replies; normal future customer messages run the standard Account lifecycle.
  - Registered canonical Automation fields take precedence over incompatible legacy label payloads.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_account_case_reroute.py backend/tests/test_account_route_pipeline.py backend/tests/test_account_intake.py backend/tests/test_account_ui_contract.py backend/tests/test_account_admin_features.py backend/tests/test_prompt_versioning.py backend/tests/test_repository_configuration.py -q` (246 passed, 3 subtests passed)
  - Full dry-run against 62 Account Cases completed with 0 failures; 9 v1 and 53 v2 records were evaluated for migration to `account-layered-router-v2`.

## 2026-07-31 - Fraud Account and classification-only Account Suspension split

- Area or subsystem: `/account` Agora Router, Automation Router, Fraud Account intake, and Account Suspension extraction
- Prompt or model version: `account-layered-router-v3`, `account-agora-v4`, `account-automation-v5`, `fraud-account-fields-v2`, `fraud-account-follow-up-v2`, `account-suspension-fields-v1`
- Summary: Replaced the risk-oriented Account Verification route with `fraud_account` and redefined `account_suspension` as a non-fraud, classification-only subcategory. Added grounded extraction for the reported suspension state, known reason, and customer actions, plus explicit Billing exclusions for Enablement.
- Reason: Cases 12523, 12529, and 12532 were non-fraud suspensions but inherited the fraud-review field checklist; Case 12505 was financial invoice-billing configuration but was attracted to Enablement wording; historical Case 12475 still treated Website as required.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/account_route_pipeline.py`
  - `backend/services/account_automation_handlers.py`
  - `backend/services/account_suspension_field_extractor.py`
  - `backend/services/account_automation_state_repair.py`
  - `backend/main.py`
  - Account UI and Admin Agent Config contracts
- Expected behavior change:
  - Explicit fraud, suspicious-activity, or risk-review evidence routes to `Automation / Fraud Account` and retains the safe four-group, one-follow-up workflow; Website remains optional.
  - Balance, payment, free-tier, package, quota, plan, or usage-related suspension routes to `Automation / Account Suspension`, extracts optional context, and never asks, emails, or replies automatically.
  - Invoice billing, payment methods, credit terms, refunds, pricing, subscriptions, packages, plans, and financial settings stay `Account & Billing` rather than Enablement.
  - Classification-only reroute remains side-effect free; a separate targeted repair command can refresh stored Fraud/Suspension fields without sending mail or customer replies.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_account_intake.py backend/tests/test_account_case_reroute.py backend/tests/test_route_correction.py backend/tests/test_account_route_pipeline.py backend/tests/test_account_suspension_field_extractor.py backend/tests/test_account_automation_state_repair.py backend/tests/test_account_verification_automation.py backend/tests/test_agent_config.py backend/tests/test_account_ui_contract.py backend/tests/test_workspace_admin_ui_contract.py`

## 2026-07-31 - Account Suspension classification-only Agora Router exception

- Area or subsystem: `/account` Agora Router and Account Suspension routing
- Prompt or model version: `account-agora-v5` / existing `intent_router` profile
- Summary: Allowed a clearly reported non-fraud account suspension to enter the Automation Router as a classification-only candidate without inventing a backend operation. Added `classification_only_automation` as a stable Agora Router reason code.
- Reason: Case 12523 clearly reported that the account was stopped after purchasing an extra usage package, but the Agora Router sent it to Human Review because the customer did not request a sufficiently concrete backend action. Account Suspension is intentionally classification-only and should not have that requirement.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/account_route_pipeline.py`
- Expected behavior change:
  - Explicit non-fraud suspension reports can reach `Automation / Account Suspension` with `backend_operation=null` and `reason_code=classification_only_automation`.
  - The Automation Router must still confirm `account_suspension`; the exception does not apply to vague account requests or other Automation categories.
  - Fraud/risk/security-review suspensions remain candidates for `Automation / Fraud Account`, while billing questions without a reported suspension remain `Account & Billing`.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_account_route_pipeline.py backend/tests/test_agent_config.py`

## 2026-07-31 - Fraud suspension-review template recognition

- Area or subsystem: `/account` Automation Router
- Prompt or model version: `account-automation-v6` / existing `intent_router` profile
- Summary: Added the complete Agora suspension-review information request as strong `fraud_account` workflow evidence: Company Information, Contact Information, Use Case, and Payment Information must all be present in the quoted notice.
- Reason: Case 12475 included Agora's standard suspension notice and supplied all four requested groups, but did not contain the literal words fraud or risk. The Router therefore misclassified it as the classification-only Account Suspension route.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
- Expected behavior change:
  - Suspensions containing the complete four-group Agora review template route to `Automation / Fraud Account` even without literal fraud terminology.
  - The word suspended alone remains insufficient; balance, package, payment, quota, free-tier, and usage-related suspensions without the complete template remain `Automation / Account Suspension`.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_account_route_pipeline.py backend/tests/test_agent_config.py backend/tests/test_account_automation_state_repair.py`

## 2026-07-31 - Account Suspension field grounding for normalized summaries

- Area or subsystem: `/account` Account Suspension field extractor
- Prompt or model version: `account-suspension-fields-v2` / existing `intent_router` profile
- Summary: Clarified that every record passed under Customer messages is approved customer-source evidence even when ingestion normalized it into third-person wording. Required the extractor to cite short, byte-exact, contiguous source quotes without joining across line breaks.
- Reason: Case 12523's customer-source record began with `Customer reports`, causing the LLM to reject all fields as a non-customer summary. Case 12532 contained line-wrapped quota text, and synthesized whitespace caused otherwise correct fields to fail grounding validation.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
- Expected behavior change:
  - Normalized customer-source summaries can provide suspension status, known reason, and customer actions.
  - Line-wrapped customer text remains extractable through short contiguous quotes while the existing exact-grounding validator continues to reject invented evidence.
  - Extraction remains optional and classification-only: missing or uncertain fields never trigger a follow-up, email, or Human Review.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_account_suspension_field_extractor.py backend/tests/test_account_automation_state_repair.py backend/tests/test_agent_config.py`

## 2026-08-04 - Account Suspension moved to Account & Billing

- Area or subsystem: `/account` Agora Router, Account & Billing Router, Automation Router, and Account Suspension extraction
- Prompt or model version: `account-layered-router-v4`, `account-billing-v1`, `account-automation-v7` / existing `intent_router` profile
- Summary: Added an Account & Billing sub-router with `account_suspension` and `other`, routed non-fraud suspension reports away from Automation, and removed Account Suspension from the Account-only Automation taxonomy. The existing grounded suspension extractor now runs as a classification-only Account & Billing handler.
- Reason: Non-fraud account suspension currently has no automated operation, customer follow-up, or internal handoff. Keeping it under Automation made `route_status=automated` and lifecycle state imply execution that never occurs.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/account_route_pipeline.py`
  - `backend/services/account_billing_handlers.py`
  - `backend/services/account_automation_handlers.py`
  - `backend/services/account_case_reroute.py`
  - `backend/services/account_full_reroute.py`
  - `backend/main.py`
  - Account UI and Admin Agent Config contracts
- Expected behavior change:
  - Balance, payment, package, quota, plan, or usage-related suspension reports route to `Agora / Account & Billing / Account Suspension` with `route_status=not_automated` and `automation_handler=null`.
  - Refunds, balances, payment methods, pricing, account administration, and other financial requests route to `Agora / Account & Billing / Other`.
  - Fraud/risk/security-review suspensions remain `Automation / Fraud Account`.
  - New cases, ordinary replies, and full reruns re-extract optional suspension status, known reason, and customer actions without asking, emailing, or generating a customer reply.
  - `/client` and the shared legacy Automation registry remain unchanged.
- Verification:
  - Targeted Account route, intake, reroute, correction, UI, Admin, prompt, and feature-list checks.

## 2026-08-05 - Legal and regulatory complaints remain Uncategorized

- Area or subsystem: `/account` Agora Router and single-case rerun validation
- Prompt or model version: `account-layered-router-v5`, `account-agora-v6` / existing `intent_router` profile
- Summary: Added a high-priority legal/compliance primary-intent rule so long third-party fraud, regulatory, or enforcement complaints remain `Agora / Uncategorized` even when they contain evidence-extraction or server-log commands. Automation `Unregistered` remains available as the registered Automation Router diagnostic fallback.
- Reason: Case 12562 was incorrectly sent to the Automation Router because a late request to extract server logs outweighed the message's primary legal and regulatory complaint intent.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/account_route_pipeline.py`
  - `backend/services/account_admin.py`
  - `backend/main.py`
- Expected behavior change:
  - Legal, regulatory, compliance, enforcement, and third-party fraud complaints use `legal_compliance_request`, stop before Automation Router, and route to Human Review.
  - Definite but unregistered backend operations continue to produce `Automation / Unregistered` for taxonomy-gap discovery.
  - `/account/cases/{account_case_id}/rerun` can run one fresh Account Case through routing, extractors, and handlers without touching other cases.
- Verification:
  - Unit route contract confirms the legal/compliance path invokes only Intent Classifier and Agora Router.
  - Single-case rerun contract confirms only the requested Case is selected.
  - Post-deploy live rerun of Case 12562 will verify `Agora / Uncategorized + Human Review`.

## 2026-08-06 - Cross-deployment internal email payload compatibility

- Area or subsystem: `/account` Automation internal handoff delivery
- Prompt or model version: `internal-handoff-v1` / deterministic domain renderers
- Summary: Added a version-aware payload upgrader so unsent Fraud, Invoice, Enablement, and Quota handoffs are rebuilt with the current HTML template before retry; added an atomic delivery claim and completion guard.
- Reason: Cases created before a deployment retained the old plain-text payload, and the retry worker sent that persisted payload without re-rendering it. Concurrent workers could also send before one worker's state update was saved.
- Affected files or config:
  - `backend/services/internal_email_payload.py`
  - `backend/services/account_verification_automation.py`
  - `backend/services/billing_automation.py`
  - `backend/services/enablement_automation.py`
  - `backend/services/quota_automation.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/worker.py`
- Expected behavior change:
  - Only unsent retryable payloads are upgraded; sent payloads are never rewritten or resent.
  - Delivery metadata and customer-confirmation idempotency keys are preserved.
  - A single Case/delivery key can be claimed by one worker; unreconstructable payloads enter manual attention without sending legacy content.
- Verification:
  - Legacy payload upgrade, sent-case preservation, four handler renderers, claim-token ownership, and Enablement retry tests pass.

## 2026-08-06 - Internal automation email dual-theme contrast

- Area or subsystem: `/account` Automation internal handoff HTML template
- Prompt or model version: `internal-handoff-v2` / deterministic domain renderers
- Summary: Replaced the low-contrast gray dark palette with explicit Light/Dark theme tokens, Outlook `data-ogsc` overrides, and inline light fallbacks for shared Fraud, Invoice, Enablement, and Quota handoff emails.
- Reason: Outlook dark mode flattened the previous slate-gray card, summary, and quote surfaces into a hazy medium-gray appearance. The email must remain legible in both dark and light themes without relying on client auto-inversion.
- Affected files or config:
  - `backend/services/internal_email_template.py`
  - `backend/services/internal_email_payload.py` (version compatibility through the shared template version)
  - `backend/tests/test_internal_email_template.py`
  - `backend/tests/test_internal_email_payload.py`
  - `design.md`
- Expected behavior change:
  - New emails use `internal-handoff-v2`, deep blue-black dark surfaces, crisp light defaults, explicit text/link/border colors, and Outlook-specific dark selectors.
  - Unsent v1 HTML payloads are rebuilt as v2 while delivery keys and attempt metadata remain unchanged; sent payloads are never rewritten or resent.
- Verification:
  - Theme contract, HTML escaping, Graph content type, four-family payload upgrade, and sent-payload preservation tests pass.

## 2026-08-11 - Account Security & Compliance taxonomy

- Area or subsystem: `/account` Agora Router, Account filters, and Admin Agent Config
- Prompt or model version: `account-layered-router-v8`, `account-agora-v7`
- Summary: Replaced the Account-only Non-technical Agora route with a Security & Compliance classification-only outcome. Security, privacy, trust, audit, data-protection, and compliance evidence requests now receive the `security_compliance` label and remain in Human Review; public company/product questions and legacy Non-technical outputs fail closed to Uncategorized.
- Reason: Non-technical mixed public-information requests did not represent a stable Account support workflow, while security and compliance requests need a distinct operational label without Web or Automation execution.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/account_route_pipeline.py`
  - `backend/services/account_case_filters.py`
  - `backend/services/route_correction.py`
  - Account UI and Admin Agent Config contracts
- Expected behavior change:
  - SOC 2, ISO, DPA, GDPR, Trust Center, security questionnaire, data residency, retention, and similar requests route to `Agora / Security & Compliance` with `route_status=not_automated` and no handler.
  - Token, encryption, authentication, SDK permission, and security implementation troubleshooting remains `Agora Technical`.
  - Historical `agora_non_technical` reads remain compatible, but new layered runs and corrections normalize that removed route to `Agora / Uncategorized`.
  - Security & Compliance cases use the Security & Compliance primary filter only; they are not members of the strict Human Review filter.
- Verification:
  - Account route, filter/API, route correction, UI/Admin contract, compile, JavaScript syntax, and feature-list checks.

## 2026-08-11 - Invoice and Security & Compliance route boundary hardening

- Area or subsystem: /account Agora Router and Account & Billing Router
- Prompt or model version: account-layered-router-v9, account-agora-v8, account-billing-v2
- Summary: Tightened Detailed Invoice to explicit detailed/itemized/transaction-level or line-item invoice/receipt requests, added stable invoice reason codes for missing invoices, charge disputes, and payment/invoice reconciliation, and expanded Security & Compliance examples for Trust Center, ISO, SOC 2, DPA, BCP/DR, vendor due diligence, NDA-gated materials, and security questionnaires.
- Reason: Usage investigations, invoice disputes, missing invoices, and payment reconciliation were being confused with Detailed Invoice automation; security documentation requests were also too close to the legal enforcement fallback.
- Affected files or config:
  - backend/services/prompts/account_routing.py
  - backend/services/account_route_pipeline.py
  - backend/services/account_admin.py
  - backend/services/agent_config.py managed prompt catalog output
  - backend/tests/fixtures/account_route_golden_cases.json
- Expected behavior change:
  - Explicit detailed invoice or full-detail top-up receipt requests route to Account & Billing / Detailed Invoice with route_status=automated, route_family=automated, and the Billing handler.
  - Missing invoice, invoice charge/usage disputes, and payment/invoice reconciliation route to Account & Billing / Other with route_status=not_automated.
  - Trust Center, ISO, SOC 2, DPA, BCP/DR, vendor due diligence, NDA-gated security material, and security questionnaire requests route to Security & Compliance; a broken document link is retained as an additional technical intent.
  - Legal enforcement requests use legal_enforcement_request; the previous legal_compliance_request is accepted only as a compatibility input alias and is canonicalized in audit output.
  - Route execution audit now exposes the stable top-level reason_code alongside stage reason codes.
- Verification:
  - Targeted Account route, prompt/catalog, Admin audit, and golden-fixture tests; no /client, shared legacy router, lifecycle, filter membership, UI, stack restart, or formal Case rerun changes.

## 2026-08-11 - Account filter and Agent Config taxonomy display

- Area or subsystem: `/account` filter UI, Admin Agent Config, and managed Account prompt catalog presentation
- Prompt or model version: `account-agora-v8`, `account-billing-v2`, `account-enablement-fields-v3`, `account-layered-router-v9`
- Summary: Synchronized the visible taxonomy with the layered Account route contract. Automated now exposes the four registered children across Account & Billing and Backend Operation; Unregistered remains a Backend Operation diagnostic fallback. Classification reason codes and execution reason codes are displayed separately in Admin audit.
- Reason: The previous UI fallback omitted Automated children, duplicated the Automated badge next to Primary/Secondary labels, and described Unregistered and Human Review with overlapping membership.
- Affected files or config:
  - `ui/account-ui/app.js`
  - `ui/account-ui/index.html`
  - `ui/workspace-ui/admin/app.js`
  - `ui/workspace-ui/admin/index.html`
  - `backend/services/agent_config.py`
  - `docs/feature_list.md`
  - `docs/roadmap.html`
  - `docs/roadmap/phase2.html`
- Expected behavior change:
  - Account filter dropdowns show full registered Automation paths and counts; All and groups without real children remain disabled.
  - Classification badges show only Primary and Secondary. The case status area remains the single source of truth for Automated / Not automated.
  - Admin Agent Config identifies four registered Automation workflows and labels Unregistered as a diagnostic fallback without a registered handler or Human Review membership.
  - Admin audit separates classification reason code from execution reason code with compatibility fallbacks for older events.
- Verification:
  - Account UI and Admin UI/API contract tests, Agent Config/prompt catalog tests, Node syntax checks, `python3 scripts/verify_feature_list.py`, and `git diff --check`.

## 2026-08-12 - Account Luna route profile and rerun preflight

- Area or subsystem: `/account` layered routers, Account field extractors, and Account rerun startup gate
- Prompt or model version: `account_route` profile (`gpt-5.6-luna`, `xhigh`, 120 seconds)
- Summary: Added an Account-only model scenario for layered routing and Account field extraction. Legacy `INTENT_ROUTER_SCENARIO` remains the profile for `/client`, `decide_support_route()`, and shared legacy handlers. Added a side-effect-free rerun preflight for the Account Case SQL write contract, managed Prompt runtime, and a minimal JSON model probe.
- Reason: The Account rerun failure exposed a 40-column/38-placeholder PostgreSQL write mismatch and did not fail before entering the Case loop. Account routing also needs a higher-reasoning, longer-timeout profile without changing client behavior.
- Affected files or config:
  - `backend/services/llm_profiles.py`
  - `backend/services/account_route_pipeline.py`
  - Account field extractor modules
  - `backend/repositories/ticket_repository.py`
  - `backend/services/account_rerun_preflight.py`
  - `.env.example`, `deployment/docker-compose.single-host.yml`
- Expected behavior change:
  - Account layered route stages and Account extractors resolve `ACCOUNT_ROUTE_SCENARIO`; `/client` and shared router calls retain their existing model settings.
  - Account Case upsert columns, placeholders, and parameters are generated from one 40-field contract.
  - Rerun jobs run the read-only preflight before any Case reset, extraction, email, or reply work; failures persist a structured `preflight_*_failed` reason and process zero Cases.
- Verification:
  - Account/profile/preflight/repository/route/lifecycle targeted tests: `178 passed, 3 subtests passed`.
  - Legacy router and extractor regressions: `124 passed, 8 subtests passed`.
  - Python AST/compile and `git diff --check` passed. Opt-in PostgreSQL round-trip remains skipped unless `RUN_POSTGRES_INTEGRATION=1` and a test DSN are supplied.

## 2026-08-12 - Account rerun fail-fast operator surface

- Area or subsystem: `/account` rerun lifecycle UI, Account Agent Config, and runtime configuration
- Prompt or model version: `account_route` profile (`gpt-5.6-luna`, `xhigh`, 120 seconds)
- Summary: Exposed Account rerun preflight, fail-fast stop, checkpoint counts, and Resume rerun in the Account UI. Admin Agent Config now shows the Account-only Luna profile and explicitly states that `/client` and shared legacy routing keep their existing profiles.
- Reason: A batch rerun must stop at the first system or Case error and make the failed stage and unprocessed scope actionable instead of reporting a misleading completed result.
- Affected files or config:
  - `ui/account-ui/app.js`, `ui/account-ui/index.html`
  - `ui/workspace-ui/admin/app.js`, `ui/workspace-ui/admin/index.html`
  - `backend/services/agent_config.py`
  - `.env.example` and single-host compose overlays
- Expected behavior change:
  - Account displays Running, Preflight failed, Stopped at Case, or Completed.
  - Stopped jobs show failed Case, failed stage, succeeded and unprocessed counts, and call the existing `/resume` API without restarting the original job.
  - The confirmation dialog explains that preflight runs before the Case loop and the first error stops processing.
- Verification:
  - Account/Admin UI contracts, Node syntax, compose/config contracts, feature-list verification, and `git diff --check`.

## 2026-08-12 - Account rerun preflight network resilience

- Area or subsystem: `/account` full-rerun startup and Account-only model profile validation
- Prompt or model version: `account_route` profile (`gpt-5.6-luna`, `xhigh`, 120 seconds; Prompt content unchanged)
- Summary: Replaced the extra live Luna JSON probe with a deterministic Account profile and credential check. Full reruns now freeze the Case inventory before preflight, and the first Case's read-only Prepare performs the first live model request.
- Reason: A transient TLS EOF in the redundant probe stopped the entire rerun before loading its 147-Case inventory, even though the first Case Prepare already provides a side-effect-free model availability check.
- Affected files or config:
  - `backend/services/account_rerun_preflight.py`
  - `backend/main.py`
  - Account rerun and UI contract tests
- Expected behavior change:
  - Preflight still fails before Case processing for an invalid PostgreSQL write contract, missing Prompt runtime, missing credentials, wrong Account model, or wrong reasoning effort.
  - Network and model invocation errors occur during the first Case Prepare and stop before Commit, email, or reply creation.
  - Preflight failures report the frozen total and accurate unprocessed Case count instead of zero.
- Verification:
  - Account preflight, rerun lifecycle, fail-fast/Resume, UI contract, Node syntax, feature-list verification, and `git diff --check`.

## 2026-08-18 - Production Account Zendesk delivery safety

- Area or subsystem: production Account reply publication, Zendesk internal-comment delivery ledger, and Workspace Admin Account operations
- Prompt or model version: No prompt or model content change. Production cases continue to persist the promoted runtime Prompt release snapshot.
- Summary: Production replies can write a Zendesk `public=false` internal comment only when their Case remains a currently registered Automation. The delivery ledger now reconciles `pending` and `outcome_unknown` records with a read-only Zendesk audit lookup; reconciliation never issues a second PUT. Workspace Admin Account automation and billing metrics explicitly read the production profile.
- Reason: A production Case must not permit a manual or retired route to create an external side effect, and an ambiguous Zendesk write outcome must be confirmed remotely before any future operator action rather than retried blindly.
- Affected files or config:
  - `backend/worker.py`
  - `backend/services/zendesk_comments.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/main.py`
  - `backend/services/account_admin.py`
- Expected behavior change:
  - Staging, non-production, unregistered, and retired Automation replies never create a Zendesk delivery claim or comment.
  - A successful audit match records the Zendesk comment id as delivered; a missing or unreadable match stays `outcome_unknown` without a resend.
  - `/api/workspace/admin/account-automation` and Account billing metrics return production-profile data only.
- Verification:
  - `141 passed, 22 subtests passed` across Zendesk, Worker, Workspace Admin, and Account Admin targeted tests.
  - Python and Node syntax checks plus `git diff --check` passed.
  - A live synthetic Zendesk ticket acceptance remains scheduled after the merged root-main stack is running.

## 2026-08-18 - Production Account schema bootstrap correction

- Area or subsystem: production Account Case PostgreSQL bootstrap
- Prompt or model version: No prompt or model content change.
- Summary: Corrected JSON object defaults in the support_account_cases CREATE TABLE and ALTER TABLE templates so Psycopg SQL.format treats them as literals instead of extra placeholders. Updated the Account Case persistence contract from 40 to 44 fields and aligned raw-SQL documentation assertions with raw SQL brace syntax.
- Reason: The first merged local_lightweight stack startup failed before API readiness because an unescaped JSON default caused SQL.format to raise IndexError. No Zendesk synthetic write was attempted while the API was unhealthy.
- Affected files or config:
  - backend/repositories/ticket_repository.py
  - backend/tests/test_repository_configuration.py
- Expected behavior change:
  - Runtime schema initialization composes both the CREATE TABLE and migration ALTER TABLE statements without formatting errors.
  - The bootstrap regression test executes PostgresTicketRepository.initialize through Psycopg composables and asserts the rule_release JSON default.
- Verification:
  - 254 passed, 22 subtests passed across repository initialization plus Zendesk, Worker, Workspace Admin, and Account Admin targeted tests.
  - Python compilation and git diff --check passed.
  - Official root-main stack health and synthetic Zendesk acceptance will be rerun after the repair PR merges.

## 2026-08-18 - Account Automation reply and rerun contract v9

- Area or subsystem: `/account` Automation Persona, Account reply jobs, normal Intake, full rerun, and reply-only recovery
- Prompt or model version: `automation-persona-v9`; model configuration unchanged
- Summary: Unified the active Fraud Account, Account Suspension, and Enablement customer-reply intents across Intake, fresh rerun, and recovery. Customer replies now require the relevant 24-hour handoff or activation/change-window facts, Suspension contact and closing stages are reconstructed from customer history, and trailing AI signatures are removed before the final contract validation.
- Reason: Case `12839` exposed missing Enablement SLA/change-window language and an AI signature in the published reply. Rerun paths also lacked the active two-stage Suspension workflow and could rebuild a generic reply instead of the canonical close decision.
- Affected files or config:
  - `backend/services/automation_persona.py`
  - `backend/services/account_reply_jobs.py`
  - `backend/services/account_full_reroute.py`
  - `backend/main.py`
  - `backend/worker.py`
  - Account Persona, Worker, Intake, full-rerun, and recovery tests
- Expected behavior change:
  - Fraud handoff replies say the relevant team will contact the customer within 24 hours and leave the ticket open.
  - Enablement submission replies include up to 24 hours to activate and a Monday-Friday change window; only an explicit positive internal completion reply can close the ticket.
  - Suspension first replies ask for the preferred contact email and ticket-email choice; only explicit confirmation, successful internal handoff, and durable closing-reply publication can close the ticket.
  - Invalid or conflicting intent/content, legacy Fraud closing jobs, and signed output fail closed or enter Human Review without publishing.
- Verification:
  - `211` tests passed in the Stage 3 full-rerun/recovery suite; the Stage 4 integrated targeted suite and documentation checks are recorded in the task Plan after completion.

## 2026-08-19 - Account Automation signature source removal v10

- Area or subsystem: `/account` Automation Persona configuration, generation, reply validation, and publication fence
- Prompt or model version: `automation-persona-v10`; model configuration unchanged
- Summary: Removed Signature and legacy signoff fields from new Persona configuration and runtime generation. The model now receives an explicit unsigned-body contract, the application adds only the greeting, and a non-destructive tail guard rejects signed output before publication instead of deleting generated text.
- Reason: Acceptance testing showed that v9 still generated the configured Sid signature and then scanned the reply to remove it. Signature-free output must be enforced at its source, while final validation remains a narrow fail-closed safety boundary.
- Affected files or config:
  - `backend/services/account_admin.py`
  - `backend/services/automation_persona.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `ui/workspace-ui/admin/app.js`
  - `ui/workspace-ui/admin/styles.css`
  - Account Persona, Workspace Admin/API, Worker, and reply-contract tests
- Expected behavior change:
  - New Persona writes accept only `instruction` and optional `opener`; historical Signature JSON stays readable but cannot propagate through drafts, publishes, rollbacks, assignments, or jobs.
  - Account Automation replies are generated without a greeting, signoff, name, title, or signature; the application adds only the configured greeting.
  - Signed output is not rewritten. It moves to Human Review before `publish_account_reply()` and before any production Zendesk delivery intent can be persisted.
  - Questions, future/request language, negated customer commitments, and revoked Enablement states cannot satisfy positive handoff, completion, SLA, or closure contracts.
- Verification:
  - Focused source-removal suite: 107 tests passed, with 19 environment-dependent PostgreSQL tests skipped.
  - Publication-fence suite: 120 tests passed.
  - Polarity and current-state suite: 139 tests passed.
  - The final integrated suite and post-merge official-stack evidence are recorded in task `p1-50` and its implementation Plan.

## 2026-08-19 - Prompt Release cross-database deploy sync

- Area or subsystem: deployment-bound Prompt Release pipeline; `/production` independent database bootstrap
- Prompt or model version: release ids unchanged (`pr-*`, random per candidate); prompt content unchanged
- Summary: `deploy_ec2.sh` previously prepared and activated the deployment-bound Prompt Release only in the staging database while passing the same `PROMPT_RELEASE_ID` to the `/production` stack. Because release ids are random, the production database lacked that release row, `api_production` crashed at startup, and the `/production` health gate rolled the whole deploy back. The deploy now replicates the candidate release into the production database (new `prompt_release sync` CLI subcommand and `sync_prompt_release` repository method, content-hash verified and idempotent) before services restart, and re-syncs after activation to align the production release status.
- Reason: close the recurring operational gap from task `p2-73`: every deploy that freezes a new prompt candidate would otherwise fail the `/production` gate and require a manual seed of the production database.
- Affected files or config:
  - `backend/scripts/prompt_release.py`
  - `backend/repositories/ticket_repository.py`
  - `deployment/deploy_ec2.sh`
  - `backend/tests/test_prompt_versioning.py`
  - `backend/tests/test_prompt_versioning_postgres.py`
  - `backend/tests/test_deploy_ec2.py`
  - `backend/tests/test_production_ui_contract.py`
- Expected behavior change:
  - Prompt content and model behavior are unchanged; both databases resolve the same immutable release id.
  - Pre-restart production sync failure aborts the deploy before stopping services; post-activation sync failure logs a WARNING while the candidate release remains deployable.
  - Deploys without `PRODUCTION_TICKET_DB_DSN` behave exactly as before.
- Verification:
  - `backend.tests.test_prompt_versioning`: 21 passed (in the runtime image).
  - `backend.tests.test_prompt_versioning_postgres`: 3 passed against the staging Postgres with an isolated schema.
  - `backend.tests.test_deploy_ec2` + `backend.tests.test_production_ui_contract`: 27 passed.

## 2026-08-19 - Account Automation handoff contract retry v11

- Area or subsystem: `/account` Automation Persona generation, Account reply worker failure metadata, and rerun status reporting
- Prompt or model version: `automation-persona-v11`; model configuration unchanged
- Summary: Fraud and Account Suspension handoff prompts now require the exact standalone sentence `The relevant team will contact you within 24 hours.` Final reply validation runs inside the existing Account AI four-call budget, so invalid model output can be regenerated without adding nested retries or rewriting customer text.
- Reason: Rerun job `account-rerun-f53393771ddd47118d4eb821d83c89e9` stopped at `AC-12715` after a successful model response used a natural handoff paraphrase that failed the deterministic Fraud contract. The prior implementation validated only after the retry loop and persisted a generic generation code with a hard-coded attempt count.
- Affected files or config:
  - `backend/services/account_ai_execution.py`
  - `backend/services/automation_persona.py`
  - `backend/worker.py`
  - `backend/main.py`
  - Account AI, Persona, Worker, rerun, dispatch, and UI contract tests
- Expected behavior change:
  - Transport failures and response-validation failures share one maximum of four physical model calls.
  - Invalid, signed, empty, contract-breaking, or forbidden-value output remains fail closed; the application does not append or rewrite the required sentence.
  - Exhausted Persona failures preserve the specific contract code and real attempt count; unpublished candidate bodies are not persisted.
  - Terminal failed reruns expose an observed summary of linked reply jobs without mutating historical counters or scheduling work.
- Verification:
  - Targeted Account AI, Persona, Worker, rerun fail-fast/recovery, dispatch, and UI suite: 210 tests passed with 29 subtests passed.
  - Integrated Account Automation suite: 519 tests passed with 19 environment-dependent PostgreSQL tests skipped and 67 subtests passed.
  - Final integrated and post-merge live-stack evidence is recorded in task `p1-50`.

## 2026-08-19 — Automation Persona missing-information publication fence

- Change type: Persona 校验行为（prompt 版本不变，仍为 automation-persona-v11）。
- Affected files or config:
  - `backend/services/automation_persona.py`
  - `backend/tests/test_automation_persona.py`
- Expected behavior change:
  - `request_missing_information` 回复新增确定性合同：禁止独立句 "The relevant team will contact you within 24 hours." 及任何数字时长承诺（如 within 2 days）；命中即转 Human Review，不做剪裁。
  - 追问类回复保持 ownership 合同不变；fraud handoff 精确句合同仅在信息齐全并发送内部邮件后适用。
- Verification:
  - `backend/tests/test_automation_persona.py` 及目标套件 623 passed；详见任务 `p1-51`。

## 2026-08-20 - Registered Enablement routing boundary v10

- Area or subsystem: Account layered Agora Router and registered Enablement routing
- Prompt or model version: `account-agora-v10`, `account-layered-router-v10`; model configuration unchanged
- Summary: Clarified that an explicit desired outcome to enable a named feature is a backend operation even without the phrase "from your end". Added an inspectable deterministic boundary for unambiguous Media Relay activation requests before the Agora Router model, while retaining the downstream Backend Operations Router.
- Reason: Production cases `12874` and `12875` had the same customer body but drifted between `backend_operation` and `uncategorized` because the first routing boundary was model-only.
- Affected files or config:
  - `backend/services/prompts/account_routing.py`
  - `backend/services/account_route_pipeline.py`
  - `backend/services/enablement_automation.py`
  - Account route and Enablement tests
- Expected behavior change:
  - Explicit Media Relay activation requests consistently enter `backend_operation` and continue through the registered Enablement router.
  - How-to, SDK/API configuration, troubleshooting, failure, and pricing requests continue through the model router and do not use the deterministic activation boundary.
  - Vague requests and unregistered feature names remain outside the deterministic boundary and keep their existing fail-closed behavior.
- Verification:
  - Focused Account routing and ownership suite: 340 passed with 51 subtests passed.
  - Direct deterministic repetition check: 20 of 20 identical Media Relay activation requests resolved to `media_relay` without an Agora Router model call.

## 2026-08-24 - Unexpected-reply grounding tool switched to ragflow-docs-search

- Area or subsystem: Account Automation unexpected-customer-reply answer generation (`p2-93`)
- Prompt or model version: existing `RAG_ANSWER_SCENARIO` model profile and `backend/services/prompts/rag_answer.py` prompt unchanged; tooling mode changed to `ragflow-docs-search` upstream `main`
- Summary: The fallback adapter now supplies RAGFlow official-doc passages as the only factual context to the existing strict RAG answer prompt. Recent ticket messages are labeled interpretation-only, and generated output must cite at least one retrieved chunk before it can enter the existing Account reply job.
- Reason: The user requested that unexpected replies for automated cases use the shared official-document skill instead of SupportPortal's local RAG service.
- Affected files or config:
  - `backend/services/ragflow_docs_search_skill.py`
  - `backend/services/account_reply_rag_fallback.py`
  - `backend/skills/ragflow-docs-search/SKILL.md`
  - `backend/skills/ragflow-docs-search/scripts/search.py`
  - `RAGFLOW_BASE_URL` and `RAGFLOW_API_KEY`
- Expected behavior change:
  - The model sees only official-document passages returned by the RAGFlow skill as citable evidence.
  - Answers without a valid retrieved citation, malformed JSON, insufficient evidence, missing credentials, or tool/model errors are not published and move through the established fail-closed Human Review path.
  - The generation call uses the remaining fallback timeout as a hard single-attempt budget; model retries and provider/model fallbacks are disabled on this customer-outbound path so timeout cannot multiply across attempts.
  - The existing RAG answer model selection, reasoning effort, signature removal, reply job, Production public-delivery, and route-back behavior remain unchanged.
- Verification:
  - Skill/fallback plus Account intake/worker regression passed `295` tests, including default-client selection, command/env contract, grounded answer mapping, untrusted-source rejection, hard total-timeout enforcement, missing-key/timeout reasons, and existing Production/staging escalation behavior.

## 2026-08-24 - RAGFlow fallback answers: luna generation + persona v13 rendering (p2-111)

- Model changes:
  - New pinned scenario `ragflow_answer` (default `openai:gpt-5.6-luna`, reasoning `xhigh`, 120s, no model/provider fallback; env overrides `RAGFLOW_ANSWER_MODEL` / `RAGFLOW_ANSWER_REASONING_EFFORT`). The shared `rag_answer` scenario (gpt-5.4) is unchanged for the local RAG pipeline and client flows.
  - Automation Persona prompt version `automation-persona-v12` → `automation-persona-v13`.
- Prompt changes:
  - `build_rag_answer_system_prompt(core_content_only=True)`: the ragflow fallback generation now asks for the core technical explanation only (no greeting/name/signing); a downstream persona voices the final customer reply. The JSON contract (answer/key_steps/citations/insufficient_evidence) is unchanged.
  - Persona v13 adds the `rag_fallback_answer` policy: restate `provided_answer` technical content in a natural first-person voice, never add/drop/reinterpret technical facts, never invent links (the application appends reference links), no signature.
- Behavior impact:
  - Unexpected-reply fallback replies are now persona-rendered (mini model) instead of published verbatim; reference links are appended deterministically after the persona body passes the existing publication gates. Fail-closed escalation semantics are unchanged.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_worker.py backend/tests/test_account_intake.py backend/tests/test_automation_persona.py backend/tests/test_ragflow_docs_search_skill.py backend/tests/test_account_reply_rag_fallback.py backend/tests/test_account_reply_version_fence.py backend/tests/test_llm_profiles.py -q`

## 2026-08-24 - Account chain small-task models move to gpt-5.6-luna (p2-114)

- Model changes:
  - New pinned scenario `account_extractor` (default `openai:gpt-5.6-luna`, reasoning `low`, 30s, env overrides `ACCOUNT_EXTRACTOR_MODEL` / `ACCOUNT_EXTRACTOR_REASONING_EFFORT` / `ACCOUNT_EXTRACTOR_TIMEOUT_SECONDS`). All seven production-case field extractors (quota, detailed invoice, verification, suspension, enablement, billing automation) move from the shared `intent_router` scenario (gpt-5.4-mini, 3s) to it; the client-flow intent router keeps its model and tight latency budget unchanged.
  - `automation_persona`, `enablement_completion_classifier`, `billing_reply`, and `enablement_reply` scenario defaults move from `gpt-5.4-mini` to `gpt-5.6-luna` (effort stays low; persona/billing/enablement-reply timeouts ->30s, classifier ->20s).
- Behavior impact:
  - The production account case chain now runs a single model family (gpt-5.6-luna): route (xhigh), field extraction (low), persona render (low), completion classifier (low), RAGFlow fallback generation (xhigh). Per-case token stats should show only `openai:gpt-5.6-luna`.
- Verification:
  - See task p2-114 evidence (llm_profiles scenario defaults/env overrides + affected suites, 482 passed).

## 2026-08-26 - Application-side Persona signature removed (p2-68)

- Prompt or model versions:
  - `automation-persona-v14` -> `automation-persona-v15`.
  - `engineer-guided-persona-v1` -> `engineer-guided-persona-v2`.
  - `engineer-investigation-reply-v8` -> `engineer-investigation-reply-v9`.
  - `engineer-guardrail-final-v1` -> `engineer-guardrail-final-v2`.
  - Model configuration is unchanged.
- Summary:
  - Persona-generated customer replies no longer contain a signoff, agent name, title, or application-side signature. Zendesk remains the sole owner of the final signature/footer.
  - English replies use the trusted customer first name in the canonical `Hi, Name` greeting. A greeting generated in either of the first two model paragraphs is removed before the canonical greeting is added, preventing `Thank you for waiting.` followed by a second `Hi Name`.
  - Engineer Guardrail and the shared Zendesk public-comment write boundary reject signature-shaped tails, including legacy `Best Regards,\nSid`, `Thanks in advance!\nSid`, and a standalone trailing `Sid`.
  - Final approval requires the current Guardrail version, so an old Slack approval button cannot publish a pre-change draft.
- Verification:
  - Customer composer, Persona, Guardrail, prompts, Zendesk public write, Slack action, Worker delivery, Account Automation, Client runtime, RAG, routing, and investigation suites cover unsigned output, named greetings, duplicate-greeting removal, stale approvals, and fail-closed legacy delivery.

## 2026-08-27 - Enablement completion acknowledgement and archive contract (p2-121)

- Area or subsystem:
  - Account Automation Enablement completion reply generation and publication validation.
- Prompt version:
  - `automation-persona-v15` -> `automation-persona-v16`; model configuration is unchanged.
- Reason:
  - Case 13061 passed Persona v15 but produced only a terse enablement confirmation and closing statement because the prompt and publication contract did not require contextual acknowledgement, customer-facing archive wording, or future-contact guidance.
- Affected files or config:
  - `backend/worker.py`
  - `backend/services/automation_persona.py`
  - `backend/services/automation_test_scenarios.py`
  - Corresponding Worker, Persona, version-fence, and scripted scenario tests.
- Prompt and fact changes:
  - Enablement completion facts now include `completion_acknowledgement=additional_information` when an Assistant `request_missing_information` message is followed by a customer message; otherwise they use `patience`.
  - Completed Enablement replies must acknowledge that context, confirm the feature is already enabled, say that the current Case will be archived now, and direct future questions or concerns to a new ticket.
  - Customer-facing `archived` wording is explicitly independent of the delivery pipeline's internal Zendesk `target_status=solved` state.
- Publication contract:
  - Missing acknowledgement, enabled, archive, or new-ticket guidance is rejected. Negative, interrogative, future enablement, and delayed archival wording is rejected.
  - A `patience` reply cannot claim that the customer provided additional information.
  - Contract failures retain the existing retry-exhaustion and Human Review behavior; no deterministic reply template or fallback was added.
  - Unpublished v15 Persona payloads are regenerated through the existing prompt-version fence. Already published replies and historical Case 13061 are unchanged.
- Verification:
  - Persona, Worker, scripted Automation scenario, and Account reply version-fence suites: 184 passed with 43 subtests passed.
  - Live E1/E2, deployment, container restart, and Zendesk readback were intentionally not run at the user's request.

## 2026-08-27 - Enablement completion natural-language validation (p2-122)

- Area or subsystem:
  - Account Automation Enablement completion reply generation and publication validation.
- Prompt version:
  - `automation-persona-v16` -> `automation-persona-v17`; model configuration is unchanged.
- Reason:
  - Case 13068 completed Enablement handling and internal confirmation, but Persona v16 completion drafts exhausted the existing retry budget at publication validation. The rejected candidate bodies were not persisted; local reproduction proved that the validator rejected semantically valid wording such as `We appreciate your patience` and `If you need anything else, please open a new ticket`.
- Prompt and validator changes:
  - The patience acknowledgement accepts `thank/thanks` or `appreciate` with `patience/waiting` while continuing to reject invented additional-information claims.
  - Future-help guidance accepts `questions/concerns`, `need anything else`, or `need further help` only when the same positive clause directs the customer to open a new ticket or case.
  - Acknowledgement, current enabled state, archive-now wording, and new-ticket guidance remain mandatory. Negative, interrogative, future enablement, delayed archival, and contradictory wording remain rejected.
  - Completion failures now identify the failed component as acknowledgement, enabled state, archive, or new-ticket guidance. Retry exhaustion and Human Review behavior are unchanged; rejected candidate bodies are not persisted.
- Historical and runtime scope:
  - Unpublished v16 Persona payloads are regenerated through the existing prompt-version fence. Already published replies and Case 13068 are unchanged; no historical reply is rerun or backfilled.
  - Deployment, stack restart, live Production cases, email, Zendesk writes, and Case 13068 mutations are outside this implementation at the user's request.

## 2026-09-01 - Engineer investigation reply custom agent endpoint routing (p2-130)

- Area or subsystem:
  - Engineer investigation reply LLM routing (scenario `engineer_investigation_reply`, `_generate_investigation_reply_turn`).
- Prompt version:
  - `engineer-investigation-reply` prompt content is unchanged for the official endpoint; new conditional prompt-layer output contract applies only when a custom agent endpoint is configured.
- Reason:
  - The investigation turn needs to route to an external agent endpoint (Hermes investigation agent with TencentDB Agent Memory) so turns gain autonomous tool use and long-term team memory. Custom agent endpoints ignore the Responses `text.format` json_schema enforcement the official endpoint applies, so the structured-output contract must be restated in the prompt.
- Tooling and routing changes:
  - New env overrides `ENGINEER_INVESTIGATION_REPLY_BASE_URL` / `ENGINEER_INVESTIGATION_REPLY_API_KEY` route the scenario to a custom OpenAI Responses-compatible endpoint; unset values keep the official endpoint and key.
  - With a custom endpoint, `fallback_models` is empty (model fallback is a model-tier downgrade; an agent endpoint has no tier below it and a same-endpoint retry would repeat a multi-minute investigation turn). The deepseek provider fallback contract is unchanged; provider degradation stays visible in `message_meta.model_name`.
  - With a custom endpoint, the full investigation reply json_schema is appended inline to the user prompt (single source: `_investigation_reply_extra_payload`), requiring the final reply to be exactly one schema-conforming JSON object.
- Verification:
  - `backend/tests/test_llm_profiles.py` + `backend/tests/test_llm_factory.py`: 38 passed (endpoint override two-state coverage; agent-endpoint output-item extraction).
  - Live end-to-end against the local Hermes agent stack: valid schema-conforming investigation turn (state/message/draft_customer_reply), `generation_status=succeeded`, conversation auto-captured to L0 memory; failure paths retain the existing fail-closed turn.
  - `backend/tests/test_investigation_flow.py`: 113 passed, 2 pre-existing multi_agent failures reproduced on clean main.

## 2026-09-01 - Hermes investigation agent endpoint production cutover (p2-133)

- Area or subsystem:
  - Engineer investigation reply LLM routing (scenario `engineer_investigation_reply`) — production endpoint configuration, no code change (routing code landed in p2-130).
- Prompt version:
  - Unchanged. The custom-endpoint inline JSON schema contract from p2-130 now applies in production because the custom endpoint is configured.
- Reason:
  - The Hermes investigation agent stack (hermes-agent + TencentDB AgentMemory memory-core, formerly local podman) was migrated to a dedicated ECS Fargate service so production investigation turns gain autonomous tool use, pilot CLI access (binary baked, credentials bootstrap pending), and long-term team memory, without touching the EC2 `/production` behavior chain.
- Tooling and routing changes:
  - Production routing for `engineer_investigation_reply` on EC2 `/production` now resolves to `ENGINEER_INVESTIGATION_REPLY_BASE_URL=https://supportcenter.stellarix.space/v1` with `ENGINEER_INVESTIGATION_REPLY_API_KEY` (SSM `hermes-api-server-key`) and `_TIMEOUT_SECONDS=300` via `~/SupportPortal/.env` (compose `env_file` passthrough); api/worker×2 production containers recreated on image `52df67fcbbfc`.
  - New ECS service `supportportal-production-hermes` (2-container task: hermes + memory-core, localhost interconnect) exposed through the existing ALB as `/v1,/v1/*` (priority 101, Bearer auth); fresh memory store bootstrapped (admin `usr-yipctouhlx`, team `team-yipeq84apx`, agent `agt-yipfo802v8` with the dual-mode investigation/review prompt).
  - ECS worker task definition rev14 carries the same three env values (no consumer in the ECS worker until the engineer investigation chain migrates there).
  - Default behavior when the three env values are removed is byte-for-byte the previous official-endpoint routing; provider fallback and fail-closed contracts unchanged.
- Verification:
  - Public endpoint: `GET /v1/models` 200; real `POST /v1/responses` LLM turn returned valid output and auto-captured to L0 memory (`/search/conversations` hit).
  - EC2 production container probe: profile shows `base_url=https://supportcenter.stellarix.space/v1`, `timeout=300.0`, `fallback_models=()`; `invoke_responses_text` returned successfully and the same turn is persisted in Hermes memory (session `c7a4d9de`), proving the EC2→ALB→Hermes path end to end.
  - EC2 main stack and `/production` public `/health` remain 200; existing ECS three-role services unaffected.

## 2026-09-01 - Reproducible Transformers tooling runtime

- Area or subsystem:
  - Shared single-host and ECS Python model tooling dependencies.
- Prompt or model versions:
  - Prompt content, Prompt Release IDs, model names, providers, reasoning effort, timeouts, and fallback policies are unchanged.
  - The effective dependency contract is fixed at `transformers==4.46.3`; the full single-host ML profile uses compatible `sentence-transformers==5.7.0` and CPU-only `torch==2.13.0+cpu`.
- Reason:
  - Separate pip transactions allowed Sentence Transformers 6.x to replace the explicit Transformers 4.46.3 pin. A single hash-verified dependency graph now rejects that conflict during lock generation instead of changing tooling during deployment.
- Affected files or config:
  - Python base/full requirements locks, ML direct requirements, both runtime Dockerfiles, lock tooling, build contracts, and operations documentation.
- Expected behavior change:
  - No intended prompt or model-output contract change. Dependency selection becomes deterministic, and CPU-only hosts stop carrying unused CUDA packages.
- Verification:
  - The full lock resolves Transformers 4.46.3, Tokenizers 0.20.3, Sentence Transformers 5.7.0, Accelerate 1.14.0, and PyTorch 2.13.0+cpu with SHA256 hashes.
  - The built full image passed import checks for the Transformers pipeline and embedding runtime, plus `pip check`; the lightweight image retained Transformers 4.46.3 without torch.

## 2026-09-01 - Engineer Slack inbound switches to Hermes investigation turns (p2-113)

- Area or subsystem:
  - Engineer Slack collaboration on `/automation/production` (ECS API inbound endpoints `api/integrations/slack/engineer-cases/*`; account intake not_automated branch).
- Prompt version:
  - Prompt content unchanged; the processing semantics behind `@bot` messages switch from "engineer guidance as the technical fact source + Persona polish" (EC2 guided reply) to the investigation turn chain (`automation_engineer_collab` → `investigation_flow` → Hermes agent endpoint, scenario `engineer_investigation_reply` with inline JSON schema contract from p2-130).
- Reason:
  - Technical (not_automated) cases on ECS are handled by the Hermes investigation agent: opening rounds are deterministic (zero LLM) at engineer-case creation, and each `@bot` message drives a Hermes investigation turn with Tencent AgentMemory recall; guardrail and final approve human gates are unchanged before any Zendesk delivery.
- Tooling and routing changes:
  - New ECS API endpoints `thread-bindings/resolve|messages|actions` (X-N8n-Request-Token auth, idempotent via ticket repository; degrades to 503 without TICKET_DB_DSN instead of failing readiness).
  - Account intake not_automated branch now persists the opening investigation round (`start_or_refresh_investigation`, deterministic) plus an `engineer_ai_response` Slack thread event next to `engineer_case_opened`.
  - Terraform `api_secrets`/`worker_secrets` gain TICKET_DB_DSN, n8n token, engineer Slack team/channel, and the Hermes endpoint SSM parameters; `ENGINEER_INVESTIGATION_REPLY_TIMEOUT_SECONDS=300` for both roles.
- Verification:
  - `backend/tests/test_automation_ecs_api.py` + `backend/tests/test_automation_account_intake.py`: 31 passed, 2 subtests passed (new endpoint contract coverage: 401/503/422/resolve semantics; opening round messages and slack events).
  - Terraform production root `validate` passed (docker hashicorp/terraform on zacBot).
  - Live acceptance (test-mode gate then real-mode canary with full guardrail/final approve/Zendesk readback) is tracked in p2-113 and runs after the p2-134 pilot/probe gate and rollout.

## 2026-09-02 - Engineer approval chain relaxation: agent self-report trusted (p2-137)

- Area or subsystem:
  - Engineer investigation reply readiness, Slack guardrail action, final approve preconditions.
- Prompt version:
  - Prompt content unchanged; the backend reply-readiness re-derivation layer is removed.
- Reason:
  - p2-113 canary (ticket 13220) proved the split-era readiness contract (all-anchors-verbatim `all()` corpus check, symptom recovery, state downgrade) unusable in practice: every revision round required re-pasting evidence phrases verbatim, and the first approval on any ticket 409'd without a prior customer comment. User decision 2026-09-02: remove the readiness gate entirely, keep the deterministic guardrail checks plus two-step human approval as the only gates.
- Tooling and routing changes:
  - `_normalize_reply_readiness` is now a structural passthrough of the agent's self-reported reply_readiness (anchors verification, symptom-level recovery, state downgrade, and blocker injection removed; dead helpers deleted; the dual-use `_contains_strong_root_cause_claim` kept for prompt-context sanitization).
  - Slack guardrail action no longer requires `ready_for_customer_reply` at the endpoint; guardrail Rule 2 removed; the proof check now trusts the self-reported `ready_for_customer_reply`.
  - Final approve falls back to a live Zendesk ownership snapshot when no comment-sync baseline exists (mirrors the legacy /production path; ZendeskCommentError → 503), and account intake writes an empty-comments baseline snapshot when the engineer case is created, so first approvals no longer depend on a prior customer comment.
- Verification:
  - Focused regression (investigation flow, execute agent, guardrail, intake, slack, comment sync, ECS api): 212 passed + 7 subtests; five removed-behavior tests deleted, passthrough assertions updated, new positive case for self-reported readiness and the intake baseline assertion added.

## 2026-09-02 - Hermes pure investigation + persona-assembled customer replies (p2-138)

- Area or subsystem:
  - Engineer investigation reply (agent role), automation persona (new intent), Slack collab assembly step.
- Prompt version:
  - `engineer-investigation-reply` v9 → **v10** (pure investigator: report findings/evidence/next steps; never write the customer draft).
  - New persona prompt version `engineer-investigation-persona-v1` for the new `engineer_investigation_reply` intent (guided contracts reused: provided_answer sole authority, source-value invention blocked, customer-name-missing contract).
- Reason:
  - User decision 2026-09-02: Hermes investigates, the automation persona assembles the customer reply, guardrail plus human approval stay as the gates. This also structurally removes the double-greeting defect (ticket 13234: persona prepends generic greeting while the agent's own bare "Hello," survived stripping) and restores customer-name greetings via the account-case name chain.
- Tooling and routing changes:
  - `automation_engineer_collab.process_engineer_investigation_message`: when the investigation turn self-reports awaiting_confirmation, facts are distilled (conclusion/evidence/known_facts/solution) into `provided_answer` and `render_automation_reply` runs immediately with the new intent; the persona content becomes `active_investigation.draft_customer_reply`, readiness gets `source_mode=persona_assembled` with ready=true, persona metadata is recorded, and the Slack thread event carries the persona header plus the Run Guardrail button. Persona failures persist an `engineer_ai_response_failed` event and surface 502.
  - `engineer_agent` schema: `draft_customer_reply` optional (missing-draft fail-closed removed); agent drafts are ignored by the ECS chain.
- Verification:
  - Focused regression across persona/collab/agent/investigation/guardrail/slack/intake/api/prompt suites: 314 passed + 48 subtests; new suites cover assembly happy path, failure path, active-skip, intent contracts, and the schema relaxation.

## 2026-09-03 - Prompt Release replication and ECS deployment gate

- Area or subsystem:
  - Prompt Release source-to-target replication, ECS release build preflight, and Production deployment orchestration.
- Prompt or model versions:
  - Prompt content, model names, providers, reasoning effort, and Persona v23 are unchanged.
- Reason:
  - Repeating a sync for an existing Release ID previously trusted the ID without proving that source and target build/content were identical. The ECS builder validated only the ID shape, and Production had no single command that tied Prompt activation to a healthy three-role rollout.
- Tooling and release-contract changes:
  - Same-ID replication now compares `build_ref` and the complete `prompt_key + content_sha256` fingerprint before any release status transition. Equivalent target-local version numbers are allowed; different content fails closed. Repeated sync returns the target's real status and never downgrades an active release.
  - `PROMPT_RELEASE_TARGET_DSN` and `PROMPT_RELEASE_TARGET_SCHEMA` provide secret-safe target inputs; the existing `--target-dsn` remains compatible. `--defer-activation` keeps the target candidate until explicit activation.
  - Prompt `validate` is read-only. The ECS builder runs it before selecting a builder or starting OCI work.
  - `deployment/deploy_automation_ecs_release.sh` is the sole Production deploy command. It defers Prompt activation until Route, Worker, heartbeat, API, public health, digest, CloudWatch, and EC2 backup checks pass; pre-activation failures restore captured service revisions, while activation uncertainty requires readback reconciliation instead of blind rollback/retry.
- Verification:
  - Prompt repository/CLI, builder and deployment tests cover same-ID idempotency/mismatch, version remap, deferred activation, environment-only DSN handling, pre-build rejection, task-definition preservation, Worker safety gates, rollout order and rollback contract.
## 2026-09-05 - Hermes canonical Runtime contract and no-L0 investigation boundary (p2-146)

- Area or subsystem: Hermes Case Workflow producer/callback, Investigation Runtime, AgentRelay Coordinator Grant, read-only Case Knowledge tools, and close-time promotion.
- Prompt or model version: Shared wire contracts remain `v1` because the real producer was never enabled; the schemas and fixtures are replaced in place. No production model or prompt was activated.
- Reason: Make SupportPortal the business-contract owner, remove model-controlled routing/completion fields, preserve Case/session lineage through native wake, and allow historical Case Knowledge reads while keeping all investigation-time L0 capture and memory writes disabled.
- Tooling and behavior change: `real` mode freezes a canonical turn only after Slack root binding, retries only the same request payload on unknown transport outcomes, validates callback lineage and receipts, and queues closed promotions only after review, guardrail, sanitization, and revision proof. Exact `Investigation result: test` still yields `reason=test` only at Summary Guardrail and does not bypass Persona, customer safety checks, human approval, or Zendesk idempotency.
- Verification boundary: Runtime/Relay component harness uses canonical HTTP turns and synthetic identities; native Hermes wake, no-L0, contract hash, and local PostgreSQL behavior are tested separately. No production deployment or real Slack, Zendesk, TencentDB, or zac-agent queue was used.
