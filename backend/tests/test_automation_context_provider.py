"""Opt-in fixed synthetic Provider evaluation; no business services are called."""

import json
import os
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services.automation_context import build_automation_context, persona_context
from backend.services.automation_persona import build_account_automation_reply_facts, render_automation_reply
from backend.services.account_route_pipeline import decide_account_route
from backend.services.enablement_field_extractor import extract_enablement_fields
from backend.services.account_verification_field_extractor import extract_account_verification_fields
from backend.services.account_suspension_field_extractor import extract_account_suspension_fields
from backend.services.enablement_completion_classifier import classify_enablement_completion
from backend.services.prompt_runtime import _code_snapshot, use_prompt_runtime_snapshot
from backend.services.ragflow_docs_search_skill import RagflowDocsSearchSkillClient


pytestmark = pytest.mark.skipif(os.getenv("AUTOMATION_CONTEXT_PROVIDER_EVAL") != "1",
                                reason="explicit live synthetic Provider evaluation only")


SAMPLES = (
    [("enablement", outcome, phrase) for outcome in ("awaiting_app_id", "appid_invalid", "project_not_found")
     for phrase in ("can you try: ", "use this App ID: ", "here it is: ", "try this instead: ")]
    + [("fraud", "office_address", phrase) for phrase in (
        "Our office is at 10 Sample Road, Test City.", "The address is 10 Sample Road, Test City.",
        "It is 10 Sample Road, Test City.", "Here you go: 10 Sample Road, Test City.",
        "Please use 10 Sample Road, Test City.", "10 Sample Road, Test City",
        "You asked for our address: 10 Sample Road, Test City.", "For the office address, use 10 Sample Road, Test City.")]
    + [("suspension", "account_suspension", text) for text in (
        "My account is suspended. Please review it.", "I see an account suspended error when logging in.",
        "Please help restore access to my suspended account.", "My account was suspended and I do not know why.")]
    + [("rag", "rag", text) for text in (
        "What is an App ID?", "Where can I find my App ID?", "Is App ID the same as App Certificate?",
        "Which Console page shows the App ID?")]
    + [("completion", expected, text) for expected, text in (
        (True, "Media Relay is enabled now. Configuration readback confirms it."),
        (False, "We will enable Media Relay tomorrow."),
        (False, "I cannot confirm whether Media Relay is enabled."),
        (True, "We enabled Media Relay and verified the project configuration successfully."))]
)
assert len(SAMPLES) == 32


@pytest.fixture(scope="module", autouse=True)
def provider_environment():
    if os.getenv("AUTOMATION_CONTEXT_PROVIDER_EVAL") != "1":
        yield
        return
    from dotenv import load_dotenv
    root = Path(__file__).resolve().parents[4]
    load_dotenv(root / ".env", override=False)
    with use_prompt_runtime_snapshot(_code_snapshot()):
        yield


@pytest.mark.parametrize("index,sample", list(enumerate(SAMPLES, 1)),
                         ids=[f"sample-{i:02}-{s[0]}" for i, s in enumerate(SAMPLES, 1)])
