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


_PRODUCTS: tuple[SupportProductProfile, ...] = (
    SupportProductProfile(
        value=SUPPORT_PRODUCT_AUDIO_VIDEO_CALLING,
        label="Audio/Video Calling",
        prompt_scope=(
            "Selected support product: Audio/Video Calling. "
            "Prefer guidance that matches RTC audio/video calling flows, joining, publishing, subscribing, "
            "media troubleshooting, and channel participation."
        ),
    ),
    SupportProductProfile(
        value=SUPPORT_PRODUCT_CLOUD_RECORDING,
        label="Cloud Recording",
        prompt_scope=(
            "Selected support product: Cloud Recording. "
            "Prefer guidance that matches Cloud Recording workflows, recording modes, recording lifecycle, "
            "and Cloud Recording API usage."
        ),
    ),
)

_PRODUCT_BY_VALUE = {profile.value: profile for profile in _PRODUCTS}


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
