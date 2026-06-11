from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tools.schema_design.llm_client import LLMClient

# ── Universal Role Constants ──────────────────────────────────────────────
# These are deliberately domain-agnostic — they describe what a candidate
# term IS in terms of its semantic role, not what domain it belongs to.

UNIVERSAL_ROLES = {
    'ObjectCandidate': {
        'label': '对象候选',
        'description': '物理实体、设备、部件、系统、材料等可被操作或描述的对象',
        'typical_suffixes': ('机', '器', '件', '装置', '设备', '系统', '机构', '开关', '杆件', '闭锁',
                           '板', '盒', '箱', '柜', '门', '窗', '管', '阀', '泵', '电机'),
    },
    'MetricCandidate': {
        'label': '指标/参数候选',
        'description': '可量化或可测量的性能指标、技术参数',
        'typical_suffixes': ('力', '度', '率', '值', '量', '电压', '电流', '电阻', '温度',
                           '时间', '频率', '距离', '动程', '间隙', '转速', '扭矩', '功率'),
    },
    'ValueCandidate': {
        'label': '数值/单位候选',
        'description': '裸数值、带单位的测量值、范围表达式',
    },
    'RuleCandidate': {
        'label': '规则/约束候选',
        'description': '阈值规则、约束条件、合规要求表述',
    },
    'ActionCandidate': {
        'label': '动作/流程候选',
        'description': '检验、测试、操作、处置等动作或流程',
        'typical_suffixes': ('试验', '检验', '测试', '检查', '测量', '校准', '调试', '维护',
                           '安装', '拆卸', '更换', '修理', '防护', '包装', '运输', '贮存'),
    },
    'DocumentCandidate': {
        'label': '文档/标准候选',
        'description': '标准文件、规范、法规、合同等文档实体',
    },
    'ActorCandidate': {
        'label': '组织/人员候选',
        'description': '机构、公司、部门、人员角色等责任主体',
        'typical_suffixes': ('公司', '局', '部门', '委员会', '中心', '研究院', '所', '厂'),
    },
    'EventCandidate': {
        'label': '事件/记录候选',
        'description': '事件、报警、工单、记录等具有时间属性的条目',
    },
    'LocationCandidate': {
        'label': '地点/位置候选',
        'description': '地点、区域、位置标识',
    },
    'TimeCandidate': {
        'label': '时间候选',
        'description': '时间点、时间段、频率、日期',
    },
    'EvidenceCandidate': {
        'label': '来源/条款候选',
        'description': '章节号、条款号、引用来源等证据信息，通常不作为业务实体',
    },
    'RelationTrigger': {
        'label': '关系触发词',
        'description': '指示语义关系的动词或词组',
    },
    'NoiseCandidate': {
        'label': '噪声候选',
        'description': '乱码、OCR 碎片、无意义字母组合等应过滤的内容',
    },
}


@dataclass
class CandidateRoleItem:
    """A single candidate term classified into a universal role."""
    text: str
    role: str  # one of UNIVERSAL_ROLES keys
    confidence: float
    freq: int
    doc_freq: int = 0
    evidence_contexts: list[str] = field(default_factory=list)
    source: str = ''  # 'pattern', 'term_freq', 'llm'
    should_be_entity: bool = False
    should_be_attribute: bool = False
    should_be_relation_trigger: bool = False
    should_be_filtered: bool = False


# ── Rule-based classification ──────────────────────────────────────────────

# Entity-like roles: these become entity types
ENTITY_ROLES = {
    'ObjectCandidate', 'MetricCandidate', 'DocumentCandidate',
    'ActorCandidate', 'ActionCandidate', 'EventCandidate',
    'LocationCandidate',
}

# Attribute-like roles: these become properties on entities
ATTRIBUTE_ROLES = {'ValueCandidate', 'TimeCandidate'}

# Should never be entities
FILTERED_ROLES = {'NoiseCandidate', 'EvidenceCandidate'}

# Relation triggers
TRIGGER_ROLES = {'RelationTrigger'}


