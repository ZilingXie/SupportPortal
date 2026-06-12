from __future__ import annotations

import unittest
from unittest import mock

from backend.services.engineer_summary_agent import (
    ENGINEER_SUMMARY_AGENT_VERSION,
    ENGINEER_SUMMARY_PACKET_VERSION,
    build_engineer_summary_packet,
)
from backend.services.engineer_cases import build_new_engineer_case


def _base_client_ticket() -> dict:
    return {
        "ticket_id": "TK-SUMMARY-1",
        "customer_id": "C-TEST",
        "requester": "Alice",
        "subject": "Cloud Recording portrait mode output is rotated",
        "product": "cloud_recording",
        "status": "open",
        "messages": [
            {
                "role": "customer",
                "content": "Hi, my Cloud Recording output appears rotated in portrait mode. SDK version 4.2.1.",
                "created_at": "2026-06-15T10:00:00Z",
            },
            {
                "role": "assistant",
                "content": "I understand you're having issues with Cloud Recording orientation. Let me look into this for you.",
                "created_at": "2026-06-15T10:01:00Z",
            },
        ],
    }


def _base_engineer_case() -> dict:
    return build_new_engineer_case(
        _base_client_ticket(),
        engineer_case_id="EC-SUMMARY-1",
        case_sequence=1,
        title="Cloud Recording portrait mode output is rotated",
        status="investigating",
        trigger_source="support_query",
        trigger_reason="rag_insufficient_evidence",
        now_value="2026-06-15T10:05:00Z",
    )


def _mock_execution(**overrides) -> mock.Mock:
    defaults = {
        "answer": "Try adjusting the orientationMode parameter to 1 for portrait.",
        "confidence": 0.72,
        "sources": ["Cloud Recording API doc - SetVideoEncoderConfiguration"],
        "citations": [{"chunk_id": "doc-1", "source_path": "cloud_recording/overview"}],
        "needs_investigating": True,
        "investigation_reason": "rag_insufficient_evidence",
        "answer_route": "rag",
        "scope_label": "agora_technical",
        "route_family": "agora_docs_rag",
        "execution_action": "rag",
        "tooling_profile": "default_rag",
        "route_reason": "insufficient_evidence",
        "route_confidence": 0.91,
        "search_used": True,
        "matched_signals": [],
        "client_intake_state": None,
        "client_agent_runtime_state": None,
    }
    merged = {**defaults, **overrides}
    return mock.Mock(**merged)


def _base_route_payload() -> dict:
    return {
        "answer_route": "rag",
        "scope_label": "agora_technical",
        "route_family": "agora_docs_rag",
        "execution_action": "rag",
        "tooling_profile": "default_rag",
        "route_reason": "insufficient_evidence",
        "route_confidence": 0.91,
        "search_used": True,
        "matched_signals": [],
        "client_intake_phase": "",
        "client_intake_ready_for_engineer_ticket": False,
        "client_intake_missing_information": ["SDK version", "Channel profile"],
    }


