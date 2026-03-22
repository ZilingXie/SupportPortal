from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


RAG_TABLE_PREFIXES = ("support_knowledge_", "support_rag_")


@dataclass(frozen=True, order=True)
class TableRef:
    schema: str
    name: str

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


def split_table_name(raw_value: str, default_schema: str) -> tuple[str, str]:
    normalized = str(raw_value or "").strip()
    if not normalized:
        raise ValueError("table name must not be empty")
    if "." not in normalized:
        return default_schema, normalized
    schema, table_name = normalized.split(".", 1)
    schema = schema.strip() or default_schema
    table_name = table_name.strip()
    if not table_name:
        raise ValueError("table name must not be empty")
    return schema, table_name


def select_rag_reset_targets(
    tables: Iterable[TableRef],
    *,
    app_schema: str,
    vector_table: str,
) -> list[TableRef]:
    vector_schema, vector_table_name = split_table_name(vector_table, app_schema)
    targets: set[TableRef] = set()
    for table in tables:
        if table.schema == app_schema and table.name.startswith(RAG_TABLE_PREFIXES):
            targets.add(table)
            continue
        if table.schema == vector_schema and table.name == vector_table_name:
            targets.add(table)
    return sorted(targets, key=lambda table: (table.schema, table.name))
