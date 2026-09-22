from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import subprocess


MODULE_PATH = Path("scripts/experiments/route_alignment/core.py")


def _load_core():
    spec = importlib.util.spec_from_file_location("route_alignment_core", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _row(alias: str = "case-001", **classification):
    return {
        "case_alias": alias,
        "ticket_id": "13401",
        "case_revision": "rev-1",
        "subject": "Quota request",
        "question": "Please increase quota.",
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
            **classification,
        },
    }


def test_snapshot_preserves_missing_production_baseline_for_review() -> None:
    module = _load_core()
    row = _row()
    row["route_classification"]["pipeline_version"] = "old-router"
    snapshot = module.build_snapshot(row, alias="case-001")
    assert snapshot.baseline_available is False
    assert snapshot.baseline_status == "missing"
    result = module.compare_case(
        snapshot,
        [module.CandidateResult(candidate="jev", status="error", error="fixture_missing")],
    )
    assert result.disagreement_fields["production"] == ["baseline_missing"]
    assert result.disagreement_fields["jev"] == ["candidate_error"]


def test_sha256_case_revision_survives_redaction_and_manifest() -> None:
    module = _load_core()
    revision = "a" * 64
    row = _row()
    row["case_revision"] = revision
    snapshot = module.build_snapshot(row, alias="case-001")
    assert snapshot.case_revision == revision
    assert module.snapshot_manifest_record(snapshot)["case_revision"] == revision


def test_redaction_removes_sensitive_values_from_snapshot() -> None:
    module = _load_core()
    row = _row()
    row["requester"] = "customer@example.com"
    row["authorization"] = "Bearer abcdefghijklmnopqrstuvwxyz0123456789"
    snapshot = module.build_snapshot(row, alias="case-001")
    encoded = json.dumps(snapshot.__dict__, ensure_ascii=False)
    assert "customer@example.com" not in encoded
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in encoded


def test_compare_only_returns_disagreement_union_and_keeps_errors() -> None:
    module = _load_core()
    snapshot = module.build_snapshot(_row(), alias="case-001")
    same = module.CandidateResult(
        candidate="jev",
        status="ok",
        normalized=module.normalize_classification(snapshot.baseline),
    )
    error = module.CandidateResult(candidate="hermes", status="error", error="timeout")
    result = module.compare_case(snapshot, [same, error])
    assert result.review_required is True
    assert result.disagreement_fields == {"hermes": ["candidate_error"]}


def test_empty_candidate_is_an_error() -> None:
    module = _load_core()
    from scripts.experiments.route_alignment.adapters import fixture_candidate

    result = fixture_candidate("jev", {"case-001": {}})(module.build_snapshot(_row(), alias="case-001"))
    assert result.status == "error"
    assert result.error == "missing_classification"


def test_stratified_rows_round_robin_route_groups() -> None:
    from scripts.experiments.route_alignment.adapters import _stratified_rows

    rows = [
        {"route_classification": {"primary_label": "Agora", "secondary_label": "A", "route_target": "rag"}, "id": index}
        for index in range(4)
    ] + [
        {"route_classification": {"primary_label": "Conversation", "secondary_label": "B", "route_target": "none"}, "id": 10}
    ]
    selected = _stratified_rows(rows, 2)
    assert {row["id"] for row in selected} == {0, 10}


def test_http_empty_json_is_missing_classification(monkeypatch) -> None:
    import urllib.request
    from scripts.experiments.route_alignment.adapters import http_candidate

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"contract": "route-alignment-v1", "case_alias": "case-001", "case_revision": "rev-1"}).encode("utf-8")

    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    module = _load_core()
    row = _row()
    row["metadata"] = {"product": "RTC", "status": "open", "private": "not-allowed"}
    result = http_candidate("hermes", "https://example.invalid/route-alignment/v1/classify")(
        module.build_snapshot(row, alias="case-001")
    )
    assert result.status == "error"
    assert "missing_classification" in (result.error or "")
    assert captured["payload"]["contract"] == "route-alignment-v1"
    assert captured["payload"]["case_snapshot"]["metadata"] == {"product": "RTC", "status": "open"}


