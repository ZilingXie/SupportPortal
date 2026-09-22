from __future__ import annotations

import io
import json
import stat
import threading
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.experiments.route_alignment import runner
from scripts.experiments.route_alignment.core import CandidateResult, build_snapshot


def _row(alias: str = "case-001") -> dict:
    return {
        "case_alias": alias,
        "ticket_id": "13401",
        "case_revision": f"rev-{alias}",
        "subject": "Quota request",
        "messages": [{"role": "user", "content": "Please increase quota."}],
        "route_classification": {
            "pipeline_version": "account-layered-router-v11",
            "intent_class": "agora",
            "agora_route": "backend_operation",
            "backend_operation_subcategory": "quota",
            "route_target": "human_review",
            "route_family": "human_review",
            "execution_action": "human_review_required",
            "automation_eligibility": "not_eligible",
        },
        "metadata": {
            "product": "RTC",
            "status": "open",
            "baseline_input_alignment": "matched",
        },
    }


def _write_fixture(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _write_candidates(path: Path, aliases: list[str]) -> None:
    value = _row()["route_classification"]
    path.write_text(
        json.dumps({name: {alias: value for alias in aliases} for name in ("jev", "hermes")}),
        encoding="utf-8",
    )


def test_freeze_then_fixture_run_preserves_dataset_identity_and_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED", "1")
    fixture = tmp_path / "cases.jsonl"
    _write_fixture(fixture, [_row()])
    frozen_dir = tmp_path / "frozen"

    assert runner.main(["--mode", "freeze", "--fixture", str(fixture), "--output-dir", str(frozen_dir)]) == 0
    frozen = frozen_dir / "frozen_snapshots.jsonl"
    frozen_record = json.loads(frozen.read_text(encoding="utf-8"))

    candidates = tmp_path / "candidates.json"
    _write_candidates(candidates, ["case-001"])
    output = tmp_path / "results"
    assert runner.main([
        "--frozen-snapshots", str(frozen),
        "--fixture-candidates", str(candidates),
        "--output-dir", str(output),
    ]) == 0

    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["dataset_id"] == frozen_record["dataset_id"]
    assert summary["run_status"] == "completed"
    assert summary["same_input_agreement_available"] is True
    assert stat.S_IMODE(frozen_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(frozen.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


def test_output_preflight_happens_before_reading_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "occupied"
    output.mkdir()
    (output / "keep").write_text("unchanged", encoding="utf-8")
    monkeypatch.setattr(runner, "_source_snapshots", lambda _args: pytest.fail("source must not be read"))

    with pytest.raises(SystemExit, match="refusing to overwrite"):
        runner.main([
            "--fixture", str(tmp_path / "missing.jsonl"),
            "--fixture-candidates", str(tmp_path / "missing-candidates.json"),
            "--output-dir", str(output),
        ])
    assert (output / "keep").read_text(encoding="utf-8") == "unchanged"


def test_freeze_reads_dsn_from_named_environment_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED", "1")
    monkeypatch.setenv("ROUTE_TEST_READ_ONLY_DSN", "postgresql://secret-value")
    seen: dict[str, str] = {}

    def fake_fetch(*, dsn: str, schema: str, limit: int):
        seen.update(dsn=dsn, schema=schema, limit=str(limit))
        return [build_snapshot(_row(), alias="case-001")]

    monkeypatch.setattr(runner, "fetch_production_snapshots", fake_fetch)
    output = tmp_path / "frozen"
    assert runner.main([
        "--mode", "freeze",
        "--production-dsn-env", "ROUTE_TEST_READ_ONLY_DSN",
        "--limit", "1",
        "--output-dir", str(output),
    ]) == 0
    assert seen == {"dsn": "postgresql://secret-value", "schema": "supportportal_production", "limit": "1"}
    assert "secret-value" not in "".join(path.read_text(encoding="utf-8") for path in output.iterdir())


def test_cli_does_not_accept_a_raw_dsn_argument() -> None:
    with pytest.raises(SystemExit):
        runner.build_parser().parse_args([
            "--mode", "freeze",
            "--production-dsn", "postgresql://must-not-appear-in-argv",
            "--output-dir", "unused",
        ])


def test_authentication_error_stops_all_later_candidate_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = tmp_path / "cases.jsonl"
    _write_fixture(fixture, [_row("case-001"), _row("case-002")])
    calls: list[tuple[str, str]] = []

    def jev(snapshot):
        calls.append(("jev", snapshot.alias))
        return CandidateResult(candidate="jev", status="error", error="unauthorized", error_code="authentication_error", call_count=1)

    def hermes(snapshot):
        calls.append(("hermes", snapshot.alias))
        return CandidateResult(candidate="hermes", status="ok", normalized=snapshot.baseline, call_count=1)

    monkeypatch.setenv("ROUTE_EXPERIMENT_DATA_PROCESSING_APPROVED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-test-key")
    monkeypatch.setenv("HERMES_EXPERIMENT_TOKEN", "fake-test-token")
    monkeypatch.setattr(runner, "_candidate_functions", lambda _args: [("jev", jev), ("hermes", hermes)])
    output = tmp_path / "results"

    assert runner.main([
        "--fixture", str(fixture),
        "--live-candidates",
        "--jev-direct",
        "--hermes-endpoint", "http://127.0.0.1:8765/route-alignment/v1/classify",
        "--output-dir", str(output),
    ]) == 0
    assert calls == [("jev", "case-001")]
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["run_status"] == "failed"
    assert summary["abort_reason"] == "authentication_error"
    assert summary["candidates"]["jev"]["call_count"] == 1
    assert summary["candidates"]["hermes"]["call_count"] == 0
    artifacts = "".join(path.read_text(encoding="utf-8") for path in output.iterdir())
    assert "fake-test-key" not in artifacts
    assert "fake-test-token" not in artifacts


def test_default_artifacts_never_store_backend_evidence_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    private_text = "PRIVATE CUSTOMER EVIDENCE PHRASE"
    row = _row()
    row["messages"] = [{"role": "user", "content": private_text}]
    fixture = tmp_path / "cases.jsonl"
    _write_fixture(fixture, [row])

    def candidate(name: str, route_target: str):
        def invoke(snapshot):
            normalized = {
                **snapshot.baseline,
                "route_target": route_target,
                "backend_operation": {"action": "adjust", "target": "quota", "evidence": private_text},
            }
            return CandidateResult(
                candidate=name,
                status="ok",
                normalized=normalized,
                requested_model=f"{name}-requested",
                returned_model=f"{name}-returned",
                call_count=1,
                metadata={"actual_model_verified": True},
            )
        return invoke

    monkeypatch.setenv("ROUTE_EXPERIMENT_DATA_PROCESSING_APPROVED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-test-key")
    monkeypatch.setenv("HERMES_EXPERIMENT_TOKEN", "fake-test-token")
    monkeypatch.setattr(
        runner,
        "_candidate_functions",
        lambda _args: [("jev", candidate("jev", "automation")), ("hermes", candidate("hermes", "human_review"))],
    )
    output = tmp_path / "results"

    assert runner.main([
        "--fixture", str(fixture),
        "--live-candidates",
        "--jev-direct",
        "--hermes-endpoint", "http://127.0.0.1:8765/route-alignment/v1/classify",
        "--output-dir", str(output),
    ]) == 0
    assert not (output / "review_context.jsonl").exists()
    for path in output.iterdir():
        assert private_text not in path.read_text(encoding="utf-8"), path.name
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["formal_experiment_ready"] is True
    assert summary["candidates"]["hermes"]["model_identity_unverified_count"] == 0


def test_oversized_shared_input_rejects_both_candidates_before_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = _row()
    row["messages"] = [{"role": "user", "content": "customer words " * 1200}]
    fixture = tmp_path / "cases.jsonl"
    _write_fixture(fixture, [row])
    calls: list[str] = []

    def forbidden(name: str):
        def invoke(_snapshot):
            calls.append(name)
            raise AssertionError("candidate must not run")
        return invoke

    monkeypatch.setenv("ROUTE_EXPERIMENT_DATA_PROCESSING_APPROVED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-test-key")
    monkeypatch.setenv("HERMES_EXPERIMENT_TOKEN", "fake-test-token")
    monkeypatch.setattr(
        runner,
        "_candidate_functions",
        lambda _args: [("jev", forbidden("jev")), ("hermes", forbidden("hermes"))],
    )
    output = tmp_path / "results"

    assert runner.main([
        "--fixture", str(fixture),
        "--live-candidates",
        "--jev-direct",
        "--hermes-endpoint", "http://127.0.0.1:8765/route-alignment/v1/classify",
        "--output-dir", str(output),
    ]) == 0
    assert calls == []
    records = json.loads((output / "normalized_comparison.jsonl").read_text(encoding="utf-8"))
    assert {item["error_code"] for item in records["candidates"].values()} == {"input_too_large"}
    assert {item["call_count"] for item in records["candidates"].values()} == {0}


def test_hermes_provider_authentication_error_aborts_complete_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.services.llm_factory import LlmInvocationError
    from scripts.experiments.route_alignment.adapters import http_candidate
    from scripts.experiments.route_alignment.hermes_classifier import (
        build_experiment_profile,
        classify_case_snapshot,
    )
    from scripts.experiments.route_alignment.hermes_service import create_server

    fixture = tmp_path / "cases.jsonl"
    _write_fixture(fixture, [_row("case-001"), _row("case-002")])
    model_calls: list[str] = []
    jev_calls: list[str] = []
    profile = build_experiment_profile(
        api_key="fake-provider-key",
        base_url="http://127.0.0.1:1/v1",
        model="requested-model",
        reasoning_effort="medium",
    )

    def failing_invoke(**_kwargs):
        model_calls.append("call")
        upstream = urllib.error.HTTPError(
            "http://127.0.0.1:1/v1/responses",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":"credential rejected"}'),
        )
        raise LlmInvocationError("route_alignment_hermes_request_failed") from upstream

    def classifier(snapshot):
        with patch("backend.services.openai_agent_tracing.current_trace_ref", return_value=None):
            return classify_case_snapshot(snapshot, profile=profile, invoke=failing_invoke)

    server = create_server(host="127.0.0.1", port=0, token="service-secret", classifier=classifier)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def jev(snapshot):
            jev_calls.append(snapshot.alias)
            return CandidateResult(
                candidate="jev",
                status="ok",
                normalized=snapshot.baseline,
                requested_model="jev-1.13.0",
                returned_model="jev-1.13.0",
                call_count=1,
                metadata={"actual_model_verified": True},
            )

        hermes = http_candidate(
            "hermes",
            f"http://127.0.0.1:{server.server_address[1]}/route-alignment/v1/classify",
            headers={"Authorization": "Bearer service-secret"},
        )
        monkeypatch.setenv("ROUTE_EXPERIMENT_DATA_PROCESSING_APPROVED", "1")
        monkeypatch.setenv("TYPESAFE_API_KEY", "fake-test-key")
        monkeypatch.setenv("HERMES_EXPERIMENT_TOKEN", "service-secret")
        monkeypatch.setattr(runner, "_candidate_functions", lambda _args: [("jev", jev), ("hermes", hermes)])
        output = tmp_path / "results"

        assert runner.main([
            "--fixture", str(fixture),
            "--live-candidates",
            "--jev-direct",
            "--hermes-endpoint", f"http://127.0.0.1:{server.server_address[1]}/route-alignment/v1/classify",
            "--output-dir", str(output),
        ]) == 0
        assert jev_calls == ["case-001"]
        assert model_calls == ["call"]
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        assert summary["run_status"] == "failed"
        assert summary["abort_reason"] == "authentication_error"
        assert summary["candidates"]["jev"]["call_count"] == 1
        assert summary["candidates"]["hermes"]["call_count"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_unverified_model_identity_blocks_formal_readiness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = tmp_path / "cases.jsonl"
    _write_fixture(fixture, [_row()])

    def jev(snapshot):
        return CandidateResult(
            candidate="jev",
            status="ok",
            normalized=snapshot.baseline,
            requested_model="jev-1.13.0",
            returned_model="jev-1.13.0",
            call_count=1,
            metadata={"actual_model_verified": True},
        )

    def hermes(_snapshot):
        return CandidateResult(
            candidate="hermes",
            status="error",
            error="model_identity_unverified",
            error_code="model_identity_unverified",
            requested_model="requested-model",
            returned_model=None,
            call_count=1,
            metadata={"actual_model_verified": False},
        )

    monkeypatch.setenv("ROUTE_EXPERIMENT_DATA_PROCESSING_APPROVED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-test-key")
    monkeypatch.setenv("HERMES_EXPERIMENT_TOKEN", "fake-test-token")
    monkeypatch.setattr(runner, "_candidate_functions", lambda _args: [("jev", jev), ("hermes", hermes)])
    output = tmp_path / "results"

    assert runner.main([
        "--fixture", str(fixture),
        "--live-candidates",
        "--jev-direct",
        "--hermes-endpoint", "http://127.0.0.1:8765/route-alignment/v1/classify",
        "--output-dir", str(output),
    ]) == 0
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["formal_experiment_ready"] is False
    assert summary["candidates"]["hermes"]["model_identity_unverified_count"] == 1
    assert summary["candidates"]["hermes"]["returned_models"] == []
