"""Synthetic Persona evaluation using the shared runtime contract and real basis builder.

This proves the code snapshot and model behavior, not deployed Prompt activation.
Requires AGENT_MODEL_ID from a timestamped target task definition. No case reruns
or customer-system writes are performed. Expected observations stay out of inputs.
"""
from __future__ import annotations
import argparse
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.services.account_admin import ACCOUNT_PERSONA_PRESETS
from backend.services.automation_hermes_agent import phase_instructions, STANDARD_PHASE_REASONING_EFFORT
from backend.services.hermes_customer_reply_basis import (
    build_hermes_customer_reply_basis, validate_customer_reply_draft,
    project_greeting, HermesReplyBasisError,
)
from backend.services.prompt_runtime import initialize_prompt_runtime, resolve_system_prompt


def _system_prompt(direction="automation", route="enablement"):
    preset = next(p for p in ACCOUNT_PERSONA_PRESETS if p.persona_key == "sid-precise")
    return phase_instructions("persona", direction=direction, route=route,
                              persona_style=preset.instruction, persona_key=preset.persona_key)


def _basis(stage, *, route="enablement", missing=None, collected=None, conclusion=None, commitments=None):
    return build_hermes_customer_reply_basis(
        source_turn_id="synthetic-turn", direction="investigation" if route == "investigation" else "automation",
        route=route, stage=stage, collected_facts=collected, missing_fields=missing,
        trusted_conclusion=conclusion, allowed_claims=commitments,
        greeting_name="Customer", extra={"allowed_commitments": commitments or []},
    )


def _stage_submission(route):
    return _basis("workflow_completed", route=route,
        collected={"app_id": "8cb7aea984c4457daad802e6960e2475", "requested_feature": "media relay"},
        conclusion={"outcome": "review_requested", "internal_email_send_status": "awaiting_public_reply"},
        commitments=["review_window_business_days", "contact_within_24_hours"])


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


SAMPLES.extend([
    {"sample_id": "billing_usage_explanation", "route": "investigation", "customer_message":
     "Why is there a $14.50 usage charge after I used the free quota?", "basis":
     _basis("investigation_conclusion", route="investigation", collected={"name": "Jordan"},
            conclusion={"answer": "The $14.50 line is usage accumulated after the free quota was exhausted. "
                        "Checked the synthetic usage ledger and quota history. No refund or billing adjustment has been made."})},
    {"sample_id": "verification_handoff", "route": "account_verification", "customer_message":
     "I sent the verification documents yesterday. Is there anything else you need?", "basis":
     _basis("executed", route="account_verification", conclusion={"status": "review_requested", "handoff": True},
            commitments=["team_contact_within_24_hours"])},
])
for sample in SAMPLES:
    sample["basis"]["source_turn_id"] = "synthetic-" + sample["sample_id"]
    if sample["sample_id"] == "billing_usage_explanation":
        sample["basis"]["greeting_name"] = "Jordan"


_SNAPSHOTS = {}