def test_http_endpoint_rejects_ordinary_business_path() -> None:
    from scripts.experiments.route_alignment.adapters import http_candidate

    for endpoint in ("https://example.invalid/account", "https://example.invalid/intake"):
        try:
            http_candidate("jev", endpoint)
        except ValueError as exc:
            assert any(text in str(exc) for text in ("ordinary business", "route-alignment"))
        else:
            raise AssertionError("ordinary endpoint was accepted")


def test_http_endpoint_requires_route_alignment_path() -> None:
    from scripts.experiments.route_alignment.adapters import http_candidate

    try:
        http_candidate("jev", "https://example.invalid/classify")
    except ValueError as exc:
        assert "route-alignment" in str(exc)
    else:
        raise AssertionError("non route-alignment endpoint was accepted")


def test_http_response_requires_contract(monkeypatch) -> None:
    import urllib.request
    from scripts.experiments.route_alignment.adapters import http_candidate

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return json.dumps({"classification": {"intent_class": "agora"}}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: Response())
    module = _load_core()
    result = http_candidate("jev", "https://example.invalid/route-alignment/v1/classify")(
        module.build_snapshot(_row(), alias="case-001")
    )
    assert result.status == "error"
    assert "invalid_contract" in (result.error or "")


def test_http_candidate_preserves_unverified_returned_model(monkeypatch) -> None:
    import urllib.request
    from scripts.experiments.route_alignment.adapters import http_candidate

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return json.dumps({
                "contract": "route-alignment-v1",
                "case_alias": "case-001",
                "case_revision": "rev-1",
                "normalized_classification": {"intent_class": "agora", "agora_route": "technical"},
                "model_version": "requested-model",
                "requested_model": "requested-model",
                "returned_model": None,
                "actual_model_verified": False,
            }).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: Response())
    module = _load_core()
    result = http_candidate("hermes", "https://example.invalid/route-alignment/v1/classify")(
        module.build_snapshot(_row(), alias="case-001")
    )
    assert result.status == "error"
    assert result.error_code == "model_identity_unverified"
    assert result.requested_model == "requested-model"
    assert result.returned_model is None
    assert result.metadata["actual_model_verified"] is False


def test_default_http_timeout_covers_hermes_model_deadline(monkeypatch) -> None:
    import urllib.request
    from scripts.experiments.route_alignment.adapters import (
        DEFAULT_CANDIDATE_HTTP_TIMEOUT_SECONDS,
        http_candidate,
    )
    from scripts.experiments.route_alignment.hermes_classifier import HERMES_MODEL_TIMEOUT_SECONDS

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self):
            return json.dumps({
                "contract": "route-alignment-v1",
                "case_alias": "case-001",
                "case_revision": "rev-1",
                "normalized_classification": {"intent_class": "agora", "agora_route": "technical"},
                "model_version": "actual-model",
                "requested_model": "requested-model",
                "returned_model": "actual-model",
                "actual_model_verified": True,
            }).encode()

    seen: dict[str, float] = {}

    def slow_urlopen(_request, timeout):
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", slow_urlopen)
    module = _load_core()
    result = http_candidate("hermes", "https://example.invalid/route-alignment/v1/classify")(
        module.build_snapshot(_row(), alias="case-001")
    )
    assert result.status == "ok"
    assert seen["timeout"] == DEFAULT_CANDIDATE_HTTP_TIMEOUT_SECONDS
    assert DEFAULT_CANDIDATE_HTTP_TIMEOUT_SECONDS > HERMES_MODEL_TIMEOUT_SECONDS


