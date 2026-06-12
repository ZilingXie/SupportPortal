"""GraphRAG CLI — ingest documents into a knowledge graph.

Usage:
    python3 -m graphiti_rag --input docs/ --schema schemas/my_domain.yaml
    python3 -m graphiti_rag --input doc.txt --mode upsert

Environment variables:
    GRAPHRAG_NEO4J_URI, GRAPHRAG_NEO4J_USER, GRAPHRAG_NEO4J_PASSWORD
    GRAPHRAG_LLM_API_KEY, GRAPHRAG_LLM_BASE_URL, GRAPHRAG_LLM_MODEL
    GRAPHRAG_CONFIG — path to config YAML (default: graphrag_config.yaml)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Ingest text documents into a Neo4j knowledge graph.',
    )
    parser.add_argument(
        '--input', required=True, type=Path,
        help='Input file (.txt, .md) or directory of files',
    )
    parser.add_argument(
        '--config', type=Path, default=None,
        help='Path to graphrag_config.yaml (default: $GRAPHRAG_CONFIG or graphrag_config.yaml)',
    )
    parser.add_argument(
        '--schema', type=Path, default=None,
        help='Path to domain schema YAML (overrides config schema.path)',
    )
    parser.add_argument(
        '--mode', choices=('append', 'upsert'), default=None,
        help='Ingest mode: append (always ingest) or upsert (skip unchanged chunks)',
    )
    parser.add_argument(
        '--no-progress', action='store_true',
        help='Disable progress display',
    )
    parser.add_argument(
        '--build-communities', action='store_true',
        help='Run community detection after ingestion',
    )
    return parser


async def run(args: argparse.Namespace) -> dict:
    # Lazy imports — only trigger Neo4j/LLM deps when actually running
    from graphiti_rag import GraphRAG
    from graphiti_rag.config_loader import load_config

    config = load_config(str(args.config) if args.config else None)

    # CLI overrides
    if args.schema:
        from graphiti_rag.schema_loader import load_graph_schema
        loaded = load_graph_schema(args.schema)
        config.schema_path = str(args.schema)
        config.entity_types = loaded.entity_types
        config.edge_types = loaded.edge_types
        config.edge_type_map = loaded.edge_type_map

    if args.mode:
        config.ingest_mode = args.mode

    if args.no_progress:
        config.progress = False

    if args.build_communities:
        config.build_communities = True

    input_path = str(args.input)

    print('GraphRAG Ingest')
    print(f'  Input:      {input_path}')
    print(f'  Schema:     {config.schema_path or "none"}')
    print(f'  Mode:       {config.ingest_mode}')
    print(f'  Model:      {config.llm_model}')
    print()

    rag = GraphRAG(config)
    try:
        result = await rag.ingest([input_path])
        print(f'\nDone: {result["files"]} file(s) → {result["chunks"]} chunks → {result["extracted"]} extracted')
    finally:
        await rag.close()

    return result


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.input.exists():
        parser.error(f'Input not found: {args.input}')

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print('\nInterrupted.', file=sys.stderr)
        return 130
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
