#!/usr/bin/env python3
"""Freeze or run the read-only route alignment experiment."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from .adapters import fetch_production_snapshots, fixture_candidate, http_candidate, load_fixture_snapshots
from .core import CandidateResult, CaseSnapshot, compare_case, result_to_dict, review_context_record, snapshot_manifest_record, write_disagreement_csv, write_jsonl
from .dataset import dataset_id_for, load_frozen_dataset, write_frozen_dataset


EXPERIMENT_VERSION = "route-alignment-v2"
CONFIDENCE_THRESHOLD = 0.7


def _latency_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "p50": None, "p95": None}
    ordered = sorted(values)
    return {"count": len(ordered), "p50": ordered[(len(ordered) - 1) // 2], "p95": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]}


def _field_agreement(results: list[Any], snapshots: list[CaseSnapshot]) -> dict[str, Any]:
    from .core import COMPARISON_FIELDS

    alignment = {item.alias: item.metadata.get("baseline_input_alignment", "unknown") for item in snapshots}
    output: dict[str, Any] = {}
    for name in sorted({name for item in results for name in item.candidates}):
        scopes = {
            "historical_label": {field: {"compared": 0, "agreed": 0} for field in COMPARISON_FIELDS},
            "same_input": {field: {"compared": 0, "agreed": 0} for field in COMPARISON_FIELDS},
        }
        for result in results:
            candidate = result.candidates.get(name)
            if result.baseline_status != "available" or candidate is None or candidate.status != "ok" or candidate.normalized is None:
                continue
            for field in COMPARISON_FIELDS:
                scopes["historical_label"][field]["compared"] += 1
                if result.baseline.get(field) == candidate.normalized.get(field):
                    scopes["historical_label"][field]["agreed"] += 1
                if alignment.get(result.alias) == "matched":
                    scopes["same_input"][field]["compared"] += 1
                    if result.baseline.get(field) == candidate.normalized.get(field):
                        scopes["same_input"][field]["agreed"] += 1
        output[name] = scopes
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--mode", choices=("run", "freeze"), default="run")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", type=Path, help="Redacted JSONL fixture; no DB call.")
    source.add_argument("--frozen-snapshots", type=Path, help="Previously frozen redacted dataset JSONL.")
    source.add_argument("--production-dsn-env", help="Environment variable containing the read-only DSN.")
    parser.add_argument("--schema", default="supportportal_production")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--jev-direct", action="store_true", help="Call the fixed TypeSafe Jev API directly.")
    parser.add_argument("--jev-endpoint", help="Legacy dedicated route-alignment endpoint.")
    parser.add_argument("--hermes-endpoint", help="Loopback classification-only candidate endpoint.")
    parser.add_argument("--fixture-candidates", type=Path, help='JSON object: {"jev": {alias: classification}, ...}')
    parser.add_argument("--live-candidates", action="store_true", help="Required to enable provider-backed candidates.")
    parser.add_argument("--include-review-text", action="store_true", help="Write redacted review_context.jsonl with explicit approval.")
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


def _ensure_output_available(path: Path, *, freeze: bool) -> None:
    if freeze and path.exists():
        raise SystemExit("freeze output directory must not exist")
    if not freeze and path.exists() and any(path.iterdir()):
        raise SystemExit("output directory must be new or empty; refusing to overwrite existing artifacts")


def _validate_threshold() -> None:
    raw = os.getenv("ACCOUNT_ROUTER_CONFIDENCE_THRESHOLD")
    if raw is None:
        os.environ["ACCOUNT_ROUTER_CONFIDENCE_THRESHOLD"] = str(CONFIDENCE_THRESHOLD)
        return
    try:
        valid = math.isclose(float(raw), CONFIDENCE_THRESHOLD, rel_tol=0.0, abs_tol=1e-12)
    except ValueError:
        valid = False
    if not valid:
        raise SystemExit("ACCOUNT_ROUTER_CONFIDENCE_THRESHOLD must be exactly 0.7 for this experiment")


def _preflight(args: argparse.Namespace) -> None:
    if args.limit <= 0 or args.limit > 100:
        raise SystemExit("--limit must be between 1 and 100")
    _ensure_output_available(args.output_dir, freeze=args.mode == "freeze")
    _validate_threshold()
    if args.mode == "freeze":
        if args.frozen_snapshots:
            raise SystemExit("freeze mode requires --fixture or --production-dsn-env")
        if any((args.jev_direct, args.jev_endpoint, args.hermes_endpoint, args.fixture_candidates, args.live_candidates)):
            raise SystemExit("freeze mode does not accept candidate options")
        if os.getenv("ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED") != "1":
            raise SystemExit("freeze mode requires ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED=1")
        if args.production_dsn_env and not os.getenv(args.production_dsn_env, "").strip():
            raise SystemExit(f"read-only DSN environment variable {args.production_dsn_env!r} is missing")
        return
    if args.production_dsn_env:
        raise SystemExit("run mode requires --frozen-snapshots; freeze Production data first")
    if args.include_review_text and os.getenv("ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED") != "1":
        raise SystemExit("review text requires ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED=1")
    fixture_mode = args.fixture_candidates is not None
    live_mode = bool(args.live_candidates)
    if fixture_mode == live_mode:
        raise SystemExit("configure exactly one of --fixture-candidates or --live-candidates")
    if fixture_mode:
        if any((args.jev_direct, args.jev_endpoint, args.hermes_endpoint)):
            raise SystemExit("fixture candidates cannot be combined with live candidate options")
        return
    if os.getenv("ROUTE_EXPERIMENT_DATA_PROCESSING_APPROVED") != "1":
        raise SystemExit("live candidates require ROUTE_EXPERIMENT_DATA_PROCESSING_APPROVED=1")
    if bool(args.jev_direct) == bool(args.jev_endpoint):
        raise SystemExit("live candidates require exactly one of --jev-direct or --jev-endpoint")
    if not args.hermes_endpoint:
        raise SystemExit("live candidates require --hermes-endpoint")
    if args.jev_direct and not os.getenv("TYPESAFE_API_KEY", "").strip():
        raise SystemExit("--jev-direct requires TYPESAFE_API_KEY")
    if not os.getenv("HERMES_EXPERIMENT_TOKEN", "").strip():
        raise SystemExit("live Hermes candidate requires HERMES_EXPERIMENT_TOKEN")


def _source_snapshots(args: argparse.Namespace) -> tuple[str, list[CaseSnapshot]]:
    if args.fixture:
        snapshots = load_fixture_snapshots(str(args.fixture))[: args.limit]
        return dataset_id_for(snapshots), snapshots
    if args.frozen_snapshots:
        dataset_id, snapshots = load_frozen_dataset(args.frozen_snapshots)
        return dataset_id, snapshots[: args.limit]
    dsn = os.getenv(args.production_dsn_env or "", "").strip()
    snapshots = fetch_production_snapshots(dsn=dsn, schema=args.schema, limit=args.limit)
    if len(snapshots) != args.limit:
        raise SystemExit(f"expected {args.limit} Production snapshots, got {len(snapshots)}")
    return dataset_id_for(snapshots), snapshots


def _code_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        return "unverified"


def _candidate_functions(args: argparse.Namespace) -> list[tuple[str, Callable[[CaseSnapshot], CandidateResult]]]:
    fixture_data = _load_candidate_fixtures(args.fixture_candidates)
    if args.fixture_candidates:
        if not (isinstance(fixture_data.get("jev"), dict) and isinstance(fixture_data.get("hermes"), dict)):
            raise SystemExit("fixture candidates must include both jev and hermes mappings")
        return [("jev", fixture_candidate("jev", fixture_data["jev"])), ("hermes", fixture_candidate("hermes", fixture_data["hermes"]))]
    if args.jev_direct:
        from .jev import jev_direct_candidate

        jev = jev_direct_candidate(api_key=os.environ["TYPESAFE_API_KEY"])
    else:
        jev = http_candidate("jev", args.jev_endpoint, headers=_candidate_headers("JEV_EXPERIMENT_TOKEN"))
    hermes = http_candidate("hermes", args.hermes_endpoint, headers=_candidate_headers("HERMES_EXPERIMENT_TOKEN"))
    return [("jev", jev), ("hermes", hermes)]


def _not_run(name: str, reason: str) -> CandidateResult:
    return CandidateResult(candidate=name, status="error", error=reason, error_code=reason, call_count=0)


def _candidate_summary(results: list[Any], name: str) -> dict[str, Any]:
    candidates = [item.candidates[name] for item in results if name in item.candidates]
    input_tokens = sum(int(item.usage.get("input_tokens") or 0) for item in candidates)
    output = {
        "error_count": sum(item.status != "ok" for item in candidates),
        "abstention_count": sum(bool(item.metadata.get("abstentions")) for item in candidates),
        "call_count": sum(item.call_count for item in candidates),
        "latency_ms": _latency_summary([item.latency_ms for item in candidates if item.latency_ms is not None]),
        "requested_models": sorted({item.requested_model for item in candidates if item.requested_model}),
        "returned_models": sorted({item.returned_model for item in candidates if item.returned_model}),
        "usage": {"input_tokens": input_tokens, "output_tokens": sum(int(item.usage.get("output_tokens") or 0) for item in candidates)},
    }
    if name == "jev":
        output["estimated_cost"] = {"currency": "USD", "amount": round(input_tokens * 42 / 1_000_000_000, 10), "basis": "$42 per billion input tokens; output free", "price_checked_at": "2026-09-21", "estimated_not_billed": True}
    return output


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _preflight(args)
    dataset_id, snapshots = _source_snapshots(args)
    if not snapshots:
        raise SystemExit("no snapshots available")
    if args.mode == "freeze":
        write_frozen_dataset(args.output_dir, snapshots)
        return 0

    candidates = _candidate_functions(args)
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.output_dir, 0o700)
    run_id = uuid.uuid4().hex
    write_jsonl(args.output_dir / "manifest.jsonl", (snapshot_manifest_record(item, run_id=run_id, dataset_id=dataset_id) for item in snapshots))
    if args.include_review_text:
        write_jsonl(args.output_dir / "review_context.jsonl", (review_context_record(item, run_id=run_id, dataset_id=dataset_id) for item in snapshots))

    results = []
    evidence_records = []
    abort_reason: str | None = None
    for snapshot in snapshots:
        candidate_results: list[CandidateResult] = []
        for name, invoke in candidates:
            if abort_reason:
                result = _not_run(name, f"not_run_after_{abort_reason}")
            else:
                result = invoke(snapshot)
                result = replace(result, metadata={**result.metadata, "baseline_input_alignment": snapshot.metadata.get("baseline_input_alignment", "unknown")})
                if result.error_code == "authentication_error":
                    abort_reason = "authentication_error"
            candidate_results.append(result)
        comparison = compare_case(snapshot, candidate_results)
        results.append(comparison)
        evidence_records.append({
            "run_id": run_id,
            "dataset_id": dataset_id,
            "case_alias": snapshot.alias,
            "candidates": [{
                "candidate": item.candidate,
                "status": item.status,
                "normalized_classification": item.normalized,
                "error_code": item.error_code,
                "latency_ms": item.latency_ms,
                "requested_model": item.requested_model,
                "returned_model": item.returned_model,
                "prompt_version": item.prompt_version,
                "call_count": item.call_count,
                "usage": item.usage,
                "decision_evidence": item.metadata,
            } for item in candidate_results],
        })

    write_jsonl(args.output_dir / "raw_results.jsonl", evidence_records)
    write_jsonl(args.output_dir / "normalized_comparison.jsonl", (result_to_dict(item, run_id=run_id, dataset_id=dataset_id) for item in results))
    disagreement_filename = f"disagreement_report.{run_id}.csv"
    write_disagreement_csv(args.output_dir / disagreement_filename, results, run_id=run_id, dataset_id=dataset_id)
    candidate_names = sorted({name for item in results for name in item.candidates})
    alignment_counts = {state: sum(item.metadata.get("baseline_input_alignment", "unknown") == state for item in snapshots) for state in ("matched", "mismatch", "unknown")}
    summary = {
        "run_id": run_id,
        "dataset_id": dataset_id,
        "experiment": EXPERIMENT_VERSION,
        "code_commit": _code_commit(),
        "run_status": "failed" if abort_reason else ("completed_with_errors" if any(candidate.status != "ok" for item in results for candidate in item.candidates.values()) else "completed"),
        "abort_reason": abort_reason,
        "artifacts": {"disagreement_report": disagreement_filename},
        "case_count": len(results),
        "review_required_count": sum(item.review_required for item in results),
        "baseline_missing_count": sum(item.baseline_status != "available" for item in results),
        "baseline_input_alignment": alignment_counts,
        "same_input_agreement_available": alignment_counts["matched"] > 0,
        "candidate_names": candidate_names,
        "candidates": {name: _candidate_summary(results, name) for name in candidate_names},
        "field_agreement": _field_agreement(results, snapshots),
        "sampling_note": "stratified experiment sample; not Production prevalence or model accuracy",
    }
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_path.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
