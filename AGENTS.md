# Collaboration Rules

## Source Of Truth
1. `AGENTS.md` is the hot-path instruction file for this repository. It must stay short enough to load frequently.
2. Read `docs/agent_workflow_details.md` only when a task triggers worker handoff, branch/finalization edge cases, stack-relevant verification, RAG/prompt/model changes, or feature-list maintenance.
3. `CLAUDE.md` mirrors these hot-path rules for Claude Code. `REASONIX.md` is the worker-specific exception and keeps Reasonix in handoff-before-review mode.
4. `docs/agent.md` is a legacy compatibility redirect for old UI-spec links. It is not an agent-instruction file and must not be treated as an independent spec.
5. For UI source of truth, use `/Users/xieziling/Desktop/personal_proj/SupportPortal/design.md`; `docs/agent.md` only redirects old UI references there.

## Non-Negotiables
1. Use `rtk` to wrap shell commands in this repository unless a tool or command cannot run through it.
2. Do not add project-level fixed preflight steps. Read AgentMemory, skill files, CodeGraph status, or Git/worktree state only when the current task actually needs that context.
3. This does not weaken platform skill rules: if the user names a skill, a task semantically matches a skill, or system/developer instructions require a skill, use that skill exactly as required. Do not interpret on-demand preflight as permission to skip applicable skills.
4. For code understanding, locating symbols, call flow, impact analysis, or preparing code edits, prefer CodeGraph before broad file reads or native search. Do not check `codegraph_status` by default; check it only when CodeGraph fails, appears stale/unavailable, or the task is to diagnose indexing. Use `rg` directly for docs, literal text, comments, config keys, logs, rules, and other non-structural searches.
5. Keep the root workspace `/Users/xieziling/Desktop/personal_proj/SupportPortal` on clean `main`. Do not edit repo-tracked files there.
6. Create task workspaces with `scripts/workflow/create_task_worktree.sh <thread-name-or-slug>`. Normal task workspaces live under `.worktrees/<thread-slug>` on `codex/<thread-slug>` branches.
7. Other unrelated `codex/*` branches or `.worktrees/...` task workspaces are not blockers, even when dirty or paused. Continue when root `main` is clean and the current thread workspace is valid.
8. Never silently reuse, borrow, or hop to another thread's branch/workspace. Stop only if the current thread's exact branch/workspace is wrong, occupied, detached, dirty with unrelated changes, or ambiguous.
9. `main` is PR-only and squash-only. Finalize by pushing the task branch, creating/reusing a PR to `main`, squash-merging, fast-forwarding root `main`, running `codegraph sync`, and cleaning only the current task workspace/branch.
10. Do not create or use `mac`, `mac-integration`, or any `mac -> main` workflow. Report and drain/remove it if it appears.
11. Temporary/tool artifacts such as `.worktrees/`, `.superpowers/`, and `.DS_Store` must stay out of commits unless explicitly required.

## Required Workflow Checks
1. Do not run Git/worktree status as a generic startup step. Run and report `git status --short --branch`, `git branch -vv`, and `git worktree list --porcelain` only before repo-tracked edits, resuming a paused task, finalization, cleanup, or any workspace-safety decision.
2. Classify every task before finalization: `文档类`, `修复类`, or `功能类/重大行为变更`. If the classification is ambiguous, ask before choosing verification depth.
3. Run narrow targeted verification that proves the current change. Documentation tasks default to text-level checks; script/workflow fixes use shell syntax or related workflow tests; product changes use the smallest relevant behavior tests.
4. Once targeted verification passes, proceed directly to `scripts/workflow/finalize_task_to_main.sh` unless the user explicitly changed the workflow.
5. Stack-relevant tasks are anything touching `ui/`, `backend/`, `deployment/`, compose/restart scripts, runtime config, or official local single-host behavior. They require post-merge live stack verification from root `main`.
6. For stack verification, inspect official stack mode first, clean auxiliary stacks if present, restart with the lightweight path by default, verify `/health`, confirm `app_build.ref`, and check one task-specific live marker.
7. Documentation-only changes do not require container restart or live stack verification.

