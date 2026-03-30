from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.services.rag_benchmark_session import (
    build_local_benchmark_session_record,
    parse_rag_change_log_entries,
    run_local_benchmark_session,
)


class _FakeSessionRepository:
    def __init__(self) -> None:
        self.latest_completed_session: dict[str, object] | None = None
        self.sessions: dict[str, dict[str, object]] = {}
        self.session_writes: list[dict[str, object]] = []

    def upsert_rag_benchmark_session(self, *, session: dict[str, object]) -> None:
        stored = dict(session)
        session_id = str(stored.get("benchmark_session_id") or "").strip()
        self.sessions[session_id] = stored
        self.session_writes.append(stored)

    def get_latest_completed_rag_benchmark_session(self) -> dict[str, object] | None:
        if self.latest_completed_session is None:
            return None
        return dict(self.latest_completed_session)

    def get_rag_benchmark_session(self, benchmark_session_id: str) -> dict[str, object] | None:
        payload = self.sessions.get(benchmark_session_id)
        return dict(payload) if payload is not None else None


class RagBenchmarkSessionTests(unittest.TestCase):
    def test_parse_rag_change_log_entries_preserves_file_order_and_ignores_malformed_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            changelog_path = Path(tmpdir) / "rag_change_log.md"
            changelog_path.write_text(
                "\n".join(
                    [
                        "# RAG Change Log",
                        "",
                        "## 2026-03-30 - First change",
                        "",
                        "- Summary: First summary.",
                        "",
                        "## 2026-03-30 - Missing summary",
                        "",
                        "- Reason: Should be ignored.",
                        "",
                        "## 2026-03-30 - Third change",
                        "",
                        "- Summary: Third summary.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            entries = parse_rag_change_log_entries(changelog_path=changelog_path)

        self.assertEqual([entry["title"] for entry in entries], ["2026-03-30 - First change", "2026-03-30 - Third change"])
        self.assertEqual([entry["entry_index"] for entry in entries], [0, 1])

    def test_build_local_benchmark_session_record_uses_baseline_message_for_first_tracked_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            changelog_path = Path(tmpdir) / "rag_change_log.md"
            changelog_path.write_text(
                "\n".join(
                    [
                        "# RAG Change Log",
                        "",
                        "## 2026-03-30 - First change",
                        "",
                        "- Summary: First summary.",
                        "",
                        "## 2026-03-30 - Second change",
                        "",
                        "- Summary: Second summary.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            dataset_paths = [Path(tmpdir) / f"dataset-{index}.json" for index in range(3)]
            benchmark_specs = [
                {"dataset_name": f"dataset-{index}", "label": f"Label {index}", "path": path}
                for index, path in enumerate(dataset_paths, start=1)
            ]
            repository = _FakeSessionRepository()

            record = build_local_benchmark_session_record(
                repository=repository,
                session_name="baseline-session",
                benchmark_specs=benchmark_specs,
                changelog_path=changelog_path,
                benchmark_session_id="BSESS-BASELINE",
            )

        self.assertEqual(record["benchmark_session_id"], "BSESS-BASELINE")
        self.assertIsNone(record["previous_session_id"])
        self.assertEqual(record["improvement_entries"], [])
        self.assertEqual(record["changelog_end_entry_index"], 1)
        self.assertIn("No previous tracked benchmark session", str(record["improvement_summary"]))
        self.assertEqual(len(list(record["benchmark_catalog_snapshot"])), 3)

    def test_build_local_benchmark_session_record_collects_entries_after_previous_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            changelog_path = Path(tmpdir) / "rag_change_log.md"
            changelog_path.write_text(
                "\n".join(
                    [
                        "# RAG Change Log",
                        "",
                        "## 2026-03-30 - First change",
                        "",
                        "- Summary: First summary.",
                        "",
                        "## 2026-03-31 - Second change",
                        "",
                        "- Summary: Second summary.",
                        "",
                        "## 2026-04-01 - Third change",
                        "",
                        "- Summary: Third summary.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            benchmark_specs = [
                {"dataset_name": "one", "label": "One", "path": Path(tmpdir) / "one.json"},
                {"dataset_name": "two", "label": "Two", "path": Path(tmpdir) / "two.json"},
                {"dataset_name": "three", "label": "Three", "path": Path(tmpdir) / "three.json"},
            ]
            repository = _FakeSessionRepository()
            repository.latest_completed_session = {
                "benchmark_session_id": "BSESS-OLD",
                "changelog_end_entry_index": 0,
                "status": "completed",
            }

            record = build_local_benchmark_session_record(
                repository=repository,
                session_name="delta-session",
                benchmark_specs=benchmark_specs,
                changelog_path=changelog_path,
                benchmark_session_id="BSESS-NEW",
            )

        self.assertEqual(record["previous_session_id"], "BSESS-OLD")
        self.assertEqual(
            [entry["title"] for entry in list(record["improvement_entries"])],
            ["2026-03-31 - Second change", "2026-04-01 - Third change"],
        )
        self.assertIn("2026-03-31 - Second change: Second summary.", str(record["improvement_summary"]))
        self.assertIn("2026-04-01 - Third change: Third summary.", str(record["improvement_summary"]))
        self.assertEqual(record["changelog_end_entry_index"], 2)

    def test_build_local_benchmark_session_record_uses_no_changes_message_when_changelog_has_no_new_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            changelog_path = Path(tmpdir) / "rag_change_log.md"
            changelog_path.write_text(
                "\n".join(
                    [
                        "# RAG Change Log",
                        "",
                        "## 2026-03-30 - First change",
                        "",
                        "- Summary: First summary.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            benchmark_specs = [
                {"dataset_name": "one", "label": "One", "path": Path(tmpdir) / "one.json"},
                {"dataset_name": "two", "label": "Two", "path": Path(tmpdir) / "two.json"},
                {"dataset_name": "three", "label": "Three", "path": Path(tmpdir) / "three.json"},
            ]
            repository = _FakeSessionRepository()
            repository.latest_completed_session = {
                "benchmark_session_id": "BSESS-OLD",
                "changelog_end_entry_index": 0,
                "status": "completed",
            }

            record = build_local_benchmark_session_record(
                repository=repository,
                session_name="no-change-session",
                benchmark_specs=benchmark_specs,
                changelog_path=changelog_path,
                benchmark_session_id="BSESS-NEW",
            )

        self.assertEqual(record["improvement_entries"], [])
        self.assertIn("No new RAG changelog entries", str(record["improvement_summary"]))

    def test_run_local_benchmark_session_runs_three_linked_eval_runs_in_catalog_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            changelog_path = Path(tmpdir) / "rag_change_log.md"
            changelog_path.write_text(
                "\n".join(
                    [
                        "# RAG Change Log",
                        "",
                        "## 2026-03-31 - Benchmark prep",
                        "",
                        "- Summary: Prepare benchmark session.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            benchmark_specs = []
            for name in ["alpha", "beta", "gamma"]:
                path = Path(tmpdir) / f"{name}.json"
                path.write_text("[]", encoding="utf-8")
                benchmark_specs.append({"dataset_name": name, "label": name.title(), "path": path})
            repository = _FakeSessionRepository()
            calls: list[dict[str, object]] = []

            def fake_run_benchmark(**kwargs):
                calls.append(dict(kwargs))
                dataset_path = Path(str(kwargs["dataset_path"]))
                return {
                    "eval_run_id": f"EVAL-{dataset_path.stem.upper()}",
                    "dataset_name": dataset_path.name,
                    "benchmark_version": dataset_path.stem,
                    "dataset_schema_version": "mixed_route_v2",
                    "judge_models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"],
                    "case_count": 1,
                    "metrics": {"route_accuracy": 1.0},
                }

            summary = run_local_benchmark_session(
                repository=repository,
                session_name="session-a",
                top_k=7,
                benchmark_specs=benchmark_specs,
                changelog_path=changelog_path,
                run_benchmark_fn=fake_run_benchmark,
            )

        self.assertEqual(summary["session_name"], "session-a")
        self.assertEqual(len(summary["runs"]), 3)
        self.assertEqual([Path(str(call["dataset_path"])).stem for call in calls], ["alpha", "beta", "gamma"])
        self.assertTrue(all(call["benchmark_session_id"] == summary["benchmark_session_id"] for call in calls))
        self.assertEqual(
            [str(call["experiment_id"]) for call in calls],
            ["session-a::alpha", "session-a::beta", "session-a::gamma"],
        )
        self.assertEqual(repository.session_writes[0]["status"], "queued")
        self.assertEqual(repository.session_writes[-1]["status"], "completed")

    def test_run_local_benchmark_session_marks_failed_when_a_child_run_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            changelog_path = Path(tmpdir) / "rag_change_log.md"
            changelog_path.write_text(
                "\n".join(
                    [
                        "# RAG Change Log",
                        "",
                        "## 2026-03-31 - Benchmark prep",
                        "",
                        "- Summary: Prepare benchmark session.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            benchmark_specs = []
            for name in ["alpha", "beta", "gamma"]:
                path = Path(tmpdir) / f"{name}.json"
                path.write_text("[]", encoding="utf-8")
                benchmark_specs.append({"dataset_name": name, "label": name.title(), "path": path})
            repository = _FakeSessionRepository()
            calls: list[dict[str, object]] = []

            def fake_run_benchmark(**kwargs):
                calls.append(dict(kwargs))
                if Path(str(kwargs["dataset_path"])).stem == "beta":
                    raise RuntimeError("beta failed")
                dataset_path = Path(str(kwargs["dataset_path"]))
                return {
                    "eval_run_id": f"EVAL-{dataset_path.stem.upper()}",
                    "dataset_name": dataset_path.name,
                    "benchmark_version": dataset_path.stem,
                    "dataset_schema_version": "mixed_route_v2",
                    "judge_models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"],
                    "case_count": 1,
                    "metrics": {"route_accuracy": 1.0},
                }

            with self.assertRaisesRegex(RuntimeError, "beta failed"):
                run_local_benchmark_session(
                    repository=repository,
                    session_name="session-b",
                    top_k=5,
                    benchmark_specs=benchmark_specs,
                    changelog_path=changelog_path,
                    run_benchmark_fn=fake_run_benchmark,
                )

        self.assertEqual([Path(str(call["dataset_path"])).stem for call in calls], ["alpha", "beta"])
        self.assertEqual(repository.session_writes[-1]["status"], "failed")
        self.assertEqual(repository.session_writes[-1]["error_message"], "beta failed")
