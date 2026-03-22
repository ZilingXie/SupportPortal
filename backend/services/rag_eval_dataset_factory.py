from __future__ import annotations

import hashlib
import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from backend.services.rag_benchmark import build_review_sample_id

_QUALITY_THRESHOLD = 0.7

if TYPE_CHECKING:
    from backend.repositories.knowledge_repository import KnowledgeRepository


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _heading_leaf(value: str) -> str:
    normalized = _clean_text(value)
    if not normalized:
        return "this section"
    return normalized.split(" > ")[-1].strip() or normalized


@dataclass(frozen=True)
class DatasetSourceChunk:
    chunk_id: str
    document_id: str
    source_type: str
    source_path: str
    heading: str
    chunk_type: str
    section_path: list[str]
    text: str
    language: str | None = None
    product: str | None = None
    metadata: dict[str, Any] | None = None


def build_dataset_candidate(chunk: DatasetSourceChunk) -> dict[str, Any]:
    normalized_text = _clean_text(chunk.text)
    heading = _clean_text(chunk.heading)
    query_type = _query_type_for_chunk(chunk.source_type, chunk.chunk_type, heading)
    difficulty = _difficulty_for_chunk(chunk.chunk_type)
    question = _question_for_chunk(query_type, heading, chunk.product)
    reference_answer = normalized_text
    key_points = _key_points(reference_answer)
    heading_path = heading or "Unknown heading"
    return {
        "dataset_item_id": _dataset_item_id(chunk.document_id, chunk.chunk_id),
        "source_type": _clean_text(chunk.source_type),
        "document_id": _clean_text(chunk.document_id),
        "chunk_id": _clean_text(chunk.chunk_id),
        "source_path": _clean_text(chunk.source_path),
        "query_type": query_type,
        "difficulty": difficulty,
        "language": _clean_text(chunk.language) or "en",
        "product": _clean_text(chunk.product),
        "question": question,
        "reference_answer": reference_answer,
        "answer_key_points": key_points,
        "expected_document_ids": [_clean_text(chunk.document_id)],
        "expected_heading_paths": [heading_path],
        "expected_evidence_refs": [
            {
                "chunk_id": _clean_text(chunk.chunk_id),
                "doc_id": _clean_text(chunk.document_id),
                "heading": heading_path,
            }
        ],
        "expected_citation_targets": [
            {
                "chunk_id": _clean_text(chunk.chunk_id),
                "doc_id": _clean_text(chunk.document_id),
                "heading": heading_path,
            }
        ],
        "item_status": "draft",
        "metadata": dict(chunk.metadata or {}),
    }


def run_generation_quality_checks(candidate: dict[str, Any]) -> dict[str, Any]:
    question = _clean_text(candidate.get("question"))
    answer = _clean_text(candidate.get("reference_answer"))
    evidence_refs = candidate.get("expected_evidence_refs") if isinstance(candidate.get("expected_evidence_refs"), list) else []
    citation_targets = candidate.get("expected_citation_targets") if isinstance(candidate.get("expected_citation_targets"), list) else []

    rejection_reasons: list[str] = []
    if not question or not answer:
        rejection_reasons.append("missing_core_fields")
    if not evidence_refs:
        rejection_reasons.append("missing_evidence_refs")
    if not citation_targets:
        rejection_reasons.append("missing_citation_targets")

    normalized_question = re.sub(r"[^a-z0-9]+", " ", question.lower()).strip()
    normalized_answer = re.sub(r"[^a-z0-9]+", " ", answer.lower()).strip()
    if normalized_question and normalized_question == normalized_answer:
        rejection_reasons.append("answer_leakage")
    elif normalized_question and normalized_question in normalized_answer:
        rejection_reasons.append("answer_leakage")

    return {
        "passed": not rejection_reasons,
        "rejection_reasons": rejection_reasons,
    }


