# Collaboration Rules

## Branch Workflow
1. Treat `main` as the source branch for new task branches and `mac` as the integration branch. Investigation that does not modify repo-tracked files may be performed directly on `mac`.
2. Changes limited to the repository-root `AGENTS.md` file may be made directly on `mac` without creating a `codex/<task>` branch.
3. Once a task requires editing any other repo-tracked files, Codex must first update the local `main` branch to the latest remote state, then create a new `codex/<task>` branch from that updated `main` branch before making those repo-tracked edits. Do not reuse old task branches unless the user explicitly asks for that.
4. After a `codex/<task>` branch is created, all repo-tracked edits, checks, and task verification for that task must happen on that `codex/<task>` branch until finalization begins.
5. If the current workspace has uncommitted changes that could affect updating `main`, branch creation, branch switching, testing, merging changes into `mac`, or deleting the local `codex/<task>` branch, Codex must stop and ask the user before proceeding. Do not stash, overwrite, or force checkout on its own.
6. If the user explicitly approves the task branch for finalization (for example by saying "验收通过" or using an equivalent approval), Codex must treat that as authorization to start the branch finalization flow.
7. Before any merge or finalization step, run a fresh round of verification that matches the current task. For tasks with repo-tracked edits, that verification must pass on the expected `codex/<task>` branch before switching away from it. If verification fails, or if the working tree/index state makes finalization unsafe or ambiguous, Codex must stop and ask the user before proceeding.
8. The finalization flow for a task branch is: run fresh verification on `codex/<task>`; switch to `mac`; merge the task branch changes into `mac`; confirm the merged result is safely present on `mac`; then delete the local `codex/<task>` branch.

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
