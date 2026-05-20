from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EVIDENCE_VERDICT_PAYLOAD_KEY = "evidence_verdict"


@dataclass(frozen=True)
class EvidenceVerdict:
    decision: str
    risk_level: str
    needs_human: bool
    handoff_reason: str | None
    judge_decision: str | None
    judge_reason: str | None
    confidence: float
    citation_count: int
    citation_coverage_ratio: float | None
    selected_doc_count: int
    generation_mode: str | None
    deadline_exhausted: bool
    timeout_stage: str | None
    judge_override: bool = False


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _payload_source(payload: dict[str, Any]) -> dict[str, Any]:
    verdict = payload.get(EVIDENCE_VERDICT_PAYLOAD_KEY)
    if isinstance(verdict, dict):
        return verdict
    evidence_summary = payload.get("evidence_summary")
    diagnostics = evidence_summary.get("diagnostics") if isinstance(evidence_summary, dict) else None
    verdict = diagnostics.get(EVIDENCE_VERDICT_PAYLOAD_KEY) if isinstance(diagnostics, dict) else None
    return verdict if isinstance(verdict, dict) else payload


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _safe_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _normalize_decision(value: Any) -> str:
    normalized = _clean_text(value).lower()
    return "answer" if normalized == "answer" else "escalate"


def _normalize_risk_level(value: Any, *, fallback: str) -> str:
    normalized = _clean_text(value).lower()
    if normalized in {"low", "medium", "high"}:
        return normalized
    return fallback


def _answer_field(answer: Any, key: str) -> Any:
    if isinstance(answer, dict):
        return answer.get(key)
    return getattr(answer, key, None)


def _trace_field(trace: Any, key: str) -> Any:
    if isinstance(trace, dict):
        return trace.get(key)
    return getattr(trace, key, None)


def _judge_summary(trace: Any) -> dict[str, Any]:
    summary = _trace_field(trace, "judge_summary")
    return dict(summary) if isinstance(summary, dict) else {}


def _judge_decision_to_api_decision(judge_decision: str | None) -> str | None:
    normalized = _clean_text(judge_decision).lower()
    if normalized == "answer_now":
        return "answer"
    if normalized in {"escalate", "needs_human"}:
        return "escalate"
    return None


def _citation_count(trace: Any, answer: Any) -> int:
    trace_count = _trace_field(trace, "citation_count")
    if trace_count is not None:
        return _safe_int(trace_count)
    citations = _answer_field(answer, "citations")
    return len(citations) if isinstance(citations, list) else 0


def _confidence(trace: Any, answer: Any) -> float:
    answer_confidence = _answer_field(answer, "confidence")
    if answer_confidence is not None:
        return _safe_float(answer_confidence)
    return _safe_float(_trace_field(trace, "confidence_score"))


def _derive_risk_level(
    *,
    decision: str,
    needs_human: bool,
    judge_decision: str | None,
    citation_count: int,
    citation_coverage_ratio: float | None,
    deadline_exhausted: bool,
    timeout_stage: str | None,
) -> str:
    if decision == "escalate" or needs_human or deadline_exhausted or timeout_stage:
        return "high"
    if _clean_text(judge_decision).lower() == "recover_once":
        return "medium"
    if citation_coverage_ratio is not None and citation_coverage_ratio < 1.0:
        return "medium"
    if citation_count < 1:
        return "medium"
    return "low"


