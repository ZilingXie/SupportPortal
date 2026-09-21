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
    assert snapshot.baseline_status == "missing_or_wrong_version"
    result = module.compare_case(
        snapshot,
        [module.CandidateResult(candidate="jev", status="error", error="fixture_missing")],
    )
    assert result.disagreement_fields["production"] == ["baseline_missing"]


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
            return b"{}"

    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    module = _load_core()
    result = http_candidate("hermes", "https://example.invalid/classify")(
        module.build_snapshot(_row(), alias="case-001")
    )
    assert result.status == "error"
    assert "missing_classification" in (result.error or "")
    assert captured["payload"]["contract"] == "route-alignment-v1"
    assert "case_snapshot" in captured["payload"]


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
    assert "case-001" in (output / "disagreement_report.csv").read_text(encoding="utf-8")
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["case_count"] == 1
    assert summary["review_required_count"] == 1
    raw = json.loads((output / "raw_results.jsonl").read_text(encoding="utf-8"))
    assert "subject" not in raw["candidates"][0]["raw_classification"]
    manifest = json.loads((output / "manifest.jsonl").read_text(encoding="utf-8"))
    assert "subject" not in manifest
    assert manifest["subject_length"] > 0


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