def test_fixed_provider_sample(index, sample, record_property):
    kind, state, current = sample
    models = []
    real_urlopen = urllib.request.urlopen

    def observed_request(request, *args, **kwargs):
        if getattr(request, "data", None):
            body = json.loads(request.data)
            if "model" in body:
                models.append(body["model"])
                if body["model"] == "gpt-6-astra":
                    assert body.get("reasoning", {}).get("effort") == "low"
                    assert "temperature" not in body
        return real_urlopen(request, *args, **kwargs)

    started = time.monotonic()
    try:
        with patch("urllib.request.urlopen", side_effect=observed_request):
            if kind == "completion":
                result = classify_enablement_completion(current, feature_label="Media Relay")
                assert result.source == "llm"
                assert result.completed is state
                return
            trusted = {}
            if kind == "enablement":
                subject = "Enable Media Relay"
                initial = "Please enable Media Relay for my project."
                question = "Please provide the correct App ID."
                current += "b" * 32
                trusted = {"requested_feature": "media_relay", "requested_feature_label": "Media Relay"}
            elif kind == "fraud":
                subject = "Fraud account review"
                initial = "Please review my account flagged for fraud."
                question = "Please provide your office address for the account review."
                trusted = {"account_type": "company", "name": "Synthetic Company"}
            elif kind == "suspension":
                subject, initial, question = "Account suspension", "", ""
            else:
                subject = "Enable Media Relay"
                initial, question = "Please enable Media Relay.", "Please provide the App ID."
            messages = [
                {"message_id": "initial", "role": "customer", "content": initial},
                {"message_id": "asked", "role": "assistant", "content": question},
                {"message_id": "current", "role": "customer", "content": current},
            ]
            context = build_automation_context({"ticket_id": "synthetic", "messages": messages},
                {"automation_handler": kind, "collected_fields": trusted,
                 "automation_context": {"enablement_archer": {"outcome": state}}},
                current_message=messages[-1], initial=kind == "suspension")
            route = decide_account_route(current, ticket_subject=subject,
                ticket_context=context["conversation"], require_latest=True)
            expected_route = {"enablement": "enablement", "fraud": "fraud_account",
                              "suspension": "account_suspension", "rag": "rag"}[kind]
            assert (route.decision.execution_action or route.decision.route) == expected_route
            if kind == "rag":
                skill = RagflowDocsSearchSkillClient()
                docs = [{"chunk_id": "synthetic-doc", "document": "Console project settings",
                         "source_url": "https://docs.agora.io/en/video-calling/get-started/manage-agora-account",
                         "similarity": 0.99,
                         "content": "An App ID identifies an Agora project. Find it in Agora Console under Project Management, "
                         "then select your project. App ID and App Certificate are different values; the certificate is a secret."}]
                with patch.object(skill, "_search", return_value=docs):
                    answer = skill.query(question=current, request_id="synthetic",
                                         ticket_context=context["conversation"])
                assert answer["decision"] == "answer"
                assert "app" in answer["answer"].lower()
                return
            extractor = {"enablement": extract_enablement_fields, "fraud": extract_account_verification_fields,
                         "suspension": extract_account_suspension_fields}[kind]
            extracted = extractor(ticket_subject=subject, customer_messages=messages,
                                  existing_fields=trusted, automation_context=context)
            assert not extracted.requires_human_review
            if kind == "enablement":
                assert extracted.collected_fields["app_id"] == "b" * 32
                assert extracted.collected_fields["requested_feature"] == "media_relay"
                from backend.services.automation_account_intake import _archer_reply_facts
                _, facts = _archer_reply_facts(outcome="enabled", collected_fields=extracted.collected_fields,
                                              customer_name="Taylor")
            elif kind == "fraud":
                assert "10 Sample Road" in extracted.collected_fields["office_address"]
                facts = build_account_automation_reply_facts(handler="fraud_account", action="fraud_account",
                    missing_fields=extracted.missing_fields, collected_fields=extracted.collected_fields,
                    customer_name="Taylor")
            else:
                assert extracted.status not in {"uncertain", "empty"}
                from backend.services.account_suspension_automation import closing_reply_facts
                facts = closing_reply_facts(confirmed_email="synthetic@example.com", customer_name="Taylor")
            facts["conversation_context"] = persona_context(context)
            rendered = render_automation_reply(reply_facts=facts,
                persona_assignment={"content": {"instruction": "Be warm, concise and precise."}}, account_scope=True)
            assert rendered.content.startswith("Hi Taylor,")
            assert rendered.prompt_version == "automation-persona-v31"
    finally:
        record_property("sample", index)
        record_property("models", json.dumps(models))
        record_property("calls", len(models))
        record_property("latency_seconds", round(time.monotonic() - started, 3))
