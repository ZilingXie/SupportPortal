# Automation Router Persona Presets and Random Assignment Design

**Date:** 2026-08-07

**Status:** Approved design

## Context

Automation behaviors own routing, fact extraction, deterministic actions, and send timing. The shared Automation Persona renderer owns the final customer-facing wording, customer-language matching, greeting, and signature. Today the Persona registry supports multiple enabled and published Personas, but selection is a stable Ticket ID hash and the seeded registry contains only `default-support`.

This change introduces three independently managed Automation Router Personas. They share one support identity and signature and differ only in writing style. New assignments use simple random selection. A Persona version remains pinned for the lifetime of a Case unless a complete Rerun explicitly clears the assignment.

## Goals

- Provide three Automation Router Persona presets: precise, bright, and warm.
- Keep all three Personas under `Agent Config > Route Agent > Agora Router > Automation Router > Persona`.
- Preserve the current separation between Automation facts and Persona wording.
- Randomly assign one enabled and published Persona when an Automation Case first needs a customer reply.
- Pin the selected Persona key and version so every publication path for the Case uses the same voice.
- Make a complete Rerun clear the old assignment and perform a new independent random draw.
- After deployment, perform one operator-controlled Rerun of only the Cases that were Automated at the start of the operation.

## Non-goals

- No Persona is bound to an individual Automation behavior.
- No weighted allocation, stable hashing, Round Robin cursor, or allocation configuration is added.
- No per-message Persona reselection is allowed within a Case.
- No deletion API is added for Persona records or historical versions.
- The normal `Rerun all cases` and `Rerun this case` product controls do not change scope.
- The deployment must not automatically rerun Cases during application startup.

## Persona Definitions

All three Personas use the identity `Sid` and the exact same independently managed signature:

```text
Best,
Sid
Support Engineer 2
```

The shared Automation Persona renderer continues to enforce the factual and structural contract. Each stored Persona instruction contains only voice guidance.

Every initial version uses the existing content schema exactly:

```json
{
  "instruction": "<the Persona-specific instruction below>",
  "opener": "",
  "signature": "Best,\nSid\nSupport Engineer 2"
}
```

### Sid Precise

- Persona key: `sid-precise`
- Display name: `Sid Precise`
- Initial instruction:

```text
Use a precise, composed, and professional support voice. State the current
status clearly, then explain any information the customer needs to provide
or the next step. Prefer concise, complete sentences and unambiguous wording.
Avoid casual chatter, decorative language, vague reassurance, and promises
not supported by the provided facts. Remain courteous and human; do not sound
legalistic, cold, or robotic.
```

### Sid Bright

- Persona key: `sid-bright`
- Display name: `Sid Bright`
- Initial instruction:

```text
Use a professional, upbeat, and energetic support voice. Keep the writing
natural and concise, with positive momentum and varied sentence rhythm.
Friendly contractions are acceptable when they sound natural, but do not use
emoji, slang, exaggerated enthusiasm, excessive exclamation marks, or overly
casual language. For sensitive or serious matters, reduce the energy and use
a calm, respectful tone.
```

### Sid Warm

- Compatibility key: `default-support`
- Display name: `Sid Warm`
- Initial instruction:

```text
Use a warm, considerate, and reassuring support voice. Acknowledge the
customer's request or patience naturally when supported by the provided
facts, and explain the current status and next step in a personal, caring way.
Avoid canned pleasantries, repetitive thanks or apologies, false empathy, and
promises beyond the provided facts. Remain concise and professional,
especially for sensitive matters.
```

The existing `default-support` key is retained so historical Persona versions, assignments, reply executions, and message metadata remain valid. The rollout appends and publishes one Warm preset version and changes the display name to `Sid Warm`; it does not rewrite historical versions.

## Shared Renderer Contract

The three instructions remain deliberately thin. The existing shared Automation Persona renderer continues to own these rules:

