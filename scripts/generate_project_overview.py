#!/usr/bin/env python3
"""Validate the Phase/Module/Function/Task registry and build Overview data."""

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
PHASE_DIR = PROJECT_DIR / "phases"
MODULE_DIR = PROJECT_DIR / "modules"
FUNCTION_DIR = PROJECT_DIR / "functions"
TASK_DIR = PROJECT_DIR / "tasks"
MEETING_DIR = PROJECT_DIR / "meetings"
PR_INDEX = PROJECT_DIR / "generated/pr-index.json"
PR_SUMMARIES = PROJECT_DIR / "pr_summaries.json"

TASK_STATUSES = {"planned", "active", "review", "blocked", "done"}
EVIDENCE_TYPES = {"pr", "test", "deployment", "document", "decision"}
PR_FIELDS = {"number", "title", "state", "isDraft", "createdAt", "updatedAt", "mergedAt", "url", "headRefName"}
TASK_ID_RE = re.compile(r"^p[123]-\d{2,}$")
PHASE_ORDER = {"phase-1": 0, "phase-2": 1, "phase-3": 2}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是 object：{path}")
    return value


def read_records(directory: Path, key: str) -> list[dict[str, Any]]:
    records = []
    for path in sorted(directory.glob("*.json")):
        record = read_json(path)
        if not record.get(key):
            raise ValueError(f"{path} 缺少 {key}")
        record["_path"] = str(path.relative_to(ROOT))
        record["_filename"] = path.stem
        records.append(record)
    return records