def classify_candidate_roles(
    candidate_terms: list[dict[str, Any]],
    pattern_items: dict[str, list[dict[str, Any]]],
    llm: LLMClient | None = None,
) -> tuple[list[CandidateRoleItem], list[CandidateRoleItem]]:
    """Classify candidate terms into universal roles.

    Returns (classified_items, relation_triggers).
    Relation triggers are separated because they inform edge types, not entity types.
    """
    # Pre-compute pattern type lookup
    pattern_lookup: dict[str, str] = {}
    for category, items in pattern_items.items():
        for item in items:
            val = item.get('value', '').strip()
            if val:
                pattern_lookup[val] = category

    classified: list[CandidateRoleItem] = []
    triggers: list[CandidateRoleItem] = []

    for term_entry in candidate_terms:
        term = term_entry.get('term', '').strip()
        freq = term_entry.get('freq', 0)
        doc_freq = term_entry.get('doc_freq', 0)
        pattern_type = term_entry.get('pattern_type', None)

        if not term:
            continue

        # ── Rule-based classification ──────────────────────────────
        role, confidence = _classify_by_rules(term, freq, pattern_type, pattern_lookup)

        item = CandidateRoleItem(
            text=term,
            role=role,
            confidence=confidence,
            freq=freq,
            doc_freq=doc_freq,
            evidence_contexts=term_entry.get('sample_contexts', [])[:3],
            source='term_freq' if pattern_type is None else f'pattern:{pattern_type}',
            should_be_entity=(role in ENTITY_ROLES),
            should_be_attribute=(role in ATTRIBUTE_ROLES),
            should_be_relation_trigger=(role in TRIGGER_ROLES),
            should_be_filtered=(role in FILTERED_ROLES),
        )

        if role == 'RelationTrigger':
            triggers.append(item)
        else:
            classified.append(item)

    # ── LLM refinement for low-confidence items ─────────────────────
    if llm is not None:
        low_conf = [c for c in classified if c.confidence < 0.7 and c.freq >= 2]
        if low_conf:
            try:
                refined = _llm_refine_roles(low_conf, llm)
                for item in classified:
                    if item in low_conf:
                        new_role = refined.get(item.text)
                        if new_role and new_role in UNIVERSAL_ROLES:
                            item.role = new_role
                            item.confidence = 0.75
                            item.should_be_entity = (new_role in ENTITY_ROLES)
                            item.should_be_attribute = (new_role in ATTRIBUTE_ROLES)
                            item.should_be_filtered = (new_role in FILTERED_ROLES)
            except Exception:
                pass

    return classified, triggers


def _classify_by_rules(
    term: str, freq: int,
    pattern_type: str | None,
    pattern_lookup: dict[str, str],
) -> tuple[str, float]:
    """Pure rule-based role classification. Returns (role, confidence)."""

    # 1. Pattern-based classification (highest confidence)
    if pattern_type:
        mapping = {
            'standards': ('DocumentCandidate', 0.95),
            'numeric_values': ('ValueCandidate', 0.90),
            'ratings': ('ValueCandidate', 0.85),  # IP54, V-2 are classified values
            'organizations': ('ActorCandidate', 0.95),
            'persons': ('ActorCandidate', 0.90),
            'sections': ('EvidenceCandidate', 0.95),
            'dates': ('TimeCandidate', 0.95),
            'relation_triggers': ('RelationTrigger', 0.95),
        }
        if pattern_type in mapping:
            return mapping[pattern_type]

    # 2. Noise detection
    if _is_noise(term, freq):
        return ('NoiseCandidate', 0.85)

    # 3. Evidence/section detection
    if re.match(r'^(\d+(?:\.\d+)*|[第].*[章节条])$', term):
        return ('EvidenceCandidate', 0.90)

    # 4. Chinese noun suffix heuristics
    if re.search(r'[一-鿿]', term):
        # First: check if this looks like an n-gram sentence fragment (not a real term)
        if _is_ngram_fragment(term):
            return ('NoiseCandidate', 0.75)

        # Check suffixes against each role
        for role_key in ('ObjectCandidate', 'MetricCandidate', 'ActionCandidate', 'ActorCandidate'):
            role_def = UNIVERSAL_ROLES[role_key]
            for suffix in role_def.get('typical_suffixes', ()):
                if term.endswith(suffix) and len(term) >= len(suffix) + 1:
                    return (role_key, 0.80)

        # Time patterns
        if re.search(r'[年月日时分秒周]', term):
            return ('TimeCandidate', 0.80)

        # After filtering fragments: long Chinese terms without clear suffix → Object
        if len(term) >= 3 and freq >= 2:
            return ('ObjectCandidate', 0.60)
        elif len(term) >= 2 and freq >= 3:
            return ('ObjectCandidate', 0.50)

    # 5. Document references (standards-like patterns)
    if re.match(r'^(GB/T|GB|ISO|IEC|TB|Q/|JJG|JJF)', term):
        return ('DocumentCandidate', 0.95)

    # 6. Numeric value detection
    if re.match(r'^\d+(?:\.\d+)?\s*(?:[°℃℉]|[A-Za-z]+|[一-鿿]*)$', term):
        return ('ValueCandidate', 0.80)

    # Default: low-confidence Object
    return ('ObjectCandidate', 0.30)


