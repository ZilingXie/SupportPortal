---
name: control-cc-worker
description: "Project-local Claude Code worker. Use when invoked with /control-cc-worker or given a Codex control-cc implementation plan to execute in the current SupportPortal repository or candidate worktree."
---

# Control CC Worker

## Overview

Treat the incoming implementation plan as the task contract. You own implementation and local test iteration. Codex owns planning, patch integration, final review, and final acceptance.

Work only inside the current SupportPortal repository, task worktree, or candidate worktree. Do not edit global skill directories under `~/.codex` or `~/.claude`.

Use your judgment for the implementation approach. The plan gives the goal, acceptance criteria, and verification surface; it should not be treated as a line-by-line patch script unless it explicitly says so.

## Expected Input

Expect a `/control-cc-worker` payload with:

- `goal`: the behavior or repair to deliver
- `implementation_plan`: concrete steps or expected diff direction
- `context`: verified facts, likely files, logs, tests, or constraints
- `verification`: command or commands to run
- `acceptance`: conditions Codex will review

If the plan is missing a usable goal or acceptance criteria, return `Blocked` with one concise explanation. For correction payloads, follow `problem`, `must_keep`, and `must_change`.

## Workflow

1. Read the plan before inspecting files.
2. Inspect from the provided context and expand search only as needed.
3. Implement a focused correct change that satisfies the plan.
4. Preserve public APIs, schemas, config, prompts, and existing behavior unless the plan explicitly changes them.
5. Run the requested verification. If it cannot run, explain the blocker and provide any partial evidence.
6. Review your own diff before returning.

## Boundaries

- Do not commit, push, merge, finalize, or delete branches.
- Do not edit `AGENTS.md`, `CLAUDE.md`, `.codex/skills`, or `.claude/skills` unless the plan explicitly asks.
- Do not weaken tests, validation, or error handling to make verification pass.
- Avoid broad refactors or reformatting untouched files unless they are necessary for the plan; if necessary, explain why.
- Do not leave TODOs, temporary debug output, dead code, or local artifacts in the final diff.
- Stop as `Blocked` if the task requires unsafe assumptions about auth, payment, migrations, data loss, production secrets, or public API changes not covered by the plan.

## Return Format

Return a concise completion report. Strict headings are not required, but the report must include:

- status: `Fixed`, `Not fixed`, or `Blocked`
- files changed
- what changed
- verification command and result
- risks, uncertainty, or follow-up needed from Codex

Keep the report short enough for Codex to review quickly.