class EngineerSummaryAgentTests(unittest.TestCase):
    def test_summary_packet_records_escalation_context_and_latest_customer_message(self) -> None:
        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message="My Cloud Recording output is rotated in portrait mode",
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        self.assertEqual(packet["created_by"], "summary_agent")
        self.assertEqual(packet["packet_version"], ENGINEER_SUMMARY_PACKET_VERSION)
        self.assertEqual(packet["summary_agent_version"], ENGINEER_SUMMARY_AGENT_VERSION)
        self.assertEqual(packet["packet_id"], "summary_EC-SUMMARY-1")

        self.assertEqual(packet["client_ticket_ref"]["ticket_id"], "TK-SUMMARY-1")
        self.assertEqual(packet["client_ticket_ref"]["customer_id"], "C-TEST")

        self.assertEqual(packet["engineer_case_ref"]["engineer_case_id"], "EC-SUMMARY-1")
        self.assertEqual(packet["engineer_case_ref"]["trigger_reason"], "rag_insufficient_evidence")

        self.assertEqual(
            packet["escalation"]["reason"],
            "rag_insufficient_evidence",
        )
        self.assertTrue(packet["escalation"]["needs_investigating"])
        self.assertIn("rotated", packet["customer_context"]["latest_customer_message"].lower())

    def test_summary_packet_extracts_missing_information_from_execution_and_route_payload(self) -> None:
        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = {
            **_base_route_payload(),
            "client_intake_missing_information": ["SDK version", "Channel profile"],
        }

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message=None,
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        missing = packet["missing_information"]
        self.assertIsInstance(missing, list)
        self.assertIn("SDK version", missing)
        self.assertIn("Channel profile", missing)

    def test_summary_packet_preserves_legacy_handoff_fields_for_engineer_agent(self) -> None:
        ticket = {
            **_base_client_ticket(),
            "client_intake_state": {
                "phase": "ready_for_engineer",
                "product": "cloud_recording",
                "issue_mode": "investigation",
                "known_information": {"platform": "iOS"},
                "missing_information": ["Recording SID"],
                "ready_for_engineer_ticket": True,
                "last_updated_at": "2026-06-15T10:04:00Z",
            },
            "client_agent_runtime_state": {"runtime_version": "client_ticket_agents_v1"},
        }
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message=None,
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        self.assertEqual(packet["source"], "support_query")
        self.assertEqual(packet["latest_customer_message"], ticket["messages"][0]["content"])
        self.assertEqual(packet["latest_client_ai_reply"], ticket["messages"][1]["content"])
        self.assertEqual(packet["unresolved_reason"], "rag_insufficient_evidence")
        self.assertEqual(packet["route_summary"]["route_family"], "agora_docs_rag")
        self.assertEqual(
            packet["rag_result"]["candidate_answer"],
            "Try adjusting the orientationMode parameter to 1 for portrait.",
        )
        self.assertEqual(packet["client_intake_state"]["missing_information"], ["Recording SID"])
        self.assertEqual(
            packet["client_agent_runtime_state"],
            {"runtime_version": "client_ticket_agents_v1"},
        )

    def test_summary_packet_extracts_missing_information_from_client_intake_state(self) -> None:
        ticket = {
            **_base_client_ticket(),
            "client_intake_state": {
                "phase": "collecting_info",
                "product": "cloud_recording",
                "missing_information": ["Exact error code", "Timestamp of issue"],
            },
        }
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message=None,
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        missing = packet["missing_information"]
        self.assertIn("Exact error code", missing)
        self.assertIn("Timestamp of issue", missing)

    def test_summary_packet_provides_default_missing_information_when_none_available(self) -> None:
        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = {
            **_base_route_payload(),
            "client_intake_missing_information": [],
        }

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message=None,
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        missing = packet["missing_information"]
        self.assertIsInstance(missing, list)
        self.assertTrue(len(missing) > 0)
        self.assertIn("SDK version", missing)

    def test_summary_packet_marks_internal_fields_as_not_customer_safe(self) -> None:
        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message=None,
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        boundary = packet["redaction_boundary"]
        self.assertIsInstance(boundary, dict)
        self.assertIn("do_not_expose_to_customer", boundary)
        self.assertIn("internal_only_fields", boundary)
        self.assertIn("customer_safe_summary_fields", boundary)

        do_not_expose = boundary["do_not_expose_to_customer"]
        self.assertIn("internal source paths", do_not_expose)
        self.assertIn("unverified root cause", do_not_expose)
        self.assertIn("private diagnostics", do_not_expose)

        customer_safe = boundary["customer_safe_summary_fields"]
        self.assertIn("customer_context.latest_customer_message", customer_safe)

    def test_summary_packet_is_deterministic_without_llm_or_repository_access(self) -> None:
        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet1 = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message=None,
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        packet2 = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message=None,
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        self.assertEqual(packet1, packet2)

    def test_summary_packet_includes_current_clues_from_rag_answer(self) -> None:
        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution(
            answer="Set orientationMode to 1 for portrait recording.",
            confidence=0.85,
            sources=["doc-1", "doc-2"],
            citations=[{"chunk_id": "a"}, {"chunk_id": "b"}, {"chunk_id": "c"}],
        )
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message=None,
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        clues = packet["current_clues"]
        self.assertEqual(len(clues), 1)
        self.assertEqual(clues[0]["kind"], "rag_answer")
        self.assertEqual(clues[0]["sources_count"], 2)
        self.assertEqual(clues[0]["citations_count"], 3)
        self.assertTrue(clues[0]["customer_safe"])

    def test_summary_packet_engineer_ticket_input_is_structured(self) -> None:
        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message="Help, my recording is rotated!",
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        ticket_input = packet["engineer_ticket_input"]
        self.assertIn("title", ticket_input)
        self.assertIn("opening_summary", ticket_input)
        self.assertIn("requested_action", ticket_input)
        self.assertIn("initial_internal_note", ticket_input)

        self.assertTrue(len(ticket_input["title"]) > 0)
        self.assertIn("rotated", ticket_input["opening_summary"].lower())
        self.assertTrue(len(ticket_input["requested_action"]) > 0)
        self.assertIn("rag_insufficient_evidence", ticket_input["initial_internal_note"])

    def test_summary_packet_does_not_expose_internal_source_paths_in_customer_safe_fields(self) -> None:
        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message=None,
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        customer_safe_fields = packet["redaction_boundary"]["customer_safe_summary_fields"]

        def _field_value(path: str) -> str:
            parts = path.split(".")
            current: object = packet
            for part in parts:
                if isinstance(current, dict):
                    current = current.get(part, "")
                elif isinstance(current, list):
                    idx = int(part) if part.isdigit() else 0
                    current = current[idx] if idx < len(current) else ""
                else:
                    return ""
            return str(current)

        for path in customer_safe_fields:
            value = _field_value(path)
            self.assertNotIn("internal source paths", str(value).lower())
            self.assertNotIn("unverified root cause", str(value).lower())

    def test_summary_packet_handles_no_execution_gracefully(self) -> None:
        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message=None,
            execution=None,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        self.assertEqual(packet["created_by"], "summary_agent")
        self.assertTrue(packet["escalation"]["needs_investigating"])
        self.assertIn("SDK version", packet["missing_information"])

    def test_summary_packet_handles_no_route_payload_gracefully(self) -> None:
        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message=None,
            execution=execution,
            route_payload=None,
            now_value="2026-06-15T10:05:00Z",
        )

        self.assertEqual(packet["created_by"], "summary_agent")
        self.assertIn("SDK version", packet["missing_information"])

    def test_summary_packet_recent_messages_are_limited(self) -> None:
        ticket = _base_client_ticket()
        many_messages = []
        for i in range(20):
            many_messages.append(
                {
                    "role": "customer" if i % 2 == 0 else "assistant",
                    "content": f"Message {i}",
                    "created_at": f"2026-06-15T10:{i:02d}:00Z",
                }
            )
        ticket["messages"] = many_messages
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message=None,
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        recent = packet["customer_context"]["recent_messages"]
        self.assertLessEqual(len(recent), 8)

    def test_summary_packet_does_not_claim_root_cause(self) -> None:
        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message=None,
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )

        # The packet must not claim a root cause in its judgment/opening summary
        opening = packet["engineer_ticket_input"]["opening_summary"].lower()
        internal_note = packet["engineer_ticket_input"]["initial_internal_note"].lower()
        self.assertNotIn("root cause", opening)
        self.assertNotIn("root cause", internal_note)

        # The redaction boundary should explicitly list "unverified root cause"
        # as a do-not-expose category — that is the expected guard, not a claim.
        self.assertIn("unverified root cause", packet["redaction_boundary"]["do_not_expose_to_customer"])