def ensure_unique(records: list[dict[str, Any]], key: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    result = {}
    for record in records:
        value = str(record.get(key, ""))
        if value in result:
            errors.append(f"重复 {key}: {value} ({result[value]['_path']} / {record['_path']})")
        result[value] = record
    return result


def parse_feature_list(path: Path) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    section = None
    status = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = {"title": line[3:].strip(), "completed": [], "planned": []}
            sections.append(section)
            status = None
        elif line.startswith("### ") and section is not None:
            heading = line[4:].strip()
            status = "completed" if heading == "已完成" else "planned" if heading == "未完成" else None
        elif line.startswith("- ") and section is not None and status:
            section[status].append(line[2:].strip())
    return sections


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def git_revision() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "working-tree"


def refresh_prs() -> None:
    command = ["gh", "pr", "list", "--state", "all", "--limit", "30", "--json", ",".join(sorted(PR_FIELDS))]
    try:
        payload = json.loads(subprocess.check_output(command, cwd=ROOT, text=True, stderr=subprocess.STDOUT))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise ValueError(f"PR 快照刷新失败；不会静默使用旧数据：{exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("gh pr list 返回的不是数组")
    PR_INDEX.parent.mkdir(parents=True, exist_ok=True)
    PR_INDEX.write_text(json.dumps({"schema_version": 1, "fetched_at": utc_now(), "repository": "ZilingXie/SupportPortal", "prs": payload}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def derive_function(function: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    children = [task for task in tasks if task["function_id"] == function["function_id"]]
    statuses = [task["status"] for task in children]
    if statuses and all(status == "done" for status in statuses):
        status = "done"
    elif statuses and all(status == "planned" for status in statuses):
        status = "planned"
    elif statuses and all(status == "blocked" for status in statuses):
        status = "blocked"
    elif statuses and all(status == "review" for status in statuses):
        status = "review"
    else:
        status = "active"
    evidence = list(function.get("evidence", []))
    for task in children:
        for item in task.get("evidence", []):
            if item not in evidence:
                evidence.append(item)
    return {
        **{key: value for key, value in function.items() if not key.startswith("_")},
        "status": status,
        "task_count": len(children),
        "done_count": sum(task["status"] == "done" for task in children),
        "blocked_count": sum(task["status"] == "blocked" for task in children),
        "evidence": evidence,
    }


def validate_records(project, phases, modules, functions, tasks, meetings, migration, pr_summaries):
    errors: list[str] = []
    phase_map = ensure_unique(phases, "phase_id", errors)
    module_map = ensure_unique(modules, "module_id", errors)
    function_map = ensure_unique(functions, "function_id", errors)
    task_map = ensure_unique(tasks, "task_id", errors)
    if set(phase_map) != {"phase-1", "phase-2", "phase-3"}:
        errors.append("Phase registry 必须且只能包含 phase-1、phase-2、phase-3")
    if project.get("current_phase_id") not in phase_map:
        errors.append("project.current_phase_id 不存在")
    legacy_owner: dict[str, str] = {}
    for function in functions:
        path = function["_path"]
        if function["_filename"] != function["function_id"]:
            errors.append(f"{path}: 文件名必须与 function_id 一致")
        if function.get("phase_id") not in phase_map:
            errors.append(f"{path}: phase_id 不存在")
        if function.get("module_id") not in module_map:
            errors.append(f"{path}: module_id 不存在")
        for legacy_id in function.get("legacy_ids", []):
            if legacy_id in legacy_owner:
                errors.append(f"重复 legacy_id: {legacy_id}")
            legacy_owner[legacy_id] = f"function:{function['function_id']}"
    for task in tasks:
        path = task["_path"]
        task_id = task.get("task_id", "")
        if task["_filename"] != task_id:
            errors.append(f"{path}: 文件名必须与 task_id 一致")
        if not TASK_ID_RE.fullmatch(task_id):
            errors.append(f"{path}: Task ID 必须符合 pN-xx 规则")
        status = task.get("status")
        if status not in TASK_STATUSES:
            errors.append(f"{path}: 非法 status {status!r}")
        function = function_map.get(task.get("function_id"))
        if not function:
            errors.append(f"{path}: function_id 不存在")
        if function and task.get("phase_id") != function.get("phase_id"):
            errors.append(f"{path}: Task 和 Function 的 phase_id 不一致")
        if function and task.get("module_id") != function.get("module_id"):
            errors.append(f"{path}: Task 和 Function 的 module_id 不一致")
        if task.get("phase_id") not in phase_map:
            errors.append(f"{path}: phase_id 不存在")
        if task.get("module_id") not in module_map:
            errors.append(f"{path}: module_id 不存在")
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
        for legacy_id in task.get("legacy_ids", []):
            if legacy_id in legacy_owner:
                errors.append(f"重复 legacy_id: {legacy_id}")
            legacy_owner[legacy_id] = f"task:{task_id}"
    task_function_ids = {task["function_id"] for task in tasks}
    for function in functions:
        if function["function_id"] not in task_function_ids:
            errors.append(f"{function['_path']}: Function 必须至少包含一个 Task")
    for meeting in meetings:
        for task_id in meeting.get("task_ids", []):
            if task_id not in task_map:
                errors.append(f"{meeting['_path']}: task_id 不存在: {task_id}")
        for function_id in meeting.get("function_ids", []):
            if function_id not in function_map:
                errors.append(f"{meeting['_path']}: function_id 不存在: {function_id}")
    migration_ids = set()
    for record in migration.get("records", []):
        legacy_id = record.get("legacy_id")
        if legacy_id in migration_ids:
            errors.append(f"migration_manifest: 重复 legacy_id: {legacy_id}")
        migration_ids.add(legacy_id)
        target_type, target_id = record.get("target_type"), record.get("target_id")
        if target_type == "task" and target_id not in task_map:
            errors.append(f"migration_manifest: target Task 不存在: {target_id}")
        if target_type == "function" and target_id not in function_map:
            errors.append(f"migration_manifest: target Function 不存在: {target_id}")
    for number, summary in pr_summaries.get("summaries", {}).items():
        for task_id in summary.get("task_ids", []):
            if task_id not in task_map:
                errors.append(f"pr_summaries PR #{number}: task_id 不存在: {task_id}")
        for function_id in summary.get("function_ids", []):
            if function_id not in function_map:
                errors.append(f"pr_summaries PR #{number}: function_id 不存在: {function_id}")
    if migration_ids != set(legacy_owner):
        errors.append("migration_manifest 未覆盖全部 legacy_id")
    return errors


def canonical_payload(project, phases, modules, functions, tasks, meetings, migration, pr_summaries, manual, system_map, pr_index, features):
    def clean(record):
        return {key: value for key, value in record.items() if not key.startswith("_")}
    derived_functions = [derive_function(function, tasks) for function in functions]
    aliases = {record["legacy_id"]: {"target_type": record["target_type"], "target_id": record["target_id"]} for record in migration.get("records", [])}
    return {
        "project": clean(project),
        "phases": [clean(item) for item in sorted(phases, key=lambda item: PHASE_ORDER.get(item["phase_id"], 99))],
        "modules": [clean(item) for item in sorted(modules, key=lambda item: item["module_id"])],
        "functions": sorted(derived_functions, key=lambda item: (PHASE_ORDER.get(item["phase_id"], 99), item["module_id"], item["function_id"])),
        "tasks": [clean(item) for item in sorted(tasks, key=lambda item: item["task_id"])],
        "meetings": [clean(item) for item in sorted(meetings, key=lambda item: item["date"], reverse=True)],
        "manual": manual,
        "system_map": system_map,
        "pr_index": pr_index,
        "pr_summaries": pr_summaries,
        "features": features,
        "migration": {"source_count": len(migration.get("records", [])), "generated_from": migration.get("generated_from", []), "records": migration.get("records", []), "aliases": aliases},
    }


def digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def write_data(payload, registry_digest):
    output = {"schema_version": 2, "generated_at": utc_now(), "source_base_commit": git_revision(), "registry_digest": registry_digest, **payload}
    encoded = json.dumps(output, ensure_ascii=False, indent=2).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    DATA_JS.write_text("window.SUPPORTPORTAL_PROJECT_DATA = " + encoded + "\n", encoding="utf-8")


def load_all():
    project = read_json(PROJECT_DIR / "project.json")
    phases = read_records(PHASE_DIR, "phase_id")
    modules = read_records(MODULE_DIR, "module_id")
    functions = read_records(FUNCTION_DIR, "function_id")
    tasks = read_records(TASK_DIR, "task_id")
    meetings = read_records(MEETING_DIR, "meeting_id")
    migration = read_json(PROJECT_DIR / "migration_manifest.json")
    pr_summaries = read_json(PR_SUMMARIES)
    manual = read_json(PROJECT_DIR / "manual.json")
    system_map = read_json(PROJECT_DIR / "system_map.json")
    pr_index = read_json(PR_INDEX) if PR_INDEX.exists() else {"schema_version": 1, "fetched_at": None, "repository": "ZilingXie/SupportPortal", "prs": []}
    features = parse_feature_list(ROOT / "docs/feature_list.md")
    return project, phases, modules, functions, tasks, meetings, migration, pr_summaries, manual, system_map, pr_index, features


def check_generated(expected_digest):
    if not DATA_JS.exists():
        raise ValueError(f"生成物不存在：{DATA_JS}")
    source = DATA_JS.read_text(encoding="utf-8")
    match = re.fullmatch(r"window\.SUPPORTPORTAL_PROJECT_DATA = (\{.*\})\n", source, re.S)
    if not match:
        raise ValueError("projectoverview-data.js 格式不正确")
    payload = json.loads(match.group(1))
    if payload.get("schema_version") != 2:
        raise ValueError("projectoverview-data.js schema_version 必须为 2")
    if payload.get("registry_digest") != expected_digest:
        raise ValueError("projectoverview-data.js 已过期，请重新运行 --write")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-prs", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.refresh_prs:
        refresh_prs()
    try:
        records = load_all()
        errors = validate_records(*records[:8])
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        payload = canonical_payload(*records)
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
    except (ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
