from __future__ import annotations

import json
import ssl
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.repositories.ticket_repository import account_case_upsert_contract
from backend.services.account_route_pipeline import (
    ACCOUNT_AGORA_PROMPT_KEY,
    ACCOUNT_AUTOMATION_PROMPT_KEY,
    ACCOUNT_BACKEND_OPERATION_PROMPT_KEY,
    ACCOUNT_BILLING_PROMPT_KEY,
    ACCOUNT_INTENT_PROMPT_KEY,
)
from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import ACCOUNT_ROUTE_SCENARIO, resolve_model_profile
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


def _check_luna_json_request(
    invoke: Callable[..., Any] = invoke_responses_text,
) -> dict[str, Any]:
    profile = resolve_model_profile(ACCOUNT_ROUTE_SCENARIO)
    if not profile.has_invocation_credentials():
        return {"status": "failed", "reason": "model_unavailable", "scenario": profile.scenario}
    try:
        response = invoke(
            profile=profile,
            system_prompt="Return exactly one JSON object with the key ok set to true.",
            user_prompt='{"probe":"account_route_preflight"}',
            extra_payload={"text": {"format": {"type": "json_object"}}},
        )
        payload = json.loads(str(getattr(response, "text", response) or ""))
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return {"status": "failed", "reason": "invalid_json", "scenario": profile.scenario}
    except json.JSONDecodeError as exc:
        return {"status": "failed", "reason": "invalid_json", "error": str(exc)[:200], "scenario": profile.scenario}
    except LlmInvocationError as exc:
        lowered = str(exc).lower()
        if any(marker in lowered for marker in ("ssl", "tls", "certificate", "cert_verify_failed")):
            reason = "tls_failed"
        elif "model_unavailable" in lowered or "no_available_model" in lowered:
            reason = "model_unavailable"
        else:
            reason = "request_failed"
        return {"status": "failed", "reason": reason, "error": str(exc)[:200], "scenario": profile.scenario}
    except ssl.SSLError as exc:
        return {"status": "failed", "reason": "tls_failed", "error": str(exc)[:200], "scenario": profile.scenario}
    except urllib.error.URLError as exc:
        return {"status": "failed", "reason": "request_failed", "error": str(exc)[:200], "scenario": profile.scenario}
    except (ValueError, TypeError) as exc:
        return {"status": "failed", "reason": "request_failed", "error": str(exc)[:200], "scenario": profile.scenario}
    return {
        "status": "passed",
        "scenario": profile.scenario,
        "model": profile.model,
        "reasoning_effort": profile.reasoning_effort,
        "temperature": profile.temperature,
        "timeout_seconds": profile.timeout_seconds,
    }


def run_account_rerun_preflight(
    *,
    invoke: Callable[..., Any] = invoke_responses_text,
) -> AccountRerunPreflightResult:
    checks: dict[str, dict[str, Any]] = {}
    for name, check in (("postgresql", _check_sql_contract), ("prompt_runtime", _check_prompt_runtime)):
        try:
            checks[name] = check()
        except Exception as exc:
            checks[name] = {"status": "failed", "reason": str(exc)[:200]}
    checks["luna_json"] = _check_luna_json_request(invoke)
    failed = next((name for name, value in checks.items() if value.get("status") != "passed"), None)
    return AccountRerunPreflightResult(
        ok=failed is None,
        checks=checks,
        reason="" if failed is None else f"preflight_{failed}_failed",
    )
