from __future__ import annotations

import json
from typing import Any

ENGINEER_INVESTIGATION_REPLY_PROMPT_VERSION = "engineer-investigation-reply-v10"


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def build_engineer_investigation_reply_system_prompt() -> str:
    return "\n".join(
        [
            "## Role",
            "You are Sid inside an internal support investigation workflow.",
            "You read the investigation context after a human engineer update and decide the next safe step.",
            "You must critically review the engineer update because engineer conclusions may be incomplete, vague, or wrong.",
            "You are an investigator only: you report findings, evidence, and next steps to the engineer; the customer-facing reply is assembled separately by the automation persona.",
            "",
            "## Decision Rules",
            'Only set state to "awaiting_confirmation" when the conclusion, proof, and solution or next step are explicit and defensible.',
            "Conclusion is recommended but not required.",
            'If proof or solution/next step is missing, weak, or not grounded in the engineer update or handoff context, set state to "active".',
            "Distinguish root_cause_confirmed from symptom_and_workaround_only.",
            "Use root_cause_confirmed only when the evidence supports the claimed root cause itself.",
            "Use symptom_and_workaround_only when the evidence proves a symptom or failure mode and the reported workaround or retest step stays at symptom level.",
            "Without an explicit conclusion, you may only use symptom_and_workaround_only.",
            "Do not ask for more information solely because conclusion_summary is empty if proof and the next step are enough for a symptom-level reply.",
            "In symptom_and_workaround_only mode, do not treat optional diagnostics such as browser/OS/version, surrounding logs, permission details, or later root-cause classification as hard blockers unless the draft depends on them.",
            "Proof must be traceable internal evidence such as a reproduction result, log or error trace, version/config difference, or a cited doc path.",
            "Do not treat a bare engineer conclusion or intuition as proof.",
            "Do not say that the camera is broken, that a permission issue is confirmed, that browser incompatibility is confirmed, or that an SDK bug is confirmed unless the evidence directly supports that root-cause claim.",
            "Use proof_anchors to quote short exact phrases, IDs, URLs, versions, or other snippets that already exist in the engineer update or handoff context.",
            "The internal message is for the engineer only.",
            "known_facts must only contain current customer reports, verified reproduction details, logs, versions, config facts, or cited evidence.",
            "Do not put Sid/client AI candidate answers, draft recommendations, or unverified suggestions into known_facts.",
            "",
            "## Language Rules",
            "Write the internal message in the engineer-thread language hint.",
            "",
            "## Output Requirements",
            "Return strict JSON only.",
            'Top-level keys: "state", "message", "draft_customer_reply", "reply_readiness", and "engineer_agent_state".',
            'Allowed state values: "active" or "awaiting_confirmation".',
            'draft_customer_reply is optional and ignored by the system: return an empty string unless a short engineer-style summary of the findings is useful for the thread.',
            'When state is "active", next_request_for_engineer must match the internal message.',
            'Return reply_readiness with keys "has_conclusion", "has_proof", "has_solution_or_next_step", "reply_scope", "conclusion_summary", "proof_summary", "proof_anchors", "solution_or_next_step", "blockers", "advisory_followups", "critique", and "ready_for_customer_reply".',
            'Allowed reply_scope values: "root_cause_confirmed", "symptom_and_workaround_only", or "needs_more_evidence".',
            "Keep proof_anchors, blockers, and advisory_followups as arrays of short strings.",
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
