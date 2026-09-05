from __future__ import annotations

import copy
import hashlib
import hmac
import json
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Json


class HermesRepositoryConflict(RuntimeError):
    pass


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _row_dict(row: tuple[Any, ...] | None, fields: tuple[str, ...]) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(zip(fields, row))
    for field in ("created_at", "updated_at", "claimed_at", "lease_expires_at", "decided_at"):
        if result.get(field) is not None:
            result[field] = _iso(result[field])
    return result


class InMemoryHermesCaseRepositoryMixin:
    def _initialize_hermes_state(self) -> None:
        self._hermes_case_bindings: dict[str, dict[str, Any]] = {}
        self._hermes_turn_requests: dict[str, dict[str, Any]] = {}
        self._hermes_outputs: dict[str, dict[str, Any]] = {}
        self._hermes_case_ledgers: dict[str, dict[str, Any]] = {}
        self._hermes_summary_snapshots: dict[str, dict[str, Any]] = {}
        self._hermes_authority_events: dict[str, dict[str, Any]] = {}
        self._hermes_close_reviews: dict[str, dict[str, Any]] = {}
        self._hermes_promotions: dict[str, dict[str, Any]] = {}
        self._hermes_rejection_receipts: dict[str, dict[str, Any]] = {}

    def start_hermes_case(self, request: dict[str, Any]) -> dict[str, Any]:
        case_id = str(request["engineer_case_id"])
        with self._assignment_lock:
            existing = self._hermes_case_bindings.get(case_id)
            if existing is not None:
                return copy.deepcopy(existing)
            now = str(request["created_at"])
            binding = {
                "engineer_case_id": case_id,
                "client_ticket_id": str(request["client_ticket_id"]),
                "investigation_id": str(request["investigation_id"]),
                "hermes_conversation_key": str(request["hermes_conversation_key"]),
                "hermes_session_id": request.get("hermes_session_id"),
                "binding_version": int(request["session_binding_version"]),
                "episode": int(request["episode"]),
                "conversation_version": int(request["conversation_version"]),
                "current_output_id": None,
                "current_ledger_revision": 0,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
            self._hermes_case_bindings[case_id] = binding
            self._hermes_case_ledgers[case_id] = {
                "engineer_case_id": case_id,
                "problem_description": str((request.get("input") or {}).get("problem_description") or ""),
                "investigation_process": "",
                "misjudgment_corrections": "",
                "current_conclusion_next_steps": "",
                "references": "",
                "episode": int(request["episode"]),
                "revision": 0,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
            self._hermes_turn_requests[str(request["request_id"])] = {
                **copy.deepcopy(request),
                "status": "queued",
                "owner_token": None,
                "claimed_at": None,
                "lease_expires_at": None,
                "runtime_receipt": None,
                "failure_code": None,
                "updated_at": now,
            }
            return copy.deepcopy(binding)

    def get_hermes_case_binding(self, engineer_case_id: str) -> dict[str, Any] | None:
        with self._assignment_lock:
            value = self._hermes_case_bindings.get(str(engineer_case_id))
            return copy.deepcopy(value) if value is not None else None

    def get_hermes_case_ledger(self, engineer_case_id: str) -> dict[str, Any] | None:
        with self._assignment_lock:
            value = self._hermes_case_ledgers.get(str(engineer_case_id))
            if value is None:
                return None
            binding = self._hermes_case_bindings.get(str(engineer_case_id), {})
            return {**copy.deepcopy(value), "client_ticket_id": binding.get("client_ticket_id")}

    def get_hermes_output(self, output_id: str) -> dict[str, Any] | None:
        with self._assignment_lock:
            value = self._hermes_outputs.get(str(output_id))
            return copy.deepcopy(value) if value is not None else None

    def get_hermes_rejection_receipt(self, output_id: str) -> dict[str, Any] | None:
        with self._assignment_lock:
            value = self._hermes_rejection_receipts.get(f"hermes-rejection:{output_id}")
            return copy.deepcopy(value) if value is not None else None

    def list_hermes_turn_requests(self, engineer_case_id: str | None = None) -> list[dict[str, Any]]:
        with self._assignment_lock:
            rows = [
                copy.deepcopy(row)
                for row in self._hermes_turn_requests.values()
                if engineer_case_id is None or row["engineer_case_id"] == engineer_case_id
            ]
        return sorted(rows, key=lambda row: (row["created_at"], row["request_id"]))

    def claim_next_hermes_turn(
        self, *, owner_token: str, claimed_at: str, lease_expires_at: str
    ) -> dict[str, Any] | None:
        with self._assignment_lock:
            candidates = sorted(
                (
                    row
                    for row in self._hermes_turn_requests.values()
                    if row["status"] == "queued"
                    or (row["status"] == "active" and str(row.get("lease_expires_at") or "") <= claimed_at)
                ),
                key=lambda row: (row["created_at"], row["request_id"]),
            )
            if not candidates:
                return None
            for candidate in candidates:
                case_id = candidate["engineer_case_id"]
                conflicting = any(
                    row is not candidate
                    and row["engineer_case_id"] == case_id
                    and row["status"] == "active"
                    and str(row.get("lease_expires_at") or "") > claimed_at
                    for row in self._hermes_turn_requests.values()
                )
                if not conflicting:
                    return self._claim_hermes_turn_unlocked(
                        candidate, owner_token, claimed_at, lease_expires_at
                    )
            return None

    def claim_hermes_turn(
        self, *, request_id: str, owner_token: str, claimed_at: str, lease_expires_at: str
    ) -> dict[str, Any] | None:
        with self._assignment_lock:
            row = self._hermes_turn_requests.get(str(request_id))
            if row is None:
                return None
            if row["status"] not in {"queued", "active"}:
                return None
            if row["status"] == "active" and str(row.get("lease_expires_at") or "") > claimed_at:
                return None
            return self._claim_hermes_turn_unlocked(row, owner_token, claimed_at, lease_expires_at)

    @staticmethod
    def _claim_hermes_turn_unlocked(
        row: dict[str, Any], owner_token: str, claimed_at: str, lease_expires_at: str
    ) -> dict[str, Any]:
        row.update(
            status="active",
            owner_token=str(owner_token),
            claimed_at=claimed_at,
            lease_expires_at=lease_expires_at,
            updated_at=claimed_at,
        )
        return copy.deepcopy(row)

    def freeze_hermes_turn_request(
        self, request_id: str, *, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self._assignment_lock:
            row = self._hermes_turn_requests.get(str(request_id))
            if row is None:
                raise HermesRepositoryConflict("unknown Hermes turn")
            existing_channel = str(row.get("slack_channel_id") or "")
            existing_thread = str(row.get("slack_thread_ts") or "")
            if existing_channel or existing_thread:
                if (
                    row.get("slack_channel_id") != payload.get("slack_channel_id")
                    or row.get("slack_thread_ts") != payload.get("slack_thread_ts")
                ):
                    raise HermesRepositoryConflict("Hermes turn payload is already frozen")
                return copy.deepcopy(row)
            if row.get("status") not in {"queued", "active"}:
                raise HermesRepositoryConflict("Hermes turn cannot be frozen in its current state")
            row.update(copy.deepcopy(payload))
            return copy.deepcopy(row)

    def mark_hermes_turn_awaiting_result(
        self, request_id: str, *, owner_token: str, receipt: dict[str, Any], accepted_at: str
    ) -> dict[str, Any]:
        with self._assignment_lock:
            row = self._hermes_turn_requests.get(str(request_id))
            if row is None or row.get("status") != "active" or row.get("owner_token") != owner_token:
                raise HermesRepositoryConflict("stale Hermes turn delivery")
            row.update(
                status="awaiting_result", runtime_receipt=copy.deepcopy(receipt),
                failure_code=None, lease_expires_at=None, updated_at=accepted_at,
            )
            return copy.deepcopy(row)

    def record_hermes_turn_delivery_failure(
        self, request_id: str, *, owner_token: str, failure_code: str,
        retryable: bool, failed_at: str,
    ) -> dict[str, Any]:
        with self._assignment_lock:
            row = self._hermes_turn_requests.get(str(request_id))
            if row is None or row.get("status") != "active" or row.get("owner_token") != owner_token:
                raise HermesRepositoryConflict("stale Hermes turn delivery")
            row.update(
                status="queued" if retryable else "failed", owner_token=None,
                claimed_at=None, lease_expires_at=None, failure_code=str(failure_code),
                updated_at=failed_at,
            )
            return copy.deepcopy(row)

    def apply_hermes_output(self, output: dict[str, Any], slack_event: dict[str, Any]) -> dict[str, Any]:
        output_id = str(output["output_id"])
        request_id = str(output["request_id"])
        case_id = str(output["engineer_case_id"])
        with self._assignment_lock:
            existing = self._hermes_outputs.get(output_id)
            if existing is not None and existing.get("accepted"):
                return {"status": "idempotent", "output_id": output_id}
            request = self._hermes_turn_requests.get(request_id)
            binding = self._hermes_case_bindings.get(case_id)
            if request is None or binding is None:
                return self._reject_hermes_output_unlocked(output, "unknown_request_or_case")
            mismatch = (
                request["engineer_case_id"] != case_id
                or request["investigation_id"] != output["investigation_id"]
                or request["hermes_conversation_key"] != output["hermes_conversation_key"]
                or int(binding["episode"]) != int(output["episode"])
                or int(binding["conversation_version"]) != int(output["conversation_version"])
                or int(request["episode"]) != int(output["episode"])
                or int(request["conversation_version"]) != int(output["conversation_version"])
                or request.get("hermes_session_id") != binding.get("hermes_session_id")
                or int(request["session_binding_version"]) != int(binding["binding_version"])
                or str(request.get("status") or "") not in {"active", "awaiting_result"}
            )
            if mismatch:
                return self._reject_hermes_output_unlocked(output, "stale_lineage")
            existing_slack_event = self._engineer_slack_events.get(str(slack_event["event_id"]))
            if existing_slack_event is not None:
                raise HermesRepositoryConflict("Hermes Slack outbox conflict")
            for snapshot in self._hermes_summary_snapshots.values():
                if snapshot["engineer_case_id"] == case_id and snapshot["status"] == "frozen":
                    snapshot.update(status="superseded", updated_at=str(output["created_at"]))
            engineer_case = self._engineer_cases.get(case_id)
            if isinstance(engineer_case, dict):
                engineer_case["draft_customer_reply"] = ""
                engineer_case["final_confirmation_requested_at"] = None
                state = dict(engineer_case.get("engineer_agent_state") or {})
                for key in (
                    "hermes_summary_snapshot_id", "hermes_summary_guardrail",
                    "guided_reply_generation", "reply_readiness", "active_guardrail_final",
                    "guardrail_final_id", "guardrail_final_version", "guardrail_final_decision",
                    "final_approval_required", "final_approved_at",
                ):
                    state.pop(key, None)
                state.update(
                    conversation_version=int(output["conversation_version"]),
                    draft_version=0,
                    round_state="active",
                )
                engineer_case["engineer_agent_state"] = state
                engineer_case["updated_at"] = str(output["created_at"])
            ledger = self._hermes_case_ledgers[case_id]
            delta = dict(output.get("ledger_delta") or {})
            for field in (
                "problem_description",
                "investigation_process",
                "misjudgment_corrections",
                "current_conclusion_next_steps",
                "references",
            ):
                value = delta.get(field)
                if value is not None:
                    ledger[field] = str(value)
            ledger["revision"] = int(ledger["revision"]) + 1
            ledger["episode"] = int(output["episode"])
            ledger["updated_at"] = str(output["created_at"])
            binding.update(
                hermes_session_id=str(output["hermes_session_id"]),
                current_output_id=output_id,
                current_ledger_revision=ledger["revision"],
                binding_version=int(binding["binding_version"]) + 1,
                updated_at=str(output["created_at"]),
            )
            request.update(status="completed", updated_at=str(output["created_at"]))
            self._hermes_outputs[output_id] = {
                **copy.deepcopy(output),
                "accepted": True,
                "rejection_reason": None,
            }
            record = {
                "event_id": slack_event["event_id"],
                "engineer_case_id": case_id,
                "event_type": slack_event["event_type"],
                "payload": copy.deepcopy(slack_event),
                "status": "queued",
                "failure_code": None,
                "slack_channel_id": None,
                "slack_message_ts": None,
                "slack_thread_ts": None,
                "confirmed_at": None,
                "created_at": str(output["created_at"]),
                "updated_at": str(output["created_at"]),
            }
            self._engineer_slack_events.setdefault(record["event_id"], record)
            return {
                "status": "accepted",
                "output_id": output_id,
                "ledger_revision": ledger["revision"],
            }

    def _reject_hermes_output_unlocked(
        self, output: dict[str, Any], reason: str
    ) -> dict[str, Any]:
        output_id = str(output.get("output_id") or "unknown")
        receipt_id = f"hermes-rejection:{output_id}"
        self._hermes_rejection_receipts.setdefault(
            receipt_id,
            {
                "receipt_id": receipt_id,
                "output_id": output_id,
                "request_id": str(output.get("request_id") or ""),
                "engineer_case_id": str(output.get("engineer_case_id") or ""),
                "reason": reason,
                "created_at": str(output.get("created_at") or ""),
            },
        )
        return {"status": "rejected", "output_id": output_id, "reason": reason}

    def freeze_hermes_summary(self, engineer_case_id: str, *, snapshot_id: str, frozen_at: str) -> dict[str, Any]:
        case_id = str(engineer_case_id)
        with self._assignment_lock:
            existing = self._hermes_summary_snapshots.get(snapshot_id)
            if existing is not None:
                return copy.deepcopy(existing)
            binding = self._hermes_case_bindings.get(case_id)
            ledger = self._hermes_case_ledgers.get(case_id)
            if binding is None or ledger is None or not binding.get("current_output_id"):
                raise HermesRepositoryConflict("Hermes output is not available")
            output = self._hermes_outputs[str(binding["current_output_id"])]
            snapshot = {
                "snapshot_id": snapshot_id,
                "engineer_case_id": case_id,
                "episode": binding["episode"],
                "conversation_version": binding["conversation_version"],
                "output_id": binding["current_output_id"],
                "ledger_revision": ledger["revision"],
                "summary": str(output["text"]),
                "status": "frozen",
                "guardrail_decision": None,
                "guardrail_reason": None,
                "created_at": frozen_at,
                "updated_at": frozen_at,
            }
            self._hermes_summary_snapshots[snapshot_id] = snapshot
            return copy.deepcopy(snapshot)

    def save_hermes_summary_guardrail(
        self,
        *,
        snapshot_id: str,
        expected_episode: int,
        expected_conversation_version: int,
        expected_output_id: str,
        expected_ledger_revision: int,
        decision: str,
        reason: str,
        decided_at: str,
    ) -> dict[str, Any]:
        with self._assignment_lock:
            snapshot = self._hermes_summary_snapshots.get(snapshot_id)
            if snapshot is None:
                raise HermesRepositoryConflict("unknown snapshot")
            binding = self._hermes_case_bindings[snapshot["engineer_case_id"]]
            current = (
                snapshot["status"] == "frozen"
                and binding["episode"] == expected_episode == snapshot["episode"]
                and binding["conversation_version"]
                == expected_conversation_version
                == snapshot["conversation_version"]
                and binding["current_output_id"] == expected_output_id == snapshot["output_id"]
                and binding["current_ledger_revision"]
                == expected_ledger_revision
                == snapshot["ledger_revision"]
            )
            if not current:
                raise HermesRepositoryConflict("stale summary snapshot")
            snapshot.update(
                guardrail_decision=decision,
                guardrail_reason=reason,
                decided_at=decided_at,
                updated_at=decided_at,
            )
            return {
                **copy.deepcopy(snapshot),
                "decision": decision,
                "reason": reason,
                "persona_required": True,
                "final_approval_required": True,
            }

    def queue_hermes_feedback_turn(self, request: dict[str, Any]) -> dict[str, Any]:
        case_id = str(request["engineer_case_id"])
        with self._assignment_lock:
            binding = self._hermes_case_bindings.get(case_id)
            if binding is None:
                raise HermesRepositoryConflict("unknown Hermes Case")
            if int(request["episode"]) != int(binding["episode"]):
                raise HermesRepositoryConflict("stale episode")
            next_version = int(binding["conversation_version"]) + 1
            next_binding_version = int(binding["binding_version"]) + 1
            if int(request["conversation_version"]) != next_version:
                raise HermesRepositoryConflict("stale conversation version")
            if int(request["session_binding_version"]) != next_binding_version:
                raise HermesRepositoryConflict("stale session binding version")
            binding.update(
                conversation_version=next_version,
                current_output_id=None,
                binding_version=next_binding_version,
                updated_at=str(request["created_at"]),
            )
            engineer_case = self._engineer_cases.get(case_id)
            if isinstance(engineer_case, dict):
                engineer_case["draft_customer_reply"] = ""
                engineer_case["final_confirmation_requested_at"] = None
                engineer_case["investigation_state"] = "active"
                state = dict(engineer_case.get("engineer_agent_state") or {})
                for key in (
                    "hermes_summary_snapshot_id", "hermes_summary_guardrail",
                    "guided_reply_generation", "reply_readiness", "active_guardrail_final",
                    "guardrail_final_id", "guardrail_final_version", "guardrail_final_decision",
                    "final_approval_required", "final_approved_at",
                ):
                    state.pop(key, None)
                state.update(conversation_version=next_version, draft_version=0, round_state="active")
                engineer_case["engineer_agent_state"] = state
                engineer_case["updated_at"] = str(request["created_at"])
            for snapshot in self._hermes_summary_snapshots.values():
                if snapshot["engineer_case_id"] == case_id and snapshot["status"] == "frozen":
                    snapshot["status"] = "superseded"
                    snapshot["updated_at"] = str(request["created_at"])
            self._hermes_turn_requests[str(request["request_id"])] = {
                **copy.deepcopy(request),
                "status": "queued",
                "owner_token": None,
                "claimed_at": None,
                "lease_expires_at": None,
                "updated_at": str(request["created_at"]),
            }
            return copy.deepcopy(request)

    def invalidate_hermes_reply_chain(self, engineer_case_id: str, *, invalidated_at: str) -> dict[str, Any] | None:
        with self._assignment_lock:
            binding = self._hermes_case_bindings.get(engineer_case_id)
            if not binding:
                return None
            next_version = int(binding["conversation_version"]) + 1
            binding.update(conversation_version=next_version, current_output_id=None,
                           binding_version=int(binding["binding_version"]) + 1, updated_at=invalidated_at)
            for snapshot in self._hermes_summary_snapshots.values():
                if snapshot["engineer_case_id"] == engineer_case_id and snapshot["status"] == "frozen":
                    snapshot.update(status="superseded", updated_at=invalidated_at)
            return copy.deepcopy(binding)

    def record_hermes_authority_event(
        self, event: dict[str, Any], request: dict[str, Any]
    ) -> dict[str, Any]:
        with self._assignment_lock:
            binding = self._hermes_case_bindings.get(str(event["engineer_case_id"]))
            if (
                binding is None
                or int(event["episode"]) != int(binding["episode"])
                or int(event["conversation_version"]) != int(binding["conversation_version"])
                or int(request["session_binding_version"]) != int(binding["binding_version"])
            ):
                raise HermesRepositoryConflict("stale Hermes authority event")
            event_id = str(event["authority_event_id"])
            existing = self._hermes_authority_events.get(event_id)
            if existing is not None:
                return {**copy.deepcopy(existing), "idempotent": True}
            close_review = None
            if event["action"] == "accept_and_finish":
                close_review = self._hermes_close_reviews.get(str(event["target_output_id"]))
                actual_digest = hashlib.sha256(
                    json.dumps(
                        (close_review or {}).get("review_payload") or {},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                if (
                    not close_review
                    or close_review["status"] != "awaiting_closed"
                    or int(close_review["episode"]) != int(binding["episode"])
                    or int(close_review["ledger_revision"]) != int(event["target_version"])
                    or not hmac.compare_digest(str(event["target_digest"]), actual_digest)
                ):
                    raise HermesRepositoryConflict("close review is stale")
            self._hermes_authority_events[event_id] = copy.deepcopy(event)
            self._hermes_turn_requests[str(request["request_id"])] = {
                **copy.deepcopy(request),
                "status": "queued",
                "owner_token": None,
                "claimed_at": None,
                "lease_expires_at": None,
                "updated_at": str(request["created_at"]),
            }
            if close_review is not None:
                close_review.update(
                    status="approved",
                    reviewer_id=str(event["actor_id"]),
                    updated_at=str(event["created_at"]),
                )
            return copy.deepcopy(event)

    def list_hermes_authority_events(self, engineer_case_id: str) -> list[dict[str, Any]]:
        with self._assignment_lock:
            return [
                copy.deepcopy(row)
                for row in self._hermes_authority_events.values()
                if row["engineer_case_id"] == engineer_case_id
            ]

    def record_hermes_case_solved(self, engineer_case_id: str, *, review_id: str, now_value: str) -> dict[str, Any]:
        with self._assignment_lock:
            binding = self._hermes_case_bindings.get(engineer_case_id)
            ledger = self._hermes_case_ledgers.get(engineer_case_id)
            if not binding or not ledger or not binding.get("current_output_id"):
                raise HermesRepositoryConflict("current Hermes output is required")
            review = {
                "review_id": review_id,
                "engineer_case_id": engineer_case_id,
                "episode": binding["episode"],
                "ledger_revision": ledger["revision"],
                "review_payload": {field: ledger[field] for field in (
                    "problem_description", "investigation_process", "misjudgment_corrections",
                    "current_conclusion_next_steps", "references",
                )},
                "status": "awaiting_closed",
                "reviewer_id": None,
                "created_at": now_value,
                "updated_at": now_value,
            }
            self._hermes_close_reviews.setdefault(review_id, review)
            binding.update(status="awaiting_closed", updated_at=now_value)
            ledger.update(status="awaiting_closed", updated_at=now_value)
            return copy.deepcopy(self._hermes_close_reviews[review_id])

    def get_hermes_close_review(self, review_id: str) -> dict[str, Any] | None:
        with self._assignment_lock:
            value = self._hermes_close_reviews.get(review_id)
            return copy.deepcopy(value) if value else None

    def approve_hermes_close_review(self, review_id: str, *, reviewer_id: str, now_value: str) -> dict[str, Any]:
        with self._assignment_lock:
            review = self._hermes_close_reviews.get(review_id)
            if not review or review["status"] != "awaiting_closed":
                raise HermesRepositoryConflict("close review is stale")
            review.update(status="approved", reviewer_id=reviewer_id, updated_at=now_value)
            return copy.deepcopy(review)

    def approve_current_hermes_close_review(self, engineer_case_id: str, *, reviewer_id: str, now_value: str) -> dict[str, Any]:
        with self._assignment_lock:
            binding = self._hermes_case_bindings.get(engineer_case_id)
            candidates = [row for row in self._hermes_close_reviews.values()
                          if row["engineer_case_id"] == engineer_case_id
                          and row["status"] == "awaiting_closed"]
            if not binding or not candidates:
                raise HermesRepositoryConflict("close review is stale")
            review = max(candidates, key=lambda row: row["created_at"])
            if (review["episode"] != binding["episode"]
                    or review["ledger_revision"] != binding["current_ledger_revision"]):
                raise HermesRepositoryConflict("close review is stale")
            review.update(status="approved", reviewer_id=reviewer_id, updated_at=now_value)
            return copy.deepcopy(review)

    def reopen_hermes_case(self, request: dict[str, Any]) -> dict[str, Any]:
        case_id = str(request["engineer_case_id"])
        with self._assignment_lock:
            binding = self._hermes_case_bindings.get(case_id)
            ledger = self._hermes_case_ledgers.get(case_id)
            if not binding or not ledger:
                raise HermesRepositoryConflict("unknown Hermes Case")
            if int(request["episode"]) != int(binding["episode"]) + 1:
                raise HermesRepositoryConflict("stale reopen episode")
            next_binding_version = int(binding["binding_version"]) + 1
            if int(request["session_binding_version"]) != next_binding_version:
                raise HermesRepositoryConflict("stale session binding version")
            binding.update(
                episode=request["episode"], conversation_version=request["conversation_version"],
                current_output_id=None, status="active",
                binding_version=next_binding_version, updated_at=request["created_at"],
            )
            ledger.update(episode=request["episode"], status="active", updated_at=request["created_at"])
            for review in self._hermes_close_reviews.values():
                if review["engineer_case_id"] == case_id and review["status"] != "invalidated":
                    review.update(status="invalidated", updated_at=request["created_at"])
            for promotion in self._hermes_promotions.values():
                if promotion["engineer_case_id"] == case_id and promotion["status"] != "invalidated":
                    promotion.update(status="invalidated", updated_at=request["created_at"])
            for snapshot in self._hermes_summary_snapshots.values():
                if snapshot["engineer_case_id"] == case_id and snapshot["status"] == "frozen":
                    snapshot.update(status="superseded", updated_at=request["created_at"])
            self._hermes_turn_requests[request["request_id"]] = {
                **copy.deepcopy(request), "status": "queued", "owner_token": None,
                "claimed_at": None, "lease_expires_at": None, "updated_at": request["created_at"],
            }
            engineer_case = self._engineer_cases.get(case_id)
            if engineer_case:
                engineer_case.update(status="investigating", investigation_state="active", closed_at=None,
                                     draft_customer_reply="", updated_at=request["created_at"])
            return copy.deepcopy(request)

    def close_hermes_case(self, promotion: dict[str, Any], *, now_value: str) -> dict[str, Any]:
        case_id = str(promotion["engineer_case_id"])
        with self._assignment_lock:
            binding = self._hermes_case_bindings.get(case_id)
            if not binding:
                raise HermesRepositoryConflict("unknown Hermes Case")
            reviews = [row for row in self._hermes_close_reviews.values()
                       if row["engineer_case_id"] == case_id and row["status"] == "approved"]
            snapshots = [row for row in self._hermes_summary_snapshots.values()
                         if row["engineer_case_id"] == case_id and row["status"] == "frozen"
                         and row.get("guardrail_decision") == "passed"]
            if not reviews or not snapshots:
                raise HermesRepositoryConflict("approved current close review and summary guardrail are required")
            review = max(reviews, key=lambda row: row["updated_at"])
            if (review["episode"] != binding["episode"]
                    or review["ledger_revision"] != binding["current_ledger_revision"]
                    or promotion["episode"] != binding["episode"]
                    or promotion["ledger_revision"] != binding["current_ledger_revision"]):
                raise HermesRepositoryConflict("close review is stale")
            self._hermes_promotions.setdefault(promotion["promotion_id"], {
                **copy.deepcopy(promotion), "owner_token": None, "claimed_at": None,
                "lease_expires_at": None, "runtime_receipt": None, "failure_code": None,
                "updated_at": now_value,
            })
            binding.update(status="closed", updated_at=now_value)
            self._hermes_case_ledgers[case_id].update(status="closed", updated_at=now_value)
            for request in self._hermes_turn_requests.values():
                if (
                    request["engineer_case_id"] == case_id
                    and request["status"] in {"queued", "active", "awaiting_result"}
                ):
                    request.update(status="cancelled", updated_at=now_value)
            return copy.deepcopy(self._hermes_promotions[promotion["promotion_id"]])

    def list_hermes_promotions(self) -> list[dict[str, Any]]:
        with self._assignment_lock:
            return [copy.deepcopy(row) for row in self._hermes_promotions.values()]

    def claim_hermes_promotion(
        self, promotion_id: str, *, owner_token: str, claimed_at: str, lease_expires_at: str
    ) -> dict[str, Any] | None:
        with self._assignment_lock:
            row = self._hermes_promotions.get(str(promotion_id))
            if row is None or row["status"] not in {"awaiting_transport", "active"}:
                return None
            if row["status"] == "active" and str(row.get("lease_expires_at") or "") > claimed_at:
                return None
            row.update(status="active", owner_token=owner_token, claimed_at=claimed_at,
                       lease_expires_at=lease_expires_at, updated_at=claimed_at)
            return copy.deepcopy(row)

    def complete_hermes_promotion_delivery(
        self, promotion_id: str, *, owner_token: str, status: str,
        receipt: dict[str, Any] | None, failure_code: str | None, completed_at: str,
    ) -> dict[str, Any]:
        if status not in {"accepted", "failed", "outcome_unknown"}:
            raise ValueError("invalid Hermes promotion delivery status")
        with self._assignment_lock:
            row = self._hermes_promotions.get(str(promotion_id))
            if row is None or row["status"] != "active" or row.get("owner_token") != owner_token:
                raise HermesRepositoryConflict("stale Hermes promotion delivery")
            row.update(status=status, runtime_receipt=copy.deepcopy(receipt),
                       failure_code=failure_code, lease_expires_at=None, updated_at=completed_at)
            return copy.deepcopy(row)


class PostgresHermesCaseRepositoryMixin:
    _BINDING_FIELDS = (
        "engineer_case_id", "client_ticket_id", "investigation_id",
        "hermes_conversation_key", "hermes_session_id", "binding_version",
        "episode", "conversation_version", "current_output_id",
        "current_ledger_revision", "status", "created_at", "updated_at",
    )
    _LEDGER_FIELDS = (
        "engineer_case_id", "problem_description", "investigation_process",
        "misjudgment_corrections", "current_conclusion_next_steps", "references",
        "episode", "revision", "status", "created_at", "updated_at",
    )

    def _initialize_hermes_schema(self, cur: psycopg.Cursor[Any]) -> None:
        tables = (
            ("support_hermes_case_bindings", """
                engineer_case_id TEXT PRIMARY KEY REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                client_ticket_id TEXT NOT NULL REFERENCES {}(ticket_id) ON DELETE CASCADE,
                investigation_id TEXT NOT NULL, hermes_conversation_key TEXT NOT NULL UNIQUE,
                hermes_session_id TEXT, binding_version INTEGER NOT NULL DEFAULT 1,
                episode INTEGER NOT NULL DEFAULT 1 CHECK (episode >= 1),
                conversation_version INTEGER NOT NULL DEFAULT 0 CHECK (conversation_version >= 0),
                current_output_id TEXT, current_ledger_revision INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL CHECK (status IN ('active','awaiting_closed','closed')),
                created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
            """, (self._table("support_engineer_cases"), self._table("support_tickets"))),
            ("support_hermes_case_ledgers", """
                engineer_case_id TEXT PRIMARY KEY REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                problem_description TEXT NOT NULL DEFAULT '', investigation_process TEXT NOT NULL DEFAULT '',
                misjudgment_corrections TEXT NOT NULL DEFAULT '',
                current_conclusion_next_steps TEXT NOT NULL DEFAULT '', "references" TEXT NOT NULL DEFAULT '',
                episode INTEGER NOT NULL DEFAULT 1 CHECK (episode >= 1), revision INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL CHECK (status IN ('active','awaiting_closed','closed')),
                created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
            """, (self._table("support_engineer_cases"),)),
            ("support_hermes_turn_requests", """
                request_id TEXT PRIMARY KEY, engineer_case_id TEXT NOT NULL REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                request_payload JSONB NOT NULL, turn_type TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN (
                    'queued','active','awaiting_result','completed','cancelled','failed'
                )),
                episode INTEGER NOT NULL, conversation_version INTEGER NOT NULL, hermes_session_id TEXT,
                owner_token TEXT, claimed_at TIMESTAMPTZ, lease_expires_at TIMESTAMPTZ,
                runtime_receipt JSONB, failure_code TEXT,
                created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
            """, (self._table("support_engineer_cases"),)),
            ("support_hermes_outputs", """
                output_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                engineer_case_id TEXT NOT NULL REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                output_payload JSONB NOT NULL, accepted BOOLEAN NOT NULL,
                rejection_reason TEXT, created_at TIMESTAMPTZ NOT NULL
            """, (self._table("support_engineer_cases"),)),
            ("support_hermes_rejection_receipts", """
                receipt_id TEXT PRIMARY KEY, output_id TEXT NOT NULL, request_id TEXT NOT NULL,
                engineer_case_id TEXT NOT NULL, reason TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL
            """, ()),
            ("support_hermes_summary_snapshots", """
                snapshot_id TEXT PRIMARY KEY, engineer_case_id TEXT NOT NULL REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                episode INTEGER NOT NULL, conversation_version INTEGER NOT NULL, output_id TEXT NOT NULL,
                ledger_revision INTEGER NOT NULL, summary TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('frozen','superseded')),
                guardrail_decision TEXT, guardrail_reason TEXT, decided_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
            """, (self._table("support_engineer_cases"),)),
            ("support_hermes_human_authority_events", """
                authority_event_id TEXT PRIMARY KEY, engineer_case_id TEXT NOT NULL REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                event_payload JSONB NOT NULL, episode INTEGER NOT NULL, conversation_version INTEGER NOT NULL,
                action TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL
            """, (self._table("support_engineer_cases"),)),
            ("support_hermes_close_reviews", """
                review_id TEXT PRIMARY KEY, engineer_case_id TEXT NOT NULL REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                episode INTEGER NOT NULL, ledger_revision INTEGER NOT NULL, review_payload JSONB NOT NULL,
                status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
            """, (self._table("support_engineer_cases"),)),
            ("support_hermes_case_promotions", """
                promotion_id TEXT PRIMARY KEY, engineer_case_id TEXT NOT NULL REFERENCES {}(engineer_case_id) ON DELETE CASCADE,
                episode INTEGER NOT NULL, ledger_revision INTEGER NOT NULL, promotion_payload JSONB NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('awaiting_transport','active','accepted','failed','outcome_unknown','invalidated')),
                owner_token TEXT, claimed_at TIMESTAMPTZ, lease_expires_at TIMESTAMPTZ,
                runtime_receipt JSONB, failure_code TEXT,
                created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
            """, (self._table("support_engineer_cases"),)),
        )
        for table_name, definition, identifiers in tables:
            cur.execute(
                sql.SQL("CREATE TABLE IF NOT EXISTS {} (").format(self._table(table_name))
                + sql.SQL(definition).format(*identifiers)
                + sql.SQL(")"),
            )
        cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (status, created_at, request_id)").format(
            sql.Identifier("idx_support_hermes_turn_requests_claim"), self._table("support_hermes_turn_requests")))
        cur.execute(sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (engineer_case_id) WHERE status='active'").format(
            sql.Identifier("idx_support_hermes_turn_requests_one_active"), self._table("support_hermes_turn_requests")))
        turn_table = self._table("support_hermes_turn_requests")
        cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS runtime_receipt JSONB").format(turn_table))
        cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS failure_code TEXT").format(turn_table))
        cur.execute(
            sql.SQL("ALTER TABLE {} DROP CONSTRAINT IF EXISTS {}").format(
                turn_table, sql.Identifier("support_hermes_turn_requests_status_check")
            )
        )
        cur.execute(
            sql.SQL(
                "ALTER TABLE {} ADD CONSTRAINT {} CHECK "
                "(status IN ('queued','active','awaiting_result','completed','cancelled','failed'))"
            ).format(
                turn_table, sql.Identifier("support_hermes_turn_requests_status_check")
            )
        )
        promotion_table = self._table("support_hermes_case_promotions")
        for column, definition in (
            ("owner_token", "TEXT"), ("claimed_at", "TIMESTAMPTZ"),
            ("lease_expires_at", "TIMESTAMPTZ"), ("runtime_receipt", "JSONB"),
            ("failure_code", "TEXT"),
        ):
            cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} " + definition).format(
                promotion_table, sql.Identifier(column)
            ))
        cur.execute(sql.SQL("ALTER TABLE {} DROP CONSTRAINT IF EXISTS {}").format(
            promotion_table, sql.Identifier("support_hermes_case_promotions_status_check")
        ))
        cur.execute(sql.SQL(
            "ALTER TABLE {} ADD CONSTRAINT {} CHECK "
            "(status IN ('awaiting_transport','active','accepted','failed','outcome_unknown','invalidated'))"
        ).format(
            promotion_table, sql.Identifier("support_hermes_case_promotions_status_check")
        ))

    def start_hermes_case(self, request: dict[str, Any]) -> dict[str, Any]:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                self._start_hermes_case_cur(cur, request)
                cur.execute(
                    sql.SQL("SELECT {} FROM {} WHERE engineer_case_id=%s").format(
                        sql.SQL(",").join(map(sql.Identifier, self._BINDING_FIELDS)),
                        self._table("support_hermes_case_bindings"),
                    ), (request["engineer_case_id"],),
                )
                return _row_dict(cur.fetchone(), self._BINDING_FIELDS) or {}
        return self._run_with_connection_retry("start_hermes_case", operation)

    def _start_hermes_case_cur(self, cur: psycopg.Cursor[Any], request: dict[str, Any]) -> None:
        now = request["created_at"]
        cur.execute(
            sql.SQL("""
                        INSERT INTO {} (engineer_case_id, client_ticket_id, investigation_id,
                            hermes_conversation_key, hermes_session_id, binding_version, episode,
                            conversation_version, current_ledger_revision, status, created_at, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,'active',%s,%s)
                        ON CONFLICT (engineer_case_id) DO NOTHING
            """).format(self._table("support_hermes_case_bindings")),
            (request["engineer_case_id"], request["client_ticket_id"], request["investigation_id"],
             request["hermes_conversation_key"], request.get("hermes_session_id"),
             request["session_binding_version"], request["episode"],
             request["conversation_version"], now, now),
        )
        cur.execute(
            sql.SQL("""
                        INSERT INTO {} (engineer_case_id, problem_description, episode, revision,
                            status, created_at, updated_at) VALUES (%s,%s,%s,0,'active',%s,%s)
                        ON CONFLICT (engineer_case_id) DO NOTHING
            """).format(self._table("support_hermes_case_ledgers")),
            (
                request["engineer_case_id"],
                str((request.get("input") or {}).get("problem_description") or ""),
                request["episode"], now, now,
            ),
        )
        self._insert_hermes_turn(cur, request)

    def _insert_hermes_turn(self, cur: psycopg.Cursor[Any], request: dict[str, Any]) -> None:
        cur.execute(
            sql.SQL("""
                INSERT INTO {} (request_id, engineer_case_id, request_payload, turn_type, status,
                    episode, conversation_version, hermes_session_id, created_at, updated_at)
                VALUES (%s,%s,%s,%s,'queued',%s,%s,%s,%s,%s)
                ON CONFLICT (request_id) DO NOTHING
            """).format(self._table("support_hermes_turn_requests")),
            (request["request_id"], request["engineer_case_id"], Json(request), request["turn_kind"],
             request["episode"], request["conversation_version"], request.get("hermes_session_id"),
             request["created_at"], request["created_at"]),
        )

    def get_hermes_case_binding(self, engineer_case_id: str) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT {} FROM {} WHERE engineer_case_id=%s").format(
                        sql.SQL(",").join(map(sql.Identifier, self._BINDING_FIELDS)),
                        self._table("support_hermes_case_bindings"),
                    ), (engineer_case_id,),
                )
                return _row_dict(cur.fetchone(), self._BINDING_FIELDS)
        return self._run_with_connection_retry("get_hermes_case_binding", operation)

    def get_hermes_case_ledger(self, engineer_case_id: str) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT {}, b.client_ticket_id FROM {} l JOIN {} b USING (engineer_case_id) WHERE l.engineer_case_id=%s").format(
                        sql.SQL(",").join(sql.SQL("l.{}" ).format(sql.Identifier(f)) for f in self._LEDGER_FIELDS),
                        self._table("support_hermes_case_ledgers"), self._table("support_hermes_case_bindings"),
                    ), (engineer_case_id,),
                )
                return _row_dict(cur.fetchone(), self._LEDGER_FIELDS + ("client_ticket_id",))
        return self._run_with_connection_retry("get_hermes_case_ledger", operation)

    def get_hermes_output(self, output_id: str) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT output_payload, accepted, rejection_reason FROM {} WHERE output_id=%s"
                    ).format(self._table("support_hermes_outputs")),
                    (output_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return {
                    **dict(row[0]),
                    "accepted": bool(row[1]),
                    "rejection_reason": row[2],
                }

        return self._run_with_connection_retry("get_hermes_output", operation)

    def get_hermes_rejection_receipt(self, output_id: str) -> dict[str, Any] | None:
        fields = (
            "receipt_id",
            "output_id",
            "request_id",
            "engineer_case_id",
            "reason",
            "created_at",
        )

        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT {} FROM {} WHERE output_id=%s").format(
                        sql.SQL(",").join(map(sql.Identifier, fields)),
                        self._table("support_hermes_rejection_receipts"),
                    ),
                    (output_id,),
                )
                return _row_dict(cur.fetchone(), fields)

        return self._run_with_connection_retry("get_hermes_rejection_receipt", operation)

    def list_hermes_turn_requests(self, engineer_case_id: str | None = None) -> list[dict[str, Any]]:
        def operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                where = sql.SQL("WHERE engineer_case_id=%s") if engineer_case_id else sql.SQL("")
                params = (engineer_case_id,) if engineer_case_id else ()
                cur.execute(sql.SQL("SELECT request_payload, status, owner_token, claimed_at, lease_expires_at, runtime_receipt, failure_code FROM {} {} ORDER BY created_at, request_id").format(
                    self._table("support_hermes_turn_requests"), where), params)
                return [
                    {**dict(row[0]), "status": row[1], "owner_token": row[2],
                     "claimed_at": _iso(row[3]) if row[3] else None,
                     "lease_expires_at": _iso(row[4]) if row[4] else None,
                     "runtime_receipt": dict(row[5]) if row[5] else None,
                     "failure_code": row[6]}
                    for row in cur.fetchall()
                ]
        return self._run_with_connection_retry("list_hermes_turn_requests", operation)

    def freeze_hermes_turn_request(
        self, request_id: str, *, payload: dict[str, Any]
    ) -> dict[str, Any]:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT request_payload,status FROM {} WHERE request_id=%s FOR UPDATE").format(
                        self._table("support_hermes_turn_requests")
                    ),
                    (request_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise HermesRepositoryConflict("unknown Hermes turn")
                current = dict(row[0])
                existing_channel = str(current.get("slack_channel_id") or "")
                existing_thread = str(current.get("slack_thread_ts") or "")
                if existing_channel or existing_thread:
                    if (
                        current.get("slack_channel_id") != payload.get("slack_channel_id")
                        or current.get("slack_thread_ts") != payload.get("slack_thread_ts")
                    ):
                        raise HermesRepositoryConflict("Hermes turn payload is already frozen")
                    return {**current, "status": row[1]}
                if str(row[1]) not in {"queued", "active"}:
                    raise HermesRepositoryConflict("Hermes turn cannot be frozen in its current state")
                cur.execute(
                    sql.SQL("UPDATE {} SET request_payload=%s WHERE request_id=%s").format(
                        self._table("support_hermes_turn_requests")
                    ),
                    (Json(payload), request_id),
                )
                return {**payload, "status": str(row[1])}

        return self._run_with_connection_retry("freeze_hermes_turn_request", operation)

    def mark_hermes_turn_awaiting_result(
        self, request_id: str, *, owner_token: str, receipt: dict[str, Any], accepted_at: str
    ) -> dict[str, Any]:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET status='awaiting_result',runtime_receipt=%s,failure_code=NULL,"
                        "lease_expires_at=NULL,updated_at=%s WHERE request_id=%s AND status='active' "
                        "AND owner_token=%s RETURNING request_payload"
                    ).format(self._table("support_hermes_turn_requests")),
                    (Json(receipt), accepted_at, request_id, owner_token),
                )
                row = cur.fetchone()
                if row is None:
                    raise HermesRepositoryConflict("stale Hermes turn delivery")
                return {**dict(row[0]), "status": "awaiting_result", "runtime_receipt": receipt}

        return self._run_with_connection_retry("mark_hermes_turn_awaiting_result", operation)

    def record_hermes_turn_delivery_failure(
        self, request_id: str, *, owner_token: str, failure_code: str,
        retryable: bool, failed_at: str,
    ) -> dict[str, Any]:
        target_status = "queued" if retryable else "failed"

        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET status=%s,owner_token=NULL,claimed_at=NULL,lease_expires_at=NULL,"
                        "failure_code=%s,updated_at=%s WHERE request_id=%s AND status='active' "
                        "AND owner_token=%s RETURNING request_payload"
                    ).format(self._table("support_hermes_turn_requests")),
                    (target_status, failure_code, failed_at, request_id, owner_token),
                )
                row = cur.fetchone()
                if row is None:
                    raise HermesRepositoryConflict("stale Hermes turn delivery")
                return {**dict(row[0]), "status": target_status, "failure_code": failure_code}

        return self._run_with_connection_retry("record_hermes_turn_delivery_failure", operation)

    def claim_next_hermes_turn(self, *, owner_token: str, claimed_at: str, lease_expires_at: str) -> dict[str, Any] | None:
        return self._claim_hermes_turn(None, owner_token, claimed_at, lease_expires_at)

    def claim_hermes_turn(self, *, request_id: str, owner_token: str, claimed_at: str, lease_expires_at: str) -> dict[str, Any] | None:
        return self._claim_hermes_turn(request_id, owner_token, claimed_at, lease_expires_at)

    def _claim_hermes_turn(self, request_id: str | None, owner_token: str, claimed_at: str, lease_expires_at: str) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                where = sql.SQL("AND t.request_id=%s") if request_id else sql.SQL("")
                params: tuple[Any, ...] = (claimed_at,) + ((request_id,) if request_id else ())
                cur.execute(
                    sql.SQL("""
                        SELECT t.request_id, t.request_payload FROM {} t
                        JOIN {} b USING (engineer_case_id)
                        WHERE (t.status='queued' OR (t.status='active' AND t.lease_expires_at <= %s)) {}
                          AND NOT EXISTS (
                              SELECT 1 FROM {} active
                              WHERE active.engineer_case_id=t.engineer_case_id
                                AND active.status='active'
                                AND active.lease_expires_at > %s
                                AND active.request_id<>t.request_id
                          )
                        ORDER BY t.created_at, t.request_id FOR UPDATE OF b, t SKIP LOCKED LIMIT 1
                    """).format(self._table("support_hermes_turn_requests"), self._table("support_hermes_case_bindings"), where, self._table("support_hermes_turn_requests")),
                    params + (claimed_at,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                cur.execute(
                    sql.SQL("UPDATE {} SET status='active', owner_token=%s, claimed_at=%s, lease_expires_at=%s, updated_at=%s WHERE request_id=%s").format(self._table("support_hermes_turn_requests")),
                    (owner_token, claimed_at, lease_expires_at, claimed_at, row[0]),
                )
                return {**dict(row[1]), "status": "active", "owner_token": owner_token,
                        "claimed_at": claimed_at, "lease_expires_at": lease_expires_at}
        return self._run_with_connection_retry("claim_hermes_turn", operation)

    def apply_hermes_output(self, output: dict[str, Any], slack_event: dict[str, Any]) -> dict[str, Any]:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT output_id, accepted FROM {} WHERE output_id=%s FOR UPDATE").format(self._table("support_hermes_outputs")), (output["output_id"],))
                existing = cur.fetchone()
                if existing and existing[1]:
                    return {"status": "idempotent", "output_id": output["output_id"]}
                cur.execute(sql.SQL("SELECT request_payload, status FROM {} WHERE request_id=%s FOR UPDATE").format(self._table("support_hermes_turn_requests")), (output["request_id"],))
                request_row = cur.fetchone()
                cur.execute(sql.SQL("SELECT {} FROM {} WHERE engineer_case_id=%s FOR UPDATE").format(
                    sql.SQL(",").join(map(sql.Identifier, self._BINDING_FIELDS)), self._table("support_hermes_case_bindings")),
                    (output["engineer_case_id"],))
                binding = _row_dict(cur.fetchone(), self._BINDING_FIELDS)
                reason = None
                request = dict(request_row[0]) if request_row else None
                request_status = str(request_row[1]) if request_row else ""
                if request is None or binding is None:
                    reason = "unknown_request_or_case"
                elif (
                    request["engineer_case_id"] != output["engineer_case_id"]
                    or request["investigation_id"] != output["investigation_id"]
                    or request["hermes_conversation_key"] != output["hermes_conversation_key"]
                    or int(binding["episode"]) != int(output["episode"])
                    or int(binding["conversation_version"]) != int(output["conversation_version"])
                    or int(request["episode"]) != int(output["episode"])
                    or int(request["conversation_version"]) != int(output["conversation_version"])
                    or request.get("hermes_session_id") != binding.get("hermes_session_id")
                    or int(request["session_binding_version"]) != int(binding["binding_version"])
                    or request_status not in {"active", "awaiting_result"}
                ):
                    reason = "stale_lineage"
                if reason:
                    cur.execute(sql.SQL("INSERT INTO {} (receipt_id, output_id, request_id, engineer_case_id, reason, created_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (receipt_id) DO NOTHING").format(self._table("support_hermes_rejection_receipts")),
                                (f"hermes-rejection:{output['output_id']}", output["output_id"], output["request_id"], output["engineer_case_id"], reason, output["created_at"]))
                    return {"status": "rejected", "output_id": output["output_id"], "reason": reason}
                cur.execute(sql.SQL("UPDATE {} SET status='superseded', updated_at=%s WHERE engineer_case_id=%s AND status='frozen'").format(self._table("support_hermes_summary_snapshots")), (output["created_at"], output["engineer_case_id"]))
                cur.execute(sql.SQL("""
                    UPDATE {} SET draft_customer_reply='', final_confirmation_requested_at=NULL,
                        engineer_agent_state=(COALESCE(engineer_agent_state,'{{}}'::jsonb)
                            - 'hermes_summary_snapshot_id' - 'hermes_summary_guardrail'
                            - 'guided_reply_generation' - 'reply_readiness'
                            - 'active_guardrail_final' - 'guardrail_final_id'
                            - 'guardrail_final_version' - 'guardrail_final_decision'
                            - 'final_approval_required' - 'final_approved_at')
                            || jsonb_build_object('conversation_version', %s, 'draft_version', 0, 'round_state', 'active'),
                        updated_at=%s WHERE engineer_case_id=%s
                """).format(self._table("support_engineer_cases")),
                    (output["conversation_version"], output["created_at"], output["engineer_case_id"]))
                delta = output.get("ledger_delta") or {}
                cur.execute(sql.SQL("""
                    UPDATE {} SET problem_description=COALESCE(%s,problem_description),
                        investigation_process=COALESCE(%s,investigation_process),
                        misjudgment_corrections=COALESCE(%s,misjudgment_corrections),
                        current_conclusion_next_steps=COALESCE(%s,current_conclusion_next_steps),
                        "references"=COALESCE(%s,"references"), revision=revision+1,
                        episode=%s, updated_at=%s WHERE engineer_case_id=%s RETURNING revision
                """).format(self._table("support_hermes_case_ledgers")),
                    (delta.get("problem_description"), delta.get("investigation_process"), delta.get("misjudgment_corrections"),
                     delta.get("current_conclusion_next_steps"), delta.get("references"), output["episode"], output["created_at"], output["engineer_case_id"]))
                revision = int(cur.fetchone()[0])
                cur.execute(sql.SQL("INSERT INTO {} (output_id, request_id, engineer_case_id, output_payload, accepted, created_at) VALUES (%s,%s,%s,%s,TRUE,%s)").format(self._table("support_hermes_outputs")),
                            (output["output_id"], output["request_id"], output["engineer_case_id"], Json(output), output["created_at"]))
                cur.execute(sql.SQL("UPDATE {} SET hermes_session_id=%s, current_output_id=%s, current_ledger_revision=%s, binding_version=binding_version+1, updated_at=%s WHERE engineer_case_id=%s").format(self._table("support_hermes_case_bindings")),
                            (output["hermes_session_id"], output["output_id"], revision, output["created_at"], output["engineer_case_id"]))
                cur.execute(sql.SQL("UPDATE {} SET status='completed', updated_at=%s WHERE request_id=%s").format(self._table("support_hermes_turn_requests")), (output["created_at"], output["request_id"]))
                cur.execute(sql.SQL("INSERT INTO {} (event_id, engineer_case_id, event_type, payload, status, created_at, updated_at) VALUES (%s,%s,%s,%s,'queued',%s,%s) ON CONFLICT (event_id) DO NOTHING").format(self._table("support_engineer_slack_events")),
                            (slack_event["event_id"], output["engineer_case_id"], slack_event["event_type"], Json(slack_event), output["created_at"], output["created_at"]))
                if cur.rowcount != 1:
                    raise HermesRepositoryConflict("Hermes Slack outbox conflict")
                return {"status": "accepted", "output_id": output["output_id"], "ledger_revision": revision}
        return self._run_with_connection_retry("apply_hermes_output", operation)

    def freeze_hermes_summary(self, engineer_case_id: str, *, snapshot_id: str, frozen_at: str) -> dict[str, Any]:
        fields = ("snapshot_id", "engineer_case_id", "episode", "conversation_version", "output_id",
                  "ledger_revision", "summary", "status", "guardrail_decision", "guardrail_reason",
                  "created_at", "updated_at")
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT episode, conversation_version, current_output_id, current_ledger_revision FROM {} WHERE engineer_case_id=%s FOR UPDATE").format(self._table("support_hermes_case_bindings")), (engineer_case_id,))
                binding = cur.fetchone()
                if binding is None or not binding[2]:
                    raise HermesRepositoryConflict("Hermes output is not available")
                cur.execute(sql.SQL("SELECT output_payload->>'text' FROM {} WHERE output_id=%s AND accepted=TRUE").format(self._table("support_hermes_outputs")), (binding[2],))
                output = cur.fetchone()
                if output is None:
                    raise HermesRepositoryConflict("Hermes output is not available")
                cur.execute(sql.SQL("INSERT INTO {} (snapshot_id, engineer_case_id, episode, conversation_version, output_id, ledger_revision, summary, status, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'frozen',%s,%s) ON CONFLICT (snapshot_id) DO NOTHING").format(self._table("support_hermes_summary_snapshots")),
                            (snapshot_id, engineer_case_id, binding[0], binding[1], binding[2], binding[3], output[0], frozen_at, frozen_at))
                cur.execute(sql.SQL("SELECT {} FROM {} WHERE snapshot_id=%s").format(sql.SQL(",").join(map(sql.Identifier, fields)), self._table("support_hermes_summary_snapshots")), (snapshot_id,))
                return _row_dict(cur.fetchone(), fields) or {}
        return self._run_with_connection_retry("freeze_hermes_summary", operation)

    def save_hermes_summary_guardrail(self, *, snapshot_id: str, expected_episode: int,
                                      expected_conversation_version: int, expected_output_id: str,
                                      expected_ledger_revision: int, decision: str, reason: str,
                                      decided_at: str) -> dict[str, Any]:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT s.engineer_case_id, s.episode, s.conversation_version, s.output_id, s.ledger_revision, s.status, b.episode, b.conversation_version, b.current_output_id, b.current_ledger_revision FROM {} s JOIN {} b USING (engineer_case_id) WHERE s.snapshot_id=%s FOR UPDATE OF s,b").format(self._table("support_hermes_summary_snapshots"), self._table("support_hermes_case_bindings")), (snapshot_id,))
                row = cur.fetchone()
                if row is None:
                    raise HermesRepositoryConflict("unknown snapshot")
                if not (row[5] == "frozen" and row[1] == row[6] == expected_episode
                        and row[2] == row[7] == expected_conversation_version
                        and row[3] == row[8] == expected_output_id
                        and row[4] == row[9] == expected_ledger_revision):
                    raise HermesRepositoryConflict("stale summary snapshot")
                cur.execute(sql.SQL("UPDATE {} SET guardrail_decision=%s, guardrail_reason=%s, decided_at=%s, updated_at=%s WHERE snapshot_id=%s").format(self._table("support_hermes_summary_snapshots")), (decision, reason, decided_at, decided_at, snapshot_id))
                return {"snapshot_id": snapshot_id, "engineer_case_id": row[0], "decision": decision,
                        "reason": reason, "persona_required": True, "final_approval_required": True}
        return self._run_with_connection_retry("save_hermes_summary_guardrail", operation)

    def queue_hermes_feedback_turn(self, request: dict[str, Any]) -> dict[str, Any]:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT episode, conversation_version, binding_version FROM {} WHERE engineer_case_id=%s FOR UPDATE").format(self._table("support_hermes_case_bindings")), (request["engineer_case_id"],))
                binding = cur.fetchone()
                if binding is None:
                    raise HermesRepositoryConflict("unknown Hermes Case")
                if int(binding[0]) != int(request["episode"]) or int(binding[1]) + 1 != int(request["conversation_version"]):
                    raise HermesRepositoryConflict("stale conversation version")
                if int(binding[2]) + 1 != int(request["session_binding_version"]):
                    raise HermesRepositoryConflict("stale session binding version")
                cur.execute(sql.SQL("UPDATE {} SET conversation_version=%s, current_output_id=NULL, binding_version=binding_version+1, updated_at=%s WHERE engineer_case_id=%s").format(self._table("support_hermes_case_bindings")), (request["conversation_version"], request["created_at"], request["engineer_case_id"]))
                cur.execute(sql.SQL("UPDATE {} SET status='superseded', updated_at=%s WHERE engineer_case_id=%s AND status='frozen'").format(self._table("support_hermes_summary_snapshots")), (request["created_at"], request["engineer_case_id"]))
                cur.execute(sql.SQL("""
                    UPDATE {} SET draft_customer_reply='', final_confirmation_requested_at=NULL,
                        engineer_agent_state=(COALESCE(engineer_agent_state,'{{}}'::jsonb)
                            - 'hermes_summary_snapshot_id' - 'hermes_summary_guardrail'
                            - 'guided_reply_generation' - 'reply_readiness'
                            - 'active_guardrail_final' - 'guardrail_final_id'
                            - 'guardrail_final_version' - 'guardrail_final_decision'
                            - 'final_approval_required' - 'final_approved_at')
                            || jsonb_build_object('conversation_version', %s, 'draft_version', 0, 'round_state', 'active'),
                        updated_at=%s WHERE engineer_case_id=%s
                """).format(self._table("support_engineer_cases")),
                    (request["conversation_version"], request["created_at"], request["engineer_case_id"]))
                self._insert_hermes_turn(cur, request)
                return copy.deepcopy(request)
        return self._run_with_connection_retry("queue_hermes_feedback_turn", operation)

    def invalidate_hermes_reply_chain(self, engineer_case_id: str, *, invalidated_at: str) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("UPDATE {} SET conversation_version=conversation_version+1, current_output_id=NULL, binding_version=binding_version+1, updated_at=%s WHERE engineer_case_id=%s RETURNING conversation_version").format(self._table("support_hermes_case_bindings")), (invalidated_at, engineer_case_id))
                row = cur.fetchone()
                if row is None:
                    return None
                cur.execute(sql.SQL("UPDATE {} SET status='superseded', updated_at=%s WHERE engineer_case_id=%s AND status='frozen'").format(self._table("support_hermes_summary_snapshots")), (invalidated_at, engineer_case_id))
                return {"engineer_case_id": engineer_case_id, "conversation_version": int(row[0])}
        return self._run_with_connection_retry("invalidate_hermes_reply_chain", operation)

    def record_hermes_authority_event(self, event: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT episode, conversation_version, binding_version FROM {} "
                        "WHERE engineer_case_id=%s FOR UPDATE"
                    ).format(self._table("support_hermes_case_bindings")),
                    (event["engineer_case_id"],),
                )
                binding = cur.fetchone()
                if (
                    binding is None
                    or int(binding[0]) != int(event["episode"])
                    or int(binding[1]) != int(event["conversation_version"])
                    or int(binding[2]) != int(request["session_binding_version"])
                ):
                    raise HermesRepositoryConflict("stale Hermes authority event")
                cur.execute(
                    sql.SQL(
                        "SELECT event_payload FROM {} WHERE authority_event_id=%s"
                    ).format(self._table("support_hermes_human_authority_events")),
                    (event["authority_event_id"],),
                )
                existing = cur.fetchone()
                if existing is not None:
                    return {**dict(existing[0]), "idempotent": True}
                close_review_id = None
                if event["action"] == "accept_and_finish":
                    close_review_id = str(event["target_output_id"])
                    cur.execute(
                        sql.SQL(
                            "SELECT episode, ledger_revision, review_payload, status FROM {} "
                            "WHERE review_id=%s FOR UPDATE"
                        ).format(self._table("support_hermes_close_reviews")),
                        (close_review_id,),
                    )
                    review = cur.fetchone()
                    actual_digest = hashlib.sha256(
                        json.dumps(
                            dict(review[2]) if review else {},
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    if (
                        review is None
                        or str(review[3]) != "awaiting_closed"
                        or int(review[0]) != int(binding[0])
                        or int(review[1]) != int(event["target_version"])
                        or not hmac.compare_digest(str(event["target_digest"]), actual_digest)
                    ):
                        raise HermesRepositoryConflict("close review is stale")
                cur.execute(sql.SQL("INSERT INTO {} (authority_event_id, engineer_case_id, event_payload, episode, conversation_version, action, actor_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (authority_event_id) DO NOTHING RETURNING authority_event_id").format(self._table("support_hermes_human_authority_events")),
                            (event["authority_event_id"], event["engineer_case_id"], Json(event), event["episode"], event["conversation_version"], event["action"], event["actor_id"], event["created_at"]))
                created = cur.fetchone() is not None
                self._insert_hermes_turn(cur, request)
                if close_review_id is not None:
                    cur.execute(
                        sql.SQL(
                            "UPDATE {} SET status='approved', "
                            "review_payload=review_payload || jsonb_build_object('reviewer_id',%s::text), "
                            "updated_at=%s WHERE review_id=%s"
                        ).format(self._table("support_hermes_close_reviews")),
                        (event["actor_id"], event["created_at"], close_review_id),
                    )
                return {**copy.deepcopy(event), "idempotent": not created}
        return self._run_with_connection_retry("record_hermes_authority_event", operation)

    def list_hermes_authority_events(self, engineer_case_id: str) -> list[dict[str, Any]]:
        def operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT event_payload FROM {} WHERE engineer_case_id=%s ORDER BY created_at, authority_event_id").format(self._table("support_hermes_human_authority_events")), (engineer_case_id,))
                return [dict(row[0]) for row in cur.fetchall()]
        return self._run_with_connection_retry("list_hermes_authority_events", operation)

    def record_hermes_case_solved(self, engineer_case_id: str, *, review_id: str, now_value: str) -> dict[str, Any]:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT b.episode, b.current_ledger_revision, b.current_output_id, l.problem_description, l.investigation_process, l.misjudgment_corrections, l.current_conclusion_next_steps, l.\"references\" FROM {} b JOIN {} l USING (engineer_case_id) WHERE b.engineer_case_id=%s FOR UPDATE OF b,l").format(self._table("support_hermes_case_bindings"), self._table("support_hermes_case_ledgers")), (engineer_case_id,))
                row = cur.fetchone()
                if row is None or not row[2]:
                    raise HermesRepositoryConflict("current Hermes output is required")
                payload = dict(zip(("problem_description", "investigation_process", "misjudgment_corrections", "current_conclusion_next_steps", "references"), row[3:]))
                cur.execute(sql.SQL("INSERT INTO {} (review_id, engineer_case_id, episode, ledger_revision, review_payload, status, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,'awaiting_closed',%s,%s) ON CONFLICT (review_id) DO NOTHING").format(self._table("support_hermes_close_reviews")), (review_id, engineer_case_id, row[0], row[1], Json(payload), now_value, now_value))
                cur.execute(sql.SQL("UPDATE {} SET status='awaiting_closed', updated_at=%s WHERE engineer_case_id=%s").format(self._table("support_hermes_case_bindings")), (now_value, engineer_case_id))
                cur.execute(sql.SQL("UPDATE {} SET status='awaiting_closed', updated_at=%s WHERE engineer_case_id=%s").format(self._table("support_hermes_case_ledgers")), (now_value, engineer_case_id))
                return {"review_id": review_id, "engineer_case_id": engineer_case_id, "episode": row[0], "ledger_revision": row[1], "review_payload": payload, "status": "awaiting_closed", "created_at": now_value, "updated_at": now_value}
        return self._run_with_connection_retry("record_hermes_case_solved", operation)

    def get_hermes_close_review(self, review_id: str) -> dict[str, Any] | None:
        fields = ("review_id", "engineer_case_id", "episode", "ledger_revision", "review_payload", "status", "created_at", "updated_at")
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT {} FROM {} WHERE review_id=%s").format(sql.SQL(",").join(map(sql.Identifier, fields)), self._table("support_hermes_close_reviews")), (review_id,))
                return _row_dict(cur.fetchone(), fields)
        return self._run_with_connection_retry("get_hermes_close_review", operation)

    def approve_hermes_close_review(self, review_id: str, *, reviewer_id: str, now_value: str) -> dict[str, Any]:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("UPDATE {} SET status='approved', review_payload=review_payload || jsonb_build_object('reviewer_id',%s::text), updated_at=%s WHERE review_id=%s AND status='awaiting_closed' RETURNING engineer_case_id, episode, ledger_revision, review_payload").format(self._table("support_hermes_close_reviews")), (reviewer_id, now_value, review_id))
                row = cur.fetchone()
                if row is None:
                    raise HermesRepositoryConflict("close review is stale")
                return {"review_id": review_id, "engineer_case_id": row[0], "episode": row[1], "ledger_revision": row[2], "review_payload": row[3], "status": "approved", "updated_at": now_value}
        return self._run_with_connection_retry("approve_hermes_close_review", operation)

    def approve_current_hermes_close_review(self, engineer_case_id: str, *, reviewer_id: str, now_value: str) -> dict[str, Any]:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT r.review_id FROM {} r JOIN {} b USING (engineer_case_id) WHERE r.engineer_case_id=%s AND r.status='awaiting_closed' AND r.episode=b.episode AND r.ledger_revision=b.current_ledger_revision ORDER BY r.created_at DESC LIMIT 1 FOR UPDATE OF r,b").format(self._table("support_hermes_close_reviews"), self._table("support_hermes_case_bindings")), (engineer_case_id,))
                row = cur.fetchone()
                if row is None:
                    raise HermesRepositoryConflict("close review is stale")
                review_id = str(row[0])
                cur.execute(sql.SQL("UPDATE {} SET status='approved', review_payload=review_payload || jsonb_build_object('reviewer_id',%s::text), updated_at=%s WHERE review_id=%s RETURNING episode, ledger_revision, review_payload").format(self._table("support_hermes_close_reviews")), (reviewer_id, now_value, review_id))
                updated = cur.fetchone()
                return {"review_id": review_id, "engineer_case_id": engineer_case_id, "episode": updated[0], "ledger_revision": updated[1], "review_payload": updated[2], "status": "approved", "updated_at": now_value}
        return self._run_with_connection_retry("approve_current_hermes_close_review", operation)

    def reopen_hermes_case(self, request: dict[str, Any]) -> dict[str, Any]:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT episode, conversation_version, binding_version FROM {} WHERE engineer_case_id=%s FOR UPDATE").format(self._table("support_hermes_case_bindings")), (request["engineer_case_id"],))
                row = cur.fetchone()
                if row is None or int(request["episode"]) != int(row[0]) + 1:
                    raise HermesRepositoryConflict("stale reopen episode")
                if int(request["session_binding_version"]) != int(row[2]) + 1:
                    raise HermesRepositoryConflict("stale session binding version")
                cur.execute(sql.SQL("UPDATE {} SET episode=%s, conversation_version=%s, current_output_id=NULL, status='active', binding_version=binding_version+1, updated_at=%s WHERE engineer_case_id=%s").format(self._table("support_hermes_case_bindings")), (request["episode"], request["conversation_version"], request["created_at"], request["engineer_case_id"]))
                cur.execute(sql.SQL("UPDATE {} SET episode=%s, status='active', updated_at=%s WHERE engineer_case_id=%s").format(self._table("support_hermes_case_ledgers")), (request["episode"], request["created_at"], request["engineer_case_id"]))
                cur.execute(sql.SQL("UPDATE {} SET status='superseded', updated_at=%s WHERE engineer_case_id=%s AND status='frozen'").format(self._table("support_hermes_summary_snapshots")), (request["created_at"], request["engineer_case_id"]))
                cur.execute(sql.SQL("UPDATE {} SET status='invalidated', updated_at=%s WHERE engineer_case_id=%s AND status <> 'invalidated'").format(self._table("support_hermes_close_reviews")), (request["created_at"], request["engineer_case_id"]))
                cur.execute(sql.SQL("UPDATE {} SET status='invalidated', updated_at=%s WHERE engineer_case_id=%s AND status <> 'invalidated'").format(self._table("support_hermes_case_promotions")), (request["created_at"], request["engineer_case_id"]))
                self._insert_hermes_turn(cur, request)
                return copy.deepcopy(request)
        return self._run_with_connection_retry("reopen_hermes_case", operation)

    def close_hermes_case(self, promotion: dict[str, Any], *, now_value: str) -> dict[str, Any]:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT episode, current_ledger_revision FROM {} WHERE engineer_case_id=%s FOR UPDATE").format(self._table("support_hermes_case_bindings")), (promotion["engineer_case_id"],))
                binding = cur.fetchone()
                if binding is None or int(binding[0]) != int(promotion["episode"]) or int(binding[1]) != int(promotion["ledger_revision"]):
                    raise HermesRepositoryConflict("close review is stale")
                cur.execute(sql.SQL("SELECT 1 FROM {} WHERE engineer_case_id=%s AND episode=%s AND ledger_revision=%s AND status='approved'").format(self._table("support_hermes_close_reviews")), (promotion["engineer_case_id"], promotion["episode"], promotion["ledger_revision"]))
                if cur.fetchone() is None:
                    raise HermesRepositoryConflict("approved current close review is required")
                cur.execute(sql.SQL("SELECT 1 FROM {} WHERE engineer_case_id=%s AND episode=%s AND ledger_revision=%s AND status='frozen' AND guardrail_decision='passed'").format(self._table("support_hermes_summary_snapshots")), (promotion["engineer_case_id"], promotion["episode"], promotion["ledger_revision"]))
                if cur.fetchone() is None:
                    raise HermesRepositoryConflict("current summary guardrail is required")
                cur.execute(sql.SQL("INSERT INTO {} (promotion_id, engineer_case_id, episode, ledger_revision, promotion_payload, status, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,'awaiting_transport',%s,%s) ON CONFLICT (promotion_id) DO NOTHING").format(self._table("support_hermes_case_promotions")), (promotion["promotion_id"], promotion["engineer_case_id"], promotion["episode"], promotion["ledger_revision"], Json(promotion), now_value, now_value))
                cur.execute(sql.SQL("UPDATE {} SET status='closed', updated_at=%s WHERE engineer_case_id=%s").format(self._table("support_hermes_case_bindings")), (now_value, promotion["engineer_case_id"]))
                cur.execute(sql.SQL("UPDATE {} SET status='closed', updated_at=%s WHERE engineer_case_id=%s").format(self._table("support_hermes_case_ledgers")), (now_value, promotion["engineer_case_id"]))
                cur.execute(sql.SQL("UPDATE {} SET status='cancelled', updated_at=%s WHERE engineer_case_id=%s AND status IN ('queued','active','awaiting_result')").format(self._table("support_hermes_turn_requests")), (now_value, promotion["engineer_case_id"]))
                return {**copy.deepcopy(promotion), "updated_at": now_value}
        return self._run_with_connection_retry("close_hermes_case", operation)

    def list_hermes_promotions(self) -> list[dict[str, Any]]:
        def operation(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(sql.SQL(
                    "SELECT promotion_payload,status,owner_token,claimed_at,lease_expires_at,"
                    "runtime_receipt,failure_code FROM {} ORDER BY created_at,promotion_id"
                ).format(self._table("support_hermes_case_promotions")))
                return [{
                    **dict(row[0]), "status": row[1], "owner_token": row[2],
                    "claimed_at": _iso(row[3]) if row[3] else None,
                    "lease_expires_at": _iso(row[4]) if row[4] else None,
                    "runtime_receipt": dict(row[5]) if row[5] else None,
                    "failure_code": row[6],
                } for row in cur.fetchall()]
        return self._run_with_connection_retry("list_hermes_promotions", operation)

    def claim_hermes_promotion(
        self, promotion_id: str, *, owner_token: str, claimed_at: str, lease_expires_at: str
    ) -> dict[str, Any] | None:
        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL(
                    """UPDATE {} SET status='active',owner_token=%s,claimed_at=%s,
                    lease_expires_at=%s,updated_at=%s WHERE promotion_id=%s AND
                    (status='awaiting_transport' OR (status='active' AND lease_expires_at<=%s))
                    RETURNING promotion_payload"""
                ).format(self._table("support_hermes_case_promotions")), (
                    owner_token, claimed_at, lease_expires_at, claimed_at, promotion_id, claimed_at,
                ))
                row = cur.fetchone()
                return None if row is None else {
                    **dict(row[0]), "status": "active", "owner_token": owner_token,
                    "claimed_at": claimed_at, "lease_expires_at": lease_expires_at,
                }
        return self._run_with_connection_retry("claim_hermes_promotion", operation)

    def complete_hermes_promotion_delivery(
        self, promotion_id: str, *, owner_token: str, status: str,
        receipt: dict[str, Any] | None, failure_code: str | None, completed_at: str,
    ) -> dict[str, Any]:
        if status not in {"accepted", "failed", "outcome_unknown"}:
            raise ValueError("invalid Hermes promotion delivery status")

        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL(
                    """UPDATE {} SET status=%s,runtime_receipt=%s,failure_code=%s,
                    lease_expires_at=NULL,updated_at=%s WHERE promotion_id=%s AND status='active'
                    AND owner_token=%s RETURNING promotion_payload"""
                ).format(self._table("support_hermes_case_promotions")), (
                    status, Json(receipt) if receipt is not None else None, failure_code,
                    completed_at, promotion_id, owner_token,
                ))
                row = cur.fetchone()
                if row is None:
                    raise HermesRepositoryConflict("stale Hermes promotion delivery")
                return {**dict(row[0]), "status": status,
                        "runtime_receipt": receipt, "failure_code": failure_code}
        return self._run_with_connection_retry("complete_hermes_promotion_delivery", operation)
