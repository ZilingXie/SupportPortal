from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch


VENDOR_ROOT = Path(__file__).resolve().parents[2] / "vendor" / "cusmem"


def _ensure_vendor_path() -> None:
    if str(VENDOR_ROOT) not in sys.path:
        sys.path.insert(0, str(VENDOR_ROOT))


def test_config_loader_maps_embedding_env_vars() -> None:
    _ensure_vendor_path()
    from graphiti_rag.config_loader import load_config

    env = {
        "GRAPHRAG_NEO4J_URI": "bolt://local_neo4j:7687",
        "GRAPHRAG_NEO4J_USER": "neo4j",
        "GRAPHRAG_NEO4J_PASSWORD": "supportportal-kg-local",
        "GRAPHRAG_EMBEDDING_API_KEY": "embedding-secret",
        "GRAPHRAG_EMBEDDING_MODEL": "BAAI/bge-m3",
        "GRAPHRAG_EMBEDDING_BASE_URL": "https://api.siliconflow.cn/v1",
        "GRAPHRAG_EMBEDDING_DIM": "1024",
    }
    with patch.dict(os.environ, env, clear=True):
        config = load_config(path="/tmp/nonexistent-graphrag-config.yaml")

    assert config.embedding_api_key == "embedding-secret"
    assert config.embedding_model == "BAAI/bge-m3"
    assert config.embedding_base_url == "https://api.siliconflow.cn/v1"
    assert config.embedding_dim == 1024


def test_graph_rag_passes_configured_embedding_api_key() -> None:
    _ensure_vendor_path()

    captured: dict[str, object] = {}

    class _FakeLLMConfig:
        def __init__(self, **kwargs: object) -> None:
            captured["llm_config"] = kwargs

    class _FakeOpenAIClient:
        def __init__(self, *, config: object) -> None:
            captured["llm_client_config"] = config

    class _FakeOpenAIEmbedderConfig:
        def __init__(self, **kwargs: object) -> None:
            captured["embedder_config"] = kwargs

    class _FakeOpenAIEmbedder:
        def __init__(self, *, config: object) -> None:
            captured["embedder_client_config"] = config

    class _FakeGraphiti:
        def __init__(self, **kwargs: object) -> None:
            captured["graphiti"] = kwargs

    fake_modules = {
        "graphiti_core": types.SimpleNamespace(Graphiti=_FakeGraphiti),
        "graphiti_core.embedder.openai": types.SimpleNamespace(
            OpenAIEmbedder=_FakeOpenAIEmbedder,
            OpenAIEmbedderConfig=_FakeOpenAIEmbedderConfig,
        ),
        "graphiti_core.llm_client.config": types.SimpleNamespace(LLMConfig=_FakeLLMConfig),
        "graphiti_core.llm_client.openai_client": types.SimpleNamespace(OpenAIClient=_FakeOpenAIClient),
    }

    with patch.dict(sys.modules, fake_modules):
        config_module = importlib.import_module("graphiti_rag.config")
        graph_rag_module = importlib.reload(importlib.import_module("graphiti_rag.graph_rag"))
        config = config_module.Config(
            neo4j_uri="bolt://local_neo4j:7687",
            neo4j_user="neo4j",
            neo4j_password="supportportal-kg-local",
            embedding_api_key="embedding-secret",
            embedding_model="BAAI/bge-m3",
            embedding_base_url="https://api.siliconflow.cn/v1",
            embedding_dim=1024,
        )

        graph_rag_module.GraphRAG(config)

    assert captured["embedder_config"]["api_key"] == "embedding-secret"
    assert captured["embedder_config"]["embedding_model"] == "BAAI/bge-m3"
    assert captured["embedder_config"]["base_url"] == "https://api.siliconflow.cn/v1"
    assert captured["embedder_config"]["embedding_dim"] == 1024