def _snapshot(sample):
    from backend.repositories.ticket_repository import InMemoryTicketRepository
    from backend.services.automation_ecs_runtime import AutomationEcsSettings
    from backend.services.automation_ecs_store import InMemoryAutomationEcsStore, JobKind
    from backend.services.automation_ecs_contracts import AutomationIntakeEvent, INTAKE_CONTRACT_VERSION
    from backend.services.automation_hermes_snapshot import build_case_snapshot
    from backend.services.hermes_customer_reply_basis import basis_for_persona_turn
    if sample["sample_id"] in _SNAPSHOTS:
        return copy.deepcopy(_SNAPSHOTS[sample["sample_id"]])
    settings_env = {
        "AUTOMATION_ENVIRONMENT": "preproduction", "AUTOMATION_DB_SCHEMA": "synthetic_preproduction",
        "AUTOMATION_DB_RESOURCE_ID": "synthetic", "AUTOMATION_JOB_NAMESPACE": "synthetic-eval.preproduction",
        "AUTOMATION_INTAKE_SHARED_TOKEN": "synthetic-only", "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1",
        "AUTOMATION_RELEASE_ID": "synthetic", "AUTOMATION_IMAGE_DIGEST": "sha256:" + "a" * 64,
        "APP_BUILD_REF": "synthetic", "PROMPT_RELEASE_ID": "synthetic-code-snapshot",
    }
    with patch.dict(os.environ, settings_env, clear=True):
        settings = AutomationEcsSettings.from_env("worker")
    store = InMemoryAutomationEcsStore(settings)
    store.migrate()
    repository = InMemoryTicketRepository()
    event = AutomationIntakeEvent.model_validate({
        "schema_version": INTAKE_CONTRACT_VERSION, "event_id": "synthetic-" + sample["sample_id"],
        "event_type": "ticket.created", "occurred_at": "2026-10-13T00:00:00Z",
        "ticket": {"id": "00000", "status": "open", "subject": "Synthetic support evaluation",
                   "description": sample["customer_message"],
                   "requester": {"email": "synthetic@example.com", "name": sample["basis"]["greeting_name"]}},
    })
    store.accept_intake(event, settings.provenance())
    route_job = store.claim_job(JobKind.ROUTE, worker_id="synthetic", lease_seconds=60)
    handoff = store.hand_off_to_hermes_agent(route_job, prompt_release_id="synthetic-code-snapshot")
    turn_id = handoff["turn_id"]
    sample["basis"]["source_turn_id"] = turn_id
    direction = sample["basis"]["direction"]
    store.record_hermes_turn_direction(turn_id, direction=direction, route=sample["route"])
    store.record_hermes_case_direction(turn_id, direction=direction, reason="synthetic authoritative fixture")
    store.record_hermes_turn_work(turn_id, work_result={"status": sample["basis"]["stage"], "reply_basis": sample["basis"]})
    store.set_hermes_turn_snapshot(turn_id, snapshot={"greeting_name": sample["basis"]["greeting_name"]})
    if direction == "investigation":
        store.save_hermes_investigation(turn_id, summary=sample["basis"]["trusted_conclusion"]["answer"],
            evidence=[{"source": "synthetic investigation record", "finding": sample["basis"]["trusted_conclusion"]["answer"]}],
            blockers=[], next_steps=[])
    turn = store.get_hermes_turn(turn_id)
    canonical = basis_for_persona_turn(turn=turn, binding=store.get_hermes_case_binding("00000"))
    assert canonical is not None
    sample["basis"] = canonical
    store.record_hermes_turn_work(turn_id, work_result={"status": canonical["stage"], "reply_basis": canonical})
    store.get_or_create_hermes_turn_run(turn_id, "persona", prompt_version="hermes-persona-manual")
    store.start_hermes_turn_run(turn_id, "persona", run_id="synthetic-rendering-adapter")
    repository.save_account_case({"account_case_id": "synthetic-case", "client_ticket_id": "00000",
        "automation_status": "automation" if direction == "automation" else "human_review_required",
        "collected_fields": canonical["collected_facts"], "missing_fields": canonical["missing_fields"],
        "internal_email_send_status": canonical["trusted_conclusion"].get("internal_email_send_status")})
    snapshot = build_case_snapshot(store, repository, zendesk_ticket_id="00000", case_revision=1,
                                   current_event=event.model_dump(mode="json"))
    _SNAPSHOTS[sample["sample_id"]] = snapshot
    return copy.deepcopy(snapshot)


