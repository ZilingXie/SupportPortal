#!/usr/bin/env python3
"""Validate project records and build the file://-compatible Overview data file."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_JS = ROOT / "docs/projectoverview-data.js"
PROJECT_DIR = ROOT / "docs/project"
TASK_DIR = PROJECT_DIR / "tasks"
TOPIC_DIR = PROJECT_DIR / "topics"
MILESTONE_DIR = PROJECT_DIR / "milestones"
MEETING_DIR = PROJECT_DIR / "meetings"
PR_INDEX = PROJECT_DIR / "generated/pr-index.json"
PR_SUMMARIES = PROJECT_DIR / "pr_summaries.json"

TASK_STATUSES = {"planned", "active", "review", "blocked", "done"}
EVIDENCE_TYPES = {"pr", "test", "deployment", "document", "decision"}
PR_FIELDS = {"number", "title", "state", "isDraft", "createdAt", "updatedAt", "mergedAt", "url", "headRefName"}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是 object：{path}")
    return value


def read_records(directory: Path, key: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        record = read_json(path)
        if not record.get(key):
            raise ValueError(f"{path} 缺少 {key}")
        record["_path"] = str(path.relative_to(ROOT))
        records.append(record)
    return records


def ensure_unique(records: list[dict[str, Any]], key: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        value = str(record.get(key, ""))
        if value in result:
            errors.append(f"重复 {key}: {value} ({result[value]['_path']} / {record['_path']})")
        result[value] = record
    return result


def parse_feature_list(path: Path) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    current_status: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current_section = {"title": line[3:].strip(), "completed": [], "planned": []}
            sections.append(current_section)
            current_status = None
        elif line.startswith("### ") and current_section is not None:
            heading = line[4:].strip()
            current_status = "completed" if heading == "已完成" else "planned" if heading == "未完成" else None
        elif line.startswith("- ") and current_section is not None and current_status:
            current_section[current_status].append(line[2:].strip())
    return sections


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def git_revision() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "working-tree"


def refresh_prs() -> None:
    command = [
        "gh", "pr", "list", "--state", "all", "--limit", "30",
        "--json", ",".join(sorted(PR_FIELDS)),
    ]
    try:
        raw = subprocess.check_output(command, cwd=ROOT, text=True, stderr=subprocess.STDOUT)
        payload = json.loads(raw)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise ValueError(f"PR 快照刷新失败；不会静默使用旧数据：{exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("gh pr list 返回的不是数组")
    PR_INDEX.parent.mkdir(parents=True, exist_ok=True)
    PR_INDEX.write_text(
        json.dumps({"schema_version": 1, "fetched_at": utc_now(), "repository": "ZilingXie/SupportPortal", "prs": payload}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_records(
    project: dict[str, Any],
    milestones: list[dict[str, Any]],
    topics: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    meetings: list[dict[str, Any]],
    migration: dict[str, Any],
    pr_summaries: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    milestone_map = ensure_unique(milestones, "milestone_id", errors)
    topic_map = ensure_unique(topics, "topic_id", errors)
    task_map = ensure_unique(tasks, "task_id", errors)
    meeting_map = ensure_unique(meetings, "meeting_id", errors)
    if project.get("current_milestone_id") not in milestone_map:
        errors.append("project.current_milestone_id 不存在")
    for task in tasks:
        path = task["_path"]
        status = task.get("status")
        if status not in TASK_STATUSES:
            errors.append(f"{path}: 非法 status {status!r}")
        if task.get("topic_id") not in topic_map:
            errors.append(f"{path}: topic_id 不存在")
        if task.get("milestone_id") not in milestone_map:
            errors.append(f"{path}: milestone_id 不存在")
        for topic_id in task.get("related_topic_ids", []):
            if topic_id not in topic_map:
                errors.append(f"{path}: related_topic_id 不存在: {topic_id}")
        evidence = task.get("evidence", [])
        if not isinstance(evidence, list):
            errors.append(f"{path}: evidence 必须是数组")
            evidence = []
        for item in evidence:
            if item.get("type") not in EVIDENCE_TYPES:
                errors.append(f"{path}: 非法 evidence.type {item.get('type')!r}")
        if status == "done" and not evidence:
            errors.append(f"{path}: done Task 必须有 evidence")
        if status == "blocked" and not task.get("blockers"):
            errors.append(f"{path}: blocked Task 必须有 blockers")
        if status != "done" and not str(task.get("next_action", "")).strip():
            errors.append(f"{path}: 非 done Task 必须有 next_action")
    for meeting in meetings:
        for task_id in meeting.get("task_ids", []):
            if task_id not in task_map:
                errors.append(f"{meeting['_path']}: task_id 不存在: {task_id}")
    for record in migration.get("records", []):
        target_id = record.get("target_id")
        if record.get("target_type") == "task" and target_id not in task_map:
            errors.append(f"migration_manifest: target Task 不存在: {target_id}")
    for number, summary in pr_summaries.get("summaries", {}).items():
        for task_id in summary.get("task_ids", []):
            if task_id not in task_map:
                errors.append(f"pr_summaries PR #{number}: task_id 不存在: {task_id}")
    return errors


def canonical_payload(
    project: dict[str, Any],
    milestones: list[dict[str, Any]],
    topics: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    meetings: list[dict[str, Any]],
    manual: dict[str, Any],
    system_map: dict[str, Any],
    pr_index: dict[str, Any],
    pr_summaries: dict[str, Any],
    features: list[dict[str, Any]],
    migration: dict[str, Any],
) -> dict[str, Any]:
    def clean(record: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in record.items() if key != "_path"}

    return {
        "project": clean(project),
        "milestones": [clean(item) for item in milestones],
        "topics": [clean(item) for item in topics],
        "tasks": [clean(item) for item in sorted(tasks, key=lambda item: item["task_id"])],
        "meetings": [clean(item) for item in sorted(meetings, key=lambda item: item["date"], reverse=True)],
        "manual": manual,
        "system_map": system_map,
        "pr_index": pr_index,
        "pr_summaries": pr_summaries,
        "features": features,
        "migration": {"source_count": len(migration.get("records", [])), "generated_from": migration.get("generated_from", [])},
    }


def digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_data(payload: dict[str, Any], registry_digest: str) -> None:
    output = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "source_base_commit": git_revision(),
        "registry_digest": registry_digest,
        **payload,
    }
    encoded = json.dumps(output, ensure_ascii=False, indent=2).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    DATA_JS.write_text("window.SUPPORTPORTAL_PROJECT_DATA = " + encoded + ";\n", encoding="utf-8")


def load_all() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    project = read_json(PROJECT_DIR / "project.json")
    milestones = read_records(MILESTONE_DIR, "milestone_id")
    topics = read_records(TOPIC_DIR, "topic_id")
    tasks = read_records(TASK_DIR, "task_id")
    meetings = read_records(MEETING_DIR, "meeting_id")
    migration = read_json(PROJECT_DIR / "migration_manifest.json")
    pr_summaries = read_json(PR_SUMMARIES)
    manual = read_json(PROJECT_DIR / "manual.json")
    system_map = read_json(PROJECT_DIR / "system_map.json")
    if PR_INDEX.exists():
        pr_index = read_json(PR_INDEX)
    else:
        pr_index = {"schema_version": 1, "fetched_at": None, "repository": "ZilingXie/SupportPortal", "prs": []}
    features = parse_feature_list(ROOT / "docs/feature_list.md")
    return project, milestones, topics, tasks, meetings, migration, pr_summaries, manual, system_map, pr_index, features


def check_generated(expected_digest: str) -> None:
    if not DATA_JS.exists():
        raise ValueError(f"生成物不存在：{DATA_JS}")
    source = DATA_JS.read_text(encoding="utf-8")
    match = re.fullmatch(r"window\.SUPPORTPORTAL_PROJECT_DATA = (\{.*\});\n", source, re.S)
    if not match:
        raise ValueError("projectoverview-data.js 格式不正确")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"projectoverview-data.js 不是合法 JSON：{exc}") from exc
    if payload.get("registry_digest") != expected_digest:
        raise ValueError("projectoverview-data.js 已过期，请重新运行 --write")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-prs", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.refresh_prs:
        refresh_prs()
    try:
        project, milestones, topics, tasks, meetings, migration, pr_summaries, manual, system_map, pr_index, features = load_all()
        errors = validate_records(project, milestones, topics, tasks, meetings, migration, pr_summaries)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        payload = canonical_payload(project, milestones, topics, tasks, meetings, manual, system_map, pr_index, pr_summaries, features, migration)
        registry_digest = digest(payload)
        if args.check:
            check_generated(registry_digest)
            print(f"Project Overview validation passed: {registry_digest}")
            return 0
        if not args.write:
            parser.error("请指定 --write 或 --check")
        write_data(payload, registry_digest)
        print(f"Project Overview generated: {DATA_JS} ({registry_digest})")
        return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