def test_reason_text_does_not_create_disagreement() -> None:
    module = _load_core()
    snapshot = module.build_snapshot(_row(), alias="case-001")
    candidate = dict(snapshot.baseline)
    candidate["route_reason_code"] = "different_explanation"
    result = module.compare_case(
        snapshot,
        [module.CandidateResult(candidate="jev", status="ok", normalized=candidate)],
    )
    assert result.review_required is False


def test_write_disagreement_csv_excludes_agreements(tmp_path: Path) -> None:
    module = _load_core()
    snapshot = module.build_snapshot(_row(), alias="case-001")
    result = module.compare_case(
        snapshot,
        [module.CandidateResult(candidate="jev", status="ok", normalized=snapshot.baseline)],
    )
    output = tmp_path / "disagreement.csv"
    module.write_disagreement_csv(output, [result])
    assert output.read_text(encoding="utf-8").count("case-001") == 0


def test_cli_emits_only_candidate_disagreements(tmp_path: Path) -> None:
    fixture = tmp_path / "cases.jsonl"
    fixture.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    candidates = tmp_path / "candidates.json"
    payload = {"jev": {"case-001": _row()["route_classification"]}, "hermes": {"case-001": {**_row()["route_classification"], "route_target": "automation"}}}
    candidates.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "out"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.experiments.route_alignment",
            "--fixture",
            str(fixture),
            "--fixture-candidates",
            str(candidates),
            "--output-dir",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    report = output / summary["artifacts"]["disagreement_report"]
    assert summary["run_id"] in report.name
    assert "case-001" in report.read_text(encoding="utf-8")
    assert summary["case_count"] == 1
    assert summary["review_required_count"] == 1
    raw = json.loads((output / "raw_results.jsonl").read_text(encoding="utf-8"))
    assert "raw_classification" not in raw["candidates"][0]
    assert "subject" not in raw["candidates"][0]
    assert "messages" not in raw["candidates"][0]
    manifest = json.loads((output / "manifest.jsonl").read_text(encoding="utf-8"))
    assert "subject" not in manifest
    assert manifest["subject_length"] > 0


def test_cli_zero_disagreements_keeps_run_id_in_csv_filename(tmp_path: Path) -> None:
    fixture = tmp_path / "cases.jsonl"
    fixture.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    candidates = tmp_path / "candidates.json"
    classification = _row()["route_classification"]
    candidates.write_text(
        json.dumps({"jev": {"case-001": classification}, "hermes": {"case-001": classification}}),
        encoding="utf-8",
    )
    output = tmp_path / "out"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.experiments.route_alignment",
            "--fixture",
            str(fixture),
            "--fixture-candidates",
            str(candidates),
            "--output-dir",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    report = output / summary["artifacts"]["disagreement_report"]
    assert summary["review_required_count"] == 0
    assert summary["run_id"] in report.name
    assert len(report.read_text(encoding="utf-8").splitlines()) == 1


def test_cli_requires_both_candidates(tmp_path: Path) -> None:
    fixture = tmp_path / "cases.jsonl"
    fixture.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"jev": {"case-001": _row()["route_classification"]}}), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.experiments.route_alignment",
            "--fixture",
            str(fixture),
            "--fixture-candidates",
            str(candidates),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "both jev and hermes" in completed.stderr


def test_latency_p95_uses_nearest_rank() -> None:
    from scripts.experiments.route_alignment.runner import _latency_summary

    assert _latency_summary([10, 20])["p95"] == 20


def test_cli_refuses_nonempty_output_directory(tmp_path: Path) -> None:
    fixture = tmp_path / "cases.jsonl"
    fixture.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    candidates = tmp_path / "candidates.json"
    value = _row()["route_classification"]
    candidates.write_text(json.dumps({"jev": {"case-001": value}, "hermes": {"case-001": value}}), encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    (output / "old.json").write_text("old", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.experiments.route_alignment", "--fixture", str(fixture), "--fixture-candidates", str(candidates), "--output-dir", str(output)],
        check=False, capture_output=True, text=True,
    )
    assert completed.returncode != 0
    assert "refusing to overwrite" in completed.stderr
