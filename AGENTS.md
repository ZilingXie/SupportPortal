# Collaboration Rules

## Source Of Truth
1. `AGENTS.md` is the hot-path instruction file for this repository. It must stay short enough to load frequently.
2. Read `docs/agent_workflow_details.md` only when a task triggers worker handoff, branch/finalization edge cases, stack-relevant verification, RAG/prompt/model changes, or feature-list maintenance.
3. `CLAUDE.md` and `REASONIX.md` add worker-specific rules; they must stay stricter than, or consistent with, this file.
4. `docs/agent.md` is a legacy compatibility redirect for old UI-spec links. It is not an agent-instruction file and must not be treated as an independent spec.
5. For UI source of truth, use `/Users/xieziling/Desktop/personal_proj/SupportPortal/design.md`; `docs/agent.md` only redirects old UI references there.

## Non-Negotiables
1. Before starting a task, follow the repository-level memory/tooling instructions already provided to the agent, including global AgentMemory lookup and `rtk` command wrapping.
2. For code understanding, use CodeGraph before broad file reads or native search. Use `rg` mainly for literal text, comments, config keys, docs wording, or files CodeGraph already identified.
3. Keep the root workspace `/Users/xieziling/Desktop/personal_proj/SupportPortal` on clean `main`. Do not edit repo-tracked files there.
4. Create task workspaces with `scripts/workflow/create_task_worktree.sh <thread-name-or-slug>`. Normal task workspaces live under `.worktrees/<thread-slug>` on `codex/<thread-slug>` branches.
5. Other unrelated `codex/*` branches or `.worktrees/...` task workspaces are not blockers, even when dirty or paused. Continue when root `main` is clean and the current thread workspace is valid.
6. Never silently reuse, borrow, or hop to another thread's branch/workspace. Stop only if the current thread's exact branch/workspace is wrong, occupied, detached, dirty with unrelated changes, or ambiguous.
7. `main` is PR-only and squash-only. Finalize by pushing the task branch, creating/reusing a PR to `main`, squash-merging, fast-forwarding root `main`, running `codegraph sync`, and cleaning only the current task workspace/branch.
8. Do not create or use `mac`, `mac-integration`, or any `mac -> main` workflow. Report and drain/remove it if it appears.
9. Temporary/tool artifacts such as `.worktrees/`, `.superpowers/`, and `.DS_Store` must stay out of commits unless explicitly required.

## Required Workflow Checks
1. Before first repo-tracked edit, resuming a paused task, finalization, or cleanup, run and report `git status --short --branch`, `git branch -vv`, and `git worktree list --porcelain` for the current thread context.
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

## Worker And Review Boundaries
1. Claude Code, Reasonix, and other workers are implementation workers only when delegated. They may edit and verify inside assigned workspaces, but must not commit, push, create PRs, merge, finalize, run post-merge verification, or clean workspaces/branches.
2. Worker implementations must come from an explicit plan and stop with a handoff: plan, changed files, verification evidence, skipped checks, and known risks.
3. Codex owns review, corrections, commits, PR creation/reuse, squash merge to `main`, CodeGraph sync, required live stack verification, and cleanup.
4. When the user asks Codex to review a worker handoff, treat it as task ownership transfer unless explicitly review-only. Fix actionable issues directly when safe, then finalize under the normal direct-to-`main` workflow.
5. Use the project-local `control-cc` skill when the user requests Codex planning + Claude Code execution + Codex review, or when delegated code work can be safely isolated. Candidate worktrees under `/tmp/control-cc-runs/...` are temporary and not authoritative.

## Required Logs
| Change type | Required update | Notes |
|---|---|---|
| RAG-related retrieval, chunking, ingestion, embedding, eval, vector table, reset, or backfill changes | `docs/rag_change_log.md` | Include date, summary, reason, affected files/config, data impact, verification. |
| Prompt/model/tooling-mode/domain-filter/provider/temperature/reasoning behavior changes | `docs/prompt_change_log.md` | Include date, subsystem, version, summary, reason, affected files/config, expected behavior change, verification. |
| Major product capability added, completed, or materially changed | `docs/feature_list.md` | Record only major features; keep required category order and run `python3 scripts/verify_feature_list.py`. |
| UI design language or component/style source-of-truth changes | `design.md` | New/refactored UI under `ui/` must follow `design.md`; update it before code when new tokens/rules/exceptions are needed. |

## When To Read `docs/agent_workflow_details.md`
- You are creating, resuming, finalizing, cleaning, or recovering a task branch/workspace.
- You are reviewing a Claude Code, Reasonix, or other worker handoff.
- You are changing stack-relevant runtime code and need post-merge live verification.
- You are changing RAG, prompts/models, major feature status, UI design source of truth, or local single-host workflow.
- You hit an edge case: dirty root, detached HEAD, stale/diverged `main`, current branch mismatch, `mac` workflow artifacts, ambiguous task ownership, or failed finalization.

## SupportPortal Diagnostic Verification
1. Do not run `$supportportal-run-report` as a default completion gate.
2. For latency, routing, retrieval, generation, answer accuracy, groundedness, review/intake/investigation, lexical retrieval, or run-level behavior tasks, use the narrowest task-appropriate tests/logs/traces/direct checks instead.
3. Run `$supportportal-run-report` or `$supportportal-run-report --profile-lexical` only when the user explicitly asks or a future instruction reinstates it as required.
