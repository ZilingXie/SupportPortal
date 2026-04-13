from __future__ import annotations

import json
from typing import Any

ENGINEER_INVESTIGATION_REPLY_PROMPT_VERSION = "engineer-investigation-reply-v2"


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def build_engineer_investigation_reply_system_prompt() -> str:
    return "\n".join(
        [
            "## Role",
            "You are Engineer AI inside an internal support investigation workflow.",
            "You read the investigation context after a human engineer update and decide the next safe step.",
            "You must critically review the engineer update because engineer conclusions may be incomplete, vague, or wrong.",
            "You may either ask the engineer for one more internal detail or prepare a customer-facing draft for approval.",
            "",
            "## Decision Rules",
            'Only set state to "awaiting_confirmation" when all three are explicit and defensible: conclusion, proof, and solution or next step.',
            'If any of those three are missing, weak, or not grounded in the engineer update or handoff context, set state to "active".',
            "Proof must be traceable internal evidence such as a reproduction result, log or error trace, version/config difference, or a cited doc path.",
            "Do not treat a bare engineer conclusion or intuition as proof.",
            "Use proof_anchors to quote short exact phrases, IDs, URLs, versions, or other snippets that already exist in the engineer update or handoff context.",
            "Never copy the engineer note directly into the customer draft.",
            "The internal message is for the engineer only.",
            "The customer draft must be polished, concise, and safe to send as-is.",
            "",
            "## Language Rules",
            "Write the internal message in the engineer-thread language hint.",
            "Write draft_customer_reply in the customer language hint.",
            "",
            "## Output Requirements",
            "Return strict JSON only.",
            'Top-level keys: "state", "message", "draft_customer_reply", "reply_readiness", and "engineer_agent_state".',
            'Allowed state values: "active" or "awaiting_confirmation".',
            'When state is "awaiting_confirmation", draft_customer_reply must be non-empty and ready to send.',
            'When state is "active", draft_customer_reply must be an empty string and next_request_for_engineer must match the internal message.',
            'Return reply_readiness with keys "has_conclusion", "has_proof", "has_solution_or_next_step", "conclusion_summary", "proof_summary", "proof_anchors", "solution_or_next_step", "blockers", "critique", and "ready_for_customer_reply".',
            "Keep proof_anchors and blockers as arrays of short strings.",
            "",
            "## engineer_agent_state Requirements",
            'Return engineer_agent_state with keys "phase", "issue_understanding", "knowledge_summary", "why_not_solved", "goal", "known_facts", "missing_information", "next_request_for_engineer", "resolution_hypothesis", "ready_to_reply", and "last_refreshed_at".',
            "Keep known_facts and missing_information as arrays of short strings.",
        ]
    ).strip()


def build_engineer_investigation_reply_user_prompt(
    *,
    customer_language_hint: str,
    latest_customer_message: str,
    latest_public_assistant_reply: str,
    ticket_conversation_summary: str,
    investigation_thread_summary: str,
    handoff_packet_summary: str,
    agent_state_summary: str,
    engineer_message: str,
    revision_note: str,
    current_draft_customer_reply: str,
    engineer_thread_language_hint: str = "en",
) -> str:
    return "\n".join(
        [
            "## Language Hints",
            _dump_json(
                {
                    "customer_language_hint": str(customer_language_hint or "").strip() or "en",
                    "engineer_thread_language_hint": str(engineer_thread_language_hint or "").strip() or "en",
                }
            ),
            "",
            "## Latest Customer Message",
            str(latest_customer_message or "").strip() or "(empty)",
            "",
            "## Latest Public Assistant Reply",
            str(latest_public_assistant_reply or "").strip() or "(empty)",
            "",
            "## Recent Ticket Conversation",
            str(ticket_conversation_summary or "").strip() or "(empty)",
            "",
            "## Current Investigation Thread",
            str(investigation_thread_summary or "").strip() or "(empty)",
            "",
            "## Handoff Packet Summary",
            str(handoff_packet_summary or "").strip() or "(empty)",
            "",
            "## Ticket-Level Agent State",
            str(agent_state_summary or "").strip() or "(empty)",
            "",
            "## Latest Engineer Update",
            _dump_json(
                {
                    "engineer_message": str(engineer_message or "").strip(),
                    "revision_note": str(revision_note or "").strip(),
                    "current_draft_customer_reply": str(current_draft_customer_reply or "").strip(),
                }
            ),
        ]
    ).strip()
