# Claude Code Rules

When invoked with `/repair-worker`, treat the provided payload as the task contract. Follow the project-local `repair-worker` skill and avoid unrelated changes.

## CodeGraph First

For any task that requires understanding, locating, tracing, or changing code, prefer the project CodeGraph tools before native file search or broad file reads. Use CodeGraph for structural questions such as definitions, callers, callees, data flow, change impact, and task-focused symbol context.

Use native search such as `rg` primarily for literal text, comments, log messages, configuration keys, documentation wording, or after CodeGraph has identified the specific files that need direct inspection. If CodeGraph is unavailable, say so explicitly and use the narrowest native fallback.
