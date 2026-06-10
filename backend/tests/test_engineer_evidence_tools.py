from __future__ import annotations

from unittest.mock import MagicMock

from backend.services.engineer_evidence_tools import serialize_engineer_evidence_search_result, search_engineer_evidence
from backend.services.engineer_evidence_tools import EngineerEvidenceSearchResult
from backend.services.rag_service_client import RagTicketAnswerDetail


def _detail(*, needs_engineer_guidance: bool) -> RagTicketAnswerDetail:
    return RagTicketAnswerDetail(
        answer="evidence",
        confidence=0.8,
        sources=["source"],
        citations=[],
        needs_engineer_guidance=needs_engineer_guidance,
        reason="grounded_answer" if not needs_engineer_guidance else "insufficient_evidence",
    )


def test_serialize_engineer_evidence_keeps_internal_citations_out_of_payload() -> None:
    internal = RagTicketAnswerDetail(
        answer="Internal runbook says the portrait output usually means transcodingConfig was misplaced.",
        confidence=0.82,
        sources=["internal://runbooks/cloud-recording"],
        citations=[{"chunk_id": "internal-1", "source_path": "technical/private.md"}],
        needs_engineer_guidance=False,
        reason="grounded_answer",
        evidence_summary={"quality_signals": {"selected_doc_count": 1}},
    )
    official = RagTicketAnswerDetail(
        answer="Official docs describe transcodingConfig under recordingConfig.",
        confidence=0.78,
        sources=["https://docs.agora.io/en/cloud-recording/reference"],
        citations=[{"chunk_id": "official-1", "source_path": "official/cloud-recording.md"}],
        needs_engineer_guidance=False,
        reason="grounded_answer",
    )

    payload = serialize_engineer_evidence_search_result(
        EngineerEvidenceSearchResult(internal=internal, official=official, errors=[]),
    )

    assert payload["access_modes"] == ["non_official_only", "official_only"]
    assert payload["internal"]["answer_summary"].startswith("Internal runbook")
    assert "sources" not in payload["internal"]
    assert "citations" not in payload["internal"]
    assert payload["official_fallback"]["sources"] == ["https://docs.agora.io/en/cloud-recording/reference"]
    assert payload["official_fallback"]["citations"] == [{"chunk_id": "official-1", "source_path": "official/cloud-recording.md"}]


def test_engineer_evidence_search_queries_non_official_first() -> None:
    client = MagicMock()
    client.query_answer_with_recovery_detail.return_value = _detail(needs_engineer_guidance=False)

    result = search_engineer_evidence(
        client,
        question="Why did cloud recording output portrait video?",
        ticket_id="T-1",
        customer_id="C-1",
    )

    assert result.internal is not None
    assert result.official is None
    assert client.query_answer_with_recovery_detail.call_count == 1
    assert client.query_answer_with_recovery_detail.call_args.kwargs["rag_access_mode"] == "non_official_only"


def test_engineer_evidence_search_uses_official_fallback_when_internal_is_insufficient() -> None:
    client = MagicMock()
    client.query_answer_with_recovery_detail.side_effect = [
        _detail(needs_engineer_guidance=True),
        _detail(needs_engineer_guidance=False),
    ]

    result = search_engineer_evidence(
        client,
        question="Which API parameter controls recording layout?",
        ticket_id="T-1",
        customer_id="C-1",
    )

    assert result.internal is not None
    assert result.official is not None
    calls = client.query_answer_with_recovery_detail.call_args_list
    assert calls[0].kwargs["rag_access_mode"] == "non_official_only"
    assert calls[1].kwargs["rag_access_mode"] == "official_only"


def test_engineer_evidence_search_uses_official_fallback_when_client_findings_request_it() -> None:
    client = MagicMock()
    client.query_answer_with_recovery_detail.side_effect = [
        _detail(needs_engineer_guidance=False),
        _detail(needs_engineer_guidance=False),
    ]

    search_engineer_evidence(
        client,
        question="Confirm the official REST API behavior.",
        ticket_id="T-1",
        customer_id="C-1",
        client_findings={"official_semantics_needed": True},
    )

    calls = client.query_answer_with_recovery_detail.call_args_list
    assert [call.kwargs["rag_access_mode"] for call in calls] == [
        "non_official_only",
        "official_only",
    ]