class EngineerSummaryAgentIntegrationTests(unittest.TestCase):
    """Integration tests: summary packet flows through engineer case and investigation opening."""

    def test_engineer_case_has_handoff_packet_after_builder(self) -> None:
        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message="My recording is rotated",
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )
        engineer_case["engineer_handoff_packet"] = packet
        engineer_case["engineer_agent_state"] = {
            **(engineer_case.get("engineer_agent_state") or {}),
            "summary_packet_id": packet["packet_id"],
            "summary_agent_version": packet["summary_agent_version"],
            "summary_packet_version": packet["packet_version"],
        }

        self.assertEqual(
            engineer_case["engineer_handoff_packet"]["created_by"],
            "summary_agent",
        )
        self.assertTrue(
            len(
                engineer_case["engineer_handoff_packet"]["engineer_ticket_input"][
                    "opening_summary"
                ]
            )
            > 0
        )
        self.assertIn(
            "do_not_expose_to_customer",
            engineer_case["engineer_handoff_packet"]["redaction_boundary"],
        )
        self.assertEqual(
            engineer_case["engineer_agent_state"]["summary_packet_version"],
            "engineer-summary-packet-v1",
        )

    def test_engineer_case_context_preserves_handoff_packet(self) -> None:
        from backend.services.engineer_cases import build_engineer_case_context

        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message="My recording is rotated",
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )
        engineer_case["engineer_handoff_packet"] = packet

        case_context = build_engineer_case_context(ticket, engineer_case)

        self.assertIsNotNone(case_context.get("engineer_handoff_packet"))
        self.assertEqual(
            case_context["engineer_handoff_packet"]["created_by"],
            "summary_agent",
        )
        self.assertEqual(
            case_context["engineer_handoff_packet"]["packet_version"],
            "engineer-summary-packet-v1",
        )

    def test_investigation_opening_context_includes_packet_fields(self) -> None:
        from backend.services.engineer_cases import build_engineer_case_context
        from backend.services.investigation_flow import build_investigation_opening_context

        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message="My recording is rotated",
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )
        engineer_case["engineer_handoff_packet"] = packet

        case_context = build_engineer_case_context(ticket, engineer_case)
        opening = build_investigation_opening_context(
            case_context,
            trigger_reason="rag_insufficient_evidence",
            rag_answer=execution.answer,
            sources=list(execution.sources),
            citations=[dict(item) for item in execution.citations],
        )

        self.assertIsNotNone(opening)
        self.assertIn("summary_opening", opening)
        self.assertIn("summary_requested_action", opening)
        self.assertIn("missing_information", opening)
        self.assertTrue(len(str(opening["summary_opening"]).strip()) > 0)
        self.assertIsInstance(opening["missing_information"], list)

    def test_default_investigation_prompt_uses_summary_packet_fields(self) -> None:
        from backend.services.engineer_cases import build_engineer_case_context
        from backend.services.investigation_flow import (
            build_investigation_opening_context,
            default_investigation_prompt,
        )

        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message="My recording is rotated",
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )
        engineer_case["engineer_handoff_packet"] = packet
        case_context = build_engineer_case_context(ticket, engineer_case)
        opening = build_investigation_opening_context(
            case_context,
            trigger_reason="rag_insufficient_evidence",
            rag_answer=execution.answer,
            sources=list(execution.sources),
            citations=[dict(item) for item in execution.citations],
        )

        turn = default_investigation_prompt(
            case_context,
            {"messages": [], "opened_at": "2026-06-15T10:05:00Z"},
            opening_context=opening,
        )

        self.assertIn("Customer message: My recording is rotated", turn["message"])
        self.assertIn("Missing information to confirm", turn["message"])
        self.assertIn("SDK version", turn["message"])

    def test_start_or_refresh_investigation_preserves_summary_packet_contract(self) -> None:
        from backend.services.engineer_cases import build_engineer_case_context
        from backend.services.investigation_flow import (
            build_investigation_opening_context,
            start_or_refresh_investigation,
        )

        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message="My recording is rotated",
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )
        engineer_case["engineer_handoff_packet"] = packet
        case_context = build_engineer_case_context(ticket, engineer_case)
        opening = build_investigation_opening_context(
            case_context,
            trigger_reason="rag_insufficient_evidence",
            rag_answer=execution.answer,
            sources=list(execution.sources),
            citations=[dict(item) for item in execution.citations],
        )

        start_or_refresh_investigation(
            case_context,
            trigger_reason="rag_insufficient_evidence",
            trigger_source="support_query",
            now_value="2026-06-15T10:06:00Z",
            next_status="investigating",
            opening_context=opening,
            execution_context={
                **route_payload,
                "answer": execution.answer,
                "sources": list(execution.sources),
                "citations": [dict(item) for item in execution.citations],
            },
        )

        handoff = case_context["engineer_handoff_packet"]
        self.assertEqual(handoff["created_by"], "summary_agent")
        self.assertEqual(handoff["packet_version"], ENGINEER_SUMMARY_PACKET_VERSION)
        self.assertEqual(handoff["summary_agent_version"], ENGINEER_SUMMARY_AGENT_VERSION)
        self.assertIn("engineer_ticket_input", handoff)
        self.assertIn("redaction_boundary", handoff)
        self.assertIn("missing_information", handoff)
        self.assertEqual(
            handoff["rag_result"]["candidate_answer"],
            "Try adjusting the orientationMode parameter to 1 for portrait.",
        )
        self.assertEqual(
            case_context["engineer_agent_state"]["summary_packet_version"],
            ENGINEER_SUMMARY_PACKET_VERSION,
        )

    def test_investigation_opening_context_does_not_expose_do_not_expose_content(self) -> None:
        from backend.services.engineer_cases import build_engineer_case_context
        from backend.services.investigation_flow import build_investigation_opening_context

        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()
        route_payload = _base_route_payload()

        packet = build_engineer_summary_packet(
            client_ticket=ticket,
            engineer_case=engineer_case,
            customer_message="My recording is rotated",
            execution=execution,
            route_payload=route_payload,
            now_value="2026-06-15T10:05:00Z",
        )
        engineer_case["engineer_handoff_packet"] = packet

        case_context = build_engineer_case_context(ticket, engineer_case)
        opening = build_investigation_opening_context(
            case_context,
            trigger_reason="rag_insufficient_evidence",
            rag_answer=execution.answer,
            sources=list(execution.sources),
            citations=[dict(item) for item in execution.citations],
        )

        self.assertIsNotNone(opening)
        do_not_expose = packet["redaction_boundary"]["do_not_expose_to_customer"]

        customer_safe_values = [
            str(opening.get("issue_summary", "")),
            str(opening.get("rag_answer_summary", "")),
            str(opening.get("action_needed", "")),
        ]
        for value in customer_safe_values:
            for forbidden in do_not_expose:
                self.assertNotIn(forbidden.lower(), value.lower())

    def test_opening_context_without_packet_still_works(self) -> None:
        from backend.services.engineer_cases import build_engineer_case_context
        from backend.services.investigation_flow import build_investigation_opening_context

        ticket = _base_client_ticket()
        engineer_case = _base_engineer_case()
        execution = _mock_execution()

        case_context = build_engineer_case_context(ticket, engineer_case)
        opening = build_investigation_opening_context(
            case_context,
            trigger_reason="rag_insufficient_evidence",
            rag_answer=execution.answer,
            sources=list(execution.sources),
            citations=[dict(item) for item in execution.citations],
        )

        self.assertIsNotNone(opening)
        self.assertIn("issue_summary", opening)
        self.assertNotIn("summary_opening", opening)
