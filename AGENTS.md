# SupportPortal Agent Rules

## Source Of Truth
1. `AGENTS.md` is the short hot-path rule file. `CLAUDE.md` mirrors it for Claude Code; `REASONIX.md` defines the Reasonix exception.
2. Read `docs/agent_workflow_details.md` only for workflow edge cases, worker handoff, stack verification, Project Overview/feature-list maintenance, RAG/prompt/model changes, or local single-host changes.
3. `docs/agent.md` is a legacy UI redirect. The UI source of truth is `/Users/xieziling/Desktop/personal_proj/SupportPortal/design.md`.
4. `docs/project/phases/*.json`, `docs/project/modules/*.json`, `docs/project/functions/*.json`, and `docs/project/tasks/*.json` are the canonical project-progress registry. `docs/projectoverview-data.js` is a generated view consumed by `docs/projectoverview.html`; `docs/roadmap.html` and its phase/meeting pages are historical references, not the current progress source.
5. Project Overview is the single source for整体落地进度；历史 Roadmap 页面只保留兼容入口。

## Working Rules
1. Do not add fixed startup scans. Use required skills and AgentMemory only when the task triggers them. Prefer CodeGraph for structural code questions and `rg` for docs, literals, config, logs, and rules.
2. Keep `/Users/xieziling/Desktop/personal_proj/SupportPortal` on clean `main`; do not edit tracked files in the root. Before tracked edits, sync `main` with `origin/main` and create a task workspace with `scripts/workflow/create_task_worktree.sh <thread-name-or-slug>`.
3. Work only in the current task's `.worktrees/<thread-slug>` on its `codex/<thread-slug>` branch. Do not borrow another task's workspace or branch. Inspect Git/worktree state before tracked edits, resuming, finalization, cleanup, or other workspace-safety decisions.
4. `main` is PR-only and squash-only. After targeted verification, use `scripts/workflow/finalize_task_to_main.sh`; it owns refresh, push, PR, merge, root fast-forward, CodeGraph sync, and current-task cleanup. A merged PR is an intermediate finalization state, not task completion: report a task complete only after that script succeeds and confirms removal of its workspace and local `codex/*` branch. For code changes, final reports may use only the completion states defined in `docs/agent_workflow_details.md`; a merge or cleanup failure is never evidence for `已完成`. Never create or use `mac` or `mac-integration` workflows.
5. Keep `.worktrees/`, `.superpowers/`, `.DS_Store`, and unrelated changes out of commits. Stop and ask before acting when ownership or workspace state is ambiguous.

## Project Progress Registry
1. For every `功能类/修复类` change that changes runtime behavior, a user-visible flow, an API contract, a data model, configuration, or a business result, find the owning Function and existing `task_id` before implementation; create a Function only for a separately reportable capability, and create a Task under `docs/project/tasks/` when none exists.
2. Maintain the same Task throughout the work: update its `status`, `next_action`, and `evidence`; `done` requires evidence and `blocked` requires the blocker to be recorded. Task IDs use `pN-xx`; a Phase move requires a new ID plus a preserved alias in `docs/project/migration_manifest.json`.
3. After changing a Phase, Module, Function, Task, Meeting, PR summary, or another Project Overview source record, run `python3 scripts/generate_project_overview.py --write`, then run `python3 scripts/generate_project_overview.py --check` before committing. The write command regenerates `docs/projectoverview-data.js`; never edit that generated file by hand. Function status is derived from its child Tasks.
4. Pure documentation, tests, instructions/rules, comments, refactors, developer-only scripts, and operations-only changes do not require a Task or Project Overview regeneration unless they also change tracked progress or runtime/user-visible behavior.
5. If it is unclear whether a change is functional or whether it needs a Task, stop and ask before implementation instead of silently skipping the registry update.

## Implementation Defaults
1. Start with the smallest clear primary path for the current requirement. Do not add speculative abstractions, configuration, retries, caches, migrations, compatibility paths, or defensive checks for hypothetical future needs.
2. Add fallback, retry, compatibility, or recovery behavior only when the task, an existing API/data/deployment contract, or repository/runtime evidence requires it. Preserve existing contract behavior until it is proven obsolete.
3. Fallback is an exceptional degraded state, not silent success. Prefer an explicit error, fail-closed behavior, or human review. Every new fallback needs one trigger, one output/state contract, and an owner-visible signal with the failure reason preserved.
4. Use the narrowest mechanism that provides the required guarantee: transactions, conditional updates, unique constraints, or atomic file operations for atomicity; hashes only for content identity or integrity. Before adding non-trivial complexity, state the concrete failure/contract, visibility signal, and smallest verification. Stop after the requested behavior is narrowly verified.

## Verification And Records
1. First classify the tracked diff as either a `文档改动` or a `代码改动`. A code change may additionally be described as a `修复类` or `功能类/重大行为变更`, but that business classification does not replace the documentation-versus-code execution boundary.
2. A documentation change is limited to `docs/**`, `AGENTS.md`, `CLAUDE.md`, `REASONIX.md`, or tests that only validate those files. It does not require automated tests, container rebuilds, stack restarts, or live-stack verification. Confirm the changed wording and any applicable document-generation or format check directly.
3. A code change requires targeted verification that proves the changed behavior. Runtime-relevant code changes require the post-merge official-stack restart and live verification from root `main`; follow the health/build-marker checks in `docs/agent_workflow_details.md`. Project Overview files remain documentation even when the backend serves them. For mixed diffs, decide the restart requirement from the runtime changes only.
4. Code-change final reports must separate `主要变更` from `验证结果` and use only the three completion states defined in `docs/agent_workflow_details.md`. List verification that actually ran, and list each required check that did not run with its reason.
5. RAG changes update `docs/rag_change_log.md`; prompt/model/tooling behavior changes update `docs/prompt_change_log.md`.
6. Major product capabilities additionally update `docs/feature_list.md` and the corresponding `docs/project/tasks/<task-id>.json`; run `python3 scripts/verify_feature_list.py` and `python3 scripts/generate_project_overview.py --check` when applicable. `docs/roadmap.html` is a historical snapshot.
7. For completed implementations, finished plans, worker handoffs, or local diff reviews, use the project-local `review-implemented-plan` skill. Do not duplicate its process here.

## Diagnostic Scope
Use the narrowest relevant tests, logs, traces, or direct checks for SupportPortal behavior tasks. Do not run `$supportportal-run-report` by default; run it only when explicitly requested or reinstated as a required gate.
