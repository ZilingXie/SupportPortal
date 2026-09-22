"""Frozen dataset and shared candidate-input contracts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from .core import CaseSnapshot, build_snapshot, shape_public_context, snapshot_manifest_record


DATASET_CONTRACT = "route-alignment-dataset-v1"
CONTEXT_POLICY_VERSION = "public-through-latest-customer-v1"
REDACTION_POLICY_VERSION = "conservative-v2"
MAX_STATE_AND_LONGEST_QUESTION_BYTES = 28_000
MAX_REQUEST_BYTES = 56_000


class DatasetError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def candidate_state(snapshot: CaseSnapshot) -> dict[str, Any]:
    metadata = {
        key: snapshot.metadata.get(key)
        for key in ("product", "status")
        if snapshot.metadata.get(key) not in (None, "")
    }
    return {
        "subject": snapshot.subject,
        "messages": list(snapshot.messages),
        "metadata": metadata,
    }


def validate_candidate_request_size(snapshot: CaseSnapshot, questions: Mapping[str, Any]) -> dict[str, int]:
    state = candidate_state(snapshot)
    state_bytes = len(_canonical(state))
    longest_question_bytes = max((len(_canonical(value)) for value in questions.values()), default=0)
    request_bytes = len(_canonical({"state": state, "questions": questions}))
    if state_bytes + longest_question_bytes > MAX_STATE_AND_LONGEST_QUESTION_BYTES:
        raise DatasetError("input_too_large:state_and_longest_question")
    if request_bytes > MAX_REQUEST_BYTES:
        raise DatasetError("input_too_large:request")
    return {
        "state_bytes": state_bytes,
        "longest_question_bytes": longest_question_bytes,
        "request_bytes": request_bytes,
    }


def dataset_id_for(snapshots: Iterable[CaseSnapshot]) -> str:
    records = [
        {
            "case_alias": item.alias,
            "case_revision": item.case_revision,
            "subject": item.subject,
            "messages": list(item.messages),
            "baseline": item.baseline,
            "baseline_status": item.baseline_status,
            "baseline_input_alignment": item.metadata.get("baseline_input_alignment", "unknown"),
            "metadata": candidate_state(item)["metadata"],
        }
        for item in snapshots
    ]
    return hashlib.sha256(_canonical(records)).hexdigest()


def _frozen_record(snapshot: CaseSnapshot, dataset_id: str) -> dict[str, Any]:
    return {
        "contract": DATASET_CONTRACT,
        "dataset_id": dataset_id,
        "case_alias": snapshot.alias,
        "case_revision": snapshot.case_revision,
        "case_revision_source": snapshot.case_revision_source,
        "subject": snapshot.subject,
        "messages": list(snapshot.messages),
        "route_classification": snapshot.baseline,
        "baseline_status": snapshot.baseline_status,
        "metadata": {
            **candidate_state(snapshot)["metadata"],
            "baseline_input_alignment": snapshot.metadata.get("baseline_input_alignment", "unknown"),
            "context_policy_version": CONTEXT_POLICY_VERSION,
            "redaction_policy_version": REDACTION_POLICY_VERSION,
        },
    }


def write_frozen_dataset(output_dir: Path, snapshots: list[CaseSnapshot]) -> str:
    if os.getenv("ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED") != "1":
        raise DatasetError("frozen text requires ROUTE_EXPERIMENT_REVIEW_TEXT_APPROVED=1")
    dataset_id = dataset_id_for(snapshots)
    output_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(output_dir, 0o700)
    paths = {
        "frozen": output_dir / "frozen_snapshots.jsonl",
        "manifest": output_dir / "dataset_manifest.jsonl",
        "lookup": output_dir / "local_case_lookup.jsonl",
    }
    with paths["frozen"].open("x", encoding="utf-8") as handle:
        for item in snapshots:
            handle.write(json.dumps(_frozen_record(item, dataset_id), ensure_ascii=False, sort_keys=True) + "\n")
    with paths["manifest"].open("x", encoding="utf-8") as handle:
        for item in snapshots:
            record = snapshot_manifest_record(item, dataset_id=dataset_id)
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    with paths["lookup"].open("x", encoding="utf-8") as handle:
        for item in snapshots:
            handle.write(json.dumps({"dataset_id": dataset_id, "case_alias": item.alias, "ticket_id": item.ticket_id}, sort_keys=True) + "\n")
    for path in paths.values():
        os.chmod(path, 0o600)
    return dataset_id


def load_frozen_dataset(path: Path) -> tuple[str, list[CaseSnapshot]]:
    dataset_ids: set[str] = set()
    snapshots: list[CaseSnapshot] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("contract") != DATASET_CONTRACT:
                raise DatasetError(f"invalid frozen contract at line {line_number}")
            dataset_ids.add(str(row.get("dataset_id") or ""))
            snapshots.append(build_snapshot(row, alias=str(row.get("case_alias") or f"case-{line_number:03d}")))
    if len(dataset_ids) != 1 or "" in dataset_ids or not snapshots:
        raise DatasetError("frozen dataset must contain one non-empty dataset_id")
    dataset_id = next(iter(dataset_ids))
    if dataset_id_for(snapshots) != dataset_id:
        raise DatasetError("frozen dataset hash mismatch")
    return dataset_id, snapshots
