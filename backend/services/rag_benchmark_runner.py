from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from backend.services.embedding_provider import (
    DEFAULT_PGVECTOR_TABLE,
    embedding_external_cost_per_1k,
    embedding_model_id,
    embedding_provider_name,
)
from backend.services.rag_benchmark import (
    DEFAULT_JUDGE_MODELS,
    BenchmarkCase,
    aggregate_judge_votes,
    compute_retrieval_metrics,
    load_benchmark_cases,
    parse_benchmark_cases,
    summarize_eval_daily_metrics,
)
from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY, RagAnswer, RagQueryResult, RagQueryTrace, run_rag_query
from backend.services.query_understanding import DEFAULT_QUERY_PROFILE, GLOSSARY_VERSION, QUERY_UNDERSTANDING_VERSION, SELF_QUERY_VERSION
from backend.services.support_router import (
    SupportResolution,
    SupportRouteDecision,
    citations_use_authoritative_source,
    decide_support_route,
    resolve_support_message,
)

if TYPE_CHECKING:
    from backend.repositories.knowledge_repository import KnowledgeRepository


_MODEL_PRICING = {
    "gpt-4.1": {"prompt_per_1k": 0.002, "completion_per_1k": 0.008},
    "gpt-4.1-mini": {"prompt_per_1k": 0.0004, "completion_per_1k": 0.0016},
    "gpt-4o-mini": {"prompt_per_1k": 0.00015, "completion_per_1k": 0.0006},
}


@dataclass(frozen=True)
class BenchmarkExecutionResult:
    answer_text: str
    confidence: float | None
    sources: list[str]
    citations: list[dict[str, Any]]
    needs_human: bool
    actual_route: str
    actual_scope_label: str
    route_reason: str
    route_confidence: float | None
    search_used: bool
    rag_result: RagQueryResult | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _create_repository() -> "KnowledgeRepository":
    from backend.repositories.knowledge_repository import create_knowledge_repository

    return create_knowledge_repository()


def _prepare_repository_for_benchmark(repo: "KnowledgeRepository") -> None:
    prepare = getattr(repo, "prepare_rag_benchmark_run", None)
    if callable(prepare):
        prepare()
        return
    repo.initialize()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _answer_correctness_eligible(case: BenchmarkCase) -> bool:
    if case.expected_route_family == "agora_docs_rag":
        return True
    if case.expected_route_family == "web_company_info" and case.temporal_sensitivity != "time_sensitive" and case.answer_key_points:
        return True
    return False


def _coerce_route_decision(raw_value: Any) -> SupportRouteDecision:
    if isinstance(raw_value, SupportRouteDecision):
        return raw_value
    if not isinstance(raw_value, dict):
        raise TypeError("route_decider must return SupportRouteDecision or dict")
    return SupportRouteDecision(
        scope_label=_clean_text(raw_value.get("scope_label")) or "non_agora",
        route=_clean_text(raw_value.get("route") or raw_value.get("execution_action")) or "refuse",
        route_family=_clean_text(raw_value.get("route_family")) or None,
        execution_action=_clean_text(raw_value.get("execution_action")) or None,
        tooling_profile=_clean_text(raw_value.get("tooling_profile")) or None,
        confidence=float(raw_value.get("confidence") or 0.0),
        reason=_clean_text(raw_value.get("reason")) or "benchmark_route_decider",
        matched_signals=[
            _clean_text(item)
            for item in raw_value.get("matched_signals") or []
            if _clean_text(item)
        ],
        response_language=_clean_text(raw_value.get("response_language")) or "en",
    )


def _expected_route_decision(case: BenchmarkCase) -> SupportRouteDecision:
    scope_label = {
        "agora_docs_rag": "agora_technical",
        "web_company_info": "agora_non_technical",
        "general_chat": "small_talk",
        "fallback_or_refuse": "non_agora",
    }.get(case.expected_route_family, "non_agora")
    return SupportRouteDecision(
        scope_label=scope_label,
        route=case.expected_execution_action,
        route_family=case.expected_route_family,
        execution_action=case.expected_execution_action,
        tooling_profile=case.expected_tooling_profile,
        confidence=1.0,
        reason="benchmark_expected_route",
        matched_signals=[],
        response_language=case.language or "en",
    )


def _build_synthetic_result(
    *,
    case: BenchmarkCase,
    resolution: SupportResolution,
) -> RagQueryResult:
    answer_text = _clean_text(resolution.answer)
    trace = RagQueryTrace(
        query_type=case.query_type or case.question_type,
        retrieval_strategy="not_applicable",
        vector_candidates_count=0,
        bm25_candidates_count=0,
        reranked_candidates_count=0,
        retrieved_chunk_ids=[],
        selected_chunk_ids=[],
        vector_retrieval_latency_ms=0.0,
        bm25_retrieval_latency_ms=0.0,
        retrieval_latency_ms=0.0,
        rerank_latency_ms=0.0,
        generation_latency_ms=0.0,
        total_latency_ms=0.0,
        prompt_tokens=0,
        completion_tokens=0,
        embedding_tokens=0,
        embedding_provider=None,
        embedding_model=None,
        embedding_dimensions=None,
        embedding_request_meta=[],
        model_name=None,
        answer_length=len(answer_text),
        citation_count=len(resolution.citations),
        cited_chunk_ids=[],
        needs_human=resolution.needs_engineer_guidance,
        handoff_reason=None,
        confidence_score=resolution.confidence,
        primary_source_type=case.source_type,
        primary_chunk_strategy="not_applicable",
        generation_mode=resolution.answer_route,
        structured_retry_used=False,
        extractive_fallback_used=False,
        selected_doc_count=0,
        top1_similarity_score=None,
        avg_selected_similarity_score=None,
        citation_coverage_ratio=0.0 if not resolution.citations else 1.0,
        retrieval_candidates=[],
        selected_contexts=[],
    )
    return RagQueryResult(
        answer=RagAnswer(
            answer=resolution.answer,
            confidence=resolution.confidence,
            sources=list(resolution.sources),
            citations=[dict(item) for item in resolution.citations],
        ),
        trace=trace,
    )


