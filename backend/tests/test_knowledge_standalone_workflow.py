"""Standalone knowledge-source workflow tests (governance plan WP1).

Case-less sources (CSD issues, article snapshots) run the same
Summary → Review → WeKnora promotion pipeline on fresh sessions, with
dedup per (source identity, version). External boundaries (Hermes
gateway, WeKnora) are scripted fakes; call counts are asserted.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.prompt_runtime import initialize_prompt_runtime

# Mirror service startup: without PROMPT_RELEASE_ID the code catalog snapshot
# resolves the summary/review manuals.
initialize_prompt_runtime()

from backend.services.knowledge_standalone_workflow import (
    drain_standalone_knowledge_tasks,
    queue_standalone_summary_for_source,
    standalone_summary_task_id_for,
)


def _enable_real_mode(env: dict) -> None:
    import os
    from unittest.mock import patch

    patcher = patch.dict(os.environ, {
        "HERMES_CASE_WORKFLOW_MODE": "real",
        "HERMES_AGENT_BASE_URL": "http://hermes.test",
        "HERMES_AGENT_API_TOKEN": "test-token",
    }, clear=False)
    patcher.start()
    return patcher


CSD_INTAKE = {
    "intake_id": "ksi-csd-1",
    "source_type": "csd_issue",
    "source_id": "CSD-77",
    "source_updated_at": "2026-10-02T00:00:00+00:00",
    "payload": {"issue": "join failures in region eu", "resolution": "fixed upstream"},
    "references_payload": {},
}

ARTICLE_INTAKE = {
    "intake_id": "ksi-art-1",
    "source_type": "article",
    "source_id": "doc-9",
    "source_updated_at": "2026-10-02T00:00:00+00:00",
    "payload": {"title": "Token guide", "body": "raw snapshot"},
    "references_payload": {},
}


class _ScriptedHermes:
    """Two-run client: summary then review; captures submissions."""

    def __init__(self, summary_output: dict, review_output: dict):
        self.runs: list[dict[str, Any]] = []
        self._outputs = [summary_output, review_output]

    def start_run(self, *, session_id, instructions, input_text, idempotency_key,
                  workspace_key=None, enabled_toolsets=None):
        self.runs.append({
            "session_id": session_id,
            "instructions": instructions,
            "input_text": input_text,
            "idempotency_key": idempotency_key,
            "workspace_key": workspace_key,
            "toolsets": list(enabled_toolsets or []),
        })
        return {"run_id": f"run-{len(self.runs)}", "status": "started"}

    def get_run(self, run_id):
        index = int(str(run_id).rsplit("-", 1)[-1]) - 1
        output = self._outputs[index]
        return {
            "run_id": run_id,
            "status": "completed",
            "output": f"```json\n{json.dumps(output)}\n```",
        }

    def stop_run(self, run_id):
        return {"run_id": run_id, "status": "stopping"}


class _NoWeKnora:
    """Evidence surface: configured = False (both surfaces unavailable)."""

    configured = lambda self: False  # noqa: E731

    def search(self, query):
        raise RuntimeError("unavailable")


class _NoMemory:
    is_configured = lambda self: False  # noqa: E731
    has_memory_identity = lambda self: False  # noqa: E731


SUMMARY_OUTPUT = {
    "problem_description": "join failures in region eu",
    "candidates": [
        {
            "candidate_id": "c1",
            "statement": "Region EU join failures were fixed upstream.",
            "context": "CSD issue CSD-77",
            "evidence_references": ["payload.resolution"],
        }
    ],
}

REVIEW_OUTPUT = {
    "decisions": [
        {
            "candidate_id": "c1",
            "candidate_type": "knowledge",
            "decision": "new",
            "confidence": 0.9,
            "rationale": "nothing similar (search unavailable -> downgraded by server anyway)",
            "proposed_content": "Region EU join failures were fixed upstream.",
            "target_object": None,
            "target_version": None,
        }
    ],
}


class StandaloneWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.patcher = _enable_real_mode({})
        self.addCleanup(self.patcher.stop)
        self.repository = InMemoryTicketRepository()
        self.repository.accept_knowledge_source(
            {
                "schema_version": "knowledge-source-v1",
                **{k: v for k, v in CSD_INTAKE.items()
                   if k not in ("intake_id", "references_payload")},
                "references": {},
            },
            now_value="2026-10-02T00:00:01+00:00",
        )
        # Use the STORED intake row (accept generates the real intake_id and
        # normalizes references_payload); tests must queue from it so the
        # drain runner can read the frozen snapshot back by intake_id.
        stored = [
            row for row in self.repository._knowledge_source_intakes.values()
            if row["source_type"] == "csd_issue" and row["source_id"] == "CSD-77"
        ]
        self.intake = stored[0] if stored else dict(CSD_INTAKE)

    def test_queue_dedup_per_source_version(self) -> None:
        first = queue_standalone_summary_for_source(
            self.repository, intake=dict(CSD_INTAKE), now_value="2026-10-02T00:01:00+00:00"
        )
        second = queue_standalone_summary_for_source(
            self.repository, intake=dict(CSD_INTAKE), now_value="2026-10-02T00:02:00+00:00"
        )
        self.assertIsNotNone(first)
        self.assertEqual(first["summary_task_id"], second["summary_task_id"])
        self.assertEqual(len(self.repository.list_standalone_summary_tasks()), 1)
        # A NEWER version is a different task id by construction.
        newer = dict(CSD_INTAKE, source_updated_at="2026-10-02T06:00:00+00:00")
        third = queue_standalone_summary_for_source(
            self.repository, intake=newer, now_value="2026-10-02T06:00:01+00:00"
        )
        self.assertNotEqual(third["summary_task_id"], first["summary_task_id"])
        self.assertEqual(len(self.repository.list_standalone_summary_tasks()), 2)

    def test_end_to_end_summary_review_promotion(self) -> None:
        task = queue_standalone_summary_for_source(
            self.repository, intake=dict(self.intake), now_value="2026-10-02T00:01:00+00:00"
        )
        self.assertEqual(task["summary_task_id"],
                         standalone_summary_task_id_for("csd_issue", "CSD-77", CSD_INTAKE["source_updated_at"]))

        client = _ScriptedHermes(SUMMARY_OUTPUT, REVIEW_OUTPUT)
        result = drain_standalone_knowledge_tasks(
            self.repository,
            client=client,
            weknora_client=_NoWeKnora(),
            memory_client=_NoMemory(),
            limit=5,
        )
        self.assertEqual(result, {"executed": 2, "failed": 0})

        # Two separate sessions: summary then review.
        sessions = [run["session_id"] for run in client.runs]
        self.assertEqual(len(sessions), 2)
        self.assertNotEqual(sessions[0], sessions[1])
        # The frozen snapshot reached the summary run input.
        self.assertIn("knowledge-source-bundle-v1", client.runs[0]["input_text"])
        self.assertIn("CSD-77", client.runs[0]["input_text"])

        # The unavailable evidence surface downgraded the `new` write to
        # human_review (fail-closed), and the promotion bridge enqueued it.
        promotions = self.repository.list_weknora_promotions()
        self.assertEqual(len(promotions), 1)
        self.assertEqual(promotions[0]["decision"], "human_review")
        self.assertIn("[downgraded from new:", promotions[0]["candidate_payload"]["note"])
        self.assertEqual(promotions[0]["source_type"], "knowledge_source_review")
        self.assertIn("knowledge-source-summary:csd_issue:CSD-77", promotions[0]["source_id"])

        # Idempotent drain replay: no further runs, no duplicate promotions.
        replay = drain_standalone_knowledge_tasks(
            self.repository, client=client,
            weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
        )
        self.assertEqual(replay, {"executed": 0, "failed": 0})
        self.assertEqual(len(client.runs), 2)
        self.assertEqual(len(self.repository.list_weknora_promotions()), 1)

    def test_article_source_queues_standalone_summary(self) -> None:
        task = queue_standalone_summary_for_source(
            self.repository, intake=dict(ARTICLE_INTAKE), now_value="2026-10-02T00:01:00+00:00"
        )
        self.assertIsNotNone(task)
        self.assertEqual(
            task["summary_task_id"],
            standalone_summary_task_id_for("article", "doc-9", ARTICLE_INTAKE["source_updated_at"]),
        )


if __name__ == "__main__":
    unittest.main()
