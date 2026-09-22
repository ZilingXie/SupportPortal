"""Schema ↔ normalizer alignment (p2-148 v3, 13650 round-3).

Every classification shape the plugin JSON Schema ACCEPTS must be a shape the
server-side normalizer (normalize_hermes_route_classification) can consume and
map to the documented direction; the one shape the matrix marks as rejected
(conversation missing action_confidence) must be blocked BY THE SCHEMA, before
any HTTP request or server call could run. When the normalizer itself rejects
an otherwise schema-valid shape (backend_operation=null degrades, invalid
billing pair), the rejection/degradation must leave NO turn, binding, or
Account case write behind.

The plugin schema is loaded from the REAL hermes-deploy worktree (the same
resolution the cross-repo integration test uses); jsonschema is a hard test
dependency in this repo's .venv, so a missing plugin file — never a missing
jsonschema — is the only reason to skip.
"""
from __future__ import annotations

import copy
import importlib.util
import os
from pathlib import Path

import jsonschema
import pytest

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.automation_ecs_store import InMemoryAutomationEcsStore, JobKind
from backend.services.automation_hermes_tools import HermesToolError, tool_record_direction

import backend.tests.test_hermes_zendesk_agent as harness

_PLUGIN_CANDIDATES = [
    Path(os.environ["HERMES_DEPLOY_PLUGIN"]) if os.environ.get("HERMES_DEPLOY_PLUGIN") else None,
    Path.home()
    / "Desktop/agentRelay/hermes-deploy/.worktrees/agent-tools-classification/build/supportportal_agent_tools/__init__.py",
    Path.home() / "Desktop/agentRelay/hermes-deploy/build/supportportal_agent_tools/__init__.py",
]
_PLUGIN_PATH = next((p for p in _PLUGIN_CANDIDATES if p and p.exists()), None)

pytestmark = pytest.mark.skipif(
    _PLUGIN_PATH is None, reason="hermes-deploy plugin not available on this machine"
)


def _plugin_schema():
    spec = importlib.util.spec_from_file_location("plugin_alignment_test", _PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [t for t in module._TOOLS if t[0] == "support_record_direction"][0][2]


_SCHEMA = None


def _schema():
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = _plugin_schema()
    return _SCHEMA


def _schema_accepts(tool_args) -> bool:
    try:
        jsonschema.validate(tool_args, _schema())
        return True
    except jsonschema.ValidationError:
        return False


def _seed_case():
    """A pending route turn on a real in-memory store + account case mirror."""
    store = InMemoryAutomationEcsStore(harness._settings())
    store.migrate()
    repository = InMemoryTicketRepository()
    repository.initialize()
    repository.save_ticket(
        {
            "ticket_id": "123",
            "customer_id": "cx@example.com",
            "requester": "cx@example.com",
            "subject": "Enable Media Relay",
            "status": "open",
            "created_at": "2026-09-08T10:00:00Z",
            "updated_at": "2026-09-08T10:00:00Z",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable media relay for 0123456789abcdef0123456789abcdef.",
                    "created_at": "2026-09-08T10:00:00Z",
                }
            ],
        },
        new_messages=[],
    )
    repository.save_account_case(
        {
            "account_case_id": "AC-123",
            "billing_ticket_id": "AC-123",
            "client_ticket_id": "123",
            "zendesk_ticket_id": "123",
            "processing_profile": "preproduction",
            "automation_status": "pending",
            "created_at": "2026-09-08T10:00:00Z",
            "updated_at": "2026-09-08T10:00:00Z",
        }
    )
    event = harness._event()
    store.accept_intake(event, harness._settings().provenance())
    job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
    handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
    return store, repository, handoff["turn_id"]


def _args(direction, route, classification):
    args = {"turn_id": "t1", "direction": direction, "reason": "matrix", "classification": classification}
    if route is not None:
        args["route"] = route
    return args


# --- Classification shapes for the matrix (all schema-valid unless noted) ---

_CONVERSATION_OK = {
    "intent_class": "conversation",
    "conversation_action": "resolve",
    "agora_route": None,
    "intent_confidence": 0.97,
    "agora_confidence": 0.97,
    "action_confidence": 0.95,
    "account_billing_subcategory": None,
    "backend_operation_subcategory": None,
    "backend_operation": None,
    "additional_intents": [],
    "confidence": 0.97,
    "reason_code": "conversation_resolution",
}

_CONVERSATION_MISSING_ACTION_CONFIDENCE = {
    k: v for k, v in _CONVERSATION_OK.items() if k != "action_confidence"
}

_UNCERTAIN_NULL_ROUTE = {
    "intent_class": "uncertain",
    "conversation_action": None,
    "agora_route": None,
    "intent_confidence": 0.5,
    "agora_confidence": 0.5,
    "action_confidence": None,
    "account_billing_subcategory": None,
    "backend_operation_subcategory": None,
    "backend_operation": None,
    "additional_intents": [],
    "confidence": 0.5,
    "reason_code": "out_of_scope_or_unknown",
}

_TECHNICAL = {
    "intent_class": "agora",
    "conversation_action": None,
    "agora_route": "technical",
    "intent_confidence": 0.9,
    "agora_confidence": 0.9,
    "action_confidence": None,
    "account_billing_subcategory": None,
    "backend_operation_subcategory": None,
    "backend_operation": None,
    "additional_intents": [],
    "confidence": 0.9,
    "reason_code": "technical_request",
}

