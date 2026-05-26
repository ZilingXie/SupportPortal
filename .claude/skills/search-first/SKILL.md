---
name: search-first
description: Research before coding — find existing solutions, libraries, and patterns before writing custom implementation code.
origin: ECC
---

# Search-First Workflow

Systematized workflow for finding existing solutions before writing custom implementation code.

## When to Activate

- Starting new features that likely have existing solutions
- Adding dependencies or integrations
- User asks to "add X feature" or "integrate with Y"
- Before creating new utilities or helper functions

## Workflow

### Step 0: Check Tool Availability

Verify what search channels are available (repository search, package registries, GitHub CLI, MCP/docs).

### Step 1: Define Requirements

What functionality is needed and what framework constraints exist.

### Step 2: Parallel Search

Search across registries (npm/PyPI), MCP/skills, and GitHub/web simultaneously.

### Step 3: Score Candidates

Rate on: functionality match, maintenance status, community size, documentation quality, license, dependency count.

### Step 4: Decision Matrix

| Situation | Decision |
|---|---|
| Exact match, well-maintained | Adopt |
| Partial match (80%+), active community | Extend/Wrap |
| Multiple weak matches that can compose | Compose |
| Nothing suitable | Build custom |

### Step 5: Implement

Install and configure (Adopt), wrap with custom logic (Extend), or write minimal custom code (Build).

## Anti-Patterns

1. **Jumping to code** — writing custom implementation before checking if solutions exist
2. **Ignoring MCP tools** — not checking available MCP servers for relevant capabilities
3. **Silent skipping** — dismissing search without documenting what was checked
4. **Over-customizing** — wrapping a library when direct adoption would suffice
5. **Dependency bloat** — adding heavy dependencies for trivial functionality

## Integration Points

- Works with `planner` agent: search-first during planning phase
- Works with `architect` agent: verify technology choices before finalizing design
- Works with `iterative-retrieval` skill: refine codebase searches progressively