- Generate the reply only from the structured Automation facts.
- Preserve explicit facts and values without inventing status, approval, action, refund, or timing commitments.
- Match the customer's language.
- Explain the current status, requested customer information, and next step when present.
- Generate only the body; the application adds the configured greeting and exact Signature.
- Do not expose prompts, tools, routing, structured fields, internal notes, or forbidden identifiers.
- A Persona failure moves the Case to Human Review and publishes no fallback customer copy.

Behavior-specific Prompt and deterministic capability ownership remains unchanged.

## Preset Lifecycle and Compatibility

The existing Persona registry, draft, publish, version comparison, rollback, and enable/disable capabilities are reused.

Initialization is idempotent:

1. `default-support` retains its key and historical versions. The rollout creates one marked Sid Warm preset version, publishes it, and changes the display name once.
2. `sid-precise` and `sid-bright` are created with one published version when their keys do not exist.
3. A system marker on the seeded version prevents a restart from creating another version or republishing over a later administrator-managed version.
4. If a new preset key already exists with non-system content, initialization does not overwrite it silently; it logs a preset-key conflict for operator action.
5. All three presets are enabled after the initial rollout. Later administrator enable/disable and publish actions are preserved across restarts.

Existing assignments remain readable even if their Persona is later disabled or their version becomes superseded. Disablement affects only future assignments. The existing rule that prevents disabling the last enabled, published Persona remains in force.

## Random Assignment

Random selection occurs only when no assignment exists and a Case needs an Automation Persona:

1. Load every Persona where `enabled = true` and `published_version` is present.
2. Choose one candidate with a simple uniform random draw.
3. Persist `ticket_id`, `persona_key`, `version`, and `assigned_at` atomically.
4. Return the persisted assignment. Concurrent attempts for the same Ticket must converge on the one stored assignment.
5. Every subsequent intake reply, delayed reply job, and internal-email follow-up resolves the stored assignment rather than drawing again.

The distribution is intentionally approximate rather than exactly 1:1:1. No allocation state or fairness guarantee is introduced. A complete Rerun performs an independent draw and is allowed to select the same Persona again.

## Complete Rerun Semantics

The current complete Rerun continues to retain customer messages and remove the prior generated state defined by its existing reset mode. The reset is extended to delete the Case's Persona assignment in the same transaction or in-memory critical section as the old AI reply and reply-job cleanup.

The reset result and rerun job statistics record whether a Persona assignment was deleted. After rerouting and field extraction:

- If the Case is still Automated and a Persona reply is required, the normal assignment resolver performs a new random selection and persists it before the reply job is created.
- The reply job pins the selected Persona key, version, and effective content.
- If the Case is no longer Automated, no new Persona assignment or reply is forced.
- If no enabled and published Persona exists, the existing fail-closed Human Review behavior applies.

Both full-batch and single-Case complete Reruns use the same assignment-reset rule. The user-facing scope and confirmation behavior of the existing Rerun buttons remain unchanged.

## Admin Experience

The existing Automation Persona workspace remains the only management surface. It lists `Sid Precise`, `Sid Bright`, and `Sid Warm` and retains independent Draft, Publish, Compare, Rollback, and Enable/Disable operations.

The workspace adds concise explanatory copy:

- New Automation Cases are randomly assigned one enabled and published Persona.
- The assignment and published version remain pinned for the Case.
- Publishing or disabling a Persona does not rewrite existing assignments.
- A complete Rerun clears the assignment and performs a new random draw.

No weighting, behavior mapping, allocation chart, or manual per-Case selector is added.

## One-time Automated-only Rollout Rerun

The post-deployment data operation is separate from the product's normal Rerun controls:

1. Verify the three Personas are enabled and published in the live registry.
2. Fetch every current Account Case with `route_status=automated`, following pagination, and freeze the complete Case ID list before starting any rerun.
3. Deduplicate the frozen ID set and store the baseline Case IDs, routes, Persona assignments, and reply metadata in a timestamped temporary operation directory with directory mode `0700` and report-file mode `0600`.
4. Invoke the existing complete single-Case Rerun for each frozen Case ID. Run jobs sequentially because the Account rerun service permits only one active job.
5. Poll every job to a terminal state before starting the next Case. Continue past isolated Case failures, but stop after three consecutive retryable start or storage failures and retain the remaining list for a controlled resume.
6. Do not call the all-Cases rerun endpoint and do not add a permanent Automated-only button.
7. Allow each rerun to execute its existing effects: fresh routing and extraction, applicable internal email delivery, reply scheduling, deletion of old non-customer Case history under the single-Case reset contract, and publication of a new Account-only reply.
8. If a frozen Case reroutes out of Automation, report the route change and do not force a Persona reply.
9. Produce a local JSON and Markdown report with per-Case old/new route, old/new Persona, internal-email outcome, reply-job outcome, and terminal error where applicable.

