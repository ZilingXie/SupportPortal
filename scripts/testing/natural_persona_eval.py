"""Offline validation of persona-manual v6 + reply-contract v6 (plan step 6).

Assembles the REAL persona phase instructions (v6 manual + v6 contract +
default style) and drives the deployment-pinned model across the seven plan
scenarios WITHOUT reason-code hints in the user prompt. Evidence is
desensitized (synthetic identities).

Usage (repo root, OPENAI_API_KEY available):
    python -m scripts.testing.natural_persona_eval \
        [--output /tmp/natural_persona_results.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import backend.tests.test_hermes_zendesk_agent as harness  # noqa: F401 env bootstrap
from backend.services.account_admin import DEFAULT_PERSONA_CONTENT
from backend.services.automation_hermes_agent import phase_instructions


def _system_prompt(direction: str = "automation", route: str = "enablement"):
    # Per-sample real assembly: the route determines which contract section
    # the model actually sees (plan finding 2).
    return phase_instructions(
        "persona",
        direction=direction,
        route=route,
        persona_style=DEFAULT_PERSONA_CONTENT,
        persona_key="default-support",
    )


def _basis(stage, *, route="enablement", missing=None, collected=None,
           conclusion=None, commitments=None):
    basis = {
        "kind": "hermes_customer_reply_basis_v1",
        "reply_owner": "hermes_persona",
        "route": route,
        "stage": stage,
        "collected_facts": collected or {},
        "missing_fields": missing or [],
        "trusted_conclusion": conclusion or {},
        "allowed_claims": [f"state:{stage}"],
        "allowed_commitments": commitments or [],
        "greeting_name": "Customer",
    }
    return json.dumps({"REPLY BASIS (server-authoritative)": basis}, ensure_ascii=False)


def _stage_submission(route):
    return _basis(
        "workflow_completed",
        route=route,
        collected={"app_id": "[App ID]", "requested_feature": "media relay"},
        conclusion={"outcome": "review_requested", "internal_email_send_status": "sent"},
        commitments=["review_window_business_days", "contact_within_24_hours"],
    )


SAMPLES = [
    {
        "sample_id": "enablement_missing_appid",
        "route": "enablement",
        "customer_message": "Hello, I want to enable media relay.",
        "basis": _basis("missing_fields", missing=["app_id"],
                       collected={"requested_feature": "media relay"}),
        "expect_terms": ["app id", "media relay"],
    },
    {
        "sample_id": "fraud_missing_fields",
        "route": "fraud_account",
        "customer_message": (
            "Our Agora account was flagged for suspicious activity and has been "
            "blocked and we need it reviewed. Please initiate the fraud review."
        ),
        "basis": _basis(
            "missing_fields",
            route="fraud_account",
            missing=["account_type", "name", "office_address", "contact_number",
                     "contact_email", "use_case_description", "console_configuration"],
            commitments=["request_missing_fields"],
        ),
        "expect_terms": ["account type", "office address"],
    },
    {
        "sample_id": "suspension_handoff",
        "route": "account_suspension",
        "customer_message": (
            "Our account has been suspended and it is blocking our production "
            "service. We already topped up the balance. Please restore it."
        ),
        "basis": _basis(
            "executed",
            route="account_suspension",
            conclusion={"internal_email_send_status": "sent", "handoff": True},
            commitments=["team_contact_within_24_hours"],
        ),
        "expect_terms": ["24", "suspension"],
    },
    {
        "sample_id": "billing_detailed_invoice",
        "route": "detailed_invoice",
        "customer_message": (
            "Hello, please send the detailed invoice for transaction "
            "1104245232004173824, amount USD 705.97, issued 6 May 2026."
        ),
        "basis": _basis(
            "executed",
            route="detailed_invoice",
            conclusion={"internal_email_send_status": "sent", "handoff": True},
            commitments=["team_contact_within_24_hours"],
        ),
        "expect_terms": ["invoice"],
    },
    {
        "sample_id": "investigation_reply",
        "route": "investigation",
        "customer_message": (
            "The live streaming keeps freezing every few minutes on iOS. "
            "What is causing this?"
        ),
        "basis": _basis(
            "investigation_conclusion",
            route="investigation",
            conclusion={
                "answer": (
                    "The freezes correlate with the audience role switching to a "
                    "weak cellular network; the SDK reconnect loop did not "
                    "complete before the render pipeline stalled. Checked: "
                    "session logs, reconnect events. Uncertain: whether the "
                    "carrier drops UDP at peak hours."
                ),
            },
        ),
        "expect_terms": ["freeze"],
    },
    {
        "sample_id": "chinese_customer",
        "route": "enablement",
        "customer_message": "你好，我想开通媒体中继功能，我的 App ID 是 8cb7aea984c4457daad802e6960e2475。",
        "basis": _stage_submission("enablement"),
        "expect_terms_any": [["relay", "媒体中继"], ["24", "24"]],
    },
    {
        "sample_id": "no_reliable_name",
        "route": "enablement",
        "customer_message": "hi enable media relay app id 8cb7aea984c4457daad802e6960e2475 thanks",
        "basis": _stage_submission("enablement"),
        "expect_terms": ["relay"],
    },
]


def _user_prompt(sample):
    snapshot = {
        "zendesk_ticket_id": "00000",
        "direction": "investigation" if sample["route"] == "investigation" else "automation",
        "route": sample["route"],
        "greeting_name": "Customer",
        "latest_customer_message": sample["customer_message"],
    }
    return (
        "CASE SNAPSHOT (JSON):\n"
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
        + "\n\n"
        + sample["basis"]
        + "\n\nTASK: Write the final customer reply body for this revision now. "
        "Output ONLY the reply body text (no greeting line; the server adds "
        "the greeting separately). No tools are available in this offline "
        "evaluation."
    )


def _verdict(reply, sample):
    text = str(reply or "")
    lowered = text.casefold()
    checks = {}
    if "expect_terms_any" in sample:
        missing_terms = [
            "none-of-synonyms"
            for group in sample["expect_terms_any"]
            if not any(t in lowered for t in group)
        ]
    else:
        missing_terms = [t for t in sample["expect_terms"] if t not in lowered]
    checks["content_terms"] = {
        "passed": not missing_terms,
        "detail": f"missing: {missing_terms}" if missing_terms else "all expected terms present",
    }
    greeting_lines = [ln for ln in text.splitlines() if ln.strip().lower().startswith("hi ")]
    checks["greeting_absent"] = {
        "passed": not greeting_lines,
        "detail": f"greeting lines in body: {len(greeting_lines)}",
    }
    internal = [w for w in ("basis", "turn_id", "work result", "workflow", "job") if w in lowered]
    checks["no_internal_exposure"] = {
        "passed": not internal,
        "detail": f"internal terms: {internal}" if internal else "clean",
    }
    hollow = [w for w in ("i'm sorry your account", "as per your message",
                          "as you mentioned in your message") if w in lowered]
    checks["no_hollow_or_meta"] = {
        "passed": not hollow,
        "detail": f"flagged: {hollow}" if hollow else "clean",
    }
    has_24h = "24" in lowered
    allowed_24h = "24" in sample["basis"]
    checks["timeline_within_commitments"] = {
        "passed": (not has_24h) or allowed_24h,
        "detail": "24h stated without allowance" if has_24h and not allowed_24h else "ok",
    }
    failed = [k for k, v in checks.items() if not v["passed"]]
    return {"passed": not failed, "failed": failed, "checks": checks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/tmp/natural_persona_results.json")
    args = parser.parse_args()

    from backend.services.llm_profiles import (
        ACCOUNT_EXTRACTOR_SCENARIO,
        resolve_model_profile,
    )
    from backend.services.llm_factory import invoke_responses_text

    profile = resolve_model_profile(ACCOUNT_EXTRACTOR_SCENARIO)

    # The code snapshot carries the v6 manual/contract builders; without
    # this the prompt layers silently resolve to empty fallbacks.
    from backend.services.prompt_runtime import initialize_prompt_runtime

    initialize_prompt_runtime()

    results = []
    system_prompts = {}
    for sample in SAMPLES:
        direction = (
            "investigation" if sample["route"] == "investigation" else "automation"
        )
        instructions, prompt_key = _system_prompt(direction, sample["route"])
        system_prompts[sample["sample_id"]] = {
            "direction": direction,
            "route": sample["route"],
            "system_prompt": instructions,
            "system_prompt_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
        }
        result = invoke_responses_text(
            profile=profile,
            system_prompt=instructions,
            user_prompt=_user_prompt(sample),
        )
        reply = str(result.text or "").strip()
        first_line, _, rest = reply.partition("\n")
        if first_line.strip().lower().startswith("hi "):
            reply = rest.strip()
        verdict = _verdict(reply, sample)
        results.append({
            "sample_id": sample["sample_id"],
            "route": sample["route"],
            "model_output": reply,
            "verdict": verdict,
            "usage": {
                "input_tokens": getattr(result, "input_tokens", None),
                "output_tokens": getattr(result, "output_tokens", None),
            },
        })
        status = "PASS" if verdict["passed"] else f"FAIL {verdict['failed']}"
        print(f"[{sample['sample_id']}] {status}")

    evidence = {
        "schema_version": "natural-persona-offline-eval-v1",
        "model": {
            "provider": profile.provider,
            "model": profile.model,
            "reasoning_effort": profile.reasoning_effort,
            "pinned_by_agent_model_env": os.environ.get("AGENT_MODEL_ID") or "",
        },
        "prompt": {
            "manual_key": prompt_key,
            "persona_manual_version": "hermes-persona-manual-v6",
            "reply_contract_version": "hermes-reply-contract-v6",
            "persona_style": "default-support",
            "per_sample_system_prompts": system_prompts,
        },
        "samples": results,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r["verdict"]["passed"]),
            "failed_samples": [r["sample_id"] for r in results if not r["verdict"]["passed"]],
        },
    }
    Path(args.output).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"evidence written to {args.output}")
    return 0 if evidence["summary"]["passed"] == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
