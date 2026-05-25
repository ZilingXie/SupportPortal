---
name: repair-worker
description: "Compatibility entry for old /repair-worker payloads. Use when invoked with /repair-worker; follow the same contract as the project-local control-cc-worker skill and return the strict structured result."
---

# Repair Worker Compatibility

Use `.claude/skills/control-cc-worker/` as the source of truth. This compatibility entry exists so older `/repair-worker` payloads keep working while Codex migrates runner prompts to `/control-cc-worker`.

Follow the same input contract, workflow, boundaries, and return format as `control-cc-worker`. Do not broaden scope or treat the task as repair-only.

If the referenced skill is not loaded, still treat the payload as the task contract: implement the smallest correct code change, run the requested verification, preserve public APIs unless explicitly required, and stop as `Blocked` when the work exceeds the payload scope.

Return exactly these six H2 headings in order, with no preamble or wrapper title. The `## Result` body must be exactly `Fixed`, `Not fixed`, or `Blocked`.

```md
## Result
Fixed

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
