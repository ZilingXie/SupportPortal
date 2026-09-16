#!/usr/bin/env python3
"""Validate versioned n8n workflow snapshots without contacting n8n."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SECRET_NAME = re.compile(
    r"authorization|cookie|password|passwd|secret|token|api[-_ ]?key|"
    r"user[-_ ]?key|client[-_ ]?secret|signing[-_ ]?secret",
    re.IGNORECASE,
)
SECRET_PATTERNS = {
    "JWT": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
    "Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}", re.IGNORECASE),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Basic authorization": re.compile(r"\bBasic\s+[A-Za-z0-9+/=]{16,}", re.IGNORECASE),
    "Bearer token": re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{16,}", re.IGNORECASE),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "email address": re.compile(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        re.IGNORECASE,
    ),
}
FORBIDDEN_KEYS = {"executionData", "pinData", "runData", "staticData"}


def _walk(value: Any, path: tuple[str, ...] = ()):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, (*path, str(index)))


def _is_expression(value: str) -> bool:
    return "{{" in value or value.startswith("$")


def _validate_no_sensitive_values(workflow: dict[str, Any], path: Path) -> None:
    for parts, value in _walk(workflow):
        if parts and parts[-1] in FORBIDDEN_KEYS:
            raise ValueError(f"{path}: forbidden runtime data at {'.'.join(parts)}")
        if isinstance(value, str):
            for label, pattern in SECRET_PATTERNS.items():
                if pattern.search(value):
                    raise ValueError(f"{path}: unredacted {label} at {'.'.join(parts)}")
        if not isinstance(value, dict):
            continue
        field_name = value.get("name")
        field_value = value.get("value")
        if (
            isinstance(field_name, str)
            and SECRET_NAME.search(field_name)
            and isinstance(field_value, str)
            and not _is_expression(field_value)
            and not field_value.startswith("__REDACTED")
        ):
            raise ValueError(
                f"{path}: secret-like field {field_name!r} is not redacted at "
                f"{'.'.join(parts)}"
            )


def _validate_snapshot(
    path: Path,
    *,
    workflow_id: str,
    version_id: str,
    snapshot_kind: str,
) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != 1:
        raise ValueError(f"{path}: unsupported schemaVersion")
    source = payload.get("source") or {}
    expected = {
        "workflowId": workflow_id,
        "versionId": version_id,
        "snapshotKind": snapshot_kind,
    }
    for key, value in expected.items():
        if source.get(key) != value:
            raise ValueError(f"{path}: source.{key} does not match manifest")
    workflow = payload.get("workflow")
    if not isinstance(workflow, dict):
        raise ValueError(f"{path}: workflow object is missing")
    nodes = workflow.get("nodes")
    connections = workflow.get("connections")
    if not isinstance(nodes, list) or not isinstance(connections, dict):
        raise ValueError(f"{path}: nodes/connections are not restorable")
    if source.get("workflowName") != workflow.get("name"):
        raise ValueError(f"{path}: workflow name mismatch")
    _validate_no_sensitive_values(workflow, path)
    redactions = (payload.get("restoreNotes") or {}).get("redactedValues")
    if not isinstance(redactions, list):
        raise ValueError(f"{path}: redaction ledger is missing")
    return len(redactions)


def validate(root: Path) -> tuple[int, int, int]:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    workflows = manifest.get("workflows")
    if manifest.get("schemaVersion") != 1 or not isinstance(workflows, list):
        raise ValueError("manifest schema is invalid")
    if manifest.get("workflowCount") != len(workflows):
        raise ValueError("manifest workflowCount is stale")

    expected_files = {manifest_path.resolve()}
    published_count = draft_count = redaction_count = 0
    workflow_ids: set[str] = set()
    for item in workflows:
        workflow_id = item["workflowId"]
        if workflow_id in workflow_ids:
            raise ValueError(f"duplicate workflowId in manifest: {workflow_id}")
        workflow_ids.add(workflow_id)

        published_path = root / item["publishedFile"]
        expected_files.add(published_path.resolve())
        redactions = _validate_snapshot(
            published_path,
            workflow_id=workflow_id,
            version_id=item["publishedVersionId"],
            snapshot_kind="published",
        )
        if redactions != item["publishedRedactionCount"]:
            raise ValueError(f"{published_path}: redaction count is stale")
        published_count += 1
        redaction_count += redactions

        draft_file = item.get("draftFile")
        if item["draftMatchesPublished"] != (draft_file is None):
            raise ValueError(f"{workflow_id}: draft file/version relationship is invalid")
        if draft_file:
            draft_path = root / draft_file
            expected_files.add(draft_path.resolve())
            redactions = _validate_snapshot(
                draft_path,
                workflow_id=workflow_id,
                version_id=item["draftVersionId"],
                snapshot_kind="draft",
            )
            if redactions != item["draftRedactionCount"]:
                raise ValueError(f"{draft_path}: redaction count is stale")
            draft_count += 1
            redaction_count += redactions

    actual_files = {
        path.resolve()
        for pattern in ("active/*.json", "drafts/*.json")
        for path in root.glob(pattern)
    }
    if actual_files != expected_files - {manifest_path.resolve()}:
        raise ValueError("snapshot files and manifest entries do not match")
    return published_count, draft_count, redaction_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("docs/integrations/n8n/workflows"),
    )
    args = parser.parse_args()
    published, drafts, redactions = validate(args.root)
    print(
        f"Validated {published} published snapshots, {drafts} divergent drafts, "
        f"and {redactions} redacted values."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
