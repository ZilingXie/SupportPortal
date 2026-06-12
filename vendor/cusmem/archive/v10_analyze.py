#!/usr/bin/env python3
"""v10 Post-Ingestion Analysis — Query Neo4j for metrics (data already ingested)."""

import asyncio
import logging
import sys
from datetime import datetime, timezone

LOG_DIR = '/root/graphiti/v10_logs'
TIMESTAMP = datetime.now().strftime('%Y%m%d_%H%M%S')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)-7s] %(name)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger('v10_analysis')


async def main():
    from graphiti_rag.config_loader import load_config
    from graphiti_rag import GraphRAG
    from graphiti_core.utils.maintenance.zero_degree_cleanup import cleanup_zero_degree_noise

    config = load_config()
    rag = GraphRAG(config)

    driver = rag.graphiti.driver

    async def query(cypher, **kwargs):
        result = await driver.execute_query(cypher, **kwargs)
        records = result.records
        return records

    # ── Entity counts by type ───────────────────────────────────────────
    logger.info('=' * 60)
    logger.info('  POST-INGESTION METRICS (v10)')
    logger.info('=' * 60)

    records = await query(
        '''MATCH (n:Entity)
           UNWIND labels(n) AS lbl
           WITH lbl, count(n) AS cnt
           WHERE lbl <> 'Entity'
           RETURN lbl, cnt ORDER BY cnt DESC'''
    )
    logger.info('\n--- Entity Type Distribution ---')
    total_entities = 0
    for r in records:
        lbl, cnt = r['lbl'], r['cnt']
        logger.info('  %s: %d', lbl, cnt)
        total_entities += cnt
    logger.info('  TOTAL: %d', total_entities)

    # ── Edge counts by type ─────────────────────────────────────────────
    records = await query(
        '''MATCH ()-[r:RELATES_TO]->()
           RETURN r.name AS rel_type, count(r) AS cnt ORDER BY cnt DESC'''
    )
    logger.info('\n--- Edge Type Distribution ---')
    total_edges = 0
    for r in records:
        rtype, cnt = r['rel_type'], r['cnt']
        logger.info('  %s: %d', rtype or 'RELATES_TO', cnt)
        total_edges += cnt
    logger.info('  TOTAL: %d', total_edges)

    # ── Zero-degree entities ────────────────────────────────────────────
    records = await query(
        '''MATCH (n:Entity)
           OPTIONAL MATCH (n)-[r:RELATES_TO]-()
           WITH n, count(r) AS degree
           WHERE degree = 0
           RETURN n.name AS name, labels(n) AS labels ORDER BY name'''
    )
    logger.info('\n--- Zero-Degree Entities (pre-cleanup): %d ---', len(records))
    for r in records:
        logger.info('  [%s] %s', ','.join(r['labels']), r['name'])

    # ── Cleanup ─────────────────────────────────────────────────────────
    logger.info('\n--- Cleanup Phase ---')
    cleanup_result = await cleanup_zero_degree_noise(driver, delete=True)
    logger.info('Cleanup results: %s', dict(cleanup_result))

    # Post-cleanup zero-degree
    records = await query(
        '''MATCH (n:Entity)
           OPTIONAL MATCH (n)-[r:RELATES_TO]-()
           WITH n, count(r) AS degree
           WHERE degree = 0
           RETURN n.name AS name, labels(n) AS labels ORDER BY name'''
    )
    logger.info('\n--- Post-Cleanup Zero-Degree: %d ---', len(records))
    for r in records:
        logger.info('  [%s] %s', ','.join(r['labels']), r['name'])

    # ── Quality metrics ─────────────────────────────────────────────────
    logger.info('\n' + '=' * 60)
    logger.info('  QUALITY METRICS (v10)')
    logger.info('=' * 60)
    edge_per_entity = total_edges / max(total_entities, 1) * 100
    logger.info('Total entities:    %d', total_entities)
    logger.info('Total edges:       %d', total_edges)
    logger.info('Edge/entity ratio: %.1f edges/100 entities', edge_per_entity)

    # Entity entity types with zero-degree
    records = await query(
        '''MATCH (n:Entity)
           OPTIONAL MATCH (n)-[r:RELATES_TO]-()
           WITH n, count(r) AS degree
           WHERE degree = 0
           UNWIND labels(n) AS lbl
           WITH lbl, count(*) AS cnt
           WHERE lbl <> 'Entity'
           RETURN lbl, cnt ORDER BY cnt DESC'''
    )
    logger.info('\nZero-degree by type:')
    for r in records:
        logger.info('  %s: %d', r['lbl'], r['cnt'])

    # Duplicate names (same name, different types)
    records = await query(
        '''MATCH (n:Entity)
           WITH n.name AS name, collect(DISTINCT [x IN labels(n) WHERE x <> 'Entity' | x]) AS type_sets, count(n) AS node_count
           WHERE node_count > 1
           RETURN name, type_sets, node_count ORDER BY node_count DESC LIMIT 20'''
    )
    logger.info('\nDuplicate-name entities (same name, diff types): %d', len(records))
    for r in records:
        logger.info('  %s: %s (nodes=%d)', r['name'], r['type_sets'], r['node_count'])

    # High-degree entities (most connected)
    records = await query(
        '''MATCH (n:Entity)-[r:RELATES_TO]-()
           WITH n, count(r) AS degree
           RETURN n.name AS name, labels(n) AS labels, degree ORDER BY degree DESC LIMIT 15'''
    )
    logger.info('\nMost-connected entities:')
    for r in records:
        logger.info('  [%s] %s: degree=%d', ','.join(r['labels']), r['name'], r['degree'])

    await rag.close()
    logger.info('\nAnalysis complete.')


if __name__ == '__main__':
    asyncio.run(main())
