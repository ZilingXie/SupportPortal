# SupportPortal Agent Rules

## Source Of Truth
1. `AGENTS.md` is the hot-path instruction file. Keep it short enough to load often.
2. `CLAUDE.md` mirrors these hot-path rules for Claude Code. `REASONIX.md` is the Reasonix exception and keeps Reasonix in handoff-before-review mode.
3. Read `docs/agent_workflow_details.md` only for worker handoff, branch/finalization/recovery edge cases, stack-relevant verification, RAG/prompt/model changes, feature-list maintenance, or local single-host workflow changes.
4. `docs/agent.md` is only a legacy UI redirect. For UI source of truth, use `/Users/xieziling/Desktop/personal_proj/SupportPortal/design.md`.

## Non-Negotiables
1. Do not add fixed preflight steps. Read AgentMemory, skill files, CodeGraph status, or Git/worktree state only when this task needs that context.
2. Use required platform/project skills when triggered by the user, task semantics, or higher-level instructions. On-demand preflight does not weaken skill rules.
3. Prefer CodeGraph for code understanding, symbols, call flow, impact analysis, or preparing code edits. Use `rg` for docs, literal text, comments, config keys, logs, rules, and other non-structural searches. Check `codegraph_status` only when CodeGraph fails, appears stale/unavailable, or indexing is the task.
4. Keep the root workspace `/Users/xieziling/Desktop/personal_proj/SupportPortal` on clean `main`; do not edit repo-tracked files there.
5. Create task workspaces with `scripts/workflow/create_task_worktree.sh <thread-name-or-slug>`. Work only in the current task workspace under `.worktrees/<thread-slug>` on `codex/<thread-slug>`.
6. Unrelated `codex/*` branches or `.worktrees/...` workspaces are not blockers. Never reuse, borrow, or hop to another thread's branch/workspace. Stop only if the current thread workspace is wrong, occupied, detached, dirty with unrelated changes, or ambiguous.
7. `main` is PR-only and squash-only. Finalization pushes the task branch, opens/reuses a PR to `main`, squash-merges, fast-forwards root `main`, runs `codegraph sync`, and cleans only the current task workspace/branch.
8. Do not create or use `mac`, `mac-integration`, or any `mac -> main` workflow. Report and drain/remove it if it appears.
9. Keep temporary/tool artifacts such as `.worktrees/`, `.superpowers/`, and `.DS_Store` out of commits unless explicitly required.

## Workflow Checks
1. Do not run Git/worktree status as generic startup. Run and report `git status --short --branch`, `git branch -vv`, and `git worktree list --porcelain` only before repo-tracked edits, resuming a paused task, finalization, cleanup, or workspace-safety decisions.
2. Before finalization classify the task as `文档类`, `修复类`, or `功能类/重大行为变更`; ask if ambiguous.
3. Run narrow targeted verification that proves the change: text checks for docs, shell/workflow tests for scripts, and the smallest relevant behavior tests for product changes.
4. Once targeted verification passes, proceed directly to `scripts/workflow/finalize_task_to_main.sh` unless the user explicitly changed the workflow.
5. Stack-relevant tasks touch `ui/`, `backend/`, `deployment/`, compose/restart scripts, runtime config, or official local single-host behavior. They require post-merge live stack verification from root `main`.
6. Stack verification: inspect official stack mode, clean auxiliary stacks if present, restart with the lightweight path by default, verify `/health`, confirm `app_build.ref`, and check one task-specific live marker.
7. Documentation-only changes do not require container restart or live stack verification.

## Direct-To-Main Hot Path
1. Start from clean root `main`, sync to `origin/main`, then create the task branch/workspace with `scripts/workflow/create_task_worktree.sh`.
2. Keep the branch name stable for the thread unless the user explicitly switches tasks or authorizes reuse.
3. Commit only task-owned tracked changes; stage untracked files only when they belong to the task.
4. Before finalization, refresh against latest `origin/main`, rerun targeted verification on the task branch, then push and open/reuse a PR to `main`.
5. Squash-merge, wait for GitHub to report merged, fast-forward root `main`, run `codegraph sync`, and delete only the current task workspace/local branch.
6. If finalization hits conflicts, failed verification, stale root `main`, ambiguous ownership, or GitHub merge failure, stop and use `docs/agent_workflow_details.md`.
7. Only one task may promote to `main` at a time; rely on the finalize script's shared lock.
8. If `docs/feature_list.md` changes, run `python3 scripts/verify_feature_list.py`; finalization also checks it.

## Review Skill
1. For completed implementation, finished plan, worker handoff, or local diff review requests, use the project-local `review-implemented-plan` skill.
2. Keep `AGENTS.md` and `CLAUDE.md` free of duplicated review-process steps; the skill owns the review/finalization workflow and trigger examples.

## Required Logs
| Change type | Required update | Notes |
|---|---|---|
| RAG-related retrieval, chunking, ingestion, embedding, eval, vector table, reset, or backfill changes | `docs/rag_change_log.md` | Include date, summary, reason, affected files/config, data impact, verification. |
| Prompt/model/tooling-mode/domain-filter/provider/temperature/reasoning behavior changes | `docs/prompt_change_log.md` | Include date, subsystem, version, summary, reason, affected files/config, expected behavior change, verification. |
| Major product capability added, completed, or materially changed / `功能类/重大行为变更` | `docs/feature_list.md` and `docs/roadmap.html` | Record major features in the feature list; also update Roadmap overall rollout / `整体落地进度` when the change materially affects delivery status, phase gates, or tracked capabilities. |
| UI design language or component/style source-of-truth changes | `design.md` | New/refactored UI under `ui/` must follow `design.md`; update it before code when new tokens/rules/exceptions are needed. |

## When To Read `docs/agent_workflow_details.md`
- You need the trigger matrix for AgentMemory, skills, CodeGraph status, or Git/worktree state.
- You are creating, resuming, finalizing, cleaning, or recovering a task branch/workspace.
- You need worker-boundary details referenced by `review-implemented-plan`.
- You are changing stack-relevant runtime code, RAG, prompts/models, major feature status, UI design source of truth, or local single-host workflow.
- You hit an edge case: dirty root, detached HEAD, stale/diverged `main`, current branch mismatch, `mac` workflow artifacts, ambiguous task ownership, or failed finalization.

## SupportPortal Diagnostic Verification
1. Do not run `$supportportal-run-report` as a default completion gate.
2. For latency, routing, retrieval, generation, answer accuracy, groundedness, review/intake/investigation, lexical retrieval, or run-level behavior tasks, use the narrowest task-appropriate tests/logs/traces/direct checks instead.
3. Run `$supportportal-run-report` or `$supportportal-run-report --profile-lexical` only when the user explicitly asks or a future instruction reinstates it as required.
