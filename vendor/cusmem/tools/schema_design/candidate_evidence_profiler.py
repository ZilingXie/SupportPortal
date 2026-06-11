from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from tools.schema_design.io_utils import read_json, read_jsonl, write_json
from tools.schema_design.models import StageResult


def profile_candidate_evidence(
    normalized_pool: dict[str, Any],
    pages_jsonl: Path,
    chunks_jsonl: Path,
    pattern_inventory_json: Path,
    term_frequency_json: Path,
    topic_md: Path,
    output_dir: Path,
) -> StageResult:
    """Scan documents against the candidate pool to collect statistical evidence.

    Does NOT discover new types. Only profiles existing candidates.
    """
    chunks = read_jsonl(chunks_jsonl)
    patterns = read_json(pattern_inventory_json)
    terms = read_json(term_frequency_json)

    entity_index = normalized_pool.get('entity_type_index', {})
    relation_index = normalized_pool.get('relation_type_index', {})

    # ── 1. Profile entity type candidates ────────────────────────────
    entity_evidence = {}
    for eid, spec in entity_index.items():
        examples = spec.get('examples', [])
        aliases = spec.get('aliases', [])
        if not examples and not aliases:
            entity_evidence[eid] = _empty_entity_evidence(eid)
            continue

        evidence = _profile_entity_candidate(eid, examples, chunks, terms, aliases)
        entity_evidence[eid] = evidence

    # ── 2. Profile relation type candidates ──────────────────────────
    relation_evidence = {}
    for rid, spec in relation_index.items():
        triggers = spec.get('trigger_words', [])
        source_ids = spec.get('source_candidates', [])
        target_ids = spec.get('target_candidates', [])

        evidence = _profile_relation_candidate(
            rid, triggers, source_ids, target_ids,
            entity_index, entity_evidence, chunks,
        )
        relation_evidence[rid] = evidence

    # ── 3. Build output ──────────────────────────────────────────────
    evidence_data = {
        'entity_type_candidates': entity_evidence,
        'relation_type_candidates': relation_evidence,
        'summary': _build_evidence_summary(entity_evidence, relation_evidence),
    }

    write_json(output_dir / 'candidate_pool_evidence.json', evidence_data)
    _write_evidence_markdown(output_dir / 'candidate_pool_evidence.md',
                             entity_evidence, relation_evidence, normalized_pool)

    # Compute metrics
    entity_with_evidence = sum(1 for e in entity_evidence.values()
                               if e.get('evidence_level') != 'none')
    relation_with_evidence = sum(1 for r in relation_evidence.values()
                                 if r.get('evidence_level') != 'none')

    return StageResult(
        output_files={
            'candidate_pool_evidence_json': output_dir / 'candidate_pool_evidence.json',
            'candidate_pool_evidence_md': output_dir / 'candidate_pool_evidence.md',
        },
        metrics={
            'entity_candidates_total': len(entity_evidence),
            'entity_candidates_with_evidence': entity_with_evidence,
            'relation_candidates_total': len(relation_evidence),
            'relation_candidates_with_evidence': relation_with_evidence,
        },
    )


