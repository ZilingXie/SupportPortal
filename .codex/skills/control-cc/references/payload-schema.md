# Implementation Plan Payload

Use this schema for the plan file passed to `run_cc_plan.py`. Store plan files outside tracked repo paths, usually under `/tmp/control-cc-runs/<thread>/pr-XX/plan-YY/plan.md`.

```md
/control-cc-worker

goal:
<one concrete behavior, repair, or refactor objective>

pr_slice:
<the current real PR slice, or "single-pr" for goal-only work>

plan_id:
<stable short id such as pr-01-plan-02>

context:
- <verified facts, likely files, logs, tests, or previous PR handoff notes>

implementation_plan:
- <ordered implementation steps or expected diff direction>

constraints:
- Smallest correct change
- No unrelated refactor
- Preserve public APIs, schemas, config, prompts, and data contracts unless listed in implementation_plan
- Do not commit, push, merge, or finalize

verification:
<exact command or commands Claude Code should run>

acceptance:
- <observable condition Codex will verify from diff and tests>

report_request:
Return status, files changed, what changed, verification command/result, risks, and Codex review notes.
```

## Field Rules

- `goal`: one outcome, not a broad project.
- `pr_slice`: exactly one real PR slice.
- `plan_id`: unique inside the current PR slice.
- `context`: verified facts only; label guesses or omit them.
- `implementation_plan`: specific enough for execution, but not a line-by-line patch.
- `constraints`: include the defaults and add task-specific stop conditions.
- `verification`: prefer the narrowest command that proves the plan.
- `acceptance`: describe what Codex will check after diff review.

## Correction Payload

Use at most one correction round when a Claude Code result is close but incomplete.

```md
/control-cc-worker

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

report_request:
Return status, files changed, verification evidence, remaining risks, and Codex review notes.
```

After a failed correction, Codex either fixes the diff directly or splits the plan further.

## Legacy

Older `/repair-worker` payloads are still supported through the compatibility runner. New Control CC work should use implementation plan payloads.
