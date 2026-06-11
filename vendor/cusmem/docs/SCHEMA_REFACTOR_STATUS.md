# Schema Refactor Status

Date: 2026-06-03

## Goal

Move domain entity and relation definitions out of Python source code and into user-authored
schema files, while keeping the existing Graphiti ingestion/search flow intact.

## Completed In This Pass

- Added `graphiti_rag/schema_loader.py`.
- Added `schemas/gbt25338.yaml` as the default GB/T 25338.1-2019 schema.
- Extended `graphiti_rag/config.py` with `schema_path`, `schema_mode`, and `edge_type_map`.
- Updated `graphiti_rag/config_loader.py` to load `schema.path` from `graphrag_config.yaml`.
- Updated `graphiti_rag/pipeline.py` and `graphiti_rag/components.py` to pass schema-derived
  `edge_type_map` into `Graphiti.add_episode`.
- Simplified `ingest_gbt.py` so it no longer defines hardcoded `ENTITY_TYPES` and `EDGE_TYPES`.
- Updated `graphrag_config.yaml` to point at `schemas/gbt25338.yaml`.

## Current Behavior

Users can edit or replace the YAML file referenced by:

```yaml
schema:
  path: "schemas/gbt25338.yaml"
  mode: "strict"
```

The loader dynamically creates Pydantic models for Graphiti:

- `entity_types`: model docstrings are built from `description`, `ontology`, and `properties`.
- `edge_types`: model docstrings are built from `description`, allowed source types, and allowed
  target types.
- `edge_type_map`: built from each edge type's `source_types` and `target_types`.

Entity properties in YAML become optional Pydantic fields, so Graphiti's existing attribute
extraction path can use them without changing the database layer.

## Not Done Yet

- `ExtractedEntity` still returns only `name`, `entity_type_id`, and `episode_indices`.
- KAG-style `official_name`, `synonyms`, and `ontology_path` extraction fields are not yet wired
  into the node extraction response model.
- A dedicated postprocessor for invalid node/edge filtering has not been added yet.
- The edge endpoint policy is still Graphiti's default behavior. KAG's "at least one endpoint in
  entity_list" relaxation has not been enabled.

## Guidance For Other Agents

- Do not reintroduce hardcoded domain-specific `ENTITY_TYPES` or `EDGE_TYPES` in ingestion scripts.
- Add or adjust domain schema in `schemas/*.yaml`.
- Keep `graphiti_rag/schema_loader.py` as the single conversion point from user schema to Graphiti
  Pydantic models.
- If adding KAG-style entity normalization, prefer optional fields first and store them in node
  attributes before changing node names.
