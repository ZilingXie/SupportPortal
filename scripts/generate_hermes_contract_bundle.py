from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.services.hermes_case_workflow import (
    CaseKnowledgePromotion,
    HermesInvestigationOutput,
    HermesTurnRequest,
    _promotion_content_hash,
)


def _bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _fixtures() -> tuple[dict[str, Any], dict[str, Any]]:
    opening = {
        "schema_version": "v1",
        "request_id": "hermes-request:case-1:1:0:opening",
        "engineer_case_id": "case-1",
        "client_ticket_id": "ticket-1",
        "investigation_id": "investigation-1",
        "hermes_conversation_key": "supportportal:engineer-case:case-1",
        "hermes_session_id": "hermes-session:case-1",
        "episode": 1,
        "conversation_version": 0,
        "turn_kind": "opening",
        "input": {
            "problem_description": "A synthetic technical issue.",
            "investigation_scope": "Investigate the synthetic issue.",
            "completion_criteria": ["Produce an evidence-backed conclusion."],
        },
        "slack_channel_id": "C-SYNTHETIC",
        "slack_thread_ts": "1757060000.000001",
        "session_binding_version": 1,
        "data_boundary": "curated_case_context",
        "human_authority": None,
        "created_at": "2026-09-05T08:00:00Z",
    }
    result = {
        "schema_version": "v1",
        "output_id": "hermes-output-1",
        "request_id": opening["request_id"],
        "engineer_case_id": opening["engineer_case_id"],
        "investigation_id": opening["investigation_id"],
        "hermes_conversation_key": opening["hermes_conversation_key"],
        "hermes_session_id": opening["hermes_session_id"],
        "episode": 1,
        "conversation_version": 0,
        "output_version": 1,
        "output_kind": "investigation_result",
        "round_id": None,
        "text": "Investigation result: test",
        "ledger_delta": {
            "schema_version": "v1",
            "problem_description": None,
            "investigation_process": "Investigation result: test",
            "misjudgment_corrections": None,
            "current_conclusion_next_steps": "Investigation result: test",
            "references": None,
        },
        "available_actions": [],
        "producer_contract_version": "v1",
        "created_at": "2026-09-05T08:01:00Z",
    }
    promotion = {
        "schema_version": "v1",
        "promotion_id": "promotion-case-1-1",
        "engineer_case_id": opening["engineer_case_id"],
        "client_ticket_id": opening["client_ticket_id"],
        "investigation_id": opening["investigation_id"],
        "episode": 1,
        "ledger_revision": 1,
        "status": "awaiting_transport",
        "sanitized_knowledge": {"summary": "A sanitized synthetic conclusion."},
        "evidence_categories": ["synthetic_test"],
        "applicability": ["workflow contract tests"],
        "limitations": ["Synthetic evidence only."],
        "corrections": [],
        "review": {"verdict": "pass", "reason": "reviewed"},
        "guardrail": {"verdict": "pass", "reason": "safe"},
        "sanitization": {"verdict": "pass", "reason": "sanitized"},
        "closed_revision_proof": {
            "status": "closed",
            "episode": 1,
            "ledger_revision": 1,
            "closed_at": "2026-09-05T09:00:00Z",
        },
        "targets": ["tencentdb_knowledge", "skill_evolution"],
        "created_at": "2026-09-05T09:00:00Z",
    }
    promotion["content_hash"] = _promotion_content_hash(promotion)
    valid = {
        "turn-opening.json": opening,
        "output-investigation-result.json": result,
        "promotion-closed.json": promotion,
    }
    invalid = {
        "turn-null-session.json": {**opening, "hermes_session_id": None},
        "turn-wake-kind.json": {**opening, "turn_kind": "wake"},
        "turn-legacy-route-fields.json": {**opening, "case_id": "case-1"},
        "output-model-route-field.json": {**result, "case_id": "forged-case"},
        "output-result-with-action.json": {
            **result,
            "available_actions": [{
                "action": "authorize_round",
                "target_round_id": "round-1",
                "target_version": 1,
                "target_digest": "digest-1",
            }],
        },
        "promotion-bad-hash.json": {**promotion, "content_hash": "0" * 64},
        "promotion-stale-revision.json": {
            **promotion,
            "closed_revision_proof": {**promotion["closed_revision_proof"], "ledger_revision": 2},
        },
        "promotion-guardrail-failed.json": {
            **promotion,
            "guardrail": {"verdict": "fail", "reason": "unsafe"},
        },
        "promotion-restricted-identifier.json": {
            **promotion,
            "sanitized_knowledge": {"summary": "Contains <restricted> customer identity."},
        },
    }
    return valid, invalid


def generate(output_dir: Path) -> None:
    schemas = {
        "HermesTurnRequest.v1.schema.json": HermesTurnRequest.model_json_schema(),
        "HermesInvestigationOutput.v1.schema.json": HermesInvestigationOutput.model_json_schema(),
        "CaseKnowledgePromotion.v1.schema.json": CaseKnowledgePromotion.model_json_schema(),
    }
    for schema in schemas.values():
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    valid, invalid = _fixtures()
    files: dict[str, bytes] = {}
    for name, value in schemas.items():
        files[f"schemas/{name}"] = _bytes(value)
    for name, value in valid.items():
        files[f"fixtures/valid/{name}"] = _bytes(value)
    for name, value in invalid.items():
        files[f"fixtures/invalid/{name}"] = _bytes(value)
    for relative, content in files.items():
        target = output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    file_hashes = {
        name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())
    }
    bundle_hash = hashlib.sha256(
        "".join(f"{name}\0{digest}\n" for name, digest in file_hashes.items()).encode("utf-8")
    ).hexdigest()
    (output_dir / "manifest.json").write_bytes(_bytes({
        "bundle": "HermesCaseContracts.v1",
        "bundle_hash": bundle_hash,
        "files": file_hashes,
    }))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    for output_dir in args.output_dirs:
        generate(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
