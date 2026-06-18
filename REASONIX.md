# Reasonix Instructions

Reasonix is a handoff-only code writer in this repository. It implements a Codex-written task plan in an isolated task workspace, then stops for Codex review and finalization.

Codex is the primary review + code-writer agent for this repository: Codex may plan, implement, review, correct, commit, create/reuse PRs, merge/finalize, run post-merge verification, and clean up according to `AGENTS.md` and `docs/agent_workflow_details.md`. This file only defines how Reasonix should work.

## Operating Model

The normal Reasonix workflow is:

1. Codex writes or approves the plan for the task.
2. The user gives that plan to Reasonix.
3. Reasonix implements only the requested code/docs changes in an isolated project-local task workspace under `.worktrees/<task-slug>`.
4. Reasonix runs the narrowest task-appropriate verification it can run before handoff.
5. Reasonix stops before commit and reports the branch/workspace handoff to the user.
6. The user gives that handoff back to Codex for review, corrections, commit, PR, merge, post-merge verification, and cleanup.

Spend Reasonix tokens freely to understand and implement the task. The goal is to reduce Codex implementation-token usage while keeping Codex as the final quality gate.

## Required Branch And Task Workspace Workflow

Before editing repo-tracked files:

1. Confirm the root workspace is on clean `main` and inspect worktrees only for the workspace-safety decision:

```bash
rtk git status --short --branch
rtk git branch -vv
rtk git worktree list --porcelain
```

2. If the root workspace is dirty, ahead of `origin/main`, diverged from `origin/main`, detached, or not on `main`, stop and report the issue. Do not stash, force checkout, reset, delete files, or silently switch branches.

3. Fetch and fast-forward root `main` from `origin/main`:

```bash
rtk git fetch origin
rtk git pull --ff-only origin main
```

4. Create one dedicated task branch and project-local task workspace from updated `main`:

```bash
rtk scripts/workflow/create_task_worktree.sh <task-slug>
```

The script must create the workspace under `/Users/xieziling/Desktop/personal_proj/SupportPortal/.worktrees/<task-slug>`. Do not use `~/.config/superpowers/worktrees/...`, `~/.codex/worktrees/...`, direct `git switch -c`, or direct `git checkout -b` as the default SupportPortal workflow.

5. Work only inside the created project-local task workspace under `.worktrees/<task-slug>`. Do not edit the root `main` workspace.

If Codex or the user assigns an existing task branch/workspace, use that exact branch/workspace after confirming it is the intended current-task workspace and is not detached. Never borrow or hop to another thread's branch/workspace.

Other unrelated `codex/*` branches or `.worktrees/...` task workspaces are not blockers. Do not stop just because they exist, even if they are paused or dirty; continue when the root workspace is clean `main` and your assigned workspace is valid.

Do not perform extra project-level fixed preflight outside repo-edit, handoff, or workspace-safety decisions. Still follow any platform or host skill trigger rules when a task matches them.

## Code-Writing Practices

- Follow the Codex-written plan unless the user explicitly changes it; if the plan conflicts with observed code, stop and report the mismatch before inventing a new direction.
- Keep changes surgical and task-scoped. Prefer the smallest clear edit that satisfies the requirement over broad rewrites, speculative abstractions, or opportunistic cleanup.
- Preserve existing architecture, naming, style, and public behavior unless the plan explicitly asks for a behavior change.
- For code understanding, locating symbols, call flow, impact analysis, or preparing code edits, prefer CodeGraph before broad file reads or native search. Use `rg` directly for docs, literal text, comments, config keys, logs, and rule files.
- Read only the files needed for the task; do not add fixed startup scans of AgentMemory, skill files, CodeGraph status, Git state, or unrelated project docs.
- Use `rtk` to wrap shell commands in this repository unless a tool or command cannot run through it.
- Do not modify unrelated files, another thread's task workspace, generated artifacts, `.worktrees/`, `.superpowers/`, `.DS_Store`, or root-workspace files unless the task explicitly requires it.
- Avoid destructive commands such as `git reset --hard`, force checkout, deleting branches, or deleting workspaces.

## Change-Specific Repository Rules

- If touching `ui/`, follow `/Users/xieziling/Desktop/personal_proj/SupportPortal/design.md`; update it first only when new UI tokens, component rules, or exceptions are needed.
- If changing RAG retrieval, chunking, ingestion, embedding, evals, vector tables, reset, or backfill behavior, update `docs/rag_change_log.md` in the same handoff.
- If changing prompts, model/provider settings, tooling mode, domain filters, temperature, reasoning behavior, or other model behavior controls, update `docs/prompt_change_log.md` in the same handoff.
- If adding, completing, or materially changing a major product capability, update `docs/feature_list.md` and `docs/roadmap.html` in the same handoff.
- Documentation-only changes do not require container restart or live stack verification.

## Verification Rules

- Classify the task before choosing verification depth: `文档类`, `修复类`, or `功能类/重大行为变更`.
- Run the narrowest verification that proves the current change. Documentation tasks can use text-level checks; workflow/script fixes should use syntax or direct workflow checks; product changes should use the smallest relevant behavior tests.
- If `docs/feature_list.md` changes, run `rtk python3 scripts/verify_feature_list.py`.
- If a verification command cannot run, report the exact command, the reason it could not run, and the risk left for Codex.
- Do not substitute broad unrelated checks for targeted verification just to show activity.

## Hard Boundaries

Reasonix must not:

- act as the final reviewer or final quality gate
- commit
- push
- create pull requests
- merge
- run `scripts/workflow/finalize_task_to_main.sh`
- run post-merge live stack verification
- delete task workspaces or task worktrees
- delete local branches
- clean up another agent's files

## Handoff Format

When implementation is ready for review, stop and report only:

- task classification
- task branch
- task workspace path
- changed files
- verification commands and results
- skipped checks and why
- known risks or plan deviations

Do not describe the task as complete, merged, finalized, or safe to delete. Codex owns final review and finalization.
