from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Callable, Mapping


@dataclass
class RagDeadline:
    started_at: float
    total_seconds: float
    stage_timeout_seconds: Mapping[str, float] = field(default_factory=dict)
    clock: Callable[[], float] = time.perf_counter
    timeout_stage: str | None = None

    def remaining_seconds(self, stage: str) -> float:
        elapsed = max(0.0, float(self.clock()) - float(self.started_at))
        total_remaining = max(0.0, float(self.total_seconds) - elapsed)
        stage_limit = self._stage_limit(stage)
        if stage_limit is None:
            return total_remaining
        return min(total_remaining, stage_limit)

    def mark_timeout(self, stage: str) -> None:
        normalized_stage = str(stage or "").strip() or "unknown"
        if self.timeout_stage is None:
            self.timeout_stage = normalized_stage

    def is_exhausted(self) -> bool:
        elapsed = max(0.0, float(self.clock()) - float(self.started_at))
        return elapsed >= max(0.0, float(self.total_seconds))

    def _stage_limit(self, stage: str) -> float | None:
        normalized_stage = str(stage or "").strip()
        if not normalized_stage:
            return None
        raw_value = self.stage_timeout_seconds.get(normalized_stage)
        if raw_value is None:
            return None
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return value
