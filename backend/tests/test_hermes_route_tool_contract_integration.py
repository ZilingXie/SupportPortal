"""Cross-repo integration: real hermes-deploy plugin schema + forwarding
handler → SupportPortal tool API → store + worker behavior (p2-148 v3).

Uses the REAL plugin from the hermes-deploy worktree when available
(HERMES_DEPLOY_PLUGIN env or the sibling repo checkout); only the HTTP
transport is bridged to the in-process tool functions, and only model
output is authored by the test. record_direction, the normalizer, and
manual selection are NEVER mocked."""
from __future__ import annotations

import importlib.util
import json
import os
import unittest
from pathlib import Path

PLUGIN_CANDIDATES = [
    Path(os.environ.get("HERMES_DEPLOY_PLUGIN", "")) if os.environ.get("HERMES_DEPLOY_PLUGIN") else None,
    Path.home() / "Desktop/agentRelay/hermes-deploy/.worktrees/agent-tools-classification/build/supportportal_agent_tools/__init__.py",
    Path.home() / "Desktop/agentRelay/hermes-deploy/build/supportportal_agent_tools/__init__.py",
]
PLUGIN_PATH = next((p for p in PLUGIN_CANDIDATES if p and p.exists()), None)


def _load_plugin():
    spec = importlib.util.spec_from_file_location("plugin_under_integration_test", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plugin_schema(plugin):
    return [t for t in plugin._TOOLS if t[0] == "support_record_direction"][0][2]


def _assert_schema_valid(plugin, tool_args):
    """Positive inputs must pass the plugin's registered JSON Schema BEFORE
    the handler runs (round-2 acceptance: schema-lens-first)."""
    try:
        import jsonschema
    except ImportError:
        return
    jsonschema.validate(tool_args, _plugin_schema(plugin))


@unittest.skipIf(PLUGIN_PATH is None, "hermes-deploy plugin not available on this machine")
class RouteToolContractIntegrationTests(unittest.TestCase):
    def setUp(self):
        import backend.tests.test_hermes_zendesk_agent as harness
        from backend.repositories.ticket_repository import InMemoryTicketRepository
        from backend.services.automation_ecs_store import InMemoryAutomationEcsStore, JobKind

        self.plugin = _load_plugin()
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.repository.save_ticket(
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
        self.repository.save_account_case(
            {
                "account_case_id": "AC-123",
                "billing_ticket_id": "AC-123",
                "client_ticket_id": "123",
                "zendesk_ticket_id": "123",
                "processing_profile": "preproduction",
                "automation_status": "automation",
                "route": "enablement",
                "created_at": "2026-09-08T10:00:00Z",
                "updated_at": "2026-09-08T10:00:00Z",
            }
        )
        self.store = InMemoryAutomationEcsStore(harness._settings())
        self.store.migrate()
        event = harness._event()
        self.store.accept_intake(event, harness._settings().provenance())
        job = self.store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
        self.handoff = self.store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
        self.turn_id = self.handoff["turn_id"]

    def _plugin_call(self, args):
        """Bridge the plugin forwarding handler to the in-process API."""
        from backend.services.automation_hermes_tools import (
            HermesToolError,
            tool_record_direction,
        )

        handler = self.plugin._make_handler("record_direction")

        captured: list[dict] = []

        def fake_urlopen(request, timeout=None):
            body = json.loads(request.data.decode("utf-8"))
            captured.append(body)

            class R:
                def read(self):
                    return json.dumps({"bridged": True}).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return R()

        from unittest.mock import patch

        env = {
            "SUPPORTPORTAL_AGENT_API_BASE_URL": "http://bridge.invalid",
            "SUPPORTPORTAL_AGENT_TOOL_TOKEN": "token",
        }
        with patch.dict("os.environ", env, clear=False), patch.object(
            self.plugin.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            result = json.loads(handler(args))
        # Apply the SAME body the plugin posted to the REAL server function.
        server_error = None
        server_result = None
        if captured:
            body = captured[0]
            try:
                server_result = tool_record_direction(
                    self.store,
                    self.repository,
                    turn_id=body["turn_id"],
                    direction=body["direction"],
                    reason=body.get("reason") or "",
                    route=body.get("route"),
                    classification=body.get("classification"),
                )
            except HermesToolError as exc:
                server_error = exc
        return result, captured, server_result, server_error

    def test_valid_media_relay_classification_full_chain(self):
        classification = {
            "intent_class": "agora",
            "conversation_action": None,
            "intent_confidence": 0.95,
            "agora_confidence": 0.95,
            "action_confidence": 0.9,
            "agora_route": "backend_operation",
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
        tool_args = {
            "turn_id": self.turn_id,
            "direction": "automation",
            "reason": "registered_enablement",
            "route": "enablement",
            "classification": classification,
        }
        _assert_schema_valid(self.plugin, tool_args)
        result, captured, server_result, server_error = self._plugin_call(tool_args)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(captured), 1)
        # The HTTP body carries the classification as a JSON OBJECT.
        self.assertIsInstance(captured[0]["classification"], dict)
        self.assertEqual(captured[0]["classification"]["backend_operation"]["target"], "media_relay")
        self.assertIsNone(server_error)
        # Server saved automation/enablement with the classification.
        turn = self.store.get_hermes_turn(self.turn_id)
        self.assertEqual(turn.get("direction"), "automation")
        self.assertEqual(turn.get("route"), "enablement")
        # Worker selects the Enablement work manual.
        from backend.services.automation_hermes_agent import phase_instructions

        _, key = phase_instructions(phase="work", direction="automation", route=turn.get("route"))
        self.assertEqual(key, "hermes-automation-enablement-manual")

    def test_valid_troubleshoot_classification_selects_investigation(self):
        classification = {
            "intent_class": "agora",
            "conversation_action": None,
            "intent_confidence": 0.9,
            "agora_confidence": 0.9,
            "agora_route": "technical",
            "account_billing_subcategory": None,
            "backend_operation_subcategory": None,
            "backend_operation": None,
            "additional_intents": [],
            "confidence": 0.9,
            "reason_code": "technical_request",
        }
        tool_args = {
            "turn_id": self.turn_id,
            "direction": "investigation",
            "reason": "technical_request",
            "classification": classification,
        }
        _assert_schema_valid(self.plugin, tool_args)
        result, captured, server_result, server_error = self._plugin_call(tool_args)
        self.assertTrue(result.get("ok"), result)
        self.assertIsNone(server_error)
        turn = self.store.get_hermes_turn(self.turn_id)
        self.assertEqual(turn.get("direction"), "investigation")
        from backend.services.automation_hermes_agent import phase_instructions

        _, key = phase_instructions(phase="work", direction="investigation", route=turn.get("route"))
        self.assertEqual(key, "hermes-investigation-manual")

    def test_13650_shaped_input_rejected_with_no_state_change(self):
        """JSON stuffed into reason, route missing: plugin forwards (the model
        DID provide a classification object), server 422s, no decision state
        is written."""
        classification = {
            "intent_class": "agora",
            "agora_route": "backend_operation",
            "reason_code": "registered_enablement",
            "confidence": 0.9,
            "backend_operation_subcategory": "enablement",
            "backend_operation": None,
        }
        _, captured, _server_result, server_error = self._plugin_call(
            {
                "turn_id": self.turn_id,
                "direction": "automation",
                "reason": json.dumps({"flavor": "enablement"}),
                "route": None,
                "classification": classification,
            }
        )
        # The server rejected the incomplete automation decision (13650
        # shape: classification without backend_operation, route missing →
        # normalizer degrades to human, conflicting with direction=automation).
        # Either rejection code is contract-compliant; the decision state
        # must be untouched.
        self.assertIsNotNone(server_error)
        self.assertIn(
            server_error.code,
            {"route_required_for_automation", "direction_conflict"},
        )
        turn = self.store.get_hermes_turn(self.turn_id)
        self.assertNotEqual(turn.get("direction"), "automation")
        self.assertIsNone(turn.get("route"))

    def test_missing_classification_plugin_rejects_zero_http(self):
        result, captured, _sr, _se = self._plugin_call(
            {"turn_id": self.turn_id, "direction": "automation", "reason": "enablement"}
        )
        self.assertEqual(result.get("error"), "classification_required")
        self.assertEqual(captured, [])


if __name__ == "__main__":
    unittest.main()
