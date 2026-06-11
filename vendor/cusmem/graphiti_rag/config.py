"""Configuration."""

from dataclasses import dataclass


@dataclass
class Config:
    # Neo4j
    neo4j_uri: str = 'bolt://localhost:7687'
    neo4j_user: str = 'neo4j'
    neo4j_password: str = 'password'

    # LLM
    llm_api_key: str = ''
    llm_base_url: str = 'https://api.deepseek.com/v1'
    llm_model: str = 'deepseek-chat'

    # Embedding
    embedding_model: str = 'bge-m3:latest'
    embedding_base_url: str = 'http://101.43.92.199:11434/v1/'
    embedding_dim: int = 1024

    # Pipeline
    file_pattern: str = r'.*\.(txt|md|docx|csv|json)$'
    chunk_size: int = 1000
    chunk_overlap: int = 200

    # Custom entity/edge types (optional, passed to Graphiti)
    entity_types: dict | None = None
    edge_types: dict | None = None
    edge_type_map: dict | None = None

    # User-authored KAG-lite schema
    schema_path: str | None = None
    schema_mode: str = 'strict'
    second_pass_extraction: bool = True
    second_pass_mode: str = 'conditional'
    second_pass_min_entities: int = 2
    second_pass_min_edges: int = 1

    # Ingest mode: 'append' (always add) or 'upsert' (skip unchanged chunks)
    ingest_mode: str = 'append'

    # Concurrency (mirrors KAG's runner pattern)
    num_chains: int = 2  # Read+Split 并行线程数
    num_threads_per_chain: int = 1  # Extractor 并发数 (CPU紧张时设1)
    max_concurrency: int = 3  # Extract 消费者数 (控制embedding并发)
    max_workers: int = 10  # fallback for ThreadPoolExecutor

    # Community detection
    build_communities: bool = False  # Run community detection after ingestion

    # Display
    progress: bool = True
