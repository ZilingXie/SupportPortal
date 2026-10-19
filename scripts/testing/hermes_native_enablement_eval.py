"""Gated, no-write model evaluation for native Hermes missing-App-ID replies.

Run only with ``HERMES_NATIVE_ENABLEMENT_EVAL=1`` and configured model
credentials. The samples are synthetic and contain no customer data. The
output is an explicit evidence artifact supplied by the caller; it is never
written to a ticket, database, release, or delivery ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services.automation_hermes_agent import phase_instructions
from backend.services.hermes_customer_reply_basis import (
    build_hermes_customer_reply_basis,
    project_greeting,
    validate_customer_reply_draft,
)
from backend.services.llm_factory import invoke_responses_text
from backend.services.llm_profiles import AUTOMATION_PERSONA_SCENARIO, resolve_model_profile
from backend.services.prompt_runtime import _code_snapshot, use_prompt_runtime_snapshot


SAMPLES = (
    ("named_english", "Taylor", "Please enable Media Relay for my project."),
    ("anonymous_english", "Customer", "Could you turn on Media Relay for my project?"),
    ("named_chinese", "Taylor", "你好，请帮我开通 Media Relay。"),
)


def _semantic_verdict(final_text: str, *, greeting_name: str) -> dict[str, bool]:
    lowered = final_text.lower()
    return {
        "c1_only_app_id_request": bool(re.search(r"\bapp id\b", lowered)) and not bool(
            re.search(r"payment|billing address|email address|console configuration", lowered)
        ),
        "c2_feature_and_next_step": "media relay" in lowered and bool(
            re.search(r"continue|proceed|once .*app id|after .*app id", lowered)
        ),
        "c3_no_unsupported_claim": not bool(
            re.search(r"submitted|already enabled|review completed|within 24 hours|within 48 hours", lowered)
        ),
        "c4_single_server_greeting": final_text.startswith(f"Hi {greeting_name},")
        and final_text.count("Hi ") == 1
        and not bool(re.search(r"workflow|reply basis|internal ticket|prompt", lowered)),
    }


def main() -> int:
    if os.getenv("HERMES_NATIVE_ENABLEMENT_EVAL") != "1":
        print("waiting-for-evidence: set HERMES_NATIVE_ENABLEMENT_EVAL=1 for explicit model evaluation")
        return 2
    output_path = Path(
        os.getenv("HERMES_NATIVE_ENABLEMENT_EVAL_OUTPUT", "/tmp/hermes_native_enablement_eval.json")
    )
    profile = resolve_model_profile(AUTOMATION_PERSONA_SCENARIO)
    if not profile.has_invocation_credentials():
        print("waiting-for-evidence: configured persona model has no invocation credentials")
        return 2

    rows: list[dict[str, object]] = []
    with use_prompt_runtime_snapshot(_code_snapshot()):
        system_prompt, prompt_key = phase_instructions(
            "persona", direction="automation", route="enablement", persona_key="eval"
        )
        prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
        for sample_id, greeting_name, customer_message in SAMPLES:
            basis = build_hermes_customer_reply_basis(
                source_turn_id=f"eval-{sample_id}",
                direction="automation",
                route="enablement",
                stage="automation_result",
                collected_facts={"requested_feature": "media_relay", "requested_feature_label": "Media Relay"},
                missing_fields=["app_id"],
                trusted_conclusion={"status": "missing_fields"},
                allowed_claims=["state:missing_fields", "feature:media_relay", "next_step:app_id"],
                forbidden_topics=["payment information", "internal implementation"],
                publication_policy="auto",
                greeting_name=greeting_name,
            )
            user_prompt = json.dumps(
                {"customer_message": customer_message, "reply_basis": basis},
                ensure_ascii=False,
                sort_keys=True,
            )
            result = invoke_responses_text(
                profile=profile,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            raw_draft = str(result.text or "").strip()
            final_draft = project_greeting(raw_draft, greeting_name)
            try:
                validation = validate_customer_reply_draft(final_draft, basis)
                validation_result: object = validation
            except Exception as exc:  # evidence must show fail-closed diagnostics
                validation_result = {"error": type(exc).__name__, "code": getattr(exc, "code", "")}
            rows.append(
                {
                    "sample_id": sample_id,
                    "request_messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "prompt_key": prompt_key,
                    "prompt_version": "hermes-reply-contract-v7",
                    "prompt_sha256": prompt_hash,
                    "model": result.provider_model_name,
                    "raw_draft": raw_draft,
                    "final_draft": final_draft,
                    "basis": basis,
                    "validator": validation_result,
                    "verdict": _semantic_verdict(final_draft, greeting_name=greeting_name),
                }
            )
    output_path.write_text(
        json.dumps(
            {"schema_version": "hermes-native-enablement-eval-v1", "created_at": datetime.now(timezone.utc).isoformat(), "rows": rows},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
