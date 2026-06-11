from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from tools.schema_design.io_utils import read_json, read_jsonl, write_json
from tools.schema_design.models import StageResult


STOPWORDS = {
    '的',
    '了',
    '在',
    '是',
    '和',
    '及',
    '或',
    '本文件',
    '本标准',
    '规定',
    '符合',
    '满足',
    '要求',
    '按照',
    '根据',
}


def profile_terms(
    chunks_jsonl: Path,
    pattern_inventory_json: Path,
    output_dir: Path,
    ngram_range: tuple[int, int] = (2, 6),
    min_df: int = 2,
    max_df: float = 0.85,
    top_n: int = 500,
) -> StageResult:
    chunks = read_jsonl(chunks_jsonl)
    patterns = read_json(pattern_inventory_json)
    rows = _extract_token_frequencies(chunks, ngram_range=ngram_range, min_df=min_df, top_n=top_n)
    relation_values = {item['value'] for item in patterns.get('relation_triggers', [])}
    numeric_values = {item['value'] for item in patterns.get('numeric_values', [])}

    for row in rows:
        row['term_type_guess'] = classify_term(
            row['term'],
            row['freq'],
            row['tfidf_score'],
            'relation_triggers' if row['term'] in relation_values else None,
            row['term'] in numeric_values,
            bool(re.search(r'[\u4e00-\u9fff]', row['term'])),
        )

    candidate_object_terms = [row for row in rows if row['term_type_guess'] == 'ENTITY']
    candidate_noise_terms = [row for row in rows if row['term_type_guess'] == 'NOISE']
    payload = {
        'top_char_ngrams': rows,
        'top_tfidf_terms': sorted(rows, key=lambda row: row['tfidf_score'], reverse=True)[:top_n],
        'regex_tokens': _regex_tokens(patterns),
        'per_section_terms': _per_section_terms(chunks),
        'candidate_object_terms': candidate_object_terms,
        'candidate_noise_terms': candidate_noise_terms,
        'entity_alignment_candidates': [],
    }
    path = write_json(output_dir / 'term_frequency.json', payload)
    return StageResult(
        output_files={'term_frequency_json': path},
        metrics={
            'term_count': len(rows),
            'candidate_object_term_count': len(candidate_object_terms),
            'candidate_noise_term_count': len(candidate_noise_terms),
        },
    )


def _extract_token_frequencies(
    chunks: list[dict], *, ngram_range: tuple[int, int], min_df: int, top_n: int
) -> list[dict]:
    freq: Counter[str] = Counter()
    doc_freq: Counter[str] = Counter()
    contexts: dict[str, tuple[str, str]] = {}
    for chunk in chunks:
        text = chunk.get('text', '')
        terms = set()
        for term in _candidate_terms(text, ngram_range):
            if term in STOPWORDS:
                continue
            freq[term] += 1
            terms.add(term)
            contexts.setdefault(term, (chunk.get('chunk_id', ''), _context(text, term)))
        for term in terms:
            doc_freq[term] += 1
    rows = []
    for term, count in freq.most_common():
        if doc_freq[term] < min_df:
            continue
        chunk_id, context = contexts[term]
        rows.append(
            {
                'term': term,
                'freq': count,
                'doc_freq': doc_freq[term],
                'tfidf_score': float(count / max(doc_freq[term], 1)),
                'sample_chunk_id': chunk_id,
                'sample_context': context,
                'review_decision': '',
                'schema_candidate': '',
            }
        )
    return rows[:top_n]


def _candidate_terms(text: str, ngram_range: tuple[int, int]) -> list[str]:
    terms = []
    terms.extend(re.findall(r'[\u4e00-\u9fffA-Za-z0-9/.\-]{2,30}', text))
    chinese_runs = re.findall(r'[\u4e00-\u9fff]{2,}', text)
    min_n, max_n = ngram_range
    for run in chinese_runs:
        for size in range(min_n, min(max_n, len(run)) + 1):
            for start in range(0, len(run) - size + 1):
                terms.append(run[start : start + size])
    return [_trim_term(term) for term in terms if _trim_term(term)]


def _trim_term(term: str) -> str:
    return term.strip(' ,.;:，。；：()（）[]【】')


def classify_term(
    term: str,
    freq: int,
    tfidf_score: float,
    pattern_type: str | None,
    is_numeric: bool,
    is_chinese: bool,
) -> str:
    if freq >= 3 and tfidf_score < 0.10 and pattern_type is None:
        return 'NOISE'
    if is_numeric and not is_chinese:
        return 'ATTRIBUTE'
    if pattern_type == 'relation_triggers':
        return 'RELATION_TRIGGER'
    if is_chinese and freq >= 1 and len(term) >= 2:
        if not _looks_like_noun(term):
            return 'ATTRIBUTE'
        return 'ENTITY'
    if freq < 3 and len(term) <= 3:
        return 'NOISE'
    if not is_chinese and len(term) <= 20 and freq >= 1:
        return 'ENTITY'
    return 'UNSURE'


def _looks_like_noun(term: str) -> bool:
    suffixes = (
        '机',
        '器',
        '件',
        '装置',
        '设备',
        '系统',
        '机构',
        '电',
        '压',
        '流',
        '力',
        '度',
        '率',
        '值',
        '量',
        '条件',
        '要求',
        '标准',
        '试验',
        '检验',
        '测试',
        '等级',
        '电阻',
        '电流',
        '温度',
    )
    return any(term.endswith(suffix) for suffix in suffixes) or len(term) >= 4


def _context(text: str, term: str, window: int = 40) -> str:
    index = text.find(term)
    if index == -1:
        return text[: window * 2]
    return text[max(0, index - window) : min(len(text), index + len(term) + window)]


def _regex_tokens(patterns: dict) -> list[dict]:
    rows = []
    for pattern_type, values in patterns.items():
        for item in values:
            row = dict(item)
            row['pattern_type'] = pattern_type
            rows.append(row)
    return rows


def _per_section_terms(chunks: list[dict]) -> list[dict]:
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    global_freq: Counter[str] = Counter()
    for chunk in chunks:
        section = ' / '.join(chunk.get('section_path') or ['unknown'])
        for term in set(_candidate_terms(chunk.get('text', ''), (2, 4))):
            grouped[section][term] += 1
            global_freq[term] += 1
    rows = []
    for section, counter in grouped.items():
        for term, count in counter.most_common(20):
            rows.append(
                {
                    'section_path': section,
                    'term': term,
                    'section_freq': count,
                    'global_freq': global_freq[term],
                    'tfidf_score': float(count / max(global_freq[term], 1)),
                    'is_section_specific': count == global_freq[term],
                }
            )
    return rows
