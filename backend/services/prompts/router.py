from __future__ import annotations

import json
from typing import Any


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def build_router_system_prompt(*, route_examples: list[dict[str, Any]]) -> str:
    examples = []
    for index, example in enumerate(route_examples, start=1):
        examples.append(
            "\n".join(
                [
                    f"Example {index}",
                    "Input message:",
                    str(example.get("message") or "").strip(),
                    "Hints:",
                    _dump_json(example.get("hints") or {}),
                    "Expected JSON output:",
                    _dump_json(example.get("output") or {}),
                ]
            )
        )

    return "\n".join(
        [
            "## Role",
            "You are Agora's route classifier.",
            "You only classify the request. You do not answer it.",
            "Do not add product facts, support steps, or outside knowledge.",
            "",
            "## Task",
            "Classify the latest support message into exactly one scope_label.",
            "",
            "## Scope Labels",
            "- ticket_resolution: customer confirms the issue is solved or the guidance worked after a substantive client-visible support reply",
            "- small_talk: greeting, weather, chit-chat, casual conversation, or generic thanks without ticket-resolution context",
            "- non_agora: unrelated request or general IT/support question that should not use Agora docs",
            "- agora_non_technical: Agora-related company, pricing, policy, investor, product overview, product portfolio, or other public-business information",
            "- agora_technical: Agora product usage, SDK/API integration, troubleshooting, configuration, feature fit, profile choice, permissions, recording strategy, notifications/signaling design, or docs-grounded benchmark/auth analysis",
            "",
            "## Decision Rules",
            "Treat supplied hints as weak evidence, not hard labels.",
            "Use matched hints and ticket context when they help disambiguate.",
            "If the latest message confirms success or thanks you after a substantive support reply, and there are no remaining-problem signals, choose ticket_resolution.",
            "If the latest message is generic gratitude without a substantive support-reply context, do not choose ticket_resolution.",
            "If the message looks like RTC, audio/video, joining, rendering, or connectivity troubleshooting and there is no explicit non-Agora signal, prefer agora_technical.",
            "If the message concerns product-mode comparisons, recording choices, auth diagnostics, or benchmark questions anchored in Agora docs topics, choose agora_technical.",
            "If the message asks which Agora products exist, wants a product overview, or needs high-level product-fit guidance without implementation details, choose agora_non_technical.",
            "If the message is only about company/public information, choose agora_non_technical.",
            "If the message is clearly unrelated or general IT help such as printers, Outlook, Excel, office wifi, or a computer blue screen, choose non_agora.",
            "",
            "## Fallback Policy",
            "If uncertain and the message resembles troubleshooting, prefer agora_technical instead of non_agora.",
            "",
            "## Output Requirements",
            "Return JSON only with keys: scope_label, confidence, reason, matched_signals.",
            "confidence must be between 0 and 1.",
            "matched_signals must be a short list of helpful hint strings.",
            "",
            "## Few-shot Examples",
            "\n\n".join(examples),
        ]
    ).strip()


def build_router_user_prompt(*, payload: dict[str, Any]) -> str:
    message = str(payload.get("message") or "").strip() or "(empty)"
    ticket_subject = str(payload.get("ticket_subject") or "").strip() or "(none)"
    response_language = str(payload.get("response_language") or "").strip() or "en"
    selected_product = str(payload.get("selected_product") or "").strip() or "(generic Agora support)"
    ticket_context = list(payload.get("ticket_context") or [])
    hints = dict(payload.get("hints") or {})
    return "\n".join(
        [
            "## Inputs",
            "",
            "## Latest Message",
            message,
            "",
            "## Ticket Subject",
            ticket_subject,
            "",
            "## Selected Product",
            selected_product,
            "",
            "## Recent Ticket Context",
            _dump_json(ticket_context),
            "",
            "## Routing Hints",
            _dump_json(hints),
            "",
            "## Response Language",
            response_language,
        ]
    ).strip()
