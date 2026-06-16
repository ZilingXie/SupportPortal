"""Tests for KG schema loading, validation, and hash stability."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.services.kg_schema import (
    OFFICIAL_DOCS_KG_SCHEMA_VERSION,
    KgSchema,
    KgSchemaError,
    compute_schema_hash,
    load_kg_schema,
    validate_edge_type,
    validate_entity_type,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _default_schema_path() -> str:
    return str(_repo_root() / "backend" / "config" / "kg" / "supportportal_official_docs_v1.yaml")


# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------


def test_schema_loads_from_default_path() -> None:
    schema = load_kg_schema()
    assert schema.name == OFFICIAL_DOCS_KG_SCHEMA_VERSION
    assert schema.version == "1.0.0"
    assert schema.mode == "strict"


def test_schema_loads_from_env_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KG_SCHEMA_PATH", _default_schema_path())
    schema = load_kg_schema()
    assert schema.name == OFFICIAL_DOCS_KG_SCHEMA_VERSION


def test_schema_loads_from_explicit_path() -> None:
    schema = load_kg_schema(path=_default_schema_path())
    assert schema.name == OFFICIAL_DOCS_KG_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 9 entity types
# ---------------------------------------------------------------------------


_EXPECTED_ENTITIES = {
    "Product",
    "API",
    "Feature",
    "ErrorCode",
    "Symptom",
    "Solution",
    "Limitation",
    "Platform",
    "Version",
}


def test_nine_entities_present() -> None:
    schema = load_kg_schema()
    assert schema.entity_names == _EXPECTED_ENTITIES


def test_every_entity_has_name_and_description() -> None:
    schema = load_kg_schema()
    for name, ent in schema.entities.items():
        assert ent.name == name
        assert isinstance(ent.description, str)


# ---------------------------------------------------------------------------
# Edge types
# ---------------------------------------------------------------------------


_EXPECTED_EDGES = {
    "PROVIDES_API",
    "SUPPORTS_PLATFORM",
    "INTRODUCED_IN_VERSION",
    "DEPRECATED_IN_VERSION",
    "HAS_LIMITATION",
    "CAUSES_SYMPTOM",
    "RESOLVES_SYMPTOM",
    "RETURNS_ERROR",
    "REQUIRES_FEATURE",
    "RELATED_TO",
}


def test_ten_edges_present() -> None:
    schema = load_kg_schema()
    assert schema.edge_names == _EXPECTED_EDGES


def test_every_edge_has_name_and_description() -> None:
    schema = load_kg_schema()
    for name, edge in schema.edges.items():
        assert edge.name == name
        assert isinstance(edge.description, str)


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------


def test_schema_mode_defaults_to_strict() -> None:
    schema = load_kg_schema()
    assert schema.mode == "strict"


# ---------------------------------------------------------------------------
# Schema hash stability
# ---------------------------------------------------------------------------


def test_schema_hash_is_stable() -> None:
    schema = load_kg_schema()
    h1 = compute_schema_hash(schema)
    h2 = compute_schema_hash(schema)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex
    assert all(c in "0123456789abcdef" for c in h1)


def test_schema_hash_changes_when_entity_set_differs() -> None:
    schema_a = load_kg_schema()
    # Build a schema with one fewer entity
    fewer = dict(schema_a.entities)
    del fewer["Version"]
    schema_b = KgSchema(
        name=schema_a.name,
        version=schema_a.version,
        mode=schema_a.mode,
        entities=fewer,
        edges=schema_a.edges,
    )
    assert compute_schema_hash(schema_a) != compute_schema_hash(schema_b)


def test_schema_hash_changes_when_edge_constraints_differ() -> None:
    schema_a = load_kg_schema()
    changed_edges = dict(schema_a.edges)
    original = changed_edges["PROVIDES_API"]
    changed_edges["PROVIDES_API"] = type(original)(
        name=original.name,
        description=original.description,
        from_types=("Feature",),
        to_types=original.to_types,
    )
    schema_b = KgSchema(
        name=schema_a.name,
        version=schema_a.version,
        mode=schema_a.mode,
        entities=schema_a.entities,
        edges=changed_edges,
    )
    assert compute_schema_hash(schema_a) != compute_schema_hash(schema_b)


def test_schema_hash_changes_when_descriptions_differ() -> None:
    schema_a = load_kg_schema()
    changed_entities = dict(schema_a.entities)
    original = changed_entities["Product"]
    changed_entities["Product"] = type(original)(
        name=original.name,
        description="Changed prompt-facing description",
    )
    schema_b = KgSchema(
        name=schema_a.name,
        version=schema_a.version,
        mode=schema_a.mode,
        entities=changed_entities,
        edges=schema_a.edges,
    )
    assert compute_schema_hash(schema_a) != compute_schema_hash(schema_b)


# ---------------------------------------------------------------------------
# Strict-mode validation
# ---------------------------------------------------------------------------


def test_known_entity_passes_validation() -> None:
    schema = load_kg_schema()
    errors = validate_entity_type(schema, "Product")
    assert len(errors) == 0


def test_unknown_entity_rejected_in_strict_mode() -> None:
    schema = load_kg_schema()
    errors = validate_entity_type(schema, "UnknownGizmo")
    assert len(errors) == 1
    assert errors[0].kind == "unknown_entity"
    assert "UnknownGizmo" in errors[0].message


def test_known_edge_passes_validation() -> None:
    schema = load_kg_schema()
    errors = validate_edge_type(schema, "PROVIDES_API")
    assert len(errors) == 0


def test_unknown_edge_rejected_in_strict_mode() -> None:
    schema = load_kg_schema()
    errors = validate_edge_type(schema, "DOES_MAGIC")
    assert len(errors) == 1
    assert errors[0].kind == "unknown_edge"
    assert "DOES_MAGIC" in errors[0].message


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_nonexistent_schema_path_raises() -> None:
    with pytest.raises((FileNotFoundError, KgSchemaError)):
        load_kg_schema(path="/nonexistent/path/kg_schema.yaml")


def test_bad_schema_name_raises() -> None:
    # The loader enforces that schema.name == OFFICIAL_DOCS_KG_SCHEMA_VERSION
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as fh:
        fh.write(
            """
