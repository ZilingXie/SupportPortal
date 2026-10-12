"""Per-source allowlist for the knowledge governance chain (stage 6, plan A).

The stage-6 switches are GLOBAL — once Summary/Review is on, every accepted
source flows into AI processing, which would process real CSD/Solved
material before the observation window clears it. This allowlist adds the
missing middle gear: intake keeps accepting and persisting snapshots
(read-only, auditable), while Summary/Review task creation only happens for
sources explicitly listed in ``KNOWLEDGE_SOURCE_ALLOWLIST``.

Format (whitespace-tolerant, order-insensitive)::

    KNOWLEDGE_SOURCE_ALLOWLIST="csd_issue:CSD-TEST-1, zendesk_ticket:990001"

- Default (unset/empty): NO source is processed — the strictest state; the
  ten-step rollout never processes real sources until they are explicitly
  listed (step 10's "全量放开" is itself an explicit, recorded allowlist
  update).
- Case-bound (case binding) sources use the synthetic key
  ``case_binding:<engineer_case_id>`` so a test case can be allowlisted the
  same way.
- The allowlist gates ONLY task creation (Summary/Review); it never deletes
  or rewrites already-accepted snapshots, promotions, deliveries or audits.
"""

from __future__ import annotations

import os

ALLOWLIST_ENV = "KNOWLEDGE_SOURCE_ALLOWLIST"


def _normalize_entry(entry: str) -> str:
    return str(entry or "").strip().lower()


def parse_allowlist(raw: str | None) -> frozenset[str]:
    """Parse ``type:id`` entries; invalid entries are skipped fail-closed."""
    entries: set[str] = set()
    for chunk in str(raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        source_type, _, source_id = chunk.partition(":")
        source_type = source_type.strip().lower()
        source_id = source_id.strip()
        if not source_type or not source_id or ":" in source_id:
            continue
        entries.add(f"{source_type}:{source_id.lower()}")
    return frozenset(entries)


def allowlist_entries() -> frozenset[str]:
    return parse_allowlist(os.getenv(ALLOWLIST_ENV))


def validate_allowlist(raw: str | None) -> None:
    """Raise ValueError when a non-empty entry cannot be honored.

    ``parse_allowlist`` drops malformed entries fail-closed at runtime; for
    a deployment-pinned allowlist that silent drop is an operator mistake
    (the operator meant to allow a source and configured a dead entry), so
    render-time preflight makes the same condition a hard configuration
    error instead of a quietly smaller allowed set."""
    for chunk in str(raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        source_type, _, source_id = chunk.partition(":")
        if not source_type.strip() or not source_id.strip() or ":" in source_id:
            raise ValueError(
                f"invalid KNOWLEDGE_SOURCE_ALLOWLIST entry {chunk!r}: "
                "entries must be 'type:id' with a non-empty type and id "
                "(id may be the '*' wildcard, e.g. 'csd_issue:*')"
            )


def allowlist_configured() -> bool:
    """True when the allowlist is EXPLICITLY set to a non-empty value.

    An empty/unset allowlist still blocks processing (the default-strict
    contract); this flag distinguishes 'never configured' from 'configured
    with entries' for evidence surfaces."""
    return bool(allowlist_entries())


def source_allowed(source_type: str, source_id: str) -> bool:
    """Is this source eligible for Summary/Review task creation?

    Entries support one wildcard form — ``type:*`` allows every source of
    that type (step 10's full rollout can be expressed as explicit ids OR
    per-type wildcards; both are equally explicit, recorded configuration)."""
    normalized_type = str(source_type or "").strip().lower()
    normalized_id = str(source_id or "").strip().lower()
    entries = allowlist_entries()
    if f"{normalized_type}:{normalized_id}" in entries:
        return True
    return f"{normalized_type}:*" in entries


def case_binding_allowed(engineer_case_id: str) -> bool:
    return source_allowed("case_binding", engineer_case_id)


def allowlist_rejection_reason(source_type: str, source_id: str) -> str | None:
    """Audit-friendly reason when a source is held back (None when allowed)."""
    if source_allowed(source_type, source_id):
        return None
    if not allowlist_entries():
        return (
            "source not processed: KNOWLEDGE_SOURCE_ALLOWLIST is empty "
            "(default-strict; snapshot accepted and retained for later processing)"
        )
    return (
        f"source {source_type}:{source_id} not in KNOWLEDGE_SOURCE_ALLOWLIST "
        "(snapshot accepted and retained)"
    )


__all__ = [
    "ALLOWLIST_ENV",
    "allowlist_configured",
    "allowlist_entries",
    "allowlist_rejection_reason",
    "case_binding_allowed",
    "parse_allowlist",
    "source_allowed",
    "validate_allowlist",
]
