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
- <ordered steps, target files, or expected diff direction; avoid line-by-line patch instructions>

constraints:
- Do not commit, push, merge, or finalize
- Do not edit global `~/.codex` or `~/.claude` skill directories
- Do not hide verification failures or weaken tests to pass
- Preserve public APIs, schemas, config, prompts, and data contracts unless the plan intentionally changes them
- Prefer focused changes; if a broader refactor is necessary, explain why in the report

verification:
<exact command or commands Claude Code should run>

acceptance:
- <observable condition Codex will verify from diff and tests>

report_request:
Return a concise status, files changed, what changed, verification command/result, risks, and Codex review notes.
```

## Field Rules

- `goal`: one outcome, not a broad project.
- `pr_slice`: exactly one real PR slice.
- `plan_id`: unique inside the current PR slice.
- `context`: verified facts only; label guesses or omit them.
- `implementation_plan`: specific enough for execution, but leave Claude Code implementation freedom.
- `constraints`: keep only repo safety rules and task-specific hard boundaries; do not add budget caps or write-scope gates unless the user asks.
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