def resolve_judge_models(raw_value: str | None = None) -> list[str]:
    value = raw_value if raw_value is not None else os.getenv("RAG_BENCHMARK_JUDGE_MODELS")
    if not _clean_text(value):
        return list(DEFAULT_JUDGE_MODELS)
    models = [_clean_text(item) for item in str(value).split(",")]
    models = [item for item in models if item]
    if len(models) != 3:
        raise ValueError("RAG_BENCHMARK_JUDGE_MODELS must provide exactly 3 judge models")
    return models


def _vector_table_name() -> str:
    schema = _clean_text(os.getenv("PGVECTOR_SCHEMA")) or "supportportal"
    raw_table = _clean_text(os.getenv("PGVECTOR_TABLE")) or DEFAULT_PGVECTOR_TABLE
    return raw_table if "." in raw_table else f"{schema}.{raw_table}"


def _tooling_profile_correct(*, expected_tooling_profile: str | None, actual_tooling_profile: str | None) -> float:
    if not _clean_text(expected_tooling_profile):
        return 1.0
    return 1.0 if _clean_text(expected_tooling_profile) == _clean_text(actual_tooling_profile) else 0.0


def _used_prohibited_agora_docs(*, actual_execution_action: str, expected_tooling_profile: str | None) -> bool:
    if actual_execution_action != "rag":
        return False
    return _clean_text(expected_tooling_profile) not in {"", "agora_docs_only"}


def _abstained_or_deflected_properly(*, case: BenchmarkCase, decision: SupportRouteDecision, answer_text: str) -> bool:
    if case.expected_behavior == "grounded_abstain":
        return _clean_text(answer_text) == _clean_text(INSUFFICIENT_EVIDENCE_REPLY)
    if decision.execution_action not in {"controlled_response", "refuse"}:
        return True
    return bool(_clean_text(answer_text)) and case.expected_execution_action in {"controlled_response", "refuse"}


def _no_unsupported_claims(*, judge_aggregate: dict[str, Any], decision: SupportRouteDecision) -> bool:
    hallucination_flag = judge_aggregate.get("hallucination_flag")
    if isinstance(hallucination_flag, bool):
        return not hallucination_flag
    return decision.execution_action in {"controlled_response", "refuse"}


def _failure_stage_and_bucket(
    *,
    case: BenchmarkCase,
    decision: SupportRouteDecision,
    retrieval_metrics: dict[str, Any],
    judge_aggregate: dict[str, Any],
    response_policy_followed: bool,
    used_prohibited_agora_docs: bool,
) -> tuple[str, str | None]:
    if case.expected_route_family != decision.route_family or case.expected_execution_action != decision.execution_action:
        return "routing", "route_to_wrong_system"
    if used_prohibited_agora_docs:
        return "generation", "answer_should_not_have_used_agora_docs"
    if case.expected_execution_action == "rag":
        evidence_hit = retrieval_metrics.get("evidence_hit_at_5")
        evidence_coverage = retrieval_metrics.get("evidence_coverage")
        if evidence_hit == 0.0:
            return "retrieval", "retrieved_nothing_useful"
        if evidence_coverage is not None and 0.0 < float(evidence_coverage) < 1.0:
            return "retrieval", "retrieved_partially_useful_context"
        if judge_aggregate.get("hallucination_flag") is True:
            return "generation", "answer_contains_unsupported_claim"
        if (judge_aggregate.get("response_completeness_score") or 1.0) < 0.7:
            return "generation", "answer_correct_but_too_vague"
        if (judge_aggregate.get("response_relevance_score") or 1.0) < 0.7:
            return "generation", "answer_correct_but_not_relevant"
        if evidence_hit == 1.0 and (judge_aggregate.get("answer_accuracy_score") or 1.0) < 0.7:
            return "generation", "retrieved_useful_context_but_answer_missed_it"
    if not response_policy_followed:
        return "business", "answer_correct_but_not_relevant"
    return "business", None


def _strategy_snapshot(judge_models: list[str]) -> dict[str, Any]:
    return {
        "embedding_provider": embedding_provider_name(),
        "embedding_model": embedding_model_id(),
        "vector_table": _vector_table_name(),
        "rag_top_k": _clean_text(os.getenv("RAG_TOP_K")) or None,
        "chat_model": _clean_text(os.getenv("OPENAI_CHAT_MODEL")) or "gpt-4.1",
        "reranker_model": _clean_text(os.getenv("RAG_RERANK_MODEL")),
        "query_understanding_enabled": (_clean_text(os.getenv("RAG_QUERY_UNDERSTANDING_ENABLED")) or "").lower()
        not in {"0", "false", "no", "off"},
        "query_understanding_version": QUERY_UNDERSTANDING_VERSION,
        "query_profile": DEFAULT_QUERY_PROFILE,
        "glossary_version": GLOSSARY_VERSION,
        "self_query_version": SELF_QUERY_VERSION,
        "query_rewrite_enabled": (_clean_text(os.getenv("RAG_QUERY_REWRITE_ENABLED")) or "").lower()
        in {"1", "true", "yes", "on"},
        "query_decomposition_enabled": (_clean_text(os.getenv("RAG_QUERY_DECOMPOSITION_ENABLED")) or "").lower()
        in {"1", "true", "yes", "on"},
        "judge_models": judge_models,
    }


