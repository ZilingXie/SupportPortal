---
name: knowledge-review
description: Knowledge-governance review methodology for the Hermes knowledge-review session. Decides, for each case-summary candidate, whether it is knowledge, memory, or a skill change, and whether the right outcome is no_change, merge, supplement, replace, new, or human_review — grounding every decision in the server-provided WeKnora similarity results and never converting missing evidence into a confident write. Human-maintained; the pipeline never edits this skill automatically.
---

# Knowledge Review (Hermes knowledge-governance Review role)

The Review role runs on a dedicated Hermes session after a case Summary
completes. It receives the Knowledge Review Bundle from the server — the
summary packet, case lineage, WeKnora similarity search results per
candidate (or an explicit unavailability marker), and current knowledge
versions — and returns one decision per candidate through the run's JSON
output. It never writes to WeKnora, the wiki, memory, or the case.

## Classification

- `knowledge`: reusable product/troubleshooting knowledge intended for the
  shared knowledge base (WeKnora).
- `memory`: case- or customer-scoped operational memory that belongs to the
  caller-isolated memory store rather than shared knowledge.
- `skill`: a change to a human-maintained troubleshooting skill. Skills are
  never auto-evolved: a skill candidate is `no_change` (library already
  covers it, verified via skills_list/skill_view) or `human_review`.

## Decision procedure (per candidate)

1. Check the candidate's evidence in the summary: no traceable evidence →
   `human_review`.
2. Find the candidate's nearest existing entries in the provided WeKnora
   search results. No usable results for the candidate's type (including the
   unavailability marker) → `human_review`.
3. Compare substance, not wording:
   - existing entry already states it → `no_change` with that target;
   - existing entry is correct but incomplete → `supplement` with proposed
     content limited to the addition;
   - existing entry is partly wrong → `replace` with the corrected full
     content and the conflict named in the rationale;
   - overlapping but separately maintained → `merge` with an explicit merged
     proposal;
   - nothing similar → `new`.
4. Anything conflicting, stale-versioned, cross-type ambiguous, or outside
   the reviewer's evidence → `human_review`. Missing evidence is never a
   confident write.

## Hard rules

- `no_change`/`merge`/`supplement`/`replace` must carry the existing
  `target_object` + `target_version` from the search results; `new` and
  `human_review` must carry none.
- Proposed content is sanitized: no customer-identifying data, credentials,
  internal URLs, or raw conversation.
- Decisions must stay within the summary's candidates — never invent extra
  candidates or decide another case's content.
- Output is English, one JSON object, one decision per candidate.
