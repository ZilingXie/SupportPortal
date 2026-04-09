# Collaboration Rules

## Branch Workflow
1. Keep the root workspace on a clean `main` checkout at all times. Use it only for browsing, syncing `main`, creating or inspecting worktrees, and fast-forwarding local `main` after task PRs merge.
2. `main` is the only authoritative long-lived branch for normal development. Do not create or use any local or remote `mac` branch, `mac-integration` worktree, or any other temporary integration branch for routine development or release work.
3. If a local or remote `mac` branch, a `mac-integration` worktree, or a `mac -> main` PR appears, treat it as a workflow violation. Stop, report it, and remove or drain it before continuing. Do not use it as a fallback path.
4. In this file, "thread" means the Codex conversation thread, not merely a shell session.
5. Every repo-tracked change, including `AGENTS.md`, must use the same direct-to-`main` task workflow unless the user explicitly says otherwise.
6. Before relying on this workflow, verify or apply the GitHub repo policy with `scripts/workflow/bootstrap_main_repo_policy.sh`. `main` must be PR-only, squash-only, auto-merge enabled, delete-branch-on-merge enabled, and protected against force pushes.
7. `origin/main` is the source of truth for branch freshness. Local `main` is only the synced landing branch used to create task branches after it has been fast-forwarded to match the latest `origin/main`.
8. When a thread first needs editing any repo-tracked file, Codex must keep the root workspace on clean `main`, fetch and sync from `origin/main`, fast-forward local `main` to match `origin/main`, and only then create one dedicated `codex/<thread-name>` branch and worktree from that updated local `main` through `scripts/workflow/create_task_worktree.sh <thread-name-or-slug>`. Do not run `git switch -c codex/...`, `git checkout -b codex/...`, or equivalent direct branch-creation commands in the root workspace.
9. Do not create a task branch from stale local state. If the root `main` checkout is dirty, ahead of `origin/main`, diverged from `origin/main`, or has not yet been synced to the latest `origin/main`, Codex must stop instead of branching.
10. By default, the first development branch for a thread should be named from the thread name itself, normalized into a short git-safe slug under the `codex/` prefix. For example, a thread named `engineer-opt` should use `codex/engineer-opt`.
11. Once a thread's first development branch is created, keep that branch name stable for the life of the thread before finalization, even if the thread title later changes. If the default thread-derived branch name is already taken, append a short disambiguating suffix and report the exact chosen branch name and worktree path to the user.
12. Before `验收通过`, the same thread must keep reusing that same active `codex/<thread-name>` branch and the same dedicated worktree for later repo-tracked edits. Each thread has at most one active development branch before finalization begins. If a thread is paused and later resumed without finalization, continue on that same bound branch/worktree by default.
13. A thread must not silently hop to another existing `codex/*` branch, another task's dirty worktree, or any reused old task branch. Another thread must not borrow that branch/worktree unless the user explicitly authorizes reuse of the exact branch name.
14. Before a thread's first repo-tracked edit, before resuming a paused thread, before finalization, and before cleanup, Codex must run `git status --short --branch`, `git branch -vv`, and `git worktree list --porcelain`. Before proceeding, Codex must report the thread's current branch name, bound worktree path, whether the worktree is clean, and whether the thread is active, paused-not-finalized, or finalizing-to-main.
15. On every later repo-tracked edit in the same thread, Codex must first confirm it is still on the thread's bound branch/worktree. If the current worktree is a different branch, a reused old branch, a different dirty task worktree, a detached HEAD, or a branch already occupied by another worktree, Codex must stop and explain the issue before continuing.
16. If the root workspace is found on any `codex/*` branch, treat that as a workflow violation. Stop, report the exact branch and root-workspace path, and run `scripts/workflow/rehome_task_worktree.sh <branch-name>` or get explicit user direction before continuing. Do not keep developing on that root workspace checkout.
17. If the thread clearly shifts to a different feature before `验收通过`, Codex must stop and ask the user whether to keep using the current branch or create a new one. It must not make that decision automatically.
18. Detached HEAD may be used only transiently while inspecting history. It must not be the long-lived state of an active coding task, a task waiting to merge into `main`, or the place where finalization or cleanup is performed.
19. Paths under Codex-managed worktrees (for example `~/.config/superpowers/worktrees/...` or `~/.codex/worktrees/...`) identify local worktree locations only. They are not mergeable artifacts and must not be used as a proxy for branch state. Session ending does not delete, free, or finalize a branch or worktree automatically.
20. If a thread stops before finalization, Codex must explicitly report it as `paused, not finalized`, together with the exact branch name, worktree path, and whether the worktree is clean or dirty. Do not describe that thread as complete or safe to delete.
21. If the current workspace has uncommitted changes unrelated to the active task, or if it is ambiguous whether changes belong in the final result, Codex must stop and ask the user before proceeding with branch creation, branch switching, testing, PR creation, merging into `main`, or deleting a task branch or task worktree. Do not stash, overwrite, or force checkout on its own unless the documented recovery script explicitly requires it.
22. Temporary or tool-generated artifacts that should not be versioned (for example `.superpowers/` or `.DS_Store`) must be ignored, cleaned, or otherwise excluded from commits unless the task explicitly requires them.
23. If the task needs container verification or restart inside a worktree, ensure the worktree has access to the repository `.env` before running compose commands. Use `scripts/workflow/link_worktree_env.sh <worktree-path>` or equivalent manual linking.
24. Before any merge step, run a fresh round of verification that matches the current task. For tasks with repo-tracked edits, that verification must pass on the expected `codex/<thread-name>` branch after any tracked task changes are committed. If verification fails, or if the working tree/index state remains unsafe or ambiguous, Codex must stop and ask the user before proceeding.
25. If the user explicitly approves the currently verified task branch/worktree for finalization (for example by saying "验收通过" or using an equivalent approval), Codex must treat that as authorization to run `scripts/workflow/finalize_task_to_main.sh` for that thread. Codex should not stop to ask about push, PR creation, or merge unless verification fails, a merge conflict occurs, the finalization lock times out, or GitHub returns a blocking error.
26. The direct-to-`main` finalization flow is: confirm the current task worktree is on the expected named branch and not detached HEAD; ensure the root workspace is still clean `main`; auto-commit any remaining tracked task changes; fetch `origin`; merge latest `origin/main` into the task branch; rerun fresh verification; push the task branch; create or reuse the task PR to `main`; squash-merge it with auto-merge enabled; wait for GitHub to confirm the merge; fast-forward local root `main`; then immediately remove the task worktree and delete the local task branch.
27. Only one accepted task may promote to `main` at a time. Use the shared finalization lock inside `scripts/workflow/finalize_task_to_main.sh`. Later accepted tasks must wait for the lock, refresh against the newest `origin/main`, and then continue.
28. There is no supported `mac` workflow in this repository. Do not recreate `mac`, `mac-integration`, or any `mac -> main` release path.

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

## SupportPortal Diagnostic Verification
1. If a task optimizes SupportPortal latency, timing, queue performance, retrieval latency, generation latency, or other end-to-end performance behavior, run the local `$supportportal-route-timing-report` skill against the repo `real_case/real_user_questions.txt` before calling the task complete.
2. If a task optimizes SupportPortal answer accuracy, grounded-answer quality, routing correctness, review/intake/investigation correctness, or other answer-chain behavior, run the local `$supportportal-answer-chain-report` skill against the repo `real_case/real_user_questions.txt` before calling the task complete.
3. If a task affects both timing/performance and answer quality/answer-chain behavior, run both skills.
4. Final task reports must summarize the key conclusions from those skill runs, not merely state that the skills were executed.

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
