"""SupportPortal KG schema loader and validator.

Loads the official-docs KG schema YAML, computes a stable schema hash, and
exposes validation helpers that reject unknown entity / edge types when the
schema is in strict mode.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


OFFICIAL_DOCS_KG_SCHEMA_VERSION = "supportportal_official_docs_v1"

_DEFAULT_SCHEMA_PATH = "backend/config/kg/supportportal_official_docs_v1.yaml"
_VALID_SCHEMA_MODES = {"strict", "lenient"}
_WILDCARD_TYPE = "*"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KgEntityDef:
    name: str
    description: str = ""


@dataclass(frozen=True)
class KgEdgeDef:
    name: str
    description: str = ""
    from_types: tuple[str, ...] = ()
    to_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class KgSchema:
    name: str
    version: str
    description: str = ""
    mode: str = "strict"
    entities: dict[str, KgEntityDef] = field(default_factory=dict)
    edges: dict[str, KgEdgeDef] = field(default_factory=dict)

    @property
    def entity_names(self) -> frozenset[str]:
        return frozenset(self.entities.keys())

    @property
    def edge_names(self) -> frozenset[str]:
        return frozenset(self.edges.keys())


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _resolve_schema_path(path: str | None = None) -> str:
    if path:
        return path
    env_path = (os.getenv("KG_SCHEMA_PATH") or "").strip()
    if env_path:
        return env_path
    # Fall back to repo-relative default
    repo_root = _repo_root()
    return str(repo_root / _DEFAULT_SCHEMA_PATH)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_kg_schema(path: str | None = None) -> KgSchema:
    """Load and validate the KG schema from a YAML file."""

    resolved = _resolve_schema_path(path)

    raw = _load_schema_mapping(resolved)

    if not isinstance(raw, dict):
        raise KgSchemaError(f"Schema file {resolved} must be a YAML mapping")

    schema_block = raw.get("schema")
    if not isinstance(schema_block, dict):
        raise KgSchemaError(f"Schema file {resolved} missing top-level 'schema' mapping")

    name = _require_str(schema_block, "name", resolved)
    if name != OFFICIAL_DOCS_KG_SCHEMA_VERSION:
        raise KgSchemaError(
            f"Schema name '{name}' does not match expected "
            f"'{OFFICIAL_DOCS_KG_SCHEMA_VERSION}' in {resolved}"
        )

    version = _require_str(schema_block, "version", resolved)
    mode = _opt_str(schema_block, "mode", "strict").lower()
    if mode not in _VALID_SCHEMA_MODES:
        raise KgSchemaError(
            f"Schema file {resolved}: mode must be one of {sorted(_VALID_SCHEMA_MODES)}, got {mode!r}"
        )

    entities: dict[str, KgEntityDef] = {}
    for item in schema_block.get("entities") or []:
        if not isinstance(item, dict):
            continue
        ename = _require_str(item, "name", resolved)
        edesc = _opt_str(item, "description", "")
        entities[ename] = KgEntityDef(name=ename, description=edesc)

    edges: dict[str, KgEdgeDef] = {}
    for item in schema_block.get("edges") or []:
        if not isinstance(item, dict):
            continue
        ename = _require_str(item, "name", resolved)
        edesc = _opt_str(item, "description", "")
        from_raw = item.get("from")
        to_raw = item.get("to")
        from_types = _normalize_type_list(from_raw)
        to_types = _normalize_type_list(to_raw)
        edges[ename] = KgEdgeDef(
            name=ename,
            description=edesc,
            from_types=from_types,
            to_types=to_types,
        )

    schema = KgSchema(
        name=name,
        version=version,
        description=_opt_str(schema_block, "description", ""),
        mode=mode,
        entities=entities,
        edges=edges,
    )
    _validate_schema_references(schema, resolved)
    return schema


# ---------------------------------------------------------------------------
# Schema hash (stable, for upsert/chunk versioning)
# ---------------------------------------------------------------------------


def compute_schema_hash(schema: KgSchema) -> str:
    """Return a stable SHA-256 hex digest of the schema's canonical JSON form."""

    canonical = {
        "name": schema.name,
        "version": schema.version,
        "description": schema.description,
        "mode": schema.mode,
        "entities": [
            {
                "name": entity.name,
                "description": entity.description,
            }
            for entity in sorted(schema.entities.values(), key=lambda item: item.name)
        ],
        "edges": [
            {
                "name": edge.name,
                "description": edge.description,
                "from_types": list(edge.from_types),
                "to_types": list(edge.to_types),
            }
            for edge in sorted(schema.edges.values(), key=lambda item: item.name)
        ],
    }
    payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class KgSchemaError(ValueError):
    """Raised when the schema file is missing, malformed, or inconsistent."""