schema:
  name: wrong_name
  version: "1.0.0"
  mode: strict
  entities:
    - name: Product
      description: Product.
  edges:
    - name: RELATED_TO
      description: Any relation.
      from: "*"
      to: "*"
"""
        )
        tmp = fh.name
    try:
        with pytest.raises(KgSchemaError, match="wrong_name"):
            load_kg_schema(path=tmp)
    finally:
        os.unlink(tmp)


def test_bad_schema_edge_reference_raises(tmp_path: Path) -> None:
    schema_path = tmp_path / "bad_edge.yaml"
    schema_path.write_text(
        """
schema:
  name: supportportal_official_docs_v1
  version: "1.0.0"
  mode: strict
  entities:
    - name: Product
      description: Product.
  edges:
    - name: BAD_EDGE
      description: Bad reference.
      from: TypoEntity
      to: Product
""",
        encoding="utf-8",
    )
    with pytest.raises(KgSchemaError, match="TypoEntity"):
        load_kg_schema(path=str(schema_path))


def test_bad_schema_mode_raises(tmp_path: Path) -> None:
    schema_path = tmp_path / "bad_mode.yaml"
    schema_path.write_text(
        """
schema:
  name: supportportal_official_docs_v1
  version: "1.0.0"
  mode: permissive
  entities:
    - name: Product
      description: Product.
  edges:
    - name: RELATED_TO
      description: Any relation.
      from: "*"
      to: "*"
""",
        encoding="utf-8",
    )
    with pytest.raises(KgSchemaError, match="mode"):
        load_kg_schema(path=str(schema_path))
