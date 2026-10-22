# Split Gateway readback carrier

This carrier is a deployment verification tool for Hermes metadata readiness. It
does not call SupportPortal business tools, create cases/sessions/runs, upload
files, or change ECS/EFS. Health and toolsets run in separate ECS Exec sessions;
the command succeeds only when both sessions produce exactly one valid JSON
marker.

```sh
python deployment/hermes-case-files/gateway_readback.py \
  --cluster supportportal-preproduction \
  --task arn:aws:ecs:us-east-1:891612554546:task/supportportal-preproduction/TASK \
  --output /absolute/path/to/fresh-readback
```

The output directory receives `health.json` and `toolsets.json`. Each record
contains raw stdout/stderr, return code, timeout and EOF flags, marker, status,
error code, and parsed payload. `GATEWAY_READBACK_PASSED` is emitted only after
both records pass. Missing, duplicate, malformed or partial markers, EOF,
non-zero exit, timeout, and runner errors emit `GATEWAY_READBACK_FAILED` and a
non-zero exit. A successful health session never masks a failed toolsets session.

The carrier's tests use the real Python entry points with a bounded runner seam
only for the ECS process boundary. They cover complete success, toolsets EOF,
non-zero, truncated JSON, missing and duplicate markers, health EOF, and timeout.
The wrapper's exit status or a partial marker is never treated as readiness.