def evaluate_dataset_candidate_votes(votes: list[dict[str, Any]]) -> dict[str, Any]:
    clean_votes = [vote for vote in votes if isinstance(vote, dict)]
    quality_scores = [float(vote.get("dataset_quality_score")) for vote in clean_votes if vote.get("dataset_quality_score") is not None]
    ambiguity_values = [bool(vote.get("ambiguity_flag")) for vote in clean_votes if isinstance(vote.get("ambiguity_flag"), bool)]
    leakage_values = [bool(vote.get("answer_leakage_flag")) for vote in clean_votes if isinstance(vote.get("answer_leakage_flag"), bool)]
    citation_values = [bool(vote.get("citation_bindable_flag")) for vote in clean_votes if isinstance(vote.get("citation_bindable_flag"), bool)]
    logic_values = [bool(vote.get("logic_eval_applicable")) for vote in clean_votes if isinstance(vote.get("logic_eval_applicable"), bool)]

    dataset_quality_score = round(statistics.median(quality_scores), 4) if quality_scores else 0.0
    disagreement = (max(quality_scores) - min(quality_scores) > 0.25) if len(quality_scores) >= 2 else False
    ambiguity_flag = sum(1 for item in ambiguity_values if item) > (len(ambiguity_values) / 2.0) if ambiguity_values else False
    answer_leakage_flag = sum(1 for item in leakage_values if item) > (len(leakage_values) / 2.0) if leakage_values else False
    citation_bindable_flag = sum(1 for item in citation_values if item) >= max(1, len(citation_values) // 2 + (len(citation_values) % 2)) if citation_values else False
    logic_eval_applicable = sum(1 for item in logic_values if item) >= max(1, len(logic_values) // 2 + (len(logic_values) % 2)) if logic_values else False
    item_status = "silver" if dataset_quality_score >= _QUALITY_THRESHOLD and not answer_leakage_flag else "needs_fix"

    reasons: list[str] = []
    if disagreement:
        reasons.append("judge_disagreement")
    if dataset_quality_score < _QUALITY_THRESHOLD:
        reasons.append("low_quality")
    if ambiguity_flag:
        reasons.append("ambiguous_candidate")
    if answer_leakage_flag:
        reasons.append("answer_leakage")
    if not citation_bindable_flag:
        reasons.append("citation_binding_risk")

    return {
        "dataset_quality_score": dataset_quality_score,
        "judge_disagreement_flag": disagreement,
        "ambiguity_flag": ambiguity_flag,
        "answer_leakage_flag": answer_leakage_flag,
        "citation_bindable_flag": citation_bindable_flag,
        "logic_eval_applicable": logic_eval_applicable,
        "item_status": item_status,
        "sampling_reasons": reasons,
        "judge_votes": clean_votes,
    }


def build_dataset_review_sample(
    *,
    dataset_item_id: str,
    generation_run_id: str,
    dataset_name: str,
    candidate: dict[str, Any],
    vote_summary: dict[str, Any],
) -> dict[str, Any]:
    normalized_item_id = _clean_text(dataset_item_id)
    normalized_generation_run_id = _clean_text(generation_run_id)
    risk_score = 1.0 - float(vote_summary.get("dataset_quality_score") or 0.0)
    if vote_summary.get("judge_disagreement_flag"):
        risk_score += 0.2
    if vote_summary.get("ambiguity_flag"):
        risk_score += 0.15
    if vote_summary.get("answer_leakage_flag"):
        risk_score += 0.2
    return {
        "sample_id": build_review_sample_id("dataset_candidate", normalized_item_id, normalized_generation_run_id),
        "sample_source": "dataset_candidate",
        "dataset_item_id": normalized_item_id,
        "request_id": None,
        "eval_run_id": None,
        "test_case_id": None,
        "risk_score": round(max(0.0, min(risk_score, 1.0)), 4),
        "sampling_reasons": list(vote_summary.get("sampling_reasons") or []),
        "review_status": "pending",
        "retrieval_ok": None,
        "answer_ok": None,
        "citation_ok": None,
        "logic_ok": None,
        "hallucination_present": None,
        "dataset_decision": None,
        "note": None,
        "sample_payload": {
            "dataset_id": _clean_text(candidate.get("dataset_id")),
            "dataset_name": _clean_text(dataset_name),
            "generation_run_id": normalized_generation_run_id,
            "source_type": _clean_text(candidate.get("source_type")),
            "query_type": _clean_text(candidate.get("query_type")),
            "difficulty": _clean_text(candidate.get("difficulty")),
            "language": _clean_text(candidate.get("language")),
            "question": _clean_text(candidate.get("question")),
            "reference_answer": _clean_text(candidate.get("reference_answer")),
            "expected_evidence_refs": candidate.get("expected_evidence_refs") if isinstance(candidate.get("expected_evidence_refs"), list) else [],
            "expected_citation_targets": candidate.get("expected_citation_targets") if isinstance(candidate.get("expected_citation_targets"), list) else [],
        },
    }


def _dataset_item_id(document_id: str, chunk_id: str) -> str:
    digest = hashlib.sha1(f"{_clean_text(document_id)}:{_clean_text(chunk_id)}".encode("utf-8")).hexdigest()[:24].upper()
    return f"DI-{digest}"


def _query_type_for_chunk(source_type: str, chunk_type: str, heading: str) -> str:
    normalized_source_type = _clean_text(source_type).lower()
    normalized_chunk_type = _clean_text(chunk_type).lower()
    normalized_heading = _clean_text(heading).lower()
    if normalized_source_type == "technical_article_api":
        if normalized_chunk_type in {"decision_logic", "root_cause_summary"}:
            return "decision"
        return "troubleshooting"
    if normalized_chunk_type in {"procedure", "howto", "api_signature", "api_params"}:
        return "configuration"
    if "compare" in normalized_heading or "difference" in normalized_heading:
        return "comparison"
    return "faq"


def _difficulty_for_chunk(chunk_type: str) -> str:
    normalized = _clean_text(chunk_type).lower()
    if normalized in {"decision_logic", "api_params", "root_cause_summary"}:
        return "advanced"
    if normalized in {"procedure", "howto", "troubleshooting_procedure"}:
        return "medium"
    return "basic"


def _question_for_chunk(query_type: str, heading: str, product: str | None) -> str:
    leaf = _heading_leaf(heading)
    clean_product = _clean_text(product)
    if query_type == "configuration":
        if clean_product:
            return f"How should I configure {leaf} for {clean_product}?"
        return f"How should I configure {leaf}?"
    if query_type == "troubleshooting":
        return f"What should I verify first when troubleshooting {leaf}?"
    if query_type == "decision":
        return f"How do I determine the correct action for {leaf}?"
    if query_type == "comparison":
        return f"What is the difference described in {leaf}?"
    return f"What does {leaf} explain?"


def _key_points(answer: str) -> list[str]:
    sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", answer) if segment.strip()]
    if sentences:
        return sentences[:3]
    return [_clean_text(answer)] if _clean_text(answer) else []


def build_dataset_candidate_votes(candidate: dict[str, Any], quality_checks: dict[str, Any]) -> list[dict[str, Any]]:
    dataset_item_id = _clean_text(candidate.get("dataset_item_id"))
    difficulty = _clean_text(candidate.get("difficulty")).lower()
    query_type = _clean_text(candidate.get("query_type")).lower()
    question = _clean_text(candidate.get("question")).lower()
    reference_answer = _clean_text(candidate.get("reference_answer"))
    base_score = 0.72
    if difficulty == "advanced":
        base_score += 0.1
    elif difficulty == "medium":
        base_score += 0.04
    if query_type in {"troubleshooting", "configuration", "decision"}:
        base_score += 0.05
    if len(_key_points(reference_answer)) >= 2:
        base_score += 0.03
    if not quality_checks.get("passed"):
        base_score -= 0.35
    ambiguity_flag = any(token in question for token in ["overview", "introduction", "summary"]) or len(question) < 18
    leakage_flag = "answer_leakage" in list(quality_checks.get("rejection_reasons") or [])
    citation_bindable_flag = bool(candidate.get("expected_citation_targets"))
    logic_eval_applicable = query_type in {"configuration", "troubleshooting", "decision"}
    votes: list[dict[str, Any]] = []
    for index in range(3):
        digest = hashlib.sha1(f"{dataset_item_id}:{index}".encode("utf-8")).hexdigest()
        jitter = (int(digest[:6], 16) / float(0xFFFFFF)) * 0.12 - 0.06
        quality_score = max(0.0, min(1.0, round(base_score + jitter, 4)))
        votes.append(
            {
                "judge_id": f"heuristic_judge_{index + 1}",
                "dataset_quality_score": quality_score,
                "ambiguity_flag": ambiguity_flag if index != 2 else ambiguity_flag and quality_score < 0.82,
                "answer_leakage_flag": leakage_flag,
                "citation_bindable_flag": citation_bindable_flag,
                "logic_eval_applicable": logic_eval_applicable,
            }
        )
    return votes


def select_manual_review_dataset_items(items: list[dict[str, Any]]) -> set[str]:
    eligible_items = [
        item
        for item in items
        if _clean_text(item.get("item_status")) in {"silver", "needs_fix"}
        and _clean_text(item.get("dataset_item_id"))
    ]
    if not eligible_items:
        return set()
    high_risk_ids = {
        _clean_text(item.get("dataset_item_id"))
        for item in eligible_items
        if item.get("judge_disagreement_flag")
        or (_clean_text(item.get("difficulty")).lower() == "advanced")
        or (float(item.get("dataset_quality_score") or 0.0) < 0.82)
        or (not bool(item.get("citation_bindable_flag")))
        or (_clean_text(item.get("item_status")) == "needs_fix")
    }
    target_size = min(len(eligible_items), max(20, math.ceil(len(eligible_items) * 0.1)))
    if len(high_risk_ids) >= target_size:
        return high_risk_ids
    ordered_items = sorted(
        eligible_items,
        key=lambda item: hashlib.sha1(_clean_text(item.get("dataset_item_id")).encode("utf-8")).hexdigest(),
    )
    selected_ids = set(high_risk_ids)
    for item in ordered_items:
        dataset_item_id = _clean_text(item.get("dataset_item_id"))
        if dataset_item_id in selected_ids:
            continue
        selected_ids.add(dataset_item_id)
        if len(selected_ids) >= target_size:
            break
    return selected_ids


def process_dataset_generation(repository: "KnowledgeRepository", generation_run_id: str) -> dict[str, Any]:
    run = repository.get_dataset_generation_run(generation_run_id)
    if run is None:
        raise LookupError(f"Dataset generation run not found: {_clean_text(generation_run_id)}")
    source_chunks = repository.list_dataset_generation_source_chunks(
        source_types=list(run.get("source_types") or []),
        question_language=_clean_text(run.get("question_language")) or "en",
    )
    items: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    for source_chunk in source_chunks:
        chunk = DatasetSourceChunk(
            chunk_id=_clean_text(source_chunk.get("chunk_id")),
            document_id=_clean_text(source_chunk.get("document_id")),
            source_type=_clean_text(source_chunk.get("source_type")),
            source_path=_clean_text(source_chunk.get("source_path")),
            heading=_clean_text(source_chunk.get("heading")),
            chunk_type=_clean_text(source_chunk.get("chunk_type")),
            section_path=source_chunk.get("section_path") if isinstance(source_chunk.get("section_path"), list) else [],
            text=_clean_text(source_chunk.get("text")),
            language=_clean_text(source_chunk.get("language")) or "en",
            product=_clean_text(source_chunk.get("product")),
            metadata=source_chunk.get("metadata") if isinstance(source_chunk.get("metadata"), dict) else {},
        )
        candidate = build_dataset_candidate(chunk)
        candidate["dataset_id"] = _clean_text(run.get("dataset_id"))
        candidate["dataset_name"] = _clean_text(run.get("dataset_name"))
        candidate["generation_run_id"] = _clean_text(run.get("generation_run_id"))
        candidate["benchmark_version"] = _clean_text(run.get("benchmark_version"))
        quality_checks = run_generation_quality_checks(candidate)
        normalized_question = re.sub(r"\s+", " ", _clean_text(candidate.get("question")).lower())
        if normalized_question in seen_questions:
            quality_checks = {
                "passed": False,
                "rejection_reasons": [*list(quality_checks.get("rejection_reasons") or []), "duplicate_question"],
            }
        if normalized_question:
            seen_questions.add(normalized_question)
        votes = build_dataset_candidate_votes(candidate, quality_checks)
        vote_summary = evaluate_dataset_candidate_votes(votes)
        item = dict(candidate)
        item.update(vote_summary)
        item["created_at"] = datetime.now(timezone.utc).isoformat()
        item["updated_at"] = item["created_at"]
        if quality_checks.get("passed"):
            item["item_status"] = _clean_text(vote_summary.get("item_status")) or "needs_fix"
            item["sampling_reasons"] = list(dict.fromkeys(vote_summary.get("sampling_reasons") or []))
        else:
            item["item_status"] = "rejected"
            item["sampling_reasons"] = list(
                dict.fromkeys([*list(quality_checks.get("rejection_reasons") or []), *list(vote_summary.get("sampling_reasons") or [])])
            )
        items.append(item)
    review_ids = select_manual_review_dataset_items(items)
    review_samples = [
        build_dataset_review_sample(
            dataset_item_id=_clean_text(item.get("dataset_item_id")),
            generation_run_id=_clean_text(run.get("generation_run_id")),
            dataset_name=_clean_text(run.get("dataset_name")),
            candidate=item,
            vote_summary=item,
        )
        for item in items
        if _clean_text(item.get("dataset_item_id")) in review_ids
    ]
    repository.save_dataset_generation_results(
        generation_run_id=_clean_text(run.get("generation_run_id")),
        items=items,
        review_samples=review_samples,
    )
    repository.update_dataset_generation_run(
        _clean_text(run.get("generation_run_id")),
        status="completed",
        error_message="",
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    refreshed_run = repository.get_dataset_generation_run(_clean_text(run.get("generation_run_id")))
    return refreshed_run or run