def _estimate_query_cost(result: RagQueryResult) -> float:
    trace = result.trace
    pricing = _MODEL_PRICING.get(_clean_text(trace.model_name), {})
    prompt_cost = (max(0, int(trace.prompt_tokens or 0)) / 1000.0) * float(pricing.get("prompt_per_1k", 0.0))
    completion_cost = (max(0, int(trace.completion_tokens or 0)) / 1000.0) * float(
        pricing.get("completion_per_1k", 0.0)
    )
    embedding_rate = 0.0
    if _clean_text(trace.embedding_model) == embedding_model_id():
        embedding_rate = embedding_external_cost_per_1k()
    embedding_cost = (max(0, int(trace.embedding_tokens or 0)) / 1000.0) * embedding_rate
    return round(prompt_cost + completion_cost + embedding_cost, 6)


def _default_scope_label(case: BenchmarkCase) -> str:
    return _clean_text(case.expected_scope_label) or "agora_technical"


def _wrap_rag_result(case: BenchmarkCase, result: RagQueryResult) -> BenchmarkExecutionResult:
    return BenchmarkExecutionResult(
        answer_text=_clean_text(result.answer.answer),
        confidence=result.answer.confidence,
        sources=list(result.answer.sources),
        citations=[dict(item) for item in result.answer.citations],
        needs_human=bool(result.trace.needs_human),
        actual_route="rag",
        actual_scope_label=_default_scope_label(case),
        route_reason="rag_direct_benchmark",
        route_confidence=1.0,
        search_used=False,
        rag_result=result,
    )


def _decision_from_execution_result(case: BenchmarkCase, execution_result: BenchmarkExecutionResult) -> SupportRouteDecision:
    return SupportRouteDecision(
        scope_label=_clean_text(execution_result.actual_scope_label) or _default_scope_label(case),
        route=_clean_text(execution_result.actual_route) or "rag",
        confidence=float(execution_result.route_confidence or 1.0),
        reason=_clean_text(execution_result.route_reason) or "benchmark_execution_result",
        matched_signals=[],
        response_language=case.language or "en",
    )


def _execution_result_from_resolution(
    *,
    case: BenchmarkCase,
    decision: SupportRouteDecision,
    resolution: SupportResolution,
    result: RagQueryResult,
) -> BenchmarkExecutionResult:
    return BenchmarkExecutionResult(
        answer_text=_clean_text(resolution.answer),
        confidence=resolution.confidence,
        sources=list(resolution.sources),
        citations=[dict(item) for item in resolution.citations],
        needs_human=bool(resolution.needs_engineer_guidance),
        actual_route=_clean_text(resolution.answer_route) or _clean_text(decision.route) or "rag",
        actual_scope_label=_clean_text(resolution.scope_label) or _clean_text(decision.scope_label) or _default_scope_label(case),
        route_reason=_clean_text(resolution.route_reason) or _clean_text(decision.reason) or "benchmark_resolution",
        route_confidence=resolution.route_confidence if resolution.route_confidence is not None else decision.confidence,
        search_used=bool(resolution.search_used),
        rag_result=result,
    )


def _empty_retrieval_metrics() -> dict[str, Any]:
    return {
        "hit_at_1": None,
        "hit_at_3": None,
        "hit_at_5": None,
        "recall_at_5": None,
        "mrr": None,
        "ndcg_at_5": None,
        "document_relevance_score": None,
        "evidence_hit_at_1": None,
        "evidence_hit_at_3": None,
        "evidence_hit_at_5": None,
    }


def _execute_case(
    *,
    case: BenchmarkCase,
    runner: Callable[[str, int | None], RagQueryResult | None],
    top_k: int | None,
    message_resolver: Callable[..., Any] | None,
    route_decider: Callable[..., SupportRouteDecision | dict[str, Any]] | None,
) -> BenchmarkExecutionResult:
    if not case.route_aware:
        result = runner(case.question, top_k=top_k)
        if result is None:
            raise RuntimeError("run_rag_query returned None; verify RAG configuration before running the benchmark")
        return _wrap_rag_result(case, result)

    from backend.services.support_router import resolve_support_message

    resolver = message_resolver or resolve_support_message
    rag_result_holder: dict[str, RagQueryResult] = {}
    decision = (
        _coerce_route_decision(route_decider(case.question, ticket_subject=None, ticket_context=None))
        if route_decider is not None
        else None
    )

    def _rag_answerer(message: str) -> tuple[str, float, list[str], list[dict[str, str]], bool]:
        rag_result = runner(message, top_k=top_k)
        if rag_result is None:
            raise RuntimeError("run_rag_query returned None; verify RAG configuration before running the benchmark")
        rag_result_holder["result"] = rag_result
        return (
            rag_result.answer.answer,
            rag_result.answer.confidence,
            list(rag_result.answer.sources),
            [dict(item) for item in rag_result.answer.citations],
            bool(rag_result.trace.needs_human),
        )

    resolution = resolver(
        case.question,
        ticket_subject=None,
        ticket_context=None,
        rag_answerer=_rag_answerer,
        decision=decision,
    )
    rag_result = rag_result_holder.get("result")
    if _clean_text(getattr(resolution, "answer_route", "")) == "rag" and rag_result is None:
        rag_result = runner(case.question, top_k=top_k)
        if rag_result is None:
            raise RuntimeError("run_rag_query returned None; verify RAG configuration before running the benchmark")
    return BenchmarkExecutionResult(
        answer_text=_clean_text(getattr(resolution, "answer", "")),
        confidence=getattr(resolution, "confidence", None),
        sources=list(getattr(resolution, "sources", []) or []),
        citations=[dict(item) for item in list(getattr(resolution, "citations", []) or []) if isinstance(item, dict)],
        needs_human=bool(getattr(resolution, "needs_engineer_guidance", False)),
        actual_route=_clean_text(getattr(resolution, "answer_route", "")) or "rag",
        actual_scope_label=_clean_text(getattr(resolution, "scope_label", "")) or _default_scope_label(case),
        route_reason=_clean_text(getattr(resolution, "route_reason", "")),
        route_confidence=getattr(resolution, "route_confidence", None),
        search_used=bool(getattr(resolution, "search_used", False)),
        rag_result=rag_result,
    )