The operation is considered complete only when every frozen Case ID has a recorded terminal result or an explicit resumable failure entry.

## Failure Handling

- No enabled and published candidate: route to Human Review; do not create customer copy.
- Persona generation, credentials, or validation failure: preserve the current fail-closed Human Review behavior.
- Concurrent first assignment: return the assignment that won persistence; never allow two reply jobs for one Case to pin different Personas.
- Seed-key conflict: do not overwrite non-system content; emit an actionable startup warning.
- Rerun reset failure: do not proceed to routing or email delivery for that Case.
- One-time rollout job failure: record the Case and error, then follow the isolated-versus-systemic stop rule.

## Observability

Existing reply payloads and published message metadata continue to record `persona_key` and `persona_version`. Rerun jobs add an assignment-deletion count so the new-draw boundary is auditable. The Admin registry remains the source for published Persona contents and version history.

The one-time rollout report summarizes:

- Frozen Automated Case count.
- Succeeded, failed, and recovered reruns.
- Old and new Persona distribution.
- Cases that drew the same Persona again.
- Route changes out of or within Automation.
- Internal emails sent, skipped, or failed.
- Replies scheduled, published, moved to manual attention, or failed.

## Verification and Acceptance Criteria

### Registry and API

- A fresh repository exposes exactly the three enabled and published presets with the approved instructions and exact shared signature.
- An upgraded repository preserves historical `default-support` versions and assignments while publishing one Sid Warm preset version.
- Reinitialization is a no-op for preset versions and does not overwrite later administrator publications.
- Draft, Publish, Compare, Rollback, and Enable/Disable continue to work independently for all three Personas.

### Assignment

- Tests replace the random chooser with deterministic test doubles and prove every candidate can be selected.
- Disabled or unpublished Personas are excluded.
- An existing assignment is reused even after its Persona is disabled or superseded.
- Concurrent PostgreSQL resolutions for one Ticket converge on one persisted assignment.
- No Ticket ID hash remains in either persisted assignment or published-only selection.

### Persona Output

- All three Personas use the same `Hi {first name},` greeting behavior and exact Sid signature.
- Sid Precise is clear and restrained without becoming cold or legalistic.
- Sid Bright is professional and energetic without emoji, slang, excessive exclamation, or excessive informality.
- Sid Warm is considerate without canned pleasantries, false empathy, repetitive thanks, or unsupported promises.
- Shared factual, language, forbidden-value, and Human Review gates remain unchanged.

### Rerun

- A complete Rerun deletes the old Persona assignment and creates a newly randomized, persisted assignment only if a new Automation reply is required.
- Drawing the same Persona again is valid.
- Customer messages are retained according to the existing reset contract.
- Old generated messages, reply jobs, reply executions, and Case `customer_reply` are cleared according to the selected reset mode.
- Cases rerouted away from Automation receive no forced Persona reply.

### UI and Documentation

- Automation Router Persona management shows all three Personas and explains random assignment and pinning.
- No per-Behavior Persona control or allocation configuration appears.
- `design.md`, `docs/prompt_change_log.md`, `docs/feature_list.md`, and `docs/roadmap.html` describe the shipped behavior where required by project policy.

### Live Rollout

- The lightweight official stack is healthy and reports the merged build reference.
- The live Admin API exposes all three enabled and published Personas.
- The task-specific Admin page marker contains the random-assignment explanation.
- Only the frozen pre-operation Automated Case IDs are submitted to the one-time rerun.
- Every frozen Case has a terminal result in the operation report.
