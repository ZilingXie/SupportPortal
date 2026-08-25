from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.account_ai_execution import (
    AccountProcessingFailure,
    account_profile_has_primary_credentials,
    invoke_account_json_payload,
)
from backend.services.llm_profiles import ACCOUNT_EXTRACTOR_SCENARIO, resolve_model_profile
from backend.services.prompt_runtime import resolve_system_prompt
from backend.services.prompts.account_routing import (
    ACCOUNT_DETAILED_INVOICE_FIELD_PROMPT_VERSION,
    build_account_detailed_invoice_field_system_prompt,
)


ACCOUNT_DETAILED_INVOICE_FIELD_PROMPT_KEY = "account-detailed-invoice-field-extractor-system"
DETAILED_INVOICE_FIELDS = ("issue_date", "transaction_id", "amount")


@dataclass(frozen=True)
class DetailedInvoiceFieldExtraction:
    status: str
    collected_fields: dict[str, str]
    missing_fields: list[str] = field(default_factory=list)
    ambiguous_fields: list[str] = field(default_factory=list)
    reason: str = ""
    prompt_snapshot: dict[str, str] = field(default_factory=dict)

    @property
    def requires_human_review(self) -> bool:
        return self.status in {"ambiguous", "uncertain"}

    def audit_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "collected_fields": dict(self.collected_fields),
            "missing_fields": list(self.missing_fields),
            "ambiguous_fields": list(self.ambiguous_fields),
            "reason": self.reason,
            "prompt_version": ACCOUNT_DETAILED_INVOICE_FIELD_PROMPT_VERSION,
        }


def extract_detailed_invoice_fields(
    *,
    message: str,
    existing_fields: dict[str, Any] | None = None,
    invoke: Callable[..., Any] | None = None,
    scenario: str = ACCOUNT_EXTRACTOR_SCENARIO,
) -> DetailedInvoiceFieldExtraction:
    system_prompt = resolve_system_prompt(
        ACCOUNT_DETAILED_INVOICE_FIELD_PROMPT_KEY,
        build_account_detailed_invoice_field_system_prompt(),
    )
    snapshot = {
        "system_prompt": system_prompt,
        "user_prompt": "[redacted detailed invoice extraction input]",
    }
    profile = resolve_model_profile(scenario)
    if not account_profile_has_primary_credentials(profile) and invoke is None:
        raise AccountProcessingFailure(
            "account_ai_missing_credentials",
            "the detailed invoice extractor has no primary OpenAI API key",
            stage="detailed_invoice_field_extractor",
        )
    user_prompt = json.dumps(
        {
            "existing_fields": dict(existing_fields or {}),
            "customer_message": str(message or "").strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    try:
        if invoke is not None:
            response = invoke(system_prompt=system_prompt, user_prompt=user_prompt)
            payload = json.loads(str(getattr(response, "text", response) or ""))
        else:
            payload = invoke_account_json_payload(
                profile=profile,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                stage="detailed_invoice_field_extractor",
            )
    except AccountProcessingFailure:
        raise
    except (LlmInvocationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return DetailedInvoiceFieldExtraction(
            status="uncertain",
            collected_fields=dict(existing_fields or {}),
            reason=f"detailed invoice field extraction failed: {exc.__class__.__name__}",
            prompt_snapshot=snapshot,
        )
    if not isinstance(payload, dict):
        return DetailedInvoiceFieldExtraction(
            status="uncertain",
            collected_fields=dict(existing_fields or {}),
            reason="detailed invoice extractor returned a non-object payload",
            prompt_snapshot=snapshot,
        )
    collected = dict(existing_fields or {})
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    for field_name in DETAILED_INVOICE_FIELDS:
        item = fields.get(field_name)
        value = item.get("value") if isinstance(item, dict) else item
        if value not in (None, ""):
            collected[field_name] = str(value).strip()
    missing = [field_name for field_name in DETAILED_INVOICE_FIELDS if not collected.get(field_name)]
    status = str(payload.get("status") or ("missing" if missing else "complete")).strip().lower()
    if status not in {"complete", "missing", "ambiguous", "uncertain"}:
        status = "uncertain"
    ambiguous = [
        str(item).strip()
        for item in (payload.get("ambiguous_fields") or [])
        if str(item).strip()
    ] if isinstance(payload.get("ambiguous_fields"), list) else []
    return DetailedInvoiceFieldExtraction(
        status=status,
        collected_fields=collected,
        missing_fields=missing,
        ambiguous_fields=ambiguous,
        reason=str(payload.get("reason") or "").strip(),
        prompt_snapshot=snapshot,
    )
