# SupportPortal Agent Rules

## Source Of Truth
1. `AGENTS.md` is the short hot-path rule file. `CLAUDE.md` mirrors it for Claude Code; `REASONIX.md` defines the Reasonix exception.
2. Read `docs/agent_workflow_details.md` only for workflow edge cases, worker handoff, stack verification, RAG/prompt/model changes, feature-list maintenance, or local single-host changes.
3. `docs/agent.md` is a legacy UI redirect. The UI source of truth is `/Users/xieziling/Desktop/personal_proj/SupportPortal/design.md`.

## Working Rules
1. Do not add fixed startup scans. Use required skills and AgentMemory only when the task triggers them. Prefer CodeGraph for structural code questions and `rg` for docs, literals, config, logs, and rules.
2. Keep `/Users/xieziling/Desktop/personal_proj/SupportPortal` on clean `main`; do not edit tracked files in the root. Before tracked edits, sync `main` with `origin/main` and create a task workspace with `scripts/workflow/create_task_worktree.sh <thread-name-or-slug>`.
3. Work only in the current task's `.worktrees/<thread-slug>` on its `codex/<thread-slug>` branch. Do not borrow another task's workspace or branch. Inspect Git/worktree state before tracked edits, resuming, finalization, cleanup, or other workspace-safety decisions.
4. `main` is PR-only and squash-only. After targeted verification, use `scripts/workflow/finalize_task_to_main.sh`; it owns refresh, push, PR, merge, root fast-forward, CodeGraph sync, and current-task cleanup. Never create or use `mac` or `mac-integration` workflows.
5. Keep `.worktrees/`, `.superpowers/`, `.DS_Store`, and unrelated changes out of commits. Stop and ask before acting when ownership or workspace state is ambiguous.

## Implementation Defaults
1. Start with the smallest clear primary path for the current requirement. Do not add speculative abstractions, configuration, retries, caches, migrations, compatibility paths, or defensive checks for hypothetical future needs.
2. Add fallback, retry, compatibility, or recovery behavior only when the task, an existing API/data/deployment contract, or repository/runtime evidence requires it. Preserve existing contract behavior until it is proven obsolete.
3. Fallback is an exceptional degraded state, not silent success. Prefer an explicit error, fail-closed behavior, or human review. Every new fallback needs one trigger, one output/state contract, and an owner-visible signal with the failure reason preserved.
4. Use the narrowest mechanism that provides the required guarantee: transactions, conditional updates, unique constraints, or atomic file operations for atomicity; hashes only for content identity or integrity. Before adding non-trivial complexity, state the concrete failure/contract, visibility signal, and smallest verification. Stop after the requested behavior is narrowly verified.

## Verification And Records
1. Classify the task as `文档类`, `修复类`, or `功能类/重大行为变更`; use the narrowest targeted verification. Documentation changes do not require container restart.
2. Stack-relevant changes require post-merge live verification from root `main`; follow the official restart and health/build-marker checks in `docs/agent_workflow_details.md`.
3. RAG changes update `docs/rag_change_log.md`; prompt/model/tooling behavior changes update `docs/prompt_change_log.md`.
4. Major product capabilities update `docs/feature_list.md` and the corresponding `docs/project/tasks/<task-id>.json`; run `python3 scripts/generate_project_overview.py --check` and `python3 scripts/verify_feature_list.py` when applicable. `docs/roadmap.html` is a historical snapshot.
5. For completed implementations, finished plans, worker handoffs, or local diff reviews, use the project-local `review-implemented-plan` skill. Do not duplicate its process here.

## Diagnostic Scope
Use the narrowest relevant tests, logs, traces, or direct checks for SupportPortal behavior tasks. Do not run `$supportportal-run-report` by default; run it only when explicitly requested or reinstated as a required gate.
