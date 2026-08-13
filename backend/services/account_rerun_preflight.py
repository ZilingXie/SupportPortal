from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Callable

from backend.repositories.ticket_repository import account_case_upsert_contract
from backend.services.account_route_pipeline import (
    ACCOUNT_AGORA_PROMPT_KEY,
    ACCOUNT_AUTOMATION_PROMPT_KEY,
    ACCOUNT_BACKEND_OPERATION_PROMPT_KEY,
    ACCOUNT_BILLING_PROMPT_KEY,
    ACCOUNT_INTENT_PROMPT_KEY,
)
from backend.services.llm_profiles import ACCOUNT_ROUTE_SCENARIO, resolve_model_profile
from backend.services.account_ai_execution import account_profile, account_profile_has_primary_credentials
from backend.services.llm_factory import invoke_responses_text
from backend.services.prompt_runtime import resolve_system_prompt, prompt_runtime_info
from backend.services.prompts.account_routing import (
    build_account_agora_system_prompt,
    build_account_automation_system_prompt,
    build_account_backend_operation_system_prompt,
    build_account_billing_system_prompt,
    build_account_intent_system_prompt,
)


@dataclass(frozen=True)
class AccountRerunPreflightResult:
    ok: bool
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, "checks": self.checks}


_PROMPTS: tuple[tuple[str, str, Callable[[], str]], ...] = (
    (ACCOUNT_INTENT_PROMPT_KEY, "intent_classifier", build_account_intent_system_prompt),
    (ACCOUNT_AGORA_PROMPT_KEY, "agora_router", build_account_agora_system_prompt),
    (ACCOUNT_BILLING_PROMPT_KEY, "account_billing_router", build_account_billing_system_prompt),
    (ACCOUNT_BACKEND_OPERATION_PROMPT_KEY, "backend_operation_router", build_account_backend_operation_system_prompt),
    (ACCOUNT_AUTOMATION_PROMPT_KEY, "automation_router", build_account_automation_system_prompt),
)


def _safe_preflight_error(value: Any, *, limit: int = 240) -> str:
    message = " ".join(str(value or "").split())
    message = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "<redacted-email>", message, flags=re.I)
    message = re.sub(
        r"(?i)\b(app[_ -]?id|token|secret|password|authorization)\s*[:=]\s*[^,;\s]+",
        r"\1=<redacted>",
        message,
    )
    message = re.sub(r"\b(?:bearer\s+)?[A-Za-z0-9_-]{28,}\b", "<redacted-token>", message, flags=re.I)
    return message[:limit] or "preflight failed"


def _check_sql_contract() -> dict[str, Any]:
    contract = account_case_upsert_contract()
    return {"status": "passed" if contract["consistent"] else "failed", **contract}


def _check_prompt_runtime() -> dict[str, Any]:
    info = prompt_runtime_info()
    snapshots: dict[str, str] = {}
    for prompt_key, name, fallback in _PROMPTS:
        content = resolve_system_prompt(prompt_key, fallback())
        if not str(content or "").strip():
            return {"status": "failed", "reason": f"empty_prompt:{name}"}
        snapshots[name] = str(content)
    return {
        "status": "passed",
        "release_id": info.get("release_id"),
        "source": info.get("source"),
        "prompt_count": info.get("prompt_count"),
        "stage_count": len(snapshots),
    }


def _check_account_model_profile() -> dict[str, Any]:
    profile = resolve_model_profile(ACCOUNT_ROUTE_SCENARIO)
    if not profile.has_invocation_credentials():
        return {"status": "failed", "reason": "model_unavailable", "scenario": profile.scenario}
    if str(profile.model or "").strip() != "gpt-5.6-luna":
        return {
            "status": "failed",
            "reason": "unexpected_model",
            "scenario": profile.scenario,
            "model": profile.model,
        }
    if str(profile.reasoning_effort or "").strip().lower() != "xhigh":
        return {
            "status": "failed",
            "reason": "unexpected_reasoning_effort",
            "scenario": profile.scenario,
            "model": profile.model,
            "reasoning_effort": profile.reasoning_effort,
        }
    return {
        "status": "passed",
        "scenario": profile.scenario,
        "model": profile.model,
        "reasoning_effort": profile.reasoning_effort,
        "temperature": profile.temperature,
        "timeout_seconds": profile.timeout_seconds,
    }


def _check_llm_canary() -> dict[str, Any]:
    profile = account_profile(resolve_model_profile(ACCOUNT_ROUTE_SCENARIO))
    if not account_profile_has_primary_credentials(profile):
        return {"status": "failed", "reason": "model_unavailable"}
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt="Account rerun preflight. Return JSON only.",
            user_prompt='Return exactly {"ok":true}.',
            extra_payload={"text": {"format": {"type": "json_object"}}},
        )
        payload = json.loads(str(response.text or ""))
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return {"status": "failed", "reason": "invalid_json_response"}
    except Exception as exc:
        return {"status": "failed", "reason": "llm_canary_failed", "error": _safe_preflight_error(exc)}
    return {"status": "passed", "scenario": profile.scenario, "model": profile.model, "reasoning_effort": profile.reasoning_effort, "temperature": profile.temperature}


def run_account_rerun_preflight(
    *,
    canary: Callable[[], dict[str, Any]] | None = None,
) -> AccountRerunPreflightResult:
    checks: dict[str, dict[str, Any]] = {}
    for name, check in (
        ("postgresql", _check_sql_contract),
        ("prompt_runtime", _check_prompt_runtime),
        ("account_model", _check_account_model_profile),
        ("llm_canary", canary or _check_llm_canary),
    ):
        try:
            checks[name] = check()
        except Exception as exc:
            checks[name] = {"status": "failed", "reason": _safe_preflight_error(exc, limit=200)}
    failed = next((name for name, value in checks.items() if value.get("status") != "passed"), None)
    return AccountRerunPreflightResult(
        ok=failed is None,
        checks=checks,
        reason="" if failed is None else f"preflight_{failed}_failed",
    )
