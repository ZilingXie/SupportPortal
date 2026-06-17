"""Configuration loader — supports YAML/JSON files + env vars."""

import json
import os
from pathlib import Path
from typing import Any

from .config import Config


def load_config(path: str | None = None) -> Config:
    """Load configuration from file, with env var overrides.

    Priority: env vars > config file > defaults

    Supported formats: .yaml, .yml, .json

    Env var mapping:
        GRAPHRAG_NEO4J_URI, GRAPHRAG_NEO4J_USER, GRAPHRAG_NEO4J_PASSWORD
        GRAPHRAG_LLM_API_KEY, GRAPHRAG_LLM_BASE_URL, GRAPHRAG_LLM_MODEL
        GRAPHRAG_CHUNK_SIZE, GRAPHRAG_CHUNK_OVERLAP
        GRAPHRAG_NUM_CHAINS, GRAPHRAG_NUM_THREADS, GRAPHRAG_MAX_CONCURRENCY
    """
    config_dict: dict[str, Any] = {}

    # Load from file
    cfg_path = Path(path or os.environ.get('GRAPHRAG_CONFIG', 'graphrag_config.yaml'))
    if cfg_path.exists():
        if cfg_path.suffix in ('.yaml', '.yml'):
            try:
                import yaml

                with open(cfg_path, encoding='utf-8') as f:
                    config_dict = yaml.safe_load(f) or {}
            except ImportError:
                pass
        elif cfg_path.suffix == '.json':
            config_dict = json.loads(cfg_path.read_text(encoding='utf-8'))

    # Flatten nested keys: neo4j.uri -> neo4j_uri
    # pipeline.chunk_size -> chunk_size (no prefix)
    flat = {}
    for section in ('neo4j', 'llm', 'embedding', 'schema'):
        if section in config_dict:
            for k, v in config_dict[section].items():
                flat[f'{section}_{k}'] = v
    # pipeline keys map directly (no prefix)
    if 'pipeline' in config_dict:
        flat.update(config_dict['pipeline'])
    for key in (
        'second_pass_extraction',
        'second_pass_mode',
        'second_pass_min_entities',
        'second_pass_min_edges',
    ):
        schema_key = f'schema_{key}'
        if schema_key in flat:
            flat[key] = flat.pop(schema_key)

    # Expand ${VAR} in string values
    import re

    for k, v in flat.items():
        if isinstance(v, str):
            flat[k] = re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), m.group(0)), v)

    # Override with env vars
    env_map = {
        'neo4j_uri': 'GRAPHRAG_NEO4J_URI',
        'neo4j_user': 'GRAPHRAG_NEO4J_USER',
        'neo4j_password': 'GRAPHRAG_NEO4J_PASSWORD',
        'llm_api_key': 'GRAPHRAG_LLM_API_KEY',
        'llm_base_url': 'GRAPHRAG_LLM_BASE_URL',
        'llm_model': 'GRAPHRAG_LLM_MODEL',
        'chunk_size': 'GRAPHRAG_CHUNK_SIZE',
        'chunk_overlap': 'GRAPHRAG_CHUNK_OVERLAP',
        'num_chains': 'GRAPHRAG_NUM_CHAINS',
        'num_threads_per_chain': 'GRAPHRAG_NUM_THREADS',
        'max_concurrency': 'GRAPHRAG_MAX_CONCURRENCY',
        'schema_path': 'GRAPHRAG_SCHEMA_PATH',
        'schema_mode': 'GRAPHRAG_SCHEMA_MODE',
        'second_pass_extraction': 'GRAPHRAG_SECOND_PASS_EXTRACTION',
        'second_pass_mode': 'GRAPHRAG_SECOND_PASS_MODE',
        'second_pass_min_entities': 'GRAPHRAG_SECOND_PASS_MIN_ENTITIES',
        'second_pass_min_edges': 'GRAPHRAG_SECOND_PASS_MIN_EDGES',
        'ingest_mode': 'GRAPHRAG_INGEST_MODE',
        'ingest_state_dir': 'GRAPHRAG_INGEST_STATE_DIR',
        'build_communities': 'GRAPHRAG_BUILD_COMMUNITIES',
    }
    for key, env_var in env_map.items():
        val = os.environ.get(env_var)
        if val is not None:
            flat[key] = _coerce_env_value(key, val)

    if flat.get('schema_path'):
        schema_path = Path(flat['schema_path'])
        if not schema_path.is_absolute():
            schema_path = cfg_path.parent / schema_path
        flat['schema_path'] = str(schema_path)

    config = Config(**{k: v for k, v in flat.items() if k in Config.__dataclass_fields__})

    if config.schema_path:
        from .schema_loader import load_graph_schema

        loaded_schema = load_graph_schema(config.schema_path)
        config.entity_types = loaded_schema.entity_types
        config.edge_types = loaded_schema.edge_types
        config.edge_type_map = loaded_schema.edge_type_map

    return config


def _coerce_env_value(key: str, value: str) -> Any:
    default = Config.__dataclass_fields__[key].default
    if isinstance(default, bool):
        return value.lower() in ('1', 'true', 'yes', 'on')
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    return value