def _is_ngram_fragment(term: str) -> bool:
    """Detect Chinese n-gram fragments that are slices of sentences, not real terms.

    Patterns like:
    - "应符合产品标" → fragment of "应符合产品标准"
    - "级的规定" → fragment of "等级的规定"
    - "品标准的规定" → fragment of "产品标准的规定"
    - "两个及两个以" → fragment of "两个及两个以上"
    """
    # Fragment starters: function words / partial word starts that indicate slicing
    fragment_starters = (
        '应符', '符合', '合产', '品标', '准的', '的规', '规定', '级的规定',
        '两个', '个及', '及两', '两个', '个以', '以上', '以上共',
        '的电', '的动', '装置', '置的',
        '不应', '应大', '大于', '应能', '能承',
        '规定', '定进', '进行',
    )
    # Fragment enders: particles/function words that shouldn't end a real term
    fragment_enders = ('的', '以', '共', '和', '或', '及', '应', '合', '品', '置', '定', '规', '能')

    if any(term.startswith(s) for s in fragment_starters):
        return True
    if term.endswith(fragment_enders) and len(term) <= 6:
        return True
    # Pure function-word composition
    if all(c in '的应以符合规定进行大于小于等于和或及不' for c in term):
        return True
    return False


def _is_noise(term: str, freq: int) -> bool:
    """Detect noise terms that should be filtered."""
    # OCR garbage
    if '�' in term or any(0xE000 <= ord(c) <= 0xF8FF for c in term):
        return True
    # Single-letter fragments (common in OCR splitting)
    if re.match(r'^[A-Za-z]{1,2}$', term) and freq <= 3:
        return True
    # Control chars
    if any(ord(c) < 0x20 and ord(c) not in (9, 10, 13) for c in term):
        return True
    # Encoding artifacts
    if re.match(r'^[&＃@]\d*$', term):
        return True
    # Isolated numbers with low freq
    if re.match(r'^\d+$', term) and freq <= 3:
        return True
    return False


def _llm_refine_roles(
    items: list[CandidateRoleItem],
    llm: LLMClient,
) -> dict[str, str]:
    """Use LLM to refine low-confidence role classifications."""
    roles_desc = '\n'.join(
        f'- {k}: {v["label"]} — {v["description"]}'
        for k, v in UNIVERSAL_ROLES.items()
    )
    items_text = '\n'.join(
        f'{i.text} (freq={i.freq}, current_role={i.role}, conf={i.confidence:.0%})'
        for i in items[:30]
    )

    system = '你是一个语料分析专家。你的任务是为候选项分配通用语义角色。只输出 JSON。'
    user = (
        '## 通用角色定义\n'
        f'{roles_desc}\n\n'
        '## 需要重新分类的候选项\n'
        f'{items_text}\n\n'
        '输出 JSON: {"item_text": "role_key", ...}\n'
        '注意：如果某项明显是噪声/OCR碎片→NoiseCandidate；如果是章节编号→EvidenceCandidate'
    )

    try:
        result = llm.chat_json(system, user)
        if isinstance(result, dict):
            return {k: v for k, v in result.items() if v in UNIVERSAL_ROLES}
    except Exception:
        pass
    return {}
