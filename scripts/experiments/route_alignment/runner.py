#!/usr/bin/env python3
"""Run the read-only route comparison experiment from a fixture or Production DB."""

from __future__ import annotations

import argparse
import json
import math
import os
import uuid
from pathlib import Path
from typing import Any

from .adapters import fetch_production_snapshots, fixture_candidate, http_candidate, load_fixture_snapshots
from .core import compare_case, normalize_classification, result_to_dict, review_context_record, snapshot_manifest_record, write_disagreement_csv, write_jsonl


def _latency_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"count": 0, "p50": None, "p95": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "p50": ordered[(len(ordered) - 1) // 2],
        "p95": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)],
    }


def _field_agreement(results: list[Any]) -> dict[str, dict[str, int]]:
    from .core import COMPARISON_FIELDS

    totals = {field: {"compared": 0, "agreed": 0} for field in COMPARISON_FIELDS}
    for result in results:
        if result.baseline_status != "available":
            continue
        for candidate in result.candidates.values():
            if candidate.status != "ok" or candidate.normalized is None:
                continue
            for field in COMPARISON_FIELDS:
                totals[field]["compared"] += 1
                if result.baseline.get(field) == candidate.normalized.get(field):
                    totals[field]["agreed"] += 1
    return totals


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", type=Path, help="Redacted JSONL fixture; no DB or HTTP calls.")
    source.add_argument("--production-dsn", help="Read-only Production DSN; never written by this command.")
    parser.add_argument("--schema", default="supportportal_production")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--jev-endpoint", help="Opt-in candidate endpoint.")
    parser.add_argument("--hermes-endpoint", help="Opt-in classification-only candidate endpoint.")
    parser.add_argument("--fixture-candidates", type=Path, help="JSON object: {\"jev\": {alias: classification}, ...}")
    parser.add_argument("--live-candidates", action="store_true", help="Required to enable HTTP candidate calls.")
    parser.add_argument("--include-review-text", action="store_true", help="Write a redacted review_context.jsonl only with explicit approval.")
    return parser


def _load_candidate_fixtures(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def _candidate_headers(env_name: str) -> dict[str, str]:
    token = os.getenv(env_name, "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit <= 0 or args.limit > 100:
        raise SystemExit("--limit must be between 1 and 100")
    if args.live_candidates and not (args.jev_endpoint or args.hermes_endpoint):
        raise SystemExit("--live-candidates requires at least one endpoint")
    if (args.jev_endpoint or args.hermes_endpoint) and not args.live_candidates:
        raise SystemExit("candidate endpoints require --live-candidates")
    if args.live_candidates and not (args.jev_endpoint and args.hermes_endpoint):
        raise SystemExit("both --jev-endpoint and --hermes-endpoint are required")
    if args.live_candidates and os.getenv("ROUTE_EXPERIMENT_DATA_PROCESSING_APPROVED") != "1":
        raise SystemExit("live candidates require ROUTE_EXPERIMENT_DATA_PROCESSING_APPROVED=1")
    if args.include_review_text and os.getenv("ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED") != "1":
        raise SystemExit("review text requires ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED=1")

    snapshots = load_fixture_snapshots(str(args.fixture)) if args.fixture else fetch_production_snapshots(dsn=args.production_dsn, schema=args.schema, limit=args.limit)
    if len(snapshots) > args.limit:
        snapshots = snapshots[: args.limit]
    if args.production_dsn and len(snapshots) != args.limit:
        raise SystemExit(f"expected {args.limit} Production snapshots, got {len(snapshots)}")
    if not snapshots:
        raise SystemExit("no snapshots available")

    fixture_data = _load_candidate_fixtures(args.fixture_candidates)
    if not args.live_candidates and not (isinstance(fixture_data.get("jev"), dict) and isinstance(fixture_data.get("hermes"), dict)):
        raise SystemExit("fixture candidates must include both jev and hermes mappings")
    candidates = []
    if args.jev_endpoint:
        candidates.append(http_candidate("jev", args.jev_endpoint, headers=_candidate_headers("JEV_EXPERIMENT_TOKEN")))
    elif isinstance(fixture_data.get("jev"), dict):
        candidates.append(fixture_candidate("jev", fixture_data["jev"]))
    if args.hermes_endpoint:
        candidates.append(http_candidate("hermes", args.hermes_endpoint, headers=_candidate_headers("HERMES_EXPERIMENT_TOKEN")))
    elif isinstance(fixture_data.get("hermes"), dict):
        candidates.append(fixture_candidate("hermes", fixture_data["hermes"]))
    if not candidates:
        raise SystemExit("configure --fixture-candidates or an explicit live endpoint")

    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise SystemExit("output directory must be new or empty; refusing to overwrite existing artifacts")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    write_jsonl(args.output_dir / "manifest.jsonl", (snapshot_manifest_record(item, run_id=run_id) for item in snapshots))
    if args.include_review_text:
        write_jsonl(args.output_dir / "review_context.jsonl", (review_context_record(item, run_id=run_id) for item in snapshots))
    results = []
    raw_records = []
    for snapshot in snapshots:
        candidate_results = [candidate(snapshot) for candidate in candidates]
        result = compare_case(snapshot, candidate_results)
        results.append(result)
        raw_records.append(
            {
                "run_id": run_id,
                "case_alias": snapshot.alias,
                "candidates": [
                    {
                        "candidate": item.candidate,
                        "status": item.status,
                        "raw_classification": normalize_classification(item.raw) if item.raw else None,
                        "error": item.error,
                        "latency_ms": item.latency_ms,
                        "model_version": item.model_version,
                        "prompt_version": item.prompt_version,
                    }
                    for item in candidate_results
                ],
            }
        )
    write_jsonl(args.output_dir / "raw_results.jsonl", raw_records)
    write_jsonl(args.output_dir / "normalized_comparison.jsonl", (result_to_dict(item, run_id=run_id) for item in results))
    disagreement_filename = f"disagreement_report.{run_id}.csv"
    write_disagreement_csv(args.output_dir / disagreement_filename, results, run_id=run_id)
    summary = {
        "run_id": run_id,
        "experiment": "route-alignment-v1",
        "artifacts": {"disagreement_report": disagreement_filename},
        "case_count": len(results),
        "review_required_count": sum(item.review_required for item in results),
        "candidate_error_count": sum(1 for item in results for candidate in item.candidates.values() if candidate.status != "ok"),
        "disagreement_count_by_candidate": {
            name: sum(name in item.disagreement_fields for item in results)
            for name in {name for item in results for name in item.candidates}
        },
        "live_candidates": bool(args.live_candidates),
        "candidate_names": sorted({name for item in results for name in item.candidates}),
        "latency_ms": {
            name: _latency_summary(
                [candidate.latency_ms for item in results for candidate in item.candidates.values() if candidate.candidate == name and candidate.latency_ms is not None]
            )
            for name in {candidate.candidate for item in results for candidate in item.candidates.values()}
        },
        "model_versions": {
            name: sorted({candidate.model_version for item in results for candidate in item.candidates.values() if candidate.candidate == name and candidate.model_version})
            for name in {candidate.candidate for item in results for candidate in item.candidates.values()}
        },
        "prompt_versions": {
            name: sorted({candidate.prompt_version for item in results for candidate in item.candidates.values() if candidate.candidate == name and candidate.prompt_version})
            for name in {candidate.candidate for item in results for candidate in item.candidates.values()}
        },
        "field_agreement": _field_agreement(results),
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