_MEDIA_RELAY = {
    "intent_class": "agora",
    "conversation_action": None,
    "agora_route": "backend_operation",
    "intent_confidence": 0.95,
    "agora_confidence": 0.95,
    "action_confidence": None,
    "account_billing_subcategory": None,
    "backend_operation_subcategory": "enablement",
    "backend_operation": {
        "action": "enable",
        "target": "media_relay",
        "evidence": "Please enable media relay for 0123456789abcdef0123456789abcdef.",
    },
    "additional_intents": [],
    "confidence": 0.95,
    "reason_code": "registered_enablement",
}

# Schema-valid (backend_operation=null is an allowed shape) but the normalizer
# degrades it to human review for insufficient evidence.
_BACKEND_OP_MISSING_EVIDENCE = {
    "intent_class": "agora",
    "conversation_action": None,
    "agora_route": "backend_operation",
    "intent_confidence": 0.95,
    "agora_confidence": 0.95,
    "action_confidence": None,
    "account_billing_subcategory": None,
    "backend_operation_subcategory": "enablement",
    "backend_operation": None,
    "additional_intents": [],
    "confidence": 0.95,
    "reason_code": "registered_enablement",
}

_BILLING_INVALID_SUBTYPE = {
    "intent_class": "agora",
    "conversation_action": None,
    "agora_route": "account_billing",
    "intent_confidence": 0.95,
    "agora_confidence": 0.95,
    "action_confidence": None,
    "account_billing_subcategory": "fraud_account",
    "backend_operation_subcategory": None,
    "backend_operation": None,
    "additional_intents": [],
    "confidence": 0.95,
    "reason_code": "not_a_billing_reason",
}


def _record(direction, route, classification):
    store, repository, turn_id = _seed_case()
    args = _args(direction, route, classification)
    # The schema is the first gate: any shape we forward to the server must pass.
    assert _schema_accepts({**args, "turn_id": turn_id}), "shape must be schema-valid before the server call"
    before_case = copy.deepcopy(repository.get_account_case("AC-123"))
    try:
        result = tool_record_direction(
            store,
            repository,
            turn_id=turn_id,
            direction=direction,
            reason="matrix",
            route=route,
            classification=classification,
        )
        error = None
    except HermesToolError as exc:
        result = None
        error = exc
    return store, repository, turn_id, before_case, result, error


def _assert_no_partial_write(store, repository, turn_id, before_case):
    turn = store.get_hermes_turn(turn_id)
    assert turn.get("direction") in (None, "", "pending"), turn.get("direction")
    assert turn.get("route") is None
    binding = store.get_hermes_case_binding("123")
    assert str(binding.get("direction") or "") in {"", "pending"}
    after_case = repository.get_account_case("AC-123")
    for field in ("route", "execution_action", "automation_status"):
        assert before_case.get(field) == after_case.get(field), field


# --- Matrix rows ---


def test_conversation_with_action_confidence_reaches_human_conversation():
    _s, _r, _t, _b, result, error = _record("human", None, _CONVERSATION_OK)
    assert error is None
    assert result["direction"] == "human"
    assert result["classification"]["route_family"] == "conversation"
    assert result["classification"]["conversation_action"] == "resolve"


def test_conversation_missing_action_confidence_blocked_by_schema_not_http():
    """The single matrix rejection: blocked at the schema layer, so it never
    reaches the handler/normalizer/HTTP at all."""
    args = _args("human", None, _CONVERSATION_MISSING_ACTION_CONFIDENCE)
    assert not _schema_accepts({**args, "turn_id": "t1"})


def test_uncertain_null_route_reaches_human_uncertain():
    _s, _r, _t, _b, result, error = _record("human", None, _UNCERTAIN_NULL_ROUTE)
    assert error is None
    assert result["direction"] == "human"
    assert result["classification"]["intent_class"] == "uncertain"
    assert result["route"] is None


def test_technical_reaches_investigation_rag():
    _s, _r, _t, _b, result, error = _record("investigation", None, _TECHNICAL)
    assert error is None
    assert result["direction"] == "investigation"
    assert result["classification"]["route_target"] == "rag"


def test_media_relay_reaches_automation_enablement():
    _s, _r, _t, _b, result, error = _record("automation", "enablement", _MEDIA_RELAY)
    assert error is None
    assert result["direction"] == "automation"
    assert result["route"] == "enablement"
    assert result["classification"]["route_family"] == "automated"


def test_backend_operation_missing_evidence_degrades_to_human_no_writes():
    """backend_operation=null is schema-valid; the normalizer degrades it to
    human review, and because direction=automation was requested the direction
    conflict is raised BEFORE any decision write."""
    store, repository, turn_id, before_case, result, error = _record(
        "automation", "enablement", _BACKEND_OP_MISSING_EVIDENCE
    )
    assert error is not None
    assert error.code == "direction_conflict"
    _assert_no_partial_write(store, repository, turn_id, before_case)


def test_billing_invalid_subtype_reason_maps_to_other_invalid_output():
    _s, _r, _t, _b, result, error = _record("human", None, _BILLING_INVALID_SUBTYPE)
    assert error is None
    assert result["direction"] == "human"
    assert result["classification"]["account_billing_subcategory"] == "other"
    assert result["classification"]["route_reason_code"] == "invalid_account_billing_output"
