"""GraphRAG — Document-to-Knowledge-Graph Pipeline.

Module boundary:
  graphiti_core     — Neo4j graph engine (entities, edges, LLM extraction, search).
                      We use it directly via Graphiti.add_episode(), not wrapped.
  graphiti_rag      — Document ingestion pipeline on top of graphiti_core.
                      Scanner → Reader → Splitter → Extractor → Writer.
                      Adds: file discovery, chunking, upsert tracking, progress.
  tools/schema_design — Schema design from documents (text → patterns → schema).
                      Generates YAML schemas that graphiti_rag consumes.

Pipeline: Scanner → Reader → Splitter → Extractor → Writer
Concurrency: ThreadPool + Producer-Consumer (KAG pattern)

Usage:
    from graphiti_rag import GraphRAG, Config
    rag = GraphRAG(Config(neo4j_password='pass', llm_api_key='sk-xxx'))
    rag.ingest(['./docs/'])
    edges = rag.search('query')
"""


def __getattr__(name: str):
    """Lazy imports — defer Neo4j/LLM deps until actually used."""
    if name == 'GraphRAG':
        from .graph_rag import GraphRAG
        return GraphRAG
    if name == 'Config':
        from .config import Config
        return Config
    if name == 'Pipeline':
        from .pipeline import Pipeline
        return Pipeline
    raise AttributeError(name)


__all__ = ['GraphRAG', 'Config', 'Pipeline']
