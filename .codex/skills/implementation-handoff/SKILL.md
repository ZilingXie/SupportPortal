---
name: implementation-handoff
description: Prepare executable implementation plans, execute an agreed plan, or consolidate repairs after failed acceptance. Use for plan-to-executor handoffs and PR evidence in any coding client. Skip ordinary questions, brainstorming outlines, and isolated small edits without a handoff.
---

# Implementation Handoff

Roles follow the task, not the client or model. Codex, ZCode, and other executors use the same contracts. The planner owns the difficult design decisions; the executor should not have to reconstruct them from chat history. Do not change models or reasoning settings automatically.

## Choose the current mode

- **Plan:** read [planning.md](references/planning.md). Planning is read-only unless the user separately authorizes saving a plan.
- **Execute:** read [execution.md](references/execution.md). Implementation requires authorization; verify an existing review gate before proceeding.
- **Repair:** read [repair.md](references/repair.md). A failed review authorizes findings, not automatically implementation. Continue repairs when the current scope is already authorized.
- **Independent review:** use the repository's review skill when present. Use the contract and evidence format below; do not implement during read-only review.

Only load the applicable mode. For small work, use objective, scope, and verification rather than filling every section. This skill does not grant network, deployment, business-write, cross-thread messaging, or delegation permission.

## Human decisions are explicit stops

The user normally reads the goal and final acceptance, not intermediate handoffs. Every role must directly ask the user about unresolved product choices, material architecture/scope changes, unapproved migrations/resources/side effects, weaker acceptance, or uncertain ownership/authorization. A PR note or a proposed default is not a decision.

Start the request with **"需要你确认：任务已暂停在 ..."**. State the verified facts, the decision, recommended option and consequences, and the alternative. Stop the affected action and dependent implementation until an explicit answer arrives. Relevant read-only diagnosis may continue. Silence, elapsed time, another agent's agreement, or an unread handoff is not consent. Do not issue an overall pass while a required human decision remains pending.

Handle equivalent local implementation choices and already authorized routine work autonomously. Do not send every test failure back to the user.

## One current contract and evidence record

Use a stable plan name and revision. Give critical contracts stable IDs (C1, C2); keep their meaning across repairs. A compact table is enough:

| Contract | Trigger / real entry / state | Verified mechanism | Observable result | Check and phase | Evidence / gap |
| --- | --- | --- | --- | --- | --- |

Evidence identifies command/check, actual result, source commit, environment, and artifact location. Separate confirmed observations, executor reports, assumptions, and unavailable evidence. Counts, mocks, health checks, and accepted requests prove only their actual layer.

Keep code acceptance, deployment verification, and complete business acceptance distinct. A partial pass names its scope and remaining gates. Waiting for external samples is not a new implementation failure; resubmit when evidence changes. Bundle nonblocking record corrections with the next meaningful handoff.

## PR as the handoff artifact

Use one branch/worktree/PR per independently reviewable task or phase. Keep repairs on that PR with new commits. Cross-repository work uses a linked PR set and exact commits. Preserve the current agreed plan, contract evidence, stable finding IDs, human decisions, and next action in the PR body or linked versioned document. Do not publish secrets or unsanitized customer data.

For independent acceptance, create a Draft PR and commit/push every file needed for review. The short message may be just plan name, PR URL, full HEAD, and evidence link. A PR URL alone does not identify reviewed code. Disclose additional local diff; do not claim it is covered by PR acceptance. Do not use a finalize/merge command to merely create a review artifact.

The user asking to give a plan to another thread and return for acceptance selects independent review. Plain implementation without that request does not add a review wait. A gate persists through repairs until passed or explicitly removed. Self-review cannot release it; the original planner can review only if it did not implement the changes.

After a matching independent pass, the authorized executor follows the project's merge/release process without another confirmation. Inspect new integration changes: re-review affected behavior, retain evidence for unaffected behavior. Test success, a SHA pin, and PR readiness are not approval by themselves. Review approval never expands the environment or business-write authorization.

A review's recorded SHA is not automatically a user-pinned source requirement. Within the already agreed scope, preparing the changed version for the required focused re-review needs no new human product decision. Pause merging pending that review; ask the human only if a material choice or authorization change actually remains. Do not message another thread unless the user authorized that communication; a forwardable review handoff is sufficient.
