from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING = "audio_video_calling"
SUPPORT_PRODUCT_CLOUD_RECORDING = "cloud_recording"


@dataclass(frozen=True)
class SupportProductProfile:
    value: str
    label: str
    prompt_scope: str
    rag_role: str
    intake_role: str
    intake_required_fields: tuple[str, ...]


_PRODUCTS: tuple[SupportProductProfile, ...] = (
    SupportProductProfile(
        value=SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING,
        label="Audio/Video Calling",
        rag_role="You are Agora tech support handling an Audio/Video Calling issue.",
        intake_role=(
            "You are Agora tech support triaging an Audio/Video Calling troubleshooting case before "
            "opening an engineer ticket."
        ),
        prompt_scope=(
            "Selected support product: Audio/Video Calling. "
            "Prefer guidance that matches RTC audio/video calling flows, joining, publishing, subscribing, "
            "media troubleshooting, and channel participation."
        ),
        intake_required_fields=(
            "channel_name",
            "problematic_uid",
            "issue_timestamp",
            "issue_symptom",
        ),
    ),
    SupportProductProfile(
        value=SUPPORT_PRODUCT_CLOUD_RECORDING,
        label="Cloud Recording",
        rag_role="You are Agora tech support handling a Cloud Recording issue.",
        intake_role=(
            "You are Agora tech support triaging a Cloud Recording troubleshooting case before "
            "opening an engineer ticket."
        ),
        prompt_scope=(
            "Selected support product: Cloud Recording. "
            "Prefer guidance that matches Cloud Recording workflows, recording modes, recording lifecycle, "
            "and Cloud Recording API usage."
        ),
        intake_required_fields=(
            "sid",
            "issue_timestamp",
            "issue_symptom",
        ),
    ),
)

_PRODUCT_BY_VALUE = {profile.value: profile for profile in _PRODUCTS}
_INTAKE_FIELD_LABELS = {
    "channel_name": "channel name",
    "problematic_uid": "problematic uid",
    "issue_timestamp": "issue timestamp",
    "issue_symptom": "issue symptom",
    "sid": "sid",
    "desired_outcome": "desired outcome",
    "blocked_step_or_error": "blocked step or error",
}


def list_support_products() -> list[SupportProductProfile]:
    return list(_PRODUCTS)


def normalize_support_product(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _PRODUCT_BY_VALUE else None


def get_support_product_profile(value: Any) -> SupportProductProfile | None:
    normalized = normalize_support_product(value)
    if normalized is None:
        return None
    return _PRODUCT_BY_VALUE[normalized]


def get_support_product_label(value: Any) -> str | None:
    profile = get_support_product_profile(value)
    return None if profile is None else profile.label


def build_support_product_prompt_scope(value: Any) -> str | None:
    profile = get_support_product_profile(value)
    return None if profile is None else profile.prompt_scope


def build_support_product_rag_role(value: Any) -> str | None:
    profile = get_support_product_profile(value)
    return None if profile is None else profile.rag_role


def build_support_product_intake_role(value: Any) -> str | None:
    profile = get_support_product_profile(value)
    return None if profile is None else profile.intake_role


def get_support_product_required_fields(value: Any) -> tuple[str, ...]:
    profile = get_support_product_profile(value)
    return () if profile is None else profile.intake_required_fields


def get_support_product_field_label(field_name: Any) -> str:
    normalized = str(field_name or "").strip().lower()
    return _INTAKE_FIELD_LABELS.get(normalized, normalized.replace("_", " ").strip())


def list_support_product_field_labels(field_names: list[str] | tuple[str, ...]) -> list[str]:
    labels: list[str] = []
    for field_name in field_names:
        label = get_support_product_field_label(field_name)
        if label and label not in labels:
            labels.append(label)
    return labels
