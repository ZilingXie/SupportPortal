# Payload Schema

Use this schema to keep Codex-to-worker handoff short and testable.

## Initial Payload

```md
/repair-worker

goal:
<one concrete repair objective>

scope_hints:
<likely files, modules, logs, failing tests, or search terms>

known_context:
<only facts Codex has already verified>

pr_slice:
<optional PR slice name or number when the user supplied a multi-PR plan>

parallel_group:
<optional group name plus whether this worker is read-only or has an isolated write workspace>

write_scope:
<files/directories this worker may edit, or "read-only">

constraints:
- Smallest correct change
- No unrelated refactor
- Preserve public APIs unless required

verification:
<exact command or commands the worker should run>

acceptance:
<observable conditions Codex will review>

final_output_contract:
- Final answer starts exactly with `## Result`.
- Under `## Result`, write exactly one of `Fixed`, `Not fixed`, or `Blocked`.
- Do not write `Fixed.`, `Success`, `Implemented`, bullets, or code formatting on the result line.
- Use exactly these six H2 headings in order: `## Result`, `## Files Changed`, `## What Changed`, `## Verification`, `## Risk / Uncertainty`, `## Needs Codex Review`.
```

## Field Rules

- `goal`: one outcome, not an implementation plan.
- `scope_hints`: point the worker to likely starting points; do not summarize the whole repo.
- `known_context`: include verified facts only. Mark guesses as guesses or omit them.
- `pr_slice`: include when the user provided a multi-PR plan; one payload should belong to exactly one PR slice.
- `parallel_group`: include when launching multiple agents together; state whether the worker is read-only or has an isolated write workspace.
- `write_scope`: required for parallel workers; never allow two writing payloads to edit the same files in the same worktree.
- `constraints`: keep the default three unless the task needs tighter rules.
- `verification`: prefer one narrow command that proves the fix.
- `acceptance`: describe user-visible behavior, regression coverage, or exact diff expectations.
- `final_output_contract`: repeat the strict return contract at the end of every payload so the worker does not drift from the runner parser.

## Correction Payload

Use one correction round when the first worker result is close but incomplete.

```md
/repair-worker

mode:
correction

problem:
<specific issue in the returned diff, test result, or risk>

must_keep:
<parts of the first result that are acceptable>

must_change:
<specific changes needed>

verification:
<command to rerun>

acceptance:
<conditions for the corrected result>

final_output_contract:
- Final answer starts exactly with `## Result`.
- Under `## Result`, write exactly one of `Fixed`, `Not fixed`, or `Blocked`.
- Do not write `Fixed.`, `Success`, `Implemented`, bullets, or code formatting on the result line.
- Use exactly these six H2 headings in order: `## Result`, `## Files Changed`, `## What Changed`, `## Verification`, `## Risk / Uncertainty`, `## Needs Codex Review`.
```

Do not use `/repair-worker correction`; Claude Code CLI treats slash-command arguments inconsistently. Keep `/repair-worker` as the first line and put `mode: correction` in the payload body.

Do not send a third worker round. After two failed or unsafe rounds, Codex takes over.
