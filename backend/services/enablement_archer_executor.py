"""Bounded executor for the vendored Archer Enablement Skill (direct transport)."""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.services.archer_direct_client import DirectArcherClient


ArcherEnablementOutcome = Literal[
    "enabled",
    "appid_invalid",
    "project_not_found",
    "enable_failed",
]

ARCHER_SKILL_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "archer-cross-channel-hosting"
    / "scripts"
    / "enable_cross_channel_hosting.py"
)
ARCHER_DETAIL_MAX_LENGTH = 500
ARCHER_SUCCESS_PREFIX = "开启结果：成功"
ARCHER_PROJECT_NOT_FOUND_MESSAGE = "查无项目"
_VALID_OUTCOMES = frozenset(
    {"enabled", "appid_invalid", "project_not_found", "enable_failed"}
)
_APP_ID_RE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")
_SKILL_APP_ID_RE = re.compile(r"[0-9a-fA-F]{32}")
_SECRET_VALUE_RE = re.compile(
    r'''(?ix)
    (\b(?:token|cookie|authorization|session|secret|password)\b["']?\s*[:=]\s*)
    (?:"[^"\r\n]*"|'[^'\r\n]*'|[^\r\n,;]+)
    '''
)


@dataclass(frozen=True)
class ArcherEnablementResult:
    outcome: ArcherEnablementOutcome
    detail: str

    def __post_init__(self) -> None:
        if self.outcome not in _VALID_OUTCOMES:
            raise ValueError("unsupported Archer Enablement outcome")


def _sanitize_detail(value: str, *, app_id: str) -> str:
    text = str(value or "")
    if app_id:
        text = re.sub(re.escape(app_id), "[REDACTED_APP_ID]", text, flags=re.IGNORECASE)
    text = _APP_ID_RE.sub("[REDACTED_APP_ID]", text)
    text = _SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = "".join(character if character in "\n\t" or ord(character) >= 32 else " " for character in text)
    text = " ".join(text.split())
    return text[:ARCHER_DETAIL_MAX_LENGTH]


_SKILL_MODULE: Any = None


def _load_skill_module() -> Any:
    global _SKILL_MODULE
    if _SKILL_MODULE is not None:
        return _SKILL_MODULE
    spec = importlib.util.spec_from_file_location(
        "archer_cross_channel_hosting_skill", ARCHER_SKILL_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"Archer Skill script not loadable: {ARCHER_SKILL_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    # the skill module defines dataclasses: it must be importable through
    # sys.modules or dataclass creation raises during exec_module
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _SKILL_MODULE = module
    return module


def execute_enablement_archer(
    app_id: str,
    *,
    client: Any = None,
) -> ArcherEnablementResult:
    """Run one authorized Archer attempt and return only the normalized result."""

    app_id = str(app_id or "").strip()
    if not _SKILL_APP_ID_RE.fullmatch(app_id):
        return ArcherEnablementResult(
            "appid_invalid",
            "App ID 必须是 32 位十六进制字符串",
        )
    try:
        module = _load_skill_module()
    except Exception as exc:  # noqa: BLE001 - any load failure is a bounded enable failure
        return ArcherEnablementResult(
            "enable_failed",
            _sanitize_detail(f"Archer Skill 加载失败: {exc}", app_id=app_id),
        )
    try:
        output = module.enable(app_id, client or DirectArcherClient())
    except Exception as exc:  # noqa: BLE001 - the skill raises operator-facing errors only
        return ArcherEnablementResult(
            "enable_failed",
            _sanitize_detail(f"{type(exc).__name__}: {exc}", app_id=app_id),
        )
    text = str(output or "")
    if text == ARCHER_PROJECT_NOT_FOUND_MESSAGE:
        outcome: ArcherEnablementOutcome = "project_not_found"
    elif text.startswith(ARCHER_SUCCESS_PREFIX):
        outcome = "enabled"
    else:
        outcome = "enable_failed"
    detail = _sanitize_detail(text, app_id=app_id)
    if outcome == "enable_failed" and not detail:
        detail = "Archer Skill 返回未知结果"
    return ArcherEnablementResult(outcome, detail)
