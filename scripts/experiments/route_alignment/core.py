"""Pure functions for the route alignment experiment.

The module deliberately has no database or HTTP dependency.  Database reads and
candidate calls are adapters around these functions so fixture tests can prove
the comparison contract without touching Production or external providers.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


BASELINE_PIPELINE_VERSION = "account-layered-router-v11"
COMPARISON_FIELDS = (
    "intent_class",
    "conversation_action",
    "agora_route",
    "account_billing_subcategory",
    "backend_operation_subcategory",
    "automation_subcategory",
    "primary_label",
    "secondary_label",
    "route_target",
    "route_family",
    "execution_action",
    "automation_eligibility",
)
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|token|secret|password|cookie|api[_-]?key|app[_-]?id|email|requester|customer)",
    re.IGNORECASE,
)
_LONG_IDENTIFIER = re.compile(r"\b[A-Za-z0-9_-]{28,}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")


@dataclass(frozen=True)
class CaseSnapshot:
    alias: str
    ticket_id: str
    case_revision: str | None
    subject: str
    messages: tuple[dict[str, Any], ...]
    baseline: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    baseline_available: bool = True


@dataclass(frozen=True)
class CandidateResult:
    candidate: str
    status: str
    raw: dict[str, Any] | None = None
    normalized: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float | None = None
    model_version: str | None = None


@dataclass(frozen=True)
class ComparisonResult:
    alias: str
    baseline: dict[str, Any]
    candidates: dict[str, CandidateResult]
    disagreement: bool
    disagreement_fields: dict[str, list[str]]
    review_required: bool


def _text(value: Any, limit: int | None = None) -> str:
    result = " ".join(str(value or "").split()).strip()
    return result[:limit] if limit else result


def normalize_classification(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep only the stable route contract and normalize empty values."""
    source = value if isinstance(value, Mapping) else {}
    normalized: dict[str, Any] = {}
    for field_name in COMPARISON_FIELDS:
        item = source.get(field_name)
        normalized[field_name] = _text(item).lower() if isinstance(item, str) else item
    for key in ("confidence", "intent_router_model_confidence"):
        if key in source:
            try:
                normalized[key] = max(0.0, min(1.0, float(source[key])))
            except (TypeError, ValueError):
                normalized[key] = None
    normalized["pipeline_version"] = _text(source.get("pipeline_version")) or None
    normalized["route_reason_code"] = _text(source.get("route_reason_code")) or None
    normalized["degraded"] = bool(source.get("degraded", False))
    return normalized


def comparison_key(value: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_classification(value)
    return {name: normalized.get(name) for name in COMPARISON_FIELDS}


def _redact(value: Any, key: str = "") -> Any:
    if isinstance(value, Mapping):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, key) for item in value]
    if not isinstance(value, str):
        return value
    if _SENSITIVE_KEY.search(key):
        return "[redacted]"
    result = _EMAIL.sub("[redacted_email]", value)
    result = _LONG_IDENTIFIER.sub("[redacted_identifier]", result)
    return result[:4000]


def redact_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Redact customer identifiers and secrets before writing a snapshot."""
    return _redact(dict(case))


def build_snapshot(row: Mapping[str, Any], *, alias: str) -> CaseSnapshot:
    baseline = row.get("route_classification")
    if not isinstance(baseline, Mapping):
        baseline = {}
    version = _text(baseline.get("pipeline_version"))
    if version != BASELINE_PIPELINE_VERSION:
        raise ValueError(f"baseline_missing_or_wrong_version:{alias}")
    comments = row.get("messages") or row.get("comments") or []
    if not isinstance(comments, list):
        comments = []
    snapshot = {
        "ticket_id": _text(row.get("ticket_id") or row.get("client_ticket_id")),
        "case_revision": _text(row.get("case_revision")) or None,
        "subject": _text(row.get("subject") or row.get("title"), 1000),
        "messages": comments,
        "route_classification": normalize_classification(baseline),
        "metadata": row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {},
    }
    redacted = redact_case(snapshot)
    return CaseSnapshot(
        alias=alias,
        ticket_id=redacted["ticket_id"],
        case_revision=redacted["case_revision"],
        subject=redacted["subject"],
        messages=tuple(redacted["messages"]),
        baseline=redacted["route_classification"],
        metadata=redacted["metadata"],
        baseline_available=True,
    )


def compare_case(snapshot: CaseSnapshot, candidates: Iterable[CandidateResult]) -> ComparisonResult:
    candidate_map = {item.candidate: item for item in candidates}
    baseline_key = comparison_key(snapshot.baseline)
    differences: dict[str, list[str]] = {}
    review_required = not snapshot.baseline_available
    if not snapshot.baseline_available:
        differences["production"] = ["baseline_missing"]
    for name, result in candidate_map.items():
        if result.status != "ok" or result.normalized is None:
            differences[name] = ["candidate_error"]
            review_required = True
            continue
        candidate_key = comparison_key(result.normalized)
        fields = [field for field in COMPARISON_FIELDS if baseline_key.get(field) != candidate_key.get(field)]
        if fields:
            differences[name] = fields
            review_required = True
    return ComparisonResult(
        alias=snapshot.alias,
        baseline=baseline_key,
        candidates=candidate_map,
        disagreement=bool(differences),
        disagreement_fields=differences,
        review_required=review_required,
    )


def _json_default(value: Any) -> Any:
    if hasattr(value, "__dict__"):
        return value.__dict__
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def result_to_dict(result: ComparisonResult) -> dict[str, Any]:
    return {
        "case_alias": result.alias,
        "production_baseline": result.baseline,
        "candidates": {
            name: {
                "status": item.status,
                "normalized": comparison_key(item.normalized) if item.normalized else None,
                "error": item.error,
                "latency_ms": item.latency_ms,
                "model_version": item.model_version,
            }
            for name, item in result.candidates.items()
        },
        "disagreement": result.disagreement,
        "disagreement_fields": result.disagreement_fields,
        "review_required": result.review_required,
    }


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_default) + "\n")


def write_disagreement_csv(path: Path, results: Iterable[ComparisonResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["case_alias", "production_baseline", "jev", "hermes", "difference_level", "errors", "human_judgment"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            if not result.review_required:
                continue
            levels = sorted({field for values in result.disagreement_fields.values() for field in values})
            writer.writerow(
                {
                    "case_alias": result.alias,
                    "production_baseline": json.dumps(result.baseline, ensure_ascii=False, sort_keys=True),
                    "jev": json.dumps(result.candidates.get("jev").normalized if result.candidates.get("jev") else None, ensure_ascii=False, sort_keys=True),
                    "hermes": json.dumps(result.candidates.get("hermes").normalized if result.candidates.get("hermes") else None, ensure_ascii=False, sort_keys=True),
                    "difference_level": ",".join(levels),
                    "errors": ";".join(
                        f"{name}:{candidate.error or candidate.status}"
                        for name, candidate in result.candidates.items()
                        if candidate.status != "ok"
                    ),
                    "human_judgment": "",
                }
            )


def snapshot_manifest_record(snapshot: CaseSnapshot) -> dict[str, Any]:
    payload = {
        "case_alias": snapshot.alias,
        "case_revision": snapshot.case_revision,
        "subject": snapshot.subject,
        "messages": list(snapshot.messages),
        "production_baseline": snapshot.baseline,
        "metadata": snapshot.metadata,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    payload["snapshot_sha256"] = digest
    return payload
