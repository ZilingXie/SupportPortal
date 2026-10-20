#!/usr/bin/env python3
"""Validate versioned n8n workflow snapshots without contacting n8n.

Two enforcement classes (dual-write phase 2, stage 2):

- legacy snapshots (the historical published chains): structural, redaction
  and manifest checks stay hard; connection-endpoint closure and direct-write
  node presence are REPORTED as LEGACY-EXPOSED warnings so the known problems
  of the old AgentMemory chains stay visible without blocking the repository;
- ``draftSourceOnly: true`` snapshots (the source-only drafts): the stage-2
  contract is enforced as errors — every connection endpoint must close, only
  allowlisted node types may appear, every static URL prefix must match the
  source-only allowlist, and any knowledge-base write endpoint is fatal.

Unpublished local candidates are validated separately and cannot carry remote
version IDs. Historical snapshots retain their original content and URLs.
"""

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

# -- stage-2 source-only contract -------------------------------------------

SOURCE_ONLY_ALLOWED_NODE_TYPES = frozenset(
    {
        "n8n-nodes-base.webhook",
        "n8n-nodes-base.scheduleTrigger",
        "n8n-nodes-base.manualTrigger",
        "n8n-nodes-base.httpRequest",
        "n8n-nodes-base.code",
        "n8n-nodes-base.if",
        "n8n-nodes-base.set",
        "n8n-nodes-base.splitInBatches",
        "n8n-nodes-base.limit",
        "n8n-nodes-base.splitOut",
        "n8n-nodes-base.merge",
        "n8n-nodes-base.noOp",
        "n8n-nodes-base.stopAndError",
        "n8n-nodes-base.aggregate",
        "n8n-nodes-base.wait",
    }
)
SOURCE_ONLY_URL_ALLOW = (
    re.compile(r"^https://agoraio\.zendesk\.com/api/v2/"),
    re.compile(r"^https://jira\.agoralab\.co/"),
    re.compile(r"^https://oauth\.agoralab\.co/"),
    # The live Preproduction API host. The chains were originally authored
    # against support.stellarix.space, which answers nginx 410 Gone for this
    # path (fixed 2026-10-13, p2-197 stage 6).
    re.compile(
        r"^https://supportcenter\.stellarix\.space/automation/preproduction/v1/knowledge/sources$"
    ),
)
# Immutable pre-repair drafts retain the URL they actually contained.
HISTORICAL_SOURCE_ONLY_URL_ALLOW = SOURCE_ONLY_URL_ALLOW + (
    re.compile(r"^https://support\.stellarix\.space/automation/preproduction/v1/knowledge/sources$"),
)
DIRECT_WRITE_URL_PATTERNS = (
    ("agentmemory-wiki-api", re.compile(r"/api/v1/knowledge/wiki/", re.IGNORECASE)),
    ("weknora-endpoint", re.compile(r"weknora", re.IGNORECASE)),
    (
        "engineer-knowledge-direct-write",
        re.compile(r"support\.stellarix\.space/api/engineer/knowledge", re.IGNORECASE),
    ),
)


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


def _static_url_prefix(url: str) -> str:
    """The static prefix of an n8n URL parameter (before any ={{ expression)."""
    value = url[1:] if url.startswith("=") else url
    return value.split("{{", 1)[0].strip()


def _validate_source_only_contract(
    workflow: dict[str, Any], path: Path, *, source_only: bool, warnings: list[str],
    historical: bool = False,
) -> None:
    nodes = workflow.get("nodes") or []
    connections = workflow.get("connections") or {}
    node_names = {str(node.get("name") or "") for node in nodes}

    # Closure: every connection endpoint must reference an existing node.
    dangling = []
    for source_name, targets in connections.items():
        if str(source_name) not in node_names:
            dangling.append(f"connection source {source_name!r}")
        if not isinstance(targets, dict):
            continue
        for outputs in targets.values():
            for group in outputs or []:
                for link in group or []:
                    target = str((link or {}).get("node") or "")
                    if target not in node_names:
                        dangling.append(
                            f"connection target {target!r} (from {source_name!r})"
                        )
    for entry in dangling:
        message = f"{path}: dangling endpoint: {entry}"
        if source_only:
            raise ValueError(message)
        warnings.append(f"LEGACY-EXPOSED {message}")

    for node in nodes:
        name = str(node.get("name") or "")
        node_type = str(node.get("type") or "")
        if source_only and node_type not in SOURCE_ONLY_ALLOWED_NODE_TYPES:
            raise ValueError(
                f"{path}: source-only node {name!r} has non-allowlisted type {node_type!r}"
            )
        for parts, value in _walk(node.get("parameters") or {}):
            if not isinstance(value, str):
                continue
            for label, pattern in DIRECT_WRITE_URL_PATTERNS:
                if pattern.search(value):
                    message = (
                        f"{path}: node {name!r} references {label} at "
                        f"{'.'.join(parts)}"
                    )
                    if source_only:
                        raise ValueError(message)
                    warnings.append(f"LEGACY-EXPOSED {message}")
            url_key = parts[-1] if parts else ""
            if source_only and url_key in {"url", "endpoint", "host"}:
                prefix = _static_url_prefix(value)
                allowed = HISTORICAL_SOURCE_ONLY_URL_ALLOW if historical else SOURCE_ONLY_URL_ALLOW
                if prefix and not any(p.search(prefix) for p in allowed):
                    raise ValueError(
                        f"{path}: source-only node {name!r} URL prefix {prefix!r} is not "
                        f"in the source-only allowlist"
                    )