## Direct-To-Main Hot Path
1. Start from root clean `main`, sync it to `origin/main`, then create the task branch/workspace with `scripts/workflow/create_task_worktree.sh`.
2. Work only in the current task workspace. Keep the branch name stable for the thread unless the user explicitly switches tasks or authorizes reuse.
3. Commit only task-owned tracked changes. Stage untracked files only when they belong to the task; leave unrelated artifacts out.
4. Before finalization, refresh against latest `origin/main`, rerun the targeted verification on the task branch, then push and open/reuse a PR to `main`.
5. Squash-merge the PR, wait for GitHub to report it merged, fast-forward root `main`, run `codegraph sync`, and delete only the current task workspace/local branch.
6. If finalization hits conflicts, failed verification, stale root `main`, ambiguous ownership, or GitHub merge failure, stop and use `docs/agent_workflow_details.md` for the recovery path.
7. Only one task may promote to `main` at a time; rely on `scripts/workflow/finalize_task_to_main.sh` for the shared lock.
8. If `docs/feature_list.md` changes, run `python3 scripts/verify_feature_list.py`; the finalize script also performs this check automatically.

## Review And Worker Boundaries
1. Codex and Claude Code are peer repository agents. Either may plan, review, modify code/docs, run verification, commit, create/reuse PRs, squash-merge to `main`, run CodeGraph sync, run required live stack verification, and clean the current task workspace/branch when assigned and following this workflow.
2. Reasonix is the default handoff-only implementation worker. It must stop before commit/PR/merge/finalization/cleanup and wait for Codex or Claude Code review, unless the user explicitly changes `REASONIX.md`.
3. When Codex or Claude Code reviews a Reasonix handoff, treat it as task ownership transfer unless explicitly review-only. Fix actionable issues directly when safe, then finalize under the normal direct-to-`main` workflow.
4. Other explicitly delegated workers must follow the boundary stated in their own handoff instructions. If no boundary is given, default to Reasonix-style handoff-before-finalization.
5. Use the project-local `control-cc` skill when the user requests coordinated Claude Code execution with Codex/Claude review, or when delegated code work can be safely isolated. Candidate worktrees under `/tmp/control-cc-runs/...` are temporary and not authoritative.

## Required Logs
| Change type | Required update | Notes |
|---|---|---|
| RAG-related retrieval, chunking, ingestion, embedding, eval, vector table, reset, or backfill changes | `docs/rag_change_log.md` | Include date, summary, reason, affected files/config, data impact, verification. |
| Prompt/model/tooling-mode/domain-filter/provider/temperature/reasoning behavior changes | `docs/prompt_change_log.md` | Include date, subsystem, version, summary, reason, affected files/config, expected behavior change, verification. |
| Major product capability added, completed, or materially changed | `docs/feature_list.md` | Record only major features; keep required category order and run `python3 scripts/verify_feature_list.py`. |
| UI design language or component/style source-of-truth changes | `design.md` | New/refactored UI under `ui/` must follow `design.md`; update it before code when new tokens/rules/exceptions are needed. |

## When To Read `docs/agent_workflow_details.md`
- You need the trigger matrix for AgentMemory, skills, CodeGraph status, or Git/worktree state.
- You are creating, resuming, finalizing, cleaning, or recovering a task branch/workspace.
- You are reviewing a Reasonix or other handoff-only worker handoff.
- You are changing stack-relevant runtime code and need post-merge live verification.
- You are changing RAG, prompts/models, major feature status, UI design source of truth, or local single-host workflow.
- You hit an edge case: dirty root, detached HEAD, stale/diverged `main`, current branch mismatch, `mac` workflow artifacts, ambiguous task ownership, or failed finalization.

## SupportPortal Diagnostic Verification
1. Do not run `$supportportal-run-report` as a default completion gate.
2. For latency, routing, retrieval, generation, answer accuracy, groundedness, review/intake/investigation, lexical retrieval, or run-level behavior tasks, use the narrowest task-appropriate tests/logs/traces/direct checks instead.
3. Run `$supportportal-run-report` or `$supportportal-run-report --profile-lexical` only when the user explicitly asks or a future instruction reinstates it as required.
