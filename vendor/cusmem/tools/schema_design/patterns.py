from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from tools.schema_design.io_utils import read_jsonl, write_json
from tools.schema_design.models import StageResult


PATTERNS = {
    'standards': [
        re.compile(r'\b(?:GB/T|GB|GB/Z|ISO|IEC|EN|ASTM|TB|JT|YD|IEEE)\s*/?\s*[A-Z0-9.\-—]+'),
        re.compile(r'\b\d{4,6}(?:\.\d+){0,2}[—\-]\d{2,4}\b'),
    ],
    'sections': [
        re.compile(r'第\s*[一二三四五六七八九十百0-9]+\s*[章节条]'),
        re.compile(r'\b(?:[A-Z]|\d+)(?:\.\d+){1,4}\b'),
        re.compile(r'附录\s*[A-ZＡ-Ｚ]'),
    ],
    'numeric_values': [
        re.compile(r'[<>≤≥=]?[\-−]?\d+(?:\.\d+)?\s*(?:℃|°C|V|kV|mV|A|mA|Hz|kHz|N|kN|Pa|kPa|MPa|mm|cm|m|s|ms|min|h|次|%|Ω|MΩ|kΩ|W|kW|g|kg|r/min|rpm|dB)'),
        re.compile(r'\d+(?:\.\d+)?\s*[±~～]\s*\d+(?:\.\d+)?'),
        re.compile(r'[<>≤≥]\s*\d+(?:\.\d)?\s*%'),
    ],
    'ratings': [
        re.compile(r'\bIP\s*\d{2}[A-Z]?\b'),
        re.compile(r'[A-Z]\s*级'),
        re.compile(r'\bV-?\d\b'),
    ],
    'dates': [
        re.compile(r'\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日'),
        re.compile(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}'),
    ],
    'organizations': [
        re.compile(r'[一-鿿A-Za-z0-9（）()]{2,30}(?:公司|研究院|委员会|协会|中心|大学|集团|部门|机构|实验室|设计院|工程局|铁路局|标准化技术委员会)'),
    ],
    'persons': [
        re.compile(r'(?:主要起草人|起草人|主审|审核|批准)\s*[：:]\s*([^。\n]{5,200})'),
    ],
    'relation_triggers': [
        re.compile(r'(规定|确定|明确|限定|界定|约定|指定)'),
        re.compile(r'(引用|参见|参照|依照|按照|根据|依据|遵循|符合|满足)'),
        re.compile(r'(替代|代替|取代|废除|废止)'),
        re.compile(r'(提出|归口|起草|参编|主编|负责起草)'),
        re.compile(r'(应符合|应满足|不应低于|不应超过|不宜)'),
        re.compile(r'(包括|包含|分为|分成|适用于|用于)'),
    ],
}


def profile_patterns(chunks_jsonl: Path, output_dir: Path) -> StageResult:
    chunks = read_jsonl(chunks_jsonl)
    inventory = {name: _extract_patterns(chunks, name) for name in PATTERNS}
    inventory['ocr_suspects'] = []
    path = write_json(output_dir / 'pattern_inventory.json', inventory)
    return StageResult(
        output_files={'pattern_inventory_json': path},
        metrics={f'{name}_count': len(rows) for name, rows in inventory.items()},
    )


def _extract_patterns(chunks: list[dict], pattern_name: str) -> list[dict]:
    patterns = PATTERNS[pattern_name]
    counter: Counter[str] = Counter()
    examples: dict[str, dict] = {}
    for chunk in chunks:
        text = chunk.get('text', '')
        for pattern in patterns:
            for match in pattern.finditer(text):
                value = match.group(1) if pattern_name == 'persons' and match.groups() else match.group(0)
                value = value.strip()
                if not value:
                    continue
                counter[value] += 1
                examples.setdefault(
                    value,
                    {
                        'value': value,
                        'sample_chunk_id': chunk.get('chunk_id', ''),
                        'sample_context': text[max(0, match.start() - 60) : min(len(text), match.end() + 60)],
                        'page_start': chunk.get('page_start', 0),
                        'match_start': match.start(),
                        'match_end': match.end(),
                        'pattern_used': pattern.pattern[:80],
                        'is_excluded': False,
                        'review_decision': '',
                    },
                )
    rows = []
    for value, count in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
        row = examples[value]
        row['count'] = count
        rows.append(row)
    return rows
