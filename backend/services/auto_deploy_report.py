from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    AUTO_DEPLOY_REPORT_SCENARIO,
    profile_has_invocation_credentials,
    resolve_model_profile,
)

DEFAULT_REPORT_SERVICES = (
    "api",
    "nginx",
    "rag_api",
    "ws_gateway",
    "worker_query",
    "worker_aux",
    "rag_worker",
)
DEFAULT_REPORT_TIMEZONE = "Asia/Shanghai"
DEFAULT_REPORT_LOG_SINCE = "24h"
DEFAULT_REPORT_LOG_LINES_PER_SERVICE = 120
DEFAULT_REPORT_MAX_LOG_CHARS = 12000
DEFAULT_SUSPICIOUS_EXCERPT_MAX_CHARS = 4000
SUSPICIOUS_LOG_PATTERN = re.compile(
    r"(error|exception|traceback|failed|failure|warn|critical|timeout|refused|denied|panic)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AutoDeployReportContext:
    status: str
    execution_mode: str
    host: str
    branch: str
    local_commit: str
    remote_commit: str
    failed_step: str
    domain: str
    started_at_utc: str
    ended_at_utc: str
    duration_seconds: int
    internal_health_status: str
    internal_health_detail: str
    external_health_status: str
    external_health_detail: str
    run_log_tail: str
    report_timezone: str = DEFAULT_REPORT_TIMEZONE


@dataclass(frozen=True)
class DockerDiagnostics:
    service_status_text: str
    service_logs_text: str
    suspicious_excerpt_text: str


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _safe_positive_int(value: str | None, default: int) -> int:
    raw = (value or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _parse_context(value: dict[str, Any]) -> AutoDeployReportContext:
    return AutoDeployReportContext(
        status=str(value.get("status") or "failed"),
        execution_mode=str(value.get("execution_mode") or "unknown"),
        host=str(value.get("host") or "unknown"),
        branch=str(value.get("branch") or "unknown"),
        local_commit=str(value.get("local_commit") or "unknown"),
        remote_commit=str(value.get("remote_commit") or "unknown"),
        failed_step=str(value.get("failed_step") or "none"),
        domain=str(value.get("domain") or ""),
        started_at_utc=str(value.get("started_at_utc") or ""),
        ended_at_utc=str(value.get("ended_at_utc") or ""),
        duration_seconds=int(value.get("duration_seconds") or 0),
        internal_health_status=str(value.get("internal_health_status") or "not-run"),
        internal_health_detail=str(value.get("internal_health_detail") or ""),
        external_health_status=str(value.get("external_health_status") or "not-run"),
        external_health_detail=str(value.get("external_health_detail") or ""),
        run_log_tail=str(value.get("run_log_tail") or ""),
        report_timezone=str(value.get("report_timezone") or DEFAULT_REPORT_TIMEZONE),
    )


def _load_env_file(env_file: Path) -> None:
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _parse_iso8601(value: str, *, fallback: datetime | None = None) -> datetime:
    raw = (value or "").strip()
    if not raw:
        return fallback or datetime.now(UTC)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return fallback or datetime.now(UTC)


def _report_now_utc() -> datetime:
    override = (os.getenv("AUTO_DEPLOY_REPORT_NOW_UTC") or "").strip()
    return _parse_iso8601(override, fallback=datetime.now(UTC)) if override else datetime.now(UTC)


def _resolve_report_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_REPORT_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_REPORT_TIMEZONE)


def _date_label(context: AutoDeployReportContext) -> str:
    ended_at = _parse_iso8601(context.ended_at_utc, fallback=_report_now_utc())
    local_dt = ended_at.astimezone(_resolve_report_timezone(context.report_timezone))
    return f"{local_dt.month}/{local_dt.day}"


def sanitize_log_text(text: str, *, max_chars: int) -> str:
    cleaned = (text or "").replace("\x00", "")
    cleaned = cleaned.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    if len(cleaned) <= max_chars:
        return cleaned.strip()
    return f"{cleaned[:max_chars].rstrip()}\n[truncated]"


def _extract_suspicious_excerpt(text: str, *, max_chars: int) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return "未采集到可疑原始日志。"

    lines = normalized.splitlines()
    selected_indexes: list[int] = []
    for index, line in enumerate(lines):
        if not SUSPICIOUS_LOG_PATTERN.search(line):
            continue
        for candidate in range(max(index - 1, 0), min(index + 2, len(lines))):
            if candidate not in selected_indexes:
                selected_indexes.append(candidate)

    if selected_indexes:
        excerpt = "\n".join(lines[index] for index in selected_indexes)
    else:
        excerpt = "\n".join(lines[-20:])

    return sanitize_log_text(excerpt, max_chars=max_chars)


def _compose_base_command(*, compose_file: Path, env_file: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(compose_file),
    ]


def _run_compose_command(*, compose_file: Path, env_file: Path, args: list[str]) -> tuple[int, str]:
    command = [*_compose_base_command(compose_file=compose_file, env_file=env_file), *args]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    combined = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part).strip()
    return result.returncode, combined