def validate_carrier(instructions, snapshot, basis):
    for key in ("hermes-persona-manual", "hermes-reply-contract"):
        layer = resolve_system_prompt(key, "")
        if not layer or layer not in instructions:
            raise ValueError("missing_prompt_layer:" + key)
    turn = next((t for t in snapshot.get("turns", []) if t["turn_id"] == basis["source_turn_id"]), {})
    if turn.get("reply_basis") != basis:
        raise ValueError("snapshot_basis_mismatch")
    for key in ("direction", "route"):
        if turn.get(key) != basis.get(key):
            raise ValueError("snapshot_basis_identity_mismatch:" + key)
    if snapshot.get("greeting_name") != basis.get("greeting_name"):
        raise ValueError("snapshot_basis_greeting_mismatch")


def _user_prompt(sample):
    return ("CASE SNAPSHOT (JSON):\n" + json.dumps(_snapshot(sample), ensure_ascii=False, sort_keys=True)
            + "\n\n--- REPLY BASIS FOR THIS TURN ---\n" + json.dumps(sample["basis"], ensure_ascii=False, sort_keys=True)
            + "\n\nOffline rendering adapter: no tools are available. Return the English draft body text "
              "that you would pass to save_reply_draft. The server projects the greeting and prepares customer-language delivery.")


def judge_draft(raw, basis):
    try:
        # Judge untouched output first: projection must never hide duplicate greetings.
        validate_customer_reply_draft(raw, basis)
        final = project_greeting(raw, basis["greeting_name"])
        validate_customer_reply_draft(final, basis)
        expected = "Hi " + basis["greeting_name"] + ","
        if final.splitlines()[0] != expected or final.lower().count("hi ") != 1:
            return {"passed": False, "code": "greeting_contract"}, final
        return {"passed": True, "code": "pass"}, final
    except HermesReplyBasisError as exc:
        return {"passed": False, "code": exc.code}, None


