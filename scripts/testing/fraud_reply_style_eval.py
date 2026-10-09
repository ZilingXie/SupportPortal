"""Offline real-model evaluation of the v4 fraud reply style contract.

Assembles the REAL persona phase instructions (v4 manual + v4 contract +
default persona style), drives the deployment-pinned model
(AGENT_MODEL_ID, medium reasoning effort — mirroring the Preproduction
pin), and judges each raw output against C1-C8. Evidence is desensitized:
synthetic names/addresses only, no real customer data, no secrets.

Usage (from the repository root, with OPENAI_API_KEY available):
    python -m scripts.testing.fraud_reply_style_eval \
        [--output /tmp/fraud_reply_style_results.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import backend.tests.test_hermes_zendesk_agent as harness  # noqa: F401  (env bootstrap)
from scripts.testing.fraud_reply_style import judge_fraud_reply_style


ALL_FIELDS = [
    "account_type",
    "name",
    "office_address",
    "contact_number",
    "contact_email",
    "use_case_description",
    "console_configuration",
]

SAMPLES = [
    {
        "sample_id": "zero_fields",
        "customer_message": (
            "Hello Agora team, our Agora account was flagged for suspicious "
            "activity and has been blocked. Please initiate the fraud account "
            "review process. Thanks."
        ),
        "missing_fields": list(ALL_FIELDS),
        "collected_fields": {},
        "greeting_name": "Case",
        "expected_restate_terms": [],
    },
    {
        "sample_id": "missing_two",
        "customer_message": (
            "Hello, our account was suspended and we need the fraud review. "
            "We are a company account; my name is Jordan Lee, phone "
            "+1-555-0100, email jordan.lee@example.com, and our use case is "
            "live-streaming classroom sessions on the web with the Agora "
            "RTC SDK; console is on the default project settings."
        ),
        "missing_fields": ["office_address", "console_configuration"],
        "collected_fields": {
            "account_type": "company",
            "name": "Jordan Lee",
            "contact_number": "+1-555-0100",
            "contact_email": "jordan.lee@example.com",
            "use_case_description": "live-streaming classroom sessions",
        },
        "greeting_name": "Jordan",
        "expected_restate_terms": ["company"],
    },
    {
        "sample_id": "missing_five_with_use_case",
        "customer_message": (
            "Hi, my Agora account got suspended. I am an individual "
            "developer testing the RTC SDK locally on my own computer for a "
            "personal project, no public deployment. Console configuration "
            "is the default one. Please review the account."
        ),
        "missing_fields": [
            "account_type", "name", "office_address",
            "contact_number", "contact_email",
        ],
        "collected_fields": {
            "use_case_description": "testing the RTC SDK locally, personal project",
            "console_configuration": "default",
        },
        "greeting_name": "Customer",
        "expected_restate_terms": ["RTC SDK"],
    },
    {
        "sample_id": "complete_fields",
        "customer_message": (
            "Hello, fraud review request: company account, contact Alex "
            "Chen, +1-555-0199, alex.chen@example.com, office at 100 Test "
            "Street, use case is in-app voice calls for our mobile app, "
            "console configuration: projects with REST token auth."
        ),
        "missing_fields": [],
        "collected_fields": {
            "account_type": "company",
            "name": "Alex Chen",
            "office_address": "100 Test Street",
            "contact_number": "+1-555-0199",
            "contact_email": "alex.chen@example.com",
            "use_case_description": "in-app voice calls",
            "console_configuration": "REST token auth",
        },
        "greeting_name": "Alex",
        "expected_restate_terms": [],
        "tool_result_executed": True,
    },
    {
        "sample_id": "chinese_customer",
        "customer_message": (
            "你好，我的声网账号因可疑活动被冻结了，请帮我发起欺诈审核。"
            "谢谢。"
        ),
        "missing_fields": list(ALL_FIELDS),
        "collected_fields": {},
        "greeting_name": "Customer",
        "expected_restate_terms": [],
    },
]


def _system_prompt() -> str:
    from backend.services.automation_hermes_agent import phase_instructions
    from backend.services.account_admin import DEFAULT_PERSONA_CONTENT

    instructions, key = phase_instructions(
        "persona",
        direction="automation",
        route="fraud_account",
        persona_style=DEFAULT_PERSONA_CONTENT,
        persona_key="default-support",
    )
    return instructions, key


def _user_prompt(sample: dict) -> str:
    snapshot = {
        "zendesk_ticket_id": "00000",
        "direction": "automation",
        "route": "fraud_account",
        "active_customer": {"name": sample["greeting_name"]},
        "greeting_name": sample["greeting_name"],
        "latest_customer_message": sample["customer_message"],
    }
    from backend.services.account_fraud_reply_basis import build_fraud_reply_basis

    basis = build_fraud_reply_basis(
        missing_fields=sample["missing_fields"],
        collected_fields=sample["collected_fields"],
    )
    if sample.get("tool_result_executed"):
        work_result = {
            "status": "executed",
            "route": "fraud_account",
            "missing_fields": [],
            "collected_fields": sample["collected_fields"],
            "internal_email_send_status": "sent",
            "reply_basis": basis,
        }
    else:
        work_result = {
            "status": "missing_fields",
            "route": "fraud_account",
            "missing_fields": sample["missing_fields"],
            "collected_fields": sample["collected_fields"],
            "reply_basis": basis,
        }
    return (
        "CASE SNAPSHOT (JSON):\n"
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + "\n\nWORK RESULT (server-authoritative, JSON):\n"
        + json.dumps(work_result, ensure_ascii=False, indent=2)
        + "\n\nTASK: Write the final customer reply body for this revision now. "
        "Output ONLY the reply body text (no greeting line; the server adds "
        "\"Hi <Name>,\" separately). Do not call any tools; there are none in "
        "this offline evaluation."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/tmp/fraud_reply_style_results.json")
    args = parser.parse_args()

    from backend.services.llm_profiles import (
        ACCOUNT_EXTRACTOR_SCENARIO,
        resolve_model_profile,
    )
    from backend.services.llm_factory import invoke_responses_text

    instructions, prompt_key = _system_prompt()
    profile = resolve_model_profile(ACCOUNT_EXTRACTOR_SCENARIO)

    results = []
    for sample in SAMPLES:
        user_prompt = _user_prompt(sample)
        result = invoke_responses_text(
            profile=profile,
            system_prompt=instructions,
            user_prompt=user_prompt,
        )
        reply = str(result.text or "").strip()
        # Strip an optional greeting the model may have added anyway.
        first_line, _, rest = reply.partition("\n")
        if first_line.strip().lower().startswith("hi "):
            reply = rest.strip()
        verdict = judge_fraud_reply_style(
            reply,
            missing_fields=list(sample["missing_fields"]),
            collected_fields=list(sample["collected_fields"]),
            expected_restate_terms=sample.get("expected_restate_terms") or None,
            collected_values=list(sample["collected_fields"].values()),
        )
        results.append(
            {
                "sample_id": sample["sample_id"],
                "missing_fields": sample["missing_fields"],
                "collected_fields_keys": sorted(sample["collected_fields"]),
                "model_output": reply,
                "verdict": verdict,
                "usage": {
                    "input_tokens": getattr(result, "input_tokens", None),
                    "output_tokens": getattr(result, "output_tokens", None),
                },
            }
        )
        status = "PASS" if verdict["passed"] else f"FAIL {verdict['failed']}"
        print(f"[{sample['sample_id']}] {status}")

    evidence = {
        "schema_version": "fraud-reply-style-offline-eval-v1",
        "model": {
            "provider": profile.provider,
            "model": profile.model,
            "reasoning_effort": profile.reasoning_effort,
            "pinned_by_agent_model_env": os.environ.get("AGENT_MODEL_ID") or "",
        },
        "prompt": {
            "manual_key": prompt_key,
            "persona_manual_version": "hermes-persona-manual-v4",
            "reply_contract_version": "hermes-reply-contract-v4",
            "system_prompt": instructions,
            "system_prompt_sha256": __import__("hashlib").sha256(
                instructions.encode()
            ).hexdigest(),
        },
        "samples": results,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r["verdict"]["passed"]),
            "failed_samples": [
                r["sample_id"] for r in results if not r["verdict"]["passed"]
            ],
        },
    }
    Path(args.output).write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"evidence written to {args.output}")
    return 0 if evidence["summary"]["passed"] == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
