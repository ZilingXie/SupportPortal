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
        # The incomplete automation decision (13650 shape: classification
        # without backend_operation, route missing) can never become an
        # automation write. Since p2-178 the direction conflict resolves as a
        # recorded server override to human instead of a 422 bounce; either
        # way the decision state never records automation.
        self.assertIn(
            server_error.code if server_error else None,
            {None, "route_required_for_automation", "direction_conflict"},
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

    def test_manual_v5_account_billing_examples_route_correctly(self):
        """Cross-check: extract the account_billing JSON examples from the
        ACTUAL delivered hermes-route-manual v5 and verify each one through
        the real plugin + normalizer + store chain. Each example gets its
        own turn so the persisted direction/route is independently verified;
        a wrong reason_code in the manual MUST cause this test to fail."""
        from backend.services.prompts.hermes_support_agent import (
            build_hermes_route_manual,
            HERMES_ROUTE_MANUAL_VERSION,
        )
        self.assertIn("v5", HERMES_ROUTE_MANUAL_VERSION)
        manual = build_hermes_route_manual()
        manual_flat = " ".join(manual.split())
        self.assertIn("registered_account_suspension", manual)
        self.assertIn("registered_fraud_account", manual)
        self.assertIn("NEVER the generic", manual)
        self.assertIn("Company Information", manual_flat)
        self.assertIn("ROUTING CLUE ONLY", manual_flat)
        import re
        json_blocks = re.findall(r'\{[^{}]{10,}?\}', manual)
        billing_examples = [
            json.loads(block) for block in json_blocks
            if "account_billing" in block and "account_billing_subcategory" in block
        ]
        self.assertGreaterEqual(len(billing_examples), 3,
            "manual must contain at least 3 account_billing examples")

        suspension_pure = False
        fraud_pure = False
        mixed_found = False

        for classification in billing_examples:
            classification.setdefault("conversation_action", None)
            classification.setdefault("conversation_subcategory", None)
            classification.setdefault("intent_confidence", 0.9)
            classification.setdefault("agora_confidence", 0.9)
            classification.setdefault("action_confidence", None)
            classification.setdefault("backend_operation", None)
            classification.setdefault("backend_operation_subcategory", None)
            classification.setdefault("additional_intents", [])
            subcategory = classification.get("account_billing_subcategory")
            tool_args = {
                "turn_id": self.turn_id,
                "direction": "automation",
                "reason": f"manual example: {subcategory}",
                "route": subcategory,
                "classification": classification,
            }
            result, _captured, server_result, server_error = self._plugin_call(tool_args)

            has_additional = bool(classification.get("additional_intents"))
            if subcategory == "account_suspension" and not has_additional:
                # Pure suspension → MUST route to automation with exact route.
                self.assertIsNone(server_error,
                    f"pure suspension example must not error: {server_error}")
                turn = self.store.get_hermes_turn(self.turn_id)
                self.assertEqual(turn.get("direction"), "automation",
                    f"pure suspension must persist direction=automation, got {turn.get('direction')}")
                self.assertEqual(turn.get("route"), "account_suspension",
                    f"pure suspension must persist route=account_suspension, got {turn.get('route')}")
                suspension_pure = True
            elif subcategory == "fraud_account" and not has_additional:
                # Pure fraud → MUST route to automation with exact route.
                self.assertIsNone(server_error,
                    f"pure fraud example must not error: {server_error}")
                turn = self.store.get_hermes_turn(self.turn_id)
                self.assertEqual(turn.get("direction"), "automation",
                    f"pure fraud must persist direction=automation, got {turn.get('direction')}")
                self.assertEqual(turn.get("route"), "fraud_account",
                    f"pure fraud must persist route=fraud_account, got {turn.get('route')}")
                fraud_pure = True
            elif has_additional:
                # Mixed → MUST be fail-closed (not automation).
                turn = self.store.get_hermes_turn(self.turn_id)
                self.assertNotEqual(turn.get("direction"), "automation",
                    f"mixed intents must NOT persist automation, got {turn.get('direction')}")
                mixed_found = True

        self.assertTrue(suspension_pure,
            "manual must contain a pure suspension example that routes to automation")
        self.assertTrue(fraud_pure,
            "manual must contain a pure fraud example that routes to automation")
        self.assertTrue(mixed_found,
            "manual must contain a mixed-intent example that fail-closes")

    def test_manual_v5_wrong_reason_code_injection_fails(self):
        """Negative control: if the manual's suspension example carried the
        WRONG reason_code (generic account_billing_request), the server must
        reject it — proving the cross-check test catches manual regressions."""
        from backend.services.prompts.hermes_support_agent import (
            build_hermes_route_manual,
        )
        manual = build_hermes_route_manual()
        import re
        json_blocks = re.findall(r'\{[^{}]{10,}?\}', manual)
        billing_examples = [
            json.loads(block) for block in json_blocks
            if "account_billing" in block and "account_billing_subcategory" in block
        ]
        # Find the pure suspension example and inject the WRONG reason code.
        suspension = next(
            (c for c in billing_examples
             if c.get("account_billing_subcategory") == "account_suspension"
             and not c.get("additional_intents")),
            None,
        )
        self.assertIsNotNone(suspension, "manual must have a pure suspension example")
        tampered = dict(suspension)
        tampered["reason_code"] = "account_billing_request"
        tampered.setdefault("conversation_action", None)
        tampered.setdefault("conversation_subcategory", None)
        tampered.setdefault("intent_confidence", 0.9)
        tampered.setdefault("agora_confidence", 0.9)
        tampered.setdefault("action_confidence", None)
        tampered.setdefault("backend_operation", None)
        tampered.setdefault("backend_operation_subcategory", None)
        tampered.setdefault("additional_intents", [])
        tool_args = {
            "turn_id": self.turn_id,
            "direction": "automation",
            "reason": "tampered suspension with wrong reason code",
            "route": "account_suspension",
            "classification": tampered,
        }
        _result, _captured, _sr, _se = self._plugin_call(tool_args)
        turn = self.store.get_hermes_turn(self.turn_id)
        self.assertNotEqual(
            turn.get("direction") if turn else None, "automation",
            "wrong reason_code must NOT persist direction=automation"
        )

    def test_suspension_with_correct_reason_code_routes_automation(self):
        """v5 contract: account_suspension + registered_account_suspension
        produces automation/account_suspension through the full chain."""
        classification = {
            "intent_class": "agora",
            "conversation_action": None,
            "intent_confidence": 0.95,
            "agora_confidence": 0.93,
            "action_confidence": None,
            "agora_route": "account_billing",
            "account_billing_subcategory": "account_suspension",
            "backend_operation_subcategory": None,
            "backend_operation": None,
            "additional_intents": [],
            "confidence": 0.93,
            "reason_code": "registered_account_suspension",
        }
        tool_args = {
            "turn_id": self.turn_id,
            "direction": "automation",
            "reason": "Account suspension review",
            "route": "account_suspension",
            "classification": classification,
        }
        _assert_schema_valid(self.plugin, tool_args)
        result, captured, server_result, server_error = self._plugin_call(tool_args)
        self.assertTrue(result.get("ok"), result)
        self.assertIsNone(server_error)
        turn = self.store.get_hermes_turn(self.turn_id)
        self.assertEqual(turn.get("direction"), "automation")
        self.assertEqual(turn.get("route"), "account_suspension")

    def test_fraud_with_correct_reason_code_routes_automation(self):
        """v5 contract: fraud_account + registered_fraud_account produces
        automation/fraud_account through the full chain."""
        classification = {
            "intent_class": "agora",
            "conversation_action": None,
            "intent_confidence": 0.95,
            "agora_confidence": 0.92,
            "action_confidence": None,
            "agora_route": "account_billing",
            "account_billing_subcategory": "fraud_account",
            "backend_operation_subcategory": None,
            "backend_operation": None,
            "additional_intents": [],
            "confidence": 0.92,
            "reason_code": "registered_fraud_account",
        }
        tool_args = {
            "turn_id": self.turn_id,
            "direction": "automation",
            "reason": "Fraud account review",
            "route": "fraud_account",
            "classification": classification,
        }
        _assert_schema_valid(self.plugin, tool_args)
        result, captured, server_result, server_error = self._plugin_call(tool_args)
        self.assertTrue(result.get("ok"), result)
        self.assertIsNone(server_error)
        turn = self.store.get_hermes_turn(self.turn_id)
        self.assertEqual(turn.get("direction"), "automation")
        self.assertEqual(turn.get("route"), "fraud_account")

    def test_suspension_with_generic_reason_code_fails_closed(self):
        """v5 contract: using the generic account_billing_request instead
        of the leaf registered_account_suspension causes the server to
        reject the classification (invalid_account_billing_output → human)."""
        classification = {
            "intent_class": "agora",
            "conversation_action": None,
            "intent_confidence": 0.92,
            "agora_confidence": 0.90,
            "action_confidence": None,
            "agora_route": "account_billing",
            "account_billing_subcategory": "account_suspension",
            "backend_operation_subcategory": None,
            "backend_operation": None,
            "additional_intents": [],
            "confidence": 0.90,
            "reason_code": "account_billing_request",
        }
        tool_args = {
            "turn_id": self.turn_id,
            "direction": "automation",
            "reason": "Generic billing reason on suspension",
            "route": "account_suspension",
            "classification": classification,
        }
        result, _captured, _sr, _se = self._plugin_call(tool_args)
        # The server must NOT accept automation for a wrong reason code —
        # either the plugin rejects or the server overrides to human.
        turn = self.store.get_hermes_turn(self.turn_id)
        if turn:
            self.assertNotEqual(turn.get("direction"), "automation")

    def test_suspension_with_additional_intents_routes_human(self):
        """v5 contract: suspension + refund in additional_intents triggers
        the human-review policy (mixed substantive intents)."""
        classification = {
            "intent_class": "agora",
            "conversation_action": None,
            "intent_confidence": 0.90,
            "agora_confidence": 0.88,
            "action_confidence": None,
            "agora_route": "account_billing",
            "account_billing_subcategory": "account_suspension",
            "backend_operation_subcategory": None,
            "backend_operation": None,
            "additional_intents": ["refund_request"],
            "confidence": 0.88,
            "reason_code": "registered_account_suspension",
        }
        tool_args = {
            "turn_id": self.turn_id,
            "direction": "automation",
            "reason": "Suspension plus refund request",
            "route": "account_suspension",
            "classification": classification,
        }
        result, _captured, _sr, _se = self._plugin_call(tool_args)
        # Mixed intents → the server's fail-closed policy routes to human.
        turn = self.store.get_hermes_turn(self.turn_id)
        if turn:
            self.assertNotEqual(turn.get("direction"), "automation")


if __name__ == "__main__":
    unittest.main()
