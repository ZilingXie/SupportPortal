from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    ensure_dir(path.parent)
    with path.open('w', encoding='utf-8') as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    return path


def write_json(path: Path, data: Any) -> Path:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
    return path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def yaml_dump(data: Any) -> str:
    try:
        import yaml

        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    except Exception:
        return _simple_yaml_dump(data)


def yaml_load(path: Path) -> dict[str, Any]:
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except Exception as exc:
        raise RuntimeError('PyYAML is required to read schema design YAML artifacts') from exc
    if not isinstance(data, dict):
        raise ValueError(f'YAML artifact must contain a mapping: {path}')
    return data


def write_yaml(path: Path, data: Any) -> Path:
    ensure_dir(path.parent)
    path.write_text(yaml_dump(data), encoding='utf-8')
    return path


def _simple_yaml_dump(data: Any, indent: int = 0) -> str:
    prefix = ' ' * indent
    if isinstance(data, dict):
        lines = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f'{prefix}{key}:')
                lines.append(_simple_yaml_dump(value, indent + 2))
            else:
                lines.append(f'{prefix}{key}: {json.dumps(value, ensure_ascii=False)}')
        return '\n'.join(lines)
    if isinstance(data, list):
        lines = []
        for value in data:
            if isinstance(value, (dict, list)):
                lines.append(f'{prefix}-')
                lines.append(_simple_yaml_dump(value, indent + 2))
            else:
                lines.append(f'{prefix}- {json.dumps(value, ensure_ascii=False)}')
        return '\n'.join(lines)
    return f'{prefix}{json.dumps(data, ensure_ascii=False)}'
