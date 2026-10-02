"""Real-model evaluation for enablement reply-language continuity (13837).

Fixed sanitized samples run through the production ``render_automation_reply``
(including its deterministic contract validators) against the configured
Persona model. Nothing here touches Zendesk, email, relay, or any database:
external send boundaries do not exist in this call path. Skipped unless
``ENABLEMENT_REPLY_LANGUAGE_EVAL=1``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.services.automation_context import CONTEXT_VERSION
from backend.services.automation_persona import (
    AUTOMATION_PERSONA_PROMPT_VERSION,
    build_automation_reply_facts,
    render_automation_reply,
)
from backend.services.prompt_runtime import _code_snapshot, use_prompt_runtime_snapshot


pytestmark = pytest.mark.skipif(os.getenv("ENABLEMENT_REPLY_LANGUAGE_EVAL") != "1",
                                reason="explicit real-model evaluation only")

PERSONA_INSTRUCTION = "Be warm, concise and precise."

_PT_REQUEST = "Olá, por favor ativem o Media Relay no meu projeto. Obrigado pela ajuda."
_PT_APPID = "Segue o App ID para você ativarem, obrigado."
_ES_REQUEST = "Hola, por favor activen Media Relay en mi proyecto. Gracias."
_ES_APPID = "Mi App ID es el de abajo, gracias."
_EN_REQUEST = "Hello, please enable Media Relay on my project. Thank you."
_ZH_REQUEST = "你好，请帮我开通 Media Relay 功能，谢谢。"


def _conversation(*messages: tuple[str, str]) -> dict:
    conversation = []
    for index, (role, content) in enumerate(messages, 1):
        conversation.append(
            {
                "message_id": f"msg-{index}",
                "role": role,
                "created_at": f"2026-10-01T10:{index - 1:02d}:00+00:00",
                "content": content,
            }
        )
    return {
        "version": CONTEXT_VERSION,
        "current_message_id": conversation[-1]["message_id"],
        "conversation": conversation,
    }


def _completion_facts(context: dict) -> dict:
    facts = build_automation_reply_facts(
        behavior="enablement",
        reply_intent="enablement_completed_and_close",
        known_information={
            "requested_feature": "media_relay",
            "requested_feature_label": "Media Relay",
        },
        source_facts=[
            "The internal team confirmed Media Relay is enabled on the project. "
            "Configuration readback verified the state."
        ],
        resolution_status="completed",
        customer_name="Taylor",
    )
    facts["completion_acknowledgement"] = "patience"
    facts["conversation_context"] = context
    return facts


def _archer_enabled_facts(context: dict) -> dict:
    facts = build_automation_reply_facts(
        behavior="enablement",
        reply_intent="enablement_archer_enabled",
        known_information={
            "requested_feature": "media_relay",
            "requested_feature_label": "Media Relay",
            "archer_outcome": "enabled",
        },
        performed_actions=["Enabled Media Relay through Archer."],
        resolution_status="enabled",
        customer_name="Taylor",
    )
    facts["completion_acknowledgement"] = "patience"
    facts["conversation_context"] = context
    return facts


def _followup_facts(context: dict) -> dict:
    facts = build_automation_reply_facts(
        behavior="enablement",
        reply_intent="resolution_update",
        known_information={
            "requested_feature": "media_relay",
            "requested_feature_label": "Media Relay",
        },
        source_facts=["The App ID provided is not correct. Ask for the correct 32-character App ID."],
        resolution_status=None,
        customer_name="Taylor",
    )
    facts["conversation_context"] = context
    return facts


SAMPLES = (
    # (id, expected language, facts builder, conversation)
    (
        "pt_completion_english_internal",
        "pt",
        _completion_facts,
        _conversation(
            ("customer", _PT_REQUEST),
            ("assistant", "Thanks for reaching out. Could you share your App ID?"),
            ("customer", "Segue o App ID [App ID], obrigado."),
        ),
    ),
    (
        "es_relay_success_english_internal",
        "es",
        _archer_enabled_facts,
        _conversation(
            ("customer", _ES_REQUEST),
            ("assistant", "Thanks for reaching out. Could you share your App ID?"),
            ("customer", "Mi App ID es [App ID], gracias."),
        ),
    ),
    (
        "pt_bare_appid_last_message",
        "pt",
        _completion_facts,
        _conversation(
            ("customer", _PT_REQUEST),
            ("assistant", "Could you share your App ID?"),
            # A bare redacted App ID carries no language signal; the reply must
            # continue in Portuguese.
            ("customer", "[App ID]"),
        ),
    ),
    (
        "explicit_switch_to_english",
        "en",
        _completion_facts,
        _conversation(
            ("customer", _PT_REQUEST),
            ("assistant", "Could you share your App ID?"),
            ("customer", "From now on, please reply in English. My App ID is [App ID]."),
        ),
    ),
    (
        "en_only_conversation",
        "en",
        _completion_facts,
        _conversation(
            ("customer", _EN_REQUEST),
            ("assistant", "Could you share your App ID?"),
            ("customer", "My App ID is [App ID]."),
        ),
    ),
    (
        "zh_conversation",
        "zh",
        _completion_facts,
        _conversation(
            ("customer", _ZH_REQUEST),
            ("assistant", "Could you share your App ID?"),
            ("customer", "我的 App ID 是 [App ID]，谢谢。"),
        ),
    ),
    (
        "es_internal_followup",
        "es",
        _followup_facts,
        _conversation(
            ("customer", _ES_REQUEST),
            ("assistant", "Could you share your App ID?"),
            ("customer", "Mi App ID es [App ID], gracias."),
        ),
    ),
)


def _detect_language(body: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", body):
        return "zh"
    lowered = body.lower()
    if re.search(r"ç|ã|obrigad|você|não|ativad|projeto|obrigado", lowered):
        return "pt"
    if re.search(r"ñ|¿|gracias|proyecto|verifí|activen|correcto|enví", lowered):
        return "es"
    return "en"


@pytest.fixture(scope="module", autouse=True)
def provider_environment():
    if os.getenv("ENABLEMENT_REPLY_LANGUAGE_EVAL") != "1":
        yield
        return
    from dotenv import load_dotenv

    root = Path(__file__).resolve().parents[4]
    load_dotenv(root / ".env", override=False)
    with use_prompt_runtime_snapshot(_code_snapshot()):
        yield


@pytest.mark.parametrize(
    "sample_id,expected_language,facts_builder,context",
    SAMPLES,
    ids=[sample[0] for sample in SAMPLES],
)
def test_enablement_reply_language(sample_id, expected_language, facts_builder, context, record_property):
    rendered = render_automation_reply(
        reply_facts=facts_builder(context),
        persona_assignment={"content": {"instruction": PERSONA_INSTRUCTION}},
        account_scope=True,
    )
    body = rendered.content.split("\n", 1)[-1] if "\n" in rendered.content else rendered.content
    detected = _detect_language(body)
    record = {
        "sample": sample_id,
        "expected_language": expected_language,
        "detected_language": detected,
        "model": rendered.model,
        "prompt_version": rendered.prompt_version,
        "generation_attempts": rendered.generation_attempts,
        "safety_status": rendered.safety_status,
        "safety_issue_codes": list(rendered.safety_issue_codes),
        "content": rendered.content,
    }
    record_property("language_eval", json.dumps(record, ensure_ascii=False))
    print(json.dumps(record, ensure_ascii=False, indent=2))
    assert rendered.prompt_version == AUTOMATION_PERSONA_PROMPT_VERSION
    assert detected == expected_language, record["content"]
