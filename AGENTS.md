# Collaboration Rules

## Branch Workflow
1. Treat `main` as the source branch for new thread-bound task branches and `mac` as the integration branch. Investigation that does not modify repo-tracked files may be performed directly on `mac`.
2. Changes limited to the repository-root `AGENTS.md` file may be made directly on `mac` without creating a `codex/<task>` branch.
3. In this file, "thread" means the Codex conversation thread, not merely a shell session.
4. When a thread first needs editing any repo-tracked file other than `AGENTS.md`, Codex must first update the local `main` branch to the latest remote state, then create a new `codex/<task>` branch from that updated `main` branch, bind it to one dedicated Codex-managed git worktree, and declare that branch/worktree as the active development location for that thread before making repo-tracked edits.
5. By default, the first development branch for a thread should be named from the thread name itself, normalized into a short git-safe slug under the `codex/` prefix. For example, a thread named `engineer-opt` should use `codex/engineer-opt`.
6. Once a thread's first development branch is created, keep that branch name stable for the life of the thread before finalization, even if the thread title later changes. If the default thread-derived branch name is already taken, append a short disambiguating suffix and report the exact chosen branch name to the user.
7. Before `验收通过`, the same thread must keep reusing that same active `codex/<task>` branch and the same dedicated worktree for later repo-tracked edits. Each thread has at most one active development branch before finalization begins. If a thread is paused and later resumed without finalization, continue on that same bound branch/worktree by default.
8. A thread must not silently hop to another existing `codex/*` branch, another task's dirty worktree, or any reused old task branch. Another thread must not borrow that branch/worktree unless the user explicitly authorizes reuse of the exact branch name.
9. On every later repo-tracked edit in the same thread, Codex must first confirm it is still on the thread's bound branch/worktree. If the current worktree is a different branch, a reused old branch, or a different dirty task worktree, Codex must stop and ask the user before continuing.
10. If the thread clearly shifts to a different feature before `验收通过`, Codex must stop and ask the user whether to keep using the current branch or create a new one. It must not make that decision automatically.
11. Detached HEAD may be used only transiently while inspecting history. It must not be the long-lived state of an active coding task or the place where finalization is performed.
12. The root workspace, or another explicitly designated clean `mac` worktree, is reserved for `mac` integration and release work. Do not use that `mac` workspace for task development, dirty task verification, or temporary task commits.
13. Paths under Codex-managed worktrees (for example `~/.config/superpowers/worktrees/...` or `~/.codex/worktrees/...`) identify local worktree locations only. They are not mergeable artifacts and must not be used as a proxy for branch state.
14. If the current workspace has uncommitted changes unrelated to the active task, or if it is ambiguous whether changes belong in the final result, Codex must stop and ask the user before proceeding with branch creation, branch switching, testing, merging changes into `mac`, or deleting the local `codex/<task>` branch. Do not stash, overwrite, or force checkout on its own.
15. Before merging any task branch into `mac`, switch to a clean `mac` worktree and sync `main` into `mac`. Use `scripts/workflow/sync_mac_from_main.sh` or equivalent manual steps.
16. If the user explicitly approves the currently verified task branch/worktree for finalization (for example by saying "验收通过" or using an equivalent approval), Codex must treat that as authorization to start the branch finalization flow for that thread. Once finalization begins, that branch stops being the active development branch for the thread; any later repo-tracked development requires a new branch unless the user explicitly says otherwise.
17. If finalization begins and the active `codex/<task>` branch still contains uncommitted task changes, Codex should stage and commit those task changes on the active task branch before switching away from it. Unknown untracked files or unrelated edits must stop the flow and be resolved with the user first.
18. Temporary or tool-generated artifacts that should not be versioned (for example `.superpowers/` or `.DS_Store`) must be ignored, cleaned, or otherwise excluded from the finalization commit unless the task explicitly requires them.
19. If the task needs container verification or restart inside a worktree, ensure the worktree has access to the repository `.env` before running compose commands. Use `scripts/workflow/link_worktree_env.sh <worktree-path>` or equivalent manual linking.
20. Before any merge or release step, run a fresh round of verification that matches the current task. For tasks with repo-tracked edits, that verification must pass on the expected `codex/<task>` branch after the task changes are committed. If verification fails, or if the working tree/index state remains unsafe or ambiguous, Codex must stop and ask the user before proceeding.
21. The finalization flow for a task branch is: confirm the current thread-bound task worktree is on the expected named branch and not detached HEAD; ensure task changes are committed and non-task artifacts are excluded; run fresh verification on `codex/<task>`; move to a clean `mac` worktree; sync `main` into `mac`; merge the task branch into `mac`; confirm the merged result is safely present on `mac`; push the current `mac` branch to `origin/mac`; rerun release-ready checks; create a fresh `mac -> main` PR with `base=main` and `head=mac`; report the PR link to the user; then delete the local task branch.
22. Every time `mac` is promoted to `main`, first sync `main` into `mac` again, then run release-ready checks from a clean `mac` worktree. Use `scripts/workflow/check_release_ready.sh`, `git push origin mac`, and `gh pr create --base main --head mac` or equivalent manual steps. Do not reuse an older PR or stale compare view. If pushing `mac` or creating the PR fails, or if an existing open `mac -> main` PR blocks creation, stop and ask the user instead of skipping the release step or auto-closing the old PR.

## UI Design Source Of Truth
1. All new or refactored UI under `ui/` must follow `/Users/xieziling/Desktop/personal_proj/SupportPortal/design.md`.
2. `design.md` is the canonical UI design language and component/style source of truth for this repo.
3. `docs/agent.md` is a legacy compatibility redirect for old UI-spec links. It is not an agent-instruction file and must not be treated as an independent spec.
4. If a UI change needs new tokens, component rules, or page-level exceptions, update `design.md` before implementing the code.

## Container Handling After Changes
1. Restart containers after completing changes only when a restart is required for the change to take effect. Typical cases include backend or service code that is loaded only at container start, dependency or image changes, startup configuration or environment changes, and compose or deployment changes.
2. Do not restart containers for changes that do not require it, such as documentation-only updates or other edits that do not affect the running containers.
3. When a restart is required, restart containers with compose down first, then compose up:
   - `podman-compose -f deployment/docker-compose.single-host.yml down`
   - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
4. After any restart, verify running status:
   - `podman-compose -f deployment/docker-compose.single-host.yml ps`

## RAG Change Logging
1. Every RAG-related change must be appended to `/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/rag_change_log.md` before the task is considered complete.
2. RAG-related changes include retrieval logic, chunking strategy, ingestion flow, embedding configuration, evaluation logic, vector tables, and any RAG data reset or backfill.
3. Each entry must include the date, summary, reason, affected files or config, data impact, and verification evidence.
