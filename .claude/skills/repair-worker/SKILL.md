---
name: repair-worker
description: Project-local Claude Code repair worker. Use when invoked with /repair-worker or given a Codex repair payload containing goal, scope_hints, known_context, constraints, verification, and acceptance. Implement the smallest correct code repair, run the requested verification, and return the fixed structured result.
---

# Repair Worker

## Overview

Treat the incoming payload as the task contract. You own implementation and local test iteration; Codex owns triage and final acceptance.

Work only inside the current SupportPortal repository or task worktree. Do not edit global skill directories under `~/.codex` or `~/.claude`.

## Input Contract

Expect:

- `goal`: repair objective
- `scope_hints`: likely starting files, modules, logs, tests, or search terms
- `known_context`: facts Codex already verified
- `constraints`: non-negotiable boundaries
- `verification`: command or commands to run
- `acceptance`: conditions Codex will review

If the payload lacks a usable `goal`, ask one concise clarification and stop.

## Workflow

1. Read the payload first and restate the goal internally.
2. Inspect code starting from `scope_hints`; expand search only as needed.
3. Make the smallest correct change. Prefer local fixes over new abstractions.
4. Preserve public APIs, schemas, config, and existing behavior unless the payload explicitly requires a change.
5. Run the requested verification. If it cannot run, report the blocker and any partial evidence.
6. Return only the structured result format below.

## Boundaries

- Do not perform unrelated refactors.
- Do not reformat untouched files.
- Do not weaken tests to make verification pass.
- Do not introduce TODOs, temporary debug output, or dead code.
- Do not edit `AGENTS.md`, `CLAUDE.md`, `.codex/skills`, or `.claude/skills` unless the payload explicitly asks.
- Stop as `Blocked` if the repair requires unsafe assumptions about auth, payment, migrations, data loss, production secrets, or public API changes.

## Return Format

```md
## Result
Fixed / Not fixed / Blocked

## Files Changed
- ...

## What Changed
- ...

## Verification
- Command:
- Result:

## Risk / Uncertainty
- ...

## Needs Codex Review
- ...
```