def build_evidence_verdict_from_trace(
    trace: Any,
    answer: Any,
    api_decision: str,
) -> EvidenceVerdict:
    decision = _normalize_decision(api_decision)
    judge_summary = _judge_summary(trace)
    judge_decision = _clean_text(judge_summary.get("decision")) or None
    judge_reason = _clean_text(judge_summary.get("reason")) or None
    citation_count = _citation_count(trace, answer)
    citation_coverage_ratio = _safe_optional_float(_trace_field(trace, "citation_coverage_ratio"))
    needs_human = bool(_trace_field(trace, "needs_human") or _answer_field(answer, "needs_human") or decision == "escalate")
    deadline_exhausted = bool(_trace_field(trace, "deadline_exhausted") or _answer_field(answer, "deadline_exhausted"))
    timeout_stage = _clean_text(_trace_field(trace, "timeout_stage") or _answer_field(answer, "timeout_stage")) or None
    mapped_judge_decision = _judge_decision_to_api_decision(judge_decision)
    risk_level = _derive_risk_level(
        decision=decision,
        needs_human=needs_human,
        judge_decision=judge_decision,
        citation_count=citation_count,
        citation_coverage_ratio=citation_coverage_ratio,
        deadline_exhausted=deadline_exhausted,
        timeout_stage=timeout_stage,
    )
    return EvidenceVerdict(
        decision=decision,
        risk_level=risk_level,
        needs_human=needs_human,
        handoff_reason=_clean_text(_trace_field(trace, "handoff_reason") or _answer_field(answer, "reason")) or None,
        judge_decision=judge_decision,
        judge_reason=judge_reason,
        confidence=round(_confidence(trace, answer), 4),
        citation_count=citation_count,
        citation_coverage_ratio=citation_coverage_ratio,
        selected_doc_count=_safe_int(_trace_field(trace, "selected_doc_count") or _answer_field(answer, "selected_doc_count")),
        generation_mode=_clean_text(_trace_field(trace, "generation_mode") or _answer_field(answer, "generation_mode")) or None,
        deadline_exhausted=deadline_exhausted,
        timeout_stage=timeout_stage,
        judge_override=mapped_judge_decision is not None and mapped_judge_decision != decision,
    )


def evidence_verdict_to_payload(verdict: EvidenceVerdict) -> dict[str, Any]:
    return {
        "decision": verdict.decision,
        "risk_level": verdict.risk_level,
        "needs_human": bool(verdict.needs_human),
        "handoff_reason": verdict.handoff_reason,
        "judge_decision": verdict.judge_decision,
        "judge_reason": verdict.judge_reason,
        "confidence": float(verdict.confidence),
        "citation_count": int(verdict.citation_count),
        "citation_coverage_ratio": verdict.citation_coverage_ratio,
        "selected_doc_count": int(verdict.selected_doc_count),
        "generation_mode": verdict.generation_mode,
        "deadline_exhausted": bool(verdict.deadline_exhausted),
        "timeout_stage": verdict.timeout_stage,
        "judge_override": bool(verdict.judge_override),
    }


def evidence_verdict_from_payload(payload: dict[str, Any]) -> EvidenceVerdict | None:
    if not isinstance(payload, dict):
        return None
    source = _payload_source(payload)
    has_contract_field = any(
        key in source
        for key in (
            "risk_level",
            "judge_decision",
            "judge_reason",
            "citation_count",
            "citation_coverage_ratio",
            "selected_doc_count",
            "generation_mode",
            "deadline_exhausted",
            "timeout_stage",
            "judge_override",
        )
    )
    if not has_contract_field:
        return None
    decision = _normalize_decision(source.get("decision"))
    needs_human = bool(source.get("needs_human") or decision == "escalate")
    citation_count = _safe_int(source.get("citation_count"))
    citation_coverage_ratio = _safe_optional_float(source.get("citation_coverage_ratio"))
    deadline_exhausted = bool(source.get("deadline_exhausted"))
    timeout_stage = _clean_text(source.get("timeout_stage")) or None
    judge_decision = _clean_text(source.get("judge_decision")) or None
    fallback_risk = _derive_risk_level(
        decision=decision,
        needs_human=needs_human,
        judge_decision=judge_decision,
        citation_count=citation_count,
        citation_coverage_ratio=citation_coverage_ratio,
        deadline_exhausted=deadline_exhausted,
        timeout_stage=timeout_stage,
    )
    return EvidenceVerdict(
        decision=decision,
        risk_level=_normalize_risk_level(source.get("risk_level"), fallback=fallback_risk),
        needs_human=needs_human,
        handoff_reason=_clean_text(source.get("handoff_reason")) or None,
        judge_decision=judge_decision,
        judge_reason=_clean_text(source.get("judge_reason")) or None,
        confidence=round(_safe_float(source.get("confidence")), 4),
        citation_count=citation_count,
        citation_coverage_ratio=citation_coverage_ratio,
        selected_doc_count=_safe_int(source.get("selected_doc_count")),
        generation_mode=_clean_text(source.get("generation_mode")) or None,
        deadline_exhausted=deadline_exhausted,
        timeout_stage=timeout_stage,
        judge_override=bool(source.get("judge_override")),
    )
