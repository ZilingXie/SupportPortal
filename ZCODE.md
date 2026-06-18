# ZCode Instructions

ZCode is the handoff-only implementation worker in this repository when the user gives it a Codex- or Claude-written plan.

Codex and Claude Code are not limited by this file. They may plan, implement, review, modify code/docs, finalize, or merge according to the user's direct request, `AGENTS.md` / `CLAUDE.md`, and low-frequency details in `docs/agent_workflow_details.md`. This file only defines how ZCode should work.

## Operating Model

The normal ZCode workflow is:

1. Codex or Claude Code writes a plan when the user asks for a plan.
2. The user copies that plan to ZCode.
3. ZCode implements the plan in an isolated project-local task workspace under `.worktrees/<task-slug>`.
4. ZCode stops before commit and reports the branch/workspace handoff.
5. The user gives that handoff to Codex or Claude Code for final review, corrections, commit, PR, merge, post-merge verification, and cleanup.

Spend ZCode tokens freely to understand and implement the task. The goal is to reduce Codex/Claude implementation-token usage while keeping Codex or Claude Code as the final quality gate.

## Required Branch And Task Workspace Workflow

Before editing repo-tracked files:

1. Confirm the root workspace is on `main`:

```bash
git status --short --branch
git branch -vv
git worktree list --porcelain
```

2. If the root workspace is dirty, ahead of `origin/main`, diverged from `origin/main`, detached, or not on `main`, stop and report the issue. Do not stash, force checkout, reset, or delete files.

3. Fetch and fast-forward root `main` from `origin/main`:

```bash
git fetch origin
git pull --ff-only origin main
```

4. Create one dedicated task branch and project-local task workspace from updated `main`:

```bash
bash scripts/workflow/create_task_worktree.sh <task-slug>
```

The script must create the workspace under `/Users/xieziling/Desktop/personal_proj/SupportPortal/.worktrees/<task-slug>`. Do not use `~/.config/superpowers/worktrees/...` or `~/.codex/worktrees/...` as the default SupportPortal workspace location.

5. Work only inside the created project-local task workspace under `.worktrees/<task-slug>`. Do not edit the root `main` workspace.

If Codex, Claude Code, or the user assigns an existing task branch/workspace, use that exact branch/workspace after confirming it is not another thread's workspace and is not detached.

Other unrelated `codex/*` branches or `.worktrees/...` task workspaces are not blockers. Do not stop just because they exist, even if they are paused or dirty; continue when the root workspace is clean `main` and your assigned workspace is valid.

Do not perform extra project-level fixed preflight outside repo-edit, handoff, or workspace-safety decisions. Still follow any platform or host skill trigger rules when a task matches them.

## Implementation Rules

- Follow the Codex- or Claude-written plan unless the user explicitly changes it.
- Keep changes scoped to the task.
- Prefer narrow code reading and targeted edits over broad rewrites.
- Run the narrowest task-appropriate verification available.
- If a verification command cannot run, report the exact command and why it could not run.
- Do not modify unrelated files or another thread's task workspace.

## Hard Boundaries

ZCode must not:

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

When implementation is complete, stop and report only:

- task branch
- task workspace path
- changed files
- verification commands and results
- skipped checks and why
- known risks

Do not describe the task as complete, merged, finalized, or safe to delete. Codex or Claude Code owns final review and finalization.
