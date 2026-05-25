# Task Plan Schema

Use this schema for temporary control-cc task plan files. Store plans outside tracked repo paths, preferably under `/tmp/control-cc-tasks/<branch-or-thread>/<packet>.md`.

```md
request_recap:
<one short restatement of the user request>

pr_slice:
<one PR-sized behavior slice>

acceptance_criteria:
- <observable condition Codex will verify>

target_files:
- <likely files or search hints>

out_of_scope:
- <explicit non-goals>

packet_type:
<"read-only probe" or "atomic writing packet">

write_scope:
<exact files/directories the worker may edit, or "read-only">

shared_core_file:
<true|false>

multi_stage_flow:
<true|false>

runtime_state:
<true|false>

semantic_tests:
<true|false>

docs_in_scope:
<true|false>

broad_write_scope:
<true|false>

verification:
<exact command or commands the worker should run>

worker_prompt:
<the payload passed to the Claude Code worker>

cleanup_policy:
Delete after successful Codex review and verification; preserve only when a worker failure needs debugging.
```

Before dispatching a writing worker, run `scripts/score_packet.py --task-plan-file <file>` and pass the resulting JSON to the runner with `--packet-score-file`.