def collect_docker_diagnostics(
    *,
    compose_file: Path,
    env_file: Path,
    services: tuple[str, ...] = DEFAULT_REPORT_SERVICES,
    since: str | None = None,
    lines_per_service: int | None = None,
    max_log_chars: int | None = None,
) -> DockerDiagnostics:
    log_since = (since or os.getenv("DEPLOY_REPORT_LOG_SINCE") or DEFAULT_REPORT_LOG_SINCE).strip()
    per_service_lines = _safe_positive_int(
        os.getenv("DEPLOY_REPORT_LOG_LINES_PER_SERVICE"),
        lines_per_service or DEFAULT_REPORT_LOG_LINES_PER_SERVICE,
    )
    max_chars = _safe_positive_int(
        os.getenv("DEPLOY_REPORT_MAX_LOG_CHARS"),
        max_log_chars or DEFAULT_REPORT_MAX_LOG_CHARS,
    )

    if shutil.which("docker") is None:
        unavailable = "docker compose unavailable: docker command not found."
        return DockerDiagnostics(unavailable, unavailable, unavailable)

    if not compose_file.exists():
        unavailable = f"docker compose unavailable: compose file not found: {compose_file}"
        return DockerDiagnostics(unavailable, unavailable, unavailable)

    status_code, service_status = _run_compose_command(
        compose_file=compose_file,
        env_file=env_file,
        args=["ps"],
    )
    if status_code != 0:
        service_status = f"docker compose ps failed.\n{service_status}".strip()
    service_status = sanitize_log_text(service_status or "(no service status output)", max_chars=max_chars)

    logs_code, service_logs = _run_compose_command(
        compose_file=compose_file,
        env_file=env_file,
        args=[
            "logs",
            "--no-color",
            "--since",
            log_since,
            "--tail",
            str(per_service_lines),
            *services,
        ],
    )
    if logs_code != 0:
        service_logs = f"docker compose logs failed.\n{service_logs}".strip()
    service_logs = sanitize_log_text(service_logs or "(no docker logs collected)", max_chars=max_chars)
    suspicious_excerpt = _extract_suspicious_excerpt(
        service_logs,
        max_chars=min(max_chars, DEFAULT_SUSPICIOUS_EXCERPT_MAX_CHARS),
    )
    return DockerDiagnostics(service_status, service_logs, suspicious_excerpt)


