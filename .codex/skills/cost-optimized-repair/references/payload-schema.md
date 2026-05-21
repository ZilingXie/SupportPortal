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

constraints:
- Smallest correct change
- No unrelated refactor
- Preserve public APIs unless required

verification:
<exact command or commands the worker should run>

acceptance:
<observable conditions Codex will review>
```

## Field Rules

- `goal`: one outcome, not an implementation plan.
- `scope_hints`: point the worker to likely starting points; do not summarize the whole repo.
- `known_context`: include verified facts only. Mark guesses as guesses or omit them.
- `constraints`: keep the default three unless the task needs tighter rules.
- `verification`: prefer one narrow command that proves the fix.
- `acceptance`: describe user-visible behavior, regression coverage, or exact diff expectations.

## Correction Payload

Use one correction round when the first worker result is close but incomplete.

```md
/repair-worker correction

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
```

Do not send a third worker round. After two failed or unsafe rounds, Codex takes over.
