from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.schema_design.io_utils import ensure_dir, write_json


@dataclass
class PipelineState:
    path: Path
    data: dict[str, Any]

    @classmethod
    def load(cls, path: Path, *, input_path: Path | None = None) -> 'PipelineState':
        if path.exists():
            import json

            data = json.loads(path.read_text(encoding='utf-8'))
        else:
            data = {
                'version': '1.0',
                'pipeline_run_id': datetime.now().strftime('run_%Y%m%d_%H%M%S'),
                'input_path': str(input_path) if input_path else '',
                'stages': {},
            }
        data.setdefault('stages', {})
        return cls(path=path, data=data)

    def is_completed(self, stage_name: str, *, input_paths: list[Path] | None = None) -> bool:
        entry = self.data.get('stages', {}).get(stage_name, {})
        if not entry.get('completed'):
            return False
        expected = entry.get('input_hashes', {})
        if not expected:
            return True
        return expected == compute_input_hashes(input_paths or [])

    def mark_completed(
        self,
        stage_name: str,
        result: dict[str, Any],
        *,
        input_paths: list[Path] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        output_files = result.get('output_files', {})
        self.data.setdefault('stages', {})[stage_name] = {
            'completed': True,
            'outputs': {key: str(value) for key, value in output_files.items()},
            'metrics': result.get('metrics', {}),
            'input_hashes': compute_input_hashes(input_paths or []),
            'timestamp': datetime.now().isoformat(timespec='seconds'),
        }
        if extra:
            self.data['stages'][stage_name].update(extra)
        self.save()

    def save(self) -> None:
        ensure_dir(self.path.parent)
        write_json(self.path, self.data)


def compute_input_hashes(paths: list[Path]) -> dict[str, str]:
    hashes = {}
    for path in paths:
        if not path.exists():
            hashes[str(path)] = 'missing'
        elif path.is_dir():
            digest = hashlib.sha256()
            for child in sorted(p for p in path.rglob('*') if p.is_file()):
                digest.update(str(child.relative_to(path)).encode())
                digest.update(child.read_bytes())
            hashes[str(path)] = f'sha256:{digest.hexdigest()}'
        else:
            hashes[str(path)] = f'sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}'
    return hashes
