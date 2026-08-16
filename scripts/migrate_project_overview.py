#!/usr/bin/env python3
"""Seed the project registry from the legacy roadmap and meeting pages."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TODAY = date.today().isoformat()

LANE_MAP = {
    "engineer-multi-agent": ("agent-collaboration", "long-term-agent-collaboration"),
    "assignment-ui": ("engineer-workspace", "phase-3-engineer-workflow"),
    "billing-routing": ("account-automation", "phase-2-controlled-validation"),
    "routing-rules": ("client-experience", "phase-2-controlled-validation"),
    "rag-vs-kg": ("rag-knowledge", "phase-2-controlled-validation"),
}

MEETING_TOPIC_MAP = {
    "ticketing-system-2026-08-10": ("account-automation", "phase-2-controlled-validation"),
    "agent-system-2026-06-18": ("agent-collaboration", "phase-1"),
}


def decode_js_string(value: str) -> str:
    try:
        return json.loads('"' + value + '"')
    except json.JSONDecodeError:
        return value.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_lane_items(source: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lane_re = re.compile(
        r'\n      \{\n        id: "(?P<lane>[^"]+)"(?P<body>.*?)(?=\n      \},\n      \{\n        id: |\n    \];)',
        re.S,
    )
    item_re = re.compile(
        r'\{ id: "(?P<id>[^"]+)", status: "(?P<status>[^"]+)", '
        r'label: "(?P<label>(?:\\.|[^"\\])*)", horizon: "(?P<horizon>(?:\\.|[^"\\])*)" \}'
    )
    tasks: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    for lane_match in lane_re.finditer(source):
        lane_id = lane_match.group("lane")
        topic_id, milestone_id = LANE_MAP.get(lane_id, ("platform-delivery", "phase-2-controlled-validation"))
        next_match = re.search(r'\n        next: \[(?P<items>.*?)\n        \]', lane_match.group("body"), re.S)
        if not next_match:
            continue
        for item_match in item_re.finditer(next_match.group("items")):
            item_id = item_match.group("id")
            status = item_match.group("status")
            label = decode_js_string(item_match.group("label"))
            horizon = decode_js_string(item_match.group("horizon"))
            mapped_status = {"todo": "planned", "doing": "active", "blocked": "blocked"}.get(status, "review")
            priority = next((token for token in ("P0", "P1", "P2") if token in label), "unclassified")
            task = {
                "schema_version": 1,
                "task_id": item_id,
                "title": label.split("：", 1)[0][:100],
                "topic_id": topic_id,
                "related_topic_ids": [],
                "milestone_id": milestone_id,
                "status": mapped_status,
                "priority": priority,
                "owner": "unassigned",
                "summary": label,
                "next_action": label if mapped_status != "blocked" else "明确解除 blocker 的验证步骤。",
                "acceptance_criteria": [f"完成 {horizon} 维度的交付和验证。"],
                "blockers": [label] if mapped_status == "blocked" else [],
                "evidence": [],
                "source_refs": ["docs/roadmap.html#lanes"],
                "created_at": TODAY,
                "updated_at": TODAY,
                "history": [{"at": TODAY, "event": "migrated", "summary": f"从 Roadmap lane {lane_id} 迁移。"}],
                "legacy_refs": [{"source": "docs/roadmap.html", "lane_id": lane_id, "item_id": item_id}],
            }
            tasks.append(task)
            manifest.append({
                "source_ref": "docs/roadmap.html",
                "legacy_id": item_id,
                "target_type": "task",
                "target_id": item_id,
                "disposition": "migrated",
            })
    return tasks, manifest


def parse_meetings(source: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    meetings: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    meeting_re = re.compile(
        r'\{\n        id: "(?P<id>[^"]+)",\n        date: "(?P<date>[^"]+)",(?P<body>.*?)(?=\n      \},\n      \{\n        id: |\n    \];)',
        re.S,
    )
    work_item_re = re.compile(
        r'\{ id: "(?P<id>[^"]+)", owner: "(?P<owner>(?:\\.|[^"\\])*)", '
        r'action: "(?P<action>(?:\\.|[^"\\])*)", status: "(?P<status>[^"]+)", '
        r'(?:(?:prs|docs): .*?, )*proof: "(?P<proof>(?:\\.|[^"\\])*)" \}'
    )
    for meeting_match in meeting_re.finditer(source):
        meeting_id = meeting_match.group("id")
        body = meeting_match.group("body")
        title_match = re.search(r'title: "((?:\\.|[^"\\])*)"', body)
        summary_match = re.search(r'summary: "((?:\\.|[^"\\])*)"', body)
        people_match = re.search(r'people: \[(.*?)\]', body, re.S)
        people = re.findall(r'"((?:\\.|[^"\\])*)"', people_match.group(1)) if people_match else []
        topic_id, milestone_id = MEETING_TOPIC_MAP.get(meeting_id, ("platform-delivery", "phase-2-controlled-validation"))
        item_ids: list[str] = []
        for item_match in work_item_re.finditer(body):
            item_id = item_match.group("id")
            item_ids.append(item_id)
            status = item_match.group("status")
            action = decode_js_string(item_match.group("action"))
            proof = decode_js_string(item_match.group("proof"))
            owner = decode_js_string(item_match.group("owner"))
            prs = [int(value) for value in re.findall(r'\d+', body[item_match.start():item_match.end()].split("prs:", 1)[-1].split("]", 1)[0])] if "prs:" in item_match.group(0) else []
            mapped_status = {"todo": "planned", "confirm": "planned", "monitor": "active", "done": "done"}.get(status, "review")
            evidence = [
                {"type": "pr", "number": number, "url": f"https://github.com/ZilingXie/SupportPortal/pull/{number}", "label": f"PR #{number}"}
                for number in prs
            ]
            if mapped_status == "done" and not evidence:
                mapped_status = "review"
            task = {
                "schema_version": 1,
                "task_id": item_id,
                "title": action[:100],
                "topic_id": topic_id,
                "related_topic_ids": [],
                "milestone_id": milestone_id,
                "status": mapped_status,
                "priority": "unclassified",
                "owner": owner or "unassigned",
                "summary": action,
                "next_action": "补齐验收证据并更新状态。" if mapped_status != "done" else "",
                "acceptance_criteria": [proof] if proof else [],
                "blockers": [],
                "evidence": evidence,
                "source_refs": [f"docs/roadmap/meetings.html#{meeting_id}"],
                "created_at": meeting_match.group("date"),
                "updated_at": TODAY,
                "history": [{"at": TODAY, "event": "migrated", "summary": f"从 Meeting {meeting_id} 迁移。"}],
                "legacy_refs": [{"source": "docs/roadmap/meetings.html", "meeting_id": meeting_id, "item_id": item_id}],
            }
            tasks.append(task)
            manifest.append({
                "source_ref": "docs/roadmap/meetings.html",
                "legacy_id": item_id,
                "target_type": "task",
                "target_id": item_id,
                "disposition": "migrated",
            })
        meetings.append({
            "schema_version": 1,
            "meeting_id": meeting_id,
            "date": meeting_match.group("date"),
            "title": decode_js_string(title_match.group(1)) if title_match else meeting_id,
            "participants": [decode_js_string(person) for person in people],
            "summary": decode_js_string(summary_match.group(1)) if summary_match else "",
            "decisions": [],
            "open_questions": [],
            "task_ids": item_ids,
            "source_refs": [f"docs/roadmap/meetings.html#{meeting_id}"],
            "legacy_anchor": f"./roadmap/meetings.html#{meeting_id}",
        })
    return meetings, tasks, manifest


def evidence(number: int, label: str) -> dict[str, Any]:
    return {"type": "pr", "number": number, "url": f"https://github.com/ZilingXie/SupportPortal/pull/{number}", "label": label}


def extra_tasks() -> list[dict[str, Any]]:
    records = [
        ("project-overview", "建立 SupportPortal Project Overview 单一维护入口", "platform-delivery", "phase-2-controlled-validation", "active", "Zac", "实现 registry、生成器、汇总页面和旧 URL 兼容。", []),
        ("billing-persona-registry", "Account Automation Persona registry 与 ownership 回复", "account-automation", "phase-2-controlled-validation", "done", "Zac", "Persona preset、版本固定和客户 ownership 回复已交付。", [749, 750]),
        ("account-rerun-recovery", "Account full rerun 的恢复、幂等和 fail-fast", "account-automation", "phase-2-controlled-validation", "done", "Zac", "Rerun 具备冻结、preflight、恢复和结果边界。", [738, 739, 740, 741, 742, 745, 746, 747, 748, 751]),
        ("account-failure-alerts", "Account 失败后的 Human Review 和负责人告警", "account-automation", "phase-2-controlled-validation", "done", "Zac", "重试耗尽后停止客户回复并发送脱敏故障告警。", [744]),
        ("routing-taxonomy", "Account route taxonomy 和 filter membership", "account-automation", "phase-2-controlled-validation", "active", "Zac", "区分 registered Automation、Human Review 和诊断 fallback。", [728, 731]),
        ("routing-security-compliance", "Security & Compliance classification-only route", "account-automation", "phase-2-controlled-validation", "done", "Zac", "敏感请求保持分类和人工边界，不自动生成客户回复。", [729]),
        ("billing-idempotency", "Account/Webhook 和 reply job 幂等边界", "account-automation", "phase-2-controlled-validation", "active", "Zac", "避免重复建单、派单、回复和旧版本 job 覆盖新状态。", [732, 751]),
        ("agent-rules", "AI 项目维护规则和详细流程分层", "platform-delivery", "phase-2-controlled-validation", "done", "Zac", "热路径规则和按需读取的工作流细节已分离。", [734]),
        ("phase2-fraud-field-contract", "明确 Fraud 与 Account Suspension 的字段边界", "account-automation", "phase-2-controlled-validation", "planned", "Suhird", "确定 required/optional 字段，避免缺失字段造成无限追问。", []),
        ("admin-environment-config-inventory", "Admin Environment Config names-only inventory", "admin-operations", "phase-2-controlled-validation", "active", "unassigned", "只展示合法配置名，不返回 value 或 value-derived metadata。", []),
        ("client-rich-attachments", "Client 对话支持图片和更多日志附件", "client-experience", "phase-2-controlled-validation", "planned", "unassigned", "补齐图片和 txt/log/md 等附件处理。", []),
        ("client-streaming-output", "Client 对话支持流式输出", "client-experience", "phase-2-controlled-validation", "planned", "unassigned", "定义流式回复、断线和最终消息一致性。", []),
    ]
    tasks: list[dict[str, Any]] = []
    for task_id, title, topic_id, milestone_id, status, owner, summary, prs in records:
        tasks.append({
            "schema_version": 1,
            "task_id": task_id,
            "title": title,
            "topic_id": topic_id,
            "related_topic_ids": [],
            "milestone_id": milestone_id,
            "status": status,
            "priority": "unclassified",
            "owner": owner,
            "summary": summary,
            "next_action": "" if status == "done" else summary,
            "acceptance_criteria": [summary],
            "blockers": [],
            "evidence": [evidence(number, f"PR #{number}") for number in prs],
            "source_refs": ["docs/roadmap.html", "docs/feature_list.md"],
            "created_at": TODAY,
            "updated_at": TODAY,
            "history": [{"at": TODAY, "event": "seeded", "summary": "从 Roadmap、Meeting、PR 或 Feature List 汇总。"}],
            "legacy_refs": [],
        })
    return tasks


def merge_tasks(tasks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for task in tasks:
        task_id = task["task_id"]
        if task_id not in merged:
            merged[task_id] = task
            continue
        current = merged[task_id]
        current["source_refs"] = sorted(set(current["source_refs"] + task["source_refs"]))
        current["evidence"] = current["evidence"] + [item for item in task["evidence"] if item not in current["evidence"]]
        current["legacy_refs"] = current["legacy_refs"] + task["legacy_refs"]
        if current["status"] == "review" and task["status"] == "done":
            current["status"] = "done"
            current["next_action"] = ""
    return merged


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    roadmap = (root / "docs/roadmap.html").read_text(encoding="utf-8")
    meetings_source = (root / "docs/roadmap/meetings.html").read_text(encoding="utf-8")
    lane_tasks, lane_manifest = parse_lane_items(roadmap)
    meetings, meeting_tasks, meeting_manifest = parse_meetings(meetings_source)
    tasks = merge_tasks(lane_tasks + meeting_tasks + extra_tasks())
    for task in tasks.values():
        write_json(root / "docs/project/tasks" / f"{task['task_id']}.json", task)
    for meeting in meetings:
        write_json(root / "docs/project/meetings" / f"{meeting['meeting_id']}.json", meeting)
    manifest = {
        "schema_version": 1,
        "generated_from": [
            "docs/roadmap.html",
            "docs/roadmap/meetings.html",
            "docs/roadmap/phase2.html",
            "docs/roadmap/phase3.html",
            "docs/feature_list.md",
        ],
        "records": lane_manifest + meeting_manifest + [
            {"source_ref": "docs/roadmap.html", "legacy_id": task["task_id"], "target_type": "task", "target_id": task["task_id"], "disposition": "seeded"}
            for task in extra_tasks()
        ],
    }
    write_json(root / "docs/project/migration_manifest.json", manifest)
    print(json.dumps({"tasks": len(tasks), "meetings": len(meetings), "manifest_records": len(manifest["records"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
