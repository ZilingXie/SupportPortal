# Agent Workflow Details

This file contains low-frequency workflow details that used to live in `AGENTS.md`.
Read it only when the concise hot-path rules in `AGENTS.md` point here.

`docs/agent.md` is a legacy compatibility redirect for old UI-spec links, not an agent-instruction file.

---

# Collaboration Rules

## On-Demand Preflight Triggers
1. Do not add project-level fixed startup checks. The concise `AGENTS.md` hot path intentionally avoids default AgentMemory, `using-superpowers`, `codegraph_status`, and Git/worktree preflights.
2. AgentMemory is on demand: search or write memory only when the user asks for memory, a durable memory write is needed, or the task clearly depends on historical preferences/global rules that are not already in the prompt.
3. Skills are trigger-based, not disabled: if the current platform's skill rules require a skill, the user names a skill, or the task semantically matches a skill, use that skill exactly as required. Never interpret on-demand preflight as permission to skip an applicable skill.
4. CodeGraph status is diagnostic only. Use CodeGraph for symbol lookup, call flow, data flow, and symbol-level impact; use the `AGENTS.md` Context On Demand table for other context. Check `codegraph_status` only when CodeGraph fails, appears unavailable/stale, or the task is to diagnose indexing.
5. Git/worktree state is safety-gated. Run `git status --short --branch`, `git branch -vv`, and `git worktree list --porcelain` before repo-tracked edits, resuming a paused task, finalization, cleanup, or any workspace-safety decision; do not run them for ordinary chat, pure planning, or read-only documentation inspection.
6. Native `rg` is preferred for docs, literal text, comments, config keys, logs, and rule-file searches. CodeGraph is not required for those non-structural lookups.

## Control CC
For explicitly requested coordinated Claude Code execution, use the project-local `control-cc` skill. Its availability does not itself authorize delegation.

`control-cc` may create temporary detached candidate worktrees under `/tmp/control-cc-runs/...` from the active task branch for isolated Claude Code execution. Candidate worktrees are not task branches: do not push, finalize, merge, or treat them as authoritative. Export reviewed patches from candidates, integrate them sequentially into the real project-local `codex/<thread>` task workspace, then clean the candidates. These candidate-run limits do not apply to a normal Claude Code session following `AGENTS.md`.

## Coding Client Parity

Codex, ZCode and Claude Code use the same repository `AGENTS.md` and applicable skills. Each can plan, execute or review within the assigned role and authorization; none is a default handoff-only worker. Preserve user-imposed worker-only/read-only limits and independent-review gates. Confirm actual rule/skill loading for a newly configured client; do not assume another client's private configuration is inherited.

## Review Skill Redirect
The completed-implementation review and finalization process lives in the project-local `review-implemented-plan` skill. Review requests are read-only by default; fixes or finalization require explicit authorization for the current scope, including authorization already given earlier in the conversation. Passing checks alone does not authorize changes or merging. Use that skill when the user says things like `实现了计划，你来review一下`, asks for a completed plan or worker handoff review, or gives a similar post-implementation review/finalization request.

## Implementation Handoff and Independent Acceptance

