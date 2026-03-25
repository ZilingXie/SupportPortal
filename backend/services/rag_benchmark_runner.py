from __future__ import annotations

import json
import os
from collections import defaultdict
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
from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY, RagQueryResult, run_rag_query

if TYPE_CHECKING:
    from backend.repositories.knowledge_repository import KnowledgeRepository


_MODEL_PRICING = {
    "gpt-4.1": {"prompt_per_1k": 0.002, "completion_per_1k": 0.008},
    "gpt-4.1-mini": {"prompt_per_1k": 0.0004, "completion_per_1k": 0.0016},
    "gpt-4o-mini": {"prompt_per_1k": 0.00015, "completion_per_1k": 0.0006},
}


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


def _strategy_snapshot(judge_models: list[str]) -> dict[str, Any]:
    return {
        "embedding_provider": embedding_provider_name(),
        "embedding_model": embedding_model_id(),
        "vector_table": _vector_table_name(),
        "rag_top_k": _clean_text(os.getenv("RAG_TOP_K")) or None,
        "chat_model": _clean_text(os.getenv("OPENAI_CHAT_MODEL")) or "gpt-4.1",
        "reranker_model": _clean_text(os.getenv("RAG_RERANK_MODEL")),
        "query_rewrite_enabled": (_clean_text(os.getenv("RAG_QUERY_REWRITE_ENABLED")) or "").lower()
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
    result: RagQueryResult,
    retrieval_metrics: dict[str, Any],
    judge_aggregate: dict[str, Any],
) -> str:
    if retrieval_metrics.get("hit_at_5") == 0.0:
        return "retrieval_miss"
    if int(result.trace.selected_doc_count or 0) <= 0:
        return "weak_context_selection"
    if result.trace.top1_similarity_score is not None and float(result.trace.top1_similarity_score or 0.0) < 0.5:
        return "bad_chunking"
    if judge_aggregate.get("hallucination_flag") is True:
        return "generation_hallucination"
    if (judge_aggregate.get("citation_correctness_score") or 1.0) < 0.70 or int(result.trace.citation_count or 0) == 0:
        return "citation_issue"
    if (judge_aggregate.get("needs_human") is True or result.trace.needs_human) and not case.expected_handoff:
        return "unnecessary_handoff"
    return "grounded_answer"


