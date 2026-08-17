from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "docs/project"
DATA_JS = ROOT / "docs/projectoverview-data.js"


class ProjectOverviewContractTests(unittest.TestCase):
    def test_registry_and_generated_data_are_current(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/generate_project_overview.py", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"Project Overview validation passed: [0-9a-f]{64}")

    def test_phase_function_task_registry_is_consistent(self) -> None:
        phase_paths = sorted((PROJECT_DIR / "phases").glob("*.json"))
        module_paths = sorted((PROJECT_DIR / "modules").glob("*.json"))
        function_paths = sorted((PROJECT_DIR / "functions").glob("*.json"))
        task_paths = sorted((PROJECT_DIR / "tasks").glob("*.json"))
        phase_ids = {json.loads(path.read_text(encoding="utf-8"))["phase_id"] for path in phase_paths}
        module_ids = {json.loads(path.read_text(encoding="utf-8"))["module_id"] for path in module_paths}
        functions = {json.loads(path.read_text(encoding="utf-8"))["function_id"]: json.loads(path.read_text(encoding="utf-8")) for path in function_paths}
        task_ids = [json.loads(path.read_text(encoding="utf-8"))["task_id"] for path in task_paths]
        self.assertEqual(phase_ids, {"phase-1", "phase-2", "phase-3"})
        self.assertEqual(len(task_ids), len(set(task_ids)))
        self.assertGreaterEqual(len(task_ids), 60)
        self.assertTrue(all(task_id.startswith(("p1-", "p2-", "p3-")) for task_id in task_ids))
        self.assertFalse(any(path.stem.startswith("TS-") or path.stem.startswith("AG-") for path in task_paths))
        for path in task_paths:
            task = json.loads(path.read_text(encoding="utf-8"))
            self.assertRegex(task["task_id"], r"^p[123]-\d{2,}$", str(path))
            self.assertEqual(path.stem, task["task_id"])
            self.assertNotRegex(task["title"].strip(), r"(?i)^(?:p\d+|phase\s+\d+|长期方向|低优先级保留)$", str(path))
            self.assertIn(task["status"], {"planned", "active", "review", "blocked", "done"})
            self.assertIn(task["phase_id"], phase_ids)
            self.assertIn(task["module_id"], module_ids)
            self.assertIn(task["function_id"], functions)
            self.assertEqual(task["phase_id"], functions[task["function_id"]]["phase_id"])
            self.assertEqual(task["module_id"], functions[task["function_id"]]["module_id"])
            if task["status"] == "done":
                self.assertTrue(task["evidence"], str(path))
            if task["status"] == "blocked":
                self.assertTrue(task["blockers"], str(path))
            if task["status"] != "done":
                self.assertTrue(task["next_action"].strip(), str(path))
        phase1_tasks = [json.loads(path.read_text(encoding="utf-8")) for path in task_paths if json.loads(path.read_text(encoding="utf-8"))["phase_id"] == "phase-1"]
        self.assertTrue(all(task["status"] == "done" for task in phase1_tasks))

    def test_generated_page_is_file_url_safe_and_read_only(self) -> None:
        html = (ROOT / "docs/projectoverview.html").read_text(encoding="utf-8")
        self.assertIn('<script src="./projectoverview-data.js"></script>', html)
        self.assertIn("SUPPORTPORTAL_PROJECT_DATA", DATA_JS.read_text(encoding="utf-8"))
        self.assertIn("/blob/main/", html)
        self.assertNotIn("fetch(", html)
        self.assertNotIn("localStorage", html)
        for marker in ("项目动态", "任务看板", "会议记录", "能力地图", "系统地图", "用户手册", "汇报模式"):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        self.assertNotIn("项目资料", html)
        self.assertNotIn("交流记录", html)
        self.assertNotIn("项目议题", html)
        self.assertNotIn("topic-copy", html)
        self.assertIn("function showModule(moduleId)", html)
        self.assertIn('id=\"module-${escapeHtml(module.module_id)}\"', html)
        for panel_id in ("dynamic", "plan", "human-events", "functions", "system-map", "manual"):
            with self.subTest(panel_id=panel_id):
                self.assertIn(f'id="panel-{panel_id}"', html)
        for detail_prefix in ('raw.startsWith("task-")', 'raw.startsWith("meeting-")', 'raw.startsWith("function-")'):
            with self.subTest(detail_prefix=detail_prefix):
                self.assertIn(detail_prefix, html)

    def test_compact_board_meeting_dialog_and_full_feature_list_contract(self) -> None:
        html = (ROOT / "docs/projectoverview.html").read_text(encoding="utf-8")
        self.assertIn('<dialog id="meetingDialog"', html)
        self.assertIn("function showMeeting(meetingId)", html)
        self.assertIn('<span class="meeting-task-title">${escapeHtml(task.title)}</span>', html)
        self.assertIn('<span class="task-id">${escapeHtml(idLabel(task.task_id))}</span><h4>', html)
        self.assertNotIn('task.priority || "unclassified"', html)
        self.assertNotIn('class="task-summary"', html)
        self.assertIn("grid-template-columns: minmax(0, 1fr); grid-template-rows: auto 1fr", html)
        self.assertIn('(section.completed || []).map((item)', html)
        self.assertNotIn('(section.completed || []).slice(0, 8)', html)

    def test_public_pr_snapshot_does_not_include_bodies(self) -> None:
        payload = json.loads((PROJECT_DIR / "generated/pr-index.json").read_text(encoding="utf-8"))
        self.assertIsInstance(payload["prs"], list)
        for pr in payload["prs"]:
            self.assertNotIn("body", pr)
            self.assertNotIn("bodyHTML", pr)
        public_data = DATA_JS.read_text(encoding="utf-8")
        self.assertNotRegex(public_data, r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

    def test_legacy_pages_and_deep_link_are_preserved(self) -> None:
        for relative in (
            "docs/roadmap.html",
            "docs/roadmap/phase1.html",
            "docs/roadmap/phase2.html",
            "docs/roadmap/phase3.html",
            "docs/roadmap/meetings.html",
        ):
            self.assertTrue((ROOT / relative).exists(), relative)
        meetings = (ROOT / "docs/roadmap/meetings.html").read_text(encoding="utf-8")
        self.assertIn('id: "ticketing-system-2026-08-10"', meetings)
        roadmap = (ROOT / "docs/roadmap.html").read_text(encoding="utf-8")
        self.assertIn("projectoverview.html", roadmap)
        self.assertNotIn("localStorage", roadmap)

    def test_generated_js_is_single_json_assignment(self) -> None:
        source = DATA_JS.read_text(encoding="utf-8")
        match = re.fullmatch(r"window\.SUPPORTPORTAL_PROJECT_DATA = (\{.*\})\n", source, re.S)
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        self.assertEqual(payload["schema_version"], 2)
        self.assertRegex(payload["registry_digest"], r"^[0-9a-f]{64}$")
        self.assertIn("phases", payload)
        self.assertIn("modules", payload)
        self.assertIn("functions", payload)
        self.assertNotIn("topics", payload)
        self.assertNotIn("milestones", payload)


if __name__ == "__main__":
    unittest.main()
