# Claude Code Rules

> **Note**: When invoked with `/repair-worker`, treat the provided payload as the task contract. Follow the project-local `repair-worker` skill and avoid unrelated changes. The standard plan/execute/review handoff workflow below does not apply to `/repair-worker` tasks.

## Authority

1. `AGENTS.md` is the repository source of truth. Follow it unless a direct user instruction for the current task explicitly says otherwise.
2. `CLAUDE.md` adds Claude Code-specific operating rules. If `CLAUDE.md` appears less strict than `AGENTS.md`, follow the stricter `AGENTS.md` rule.
3. Read `docs/agent_workflow_details.md` when `AGENTS.md` points to low-frequency workflow details.
4. For delegated code work, Claude Code is an implementation worker. Codex owns review, acceptance, commits, PR creation, merges, finalization, and cleanup.

## Non-Negotiable Boundary

When Claude Code writes code or edits repo-tracked files:

1. Do not commit.
2. Do not push.
3. Do not create pull requests.
4. Do not merge branches.
5. Do not run `scripts/workflow/finalize_task_to_main.sh`.
6. Do not delete or clean up task workspaces or task worktrees.
7. Do not use `/codex:rescue` as a shortcut to commit, merge, finalize, or clean up your own work.

After implementation, stop with a handoff to Codex review. Codex will review the diff, make any needed corrections, commit, create or update the PR, merge, run required post-merge verification, and clean up.

## CodeGraph First

For any task that requires understanding, locating, tracing, or changing code, prefer CodeGraph tools before native file search or broad file reads.

| Question | Tool |
|---|---|
| "Where is X defined?" / "Find symbol named X" | `codegraph_search` |
| "What calls function Y?" | `codegraph_callers` |
| "What does Y call?" | `codegraph_callees` |
| "How does X reach/become Y?" | `codegraph_trace` |
| "What would break if I changed Z?" | `codegraph_impact` |
| "Show me Y's signature / source / docstring" | `codegraph_node` |
| "Give me focused context for a task/area" | `codegraph_context` |
| "See several related symbols' source at once" | `codegraph_explore` |
| "What files exist under path/" | `codegraph_files` |
| "Diagnose suspected stale/unavailable index" | `codegraph_status` |

### Rules of thumb

- Answer directly; do not delegate codebase exploration to other agents.
- Use `codegraph_context` followed by at most one `codegraph_explore` for broad task context.
- Use `codegraph_trace` for static flow questions.
- Trust CodeGraph results from the AST index; do not re-check symbol lookups with grep.
- Fall back only when needed. If CodeGraph is unavailable, uninitialized, or stale, report it explicitly, use `codegraph_status` only for that diagnostic, and then use the narrowest native search.
- Use native search such as `rg` only for literal text, comments, log messages, config keys, documentation wording, or files already identified by CodeGraph.
- Remember index lag: file watcher updates may be about 500ms behind writes.

## Code Change Workflow

When a task involves modifying code or files, follow this workflow:

### Workspace rule

- Work only inside the assigned project-local task workspace, normally `/Users/xieziling/Desktop/personal_proj/SupportPortal/.worktrees/<thread-slug>`.
- Do not use `~/.config/superpowers/worktrees/...` or `~/.codex/worktrees/...` as the default SupportPortal task workspace.
- Do not create task branches manually from the root workspace. Codex should create them with `scripts/workflow/create_task_worktree.sh <thread-name-or-slug>`.
- If you find the root workspace on a `codex/*` branch, stop and report it instead of continuing.
- Other unrelated `codex/*` branches or `.worktrees/...` task workspaces are not blockers. Continue when the root workspace is clean `main` and your assigned workspace is the expected branch.
- Do not perform extra project-level fixed preflight for ordinary chat or planning; follow platform skill triggers, and run Git workspace checks only for assigned repo edits, handoff, or workspace-safety decisions.

### 1. Confirm assignment

Read `AGENTS.md`, confirm the expected `codex/<thread>` branch and project-local task workspace, and stay inside the assigned workspace. The root workspace at `/Users/xieziling/Desktop/personal_proj/SupportPortal` must remain on clean `main`; do not edit repo-tracked files there. If no task workspace or plan has been assigned, stop and ask Codex or the user for the plan and workspace.

### 2. Plan

Make or follow an explicit implementation plan before writing code. For Claude Code sessions that support plan mode, use:

```text
EnterPlanMode
```

The plan should state target files, intended behavior, verification commands, and any risk or dependency.

### 3. Execute

Implement the approved plan. Keep changes scoped to the task. Avoid unrelated refactors, generated junk files, and broad formatting churn.

### 4. Verify

Run the narrowest task-appropriate verification available in the assigned task workspace. If verification cannot run, explain exactly why and include the command that was attempted.

### 5. Stop before commit

Do not stage for commit unless Codex explicitly requests staged output for review, and still do not commit. Leave the working tree ready for Codex to inspect.

### 6. Handoff to Codex review

Hand off all changes with:

```text
/codex:review
```

Include the implementation plan, changed-file summary, verification evidence, and known risks or skipped checks. After this handoff, wait for Codex review. If Codex finds issues, Codex owns the corrections unless it explicitly delegates another no-commit implementation pass.

## Agent Delegation

### When to use agents

| Agent | Use For |
|---|---|
| `planner` | Complex multi-step features, before writing code |
| `architect` | System design decisions, technology choices |
| `code-reviewer` | Extra local review before Codex review |
| `security-reviewer` | Auth, payments, user input handling |
| `database-reviewer` | Schema changes, query optimization |
| `tdd-guide` | When writing tests first is required |
| `build-error-resolver` | Build, import, or type-check failures |
| `refactor-cleaner` | Dead code removal, deduplication |
| `doc-updater` | Documentation sync with code changes |

### Delegation rules

- Use agents for domain-specific review and implementation subproblems, not for codebase exploration.
- Delegate parallelizable research to multiple agents only when the work is genuinely independent.
- Use the right specialist for each domain.
- Do not use agents for trivial one-line changes.
- Review agent output before handing work to Codex.
- Agent output does not authorize commits, pushes, PRs, merges, finalization, or cleanup.

## Codex Integration

### Commands

| Command | Purpose |
|---|---|
| `/codex:review` | Required handoff for Codex review against the local git diff |
| `/codex:adversarial-review` | Challenging review that questions design choices |
| `/codex:status` | View active and recent Codex jobs |
| `/codex:result <job-id>` | View completed job results |
| `/codex:cancel <job-id>` | Cancel a running job |
| `/codex:setup` | Check Codex CLI, config, and auth status |

### Quick reference

| Scenario | Command |
|---|---|
| Review my completed implementation | `/codex:review` |
| Review and question my design | `/codex:adversarial-review` |
| Large review in background | `/codex:review --background` |
| Check review progress | `/codex:status` |

Do not use `/codex:rescue` unless Codex or the user explicitly asks for it. Claude Code should not use Codex commands to trigger commits, merges, finalization, or cleanup for its own work.
