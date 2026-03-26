# Collaboration Rules

## Branch Workflow
1. By default, Codex must not make task changes directly on the `mac` branch. Treat `mac` as the base and integration branch.
2. For each task, create a new `codex/<task>` branch from the latest `mac` branch state. Do not reuse old task branches unless the user explicitly asks for that.
3. Perform all edits, checks, and tests for the task on that `codex/<task>` branch.
4. If the current workspace has uncommitted changes that could affect branch creation, switching, testing, or merging, Codex must stop and ask the user before proceeding. Do not stash, overwrite, or force checkout on its own.
5. If the user explicitly approves the task branch for finalization (for example by saying "验收通过" or using an equivalent approval), Codex must treat that as authorization to start the branch finalization flow.
6. Before any commit or merge, run a fresh round of verification that matches the current task. If verification fails, if Codex is not currently on the expected `codex/<task>` branch, or if the working tree/index state makes finalization unsafe or ambiguous, Codex must stop and ask the user before proceeding.
7. After that verification passes, commit the task-branch changes, merge the resulting commit back into `mac`, and delete the local `codex/<task>` branch only after the merge succeeds.

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
