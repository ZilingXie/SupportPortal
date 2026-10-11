"""Read-only Knowledge Review projections for the dashboard (stage 5, p2-196).

One service assembles the complete review evidence per candidate — promotion
lineage, source snapshot, Summary, Review, Slack thread binding, both delivery
targets and the human audit — so the UI never touches internal repositories.

Contracts (plan r2-stage5):

- C3 identity: every linked record is resolved through its PERSISTED id
  (promotion.source_id embeds the standalone summary task id; the case-bound
  task ids derive deterministically from the persisted engineer_case_id).
- C4 missing/conflicting lineage renders ``unavailable``/``lineage_conflict``
  with an explicit reason — never a time-based guess.
- C5 a missing source snapshot reports WHY, never an empty fake snapshot.
- C7 duplicate evidence distinguishes hits / not_recorded / unavailable /
  malformed.
- C9 Slack thread URLs come only from persisted channel/thread bindings plus
  the configured workspace domain; anything missing yields ``url: null`` with
  a reason. No guessed links.
- C14 InMemory and PostgreSQL produce the same projection semantics (the
  service reads only shared repository surfaces).
"""

from __future__ import annotations

import os
from typing import Any

KNOWLEDGE_SLACK_WORKSPACE_DOMAIN_ENV = "KNOWLEDGE_SLACK_WORKSPACE_DOMAIN"
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20
PROMOTION_STATUSES = (
    "queued", "active", "accepted", "failed", "outcome_unknown",
    "human_review", "invalidated", "rejected",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def slack_thread_url(
    channel_id: str | None, thread_ts: str | None
) -> dict[str, Any]:
    """C9: a thread URL only from persisted bindings + workspace config."""
    channel = _clean(channel_id)
    thread = _clean(thread_ts)
    workspace = _clean(os.getenv(KNOWLEDGE_SLACK_WORKSPACE_DOMAIN_ENV))
    if not channel:
        return {"url": None, "reason": "no_slack_channel_recorded"}
    if not thread:
        return {"url": None, "reason": "no_slack_thread_recorded"}
    if not workspace:
        return {"url": None, "reason": "slack_workspace_not_configured"}
    compact = thread.replace(".", "")
    if len(compact) < 16:
        compact = compact.ljust(16, "0")
    return {
        "url": f"https://{workspace}.slack.com/archives/{channel}/p{compact}",
        "reason": None,
    }


def _delivery_summary(repository: Any, promotion_id: str) -> dict[str, Any]:
    rows = repository.list_knowledge_deliveries(promotion_id) or []
    return {
        str(row.get("target")): str(row.get("status") or "") for row in rows if isinstance(row, dict)
    }


def _slack_projection(promotion: dict[str, Any]) -> dict[str, Any]:
    link = slack_thread_url(
        promotion.get("slack_channel_id"), promotion.get("slack_thread_ts")
    )
    return {
        "channel_id": _clean(promotion.get("slack_channel_id")) or None,
        "thread_ts": _clean(promotion.get("slack_thread_ts")) or None,
        "thread_url": link["url"],
        "thread_url_reason": link["reason"],
        "review_status": _clean(promotion.get("slack_review_status")) or None,
        "message_ts": _clean(promotion.get("slack_review_message_ts")) or None,
        "failure_code": _clean(promotion.get("slack_review_failure_code")) or None,
    }


def _human_audit(promotion: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": _clean(promotion.get("human_decision")) or None,
        "decided_at": _clean(promotion.get("human_decided_at")) or None,
        "principal": _clean(promotion.get("human_decision_detail")) or None,
        "email": _clean(promotion.get("human_decided_by_email")) or None,
        "slack_user_id": _clean(promotion.get("human_decided_slack_user_id")) or None,
    }


def _list_item(promotion: dict[str, Any], repository: Any) -> dict[str, Any]:
    candidate = (
        promotion.get("candidate_payload")
        if isinstance(promotion.get("candidate_payload"), dict)
        else {}
    )
    return {
        "promotion_id": _clean(promotion.get("promotion_id")),
        "status": _clean(promotion.get("status")),
        "decision": _clean(candidate.get("decision") or promotion.get("decision")),
        "candidate_type": _clean(promotion.get("candidate_type")),
        "source": {
            "type": _clean(promotion.get("source_type")),
            "id": _clean(promotion.get("source_id")),
            "version": _clean(promotion.get("source_version")),
            "content_hash": _clean(promotion.get("content_hash")),
        },
        "title": _clean(candidate.get("title")),
        "statement": _clean(candidate.get("statement"))[:200],
        "target_object_id": _clean(candidate.get("target_object_id")) or None,
        "base_version": _clean(candidate.get("base_version")) or None,
        "slack": _slack_projection(promotion),
        "delivery_summary": _delivery_summary(repository, _clean(promotion.get("promotion_id"))),
        "human_audit": _human_audit(promotion),
        "created_at": _clean(promotion.get("created_at")) or None,
        "updated_at": _clean(promotion.get("updated_at")) or None,
    }


def list_knowledge_review_promotions(
    repository: Any,
    *,
    status: str | None = None,
    decision: str | None = None,
    source_type: str | None = None,
    delivery_status: str | None = None,
    slack_review_status: str | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """C2: stable filtered pagination; read-only (no business mutation)."""
    normalized_page = max(1, int(page or 1))
    normalized_size = min(MAX_PAGE_SIZE, max(1, int(page_size or DEFAULT_PAGE_SIZE)))
    rows = [
        row for row in repository.list_weknora_promotions() if isinstance(row, dict)
    ]
    # C2/C14: the CONTRACT ordering is enforced here, not inherited from the
    # repository — the InMemory store returns insertion order, which would
    # paginate differently from PostgreSQL's (created_at, promotion_id) index
    # order for the same data.
    rows.sort(key=lambda row: (_clean(row.get("created_at")), _clean(row.get("promotion_id"))))
    items: list[dict[str, Any]] = []
    for row in rows:
        if status and _clean(row.get("status")) != _clean(status):
            continue
        if source_type and _clean(row.get("source_type")) != _clean(source_type):
            continue
        if slack_review_status:
            recorded = _clean(row.get("slack_review_status"))
            normalized_filter = _clean(slack_review_status)
            if normalized_filter == "none":
                if recorded:
                    continue
            elif recorded != normalized_filter:
                continue
        item = _list_item(row, repository)
        if decision and _clean(item["decision"]) != _clean(decision):
            continue
        if delivery_status:
            statuses = set(item["delivery_summary"].values())
            if _clean(delivery_status) not in statuses:
                continue
        items.append(item)
    total = len(items)
    start = (normalized_page - 1) * normalized_size
    return {
        "items": items[start : start + normalized_size],
        "page": normalized_page,
        "page_size": normalized_size,
        "total": total,
        # C2: the ordering is the repository's fixed (created_at, promotion_id).
        "order": ["created_at", "promotion_id"],
    }


# ------------------------------------------------------------ lineage detail


def _standalone_lineage(repository: Any, promotion: dict[str, Any]) -> dict[str, Any]:
    """Source-only candidates: the promotion source_id embeds the persisted
    standalone summary task id (``<summary_task_id>:<candidate_id>``)."""
    source_id = _clean(promotion.get("source_id"))
    summary_task_id = source_id.rsplit(":", 1)[0] if ":" in source_id else ""
    if not summary_task_id:
        return {"state": "unavailable", "reason": "promotion_source_id_not_parseable"}
    summary = repository.get_standalone_summary_task(summary_task_id)
    if not isinstance(summary, dict):
        return {
            "state": "unavailable",
            "reason": "standalone_summary_task_not_found",
            "summary_task_id": summary_task_id,
        }
    review = repository.get_standalone_review_task(f"{summary_task_id}:review")
    return {
        "state": "available",
        "summary_task_id": summary_task_id,
        "review_task_id": f"{summary_task_id}:review",
        "summary": summary,
        "review": review if isinstance(review, dict) else None,
        "review_state": "available" if isinstance(review, dict) else "not_recorded",
    }


def _case_bound_lineage(repository: Any, promotion: dict[str, Any]) -> dict[str, Any]:
    """Case-bound candidates: task ids derive deterministically from the
    persisted engineer_case_id (``<ticket>-<episode>``)."""
    engineer_case_id = _clean(promotion.get("engineer_case_id"))
    if not engineer_case_id:
        return {"state": "unavailable", "reason": "promotion_has_no_case_lineage"}
    episode_part = engineer_case_id.rsplit("-", 1)[-1]
    episode = int(episode_part) if episode_part.isdigit() else None
    if episode is None:
        return {
            "state": "unavailable",
            "reason": "engineer_case_id_episode_not_parseable",
        }
    summary_task_id = f"hermes-summary-task:{engineer_case_id}:{episode}"
    review_task_id = f"hermes-review-task:{engineer_case_id}:{episode}"
    summary = repository.get_hermes_summary_task(summary_task_id)
    review = repository.get_hermes_review_task(review_task_id)
    result: dict[str, Any] = {
        "state": "available",
        "summary_task_id": summary_task_id,
        "review_task_id": review_task_id,
        "summary": summary if isinstance(summary, dict) else None,
        "review": review if isinstance(review, dict) else None,
    }
    if not isinstance(summary, dict):
        result["summary_state"] = "not_recorded"
    return result


def _source_snapshot(repository: Any, lineage: dict[str, Any]) -> dict[str, Any]:
    """C5: the accepted source snapshot behind the candidate, with explicit
    missing/conflict reasons — never a fabricated empty snapshot."""
    if lineage.get("state") != "available":
        return {
            "state": "unavailable",
            "reason": lineage.get("reason") or "lineage_unavailable",
        }
    summary = lineage.get("summary") or {}
    intake_id = _clean(summary.get("intake_id"))
    if intake_id:
        intake = repository.get_knowledge_source(intake_id)
        if isinstance(intake, dict):
            return {
                "state": "available",
                "intake_id": intake_id,
                "source_type": _clean(intake.get("source_type")),
                "source_id": _clean(intake.get("source_id")),
                "source_version": _clean(intake.get("source_version")),
                "source_updated_at": _clean(intake.get("source_updated_at")) or None,
                "payload": intake.get("payload") if isinstance(intake.get("payload"), dict) else None,
                "references": intake.get("references") if isinstance(intake.get("references"), dict) else None,
            }
        return {"state": "missing", "reason": "source_intake_not_found", "intake_id": intake_id}
    # Case-bound summary rows have no direct intake pointer: resolve through
    # the case's linked knowledge sources — exactly one link is a resolution,
    # zero or many is an explicit conflict/missing state (C4, no guessing).
    case_id = _clean(summary.get("engineer_case_id"))
    if not case_id:
        return {"state": "unavailable", "reason": "summary_has_no_intake_or_case"}
    linked = repository.list_knowledge_sources_for_case(case_id) or []
    if len(linked) == 1:
        intake = linked[0]
        return {
            "state": "available",
            "intake_id": _clean(intake.get("intake_id")),
            "source_type": _clean(intake.get("source_type")),
            "source_id": _clean(intake.get("source_id")),
            "source_version": _clean(intake.get("source_version")),
            "source_updated_at": _clean(intake.get("source_updated_at")) or None,
            "payload": intake.get("payload") if isinstance(intake.get("payload"), dict) else None,
            "references": intake.get("references") if isinstance(intake.get("references"), dict) else None,
        }
    if not linked:
        return {"state": "missing", "reason": "no_source_linked_to_case", "engineer_case_id": case_id}
    return {
        "state": "lineage_conflict",
        "reason": "multiple_sources_linked_to_case_without_promotion_pointer",
        "engineer_case_id": case_id,
        "linked_intake_ids": [_clean(row.get("intake_id")) for row in linked],
    }


def _duplicate_evidence(lineage: dict[str, Any]) -> dict[str, Any]:
    """C7: the review-time duplicate retrieval evidence, graded — hits /
    not_recorded / unavailable / malformed."""
    review = lineage.get("review") if isinstance(lineage.get("review"), dict) else None
    if lineage.get("state") != "available":
        return {"state": "unavailable", "reason": lineage.get("reason") or "lineage_unavailable"}
    if review is None:
        return {"state": "not_recorded", "reason": "review_task_not_recorded"}
    report = review.get("report") if isinstance(review.get("report"), dict) else None
    if report is None:
        status = _clean(review.get("status"))
        if status in {"pending", "running"}:
            return {"state": "not_recorded", "reason": f"review_{status}"}
        return {"state": "malformed", "reason": "review_report_missing", "review_status": status or None}
    # The standalone review bundle carries the retrieval surfaces verbatim.
    surfaces: dict[str, Any] = {}
    bundle_keys = ("weknora", "agent_memory")
    for key in bundle_keys:
        surface = report.get(key)
        surfaces[key] = surface if isinstance(surface, dict) else None
    structural_error = _review_report_structural_error(report)
    if structural_error is not None:
        return {
            "state": "malformed",
            "reason": structural_error,
            "report": report,
            "retrieval_surfaces": surfaces,
        }
    return {"state": "available", "report": report, "retrieval_surfaces": surfaces}


def _review_report_structural_error(report: dict[str, Any]) -> str | None:
    """C6/C7 (review B6): structural validation of a review report.

    Returns None when the report is structurally sound, or a stable reason:
    - no decisions AND no surfaces at all -> review_report_empty
    - decisions present but not a list -> review_report_decisions_not_a_list
    - a present retrieval surface that is not an object ->
      review_report_surface_not_an_object
    """
    decisions = report.get("decisions")
    if "decisions" in report and not isinstance(decisions, list):
        return "review_report_decisions_not_a_list"
    for key in ("weknora", "agent_memory"):
        if key in report and not isinstance(report.get(key), dict):
            return "review_report_surface_not_an_object"
    if not isinstance(decisions, list) and not any(
        isinstance(report.get(key), dict) for key in ("weknora", "agent_memory")
    ):
        return "review_report_empty"
    return None


def _summary_projection(lineage: dict[str, Any]) -> dict[str, Any]:
    if lineage.get("state") != "available":
        return {"state": "unavailable", "reason": lineage.get("reason") or "lineage_unavailable"}
    summary = lineage.get("summary")
    if not isinstance(summary, dict):
        return {"state": "not_recorded", "reason": "summary_task_not_recorded"}
    status = _clean(summary.get("status")) or None
    # C6 (review B5): a FAILED Summary is an explicit failure state with its
    # recorded error — never "available" with a dropped packet.
    packet = summary.get("packet")
    if status == "failed":
        return {
            "state": "failed",
            "failure_reason": _clean(summary.get("error")) or "summary_failed",
            "status": status,
            "session_id": _clean(summary.get("summary_session_id")) or None,
            "run_id": _clean(summary.get("run_id")) or None,
            "error": _clean(summary.get("error")) or None,
        }
    # C6: a completed Summary whose packet is not a structured object is
    # malformed — never "available" with a silently dropped packet.
    if status == "completed" and not isinstance(packet, dict):
        return {
            "state": "malformed",
            "malformed_reason": "summary_packet_missing_or_not_object",
            "status": status,
            "session_id": _clean(summary.get("summary_session_id")) or None,
            "run_id": _clean(summary.get("run_id")) or None,
            "error": _clean(summary.get("error")) or None,
        }
    return {
        "state": "available",
        "status": status,
        "session_id": _clean(summary.get("summary_session_id")) or None,
        "run_id": _clean(summary.get("run_id")) or None,
        "error": _clean(summary.get("error")) or None,
        "packet": packet if isinstance(packet, dict) else None,
    }


def _review_projection(lineage: dict[str, Any]) -> dict[str, Any]:
    if lineage.get("state") != "available":
        return {"state": "unavailable", "reason": lineage.get("reason") or "lineage_unavailable"}
    review = lineage.get("review")
    if not isinstance(review, dict):
        return {"state": "not_recorded", "reason": "review_task_not_recorded"}
    report = review.get("report") if isinstance(review.get("report"), dict) else None
    state = "available" if report is not None else "malformed"
    malformed_reason = None if report is not None else "review_report_missing"
    if report is not None:
        # C6/C7 (review B6): the report must be STRUCTURALLY valid, not just
        # non-empty — decisions (when present) must be a list and every
        # present retrieval surface must be an object; anything else is
        # malformed and never enters the available path.
        malformed_reason = _review_report_structural_error(report)
        if malformed_reason is not None:
            state = "malformed"
    return {
        "state": state,
        "malformed_reason": malformed_reason,
        "status": _clean(review.get("status")) or None,
        "session_id": _clean(review.get("review_session_id")) or None,
        "run_id": _clean(review.get("run_id")) or None,
        "error": _clean(review.get("error")) or None,
        "report": report,
    }


def _deliveries_detail(repository: Any, promotion_id: str) -> dict[str, Any]:
    """C10: both targets independently — status, external ids, versions,
    receipts, readbacks and errors."""
    rows = repository.list_knowledge_deliveries(promotion_id) or []
    detail: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        target = _clean(row.get("target"))
        detail[target] = {
            "delivery_id": _clean(row.get("delivery_id")),
            "status": _clean(row.get("status")),
            "external_object_id": _clean(row.get("external_object_id")) or None,
            "external_version": _clean(row.get("external_version")) or None,
            "idempotency_key": _clean(row.get("idempotency_key")) or None,
            "operation_receipt": row.get("operation_receipt")
            if isinstance(row.get("operation_receipt"), dict)
            else None,
            "readback_result": row.get("readback_result")
            if isinstance(row.get("readback_result"), dict)
            else None,
            "failure_code": _clean(row.get("failure_code")) or None,
            "failure_detail": _clean(row.get("failure_detail")) or None,
            "attempt_count": row.get("attempt_count"),
        }
    return detail


def knowledge_review_detail(repository: Any, promotion_id: str) -> dict[str, Any] | None:
    rows = [
        row
        for row in repository.list_weknora_promotions()
        if isinstance(row, dict) and _clean(row.get("promotion_id")) == _clean(promotion_id)
    ]
    if not rows:
        return None
    promotion = rows[0]
    if _clean(promotion.get("source_type")) == "knowledge_source_review":
        lineage = _standalone_lineage(repository, promotion)
    else:
        lineage = _case_bound_lineage(repository, promotion)
    item = _list_item(promotion, repository)
    candidate = (
        promotion.get("candidate_payload")
        if isinstance(promotion.get("candidate_payload"), dict)
        else {}
    )
    return {
        **item,
        "candidate_payload": candidate,
        "lineage": {
            "engineer_case_id": _clean(promotion.get("engineer_case_id")) or None,
            "client_ticket_id": _clean(promotion.get("client_ticket_id")) or None,
            "investigation_id": _clean(promotion.get("investigation_id")) or None,
            "summary_session_id": _clean(promotion.get("summary_session_id")) or None,
            "summary_run_id": _clean(promotion.get("summary_run_id")) or None,
            "review_session_id": _clean(promotion.get("review_session_id")) or None,
            "review_run_id": _clean(promotion.get("review_run_id")) or None,
            "input_fingerprint": _clean(promotion.get("input_fingerprint")) or None,
            "resolution_state": lineage.get("state"),
            "resolution_reason": lineage.get("reason"),
            "summary_task_id": lineage.get("summary_task_id"),
            "review_task_id": lineage.get("review_task_id"),
        },
        "source_snapshot": _source_snapshot(repository, lineage),
        "summary": _summary_projection(lineage),
        "review": _review_projection(lineage),
        "duplicate_evidence": _duplicate_evidence(lineage),
        "deliveries": _deliveries_detail(repository, _clean(promotion.get("promotion_id"))),
        "operation_receipt": promotion.get("operation_receipt")
        if isinstance(promotion.get("operation_receipt"), dict)
        else None,
        "failure_code": _clean(promotion.get("failure_code")) or None,
        "failure_detail": _clean(promotion.get("failure_detail")) or None,
    }


__all__ = [
    "KNOWLEDGE_SLACK_WORKSPACE_DOMAIN_ENV",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "PROMOTION_STATUSES",
    "knowledge_review_detail",
    "list_knowledge_review_promotions",
    "slack_thread_url",
]
