from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.experiments.route_alignment import runner


def _row(alias: str, *, subcategory: str | None = "progress_inquiry") -> dict:
    return {
        "case_alias": alias,
        "case_revision": f"rev-{alias}",
        "subject": "Status update",
        "messages": [{"role": "assistant", "content": "We are checking this."}, {"role": "user", "content": "Any update?"}],
        "route_classification": {
            "pipeline_version": "account-layered-router-v11",
            "intent_class": "conversation",
            "conversation_action": "follow_up",
            "primary_label": "Conversation",
            "secondary_label": "Follow-up",
            "conversation_subcategory": subcategory,
            "route_target": "conversation_reply",
        },
        "metadata": {"product": "RTC", "status": "open", "baseline_input_alignment": "matched"},
    }


def test_three_way_csv_contains_failed_case_and_pair_denominators(tmp_path: Path, monkeypatch) -> None:
    fixture = tmp_path / "cases.jsonl"
    fixture.write_text("".join(json.dumps(_row(alias)) + "\n" for alias in ("same", "different", "failed")), encoding="utf-8")
    candidates = tmp_path / "candidates.json"
    baseline = _row("same")["route_classification"]
    different = {**baseline, "conversation_subcategory": "priority_request"}
    candidates.write_text(json.dumps({
        "jev": {"same": baseline, "different": baseline, "failed": baseline},
        "hermes": {"same": baseline, "different": different},
    }), encoding="utf-8")
    output = tmp_path / "run"
    monkeypatch.setenv("ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED", "1")

    assert runner.main([
        "--fixture", str(fixture), "--fixture-candidates", str(candidates), "--output-dir", str(output)
    ]) == 0

    rows = list(csv.DictReader((output / "three_way_comparison.csv").open(encoding="utf-8")))
    assert [row["case_alias"] for row in rows] == ["same", "different", "failed"]
    assert rows[1]["production_hermes_different_fields"] == "conversation_subcategory"
    assert rows[2]["hermes_status"] == "error"
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["three_way_agreement"]["production_hermes"]["comparable_sample_count"] == 2
    assert summary["three_way_agreement"]["production_hermes"]["fields"]["conversation_subcategory"]["compared"] == 2
    assert summary["three_way_agreement"]["production_jev"]["comparable_sample_count"] == 3


def test_candidate_fixture_input_excludes_production_route() -> None:
    from scripts.experiments.route_alignment.core import build_snapshot
    from scripts.experiments.route_alignment.dataset import candidate_state

    snapshot = build_snapshot(_row("case-001"), alias="case-001")
    state = candidate_state(snapshot)
    assert "route_target" not in state
    assert state["subject"] == snapshot.subject
    assert state["messages"] == list(snapshot.messages)
