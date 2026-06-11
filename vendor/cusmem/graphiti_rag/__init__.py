"""GraphRAG — Document-to-Knowledge-Graph Pipeline.

Pipeline: Scanner → Reader → Splitter → Extractor → Writer
Concurrency: ThreadPool/ProcessPool per phase, Producer-Consumer pattern.

Usage:
    from graphiti_rag import GraphRAG, Config
    rag = GraphRAG(Config(neo4j_password='pass', llm_api_key='sk-xxx'))
    rag.ingest(['./docs/'])
    edges = rag.search('query')
"""
from .config import Config
from .graph_rag import GraphRAG
__all__ = ['GraphRAG', 'Config']
