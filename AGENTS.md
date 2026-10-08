# SupportPortal Agent Rules

## Authorization and Human Decisions

Planning/review/diagnosis/status are read-only unless implementation is authorized. Every role must explicitly ask the human and pause dependent actions for an unresolved product/architecture choice, scope or acceptance change, unapproved side effect, user-pin change, or uncertain ownership/authorization. Use **"需要你确认：任务已暂停在 ..."** with facts, options and consequences; a PR note, another agent's agreement or silence is not approval. Equivalent local choices and already authorized routine work need no renewed permission.

## Top Priority: ECS Deployment

- ECS changes go to **Preproduction first**. Normal Production promotion requires Preproduction verification and explicit user authorization. Direct Production is only for an explicitly identified urgent Production hotfix, never inferred from a generic deploy/finalize request.
- Authorized ECS implementation includes routine build/release, Preproduction deployment and verification after any requested review gate. Developer-tooling/documentation-only work does not deploy the application. Preserve immutable releases and all schema, Prompt, image, health and rollback gates.
- Known clean main advances are not automatic blockers: inspect integration impact. Continue within existing scope when changes are unrelated; ask before unapproved behavior/configuration/migration/side effects or changing a user-pinned source. See [release continuation and restricted hotfix rules](docs/agent_workflow_details.md#preproduction-deployment-continuation).

## Source Of Truth and Context On Demand

`AGENTS.md` is the sole repository agent-rule entry point. Read details only when relevant; no fixed startup scans.

| Need | Source |
| --- | --- |
| Plan / execute / repair handoff | [.codex/skills/implementation-handoff/SKILL.md](.codex/skills/implementation-handoff/SKILL.md) and only the current mode |
| Completed implementation or PR review | [.codex/skills/review-implemented-plan/SKILL.md](.codex/skills/review-implemented-plan/SKILL.md) |
| PR preparation and merge | [PR handoff](docs/agent-workflows/pr-handoff.md) |
| Workflow edge cases, release, stack checks, records | [Workflow details](docs/agent_workflow_details.md) |
| Environment/build/test operations | [Operations index](docs/operations/README.md), then the relevant runbook |
| Routes/models/env/import inventory | Root `.codesight/wiki/index.md` + one targeted article, or CodeSight tools |
| Symbol definitions/callers/data flow | Existing CodeGraph, then current task source; targeted search if unavailable |
| Literal docs/config/log/rule text | Targeted `rg` and direct reads |
| Live release/configuration/health | Timestamped target-environment evidence; static maps do not prove live state |

UI source: `design.md`; `docs/agent.md` is a legacy redirect. Progress source: `docs/project/{phases,modules,functions,tasks}/*.json`; Project Overview is the current view, historical Roadmap pages are not. `.codesight/` stays generated/gitignored; maintained operations belong under `docs/operations/`. Root maps may lag task edits. After finalized code changes refresh once from root main with `npx codesight --wiki`; pure documentation skips this. Do not initialize CodeGraph without authorization.

## Named Plans and Review Handoffs

Use a stable `计划名称` and current contract revision. Roles are independent of Codex/ZCode/model; planners settle important design and verification decisions before handoff. Retrieve the agreed plan and actual version, not just the plan name. One reviewable task/phase keeps one branch/worktree/PR through repairs. The PR or linked document carries current contracts, exact HEAD, evidence, open findings and decisions. Small edits need no full template.

## Execution Modes

- `实施计划，需要验收`, or an explicit plan-to-another-thread-then-return-for-review arrangement, requires independent acceptance before finalize/merge/deploy. The gate persists across continuations and repairs. A planner can review if it did not implement; executor self-review cannot release it. Keep a Draft PR and workspace while waiting.
- Plain `实施计划` with no existing gate follows targeted verification and normal finalization. An acceptance table alone does not add a wait.
- A review states one of `通过 / 未通过 / 证据不足`, scope, reviewed commit/diff, evidence/limits and permitted next action. A pending human decision blocks overall acceptance. Separate code, deployment and full business acceptance.
- A matching pass resumes already authorized work without an extra confirmation. Reassess integration changes; re-review affected behavior, preserve unaffected evidence. Production/new business side effects remain separately authorized. Follow [PR handoff](docs/agent-workflows/pr-handoff.md) for version pins and Draft readiness.

## Working Rules

- Root `/Users/xieziling/Desktop/personal_proj/SupportPortal` remains clean `main`; no tracked edits there. Sync via `scripts/workflow/create_task_worktree.sh <slug>` before first edit; use only this task's `.worktrees/<slug>` and `codex/<slug>`. Retain the same branch during repair. Never borrow another task's workspace or use `mac` / `mac-integration` workflows.
- Check `git status --short --branch`, `git branch -vv`, `git worktree list --porcelain` before editing/resuming/finalizing/cleanup; report the current task branch/path/state. Confirm the task location on subsequent edits. Ignore unrelated workspaces. Stop for ambiguous ownership, foreign changes or an invalid task workspace; never silently stash/revert/overwrite them.
- `main` is PR-only and squash-only. After verification and applicable review, use `scripts/workflow/finalize_task_to_main.sh`; it refreshes, pushes, creates/reuses PR, merges, fast-forwards root and syncs existing CodeGraph. It does not enforce human acceptance or clean the task workspace.
- After successful finalize, run `scripts/workflow/cleanup_task_worktree.sh <branch>` **from root**, not from the directory being removed. Completion requires applicable runtime checks and cleanup, not merely merge. Exclude `.worktrees/`, `.superpowers/`, `.DS_Store`, secrets and unrelated changes from commits.
- Before authorized remote n8n mutation, refresh active published and divergent draft snapshots under `docs/integrations/n8n/workflows/`, record both versions, and run `python3 scripts/n8n/validate_workflow_snapshots.py`. Refresh after readback. Store credential references/redaction placeholders, never executions, pin data, secrets or customer data.

## Implementation and Verification

- Read actual source before changing it. Use the smallest existing mechanism satisfying the requirement end to end. Add fallback/retry/compatibility only for a requirement, existing contract or demonstrated failure; define trigger, state and visible reason. No silent success, speculative abstractions or hashes as a substitute for atomicity.
- Classify the diff as documentation or code. Documentation needs direct text/link/format/generation checks, not application tests/restarts. Code needs narrowly relevant behavioral verification. Test real entries, mock external boundaries, and use isolated PostgreSQL for PostgreSQL guarantees. Report actual results/skips; do not weaken assertions or replace proof with test counts.
- Runtime-relevant code requires applicable post-merge official-stack verification from root main; see [completion and stack rules](docs/agent_workflow_details.md#completion-reporting-contract). Developer-tooling-only changes do not require application restart/deployment.
- Code reports separate changes and verification and use `状态：已完成`, `状态：已合并，运行验证未完成`, or `状态：实现完成，未 finalize` according to those completion rules. Documentation reports state their actual boundary plainly.
- RAG changes update `docs/rag_change_log.md`; prompt/model/tooling behavior updates `docs/prompt_change_log.md`. Authorized Production releases also update `docs/release_notes.json`. Changes to environment/build/deployment/testing workflow update the relevant operations page/runbook with source links and verification date.
- Use narrow tests/logs/traces. Do not invoke `supportportal-run-report` by default; only when requested or reinstated as a gate.

## Project Progress Registry

Runtime/user-visible/API/data/config/business changes must find the owning Function and Task before implementation; create a Task only when needed and a Function only for a separately reportable capability. Maintain the same Task's status/next_action/evidence: done requires evidence, blocked requires a recorded blocker. Task IDs are `pN-xx`; phase moves require a new ID and migration alias.

After registry/meeting/PR-summary source changes, run `python3 scripts/generate_project_overview.py --write` then `--check`; never edit `docs/projectoverview-data.js` by hand. Function status derives from child Tasks. Major capabilities also update `docs/feature_list.md` and run `scripts/verify_feature_list.py`. Pure docs/tests/rules/refactors/developer or operations tools need no Task/Overview unless tracked progress or runtime behavior changes. Ask if this classification remains materially ambiguous.
