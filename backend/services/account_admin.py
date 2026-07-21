from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.services.support_router import SupportRouteDecision
from backend.services.support_router_prompt import build_route_system_prompt
from backend.services.customer_reply_composer import ensure_customer_reply_email_style


ROUTER_PROMPT_VERSION = "account-router-v1"
DEFAULT_PERSONA_KEY = "default-support"
DEFAULT_PERSONA_CONTENT = {
    "instruction": "Use a calm, warm, polished concierge-style support voice. Match the customer's language.",
    "signoff_name": "Sid",
}
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def environment_config_names(env_path: Path) -> list[str]:
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    names: set[str] = set()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, _ = line.partition("=")
        name = name.strip()
        if separator and _ENV_KEY_RE.fullmatch(name):
            names.add(name)
    return sorted(names)


def _is_automated(ticket: dict[str, Any]) -> bool:
    return str(ticket.get("automation_status") or ticket.get("status") or "").strip().lower() in {
        "automation",
        "automated",
    }


def account_automation_payload(
    repository: Any,
    *,
    page: int = 1,
    page_size: int = 50,
    route_status: str | None = None,
    category: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> dict[str, Any]:
    safe_page = max(1, int(page))
    safe_size = min(200, max(1, int(page_size)))
    all_cases = repository.list_billing_tickets(limit=100000, offset=0)
    automated = sum(1 for item in all_cases if _is_automated(item))
    total = len(all_cases)
    filtered = list(all_cases)
    normalized_status = str(route_status or "").strip().lower()
    if normalized_status == "automation":
        filtered = [item for item in filtered if _is_automated(item)]
    elif normalized_status == "not_automated":
        filtered = [item for item in filtered if not _is_automated(item)]
    normalized_category = str(category or "").strip().lower()
    if normalized_category:
        filtered = [item for item in filtered if normalized_category in {str(item.get("route_family") or "").lower(), str(item.get("semantic_intent") or "").lower()}]
    if created_from:
        filtered = [item for item in filtered if str(item.get("created_at") or "") >= str(created_from)]
    if created_to:
        filtered = [item for item in filtered if str(item.get("created_at") or "") <= str(created_to)]
    start = (safe_page - 1) * safe_size
    return {
        "metrics": {
            "total_account_cases": total,
            "automated_cases": automated,
            "not_automated_cases": total - automated,
            "automation_rate": automated / total if total else 0,
        },
        "cases": filtered[start : start + safe_size],
        "page": safe_page,
        "page_size": safe_size,
        "total": len(filtered),
    }


def route_execution_from_decision(
    *,
    ticket_id: str,
    decision: SupportRouteDecision,
    system_prompt: str | None,
    user_prompt: str | None,
    created_at: str | None = None,
) -> dict[str, Any]:
    attempted = bool(decision.intent_router_attempted)
    stages = [
        {"name": "semantic_intent", "status": "attempted" if attempted else "skipped"},
        {"name": "confidence_threshold", "status": "passed" if attempted and not decision.intent_router_fallback_reason else "not_passed"},
        {"name": "policy_gate", "status": str(decision.policy_decision or "not_applicable")},
        {"name": "final_route", "status": str(decision.execution_action or decision.route)},
    ]
    return {
        "execution_id": f"route-{uuid4().hex}",
        "ticket_id": str(ticket_id),
        "final_route": str(decision.execution_action or decision.route),
        "route_source": decision.router_source,
        "semantic_intent": decision.semantic_intent,
        "automation_eligibility": decision.automation_eligibility,
        "policy_decision": decision.policy_decision,
        "confidence": decision.confidence,
        "confidence_threshold": decision.intent_router_confidence_threshold,
        "fallback_reason": decision.intent_router_fallback_reason,
        "failure_type": decision.intent_router_failure_type,
        "failure_source": decision.intent_router_failure_source,
        "matched_policy_rules": list(decision.matched_signals),
        "router_prompt_version": ROUTER_PROMPT_VERSION if attempted else None,
        "system_prompt": system_prompt if attempted else None,
        "user_prompt": user_prompt if attempted else None,
        "prompt_snapshot_available": bool(attempted and system_prompt and user_prompt),
        "stages": stages,
        "created_at": created_at,
    }


def routing_config_payload() -> dict[str, Any]:
    return {
        "router_prompt_version": ROUTER_PROMPT_VERSION,
        "system_prompt": build_route_system_prompt(),
        "stages": ["semantic_intent", "confidence_threshold", "policy_gate", "heuristic_fallback", "final_route"],
    }


def apply_persona_to_customer_reply(reply: str, persona: dict[str, Any]) -> str:
    content = persona.get("content") if isinstance(persona.get("content"), dict) else {}
    signoff_name = str(content.get("signoff_name") or "Sid").strip() or "Sid"
    normalized = str(reply or "").strip()
    if signoff_name == "Sid":
        return normalized
    if re.search(r"\nSid\s*$", normalized):
        return re.sub(r"\nSid\s*$", f"\n{signoff_name}", normalized)
    signoff_pattern = re.compile(r"(\n\n(?:Best [Rr]egards,|此致)\n)[^\n]+\s*$")
    if signoff_pattern.search(normalized):
        return signoff_pattern.sub(lambda match: f"{match.group(1)}{signoff_name}", normalized)
    return ensure_customer_reply_email_style(body=normalized, signoff_name=signoff_name)
