# Control CC Planning Artifacts

Control CC uses temporary coordination files outside tracked repo paths. Prefer `/tmp/control-cc-runs/<thread>/`.

## PR Handoff

Write this after each real PR slice is merged to `main`:

```md
pr_slice:
<number and short title>

merged_branch:
<codex/... branch that was finalized>

summary:
- <what changed>

verification:
- <fresh verification commands and results>

diff_review:
- <important review conclusions, risks, or follow-ups>

next_pr_context:
- <only facts the next PR needs>
```

## Plan Directory

Each implementation plan should have a directory such as:

```text
/tmp/control-cc-runs/<thread>/pr-01/plan-01/
  plan.md
  report.json
  review_packet.json
  accepted.patch
  worktree/
```

`plan.md` follows `references/payload-schema.md`. `report.json` and `review_packet.json` are written by `run_cc_plan.py`. `review_packet.json` is the first Codex review surface; open full logs or full diffs only when the packet flags risk or the task is high risk. `accepted.patch` is exported from a candidate worktree only after Codex or a plan sub-agent reviews the result.

## Cleanup Policy

Delete candidate worktrees after integration or rejection with `candidate_worktree.py cleanup`. Temporary run directories may be kept until the thread is finalized when they contain useful reports, patches, or handoff summaries. Never commit these files.

## Legacy

The older packet task-plan format remains useful only for compatibility with the v2 runner path.