The role workflow lives in [implementation-handoff](../.codex/skills/implementation-handoff/SKILL.md); independent review lives in [review-implemented-plan](../.codex/skills/review-implemented-plan/SKILL.md). Select the mode from [AGENTS.md](../AGENTS.md#execution-modes). The human's plan-to-another-thread-and-back-for-review arrangement counts as a requested gate. Tool/client/model names do not assign roles. Pending human decisions require an explicit question and pause, never inferred approval from an unread handoff.

Use [PR handoff](agent-workflows/pr-handoff.md) for Draft preparation, evidence, exact version matching, readiness and finalization. Do not call finalize to create a PR awaiting review. A passing review authorizes only continuation already within scope; code acceptance is distinct from deployment and business verification.

### Forwardable Review Result

```text
计划名称 / 修订号：...
验收结论：通过 / 未通过 / 证据不足（选一项）
范围：本轮修复 / 全部代码 / 部署运行 / 完整业务验收
验收对象：PR URL + full HEAD；额外 diff；工作区
合同证据：检查、实际结果、版本、环境、报告位置
开放项：稳定 finding ID、缺失证据、人工待决项（无则明确写无）
下一步：在既有授权内允许执行的动作及停止点
```

A matching pass requires no extra "continue". Keep unaffected closed findings and evidence; re-review new behavior or affected paths. Unchanged external waits and nonblocking wording edits do not justify another full acceptance cycle.

## Preproduction Deployment Continuation
1. For ECS runtime changes, `实施计划` authorizes the normal Preproduction path, and `实施计划，需要验收` authorizes that path after independent acceptance. An authorized Preproduction deployment includes the routine CodeBuild rebuild, new release, deployment, and verification needed to finish that same scope. Do not ask for deployment authorization again merely because a build must be replaced or resumed. A request only to review, diagnose, or edit rules does not authorize deployment; existing user limits remain binding.
2. When a known concurrent PR advances clean `main`, inspect the commits and diff from the selected release to the proposed new source. Confirm provenance, workspace ownership, and that the changes do not add unapproved application behavior, migrations, configuration changes, or external side effects. Rules, tests, and developer tooling may be outside the release gate's allowlist without changing ECS application behavior; verify their actual impact rather than inferring it from filenames or the gate's `runtime changes` label. Preserve unrelated workspaces and uncommitted changes; unresolved ownership or unexpected changes still require stopping.
3. If that inspection confirms the same authorized scope and the existing gate requires a new release, select and freeze the reviewed source commit, run the established CodeBuild/release pipeline, and continue to Preproduction without another confirmation. Do not bypass or relax release gates, edit the old manifest/digests, repoint an existing release, or silently skip failed checks. Retain immutable images, Prompt release gates, Terraform checks, applicable fresh-credential checks, and rollback requirements. A technical gate failure requires diagnosis; it is not automatically a request for renewed authorization.
4. A commit/release chosen by the agent for an earlier build is not a user-imposed pin. An explicit user instruction to deploy only a particular commit/release or reuse exactly the same images is binding: ask before replacing it. Also ask before introducing an unapproved runtime change, migration, configuration change, external side effect, or protected-system action, or when read-only inspection cannot resolve the target, authorization, or workspace uncertainty. Preproduction authorization does not authorize Production promotion.
5. Report the old and new source/release identifiers, why a rebuild was needed, the relevant diff assessment, and the verification outcome in normal progress/evidence reporting. This provides visibility without adding an approval checkpoint for routine work already authorized.

### Restricted Production Hotfix Source

**受限热修复来源例外（restricted hotfix source exception）**: for a user-authorized urgent Production hotfix whose fixes must ship from a non-main baseline (e.g. the production commit lineage), the release gates accept `--hotfix-baseline <full-sha>` plus `AUTOMATION_RELEASE_HOTFIX_AUTHORIZED=<reviewed hotfix full SHA>`. Every use requires all of: an explicit urgent-hotfix authorization from the user for that specific baseline and SHA, the hotfix commit descending from the pinned baseline, and the operator-run release tooling passing the flags at every gate. The hotfix branch is a build vehicle only — `main` remains the single source of truth, the branch is not merged back, and it is deleted once a full `main` release supersedes it. This exception never applies to routine releases, never skips schema/migration/health/rollback gates, and must not be inferred from scripts, defaults, or prior hotfixes.

## CodeGraph First For Code Context
1. Use CodeGraph first for structural symbol questions: definitions, callers/callees, data flow between symbols, and symbol-level impact. This is not a prerequisite for every code-related task; use [Context On Demand](../AGENTS.md#source-of-truth-and-context-on-demand) for project-surface inventories, operations knowledge, and live-state questions.
2. Use `rg` for literal text, comments, log messages, configuration keys, and documentation. Read the relevant current task-workspace source after locating it; generated root-main maps may not reflect unmerged task changes.
3. If CodeGraph is unavailable, uninitialized, or stale, report that explicitly and fall back to the narrowest native search needed. Check `codegraph_status` only as a diagnostic when failure/staleness is suspected. Do not initialize a CodeGraph index unless the user explicitly requests it; for an initialized project, use `codegraph sync` to refresh changed files.

## Branch Workflow

The hot-path ownership and finalization rules are in [AGENTS.md](../AGENTS.md#working-rules). These are the exceptional details:

1. `origin/main` is the freshness authority. Branch creation uses `create_task_worktree.sh`, never a direct switch in root. Root must be clean, non-diverged main. Keep one stable task branch/worktree until finalization; title changes do not rename it. A name collision gets a reported suffix. An intentional shift to another feature requires the user's scope/branch decision.
2. Unrelated worktrees, including paused or dirty ones, do not block this task. Do not inspect or clean them deeply. Ambiguous changes in this task's workspace require a stop and human decision before switching, testing, publishing or deleting; do not stash them. Detached HEAD is only for transient history inspection, never an active task or release vehicle.
3. If root is on `codex/*`, stop normal development and report it; use `rehome_task_worktree.sh` only within authorization or obtain direction. Unsupported mac/mac-integration state requires resolution, never use it as a workaround. Session end does not free ownership.
4. For repository policy maintenance, inspect existing policy before using `bootstrap_main_repo_policy.sh`; remote policy changes require authorization. Desired policy is PR-only, squash-only, auto-merge enabled, remote branch deletion on merge, no force pushes. Do not repeatedly change policy during ordinary tasks.
5. Before finalization commit all required task files, including untracked additions; check the current diff and appropriate tests. Finalize can auto-commit tracked changes and merge newer main, so gated tasks use `--reviewed-head` per [PR handoff](agent-workflows/pr-handoff.md). A finalization lock serializes promotions; each waiter must recheck its integration.
6. Finalize owns merge, root fast-forward and existing CodeGraph sync. CodeGraph failure after merge leaves the workspace for recovery; it must not initialize an index. Cleanup is separate from root because removing the caller's directory breaks subsequent ZCode shell spawns (`ENOENT`). Keep release evidence and task-owned deliverables.
7. Task workspaces needing container checks use `link_worktree_env.sh` within the existing environment authorization. Do not treat a linked runtime environment as an isolated test database.
8. For runtime-relevant post-merge single-host verification: inspect mode with `inspect_single_host_stack_mode.sh`; resolve an auxiliary stack under the applicable scope. Default restart is `restart_single_host_stack.sh --mode local_lightweight --db remote`. Root `.env` is authoritative; `--use-local-env` is a deprecated alias. Full mode is only for tasks needing full/ML capabilities. Check `/health`, merged build ref and a task-specific live marker for frontend work. The ECS Preproduction-first boundary takes precedence. Tooling/docs-only tasks do not restart an application stack.
9. Restart validates/builds before stopping the healthy stack; failed startup/health restores the prior API image but still fails verification. Do not call the task complete merely because a PR merged. Use the completion contract below for pending cleanup, synchronization or runtime checks.

## Completion Reporting Contract
1. The final response must state `任务类型：文档改动` or `任务类型：代码改动`. Do not call a documentation change a code change merely because it updates rules, and do not call a code change documentation-only merely because it also updates docs or tests.
2. Documentation-change reports do not require a test, restart, health, build-ref, or live-stack section. Report the changed documents and any direct wording, format, generation, or registry checks that actually ran. Documentation tasks still use the normal branch and finalization workflow unless the user explicitly narrows that workflow.
3. A code-change final response must use exactly one of these status lines: `状态：已完成`, `状态：已合并，运行验证未完成`, or `状态：实现完成，未 finalize`. Do not substitute `完成`, `已验收`, `merged, cleanup pending`, or another phrase that obscures the required state.
4. `状态：已完成` is allowed only when all applicable hard gates have evidence: targeted verification passed; `scripts/workflow/finalize_task_to_main.sh` succeeded; the PR is merged; root `main` is synchronized; the current task worktree and local branch were removed; and, for stack-relevant code, official-stack restart, `/health`, build provenance, and task-specific live verification passed. The report must include the PR reference, root `main` SHA, restart path, `/health.app_build.ref`, official-stack mode, auxiliary-stack result, and task-specific live marker result.
5. `状态：已合并，运行验证未完成` is required when a PR has merged but any post-merge requirement is unexecuted or failed, including root synchronization, cleanup, official-stack restart, health, build provenance, or a required live marker. Include the PR reference, current root SHA, exact missing or failing command/result, current stack state when known, and the next action. Retain ownership until the state becomes `已完成`.
6. `状态：实现完成，未 finalize` is required when code and available local verification are complete but commit, PR creation, merge, `finalize_task_to_main.sh`, or post-merge validation has not completed. Include the current branch, task workspace, clean/dirty state, targeted verification that ran, and the specific reason finalization has not occurred.
7. Every code-change final response must have separate `主要变更` and `验证结果` sections. `主要变更` states behavior and compatibility decisions, not commands. `验证结果` lists only commands or direct checks that actually ran and their observed outcomes. It must separately list required tests or checks that did not run, with the concrete reason, such as a missing dependency or unavailable service.
8. Empty command output, a command-wrapper error, a stale working directory, a non-zero exit code, or an interrupted command is not success evidence. Report the relevant action as unexecuted, unknown, or failed until a subsequent command produces direct evidence. Never state that a commit, PR, merge, cleanup, restart, health check, or live check occurred merely because it was intended or announced.
9. When root `main` advances after the task PR merges, post-merge stack verification must use the then-current root `main` SHA. Read that SHA immediately before restart and require the image, `/health.app_build.ref`, and runtime build ref to match it. If root `main` advances again before reporting `已完成`, repeat the affected restart/provenance verification against the new SHA.
10. Use this compact code-change report shape, omitting only fields that are inapplicable to the selected status:

    `状态：…`

    `任务类型：代码改动`

    `PR：…`  `Root main：…`

    `官方重启：lightweight/full；Health build ref：…；官方 stack：…；辅助 stack：…`

    `主要变更：` behavior-focused bullets.

    `验证结果：` targeted tests/checks, live markers, and required checks not run with their reasons.

## UI Design Source Of Truth
1. All new or refactored UI under `ui/` must follow `/Users/xieziling/Desktop/personal_proj/SupportPortal/design.md`.
2. `design.md` is the canonical UI design language and component/style source of truth for this repo.
3. `docs/agent.md` is a legacy compatibility redirect for old UI-spec links. It is not an agent-instruction file and must not be treated as an independent spec.
4. If a UI change needs new tokens, component rules, or page-level exceptions, update `design.md` before implementing the code.

## Container Handling After Changes
1. Restart containers after completing changes only when a restart is required for the change to take effect. Typical cases include backend or service code that is loaded only at container start, dependency or image changes, startup configuration or environment changes, and compose or deployment changes.
2. A diff limited to `docs/`, `AGENTS.md`, and tests that only validate those files must not rebuild or restart containers. This includes `docs/project/**`, `docs/projectoverview.html`, and `docs/projectoverview-data.js`; verify the workspace files directly instead of requiring the local container's `app_build.ref` to match the documentation commit.
3. For local development, the default single-host restart path is `bash scripts/workflow/restart_single_host_stack.sh --mode local_lightweight --db remote`; it reads only the root `.env`.
4. Use `bash scripts/workflow/restart_single_host_stack.sh` without a mode only for production / EC2 style full builds or when the task explicitly needs local ML dependencies.
5. Before relying on a running single-host environment for validation, run `bash scripts/workflow/inspect_single_host_stack_mode.sh` to confirm the official stack mode and detect any auxiliary stack.
6. If `inspect_single_host_stack_mode.sh` reports an auxiliary stack such as `deploymentlw`, report it and clean it with `bash scripts/workflow/cleanup_single_host_aux_stack.sh` before treating the local environment as the official single-host stack.
7. The official local single-host stack is `deployment`. Auxiliary stacks such as `deploymentlw` are for temporary manual isolation only and are not part of the standard workflow.
8. For stack-relevant tasks, post-merge live stack verification is part of completion, not an optional follow-up. Run it from the root `main` workspace after the task PR has merged and local `main` has been fast-forwarded.
9. Final reports for stack-relevant tasks must state which restart path was used (`lightweight` or `full`), the `/health` `app_build.ref`, and the task-specific live marker result that proves the running local stack is serving the merged build.
10. If post-merge live stack verification fails, do not report the task complete even if the code has already merged. Report the failure and keep working until the running official stack serves the expected version.
11. Single-host restarts must preserve the current healthy stack through compose validation and image build. A failed new-stack startup or health gate must restore the previous API image, while retaining a non-zero command status so rollback is visible to automation and operators.

## SupportPortal Diagnostic Verification
1. Temporarily do not run the local `$supportportal-run-report` skill as a default completion gate for SupportPortal tasks.
2. Even for tasks that optimize latency, timing, queue performance, retrieval latency, generation latency, answer accuracy, grounded-answer quality, routing correctness, review/intake/investigation correctness, lexical retrieval performance, or other run-level performance or answer-chain behavior, use the narrowest task-appropriate tests, logs, traces, or direct checks instead of the default `real_case/real_user_questions.txt` run-report batch.
3. Run `$supportportal-run-report` or `$supportportal-run-report --profile-lexical` only when the user explicitly asks for that report or when a future instruction reinstates it as a required gate.
4. Final task reports should summarize the verification evidence that was actually run; they do not need a run-report summary when the run-report was intentionally skipped under this temporary rule.

## RAG Change Logging
1. Every RAG-related change must be appended to `/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/rag_change_log.md` before the task is considered complete.
2. RAG-related changes include retrieval logic, chunking strategy, ingestion flow, embedding configuration, evaluation logic, vector tables, and any RAG data reset or backfill.
3. Each entry must include the date, summary, reason, affected files or config, data impact, and verification evidence.

## Prompt and Model Change Logging
1. Every prompt-related or model-related change must be appended to `/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/prompt_change_log.md` before the task is considered complete.
2. Prompt-related or model-related changes include system prompts, user prompt builders, few-shot examples, fallback instructions, refusal templates, model names, model providers, reasoning effort, temperature, tooling mode, domain filters, and any other configuration that can change model behavior.
3. Each entry must include the date, area or subsystem, prompt or model version, summary, reason, affected files or config, expected behavior change, and verification evidence.

## Release Notes Maintenance
1. The workspace admin console (`/workspace/admin/`) shows two release-note views. `Versions` is the human view backed by `docs/release_notes.json` and maintained by the agent; `Deployment records` is the machine view read live from the ECS production database (`support_release_notes` table, written automatically by the deploy pipeline at activation) and requires no manual mirror.
2. Versioning rules (baseline 1.0.0 = the Production release running on 2026-09-11, `r20260911-42f2f11`): a new user-visible capability bumps `minor` (e.g., Enablement Archer auto-activation would be 1.1.0); fixes, configuration changes, toggles, and feature removals bump `patch` (e.g., turning off Production engineer Slack outbound would be 1.0.1); a whole-batch promotion of accumulated Preproduction changes to Production or a breaking change bumps `major` (e.g., 2.0.0). The agent decides per these rules, states the chosen version in the production release report, and the user may correct it by amending the data file.
3. Every authorized Production ECS deployment (routine, promotion, or hotfix) appends one entry to the `versions` list in `docs/release_notes.json` (newest first) after deployment verification passes; a docs-only follow-up commit to `main` is the accepted mechanism. Preproduction deployments never bump the version.
4. Entry format (English, Shengwang-style): `version`, `released_at` (ISO date), `release_id` (the deployed ECS release), a one-sentence `summary`, and `sections` as a list of `{title, items}` using categories such as `New features`, `Improvements`, `Fixed`, `Breaking changes`, `Notes`; each item is one sentence. Omit categories that do not apply; small releases may carry a single category.
5. Deployment-record mirroring is forbidden in the data file: never copy `deployments` into `docs/release_notes.json`; the endpoint composes the machine view from the live database at request time.

## Project Progress Registry Maintenance
1. `docs/project/phases/*.json`, `docs/project/modules/*.json`, `docs/project/functions/*.json`, and `docs/project/tasks/*.json` are the canonical state for project progress. Board, Function, Meeting, activity, and report sections in Project Overview are generated views; `docs/projectoverview-data.js` must not be edited by hand.
2. Any `功能类/修复类` change that changes runtime behavior, a user-visible flow, an API contract, a data model, configuration, or a business result must use one Task under an owning Function. Find the existing Function and `task_id` first; create a Function only for a separately reportable capability, then update the Task's `status`, `next_action`, and `evidence` throughout the work.
3. After changing a Phase, Module, Function, Task, Meeting, PR summary, or another Project Overview source record, run `python3 scripts/generate_project_overview.py --write` and then `python3 scripts/generate_project_overview.py --check` before committing. Function status is derived from child Tasks; Phase moves require a new `pN-xx` ID and a migration alias. If the change is ambiguous, stop and ask before implementation.
4. Pure documentation, tests, instructions/rules, comments, refactors, developer-only scripts, and operations-only changes are exempt unless they also change tracked progress or runtime/user-visible behavior.

## Feature List Maintenance
1. `/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/feature_list.md` is the canonical feature list for major product capabilities in this repository.
2. Any task that adds, completes, or materially changes a major feature must update `/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/feature_list.md` and the corresponding `/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/project/tasks/<task-id>.json` in the same task before the task is considered complete.
3. Record only major features. Do not record UI tweaks, style changes, copy changes, ticket-state tweaks, other small logic adjustments, pure bug fixes, tests, refactors, scripts, or operations-only changes.
4. Keep each feature entry to one short sentence. Do not include reasons, implementation details, file paths, verification notes, or `same as above`.
5. Keep the fixed category order `Client 端`, `Engineer 端`, `Ticket Dashboard`, `RAG Dashboard`, `RAG`, and keep both `已完成` and `未完成` under every category.
6. When one major feature spans multiple categories, record it in every relevant category using the same wording.
7. When a feature is completed, move it from the relevant `未完成` lists to the matching `已完成` lists in the same task. Do not leave the same feature in both states within one category.
8. Any task that changes `/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/feature_list.md` must pass `python3 scripts/verify_feature_list.py` and `python3 scripts/generate_project_overview.py --check`; direct-to-main finalization runs both validations automatically when the feature list or Project Overview registry paths change. `docs/roadmap.html` remains a historical snapshot and is not a progress-state source.
