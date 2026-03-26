# Collaboration Rules

## Branch Workflow
1. Treat `mac` as the base and integration branch. Investigation that does not modify repo-tracked files may be performed directly on `mac`.
2. Once a task requires editing repo-tracked files, Codex must create a new `codex/<task>` branch from the latest `mac` branch state before making those edits. Do not reuse old task branches unless the user explicitly asks for that.
3. After a `codex/<task>` branch is created, all repo-tracked edits, checks, and task verification for that task must happen on that `codex/<task>` branch until finalization begins.
4. If the current workspace has uncommitted changes that could affect investigation, branch creation, branch switching, testing, squashing changes onto `mac`, or recreating the final change on `main`, Codex must stop and ask the user before proceeding. Do not stash, overwrite, or force checkout on its own.
5. If the user explicitly approves the task branch for finalization (for example by saying "验收通过" or using an equivalent approval), Codex must treat that as authorization to start the branch finalization flow.
6. Before any commit or finalization transfer, run a fresh round of verification that matches the current task. For tasks with repo-tracked edits, that verification must pass on the expected `codex/<task>` branch before switching away from it. If verification fails, or if the working tree/index state makes finalization unsafe or ambiguous, Codex must stop and ask the user before proceeding.
7. The finalization flow for a task branch is: run fresh verification on `codex/<task>`; switch to `mac`; transfer the task-branch changes onto `mac` as a squash result; delete the local `codex/<task>` branch only after the squash result is safely present on `mac`; create the final authoritative commit on `mac`; switch to `main`; apply the same diff from the `mac` commit without reusing the `mac` commit object; then create a separate new commit on `main`.
8. The `main` step is not a `mac` to `main` merge and not a direct cherry-pick of the `mac` commit.

## UI Design Source Of Truth
1. All new or refactored UI under `ui/` must follow `/Users/xieziling/Desktop/personal_proj/SupportPortal/design.md`.
2. `design.md` is the canonical UI design language and component/style source of truth for this repo.
3. `docs/agent.md` is a legacy compatibility redirect for old UI-spec links. It is not an agent-instruction file and must not be treated as an independent spec.
4. If a UI change needs new tokens, component rules, or page-level exceptions, update `design.md` before implementing the code.

## Container Handling After Changes
1. After completing code changes, always restart containers with compose down first, then compose up:
   - `podman-compose -f deployment/docker-compose.single-host.yml down`
   - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
2. After restart, verify running status:
   - `podman-compose -f deployment/docker-compose.single-host.yml ps`

## RAG Change Logging
1. Every RAG-related change must be appended to `/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/rag_change_log.md` before the task is considered complete.
2. RAG-related changes include retrieval logic, chunking strategy, ingestion flow, embedding configuration, evaluation logic, vector tables, and any RAG data reset or backfill.
3. Each entry must include the date, summary, reason, affected files or config, data impact, and verification evidence.