def _missed_expected_docs(
    *,
    case: BenchmarkCase,
    retrieval_candidates: list[dict[str, Any]],
) -> list[str]:
    expected_docs = {_clean_text(item) for item in case.expected_document_ids if _clean_text(item)}
    candidate_docs = {
        _clean_text(candidate.get("doc_id"))
        for candidate in retrieval_candidates
        if _clean_text(candidate.get("doc_id"))
    }
    return sorted(item for item in expected_docs if item and item not in candidate_docs)


def _root_cause_label(
    *,
    case: BenchmarkCase,
    execution_result: BenchmarkExecutionResult,
    retrieval_metrics: dict[str, Any],
    judge_aggregate: dict[str, Any],
) -> str:
    if case.route_aware and execution_result.actual_route != case.expected_route:
        return "route_mismatch"
    if execution_result.actual_route == "web_search":
        if not execution_result.search_used:
            return "web_search_failure"
        if judge_aggregate.get("hallucination_flag") is True:
            return "generation_hallucination"
        return "grounded_answer"
    if execution_result.actual_route == "refuse":
        if judge_aggregate.get("hallucination_flag") is True:
            return "generation_hallucination"
        return "grounded_answer"
    result = execution_result.rag_result
    if result is None:
        return "route_mismatch"
    if case.expected_execution_action != "rag":
        return "policy_controlled_response" if case.expected_execution_action == "controlled_response" else "grounded_answer"
    if retrieval_metrics.get("hit_at_5") == 0.0:
        return "retrieval_miss"
    if int(result.trace.selected_doc_count or 0) <= 0:
        return "retrieval_miss"
    if judge_aggregate.get("hallucination_flag") is True:
        return "generation_hallucination"
    if (judge_aggregate.get("citation_correctness_score") or 0.0) < 0.70:
        return "citation_issue"
    if (judge_aggregate.get("needs_human") is True or result.trace.needs_human) and not case.expected_handoff:
        return "unnecessary_handoff"
    return "grounded_answer"