def _build_trace_payload(
    *,
    case: BenchmarkCase,
    result: RagQueryResult,
    retrieval_metrics: dict[str, Any],
    judge_votes: list[dict[str, Any]],
) -> dict[str, Any]:
    trace = result.trace
    answer = result.answer
    missed_expected_docs = _missed_expected_docs(
        case=case,
        retrieval_candidates=trace.retrieval_candidates,
    )
    return {
        "question": case.question,
        "answer_text": answer.answer,
        "answer_preview": _clean_text(answer.answer)[:280],
        "query_type": case.query_type or trace.query_type,
        "source_type": case.source_type or trace.primary_source_type,
        "product": case.product,
        "language": case.language,
        "expected_document_ids": case.expected_document_ids,
        "expected_heading_paths": case.expected_heading_paths,
        "expected_evidence_refs": case.expected_evidence_refs,
        "missed_expected_docs": missed_expected_docs,
        "retrieval_metrics": retrieval_metrics,
        "generation_mode": trace.generation_mode,
        "needs_human": trace.needs_human,
        "handoff_reason": trace.handoff_reason,
        "confidence_score": trace.confidence_score,
        "citation_count": trace.citation_count,
        "citation_coverage_ratio": trace.citation_coverage_ratio,
        "cited_chunk_ids": trace.cited_chunk_ids,
        "structured_retry_used": trace.structured_retry_used,
        "extractive_fallback_used": trace.extractive_fallback_used,
        "selected_doc_count": trace.selected_doc_count,
        "top1_similarity_score": trace.top1_similarity_score,
        "avg_selected_similarity_score": trace.avg_selected_similarity_score,
        "vector_candidates_count": trace.vector_candidates_count,
        "bm25_candidates_count": trace.bm25_candidates_count,
        "reranked_candidates_count": trace.reranked_candidates_count,
        "retrieval_candidates": trace.retrieval_candidates,
        "selected_contexts": trace.selected_contexts,
        "latency_ms": {
            "retrieval": trace.retrieval_latency_ms,
            "generation": trace.generation_latency_ms,
            "total": trace.total_latency_ms,
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
    result: RagQueryResult,
    retrieval_metrics: dict[str, Any],
) -> dict[str, Any]:
    api_key = _clean_text(os.getenv("OPENAI_API_KEY"))
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for benchmark judges")

    from langchain_openai import ChatOpenAI

    answer = result.answer
    trace = result.trace
    llm = ChatOpenAI(
        model=judge_model,
        temperature=0,
        api_key=api_key,
        request_timeout=float(os.getenv("RAG_BENCHMARK_JUDGE_TIMEOUT_SECONDS") or 30.0),
        max_retries=1,
    )
    system_prompt = """You are grading a technical support RAG answer.

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
- Judge only from the provided question, expected answer key points, expected handoff, retrieved candidates, selected context, final answer, and citations.
- Mark hallucination_flag true if the answer includes claims not supported by the selected context.
- failure_type should be one of: retrieval_miss, hallucination, incomplete_answer, bad_citation, handoff_needed, grounded_answer.
"""
    user_prompt = json.dumps(
        {
            "test_case_id": case.test_case_id,
            "question": case.question,
            "expected_document_ids": case.expected_document_ids,
            "expected_heading_paths": case.expected_heading_paths,
            "expected_evidence_refs": case.expected_evidence_refs,
            "answer_key_points": case.answer_key_points,
            "expected_handoff": case.expected_handoff,
            "retrieval_metrics": retrieval_metrics,
            "retrieval_candidates": trace.retrieval_candidates,
            "selected_contexts": trace.selected_contexts,
            "answer": answer.answer,
            "citations": answer.citations,
            "generation_mode": trace.generation_mode,
            "needs_human": trace.needs_human,
        },
        ensure_ascii=False,
    )
    response = llm.invoke([("system", system_prompt), ("user", user_prompt)])
    payload = _extract_json_payload(_response_to_text(response))
    if payload is None:
        raise ValueError(f"Judge {judge_model} returned invalid JSON")
    payload["judge_model"] = judge_model
    return payload


def _failure_type(
    *,
    result: RagQueryResult,
    retrieval_metrics: dict[str, Any],
    judge_aggregate: dict[str, Any],
    case: BenchmarkCase,
) -> str:
    answer_text = _clean_text(result.answer.answer)
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
    result: RagQueryResult,
    judge_votes: list[dict[str, Any]],
) -> dict[str, Any]:
    retrieval_metrics = compute_retrieval_metrics(
        result.trace.retrieval_candidates,
        expected_document_ids=case.expected_document_ids,
        expected_heading_paths=case.expected_heading_paths,
        expected_evidence_refs=case.expected_evidence_refs,
    )
    judge_aggregate = aggregate_judge_votes(judge_votes)
    trace_payload = _build_trace_payload(
        case=case,
        result=result,
        retrieval_metrics=retrieval_metrics,
        judge_votes=judge_votes,
    )
    row = {
        "test_case_id": case.test_case_id,
        "question": case.question,
        "query_type": case.query_type or result.trace.query_type,
        "source_type": case.source_type or result.trace.primary_source_type,
        "product": case.product,
        "language": case.language,
        "chunk_strategy": result.trace.primary_chunk_strategy,
        "retrieval_strategy": result.trace.retrieval_strategy,
        "hit_at_1": retrieval_metrics.get("hit_at_1"),
        "hit_at_3": retrieval_metrics.get("hit_at_3"),
        "hit_at_5": retrieval_metrics.get("hit_at_5"),
        "recall_at_5": retrieval_metrics.get("recall_at_5"),
        "mrr": retrieval_metrics.get("mrr"),
        "ndcg_at_5": retrieval_metrics.get("ndcg_at_5"),
        "evidence_hit_at_1": retrieval_metrics.get("evidence_hit_at_1"),
        "evidence_hit_at_3": retrieval_metrics.get("evidence_hit_at_3"),
        "evidence_hit_at_5": retrieval_metrics.get("evidence_hit_at_5"),
        "document_relevance_score": judge_aggregate.get("document_relevance_score", retrieval_metrics.get("document_relevance_score")),
        "faithfulness_score": judge_aggregate.get("faithfulness_score"),
        "groundedness_score": judge_aggregate.get("groundedness_score"),
        "response_relevance_score": judge_aggregate.get("response_relevance_score"),
        "response_completeness_score": judge_aggregate.get("response_completeness_score"),
        "citation_correctness_score": judge_aggregate.get("citation_correctness_score"),
        "answer_accuracy_score": judge_aggregate.get("answer_accuracy_score"),
        "answer_logic_score": judge_aggregate.get("answer_logic_score"),
        "hallucination_flag": judge_aggregate.get("hallucination_flag"),
        "needs_human": (
            judge_aggregate.get("needs_human")
            if judge_aggregate.get("needs_human") is not None
            else result.trace.needs_human
        ),
        "judge_votes": judge_votes,
        "judge_disagreement_flag": bool(judge_aggregate.get("judge_disagreement_flag")),
        "answer_preview": _clean_text(result.answer.answer)[:280],
        "expected_document_ids": case.expected_document_ids,
        "expected_heading_paths": case.expected_heading_paths,
        "expected_evidence_refs": case.expected_evidence_refs,
        "trace_payload": trace_payload,
        "retrieval_latency_ms": result.trace.retrieval_latency_ms,
        "generation_latency_ms": result.trace.generation_latency_ms,
        "total_latency_ms": result.trace.total_latency_ms,
        "selected_doc_count": result.trace.selected_doc_count,
        "top1_similarity_score": result.trace.top1_similarity_score,
        "avg_selected_similarity_score": result.trace.avg_selected_similarity_score,
        "avg_cost_per_query": _estimate_query_cost(result),
    }
    row["failure_type"] = _failure_type(
        result=result,
        retrieval_metrics=retrieval_metrics,
        judge_aggregate=row,
        case=case,
    )
    row["root_cause_label"] = _root_cause_label(
        case=case,
        result=result,
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
    limit: int | None = None,
    repository: "KnowledgeRepository" | None = None,
    query_runner: Callable[[str, int | None], RagQueryResult | None] | None = None,
    judge_runner: Callable[..., dict[str, Any]] | None = None,
    top_k: int | None = None,
    eval_run_id: str | None = None,
) -> dict[str, Any]:
    repo = repository or _create_repository()
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
    eval_run_id = _clean_text(eval_run_id) or f"EVAL-{uuid4().hex[:12].upper()}"
    started_at = _utc_now()
    normalized_experiment_id = _clean_text(experiment_id) or eval_run_id
    runner = query_runner or run_rag_query
    judge = judge_runner or invoke_judge_vote

    repo.upsert_rag_eval_run(
        eval_run={
            "eval_run_id": eval_run_id,
            "dataset_name": dataset_name,
            "eval_type": "offline_benchmark",
            "experiment_id": normalized_experiment_id,
            "strategy_snapshot": _strategy_snapshot(judge_models),
            "judge_models": judge_models,
            "benchmark_version": benchmark_version,
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
        }
    )

    result_rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            result = runner(case.question, top_k=top_k)
            if result is None:
                raise RuntimeError("run_rag_query returned None; verify RAG configuration before running the benchmark")
            judge_votes = []
            retrieval_metrics = compute_retrieval_metrics(
                result.trace.retrieval_candidates,
                expected_document_ids=case.expected_document_ids,
                expected_heading_paths=case.expected_heading_paths,
                expected_evidence_refs=case.expected_evidence_refs,
            )
            for judge_model in judge_models:
                try:
                    judge_votes.append(
                        judge(
                            judge_model=judge_model,
                            case=case,
                            result=result,
                            retrieval_metrics=retrieval_metrics,
                        )
                    )
                except Exception as exc:
                    judge_votes.append(
                        {
                            "judge_model": judge_model,
                            "error": str(exc),
                        }
                    )
            result_rows.append(_build_eval_row(case=case, result=result, judge_votes=judge_votes))

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
                "strategy_snapshot": _strategy_snapshot(judge_models),
                "judge_models": judge_models,
                "benchmark_version": benchmark_version,
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
                "strategy_snapshot": _strategy_snapshot(judge_models),
                "judge_models": judge_models,
                "benchmark_version": benchmark_version,
                "status": "failed",
                "started_at": started_at,
                "finished_at": _utc_now(),
            }
        )
        raise

    return {
        "eval_run_id": eval_run_id,
        "dataset_name": dataset_name,
        "benchmark_version": benchmark_version,
        "judge_models": judge_models,
        "case_count": len(result_rows),
        "metrics": summarize_eval_daily_metrics(result_rows),
    }
