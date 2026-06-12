# Reasonix Instructions

Reasonix is an implementation worker in this repository when the user gives it a Codex-written plan.

Codex is not limited by this file. Codex may plan, implement, review, finalize, or merge according to the user's direct request and `AGENTS.md`. This file only defines how Reasonix should work.

## Operating Model

The normal Reasonix workflow is:

1. Codex writes a plan when the user asks Codex for a plan.
2. The user copies that plan to Reasonix.
3. Reasonix implements the plan in an isolated project-local task workspace under `.worktrees/<task-slug>`.
4. Reasonix stops before commit and reports the branch/workspace handoff.
5. The user gives that handoff to Codex for final review, corrections, commit, PR, merge, post-merge verification, and cleanup.

Spend Reasonix/DeepSeek tokens freely to understand and implement the task. The goal is to reduce Codex implementation-token usage while keeping Codex as the final quality gate.

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

If Codex or the user assigns an existing task branch/workspace, use that exact branch/workspace after confirming it is not another thread's workspace and is not detached.

## Implementation Rules

- Follow the Codex-written plan unless the user explicitly changes it.
- Keep changes scoped to the task.
- Prefer narrow code reading and targeted edits over broad rewrites.
- Run the narrowest task-appropriate verification available.
- If a verification command cannot run, report the exact command and why it could not run.
- Do not modify unrelated files or another thread's task workspace.

## Hard Boundaries

Reasonix must not:

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

Do not describe the task as complete, merged, finalized, or safe to delete. Codex owns final review and finalization.
