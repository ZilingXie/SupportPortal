"""Bounded executor for the vendored Archer Enablement Skill."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


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
ARCHER_ENABLEMENT_TIMEOUT_SECONDS = 330.0
ARCHER_DETAIL_MAX_LENGTH = 500
_VALID_OUTCOMES = frozenset(
    {"enabled", "appid_invalid", "project_not_found", "enable_failed"}
)
_APP_ID_RE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")
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


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def execute_enablement_archer(
    app_id: str,
    *,
    script_path: Path = ARCHER_SKILL_SCRIPT,
    timeout_seconds: float = ARCHER_ENABLEMENT_TIMEOUT_SECONDS,
    pilot_bin: str | None = None,
    xdg_config_home: str | None = None,
) -> ArcherEnablementResult:
    """Run one authorized Archer attempt and return only the normalized result."""
    app_id = str(app_id or "").strip()
    environment = os.environ.copy()
    environment["PILOT_BIN"] = str(
        pilot_bin or environment.get("PILOT_BIN") or "/app/bin/pilot"
    )
    environment["XDG_CONFIG_HOME"] = str(
        xdg_config_home or environment.get("XDG_CONFIG_HOME") or "/var/lib/pilot"
    )
    try:
        process = subprocess.Popen(
            [sys.executable, str(script_path), app_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        return ArcherEnablementResult(
            "enable_failed",
            _sanitize_detail(f"Archer Skill could not start: {exc}", app_id=app_id),
        )

    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        return ArcherEnablementResult("enable_failed", "Archer Skill timed out")

    output_lines = stdout.splitlines()
    first_line = output_lines[0].strip() if output_lines else ""
    if process.returncode == 0 and first_line == "开启结果：成功":
        outcome: ArcherEnablementOutcome = "enabled"
    elif process.returncode == 2 and first_line == "关键词必须为整数或 32 位字符串":
        outcome = "appid_invalid"
    elif process.returncode == 3 and first_line == "查无项目":
        outcome = "project_not_found"
    else:
        outcome = "enable_failed"
    detail_source = "\n".join(part for part in (stdout, stderr) if part.strip())
    detail = _sanitize_detail(detail_source, app_id=app_id)
    if outcome == "enable_failed" and not detail:
        detail = f"Archer Skill exited with code {process.returncode}"
    return ArcherEnablementResult(outcome, detail)
