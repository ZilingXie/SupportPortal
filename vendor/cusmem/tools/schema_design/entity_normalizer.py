from __future__ import annotations

import re
from typing import Any


def normalize_entity_name(name: str) -> tuple[str, bool, str]:
    """Normalize entity names that are too long or contain value information.

    Returns (normalized_name, was_normalized, reason).

    Handles patterns like:
    - "额定转换力为 2.5 kN" → "额定转换力" (value attached to entity)
    - "动作杆动程为 220 mm" → "动作杆动程"
    - "转换时间 7s" → "转换时间" (threshold/classification context)
    - "第4.1条" → filtered (evidence/source clause)
    """
    original = name.strip()

    # 1. Evidence/section patterns → mark as non-entity
    if re.match(r'^(第\s*[0-9一二三四五六七八九十百]+|[0-9]+(?:\.[0-9]+)*)\s*[章节条款]?$', original):
        return ('', True, 'evidence_clause')

    # 2. "X 为 Y unit" or "X Yunit" pattern → extract X (entity name), Y is value
    m = re.match(r'^(.+?)\s*(?:为|是|不大于|不小于|大于|小于|等于|=|≥|≤|>|<|±)\s*[\d.]+[\s]*[^\s]*$', original)
    if m:
        base = m.group(1).strip()
        if len(base) >= 2 and len(base) < len(original) * 0.7:
            return (base, True, 'value_attached')
    # Also handle "X Nunit" without comparator (e.g., "转换时间 7s")
    m = re.match(r'^(.+?)\s+\d+(?:\.\d+)?\s*(?:[°℃℉]|[A-Za-z]+|[%])$', original)
    if m:
        base = m.group(1).strip()
        if len(base) >= 2 and len(base) < len(original) * 0.7:
            return (base, True, 'value_attached')

    # 3. "X should Y" → X is candidate entity
    m = re.match(r'^(.+?)\s*(?:应|不应|应满足|应符合|应达到|不应超过|不应低于)', original)
    if m:
        base = m.group(1).strip()
        if len(base) >= 2 and len(base) < len(original) * 0.7:
            return (base, True, 'requirement_prefix')

    # 4. Long entity name (ratio check): if entity looks like a sentence
    if len(original) > 15 and _looks_like_sentence(original):
        # Try to extract the core noun phrase
        core = _extract_core_noun(original)
        if core and len(core) >= 2 and len(core) < len(original) * 0.8:
            return (core, True, 'sentence_as_entity')

    # 5. Numeric-only → not an entity
    if re.match(r'^[\d.]+\s*[^\s一-鿿]*$', original):
        return ('', True, 'numeric_only')

    # 6. OCR fragment → bad entity
    if '�' in original or re.match(r'^[A-Za-z]{1,2}$', original):
        return ('', True, 'ocr_fragment')

    return (original, False, '')


def _looks_like_sentence(text: str) -> bool:
    """Heuristic: does text look like a sentence rather than an entity name?"""
    indicators = [
        r'为[\d.]',       # "X 为 2.5"
        r'应[满足符合]',    # "X 应满足"
        r'不大于|不小于',    # "X 不大于"
        r'按.*进行',        # "按 X 进行"
        r'第[一二三]',       # "第X章"
    ]
    return any(re.search(p, text) for p in indicators)


def _extract_core_noun(text: str) -> str:
    """Extract the core noun from a verbose entity name."""
    # Remove prefixes like "按", "对", "对于"
    text = re.sub(r'^(按|对|对于|关于|根据|依照|按照)\s*', '', text)
    # Remove suffixes like "的规定", "的要求", "进行"
    text = re.sub(r'\s*(的规定|的要求|进行|执行|实施|试验|检测)+$', '', text)
    # Try to find the rightmost noun suffix
    noun_suffixes = [
        '转辙机', '电动机', '装置', '设备', '系统', '机构', '开关',
        '转换力', '动程', '频率', '电压', '电流', '电阻', '温度',
        '试验', '检验', '测试', '标准',
    ]
    for ns in sorted(noun_suffixes, key=len, reverse=True):
        if ns in text:
            idx = text.rindex(ns)
            # Take from the word boundary before the suffix
            start = max(0, idx - 10)
            prefix = text[start:idx + len(ns)]
            # Clean leading punctuation/spaces
            prefix = re.sub(r'^[,\s，、。；;：:]+', '', prefix)
            return prefix
    return ''


def compute_long_entity_ratio(entities: list[dict[str, Any]]) -> float:
    """What fraction of entity names appear too long / sentence-like?"""
    if not entities:
        return 0.0
    long_count = 0
    for e in entities:
        name = e.get('name', '')
        if len(name) > 15 or _looks_like_sentence(name):
            long_count += 1
    return long_count / len(entities)


def compute_value_as_entity_ratio(entities: list[dict[str, Any]]) -> float:
    """What fraction of entities look like values rather than objects?"""
    if not entities:
        return 0.0
    value_count = 0
    for e in entities:
        name = e.get('name', '')
        if re.match(r'^[\d.]+\s*[^\s一-鿿]*$', name) or _looks_like_sentence(name):
            value_count += 1
    return value_count / len(entities)