def negative_controls():
    sample = SAMPLES[0]
    instructions, _ = _system_prompt()
    checks = []
    for key in ("hermes-persona-manual", "hermes-reply-contract"):
        broken = instructions.replace(resolve_system_prompt(key, ""), "")
        try:
            validate_carrier(broken, _snapshot(sample), sample["basis"])
            detected = False
        except ValueError:
            detected = True
        checks.append({"control": "missing_" + key, "detected": detected})
    snapshot = _snapshot(sample)
    snapshot["turns"][0]["reply_basis"]["missing_fields"] = []
    try:
        validate_carrier(instructions, snapshot, sample["basis"])
        detected = False
    except ValueError:
        detected = True
    checks.append({"control": "snapshot_basis_mismatch", "detected": detected})
    for label, raw, code in (
        ("invented_restoration", "Your account has been restored.", "unsupported_account_state_claim"),
        ("unauthorized_timeline", "We will complete this within 24 hours.", "unsupported_time_commitment"),
        ("payment_credentials", "Please provide your credit card number.", "payment_information_request"),
        ("duplicate_greeting", "Hi Customer,\nHi Customer,\nPlease share your App ID.", "duplicate_greeting"),
    ):
        verdict, _ = judge_draft(raw, sample["basis"])
        checks.append({"control": label, "detected": not verdict["passed"] and verdict["code"] == code,
                       "actual_code": verdict["code"]})
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/tmp/natural_persona_results.json")
    parser.add_argument("--model-config-source", required=True)
    args = parser.parse_args()
    if not os.environ.get("AGENT_MODEL_ID"):
        raise SystemExit("AGENT_MODEL_ID from the target Persona deployment is required")
    from backend.services.llm_profiles import AUTOMATION_PERSONA_SCENARIO, resolve_model_profile
    from backend.services.llm_factory import invoke_responses_text
    from backend.services.automation_hermes_delivery import translate_draft_for_delivery, _validate_translated_content
    runtime = initialize_prompt_runtime()
    profile = replace(resolve_model_profile(AUTOMATION_PERSONA_SCENARIO),
        reasoning_effort=STANDARD_PHASE_REASONING_EFFORT, max_retries=0, fallback_models=(), fallback_profiles=())
    preset = next(p for p in ACCOUNT_PERSONA_PRESETS if p.persona_key == "sid-precise")
    evidence = {
        "schema_version": "natural-persona-offline-eval-v3", "created_at": datetime.now(timezone.utc).isoformat(),
        "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
        "source_files_sha256": {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in
             ["scripts/testing/natural_persona_eval.py", "backend/services/prompts/hermes_support_agent.py",
              "backend/services/account_admin.py", "backend/services/hermes_customer_reply_basis.py"]},
        "model": {"configured_id": profile.model, "provider": profile.provider,
                  "reasoning_effort": profile.reasoning_effort, "config_source": args.model_config_source,
                  "transport": "llm_factory Responses API offline adapter; not a live Hermes case run",
                  "max_retries": profile.max_retries, "fallback_models": []},
        "prompt": {"source": runtime.source, "release_id": runtime.release_id,
                   "manual_version": "hermes-persona-manual-v6", "contract_version": "hermes-reply-contract-v6",
                   "assembly": "shared complete contract; direction/route/customer/basis are sample context"},
        "persona": {"key": preset.persona_key, "seed_marker": preset.seed_marker,
                    "content": preset.content, "content_sha256": hashlib.sha256(json.dumps(preset.content,
                         ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                    "published_runtime_verified": False},
        "negative_controls": negative_controls(), "samples": [],
    }
    if not all(c["detected"] for c in evidence["negative_controls"]):
        raise SystemExit("negative control failed")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    for sample in SAMPLES:
        snapshot = _snapshot(sample)
        instructions, prompt_key = _system_prompt(sample["basis"]["direction"], sample["route"])
        validate_carrier(instructions, snapshot, sample["basis"])
        request = {"system_prompt": instructions, "user_prompt": _user_prompt(sample),
                   "model": profile.model, "reasoning_effort": profile.reasoning_effort, "temperature": profile.temperature}
        row = {"sample_id": sample["sample_id"], "basis": sample["basis"], "snapshot": snapshot,
               "request": request, "prompt_key": prompt_key, "system_sha256": hashlib.sha256(instructions.encode()).hexdigest()}
        try:
            result = invoke_responses_text(profile=profile, system_prompt=instructions, user_prompt=request["user_prompt"])
            raw = result.text
            verdict, projected = judge_draft(raw, sample["basis"])
            row.update(raw_output=raw, projected_draft=projected, verdict=verdict,
                       returned_model_id=(result.raw_payload or {}).get("model"), client_model_id=result.model_name,
                       usage={"input_tokens": result.prompt_tokens, "output_tokens": result.completion_tokens})
            if verdict["passed"]:
                translated = translate_draft_for_delivery(english_content=projected, language_reference=sample["customer_message"])
                final = projected if translated.reference_language == "english" else translated.translated_text
                safety = _validate_translated_content(final, projected, sample["customer_message"], reference_language=translated.reference_language)
                validate_customer_reply_draft(final, sample["basis"])
                row.update(final_delivery_body=final, reference_language=translated.reference_language,
                           translation_safety_error=safety, language_path="real translate_draft_for_delivery + safety validator")
                if safety or translated.reference_language == "undetermined":
                    row["verdict"] = {"passed": False, "code": safety or "language_undetermined"}
            row["naturalness_review"] = {"status": "pending_human_text_review", "basis": "read raw prose and final body; no keyword scoring"}
        except Exception as exc:
            row["verdict"] = {"passed": False, "code": type(exc).__name__}
        evidence["samples"].append(row)
        evidence["summary"] = {"total": len(evidence["samples"]),
            "passed": sum(r["verdict"]["passed"] for r in evidence["samples"]),
            "failed_samples": [r["sample_id"] for r in evidence["samples"] if not r["verdict"]["passed"]]}
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(sample["sample_id"], row["verdict"], flush=True)
    return 0 if evidence["summary"]["passed"] == len(SAMPLES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
