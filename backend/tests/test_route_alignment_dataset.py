from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.experiments.route_alignment.core import build_snapshot
from scripts.experiments.route_alignment.dataset import (
    DatasetError,
    candidate_state,
    dataset_id_for,
    load_frozen_dataset,
    shape_public_context,
    validate_candidate_request_size,
    write_frozen_dataset,
)


def _snapshot():
    return build_snapshot(
        {
            "ticket_id": "13500",
            "case_revision": "a" * 64,
            "case_revision_source": "comments_revision",
            "subject": "SDK question from user@example.com",
            "messages": [
                {"role": "user", "content": "Initial request"},
                {"role": "assistant", "content": "Please clarify"},
                {"role": "user", "content": "Latest request +1 415 555 0100"},
                {"role": "assistant", "content": "Future reply must not be visible"},
            ],
            "route_classification": {
                "pipeline_version": "account-layered-router-v11",
                "intent_class": "agora",
                "agora_route": "technical",
            },
            "metadata": {"product": "RTC", "status": "open"},
        },
        alias="prod-001",
    )


def test_context_stops_at_latest_customer_and_question_is_only_fallback() -> None:
    messages = shape_public_context(
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "latest"},
            {"role": "assistant", "content": "after"},
        ],
        question="duplicate",
    )
    assert [item["content"] for item in messages] == ["first", "answer", "latest"]
    assert shape_public_context([], question="fallback") == [{"role": "user", "content": "fallback"}]


def test_candidate_state_is_redacted_and_excludes_ticket_and_baseline() -> None:
    state = candidate_state(_snapshot())
    encoded = json.dumps(state)
    assert "13500" not in encoded
    assert "route_classification" not in encoded
    assert "Future reply" not in encoded
    assert "user@example.com" not in encoded
    assert "415 555" not in encoded


def test_frozen_dataset_round_trip_and_permissions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED", "1")
    snapshot = _snapshot()
    output = tmp_path / "frozen"
    dataset_id = write_frozen_dataset(output, [snapshot])
    loaded_id, snapshots = load_frozen_dataset(output / "frozen_snapshots.jsonl")
    assert loaded_id == dataset_id == dataset_id_for(snapshots)
    assert snapshots[0].case_revision == "a" * 64
    assert snapshots[0].ticket_id == ""
    assert os.stat(output).st_mode & 0o077 == 0
    for item in output.iterdir():
        assert os.stat(item).st_mode & 0o077 == 0


def test_frozen_text_requires_explicit_approval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED", raising=False)
    with pytest.raises(DatasetError, match="REVIEW_TEXT_APPROVED"):
        write_frozen_dataset(tmp_path / "frozen", [_snapshot()])


def test_frozen_dataset_detects_tampering(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED", "1")
    output = tmp_path / "frozen"
    write_frozen_dataset(output, [_snapshot()])
    path = output / "frozen_snapshots.jsonl"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["subject"] = "changed"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="hash mismatch"):
        load_frozen_dataset(path)


def test_input_limit_is_fail_closed_without_truncation() -> None:
    snapshot = build_snapshot(
        {
            "subject": "word " * 5_800,
            "messages": [{"role": "user", "content": "request"}],
            "route_classification": {"pipeline_version": "account-layered-router-v11"},
        },
        alias="large",
    )
    assert len(snapshot.subject) == 28_999
    with pytest.raises(DatasetError, match="input_too_large"):
        validate_candidate_request_size(snapshot, {"intent": {"type": "choice"}})
