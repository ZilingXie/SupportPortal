#!/usr/bin/env python3
"""v10 Pipeline — Rejection Ledger验证运行.

记录所有关键质量指标，用于对比 v8/v9。
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Setup logging ────────────────────────────────────────────────────────
LOG_DIR = Path(__file__).with_name('v10_logs')
LOG_DIR.mkdir(exist_ok=True)
TIMESTAMP = datetime.now().strftime('%Y%m%d_%H%M%S')

# File log: everything at DEBUG level
file_handler = logging.FileHandler(LOG_DIR / f'v10_{TIMESTAMP}.log', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)-7s] %(name)s | %(message)s', datefmt='%H:%M:%S'
))

# Console log: INFO+ only
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)-7s] %(name)s | %(message)s', datefmt='%H:%M:%S'
))

# Metrics JSON log (structured)
metrics_log = LOG_DIR / f'v10_metrics_{TIMESTAMP}.jsonl'

logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler])
logger = logging.getLogger('v10')

# ── Run timing ───────────────────────────────────────────────────────────

class Timer:
    def __init__(self, name):
        self.name = name
        self.start = time.time()
    @property
    def elapsed(self):
        return time.time() - self.start
    def checkpoint(self, label=''):
        t = time.time() - self.start
        logger.info(f'  ⏱  {self.name}/{label}: {t:.1f}s')
        return t

# ── Neo4j query helpers ──────────────────────────────────────────────────

def neo4j_query(statements, uri='bolt://localhost:7688', user='neo4j', password='graphiti123'):
    import requests
    url = uri.replace('bolt://', 'http://').replace(':7688', ':7475')
    resp = requests.post(
        f'{url}/db/neo4j/tx/commit',
        auth=(user, password),
        json={'statements': statements},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


async def run():
    timer = Timer('v10')

    # ── Import and config ─────────────────────────────────────────────────
    from graphiti_rag.config_loader import load_config
    from graphiti_rag import GraphRAG
    from graphiti_rag.pipeline import Pipeline
    from graphiti_rag.components import Extractor

    config = load_config()
    logger.info(f'Config loaded: chunk={config.chunk_size}, overlap={config.chunk_overlap}')
    logger.info(f'Schema: {config.schema_path} mode={config.schema_mode}')
    logger.info(f'2nd pass: {config.second_pass_extraction} mode={config.second_pass_mode}')
    logger.info(f'2nd pass thresholds: entities>={config.second_pass_min_entities} edges>={config.second_pass_min_edges}')

    # ── Monkey-patch Extractor to capture rejection ledger ────────────────
    original_extract = Extractor.extract
    ledger_stats = {
        'chunks': 0,
        'total_first_pass_entities': 0,
        'total_first_pass_edges': 0,
        'entity_refinements': 0,
        'edge_refinements': 0,
        'entity_rejections': [],
        'edge_rejections': [],
        'entity_validated': 0,
        'edge_validated': 0,
        'entity_dropped': 0,
        'edge_dropped': 0,
    }

    async def instrumented_extract(self, chunk):
        nonlocal ledger_stats
        ledger_stats['chunks'] += 1
        result = await original_extract(self, chunk)
        if not result.error:
            ledger_stats['total_first_pass_entities'] += len(result.entities)
        return result

    Extractor.extract = instrumented_extract

    # Also monkey-patch node_operations to capture rejection counts
    from graphiti_core.utils.maintenance import node_operations, edge_operations
    from graphiti_core.utils.maintenance.node_operations import _validate_extracted_entities as orig_validate_entities
    from graphiti_core.utils.maintenance.edge_operations import _validate_extracted_edges as orig_validate_edges

    def instrumented_validate_entities(extracted_entities, entity_types_context,
                                              excluded_entity_types=None):
        result = orig_validate_entities(extracted_entities, entity_types_context, excluded_entity_types)
        ledger_stats['entity_validated'] += len(result.valid_entities)
        ledger_stats['entity_dropped'] += result.dropped_count
        for r in result.rejected_entities:
            ledger_stats['entity_rejections'].append({
                'chunk': ledger_stats['chunks'],
                'name': r.get('name', ''),
                'reason': r.get('reason', ''),
                'fixable': r.get('fixable', False),
            })
        return result

    def instrumented_validate_edges(extracted_edges, name_to_node):
        result = orig_validate_edges(extracted_edges, name_to_node)
        ledger_stats['edge_validated'] += len(result.valid_edges)
        ledger_stats['edge_dropped'] += result.dropped_count
        for r in result.rejected_edges:
            ledger_stats['edge_rejections'].append({
                'chunk': ledger_stats['chunks'],
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

    logger.info('Instrumentation installed ✓')

    # ── Run ingestion ─────────────────────────────────────────────────────
    timer.checkpoint('setup')

    default_input = Path(__file__).with_name('GBT+25338.1-2019.pdf')
    input_path = os.environ.get('GRAPHRAG_INPUT', str(default_input))
    logger.info(f'Input: {input_path}')

    rag = GraphRAG(config)
    logger.info(f'GraphRAG initialized, starting ingest...')

    result = await rag.ingest([input_path])
    timer.checkpoint('ingest')
    logger.info(f'Ingestion complete: {result}')

    # ── Gather metrics from Neo4j ─────────────────────────────────────────
    logger.info('\n' + '=' * 60)
    logger.info('  POST-INGESTION METRICS')
    logger.info('=' * 60)

    # Entity counts by type
    entity_rows, _, _ = await rag.graphiti.driver.execute_cypher(
        '''MATCH (n:Entity)
           UNWIND labels(n) AS lbl
           WITH lbl, count(n) AS cnt
           WHERE lbl <> 'Entity'
           RETURN lbl, cnt ORDER BY cnt DESC'''
    )
    logger.info('\n--- Entity Type Distribution ---')
    total_entities = 0
    for row in entity_rows:
        lbl, cnt = row['lbl'], row['cnt']
        logger.info(f'  {lbl}: {cnt}')
        total_entities += cnt
    logger.info(f'  TOTAL: {total_entities}')

    # Edge counts by type
    edge_rows, _, _ = await rag.graphiti.driver.execute_cypher(
        '''MATCH ()-[r:RELATES_TO]->()
           RETURN type(r) AS rel_type, count(r) AS cnt ORDER BY cnt DESC'''
    )
    logger.info('\n--- Edge Type Distribution ---')
    total_edges = 0
    for row in edge_rows:
        rtype = row['rel_type']
        cnt = row['cnt']
        logger.info('  %s: %d', rtype, cnt)
        total_edges += cnt
    logger.info('  TOTAL: %d', total_edges)

    # Zero-degree entities
    zd_rows, _, _ = await rag.graphiti.driver.execute_cypher(
        '''MATCH (n:Entity)
           OPTIONAL MATCH (n)-[r:RELATES_TO]-()
           WITH n, count(r) AS degree
           WHERE degree = 0
           RETURN n.name AS name, labels(n) AS labels ORDER BY name'''
    )
    logger.info(f'\n--- Zero-Degree Entities: {len(zd_rows)} ---')
    for row in zd_rows:
        logger.info(f'  [{",".join(row["labels"])}] {row["name"]}')

    # ── Run cleanup ───────────────────────────────────────────────────────
    logger.info('\n--- Cleanup Phase ---')
    from graphiti_core.utils.maintenance.zero_degree_cleanup import cleanup_zero_degree_noise

    cleanup_result = await cleanup_zero_degree_noise(rag.graphiti.driver, delete=True)
    logger.info(f'Cleanup results: {cleanup_result}')

    # Post-cleanup zero-degree
    zd_after, _, _ = await rag.graphiti.driver.execute_cypher(
        '''MATCH (n:Entity)
           OPTIONAL MATCH (n)-[r:RELATES_TO]-()
           WITH n, count(r) AS degree
           WHERE degree = 0
           RETURN n.name AS name, labels(n) AS labels ORDER BY name'''
    )
    logger.info(f'\n--- Post-Cleanup Zero-Degree: {len(zd_after)} ---')
    for row in zd_after:
        logger.info(f'  [{",".join(row["labels"])}] {row["name"]}')

    # ── Rejection Ledger Summary ──────────────────────────────────────────
    logger.info('\n' + '=' * 60)
    logger.info('  REJECTION LEDGER SUMMARY')
    logger.info('=' * 60)
    logger.info(f'Chunks processed: {ledger_stats["chunks"]}')
    logger.info(f'First-pass entities: {ledger_stats["total_first_pass_entities"]}')
    logger.info(f'Entities validated: {ledger_stats["entity_validated"]}')
    logger.info(f'Entities dropped: {ledger_stats["entity_dropped"]}')

    entity_rejections = ledger_stats['entity_rejections']
    er_by_reason = {}
    for r in entity_rejections:
        key = r['reason']
        if key not in er_by_reason:
            er_by_reason[key] = {'total': 0, 'fixable': 0}
        er_by_reason[key]['total'] += 1
        if r['fixable']:
            er_by_reason[key]['fixable'] += 1
    logger.info('\nEntity rejections by reason:')
    for reason, counts in sorted(er_by_reason.items()):
        logger.info(f'  {reason}: {counts["total"]} ({counts["fixable"]} fixable)')

    edge_rejections = ledger_stats['edge_rejections']
    eg_by_reason = {}
    for r in edge_rejections:
        key = r['reason']
        if key not in eg_by_reason:
            eg_by_reason[key] = {'total': 0, 'fixable': 0}
        eg_by_reason[key]['total'] += 1
        if r['fixable']:
            eg_by_reason[key]['fixable'] += 1
    logger.info('\nEdge rejections by reason:')
    for reason, counts in sorted(eg_by_reason.items()):
        logger.info(f'  {reason}: {counts["total"]} ({counts["fixable"]} fixable)')

    # Shows fixable edges in detail
    fixable_edges = [e for e in edge_rejections if e['fixable']]
    if fixable_edges:
        logger.info(f'\nFixable rejected edges ({len(fixable_edges)}):')
        for e in fixable_edges:
            logger.info(f'  [{e["reason"]}] {e["source"]} → {e["target"]} ({e["relation"]})')
            if e.get('candidate_source'):
                logger.info(f'    candidate_source: {e["candidate_source"]}')
            if e.get('candidate_target'):
                logger.info(f'    candidate_target: {e["candidate_target"]}')

    # ── Quality metrics ───────────────────────────────────────────────────
    logger.info('\n' + '=' * 60)
    logger.info('  QUALITY METRICS')
    logger.info('=' * 60)

    edge_per_entity = total_edges / max(total_entities, 1)
    zd_rate_before = len(zd_rows) / max(total_entities, 1) * 100
    zd_rate_after = len(zd_after) / max(total_entities, 1) * 100

    logger.info(f'Total entities:    {total_entities}')
    logger.info(f'Total edges:       {total_edges}')
    logger.info(f'Edge/entity ratio: {edge_per_entity:.2f}')
    logger.info(f'Zero-degree (before cleanup): {len(zd_rows)} ({zd_rate_before:.1f}%)')
    logger.info(f'Zero-degree (after cleanup):  {len(zd_after)} ({zd_rate_after:.1f}%)')
    logger.info(f'Entity rejections: {len(entity_rejections)}')
    logger.info(f'Edge rejections:   {len(edge_rejections)}')
    logger.info(f'Fixable edge rejects: {len(fixable_edges)}')

    # ── Persist metrics JSON ──────────────────────────────────────────────
    metrics = {
        'version': 'v10',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'config': {
            'chunk_size': config.chunk_size,
            'chunk_overlap': config.chunk_overlap,
            'schema_mode': config.schema_mode,
            'second_pass_extraction': config.second_pass_extraction,
            'second_pass_mode': config.second_pass_mode,
            'second_pass_min_entities': config.second_pass_min_entities,
            'second_pass_min_edges': config.second_pass_min_edges,
            'model': config.llm.get('model') if hasattr(config, 'llm') else 'deepseek-chat',
        },
        'chunks': ledger_stats['chunks'],
        'entities': {
            'total': total_entities,
            'first_pass': ledger_stats['total_first_pass_entities'],
            'validated': ledger_stats['entity_validated'],
            'dropped': ledger_stats['entity_dropped'],
        },
        'edges': {
            'total': total_edges,
        },
        'zero_degree': {
            'before_cleanup': len(zd_rows),
            'after_cleanup': len(zd_after),
            'rate_before': round(zd_rate_before, 1),
            'rate_after': round(zd_rate_after, 1),
        },
        'rejections': {
            'entity': {reason: counts for reason, counts in er_by_reason.items()},
            'edge': {reason: counts for reason, counts in eg_by_reason.items()},
            'fixable_edges': len(fixable_edges),
        },
        'cleanup': {k: v for k, v in cleanup_result.items()},
        'edge_per_entity': round(edge_per_entity, 2),
        'elapsed_seconds': timer.elapsed,
    }
    with open(metrics_log, 'w', encoding='utf-8') as f:
        f.write(json.dumps(metrics, ensure_ascii=False, indent=2))
    logger.info(f'\nMetrics written to: {metrics_log}')

    # ── Close ─────────────────────────────────────────────────────────────
    await rag.close()
    logger.info(f'\nTotal elapsed: {timer.elapsed:.1f}s')
    logger.info('v10 run complete.')


if __name__ == '__main__':
    asyncio.run(run())
