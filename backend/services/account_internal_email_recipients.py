from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
import re
from typing import Any

from backend.services.graph_mail import automation_internal_email_cc


ECS_ACCOUNT_ONLY_ENV = "AUTOMATION_ECS_ACCOUNT_ONLY"
ENABLEMENT_RECIPIENTS_JSON_ENV = "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON"
FRAUD_RECIPIENTS_JSON_ENV = "FRAUD_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON"
ACCOUNT_SUSPENSION_RECIPIENTS_JSON_ENV = (
    "ACCOUNT_SUSPENSION_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON"
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountInternalEmailRecipientError(ValueError):
    def __init__(self, code: str, config_key: str, detail: str) -> None:
        super().__init__(f"{config_key}: {detail}")
        self.code = code
        self.config_key = config_key


@dataclass(frozen=True)
class _RecipientConfig:
    json_env: str
    legacy_to_envs: tuple[str, ...]
    legacy_default_to: str = ""


@dataclass(frozen=True)
class AccountInternalEmailRecipients:
    to: tuple[str, ...]
    cc: tuple[str, ...]
    config_key: str
    source: str

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        resolved = deepcopy(payload) if isinstance(payload, dict) else {}
        resolved.update(
            {
                "to": self.to[0],
                "to_addresses": list(self.to),
                "cc_addresses": list(self.cc),
                "recipient_config_key": self.config_key,
                "recipient_resolution_source": self.source,
                "resolved_to": self.to[0],
            }
        )
        return resolved


_RECIPIENT_CONFIGS = {
    "enablement": _RecipientConfig(
        json_env=ENABLEMENT_RECIPIENTS_JSON_ENV,
        legacy_to_envs=("ENABLEMENT_AUTOMATION_INTERNAL_EMAIL",),
    ),
    "fraud_account": _RecipientConfig(
        json_env=FRAUD_RECIPIENTS_JSON_ENV,
        legacy_to_envs=("BILLING_AUTOMATION_ACCOUNT_VERIFICATION_EMAIL",),
        legacy_default_to="xieziling@agora.io",
    ),
    "account_verification": _RecipientConfig(
        json_env=FRAUD_RECIPIENTS_JSON_ENV,
        legacy_to_envs=("BILLING_AUTOMATION_ACCOUNT_VERIFICATION_EMAIL",),
        legacy_default_to="xieziling@agora.io",
    ),
    "account_suspension": _RecipientConfig(
        json_env=ACCOUNT_SUSPENSION_RECIPIENTS_JSON_ENV,
        legacy_to_envs=(
            "BILLING_AUTOMATION_ACCOUNT_SUSPENSION_EMAIL",
            "BILLING_AUTOMATION_INTERNAL_EMAIL",
        ),
        legacy_default_to="xieziling@agora.io",
    ),
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _addresses(value: Any, *, field: str, config_key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise AccountInternalEmailRecipientError(
            "account_internal_email_recipient_invalid",
            config_key,
            f"{field} must be a non-empty array",
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        address = _clean(raw)
        if not _EMAIL_RE.fullmatch(address):
            raise AccountInternalEmailRecipientError(
                "account_internal_email_recipient_invalid",
                config_key,
                f"{field}[{index}] is not a valid email address",
            )
        identity = address.casefold()
        if identity not in seen:
            seen.add(identity)
            normalized.append(address)
    return tuple(normalized)


def _config(handler: str) -> _RecipientConfig:
    normalized = _clean(handler).lower()
    config = _RECIPIENT_CONFIGS.get(normalized)
    if config is None:
        raise AccountInternalEmailRecipientError(
            "account_internal_email_recipient_unregistered",
            normalized or "unknown",
            "handler is not registered",
        )
    return config


def resolve_account_internal_email_recipients(
    handler: str,
    *,
    require_json: bool | None = None,
) -> AccountInternalEmailRecipients:
    config = _config(handler)
    raw_json = str(os.getenv(config.json_env) or "").strip()
    json_required = (
        str(os.getenv(ECS_ACCOUNT_ONLY_ENV) or "").strip() == "1"
        if require_json is None
        else require_json
    )
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise AccountInternalEmailRecipientError(
                "account_internal_email_recipient_invalid",
                config.json_env,
                "value is not valid JSON",
            ) from exc
        if not isinstance(parsed, dict) or set(parsed) != {"to", "cc"}:
            raise AccountInternalEmailRecipientError(
                "account_internal_email_recipient_invalid",
                config.json_env,
                "value must contain exactly to and cc",
            )
        return AccountInternalEmailRecipients(
            to=_addresses(parsed.get("to"), field="to", config_key=config.json_env),
            cc=_addresses(parsed.get("cc"), field="cc", config_key=config.json_env),
            config_key=config.json_env,
            source="environment_json",
        )
    if json_required:
        raise AccountInternalEmailRecipientError(
            "account_internal_email_recipient_missing",
            config.json_env,
            "configuration is required",
        )

    to_address = next(
        (_clean(os.getenv(env_name)) for env_name in config.legacy_to_envs if _clean(os.getenv(env_name))),
        config.legacy_default_to,
    )
    if not to_address:
        raise AccountInternalEmailRecipientError(
            "account_internal_email_recipient_missing",
            config.legacy_to_envs[0],
            "configuration is required",
        )
    return AccountInternalEmailRecipients(
        to=_addresses([to_address], field="to", config_key=config.legacy_to_envs[0]),
        cc=_addresses(
            automation_internal_email_cc(),
            field="cc",
            config_key="AUTOMATION_INTERNAL_EMAIL_CC",
        ),
        config_key=config.legacy_to_envs[0],
        source="legacy_environment",
    )


def attach_account_internal_email_recipients(
    payload: dict[str, Any],
    *,
    handler: str,
) -> dict[str, Any]:
    config = _config(handler)
    ecs_mode = str(os.getenv(ECS_ACCOUNT_ONLY_ENV) or "").strip() == "1"
    if not ecs_mode and not str(os.getenv(config.json_env) or "").strip():
        return deepcopy(payload) if isinstance(payload, dict) else {}
    return resolve_account_internal_email_recipients(handler).apply(payload)


def validate_ecs_account_internal_email_recipients() -> None:
    for handler in ("enablement", "fraud_account", "account_suspension"):
        resolve_account_internal_email_recipients(handler, require_json=True)