def build_ai_analysis(
    *,
    context: AutoDeployReportContext | dict[str, Any],
    diagnostics: DockerDiagnostics | dict[str, str],
) -> str:
    report_context = _parse_context(context) if isinstance(context, dict) else context
    report_diagnostics = (
        DockerDiagnostics(**diagnostics) if isinstance(diagnostics, dict) else diagnostics
    )

    if not _env_flag("DEPLOY_REPORT_ENABLE_AI", True):
        return "AI analysis unavailable: DEPLOY_REPORT_ENABLE_AI=false."
    if not report_diagnostics.service_logs_text.strip():
        return "AI analysis unavailable: no docker logs collected."

    profile = resolve_model_profile(AUTO_DEPLOY_REPORT_SCENARIO)
    if not profile_has_invocation_credentials(profile):
        return "AI analysis unavailable: OPENAI_API_KEY or DEEPSEEK_API_KEY missing."

    system_prompt = (
        "你是 SupportPortal 的运维日报分析助手。"
        "请基于给定的运行摘要和 docker 日志，输出一段简洁中文分析。"
        "只关注明确错误、可疑风险、以及是否需要人工跟进。"
        "输出必须是纯文本，最多 6 行，每行一句。"
    )
    user_prompt = "\n".join(
        [
            f"运行状态: {report_context.status}",
            f"执行模式: {report_context.execution_mode}",
            f"主机: {report_context.host}",
            f"分支: {report_context.branch}",
            f"当前提交: {report_context.local_commit}",
            f"远端提交: {report_context.remote_commit}",
            f"失败步骤: {report_context.failed_step}",
            f"Internal health: {report_context.internal_health_status} | {report_context.internal_health_detail}",
            f"External health: {report_context.external_health_status} | {report_context.external_health_detail}",
            "",
            "docker compose ps:",
            report_diagnostics.service_status_text,
            "",
            "docker logs excerpt:",
            report_diagnostics.service_logs_text,
        ]
    )
    try:
        result = invoke_responses_text(
            profile=profile,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    except (LlmInvocationError, ValueError) as exc:
        return f"AI analysis unavailable: {exc}"

    summary = (result.text or "").strip()
    return summary or "AI analysis unavailable: empty model response."


def build_report_email_payload(
    *,
    context: AutoDeployReportContext | dict[str, Any],
    diagnostics: DockerDiagnostics | dict[str, str],
    ai_analysis: str,
    from_address: str,
    to_addresses: list[str],
) -> dict[str, Any]:
    report_context = _parse_context(context) if isinstance(context, dict) else context
    report_diagnostics = (
        DockerDiagnostics(**diagnostics) if isinstance(diagnostics, dict) else diagnostics
    )
    subject_prefix = "[Failed] " if report_context.status != "success" else ""
    subject = f"{subject_prefix}SupportPortal Report {_date_label(report_context)}"

    body = "\n".join(
        [
            "运行摘要",
            f"运行状态：{report_context.status}",
            f"执行模式：{report_context.execution_mode}",
            f"主机：{report_context.host}",
            f"分支：{report_context.branch}",
            f"当前提交：{report_context.local_commit}",
            f"远端提交：{report_context.remote_commit}",
            f"失败步骤：{report_context.failed_step}",
            f"开始时间（UTC）：{report_context.started_at_utc}",
            f"结束时间（UTC）：{report_context.ended_at_utc}",
            f"耗时（秒）：{report_context.duration_seconds}",
            "",
            "健康检查",
            f"Internal：{report_context.internal_health_status}",
            report_context.internal_health_detail or "(no internal health detail)",
            f"External：{report_context.external_health_status}",
            report_context.external_health_detail or "(no external health detail)",
            "",
            "服务状态",
            report_diagnostics.service_status_text or "(no service status output)",
            "",
            "AI 日志分析",
            ai_analysis or "AI analysis unavailable: empty analysis.",
            "",
            "可疑原始日志",
            report_diagnostics.suspicious_excerpt_text or report_context.run_log_tail or "(no suspicious raw log excerpt)",
            "",
            "回退诊断",
            report_context.run_log_tail or "(no run log tail)",
        ]
    )

    return {
        "FromEmailAddress": from_address,
        "Destination": {"ToAddresses": to_addresses},
        "Content": {
            "Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            }
        },
    }


def generate_report_payload(
    *,
    context_file: Path,
    project_root: Path,
    env_file: Path,
    compose_file: Path,
) -> dict[str, Any]:
    _load_env_file(env_file)
    context = _parse_context(json.loads(context_file.read_text(encoding="utf-8")))
    diagnostics = collect_docker_diagnostics(compose_file=compose_file, env_file=env_file)
    ai_analysis = build_ai_analysis(context=context, diagnostics=diagnostics)
    to_addresses = [item.strip() for item in (os.getenv("DEPLOY_ALERT_TO") or "").split(",") if item.strip()]
    from_address = (os.getenv("DEPLOY_ALERT_FROM") or "").strip()
    return build_report_email_payload(
        context=context,
        diagnostics=diagnostics,
        ai_analysis=ai_analysis,
        from_address=from_address,
        to_addresses=to_addresses,
    )
