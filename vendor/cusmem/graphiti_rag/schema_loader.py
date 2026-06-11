"""Load user-authored graph schemas into Graphiti-compatible Pydantic types."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, create_model


@dataclass(frozen=True)
class LoadedSchema:
    """Graphiti-ready schema types plus the raw user schema."""

    entity_types: dict[str, type[BaseModel]]
    edge_types: dict[str, type[BaseModel]]
    edge_type_map: dict[tuple[str, str], list[str]]
    raw: dict[str, Any]


_SCALAR_TYPES: dict[str, Any] = {
    'str': str,
    'string': str,
    'text': str,
    'int': int,
    'integer': int,
    'float': float,
    'number': float,
    'bool': bool,
    'boolean': bool,
    'dict': dict[str, Any],
    'object': dict[str, Any],
    'any': Any,
}


def load_graph_schema(path: str | Path) -> LoadedSchema:
    """Load a YAML/JSON schema file and convert it to Graphiti's type dictionaries."""

    schema_path = Path(path)
    raw = _load_mapping(schema_path)
    entity_specs = raw.get('entity_types') or {}
    edge_specs = raw.get('edge_types') or {}

    if not isinstance(entity_specs, dict):
        raise ValueError('schema entity_types must be a mapping')
    if not isinstance(edge_specs, dict):
        raise ValueError('schema edge_types must be a mapping')

    entity_types = {
        type_name: _build_model(type_name, spec, model_suffix='EntityType')
        for type_name, spec in entity_specs.items()
    }
    edge_types = {
        type_name: _build_model(type_name, spec, model_suffix='EdgeType', is_edge=True)
        for type_name, spec in edge_specs.items()
    }
    edge_type_map = _build_edge_type_map(edge_specs)

    return LoadedSchema(
        entity_types=entity_types,
        edge_types=edge_types,
        edge_type_map=edge_type_map,
        raw=raw,
    )


def _load_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f'Schema file not found: {path}')

    if path.suffix.lower() in ('.yaml', '.yml'):
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError('PyYAML is required to load YAML schema files') from exc
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    elif path.suffix.lower() == '.json':
        data = json.loads(path.read_text(encoding='utf-8'))
    else:
        raise ValueError(f'Unsupported schema file extension: {path.suffix}')

    if not isinstance(data, dict):
        raise ValueError('Schema file must contain a mapping at the top level')
    return data


def _build_model(
    type_name: str,
    spec: Any,
    *,
    model_suffix: str,
    is_edge: bool = False,
) -> type[BaseModel]:
    _validate_type_name(type_name)
    normalized = _normalize_type_spec(spec)
    fields = _build_property_fields(normalized.get('properties') or {})
    model_name = f'{_to_model_name(type_name)}{model_suffix}'
    model = create_model(model_name, __base__=BaseModel, **fields)
    model.__doc__ = _build_docstring(type_name, normalized, is_edge=is_edge)
    return model


def _normalize_type_spec(spec: Any) -> dict[str, Any]:
    if spec is None:
        return {}
    if isinstance(spec, str):
        return {'description': spec}
    if not isinstance(spec, dict):
        raise ValueError('Each schema type must be a mapping or description string')
    return spec


def _build_property_fields(properties: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    if not isinstance(properties, dict):
        raise ValueError('schema properties must be a mapping')

    fields: dict[str, tuple[Any, Any]] = {}
    for property_name, property_spec in properties.items():
        _validate_type_name(property_name, label='property')
        normalized = _normalize_property_spec(property_spec)
        annotation = _parse_property_type(normalized.get('type', 'string')) | None
        fields[property_name] = (
            annotation,
            Field(default=None, description=normalized.get('description') or ''),
        )
    return fields


def _normalize_property_spec(spec: Any) -> dict[str, Any]:
    if spec is None:
        return {'type': 'string'}
    if isinstance(spec, str):
        return {'type': spec}
    if not isinstance(spec, dict):
        raise ValueError('Each schema property must be a mapping or type string')
    return spec


def _parse_property_type(type_spec: Any) -> Any:
    if not isinstance(type_spec, str):
        return Any

    normalized = type_spec.strip().lower().replace(' ', '')
    list_match = re.fullmatch(r'(?:list|array)\[(.+)\]', normalized)
    if list_match:
        return list[_parse_property_type(list_match.group(1))]

    if normalized in ('list', 'array'):
        return list[str]

    return _SCALAR_TYPES.get(normalized, str)


def _build_edge_type_map(edge_specs: dict[str, Any]) -> dict[tuple[str, str], list[str]]:
    edge_type_map: dict[tuple[str, str], list[str]] = {}
    for edge_name, spec in edge_specs.items():
        _validate_type_name(edge_name, label='edge type')
        normalized = _normalize_type_spec(spec)
        source_types = _as_type_list(normalized.get('source_types'), default=['Entity'])
        target_types = _as_type_list(normalized.get('target_types'), default=['Entity'])
        for source_type in source_types:
            for target_type in target_types:
                edge_type_map.setdefault((source_type, target_type), []).append(edge_name)
    return edge_type_map


def _as_type_list(value: Any, *, default: list[str]) -> list[str]:
    if value is None:
        return default
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError('source_types and target_types must be strings or lists')

    cleaned = [str(item) for item in items if str(item).strip()]
    for item in cleaned:
        _validate_type_name(item, label='entity type reference')
    return cleaned or default


def _build_docstring(type_name: str, spec: dict[str, Any], *, is_edge: bool) -> str:
    parts = []
    description = spec.get('description')
    if description:
        parts.append(str(description))

    ontology = spec.get('ontology') or spec.get('ontology_path')
    if ontology:
        parts.append(f'领域本体: {ontology}')

    if is_edge:
        source_types = ', '.join(_as_type_list(spec.get('source_types'), default=['Entity']))
        target_types = ', '.join(_as_type_list(spec.get('target_types'), default=['Entity']))
        parts.append(f'允许头实体类型: {source_types}')
        parts.append(f'允许尾实体类型: {target_types}')

    properties = spec.get('properties') or {}
    if isinstance(properties, dict) and properties:
        property_parts = []
        for property_name, property_spec in properties.items():
            normalized = _normalize_property_spec(property_spec)
            field_type = normalized.get('type', 'string')
            field_desc = normalized.get('description')
            if field_desc:
                property_parts.append(f'{property_name}({field_type}): {field_desc}')
            else:
                property_parts.append(f'{property_name}({field_type})')
        parts.append('属性: ' + '; '.join(property_parts))

    return '\n'.join(parts) or f'{type_name} schema type'


def _validate_type_name(name: str, *, label: str = 'entity type') -> None:
    if not isinstance(name, str) or not name.isidentifier():
        raise ValueError(f'Invalid {label} name: {name}')


def _to_model_name(type_name: str) -> str:
    return ''.join(part.capitalize() for part in type_name.split('_') if part) or 'Schema'
