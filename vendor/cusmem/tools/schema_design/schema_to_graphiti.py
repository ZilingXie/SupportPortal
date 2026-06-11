from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.schema_design.io_utils import yaml_load


def schema_to_pydantic_models(schema_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Convert selected_schema.yaml to Pydantic models usable by Graphiti.

    Returns (entity_types, edge_types) — dicts of name → Pydantic model class,
    ready to pass to Graphiti(..., entity_types=entity_types, edge_types=edge_types).

    Usage:
        from tools.schema_design.schema_to_graphiti import schema_to_pydantic_models

        entity_types, edge_types = schema_to_pydantic_models(Path('selected_schema.yaml'))

        graphiti = Graphiti(...)
        await graphiti.add_episode(
            name='doc_001',
            episode_body=document_text,
            source=EpisodeSource.text,
            entity_types=entity_types,
            edge_types=edge_types,
        )
    """
    schema = yaml_load(schema_path)
    entity_types = _build_entity_models(schema.get('entity_types', {}))
    edge_types = _build_edge_models(schema.get('edge_types', {}))
    return entity_types, edge_types


def load_entity_types(schema_path: Path) -> dict[str, Any]:
    """Load only entity type Pydantic models from a schema YAML."""
    schema = yaml_load(schema_path)
    return _build_entity_models(schema.get('entity_types', {}))


def load_edge_types(schema_path: Path) -> dict[str, Any]:
    """Load only edge type Pydantic models from a schema YAML."""
    schema = yaml_load(schema_path)
    return _build_edge_models(schema.get('edge_types', {}))


# ── Internal builders ──────────────────────────────────────────────────────

try:
    from pydantic import BaseModel, Field, create_model
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False


def _build_entity_models(entity_types: dict[str, Any]) -> dict[str, Any]:
    """Build Pydantic EntityNode subclasses from schema entity type definitions."""
    if not HAS_PYDANTIC:
        raise ImportError('pydantic is required. Install with: pip install pydantic')

    # Try to import Graphiti's base entity class
    try:
        from graphiti_core.nodes import EntityNode
        base = EntityNode
    except ImportError:
        base = BaseModel

    models = {}
    for name, spec in entity_types.items():
        if not isinstance(spec, dict):
            continue

        description = spec.get('description', name)
        good_examples = spec.get('good_examples', [])
        bad_examples = spec.get('bad_examples', [])

        # Build field annotations
        fields: dict[str, Any] = {
            'name': (str, Field(..., description='实体名称')),
        }

        # Add custom properties from schema
        for prop_name, prop_spec in (spec.get('properties') or {}).items():
            if isinstance(prop_spec, dict):
                prop_type_str = prop_spec.get('type', 'string')
                prop_type = _type_from_string(prop_type_str)
                fields[prop_name] = (
                    prop_type | None,
                    Field(default=None, description=prop_spec.get('description', '')),
                )

        model = create_model(
            name,
            __base__=base,
            __doc__=description,
            **fields,
        )

        # Attach examples as class attributes for reference
        model.__good_examples__ = good_examples
        model.__bad_examples__ = bad_examples

        models[name] = model

    return models


def _build_edge_models(edge_types: dict[str, Any]) -> dict[str, Any]:
    """Build Pydantic EntityEdge subclasses from schema edge type definitions."""
    if not HAS_PYDANTIC:
        raise ImportError('pydantic is required. Install with: pip install pydantic')

    try:
        from graphiti_core.edges import EntityEdge
        base = EntityEdge
    except ImportError:
        base = BaseModel

    models = {}
    for name, spec in edge_types.items():
        if not isinstance(spec, dict):
            continue

        description = spec.get('description', name)
        source_types = spec.get('source_types', [])
        target_types = spec.get('target_types', [])
        trigger_words = spec.get('trigger_words', [])

        fields: dict[str, Any] = {
            'name': (str, Field(..., description='关系事实描述')),
        }

        model = create_model(
            name,
            __base__=base,
            __doc__=description,
            **fields,
        )

        # Attach Graphiti-compatible metadata
        model.__source_types__ = source_types
        model.__target_types__ = target_types
        model.__trigger_words__ = trigger_words

        models[name] = model

    return models


def _type_from_string(type_str: str) -> type:
    """Convert YAML type string to Python type."""
    mapping = {
        'string': str,
        'str': str,
        'integer': int,
        'int': int,
        'number': float,
        'float': float,
        'boolean': bool,
        'bool': bool,
        'datetime': str,  # Graphiti uses ISO strings for datetimes
        'list[string]': list[str],
        'list[str]': list[str],
        'number|string': str,  # ambiguous → string is safer
    }
    return mapping.get(type_str, str)
