---
name: skill-stocktake
description: Audit Claude skills and commands for quality. Supports Quick Scan (changed skills only) and Full Stocktake modes with sequential subagent batch evaluation.
origin: ECC
---

# skill-stocktake

Audits all Claude skills and commands using a quality checklist and AI holistic judgment.

## Modes

| Mode | Trigger | Duration |
|------|---------|---------|
| Quick Scan | `results.json` exists (default) | 5-10 min |
| Full Stocktake | `results.json` absent, or `/skill-stocktake full` | 20-30 min |

**Results cache:** `~/.claude/skills/skill-stocktake/results.json`

## Scope

Targets: `~/.claude/skills/` (global) and `{cwd}/.claude/skills/` (project-level).

## Quick Scan Flow

1. Read `results.json`
2. Run diff script to find changed skills since last run
3. If no changes: report and stop
4. Re-evaluate only changed files
5. Carry forward unchanged skills from previous results
6. Output diff only

## Full Stocktake Flow

### Phase 1 — Inventory

Scan skill directories, extract frontmatter, collect mtimes.

### Phase 2 — Quality Evaluation

Evaluate each skill against this checklist:

- [ ] Content overlap with other skills checked
- [ ] Overlap with MEMORY.md / CLAUDE.md checked
- [ ] Freshness of technical references verified
- [ ] Usage frequency considered

Verdicts:

| Verdict | Meaning |
|---------|---------|
| Keep | Useful and current |
| Improve | Worth keeping, but specific improvements needed |
| Update | Referenced technology is outdated |
| Retire | Low quality, stale, or cost-asymmetric |
| Merge into [X] | Substantial overlap with another skill |

### Phase 3 — Summary Table

| Skill | 7d use | Verdict | Reason |

### Phase 4 — Consolidation

1. **Retire / Merge**: present justification before confirming
2. **Improve**: present specific improvement suggestions
3. **Update**: present updated content with sources

## Evaluation Dimensions

- **Actionability**: code examples, commands, or steps that let you act immediately
- **Scope fit**: name, trigger, and content are aligned
- **Uniqueness**: value not replaceable by MEMORY.md / CLAUDE.md / another skill
- **Currency**: technical references work in the current environment
