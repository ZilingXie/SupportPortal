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
4. CodeGraph status is diagnostic only. Use CodeGraph for code understanding, symbol lookup, call flow, impact analysis, and code-change context, but check `codegraph_status` only when CodeGraph fails, appears unavailable/stale, or the task is to diagnose indexing.
5. Git/worktree state is safety-gated. Run `git status --short --branch`, `git branch -vv`, and `git worktree list --porcelain` before repo-tracked edits, resuming a paused task, finalization, cleanup, or any workspace-safety decision; do not run them for ordinary chat, pure planning, or read-only documentation inspection.
6. Native `rg` is preferred for docs, literal text, comments, config keys, logs, and rule-file searches. CodeGraph is not required for those non-structural lookups.

## Control CC
For delegated code work, prefer the project-local `control-cc` skill when the user requests coordinated Claude Code execution with Codex/Claude review, or when implementation can be safely delegated.

`control-cc` may create temporary detached candidate worktrees under `/tmp/control-cc-runs/...` from the active task branch for isolated Claude Code execution. Candidate worktrees are not task branches: do not push, finalize, merge, or treat them as authoritative. Export reviewed patches from candidates, integrate them sequentially into the real project-local `codex/<thread>` task workspace, then clean the candidates. These candidate-run limits do not apply to a normal Claude Code session following `CLAUDE.md`.

## Claude Code Agent Parity
1. Claude Code follows `CLAUDE.md`, which mirrors the hot-path rules in `AGENTS.md`.
2. Claude Code is not a default handoff-only worker. When assigned a task, Claude Code may plan, review, modify code/docs, run verification, commit, create or reuse PRs, merge/finalize through the repository workflow, run required post-merge checks, and clean the current task workspace/branch.
3. Claude Code must still follow the same branch/workspace, verification, logging, CodeGraph, and direct-to-`main` rules as Codex.
4. If a user explicitly asks Claude Code to act only as an implementation worker, obey that narrower boundary for that task and hand off as requested.

## Reasonix Handoff Boundary
Reasonix-specific worker rules live in `REASONIX.md`. When the user gives a Codex- or Claude-written plan to Reasonix, Reasonix is an implementation worker and should stop before commit with a branch/worktree handoff for Codex or Claude Code review. This does not change Codex's or Claude Code's role: either may plan, implement, review, finalize, or merge according to the user's direct request and the rest of `AGENTS.md` / `CLAUDE.md`.

## Codex / Claude Code Review Finalization
1. When the user asks Codex or Claude Code to review an implementation handoff, the reviewing agent must treat the request as ownership transfer for that task unless the user explicitly says review-only.
2. Review means inspect the diff and verification evidence, make any necessary corrections directly, run the narrowest task-appropriate verification, then continue through commit, PR creation or reuse, squash merge to `main`, CodeGraph sync, required live stack verification, and cleanup under the normal direct-to-`main` workflow.
3. Codex and Claude Code must not stop after listing findings when the user expects completion. If findings are fixable in the current branch/worktree, fix them; only stop for user input when the issue is ambiguous, unsafe to decide, or blocked by missing external state.
4. If the handoff worktree contains untracked or unstaged task files, the reviewing agent must stage and include them after review instead of treating the handoff as complete.
5. Reasonix and any other handoff-only workers still must not commit, push, create PRs, merge, finalize, or clean task worktrees unless the user explicitly changes that boundary.

## CodeGraph First For Code Context
1. For any task that requires understanding, locating, tracing, or changing code, prefer the project CodeGraph tools before native file search or broad file reads. Use CodeGraph for structural questions such as where a symbol is defined, who calls it, what it calls, how data flows between symbols, what would be affected by a change, or which files and symbols are relevant to a task.
2. Use native search such as `rg` primarily for literal text, comments, log messages, configuration keys, documentation wording, or after CodeGraph has already identified the specific files that need direct inspection.
3. If CodeGraph is unavailable, uninitialized, or stale, report that explicitly and fall back to the narrowest native search needed. Check `codegraph_status` only as a diagnostic when failure/staleness is suspected. Do not run `codegraph init -i` unless the project has not been initialized; for an initialized project, use `codegraph sync` to refresh changed files.