def _build_trace_payload(
    *,
    case: BenchmarkCase,
    execution_result: BenchmarkExecutionResult,
    retrieval_metrics: dict[str, Any],
    judge_votes: list[dict[str, Any]],
    decision: SupportRouteDecision,
) -> dict[str, Any]:
    result = execution_result.rag_result
    trace = result.trace if result is not None else None
    retrieval_candidates = list(trace.retrieval_candidates) if trace is not None else []
    selected_contexts = list(trace.selected_contexts) if trace is not None else []
    missed_expected_docs = (
        _missed_expected_docs(
            case=case,
            retrieval_candidates=retrieval_candidates,
        )
        if trace is not None and case.retrieval_metrics_enabled
        else []
    )
    return {
        "question": case.question,
        "answer_text": execution_result.answer_text,
        "actual_answer_text": execution_result.answer_text,
        "expected_answer_text": case.reference_answer,
        "answer_preview": _clean_text(execution_result.answer_text)[:280],
        "answer_sources": list(execution_result.sources),
        "answer_citations": [dict(item) for item in execution_result.citations],
        "route_family": decision.route_family,
        "execution_action": decision.execution_action,
        "tooling_profile": decision.tooling_profile,
        "query_type": case.query_type or (trace.query_type if trace is not None else ""),
        "source_type": case.source_type or (trace.primary_source_type if trace is not None else ""),
        "product": case.product,
        "language": case.language,
        "expected_document_ids": case.expected_document_ids,
        "expected_heading_paths": case.expected_heading_paths,
        "expected_evidence_refs": case.expected_evidence_refs,
        "expected_behavior": case.expected_behavior,
        "expected_route": case.expected_route,
        "actual_route": execution_result.actual_route,
        "expected_scope_label": case.expected_scope_label,
        "actual_scope_label": execution_result.actual_scope_label,
        "route_correct": execution_result.actual_route == case.expected_route,
        "route_reason": execution_result.route_reason,
        "route_confidence": execution_result.route_confidence,
        "search_used": execution_result.search_used,
        "sources": execution_result.sources,
        "citations": execution_result.citations,
        "missed_expected_docs": missed_expected_docs,
        "retrieval_metrics": retrieval_metrics,
        "generation_mode": trace.generation_mode if trace is not None else execution_result.actual_route,
        "needs_human": trace.needs_human if trace is not None else execution_result.needs_human,
        "handoff_reason": trace.handoff_reason if trace is not None else None,
        "confidence_score": trace.confidence_score if trace is not None else execution_result.confidence,
        "citation_count": trace.citation_count if trace is not None else len(execution_result.citations),
        "citation_coverage_ratio": trace.citation_coverage_ratio if trace is not None else None,
        "cited_chunk_ids": trace.cited_chunk_ids if trace is not None else [],
        "structured_retry_used": trace.structured_retry_used if trace is not None else False,
        "extractive_fallback_used": trace.extractive_fallback_used if trace is not None else False,
        "selected_doc_count": trace.selected_doc_count if trace is not None else None,
        "top1_similarity_score": trace.top1_similarity_score if trace is not None else None,
        "avg_selected_similarity_score": trace.avg_selected_similarity_score if trace is not None else None,
        "vector_candidates_count": trace.vector_candidates_count if trace is not None else None,
        "bm25_candidates_count": trace.bm25_candidates_count if trace is not None else None,
        "reranked_candidates_count": trace.reranked_candidates_count if trace is not None else None,
        "retrieval_candidates": retrieval_candidates,
        "selected_contexts": selected_contexts,
        "latency_ms": {
            "retrieval": trace.retrieval_latency_ms if trace is not None else None,
            "generation": trace.generation_latency_ms if trace is not None else None,
            "total": trace.total_latency_ms if trace is not None else None,
        },
        "judge_votes": judge_votes,
    }


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    content = str(text or "").strip()
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _response_to_text(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")).strip())
            else:
                parts.append(str(item).strip())
        return "\n".join(part for part in parts if part)
    return str(content or "")


def invoke_judge_vote(
    *,
    judge_model: str,
    case: BenchmarkCase,
    result: BenchmarkExecutionResult,
    retrieval_metrics: dict[str, Any],
) -> dict[str, Any]:
    api_key = _clean_text(os.getenv("OPENAI_API_KEY"))
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for benchmark judges")

    from langchain_openai import ChatOpenAI

    rag_result = result.rag_result
    answer_text = _clean_text(result.answer_text)
    retrieval_candidates = list(rag_result.trace.retrieval_candidates) if rag_result is not None else []
    selected_contexts = list(rag_result.trace.selected_contexts) if rag_result is not None else []
    llm = ChatOpenAI(
        model=judge_model,
        temperature=0,
        api_key=api_key,
        request_timeout=float(os.getenv("RAG_BENCHMARK_JUDGE_TIMEOUT_SECONDS") or 30.0),
        max_retries=1,
    )
    system_prompt = """You are grading a support assistant benchmark answer.

Return JSON only with this exact schema:
{
  "document_relevance_score": 0.0,
  "faithfulness_score": 0.0,
  "groundedness_score": 0.0,
  "response_relevance_score": 0.0,
  "response_completeness_score": 0.0,
  "citation_correctness_score": 0.0,
  "answer_accuracy_score": 0.0,
  "answer_logic_score": 0.0,
  "hallucination_flag": false,
  "needs_human": false,
  "failure_type": "string"
}

Scoring rules:
- All numeric scores must be between 0.0 and 1.0.
- Judge only from the provided question, expected answer, expected route, retrieved candidates, selected context, final answer, and citations.
- When expected_route is not "rag", prioritize route correctness, answer relevance, answer accuracy, and answer logic.
- Mark hallucination_flag true if the answer includes claims not supported by the selected context or provided citations.
- failure_type should be one of: retrieval_miss, hallucination, incomplete_answer, bad_citation, handoff_needed, route_mismatch, web_search_failure, grounded_answer.
"""
    user_prompt = json.dumps(
        {
            "test_case_id": case.test_case_id,
            "question": case.question,
            "reference_answer": case.reference_answer,
            "expected_document_ids": case.expected_document_ids,
            "expected_heading_paths": case.expected_heading_paths,
            "expected_evidence_refs": case.expected_evidence_refs,
            "answer_key_points": case.answer_key_points,
            "expected_handoff": case.expected_handoff,
            "expected_route": case.expected_route,
            "expected_scope_label": case.expected_scope_label,
            "actual_route": result.actual_route,
            "actual_scope_label": result.actual_scope_label,
            "search_used": result.search_used,
            "retrieval_metrics_enabled": case.retrieval_metrics_enabled,
            "citation_metrics_enabled": case.citation_metrics_enabled,
            "retrieval_metrics": retrieval_metrics,
            "retrieval_candidates": retrieval_candidates,
            "selected_contexts": selected_contexts,
            "answer": answer_text,
            "citations": result.citations,
            "sources": result.sources,
            "generation_mode": rag_result.trace.generation_mode if rag_result is not None else result.actual_route,
            "needs_human": rag_result.trace.needs_human if rag_result is not None else result.needs_human,
        },
        ensure_ascii=False,
    )
    response = llm.invoke([("system", system_prompt), ("user", user_prompt)])
    payload = _extract_json_payload(_response_to_text(response))
    if payload is None:
        raise ValueError(f"Judge {judge_model} returned invalid JSON")
    payload["judge_model"] = judge_model
    if not case.retrieval_metrics_enabled:
        payload["document_relevance_score"] = None
        payload["faithfulness_score"] = None
        payload["groundedness_score"] = None
    if not case.citation_metrics_enabled:
        payload["citation_correctness_score"] = None
    return payload


def _failure_type(
    *,
    execution_result: BenchmarkExecutionResult,
    retrieval_metrics: dict[str, Any],
    judge_aggregate: dict[str, Any],
    case: BenchmarkCase,
) -> str:
    if case.route_aware and execution_result.actual_route != case.expected_route:
        return "route_mismatch"

    answer_text = _clean_text(execution_result.answer_text)
    if execution_result.actual_route == "web_search":
        if (judge_aggregate.get("answer_accuracy_score") or 0.0) < 0.70:
            return "web_search_failure"
        if judge_aggregate.get("hallucination_flag") is True:
            return "hallucination"
        return "grounded_answer"

    if execution_result.actual_route == "refuse":
        if judge_aggregate.get("hallucination_flag") is True:
            return "hallucination"
        if (judge_aggregate.get("answer_accuracy_score") or 0.0) < 0.70:
            return "incomplete_answer"
        return "grounded_answer"

    result = execution_result.rag_result
    if result is None:
        return "route_mismatch"
    if case.expected_execution_action != "rag":
        if judge_aggregate.get("response_policy_followed") is False:
            return "policy_violation"
        return "grounded_answer"
    if case.expected_behavior == "grounded_abstain":
        if answer_text == INSUFFICIENT_EVIDENCE_REPLY:
            return "grounded_answer"
        if judge_aggregate.get("hallucination_flag") is True:
            return "hallucination"
        return "incomplete_answer"
    if answer_text == INSUFFICIENT_EVIDENCE_REPLY or result.trace.needs_human != case.expected_handoff:
        return "handoff_needed"
    if retrieval_metrics.get("hit_at_5") == 0.0:
        return "retrieval_miss"
    if judge_aggregate.get("hallucination_flag") is True:
        return "hallucination"
    if (judge_aggregate.get("citation_correctness_score") or 0.0) < 0.70:
        return "bad_citation"
    if (judge_aggregate.get("response_completeness_score") or 0.0) < 0.70:
        return "incomplete_answer"
    return "grounded_answer"


def _group_results(rows: list[dict[str, Any]]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            row.get("source_type"),
            row.get("query_type"),
            row.get("retrieval_strategy"),
            row.get("chunk_strategy"),
        )
        groups[key].append(row)
    return groups


def _build_eval_row(
    *,
    case: BenchmarkCase,
    decision: SupportRouteDecision,
    execution_result: BenchmarkExecutionResult,
    judge_votes: list[dict[str, Any]],
) -> dict[str, Any]:
    rag_result = execution_result.rag_result
    retrieval_metrics = (
        compute_retrieval_metrics(
            rag_result.trace.retrieval_candidates,
            expected_document_ids=case.expected_document_ids,
            expected_heading_paths=case.expected_heading_paths,
            expected_evidence_refs=case.expected_evidence_refs,
            answer_key_points=case.answer_key_points,
        )
        if case.retrieval_metrics_enabled and rag_result is not None
        else _empty_retrieval_metrics()
    )
    judge_aggregate = aggregate_judge_votes(judge_votes)
    matched_expected_execution_action = decision.execution_action == case.expected_execution_action
    used_prohibited_agora_docs = _used_prohibited_agora_docs(
        actual_execution_action=decision.execution_action or "",
        expected_tooling_profile=case.expected_tooling_profile,
    )
    abstained_or_deflected_properly = _abstained_or_deflected_properly(
        case=case,
        decision=decision,
        answer_text=execution_result.answer_text,
    )
    no_unsupported_claims = _no_unsupported_claims(
        judge_aggregate=judge_aggregate,
        decision=decision,
    )
    response_policy_followed = (
        matched_expected_execution_action
        and not used_prohibited_agora_docs
        and abstained_or_deflected_properly
        and no_unsupported_claims
    )
    failure_stage, failure_bucket = _failure_stage_and_bucket(
        case=case,
        decision=decision,
        retrieval_metrics=retrieval_metrics,
        judge_aggregate=judge_aggregate,
        response_policy_followed=response_policy_followed,
        used_prohibited_agora_docs=used_prohibited_agora_docs,
    )
    trace_payload = _build_trace_payload(
        case=case,
        execution_result=execution_result,
        retrieval_metrics=retrieval_metrics,
        judge_votes=judge_votes,
        decision=decision,
    )
    chunk_strategy = rag_result.trace.primary_chunk_strategy if rag_result is not None else None
    retrieval_strategy = rag_result.trace.retrieval_strategy if rag_result is not None else f"route_{execution_result.actual_route}"
    row = {
        "test_case_id": case.test_case_id,
        "dataset_schema_version": case.dataset_schema_version,
        "question": case.question,
        "question_type": case.question_type,
        "category": case.category,
        "query_type": case.query_type or (rag_result.trace.query_type if rag_result is not None else case.query_type),
        "source_type": case.source_type or (rag_result.trace.primary_source_type if rag_result is not None else case.source_type),
        "product": case.product,
        "language": case.language,
        "chunk_strategy": chunk_strategy,
        "retrieval_strategy": retrieval_strategy,
        "expected_route_family": case.expected_route_family,
        "actual_route_family": decision.route_family,
        "expected_execution_action": case.expected_execution_action,
        "actual_execution_action": decision.execution_action,
        "expected_tooling_profile": case.expected_tooling_profile,
        "expected_behavior": case.expected_behavior,
        "actual_tooling_profile": decision.tooling_profile,
        "route_family_correct": 1.0 if case.expected_route_family == decision.route_family else 0.0,
        "execution_action_correct": 1.0 if matched_expected_execution_action else 0.0,
        "tooling_profile_correct": _tooling_profile_correct(
            expected_tooling_profile=case.expected_tooling_profile,
            actual_tooling_profile=decision.tooling_profile,
        ),
        "hit_at_1": retrieval_metrics.get("hit_at_1"),
        "hit_at_3": retrieval_metrics.get("hit_at_3"),
        "hit_at_5": retrieval_metrics.get("hit_at_5"),
        "document_hit_at_5": retrieval_metrics.get("document_hit_at_5"),
        "recall_at_5": retrieval_metrics.get("recall_at_5"),
        "mrr": retrieval_metrics.get("mrr"),
        "ndcg_at_5": retrieval_metrics.get("ndcg_at_5"),
        "evidence_hit_at_1": retrieval_metrics.get("evidence_hit_at_1"),
        "evidence_hit_at_3": retrieval_metrics.get("evidence_hit_at_3"),
        "evidence_hit_at_5": retrieval_metrics.get("evidence_hit_at_5"),
        "evidence_coverage": retrieval_metrics.get("evidence_coverage"),
        "noise_rate": retrieval_metrics.get("noise_rate"),
        "document_relevance_score": judge_aggregate.get("document_relevance_score", retrieval_metrics.get("document_relevance_score")),
        "faithfulness_score": judge_aggregate.get("faithfulness_score"),
        "groundedness_score": judge_aggregate.get("groundedness_score"),
        "response_relevance_score": judge_aggregate.get("response_relevance_score"),
        "response_completeness_score": judge_aggregate.get("response_completeness_score"),
        "citation_correctness_score": judge_aggregate.get("citation_correctness_score"),
        "answer_accuracy_score": judge_aggregate.get("answer_accuracy_score") if _answer_correctness_eligible(case) else None,
        "answer_logic_score": judge_aggregate.get("answer_logic_score"),
        "hallucination_flag": judge_aggregate.get("hallucination_flag"),
        "needs_human": (
            judge_aggregate.get("needs_human")
            if judge_aggregate.get("needs_human") is not None
            else execution_result.needs_human
        ),
        "answer_correctness_eligible": _answer_correctness_eligible(case),
        "matched_expected_execution_action": matched_expected_execution_action,
        "used_prohibited_agora_docs": used_prohibited_agora_docs,
        "abstained_or_deflected_properly": abstained_or_deflected_properly,
        "no_unsupported_claims": no_unsupported_claims,
        "response_policy_followed": response_policy_followed,
        "authoritative_source_used": (
            citations_use_authoritative_source(execution_result.citations, sources=execution_result.sources)
            if decision.execution_action == "web_search"
            else None
        ),
        "citation_present": bool(execution_result.citations),
        "unsupported_claim_avoidance": no_unsupported_claims,
        "route_correct_flag": execution_result.actual_route == case.expected_route if case.route_aware else True,
        "judge_votes": judge_votes,
        "judge_disagreement_flag": bool(judge_aggregate.get("judge_disagreement_flag")),
        "answer_preview": _clean_text(execution_result.answer_text)[:280],
        "reference_answer": case.reference_answer,
        "expected_document_ids": case.expected_document_ids,
        "expected_heading_paths": case.expected_heading_paths,
        "expected_evidence_refs": case.expected_evidence_refs,
        "answer_key_points": case.answer_key_points,
        "trace_payload": trace_payload,
        "retrieval_latency_ms": rag_result.trace.retrieval_latency_ms if rag_result is not None else None,
        "generation_latency_ms": rag_result.trace.generation_latency_ms if rag_result is not None else None,
        "total_latency_ms": rag_result.trace.total_latency_ms if rag_result is not None else None,
        "selected_doc_count": rag_result.trace.selected_doc_count if rag_result is not None else None,
        "top1_similarity_score": rag_result.trace.top1_similarity_score if rag_result is not None else None,
        "avg_selected_similarity_score": rag_result.trace.avg_selected_similarity_score if rag_result is not None else None,
        "avg_cost_per_query": _estimate_query_cost(rag_result) if rag_result is not None else None,
        "failure_stage": failure_stage,
        "failure_bucket": failure_bucket,
    }
    row["failure_type"] = _failure_type(
        execution_result=execution_result,
        retrieval_metrics=retrieval_metrics,
        judge_aggregate=row,
        case=case,
    )
    row["root_cause_label"] = _root_cause_label(
        case=case,
        execution_result=execution_result,
        retrieval_metrics=retrieval_metrics,
        judge_aggregate=row,
    )
    return row


def run_benchmark(
    *,
    dataset_path: str | Path | None = None,
    dataset_id: str | None = None,
    dataset_tier: str = "gold",
    experiment_id: str | None = None,
    benchmark_session_id: str | None = None,
    limit: int | None = None,
    repository: "KnowledgeRepository" | None = None,
    query_runner: Callable[[str, int | None], RagQueryResult | None] | None = None,
    judge_runner: Callable[..., dict[str, Any]] | None = None,
    route_decider: Callable[..., SupportRouteDecision | dict[str, Any]] | None = None,
    message_resolver: Callable[..., Any] | None = None,
    top_k: int | None = None,
    eval_run_id: str | None = None,
    initialize_repository: bool = True,
) -> dict[str, Any]:
    repo = repository or _create_repository()
    if initialize_repository:
        _prepare_repository_for_benchmark(repo)
    if dataset_path is None and not _clean_text(dataset_id):
        raise ValueError("dataset_path or dataset_id is required")
    dataset_name: str
    benchmark_version: str
    if _clean_text(dataset_id):
        snapshot = repo.get_dataset_snapshot(_clean_text(dataset_id))
        if snapshot is None:
            raise ValueError(f"Dataset snapshot not found: {_clean_text(dataset_id)}")
        cases = parse_benchmark_cases(
            repo.load_dataset_benchmark_cases(_clean_text(dataset_id), tier=dataset_tier),
            source_label=f"dataset:{_clean_text(dataset_id)}:{dataset_tier}",
        )
        dataset_name = _clean_text(snapshot.get("dataset_name")) or _clean_text(dataset_id)
        benchmark_version = _clean_text(snapshot.get("benchmark_version")) or _clean_text(dataset_id)
    else:
        assert dataset_path is not None
        cases = load_benchmark_cases(dataset_path)
        benchmark_version = Path(dataset_path).stem
        dataset_name = Path(dataset_path).name
    if limit is not None:
        cases = cases[: max(0, int(limit))]
    if not cases:
        raise ValueError("No benchmark cases to run")

    judge_models = resolve_judge_models()
    dataset_schema_version = cases[0].dataset_schema_version if len({case.dataset_schema_version for case in cases}) == 1 else "mixed"
    eval_run_id = _clean_text(eval_run_id) or f"EVAL-{uuid4().hex[:12].upper()}"
    started_at = _utc_now()
    normalized_experiment_id = _clean_text(experiment_id) or eval_run_id
    runner = query_runner or run_rag_query
    judge = judge_runner or invoke_judge_vote
    decider = route_decider or decide_support_route

    repo.upsert_rag_eval_run(
        eval_run={
            "eval_run_id": eval_run_id,
            "dataset_name": dataset_name,
            "eval_type": "offline_benchmark",
            "experiment_id": normalized_experiment_id,
            "benchmark_session_id": _clean_text(benchmark_session_id),
            "strategy_snapshot": _strategy_snapshot(judge_models),
            "judge_models": judge_models,
            "benchmark_version": benchmark_version,
            "dataset_schema_version": dataset_schema_version,
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
        }
    )

    result_rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            if case.route_aware:
                execution_result = _execute_case(
                    case=case,
                    runner=runner,
                    top_k=top_k,
                    message_resolver=message_resolver,
                    route_decider=route_decider,
                )
                decision = _decision_from_execution_result(case, execution_result)
            else:
                if case.dataset_schema_version == "legacy_rag_v1" and route_decider is None:
                    decision = _expected_route_decision(case)
                else:
                    decision = _coerce_route_decision(
                        decider(case.question, ticket_subject=None, ticket_context=None)
                    )
                if decision.execution_action == "rag":
                    result = runner(case.question, top_k=top_k)
                    if result is None:
                        raise RuntimeError("run_rag_query returned None; verify RAG configuration before running the benchmark")
                    execution_result = _wrap_rag_result(case, result)
                else:
                    resolution = resolve_support_message(case.question, decision=decision)
                    synthetic_result = _build_synthetic_result(case=case, resolution=resolution)
                    execution_result = _execution_result_from_resolution(
                        case=case,
                        decision=decision,
                        resolution=resolution,
                        result=synthetic_result,
                    )

            retrieval_metrics = (
                compute_retrieval_metrics(
                    execution_result.rag_result.trace.retrieval_candidates,
                    expected_document_ids=case.expected_document_ids,
                    expected_heading_paths=case.expected_heading_paths,
                    expected_evidence_refs=case.expected_evidence_refs,
                    answer_key_points=case.answer_key_points,
                )
                if case.retrieval_metrics_enabled and execution_result.rag_result is not None
                else _empty_retrieval_metrics()
            )
            judge_votes: list[dict[str, Any]] = []
            judge_vote_cache: dict[str, dict[str, Any]] = {}
            for judge_model in judge_models:
                cached_vote = judge_vote_cache.get(judge_model)
                if cached_vote is None:
                    try:
                        cached_vote = judge(
                            judge_model=judge_model,
                            case=case,
                            result=execution_result,
                            retrieval_metrics=retrieval_metrics,
                        )
                    except Exception as exc:
                        cached_vote = {
                            "judge_model": judge_model,
                            "error": str(exc),
                        }
                    judge_vote_cache[judge_model] = dict(cached_vote)
                else:
                    cached_vote = dict(cached_vote)
                judge_votes.append(cached_vote)
            result_rows.append(
                _build_eval_row(
                    case=case,
                    decision=decision,
                    execution_result=execution_result,
                    judge_votes=judge_votes,
                )
            )

        repo.replace_rag_eval_results(eval_run_id=eval_run_id, rows=result_rows)

        metric_date = datetime.now(timezone.utc).date().isoformat()
        repo.upsert_rag_daily_metric(
            metric_date=metric_date,
            metrics=summarize_eval_daily_metrics(result_rows),
            experiment_id=normalized_experiment_id,
        )
        for group_key, group_rows in _group_results(result_rows).items():
            repo.upsert_rag_daily_metric(
                metric_date=metric_date,
                metrics=summarize_eval_daily_metrics(group_rows),
                source_type=group_key[0],
                query_type=group_key[1],
                retrieval_strategy=group_key[2],
                chunk_strategy=group_key[3],
                experiment_id=normalized_experiment_id,
            )

        repo.upsert_rag_eval_run(
            eval_run={
                "eval_run_id": eval_run_id,
                "dataset_name": dataset_name,
                "eval_type": "offline_benchmark",
                "experiment_id": normalized_experiment_id,
                "benchmark_session_id": _clean_text(benchmark_session_id),
                "strategy_snapshot": _strategy_snapshot(judge_models),
                "judge_models": judge_models,
                "benchmark_version": benchmark_version,
                "dataset_schema_version": dataset_schema_version,
                "status": "completed",
                "started_at": started_at,
                "finished_at": _utc_now(),
            }
        )
    except Exception:
        repo.upsert_rag_eval_run(
            eval_run={
                "eval_run_id": eval_run_id,
                "dataset_name": dataset_name,
                "eval_type": "offline_benchmark",
                "experiment_id": normalized_experiment_id,
                "benchmark_session_id": _clean_text(benchmark_session_id),
                "strategy_snapshot": _strategy_snapshot(judge_models),
                "judge_models": judge_models,
                "benchmark_version": benchmark_version,
                "dataset_schema_version": dataset_schema_version,
                "status": "failed",
                "started_at": started_at,
                "finished_at": _utc_now(),
            }
        )
        raise

    return {
        "eval_run_id": eval_run_id,
        "benchmark_session_id": _clean_text(benchmark_session_id) or None,
        "dataset_name": dataset_name,
        "benchmark_version": benchmark_version,
        "dataset_schema_version": dataset_schema_version,
        "judge_models": judge_models,
        "case_count": len(result_rows),
        "metrics": summarize_eval_daily_metrics(result_rows),
    }
