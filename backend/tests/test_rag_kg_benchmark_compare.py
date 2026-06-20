from __future__ import annotations

from backend.services.rag_kg_benchmark_compare import build_rag_vs_kg_comparison_report


def test_build_rag_vs_kg_comparison_report_gates_on_quality_latency_and_degrade() -> None:
    pure_rag = {
        "eval_run_id": "EVAL-PURE",
        "case_count": 50,
        "metrics": {
            "evidence_hit_at_5": 0.70,
            "citation_correctness_score": 0.92,
            "faithfulness_score": 0.91,
            "answer_accuracy_score": 0.86,
            "total_latency_ms_p95": 1100.0,
        },
    }
    rag_plus_kg = {
        "eval_run_id": "EVAL-KG",
        "case_count": 50,
        "metrics": {
            "evidence_hit_at_5": 0.78,
            "citation_correctness_score": 0.92,
            "faithfulness_score": 0.93,
            "answer_accuracy_score": 0.89,
            "total_latency_ms_p95": 1240.0,
            "kg_degrade_rate": 0.04,
            "kg_contribution_rate": 0.42,
        },
    }

    report = build_rag_vs_kg_comparison_report(
        pure_rag_summary=pure_rag,
        rag_plus_kg_summary=rag_plus_kg,
        max_latency_regression_ms=200.0,
        max_kg_degrade_rate=0.10,
    )

    assert report["mode"] == "rag_vs_rag_plus_kg"
    assert report["case_count"] == 50
    assert report["pure_rag_eval_run_id"] == "EVAL-PURE"
    assert report["rag_plus_kg_eval_run_id"] == "EVAL-KG"
    assert report["deltas"]["evidence_hit_at_5"] == 0.08
    assert report["deltas"]["total_latency_ms_p95"] == 140.0
    assert report["kg"]["degrade_rate"] == 0.04
    assert report["kg"]["contribution_rate"] == 0.42
    assert report["gate"]["passed"] is True
    assert report["gate"]["reasons"] == []


def test_build_rag_vs_kg_comparison_report_blocks_faithfulness_regression() -> None:
    report = build_rag_vs_kg_comparison_report(
        pure_rag_summary={
            "eval_run_id": "EVAL-PURE",
            "case_count": 10,
            "metrics": {
                "citation_correctness_score": 0.95,
                "faithfulness_score": 0.94,
                "total_latency_ms_p95": 1000.0,
            },
        },
        rag_plus_kg_summary={
            "eval_run_id": "EVAL-KG",
            "case_count": 10,
            "metrics": {
                "citation_correctness_score": 0.91,
                "faithfulness_score": 0.90,
                "total_latency_ms_p95": 1005.0,
                "kg_degrade_rate": 0.02,
            },
        },
    )

    assert report["gate"]["passed"] is False
    assert "citation_correctness_regressed" in report["gate"]["reasons"]
    assert "faithfulness_regressed" in report["gate"]["reasons"]


def test_build_rag_vs_kg_comparison_report_accepts_benchmark_p95_latency_metric() -> None:
    report = build_rag_vs_kg_comparison_report(
        pure_rag_summary={
            "eval_run_id": "EVAL-PURE",
            "case_count": 2,
            "metrics": {"benchmark_p95_total_latency_ms": 900.0},
        },
        rag_plus_kg_summary={
            "eval_run_id": "EVAL-KG",
            "case_count": 2,
            "metrics": {
                "benchmark_p95_total_latency_ms": 980.0,
                "kg_degrade_rate": 0.0,
            },
        },
    )

    assert report["deltas"]["total_latency_ms_p95"] == 80.0
    assert report["rag_plus_kg_metrics"]["total_latency_ms_p95"] == 980.0
