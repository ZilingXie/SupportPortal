#!/usr/bin/env python3
"""v11 Pipeline — Fixed cleanup + full rejection ledger instrumentation.

Records all key quality metrics. Logs to v11_logs/.
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).with_name('v11_logs')
LOG_DIR.mkdir(exist_ok=True)
TIMESTAMP = datetime.now().strftime('%Y%m%d_%H%M%S')

# File + Console logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)-7s] %(name)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_DIR / f'v11_{TIMESTAMP}.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger('v11')


async def run():
    t0 = time.time()

    # ── Config ─────────────────────────────────────────────────────────
    from graphiti_rag.config_loader import load_config
    from graphiti_rag import GraphRAG

    config = load_config()
    logger.info('v11 pipeline start: chunk=%d overlap=%d schema=%s 2nd=%s/%s min_e=%d min_r=%d',
                config.chunk_size, config.chunk_overlap, config.schema_mode,
                config.second_pass_extraction, config.second_pass_mode,
                config.second_pass_min_entities, config.second_pass_min_edges)

    # ── Instrumentation ────────────────────────────────────────────────
    from graphiti_core.utils.maintenance import node_operations, edge_operations
    from graphiti_rag.components import Extractor

    orig_validate_entities = node_operations._validate_extracted_entities
    orig_validate_edges = edge_operations._validate_extracted_edges
    orig_extract = Extractor.extract

    stats = {
        'chunks': 0, 'entity_refinements': 0, 'edge_refinements': 0,
        'entity_rejections': [], 'edge_rejections': [],
        'entity_validated': 0, 'entity_dropped': 0,
        'edge_validated': 0, 'edge_dropped': 0,
    }

    def instrumented_validate_entities(extracted_entities, entity_types_context,
                                        excluded_entity_types=None):
        result = orig_validate_entities(extracted_entities, entity_types_context,
                                         excluded_entity_types)
        stats['entity_validated'] += len(result.valid_entities)
        stats['entity_dropped'] += result.dropped_count
        for r in result.rejected_entities:
            stats['entity_rejections'].append({
                'name': r.get('name', ''), 'reason': r.get('reason', ''),
                'fixable': r.get('fixable', False),
            })
        return result

    def instrumented_validate_edges(extracted_edges, name_to_node):
        result = orig_validate_edges(extracted_edges, name_to_node)
        stats['edge_validated'] += len(result.valid_edges)
        stats['edge_dropped'] += result.dropped_count
        for r in result.rejected_edges:
            stats['edge_rejections'].append({
                'source': r.get('source_entity_name', ''),
                'target': r.get('target_entity_name', ''),
                'relation': r.get('relation_type', ''),
                'reason': r.get('reason', ''),
                'fixable': r.get('fixable', False),
                'candidate_source': r.get('candidate_source', ''),
                'candidate_target': r.get('candidate_target', ''),
            })
        return result

    node_operations._validate_extracted_entities = instrumented_validate_entities
    edge_operations._validate_extracted_edges = instrumented_validate_edges
    logger.info('Instrumentation installed')

    # ── Ingest ─────────────────────────────────────────────────────────
    input_path = os.environ.get('GRAPHRAG_INPUT',
                                str(Path(__file__).with_name('GBT+25338.1-2019.txt')))
    logger.info('Input: %s', input_path)

    rag = GraphRAG(config)
    result = await rag.ingest([input_path])
    t_ingest = time.time() - t0
    logger.info('Ingestion complete: %s (%.1fs)', result, t_ingest)

    # ── Post-Ingestion Metrics ─────────────────────────────────────────
    driver = rag.graphiti.driver

    async def query(cypher, **params):
        r = await driver.execute_query(cypher, params=params or None)
        return r.records

    def log_section(title):
        logger.info('\n' + '=' * 60)
        logger.info('  %s', title)
        logger.info('=' * 60)

    log_section('POST-INGESTION METRICS')

    # Entity types
    records = await query(
        '''MATCH (n:Entity)
           UNWIND labels(n) AS lbl
           WITH lbl, count(n) AS cnt WHERE lbl <> 'Entity'
           RETURN lbl, cnt ORDER BY cnt DESC''')
    logger.info('\n--- Entity Type Distribution ---')
    total_entities = 0
    for r in records:
        logger.info('  %s: %d', r['lbl'], r['cnt'])
        total_entities += r['cnt']

    # Edge types (by e.name, not type(r))
    records = await query(
        '''MATCH ()-[r:RELATES_TO]->()
           RETURN r.name AS rel_type, count(r) AS cnt ORDER BY cnt DESC''')
    logger.info('\n--- Edge Type Distribution ---')
    total_edges = 0
    for r in records:
        logger.info('  %s: %d', r['rel_type'] or 'RELATES_TO', r['cnt'])
        total_edges += r['cnt']

    # Zero-degree (pre-cleanup)
    records = await query(
        '''MATCH (n:Entity)
           OPTIONAL MATCH (n)-[r:RELATES_TO]-()
           WITH n, count(r) AS degree WHERE degree = 0
           RETURN n.name AS name, labels(n) AS labels ORDER BY name''')
    zd_before = len(records)
    logger.info('\n--- Zero-Degree Entities (pre-cleanup): %d ---', zd_before)
    for r in records:
        logger.info('  [%s] %s', ','.join(r['labels']), r['name'])

    # ── Cleanup ────────────────────────────────────────────────────────
    log_section('CLEANUP')
    from graphiti_core.utils.maintenance.zero_degree_cleanup import cleanup_zero_degree_noise

    cleanup_result = await cleanup_zero_degree_noise(driver, delete=True)
    logger.info('Cleanup: %s', dict(cleanup_result))

    # Zero-degree (post-cleanup)
    records = await query(
        '''MATCH (n:Entity)
           OPTIONAL MATCH (n)-[r:RELATES_TO]-()
           WITH n, count(r) AS degree WHERE degree = 0
           RETURN n.name AS name, labels(n) AS labels ORDER BY name''')
    zd_after = len(records)
    entity_after = total_entities - sum(cleanup_result.values())
    logger.info('\n--- Post-Cleanup Zero-Degree: %d ---', zd_after)
    for r in records:
        logger.info('  [%s] %s', ','.join(r['labels']), r['name'])

    # Zero-degree by type
    records = await query(
        '''MATCH (n:Entity)
           OPTIONAL MATCH (n)-[r:RELATES_TO]-()
           WITH n, count(r) AS degree WHERE degree = 0
           UNWIND labels(n) AS lbl
           WITH lbl, count(*) AS cnt WHERE lbl <> 'Entity'
           RETURN lbl, cnt ORDER BY cnt DESC''')
    logger.info('\nZero-degree by type:')
    for r in records:
        logger.info('  %s: %d', r['lbl'], r['cnt'])

    # ── Rejection Ledger Summary ───────────────────────────────────────
    log_section('REJECTION LEDGER')

    er_by_reason = {}
    for r in stats['entity_rejections']:
        k = r['reason']
        er_by_reason.setdefault(k, {'total': 0, 'fixable': 0})
        er_by_reason[k]['total'] += 1
        if r['fixable']:
            er_by_reason[k]['fixable'] += 1
    logger.info('\nEntity rejections:')
    for reason, c in sorted(er_by_reason.items()):
        logger.info('  %s: %d (%d fixable)', reason, c['total'], c['fixable'])

    eg_by_reason = {}
    for r in stats['edge_rejections']:
        k = r['reason']
        eg_by_reason.setdefault(k, {'total': 0, 'fixable': 0})
        eg_by_reason[k]['total'] += 1
        if r['fixable']:
            eg_by_reason[k]['fixable'] += 1
    logger.info('\nEdge rejections:')
    for reason, c in sorted(eg_by_reason.items()):
        logger.info('  %s: %d (%d fixable)', reason, c['total'], c['fixable'])

    fixable_edges = [e for e in stats['edge_rejections'] if e['fixable']]
    if fixable_edges:
        logger.info('\nFixable rejected edges (%d):', len(fixable_edges))
        for e in fixable_edges:
            logger.info('  [%s] %s → %s (%s)', e['reason'], e['source'], e['target'], e['relation'])
            if e.get('candidate_source'):
                logger.info('    candidate_source: %s', e['candidate_source'])
            if e.get('candidate_target'):
                logger.info('    candidate_target: %s', e['candidate_target'])

    # ── Quality Metrics ────────────────────────────────────────────────
    log_section('QUALITY METRICS')

    ep = total_edges / max(total_entities, 1)
    zr_b = zd_before / max(total_entities, 1) * 100
    zr_a = zd_after / max(entity_after, 1) * 100 if entity_after > 0 else 0

    logger.info('Total entities:       %d', total_entities)
    logger.info('Total edges:          %d', total_edges)
    logger.info('Edge/entity ratio:    %.2f', ep)
    logger.info('Zero-degree (before): %d (%.1f%%)', zd_before, zr_b)
    logger.info('Zero-degree (after):  %d (%.1f%%)', zd_after, zr_a)
    logger.info('Entity rejections:    %d', len(stats['entity_rejections']))
    logger.info('Edge rejections:      %d', len(stats['edge_rejections']))
    logger.info('Fixable edge rejects: %d', len(fixable_edges))
    logger.info('Cleanup removed:      %d', sum(cleanup_result.values()))
    logger.info('Total elapsed:        %.1fs', time.time() - t0)

    # ── Persist metrics JSON ───────────────────────────────────────────
    metrics = {
        'version': 'v11',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'config': {
            'chunk_size': config.chunk_size,
            'chunk_overlap': config.chunk_overlap,
            'schema_mode': config.schema_mode,
            'second_pass_extraction': config.second_pass_extraction,
            'second_pass_mode': config.second_pass_mode,
        },
        'entities': {'total': total_entities, 'post_cleanup': entity_after},
        'edges': {'total': total_edges},
        'zero_degree': {'before': zd_before, 'after': zd_after, 'rate_before': round(zr_b, 1), 'rate_after': round(zr_a, 1)},
        'edge_per_entity': round(ep, 2),
        'rejections': {
            'entity': {k: v for k, v in er_by_reason.items()},
            'edge': {k: v for k, v in eg_by_reason.items()},
            'fixable_edges': len(fixable_edges),
        },
        'cleanup': {k: v for k, v in cleanup_result.items()},
        'elapsed_seconds': round(time.time() - t0, 1),
    }
    metrics_path = LOG_DIR / f'v11_metrics_{TIMESTAMP}.json'
    with open(metrics_path, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    logger.info('\nMetrics saved: %s', metrics_path)

    await rag.close()
    logger.info('v11 complete.')


if __name__ == '__main__':
    asyncio.run(run())
