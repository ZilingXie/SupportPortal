"""GraphRAG main entry point.

Pipeline: Scanner → Reader → Splitter → Extractor → Writer
Concurrency: ThreadPool + Producer-Consumer (KAG pattern)

Offline chunk ingest: GraphRAG.ingest_chunks() / ingest_chunks_sync()
accepts pre-built Chunk lists (SupportPortal adapter path).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from graphiti_core import Graphiti
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient

from .config import Config
from .pipeline import Pipeline

if TYPE_CHECKING:
    from .components import Chunk


class GraphRAG:
    """Document-to-Knowledge-Graph RAG system."""

    def __init__(self, config: Config | None = None):
        self.cfg = config or Config()

        llm = OpenAIClient(config=LLMConfig(
            api_key=self.cfg.llm_api_key,
            base_url=self.cfg.llm_base_url,
            model=self.cfg.llm_model,
            small_model=self.cfg.llm_model,
        ))
        embedder = OpenAIEmbedder(config=OpenAIEmbedderConfig(
            embedding_model=self.cfg.embedding_model,
            base_url=self.cfg.embedding_base_url,
            embedding_dim=self.cfg.embedding_dim,
            api_key=self.cfg.embedding_api_key,
        ))
        self.graphiti = Graphiti(
            uri=self.cfg.neo4j_uri, user=self.cfg.neo4j_user,
            password=self.cfg.neo4j_password,
            llm_client=llm, embedder=embedder,
        )
        self.pipeline = Pipeline(self.cfg, self.graphiti)

    async def initialize(self):
        """Build Neo4j indices."""
        await self.graphiti.build_indices_and_constraints()

    async def ingest(self, paths: list[str]) -> dict:
        """Ingest documents (async)."""
        await self.initialize()
        return await self.pipeline.run(paths)

    def ingest_sync(self, paths: list[str]) -> dict:
        """Ingest documents (sync)."""
        return asyncio.run(self.ingest(paths))

    async def ingest_chunks(self, chunks: list[Chunk]) -> dict:
        """Ingest pre-built chunks (SupportPortal adapter path).

        Skips Scanner/Reader/Splitter — chunks go directly to extraction.
        """
        await self.initialize()
        return await self.pipeline.run_chunks(chunks)

    def ingest_chunks_sync(self, chunks: list[Chunk]) -> dict:
        """Ingest pre-built chunks (sync)."""
        return asyncio.run(self.ingest_chunks(chunks))

    async def search(self, query: str, num_results: int = 10) -> list:
        """Search the knowledge graph."""
        return await self.graphiti.search(query=query, num_results=num_results)

    def search_sync(self, query: str, num_results: int = 10) -> list:
        """Search (sync)."""
        return asyncio.run(self.search(query, num_results))

    async def close(self):
        """Close connections."""
        await self.graphiti.close()