def _profile_entity_candidate(
    eid: str,
    examples: list[str],
    chunks: list[dict[str, Any]],
    terms: dict[str, Any],
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    """Count how often each example/alias appears in chunks, per unique doc_id."""
    all_patterns = list(examples) + (aliases or [])
    matched_terms: list[str] = []
    contexts: list[str] = []
    freq_total = 0
    doc_ids_hit: set[str] = set()

    for pattern in all_patterns:
        pattern_freq = 0
        for chunk in chunks:
            text = chunk.get('text', '')
            count = text.count(pattern)
            if count > 0:
                pattern_freq += count
                doc_ids_hit.add(chunk.get('doc_id', ''))
                if len(contexts) < 5:
                    idx = text.find(pattern)
                    start = max(0, idx - 30)
                    end = min(len(text), idx + len(pattern) + 50)
                    contexts.append(text[start:end].replace('\n', ' '))

        if pattern_freq > 0:
            matched_terms.append(pattern)
            freq_total += pattern_freq

    doc_freq = len(doc_ids_hit)

    # Check term_frequency for additional matches
    obj_terms = terms.get('candidate_object_terms', [])
    for t in obj_terms:
        term_text = t.get('term', '')
        if term_text not in matched_terms:
            for example in examples:
                if example in term_text or term_text in example:
                    matched_terms.append(term_text)
                    freq_total += t.get('freq', 0)

    confidence = min(0.95, (freq_total / max(1, freq_total + 5)) + (doc_freq / max(1, len(chunks))))
    evidence_level = 'high' if freq_total >= 5 else ('low' if freq_total > 0 else 'none')

    return {
        'candidate_id': eid,
        'matched_terms': list(set(matched_terms)),
        'freq_total': freq_total,
        'doc_freq': doc_freq,
        'contexts': contexts[:5],
        'confidence': round(confidence, 4),
        'evidence_level': evidence_level,
    }


def _profile_relation_candidate(
    rid: str,
    triggers: list[str],
    source_ids: list[str],
    target_ids: list[str],
    entity_index: dict[str, Any],
    entity_evidence: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Count trigger word hits and source-target co-occurrence."""
    trigger_hits: dict[str, int] = defaultdict(int)
    contexts: list[str] = []
    cooccurrence = 0

    # Collect source/target examples
    source_examples: list[str] = []
    target_examples: list[str] = []
    for sid in source_ids:
        src_ev = entity_evidence.get(sid, {})
        source_examples.extend(src_ev.get('matched_terms', []))
    for tid in target_ids:
        tgt_ev = entity_evidence.get(tid, {})
        target_examples.extend(tgt_ev.get('matched_terms', []))

    for chunk in chunks:
        text = chunk.get('text', '')

        # Trigger hits
        for tw in triggers:
            count = text.count(tw)
            if count > 0:
                trigger_hits[tw] += count
                if len(contexts) < 5:
                    idx = text.find(tw)
                    start = max(0, idx - 30)
                    end = min(len(text), idx + len(tw) + 80)
                    contexts.append(text[start:end].replace('\n', ' '))

        # Source-target co-occurrence
        if source_examples and target_examples:
            has_source = any(se in text for se in source_examples[:10])
            has_target = any(te in text for te in target_examples[:10])
            if has_source and has_target:
                cooccurrence += 1

    total_triggers = sum(trigger_hits.values())
    confidence = min(0.90, (total_triggers / max(1, total_triggers + 5)) +
                    (cooccurrence / max(1, len(chunks))))

    evidence_level = 'high' if (total_triggers >= 3 or cooccurrence >= 2) else (
        'low' if total_triggers > 0 else 'none'
    )

    return {
        'candidate_id': rid,
        'trigger_hits': dict(trigger_hits),
        'source_target_cooccurrence': cooccurrence,
        'contexts': contexts[:5],
        'confidence': round(confidence, 4),
        'evidence_level': evidence_level,
    }


def _empty_entity_evidence(eid: str) -> dict[str, Any]:
    return {
        'candidate_id': eid,
        'matched_terms': [],
        'freq_total': 0,
        'doc_freq': 0,
        'contexts': [],
        'confidence': 0.0,
        'evidence_level': 'none',
    }


def _build_evidence_summary(
    entity_evidence: dict[str, Any],
    relation_evidence: dict[str, Any],
) -> str:
    lines = ['# Candidate Pool Evidence Summary\n']
    lines.append('## Entity Type Candidates\n')
    for eid, ev in entity_evidence.items():
        level = ev['evidence_level']
        icon = {'high': '✓', 'low': '?', 'none': '✗'}.get(level, '?')
        lines.append(
            f'- {icon} **{eid}**: freq={ev["freq_total"]}, '
            f'matched={ev["matched_terms"][:5]}, confidence={ev["confidence"]:.0%}'
        )
    lines.append('\n## Relation Type Candidates\n')
    for rid, ev in relation_evidence.items():
        level = ev['evidence_level']
        icon = {'high': '✓', 'low': '?', 'none': '✗'}.get(level, '?')
        lines.append(
            f'- {icon} **{rid}**: triggers={dict(list(ev["trigger_hits"].items())[:3])}, '
            f'cooccur={ev["source_target_cooccurrence"]}, confidence={ev["confidence"]:.0%}'
        )
    return '\n'.join(lines)


def _write_evidence_markdown(
    path: Path,
    entity_evidence: dict[str, Any],
    relation_evidence: dict[str, Any],
    normalized_pool: dict[str, Any],
) -> None:
    entity_index = normalized_pool.get('entity_type_index', {})
    relation_index = normalized_pool.get('relation_type_index', {})

    lines = ['# Candidate Pool Evidence Report\n']
    lines.append('## Entity Type Evidence\n')

    for eid, ev in entity_evidence.items():
        spec = entity_index.get(eid, {})
        lines.append(f'### {eid} ({spec.get("name", "")})')
        lines.append(f'- Evidence level: {ev["evidence_level"]}')
        lines.append(f'- Frequency: {ev["freq_total"]}')
        lines.append(f'- Doc freq: {ev["doc_freq"]}')
        lines.append(f'- Matched terms: {ev["matched_terms"][:10]}')
        lines.append(f'- Confidence: {ev["confidence"]:.0%}')
        if ev['contexts']:
            lines.append('- Sample contexts:')
            for ctx in ev['contexts'][:3]:
                lines.append(f'  > {ctx[:150]}')
        lines.append('')

    lines.append('## Relation Type Evidence\n')
    for rid, ev in relation_evidence.items():
        spec = relation_index.get(rid, {})
        lines.append(f'### {rid} ({spec.get("name", "")})')
        lines.append(f'- Evidence level: {ev["evidence_level"]}')
        lines.append(f'- Trigger hits: {ev["trigger_hits"]}')
        lines.append(f'- Source-target co-occurrence: {ev["source_target_cooccurrence"]}')
        lines.append(f'- Confidence: {ev["confidence"]:.0%}')
        if ev['contexts']:
            lines.append('- Sample contexts:')
            for ctx in ev['contexts'][:3]:
                lines.append(f'  > {ctx[:150]}')
        lines.append('')

    path.write_text('\n'.join(lines), encoding='utf-8')