## Branch Workflow
1. Keep the root workspace at `/Users/xieziling/Desktop/personal_proj/SupportPortal` on a clean `main` checkout at all times. Use it only for browsing, syncing `main`, creating or inspecting task workspaces, fast-forwarding local `main` after task PRs merge, CodeGraph sync, and post-merge live stack verification.
2. Ordinary repo-tracked edits must not be made in the root workspace. When a thread needs to edit repo-tracked files, create or reuse that thread's dedicated `codex/<thread-name>` branch in a temporary project-local task workspace under `/Users/xieziling/Desktop/personal_proj/SupportPortal/.worktrees/<thread-slug>`.
3. `.worktrees/` must be ignored by Git before any project-local task workspace is created. Do not use `~/.config/superpowers/worktrees/...` or `~/.codex/worktrees/...` as the default location for new SupportPortal task workspaces.
4. `main` is the only authoritative long-lived branch for normal development. Do not create or use any local or remote `mac` branch, `mac-integration` worktree, or any other temporary integration branch for routine development or release work.
5. If a local or remote `mac` branch, a `mac-integration` worktree, or a `mac -> main` PR appears, treat it as a workflow violation. Stop, report it, and remove or drain it before continuing. Do not use it as a fallback path.
6. In this file, "thread" means the active agent conversation thread, not merely a shell session.
7. Every repo-tracked change, including `AGENTS.md`, must use the same direct-to-`main` task workflow unless the user explicitly says otherwise.
8. Before relying on this workflow, verify or apply the GitHub repo policy with `scripts/workflow/bootstrap_main_repo_policy.sh`. `main` must be PR-only, squash-only, auto-merge enabled, delete-branch-on-merge enabled, and protected against force pushes.
9. `origin/main` is the source of truth for branch freshness. Local `main` is only the synced landing branch used to create task branches after it has been fast-forwarded to match the latest `origin/main`.
10. When a thread first needs editing any repo-tracked file, the active Codex or Claude Code agent must keep the root workspace on clean `main`, fetch and sync from `origin/main`, fast-forward local `main` to match `origin/main`, and only then create one dedicated `codex/<thread-name>` branch with a project-local task workspace through `scripts/workflow/create_task_worktree.sh <thread-name-or-slug>`. Do not run `git switch -c codex/...`, `git checkout -b codex/...`, or equivalent direct branch-creation commands in the root workspace.
11. Do not create a task branch from stale local state. If the root `main` checkout is dirty, ahead of `origin/main`, diverged from `origin/main`, or has not yet been synced to the latest `origin/main`, the active agent must stop instead of branching.
12. By default, the first development branch for a thread should be named from the thread name itself, normalized into a short git-safe slug under the `codex/` prefix. For example, a thread named `engineer-opt` should use `codex/engineer-opt` and a workspace like `.worktrees/engineer-opt`.
13. Once a thread's first development branch is created, keep that branch name stable for the life of the thread before finalization, even if the thread title later changes. If the default thread-derived branch name is already taken, append a short disambiguating suffix and report the exact chosen branch name and task workspace path to the user.
14. Before finalization, the same thread must keep reusing that same active `codex/<thread-name>` branch and the same project-local task workspace for later repo-tracked edits. Each thread has at most one active development branch before finalization begins. If a thread is paused and later resumed without finalization, continue on that same branch/workspace by default.
15. Classify every task before deciding verification depth and whether post-merge live stack verification is required. `文档类` means docs, instructions, rules, comments, or other non-runtime descriptions only. `修复类` means restoring expected behavior, fixing bugs, workflow/script/config mistakes, wording mistakes, or incorrect logic without adding a major new capability. `功能类/重大行为变更` means new capabilities, expanded scope, clearly changed user flows, or other material product behavior changes.
16. Any repo-tracked task that needs a new workspace still follows the same `codex/<thread-name>` project-local task workspace workflow. Task classification changes only the verification depth and live stack verification requirements, not branch creation, workspace creation, or the direct-to-`main` merge path.
17. A thread must not silently hop to another existing `codex/*` branch, another task's dirty workspace, or any reused old task branch. Another thread must not borrow that branch/workspace unless the user explicitly authorizes reuse of the exact branch name.
18. The mere presence of other `codex/*` branches or `.worktrees/...` task workspaces, including dirty, paused, or unrelated workspaces, is not a blocker. Treat them as background state only; do not pause, clean, inspect deeply, or ask for permission because of them unless they occupy the current thread's exact branch or workspace path.
19. Before a thread's first repo-tracked edit, before resuming a paused thread, before finalization, and before cleanup, the active agent must run `git status --short --branch`, `git branch -vv`, and `git worktree list --porcelain`. Before proceeding, the active agent must report the current thread's branch name, task workspace path, whether that workspace is clean, and whether the thread is active, paused-not-finalized, or finalizing-to-main. Other listed workspaces may be mentioned for awareness, but they must not block progress when root `main` is clean and the current thread workspace is valid.
20. On every later repo-tracked edit in the same thread, the active agent must first confirm it is still on the thread's branch/workspace. If the current workspace is a different branch, a reused old branch, another task's workspace, a detached HEAD, or a branch already occupied by another workspace, the active agent must stop and explain the issue before continuing. Do not stop just because unrelated task workspaces exist elsewhere.
21. If the root workspace is found on any `codex/*` branch, treat that as a workflow violation. Stop, report the exact branch and root-workspace path, and run `scripts/workflow/rehome_task_worktree.sh <branch-name>` or get explicit user direction before continuing. Do not keep developing on that root workspace checkout.
22. If the thread clearly shifts to a different feature before finalization, the active agent must stop and ask the user whether to keep using the current branch or create a new one. It must not make that decision automatically.
23. Detached HEAD may be used only transiently while inspecting history. It must not be the long-lived state of an active coding task, a task waiting to merge into `main`, or the place where finalization or cleanup is performed.
24. Paths under `.worktrees/...` identify local task workspace locations only. They are not mergeable artifacts and must not be used as a proxy for branch state. Session ending does not delete, free, or finalize a branch or workspace automatically.
25. If a thread stops before finalization, the active agent must explicitly report it as `paused, not finalized`, together with the exact branch name, workspace path, and whether the workspace is clean or dirty. Do not describe that thread as complete or safe to delete.
26. If the current workspace has uncommitted changes unrelated to the active task, or if it is ambiguous whether changes belong in the final result, the active agent must stop and ask the user before proceeding with branch creation, branch switching, testing, PR creation, merging into `main`, or deleting a task branch or task workspace. Do not stash, overwrite, or force checkout on its own unless the documented recovery script explicitly requires it.
27. Temporary or tool-generated artifacts that should not be versioned (for example `.worktrees/`, `.superpowers/`, or `.DS_Store`) must be ignored, cleaned, or otherwise excluded from commits unless the task explicitly requires them.
28. If the task needs container verification or restart inside a task workspace, ensure the workspace has access to the repository `.env` before running compose commands. Use `scripts/workflow/link_worktree_env.sh <worktree-path>` or equivalent manual linking.
29. Before any merge step, run a fresh round of verification that matches the current task. That verification must pass on the expected `codex/<thread-name>` branch after any tracked task changes are committed. Every task must use targeted verification that directly proves the current change is correct, using the narrowest sufficient command. Documentation tasks should default to text-level checks such as format checks and explicit confirmation that new wording exists and superseded wording is gone. Workflow or script fixes should default to the relevant workflow tests, shell syntax checks, or directly related unit tests. Feature or major behavior changes should default to the smallest task-appropriate tests or checks that directly exercise the changed behavior. Do not substitute unrelated generic checks for this targeted verification.
30. Once fresh targeted verification passes on the expected branch/workspace, the active agent must report the exact verification command or commands it ran and the task classification it used, then proceed directly to `scripts/workflow/finalize_task_to_main.sh` without waiting for extra user confirmation. Optional user messages such as `验收通过` may confirm the same state, but they are not required for finalization.
31. If task classification is ambiguous, the active agent must stop and ask the user before choosing verification depth or live stack verification scope. If a task began as one classification but the final diff has evolved into another, the active agent must reclassify it, rerun the appropriate verification, and then continue on the same automatic finalization path once that updated verification passes.
32. A task is stack-relevant if it touches `ui/`, `backend/`, `deployment/`, single-host workflow or compose or restart scripts, runtime configuration, or anything else that can change the behavior of the running official local single-host stack. For stack-relevant tasks, successful merge alone is not enough to call the task complete.
33. For every stack-relevant task, after `scripts/workflow/finalize_task_to_main.sh` merges the task and fast-forwards local root `main`, the active agent must run post-merge live stack verification from the root workspace. First run `bash scripts/workflow/inspect_single_host_stack_mode.sh` to confirm the official stack is `deployment` and detect any auxiliary stack. If an auxiliary stack such as `deploymentlw` is present, report it and clean it with `bash scripts/workflow/cleanup_single_host_aux_stack.sh` before relying on the local environment.
34. For stack-relevant tasks, the default restart path is `bash scripts/workflow/restart_single_host_lightweight_stack.sh`. Use `bash scripts/workflow/restart_single_host_stack.sh` only when the task explicitly needs local ML or full-image capabilities. After the restart, verify that `/health` succeeds, confirm `app_build.ref` matches the merged `main` commit, and, for frontend or page tasks, verify at least one task-specific live marker such as a page title, asset-version query string, unique DOM copy, or unique JS or CSS marker. If this live stack verification fails, the task is not complete even if the PR has already merged.
35. The direct-to-`main` finalization flow is: confirm the current task workspace is on the expected named branch and not detached HEAD; ensure the root workspace is still clean `main`; auto-commit any remaining tracked task changes; fetch `origin`; merge latest `origin/main` into the task branch; rerun fresh verification; push the task branch; create or reuse the task PR to `main`; squash-merge it with auto-merge enabled; wait for GitHub to confirm the merge; fast-forward local root `main`; run `codegraph sync` from the root `main` workspace to incrementally update the local CodeGraph index; run any required post-merge live stack verification for stack-relevant tasks; then immediately remove the task workspace and delete the local task branch.
36. Only one verified task may promote to `main` at a time. Use the shared finalization lock inside `scripts/workflow/finalize_task_to_main.sh`. Later verified tasks must wait for the lock, refresh against the newest `origin/main`, and then continue.
37. There is no supported `mac` workflow in this repository. Do not recreate `mac`, `mac-integration`, or any `mac -> main` release path.

