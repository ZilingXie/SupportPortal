# Repository Guidelines

## Project Structure & Module Organization
Graphiti's core library lives under `graphiti_core/`, split into domain modules such as `nodes.py`, `edges.py`, `models/`, and `search/` for retrieval pipelines. Database drivers in `graphiti_core/driver/` support Neo4j, FalkorDB, Kuzu, and Neptune. Additional core modules include `cross_encoder/` (reranking via BGE, OpenAI, and Gemini), `telemetry/` (OpenTelemetry tracing), `namespaces/` (namespace management), and `migrations/` (database migrations). Service adapters and API glue reside in `server/graph_service/`, while the MCP integration lives in `mcp_server/` (with its own `src/`, `tests/`, `config/`, and `docker/` subdirectories). Shared assets sit in `images/` and `examples/`. Tests cover the core package via `tests/`, with configuration in `conftest.py`, `pytest.ini`, and Docker compose files for optional services. Specifications live in `spec/` and type signatures in `signatures/`. Tooling manifests live at the repo root, including `pyproject.toml`, `Makefile`, and deployment compose files.

## Build, Test, and Development Commands
- `make install`: install the dev environment (`uv sync --extra dev`).
- `make format`: run `ruff` to sort imports and apply the canonical formatter.
- `make lint`: execute `ruff` plus `pyright` type checks against `graphiti_core`.
- `make test`: run unit tests only, excluding integration tests and disabling non-Neo4j drivers (`DISABLE_FALKORDB=1 DISABLE_KUZU=1 DISABLE_NEPTUNE=1 uv run pytest -m "not integration"`).
- `make check`: run format, lint, and test in sequence.
- `uv run pytest tests/path/test_file.py`: target a specific module or test selection.
- `docker-compose -f docker-compose.test.yml up`: provision local graph/search dependencies for integration flows.

## Coding Style & Naming Conventions
Python code uses 4-space indentation, 100-character lines, and prefers single quotes as configured in `pyproject.toml`. Modules, files, and functions stay snake_case; Pydantic models in `graphiti_core/models` use PascalCase with explicit type hints. Keep side-effectful code inside drivers or adapters (`graphiti_core/driver`, `graphiti_core/cross_encoder`, `graphiti_core/utils`) and rely on pure helpers elsewhere. Run `make format` before committing to normalize imports and docstring formatting.

## Testing Guidelines
Author tests alongside features under `tests/`, naming files `test_<feature>.py` and functions `test_<behavior>`. Integration test files use the `_int` suffix (e.g., `test_edge_int.py`, `test_node_int.py`). Use `@pytest.mark.integration` for database-reliant scenarios so CI can gate them; `make test` excludes these by default. Async tests run automatically via `asyncio_mode = auto` in `pytest.ini`. Reproduce regressions with a failing test first and validate fixes via `uv run pytest -k "pattern"`. Start required backing services through `docker-compose.test.yml` when running integration suites locally. The `mcp_server/` has its own separate test suite under `mcp_server/tests/`.

## Commit & Pull Request Guidelines
Commits use an imperative, present-tense summary (for example, `add async cache invalidation`) optionally suffixed with the PR number as seen in history (`(#927)`). Squash fixups and keep unrelated changes isolated. Pull requests should include: a concise description, linked tracking issue, notes about schema or API impacts, and screenshots or logs when behavior changes. Confirm `make lint` and `make test` pass locally, and update docs or examples when public interfaces shift.

## Schema Refactor Collaboration Notes
Domain entity and relation definitions should come from user-authored schema files, not from hardcoded `ENTITY_TYPES` or `EDGE_TYPES` dictionaries in ingestion scripts. The default GB/T schema lives at `schemas/gbt25338.yaml` and is referenced by `graphrag_config.yaml` under `schema.path`. Use `graphiti_rag/schema_loader.py` as the single conversion layer from YAML/JSON schema to Graphiti-compatible Pydantic models.

For follow-up work on KAG-style anti-hallucination, check `docs/SCHEMA_REFACTOR_STATUS.md` before editing. Keep changes incremental: first extend schema/config, then prompt response fields such as `official_name` and `synonyms`, then postprocessing. Do not change node naming semantics until the attributes-based normalization path has been verified.
