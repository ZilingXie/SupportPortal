# Claude Code Rules

> **Note**: When invoked with `/repair-worker`, treat the provided payload as the task contract. Follow the project-local `repair-worker` skill and avoid unrelated changes. The standard isolate/plan/execute/review/fix/merge workflow below does not apply to `/repair-worker` tasks.

## CodeGraph First

For any task that requires understanding, locating, tracing, or changing code, prefer CodeGraph tools before native file search or broad file reads.

| Question | Tool |
|---|---|
| "Where is X defined?" / "Find symbol named X" | `codegraph_search` |
| "What calls function Y?" | `codegraph_callers` |
| "What does Y call?" | `codegraph_callees` |
| "How does X reach/become Y?" | `codegraph_trace` — returns the call path when statically available; may point to dynamic dispatch hops (callbacks, React, JSX) that need manual inspection |
| "What would break if I changed Z?" | `codegraph_impact` |
| "Show me Y's signature / source / docstring" | `codegraph_node` |
| "Give me focused context for a task/area" | `codegraph_context` |
| "See several related symbols' source at once" | `codegraph_explore` |
| "What files exist under path/" | `codegraph_files` |
| "Is the index healthy?" | `codegraph_status` |

### Rules of thumb

- **Answer directly — don't delegate exploration.** `codegraph_context` → ONE `codegraph_explore` is the standard 2-call pattern. For a flow question, `codegraph_trace` from→to returns the whole path in one call.
- **Trust codegraph results.** They come from a full AST parse. Do not re-verify with grep.
- **Fall back when needed.** If CodeGraph is unavailable, uninitialized, or stale, report it explicitly and fall back to the narrowest native search (`rg`, `grep`).
- **Don't grep first** when looking up a symbol by name. `codegraph_search` is faster.
- **Don't chain `codegraph_search` + `codegraph_node`** — `codegraph_context` does both in one call.
- **Don't loop `codegraph_node`** — `codegraph_explore` returns many symbols' source in one capped call.
- **Index lag**: file watcher debounces ~500ms behind writes; don't re-query immediately after editing.

Use native search (`rg`, `grep`) only for literal text — comments, log messages, config keys, docs wording — or after CodeGraph has identified the specific files to inspect.

---

## Code Change Workflow

When a task involves modifying code or files, follow this workflow:

### 1. Isolate
Create a new branch and worktree so changes stay off `main`. The project provides a standard workflow via `scripts/workflow/create_task_worktree.sh <slug>` which handles clean root `main` sync, branch creation, and worktree setup. For simple tasks, `EnterWorktree` is also available. Always confirm you are on the correct branch before editing.

### 2. Plan
Make an implementation plan before writing any code:
```
EnterPlanMode
```
Explore the codebase, design the approach, and get user approval before implementing.

### 3. Execute
Implement the changes according to the approved plan.

### 4. Review
Hand off all changes to Codex for review:
```
/codex:review
```
Codex reviews the diff for bugs, edge cases, security issues, and design problems.

### 5. Fix
If Codex finds issues, let it fix them:
```
/codex:rescue --resume 修复审查发现的 N 个问题并优化实现
```

### 6. Merge & Cleanup
After review and fixes pass, finalize the task branch to `main`. The canonical path is `scripts/workflow/finalize_task_to_main.sh`, which handles task classification, targeted verification, squash-merge, CodeGraph sync, and post-merge live stack verification. For simpler workflows, you can also delegate to Codex:
```
/codex:rescue --resume 审查通过，合并分支到 main 并清理 worktree
```

---

## Agent Delegation

### When to Use Agents

| Agent | Use For |
|---|---|
| `planner` | Complex multi-step features, before writing code |
| `architect` | System design decisions, technology choices |
| `code-reviewer` | Before finalizing PRs |
| `security-reviewer` | Auth, payments, user input handling |
| `database-reviewer` | Schema changes, query optimization |
| `tdd-guide` | When writing tests first is required |
| `build-error-resolver` | Build, import, or type-check failures |
| `refactor-cleaner` | Dead code removal, deduplication |
| `doc-updater` | Documentation sync with code changes |

### Delegation Rules

- Use agents for domain-specific review (code-reviewer, security-reviewer) and implementation subproblems — not for codebase exploration (use CodeGraph directly)
- Delegate parallelizable research to multiple agents at once
- Use the right specialist for each domain
- Don't use agents for trivial one-line changes
- Review agent output before committing

---

## Codex Integration

### Commands

| Command | Purpose |
|---|---|
| `/codex:review` | Code review against local git diff (read-only) |
| `/codex:adversarial-review` | Challenging review that questions design choices |
| `/codex:rescue` | Delegate investigation, fixes, or optimization to Codex |
| `/codex:status` | View active and recent Codex jobs |
| `/codex:result <job-id>` | View completed job results |
| `/codex:cancel <job-id>` | Cancel a running job |
| `/codex:setup` | Check Codex CLI, config, and auth status |

### Quick Reference

| Scenario | Command |
|---|---|
| Review my changes | `/codex:review` |
| Review + question my design | `/codex:adversarial-review` |
| Fix issues from last review | `/codex:rescue --resume 修复审查发现的这些问题：xxx` |
| One-shot review + fix | `/codex:rescue 审查当前 diff 并修复发现的问题` |
| Large PR, run in background | `/codex:review --background` |
| Investigate CI failure | `/codex:rescue 调查 CI 失败原因并修复` |
| Continue previous rescue | `/codex:rescue --resume` |
| Check job progress | `/codex:status` |

---

## Other Rules

When invoked with `/repair-worker`, treat the provided payload as the task contract. Follow the project-local `repair-worker` skill and avoid unrelated changes.
