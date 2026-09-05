from __future__ import annotations


ACCOUNT_PROCESSING_PROFILES = frozenset({"staging", "preproduction", "production"})
LIVE_ACCOUNT_PROCESSING_PROFILES = frozenset({"preproduction", "production"})


def normalize_account_processing_profile(
    value: object,
    *,
    default: str = "staging",
) -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in ACCOUNT_PROCESSING_PROFILES:
        raise ValueError("processing_profile must be staging, preproduction, or production")
    return normalized


def is_live_account_processing_profile(value: object) -> bool:
    return str(value or "").strip().lower() in LIVE_ACCOUNT_PROCESSING_PROFILES