def _validate_snapshot(
    path: Path,
    *,
    workflow_id: str,
    version_id: str | None,
    snapshot_kind: str,
    published_version_id: str | None = None,
    base_version_id: str | None = None,
    source_only: bool = False,
    warnings: list[str] | None = None,
) -> int:
    warnings = warnings if warnings is not None else []
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
    if "id" in workflow and workflow["id"] != workflow_id:
        raise ValueError(f"{path}: workflow.id does not match manifest")
    if snapshot_kind == "local_candidate":
        if not base_version_id or source.get("baseVersionId") != base_version_id:
            raise ValueError(f"{path}: local candidate baseVersionId does not match manifest")
        if any(key in source for key in ("versionId", "publishedVersionId", "draftVersionId")):
            raise ValueError(f"{path}: local candidate must not claim a remote version")
        if any(key in workflow for key in ("versionId", "activeVersionId")):
            raise ValueError(f"{path}: local candidate workflow must not claim a remote version")
        if (payload.get("restoreNotes") or {}).get("publicationStatus") != "unpublished_local_candidate":
            raise ValueError(f"{path}: local candidate must declare unpublished status")
        if source.get("sourceOnly", False) != source_only:
            raise ValueError(f"{path}: local candidate sourceOnly does not match manifest")
    else:
        if "versionId" in workflow and workflow["versionId"] != version_id:
            raise ValueError(f"{path}: workflow.versionId does not match source/manifest")
        active_version = published_version_id or (version_id if snapshot_kind == "published" else None)
        if "activeVersionId" in workflow and workflow["activeVersionId"] != active_version:
            raise ValueError(f"{path}: workflow.activeVersionId does not match published version")
        if "publishedVersionId" in source and source["publishedVersionId"] != active_version:
            raise ValueError(f"{path}: source.publishedVersionId does not match manifest")
        if snapshot_kind == "draft" and "draftVersionId" in source and source["draftVersionId"] != version_id:
            raise ValueError(f"{path}: source.draftVersionId does not match manifest")
    nodes = workflow.get("nodes")
    connections = workflow.get("connections")
    if not isinstance(nodes, list) or not isinstance(connections, dict):
        raise ValueError(f"{path}: nodes/connections are not restorable")
    if source.get("workflowName") != workflow.get("name"):
        raise ValueError(f"{path}: workflow name mismatch")
    _validate_no_sensitive_values(workflow, path)
    _validate_source_only_contract(
        workflow, path, source_only=source_only, warnings=warnings,
        historical=snapshot_kind != "local_candidate",
    )
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

    warnings: list[str] = []
    expected_files = {manifest_path.resolve()}
    published_count = draft_count = redaction_count = candidate_count = 0
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
            warnings=warnings,
        )
        if redactions != item["publishedRedactionCount"]:
            raise ValueError(f"{published_path}: redaction count is stale")
        published = json.loads(published_path.read_text(encoding="utf-8"))
        if item.get("nodeCount") != len(published["workflow"]["nodes"]):
            raise ValueError(f"{published_path}: published nodeCount does not match manifest")
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
                published_version_id=item["publishedVersionId"],
                source_only=bool(item.get("draftSourceOnly")),
                warnings=warnings,
            )
            if redactions != item["draftRedactionCount"]:
                raise ValueError(f"{draft_path}: redaction count is stale")
            draft_count += 1
            redaction_count += redactions

        candidate_file = item.get("candidateFile")
        if candidate_file:
            if item.get("candidateBaseVersionId") != item["draftVersionId"]:
                raise ValueError(f"{workflow_id}: candidate base must match recorded draft version")
            candidate_path = root / candidate_file
            expected_files.add(candidate_path.resolve())
            _validate_snapshot(
                candidate_path, workflow_id=workflow_id, version_id=None,
                snapshot_kind="local_candidate", base_version_id=item["candidateBaseVersionId"],
                source_only=bool(item.get("candidateSourceOnly")), warnings=warnings,
            )
            candidate_count += 1

    actual_files = {
        path.resolve()
        for pattern in ("active/*.json", "drafts/*.json", "candidates/*.json")
        for path in root.glob(pattern)
    }
    if actual_files != expected_files - {manifest_path.resolve()}:
        raise ValueError("snapshot files and manifest entries do not match")
    for warning in warnings:
        print(warning)
    if warnings:
        print(
            f"NOTE: {len(warnings)} LEGACY-EXPOSED finding(s) above belong to "
            "historical published chains; source-only drafts are enforced strictly."
        )
    if candidate_count:
        print(f"Validated {candidate_count} unpublished local candidates (not published/draft versions).")
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
