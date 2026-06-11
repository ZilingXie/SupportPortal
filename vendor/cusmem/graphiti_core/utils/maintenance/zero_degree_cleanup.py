"""Post-ingestion zero-degree entity cleanup.

Removes zero-degree entities that match noise patterns: deep section numbers,
catalog metadata, and isolated parameter values. Entities that pass all
filters are preserved — they may be genuinely useful even without edges.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ── Section depth filter ──────────────────────────────────────────────────

# Section patterns like "5.10.1", "7.1.2.3", "A.1.2"
_SECTION_PATTERN = re.compile(r'^(?:[A-Za-z]|\d+)(?:\.\d+)+$')

# Chinese-numbered sections like "附录A", "第5章"
_CHINESE_SECTION_PATTERN = re.compile(r'^(附录|第)[A-Za-z\d]+(章|节|条)?$')


def _is_deep_section(name: str, max_depth: int = 2) -> bool:
    """Return True if name is a section number deeper than max_depth levels.

    Depth counts dot-separated levels, treating a leading letter (A-Z) as a level.
    Depth 2: "5.2", "A.1" (section — keep)
    Depth 3+: "5.10.1", "A.1.2" (sub-sub-section — filter)
    """
    name = name.strip()
    if not _SECTION_PATTERN.match(name):
        return False
    # Count the letter/prefix as one level
    # "A.1.2" → 3 levels, "5.2" → 2 levels, "5.10.1" → 3 levels
    depth = len(name.split('.'))
    return depth > max_depth


# ── Catalog metadata filter ───────────────────────────────────────────────

_CATALOG_PATTERNS = [
    re.compile(r'^ICS\s+\d', re.IGNORECASE),           # ICS 45.020
    re.compile(r'^CCS\s+\w', re.IGNORECASE),            # CCS S 61
    re.compile(r'^[A-Z]\s+\d{2,}$', re.IGNORECASE),    # S 61 (single letter + space + digits)
    re.compile(r'^\d{4}-\d{2}-\d{2}$'),                 # 2019-05-10 (date)
    re.compile(r'^\d{4}年\d{1,2}月\d{1,2}日$'),         # 2019年5月10日
]


def _is_catalog_metadata(name: str) -> bool:
    """Return True if name looks like catalog metadata (ICS, CCS, dates, etc)."""
    name = name.strip()
    return any(p.match(name) for p in _CATALOG_PATTERNS)


# ── Isolated parameter value filter ───────────────────────────────────────

_PARAMETER_VALUE_PATTERNS = [
    re.compile(r'^\d+\.?\d*\s*(MΩ|kΩ|Ω|MQ|mm|cm|m|s|ms|N|kN|MPa|V|kV|A|mA|W|kW|Hz|kHz|°C|℃|g|kg|min|h)$'),
    re.compile(r'^\d+\.?\d*\s*[±]\s*\d+'),              # 170 ± 5
    re.compile(r'^\d+\.?\d*\s*(~|～)\s*\d+\.?\d*'),     # 170 ~ 200
    re.compile(r'^[表图]\d{1,3}$'),                       # 表4, 图1
]

# OCR artifacts of approximate values: "相当于 15", "相当于 0.5&"
_OCR_APPROX_PATTERN = re.compile(r'^相当于\s*\d+')
# Parameter value with trailing garbage number: "试验电压 2 000", "试验电压 2 400"
_PARAM_NAME_WITH_VALUE = re.compile(r'^(试验电压|试验电流|试验力)\s*\d[\d\s]*$')
# Unit-only names (no numeric value)
_UNIT_ONLY_PATTERN = re.compile(
    r'^(Hz|kHz|MHz|MHz|MΩ|kΩ|Ω|MΩ|mm|cm|m|km|s|ms|min|h|N|kN|MPa|V|kV|mV|'
    r'A|mA|W|kW|°C|℃|g|kg|m/s²|m/s|mm/s|r/min|rpm|L|mL)$'
)
# OCR fragments: names that look like truncated or garbled text
_OCR_FRAGMENT_PATTERNS = [
    # Chinese text with mid-text commas (OCR garbling): "振峰,上且两个共振"
    re.compile(r'[一-鿿]+[,，]\s*[一-鿿]'),
    # Text ending with special chars that suggest truncation/corruption
    re.compile(r'.+[&＆#@]\s*\d*$'),
    # 3+ chars of Chinese containing disconnected punctuation: "验,时间" type
    re.compile(r'^[一-鿿]{1,2}[,，.。!！][一-鿿]+'),
]


def _is_isolated_parameter_value(name: str) -> bool:
    """Return True if name looks like a parameter value rather than a named entity."""
    name = name.strip()
    # Pure numbers (integer or decimal)
    if re.match(r'^\d+\.?\d*$', name):
        return True
    # Alphanumeric that looks like a value: "1s", "2b级"
    if re.match(r'^\d+[a-zA-Z一-鿿]+$', name) and len(name) <= 5:
        return True
    # OCR approximate values
    if _OCR_APPROX_PATTERN.match(name):
        return True
    # Parameter name + trailing value: "试验电压 2 000"
    if _PARAM_NAME_WITH_VALUE.match(name):
        return True
    return any(p.match(name) for p in _PARAMETER_VALUE_PATTERNS)


# ── Cleanup function ──────────────────────────────────────────────────────


def classify_zero_degree_entity(
    name: str,
    labels: list[str],
    official_name: str | None = None,
) -> str | None:
    """Classify a zero-degree entity for filtering.

    Returns the filter reason string if the entity should be removed,
    or None if it should be kept.
    """
    label_set = {l for l in labels if l != 'Entity'}

    # Catalog metadata — always removable
    if _is_catalog_metadata(name):
        return 'catalog_metadata'

    # Deep section numbers — filter if >= 3 levels
    if 'Section' in label_set and _is_deep_section(name):
        return 'deep_section'

    # Unit-only TechnicalParameter (no value, no meaning)
    if 'TechnicalParameter' in label_set and _UNIT_ONLY_PATTERN.match(name):
        return 'unit_only_parameter'

    # Isolated parameter values (check BEFORE OCR fragments — pure numbers
    # like "100" should be classified as parameter values, not fragments)
    if _is_isolated_parameter_value(name):
        param_types = label_set & {'TechnicalParameter', 'Rating'}
        if param_types:
            return 'isolated_parameter_value'

    # Numeric-heavy names that are likely parameter values
    if re.search(r'\d', name) and 'TechnicalParameter' in label_set:
        if _is_isolated_parameter_value(name):
            return 'isolated_parameter_value'

    # OCR fragments (check AFTER parameter values to avoid misclassification)
    # Only match if name contains at least one CJK character — pure ASCII
    # short strings are usually parameter values, not OCR fragments.
    if any(p.match(name) for p in _OCR_FRAGMENT_PATTERNS):
        if 'TechnicalTerm' in label_set or 'TechnicalParameter' in label_set:
            # Guard: don't flag pure numeric/alphanumeric as OCR fragment
            if not re.match(r'^[0-9A-Za-z\s.±~～\-]+$', name):
                return 'ocr_fragment'

    return None


async def cleanup_zero_degree_noise(
    driver,  # GraphDriver (Neo4jDriver or similar)
    delete: bool = True,
) -> dict[str, int]:
    """Scan zero-degree entities and remove noise-pattern matches.

    Args:
        driver: GraphDriver instance with execute_query or session.
        delete: If True, actually delete matched entities. If False, only report.

    Returns:
        Dict with classification → count of entities filtered.
    """
    from collections import Counter

    ZD_QUERY = '''MATCH (n:Entity)
                  OPTIONAL MATCH (n)-[r:RELATES_TO]-()
                  WITH n, count(r) AS degree
                  WHERE degree = 0
                  RETURN n.name AS name, labels(n) AS labels, n.uuid AS uuid
                  ORDER BY name'''

    DELETE_QUERY = 'MATCH (n:Entity {uuid: $uuid}) DETACH DELETE n'

    # Query zero-degree entities
    if hasattr(driver, 'execute_query'):
        result = await driver.execute_query(ZD_QUERY)
        records = [dict(r) for r in result.records]
    elif hasattr(driver, 'execute_cypher'):
        records, _, _ = await driver.execute_cypher(ZD_QUERY)
    else:
        # Fallback for sync Neo4j driver
        with driver.session() as session:
            result = session.run(ZD_QUERY)
            records = [dict(r) for r in result]

    counts: Counter[str] = Counter()
    to_delete: list[str] = []

    for record in records:
        name = record['name']
        labels = record['labels']
        uuid = record['uuid']

        reason = classify_zero_degree_entity(name, labels)
        if reason:
            counts[reason] += 1
            to_delete.append(uuid)
            logger.info('Zero-degree cleanup [%s]: %s (labels=%s)', reason, name, labels)

    if delete and to_delete:
        if hasattr(driver, 'execute_query'):
            for uuid in to_delete:
                await driver.execute_query(DELETE_QUERY, params={'uuid': uuid})
        elif hasattr(driver, 'execute_cypher'):
            for uuid in to_delete:
                await driver.execute_cypher(DELETE_QUERY, uuid=uuid)
        else:
            with driver.session() as session:
                for uuid in to_delete:
                    session.run(DELETE_QUERY, uuid=uuid)

    total = sum(counts.values())
    logger.info(
        'Zero-degree cleanup: %d entities removed (%s)',
        total,
        dict(counts),
    )
    return dict(counts)


# ── Sync wrapper ──────────────────────────────────────────────────────────


def cleanup_zero_degree_noise_sync(driver, delete: bool = True) -> dict[str, int]:
    """Synchronous wrapper for cleanup_zero_degree_noise."""
    import asyncio

    return asyncio.run(cleanup_zero_degree_noise(driver, delete=delete))
