from __future__ import annotations

import json
from typing import Any


PRODUCT_SELECTION_PROMPT_VERSION = "product-selection-v1"


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def build_product_selection_system_prompt() -> str:
    return "\n".join(
        [
            "## Role",
            "You identify which Agora support product a customer issue most likely belongs to.",
            "You only classify the product. You do not answer the customer and you do not ask follow-up questions.",
            "",
            "## Allowed Products",
            '- "audio_video_calling": RTC / Audio-Video Calling / joining, publishing, subscribing, remote media, token, channel, uid, black-screen, no-audio, no-video, role/profile issues.',
            '- "cloud_recording": Cloud Recording / acquire-start-stop-query flows, sid, resource id, recording mode, composite, individual, layout, recording files, recording lifecycle.',
            '- "unknown": use this when the message could fit multiple products or the evidence is too weak.',
            "",
            "## Decision Rules",
            "Prefer explicit product mentions first.",
            "Use technical signals from the latest message and recent ticket context only.",
            "If the message is ambiguous, mixed, or too generic, return unknown instead of guessing.",
            "",
            "## Output Requirements",
            'Return JSON only with keys "product", "confidence", "reason", and "matched_signals".',
            'Allowed "product" values: "audio_video_calling", "cloud_recording", "unknown".',
            '"confidence" must be between 0 and 1.',
            '"matched_signals" must be a short list of the strongest helpful clues.',
        ]
    ).strip()


def build_product_selection_user_prompt(
    *,
    latest_customer_message: str,
    ticket_subject: str | None,
    ticket_context: list[dict[str, Any]] | None,
    current_product: str | None,
    awaiting_confirmation: bool,
    allowed_products: list[dict[str, str]],
) -> str:
    return "\n".join(
        [
            "## Latest Customer Message",
            str(latest_customer_message or "").strip() or "(empty)",
            "",
            "## Ticket Subject",
            str(ticket_subject or "").strip() or "(none)",
            "",
            "## Recent Ticket Context",
            _dump_json(list(ticket_context or [])),
            "",
            "## Current Product",
            str(current_product or "").strip() or "(none)",
            "",
            "## Awaiting Customer Confirmation",
            "true" if awaiting_confirmation else "false",
            "",
            "## Allowed Products",
            _dump_json(list(allowed_products or [])),
        ]
    ).strip()
