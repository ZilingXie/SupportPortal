from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from backend.services.embedding_provider import DEFAULT_PGVECTOR_TABLE, embedding_model_id, embedding_provider_name
from backend.services.rag_benchmark import (
    DEFAULT_JUDGE_MODELS,
    BenchmarkCase,
    aggregate_judge_votes,
    compute_retrieval_metrics,
    load_benchmark_cases,
    summarize_eval_daily_metrics,
)
from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY, RagQueryResult, run_rag_query

if TYPE_CHECKING:
    from backend.repositories.knowledge_repository import KnowledgeRepository


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _create_repository() -> "KnowledgeRepository":
    from backend.repositories.knowledge_repository import create_knowledge_repository

    return create_knowledge_repository()


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
        "judge_models": judge_models,
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
    )
    judge_aggregate = aggregate_judge_votes(judge_votes)
    row = {
        "test_case_id": case.test_case_id,
        "question": case.question,
        "query_type": case.query_type or result.trace.query_type,
        "source_type": case.source_type or result.trace.primary_source_type,
        "chunk_strategy": result.trace.primary_chunk_strategy,
        "retrieval_strategy": result.trace.retrieval_strategy,
        "hit_at_1": retrieval_metrics.get("hit_at_1"),
        "hit_at_3": retrieval_metrics.get("hit_at_3"),
        "hit_at_5": retrieval_metrics.get("hit_at_5"),
        "recall_at_5": retrieval_metrics.get("recall_at_5"),
        "mrr": retrieval_metrics.get("mrr"),
        "ndcg_at_5": retrieval_metrics.get("ndcg_at_5"),
        "document_relevance_score": judge_aggregate.get("document_relevance_score", retrieval_metrics.get("document_relevance_score")),
        "faithfulness_score": judge_aggregate.get("faithfulness_score"),
        "groundedness_score": judge_aggregate.get("groundedness_score"),
        "response_relevance_score": judge_aggregate.get("response_relevance_score"),
        "response_completeness_score": judge_aggregate.get("response_completeness_score"),
        "citation_correctness_score": judge_aggregate.get("citation_correctness_score"),
        "hallucination_flag": judge_aggregate.get("hallucination_flag"),
        "needs_human": (
            judge_aggregate.get("needs_human")
            if judge_aggregate.get("needs_human") is not None
            else result.trace.needs_human
        ),
        "judge_votes": judge_votes,
        "judge_disagreement_flag": bool(judge_aggregate.get("judge_disagreement_flag")),
        "answer_preview": _clean_text(result.answer.answer)[:280],
        "retrieval_latency_ms": result.trace.retrieval_latency_ms,
        "generation_latency_ms": result.trace.generation_latency_ms,
        "total_latency_ms": result.trace.total_latency_ms,
    }
    row["failure_type"] = _failure_type(
        result=result,
        retrieval_metrics=retrieval_metrics,
        judge_aggregate=row,
        case=case,
    )
    return row


def run_benchmark(
    *,
    dataset_path: str | Path,
    experiment_id: str | None = None,
    limit: int | None = None,
    repository: "KnowledgeRepository" | None = None,
    query_runner: Callable[[str, int | None], RagQueryResult | None] | None = None,
    judge_runner: Callable[..., dict[str, Any]] | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    repo = repository or _create_repository()
    repo.initialize()
    cases = load_benchmark_cases(dataset_path)
    if limit is not None:
        cases = cases[: max(0, int(limit))]
    if not cases:
        raise ValueError("No benchmark cases to run")

    judge_models = resolve_judge_models()
    benchmark_version = Path(dataset_path).stem
    dataset_name = Path(dataset_path).name
    eval_run_id = f"EVAL-{uuid4().hex[:12].upper()}"
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