@dataclass(frozen=True)
class KgSchemaValidationError:
    kind: str  # "unknown_entity" | "unknown_edge"
    value: str
    message: str


def validate_entity_type(schema: KgSchema, entity_type: str) -> list[KgSchemaValidationError]:
    if schema.mode == "strict" and entity_type not in schema.entities:
        return [
            KgSchemaValidationError(
                kind="unknown_entity",
                value=entity_type,
                message=(
                    f"Entity type '{entity_type}' is not defined in KG schema "
                    f"'{schema.name}'. Known types: {sorted(schema.entity_names)}"
                ),
            )
        ]
    return []


def validate_edge_type(schema: KgSchema, edge_type: str) -> list[KgSchemaValidationError]:
    if schema.mode == "strict" and edge_type not in schema.edges:
        return [
            KgSchemaValidationError(
                kind="unknown_edge",
                value=edge_type,
                message=(
                    f"Edge type '{edge_type}' is not defined in KG schema "
                    f"'{schema.name}'. Known types: {sorted(schema.edge_names)}"
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_str(block: dict[str, Any], key: str, path: str) -> str:
    value = block.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KgSchemaError(f"Schema file {path}: '{key}' must be a non-empty string")
    return value.strip()


def _opt_str(block: dict[str, Any], key: str, default: str = "") -> str:
    value = block.get(key)
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return default
    return str(value).strip()


def _normalize_type_list(raw: object) -> tuple[str, ...]:
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list):
        return tuple(str(item).strip() for item in raw if isinstance(item, str))
    return ()


def _load_schema_mapping(path: str) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        import yaml as _yaml  # type: ignore[import-untyped]
    except ModuleNotFoundError:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = _parse_limited_schema_yaml(text)
    else:
        parsed = _yaml.safe_load(text)

    if not isinstance(parsed, dict):
        raise KgSchemaError(f"Schema file {path} must be a YAML mapping")
    return parsed


def _parse_limited_schema_yaml(text: str) -> dict[str, Any]:
    """Parse the small schema YAML subset used by this repository.

    This fallback keeps local tests useful in environments that have not yet
    installed PyYAML. Production deployments should still install `PyYAML`.
    """

    schema: dict[str, Any] = {}
    current_list: str | None = None
    current_item: dict[str, Any] | None = None
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw_line = lines[index]
        line = _strip_yaml_comment(raw_line).rstrip()
        stripped = line.strip()
        index += 1
        if not stripped or stripped == "schema:":
            continue
        if not line.startswith("  "):
            continue
        if line.startswith("  ") and not line.startswith("    "):
            key, value = _split_yaml_key_value(stripped)
            if key in {"entities", "edges"}:
                current_list = key
                schema[current_list] = []
                current_item = None
                continue
            if value == ">":
                folded: list[str] = []
                while index < len(lines) and lines[index].startswith("    "):
                    folded.append(lines[index].strip())
                    index += 1
                schema[key] = " ".join(item for item in folded if item).strip()
            else:
                schema[key] = _parse_scalar(value)
            continue
        if current_list and stripped.startswith("- "):
            key, value = _split_yaml_key_value(stripped[2:].strip())
            current_item = {key: _parse_scalar(value)}
            schema[current_list].append(current_item)
            continue
        if current_item is not None and line.startswith("      "):
            key, value = _split_yaml_key_value(stripped)
            current_item[key] = _parse_scalar(value)
    return {"schema": schema}


def _strip_yaml_comment(line: str) -> str:
    in_quote: str | None = None
    for index, char in enumerate(line):
        if char in {'"', "'"}:
            in_quote = None if in_quote == char else char
        if char == "#" and in_quote is None:
            return line[:index]
    return line


def _split_yaml_key_value(line: str) -> tuple[str, str]:
    if ":" not in line:
        raise KgSchemaError(f"Unsupported schema YAML line: {line}")
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item.strip()) for item in inner.split(",")]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _validate_schema_references(schema: KgSchema, path: str) -> None:
    if not schema.entities:
        raise KgSchemaError(f"Schema file {path}: at least one entity type is required")
    if not schema.edges:
        raise KgSchemaError(f"Schema file {path}: at least one edge type is required")

    known_types = set(schema.entity_names)
    for edge in schema.edges.values():
        for direction, type_names in (("from", edge.from_types), ("to", edge.to_types)):
            if not type_names:
                raise KgSchemaError(f"Schema file {path}: edge '{edge.name}' missing '{direction}' types")
            unknown = [
                item
                for item in type_names
                if item != _WILDCARD_TYPE and item not in known_types
            ]
            if unknown:
                raise KgSchemaError(
                    f"Schema file {path}: edge '{edge.name}' references unknown "
                    f"{direction} type(s): {unknown}"
                )
