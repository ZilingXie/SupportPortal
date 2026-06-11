from __future__ import annotations

from collections import defaultdict
from typing import Any

from tools.schema_design.role_tagging import CandidateRoleItem, UNIVERSAL_ROLES


def build_role_clusters(
    role_items: list[CandidateRoleItem],
    relation_triggers: list[CandidateRoleItem],
) -> dict[str, Any]:
    """Group candidates by role and then cluster within each role.

    Returns a structured dict ready for Stage 6 LLM schema induction:
    {
      "role_clusters": {
        "ObjectCandidate": {
          "role_label": "对象候选",
          "description": "...",
          "count": N,
          "entries": [{"text": "...", "freq": N, ...}],
          "sub_clusters": [{"label": "...", "entries": [...], "rationale": "..."}]
        },
        ...
      },
      "unassigned": [...],
      "relation_triggers": [...],
      "cluster_summary": "summary text for LLM prompt"
    }
    """
    # Group by role
    by_role: dict[str, list[CandidateRoleItem]] = defaultdict(list)
    for item in role_items:
        by_role[item.role].append(item)

    role_clusters = {}
    for role_key, role_def in UNIVERSAL_ROLES.items():
        entries = by_role.get(role_key, [])
        if not entries:
            continue

        # Sort by freq descending
        entries.sort(key=lambda x: (-x.freq, x.text))

        # Sub-cluster within role
        sub_clusters = _sub_cluster(entries)

        role_clusters[role_key] = {
            'role_label': role_def['label'],
            'description': role_def['description'],
            'count': len(entries),
            'entries': [
                {
                    'text': e.text,
                    'freq': e.freq,
                    'confidence': e.confidence,
                    'contexts': e.evidence_contexts[:2],
                }
                for e in entries[:30]  # top 30 per role
            ],
            'sub_clusters': sub_clusters,
        }

    # Relation triggers separate
    trigger_summary = [
        {'text': t.text, 'freq': t.freq, 'contexts': t.evidence_contexts[:2]}
        for t in sorted(relation_triggers, key=lambda x: -x.freq)[:20]
    ]

    # Build summary for LLM
    summary_lines = ['# Candidate Role Clusters\n']
    for role_key, data in role_clusters.items():
        top_terms = ', '.join(e['text'] for e in data['entries'][:8])
        summary_lines.append(
            f'## {role_key} ({data["role_label"]}) — {data["count"]} 个候选项'
        )
        summary_lines.append(f'Top: {top_terms}')
        if data['sub_clusters']:
            for sc in data['sub_clusters'][:3]:
                terms = ', '.join(sc['entries'][:5])
                summary_lines.append(f'  - {sc["label"]}: {terms}')

    return {
        'role_clusters': role_clusters,
        'relation_triggers': trigger_summary,
        'cluster_summary': '\n'.join(summary_lines),
    }


def _sub_cluster(
    entries: list[CandidateRoleItem],
) -> list[dict[str, Any]]:
    """Create sub-clusters within a role based on text similarity heuristics."""
    if len(entries) <= 3:
        return []

    # Simple suffix-based grouping
    by_suffix: dict[str, list[CandidateRoleItem]] = defaultdict(list)
    for e in entries:
        suffix = ''
        text = e.text
        for s_len in (3, 2, 1):
            if len(text) >= s_len:
                candidate = text[-s_len:]
                # Check if at least 2 entries share this suffix
                count = sum(1 for ee in entries if ee.text.endswith(candidate))
                if count >= 2:
                    suffix = candidate
                    break
        by_suffix[suffix or text[-1:]].append(e)

    clusters = []
    for suffix, items in by_suffix.items():
        if len(items) >= 2:
            # Generate a label for the sub-cluster
            exemplars = [i.text for i in items[:3]]
            label = f'{exemplars[0]}...' if len(exemplars) > 1 else exemplars[0]
            clusters.append({
                'label': label,
                'entries': [i.text for i in items],
                'rationale': f'共享词尾 "{suffix}" 的 {len(items)} 个候选项',
            })

    # Sort clusters by size
    clusters.sort(key=lambda c: -len(c['entries']))
    return clusters[:5]