## UI Design Source Of Truth
1. All new or refactored UI under `ui/` must follow `/Users/xieziling/Desktop/personal_proj/SupportPortal/design.md`.
2. `design.md` is the canonical UI design language and component/style source of truth for this repo.
3. `docs/agent.md` is a legacy compatibility redirect for old UI-spec links. It is not an agent-instruction file and must not be treated as an independent spec.
4. If a UI change needs new tokens, component rules, or page-level exceptions, update `design.md` before implementing the code.

## Container Handling After Changes
1. Restart containers after completing changes only when a restart is required for the change to take effect. Typical cases include backend or service code that is loaded only at container start, dependency or image changes, startup configuration or environment changes, and compose or deployment changes.
2. Do not restart containers for changes that do not require it, such as documentation-only updates or other edits that do not affect the running containers.
3. For local development, the default single-host restart path is `bash scripts/workflow/restart_single_host_lightweight_stack.sh`.
4. Use `bash scripts/workflow/restart_single_host_stack.sh` only for production / EC2 style full builds or when the task explicitly needs local ML dependencies.
5. Before relying on a running single-host environment for validation, run `bash scripts/workflow/inspect_single_host_stack_mode.sh` to confirm the official stack mode and detect any auxiliary stack.
6. If `inspect_single_host_stack_mode.sh` reports an auxiliary stack such as `deploymentlw`, report it and clean it with `bash scripts/workflow/cleanup_single_host_aux_stack.sh` before treating the local environment as the official single-host stack.
7. The official local single-host stack is `deployment`. Auxiliary stacks such as `deploymentlw` are for temporary manual isolation only and are not part of the standard workflow.
8. For stack-relevant tasks, post-merge live stack verification is part of completion, not an optional follow-up. Run it from the root `main` workspace after the task PR has merged and local `main` has been fast-forwarded.
9. Final reports for stack-relevant tasks must state which restart path was used (`lightweight` or `full`), the `/health` `app_build.ref`, and the task-specific live marker result that proves the running local stack is serving the merged build.
10. If post-merge live stack verification fails, do not report the task complete even if the code has already merged. Report the failure and keep working until the running official stack serves the expected version.

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

## Feature List Maintenance
1. `/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/feature_list.md` is the canonical feature list for major product capabilities in this repository.
2. Any task that adds, completes, or materially changes a major feature must update `/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/feature_list.md` in the same task before the task is considered complete.
3. Record only major features. Do not record UI tweaks, style changes, copy changes, ticket-state tweaks, other small logic adjustments, pure bug fixes, tests, refactors, scripts, or operations-only changes.
4. Keep each feature entry to one short sentence. Do not include reasons, implementation details, file paths, verification notes, or `same as above`.
5. Keep the fixed category order `Client 端`, `Engineer 端`, `Ticket Dashboard`, `RAG Dashboard`, `RAG`, and keep both `已完成` and `未完成` under every category.
6. When one major feature spans multiple categories, record it in every relevant category using the same wording.
7. When a feature is completed, move it from the relevant `未完成` lists to the matching `已完成` lists in the same task. Do not leave the same feature in both states within one category.
8. Any task that changes `/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/feature_list.md` must pass `python3 scripts/verify_feature_list.py`; direct-to-main finalization should run this verification automatically when that file changes.
