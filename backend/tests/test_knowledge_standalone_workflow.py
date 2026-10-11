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
        "HERMES_KNOWLEDGE_WORKFLOW_ENABLED": "1",
        "KNOWLEDGE_SUMMARY_REVIEW_ENABLED": "1",
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


class _AgentMemoryOk:
    """Stage 3 (p2-194): a healthy AgentMemory retrieval surface (empty hits)."""

    def configured(self) -> bool:
        return True

    def search_knowledge(self, query: str) -> dict:
        return {"wiki_count": 2, "searched": 2, "hits": []}


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

    def test_object_array_timeline_completes_and_queues_review(self) -> None:
        """R25 (p2-184): the 18/18 Preproduction CSD Summary failures came
        from the model returning timeline as an array of objects. The
        tolerant normalizer must complete the Summary and enqueue the
        independent Review instead of failing the task."""
        queue_standalone_summary_for_source(
            self.repository, intake=dict(self.intake), now_value="2026-10-02T00:01:00+00:00"
        )
        object_timeline_output = {
            "problem_description": "join failures in region eu",
            "timeline": [
                {"time": "2026-10-01", "event": "reported"},
                {"time": "2026-10-02", "event": "resolved upstream"},
            ],
            "investigation_process": "checked region routing",
            "candidates": [
                {
                    "candidate_id": "c1",
                    "statement": "Region EU join failures were fixed upstream.",
                    "context": "CSD issue CSD-77",
                    "evidence_references": ["payload.resolution"],
                }
            ],
        }
        result = drain_standalone_knowledge_tasks(
            self.repository,
            client=_ScriptedHermes(object_timeline_output, REVIEW_OUTPUT),
            weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
        )
        self.assertEqual(result, {"executed": 2, "failed": 0})
        summary = self.repository.list_standalone_summary_tasks()[0]
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(
            summary["packet"]["timeline"],
            '{"event": "reported", "time": "2026-10-01"}\n'
            '{"event": "resolved upstream", "time": "2026-10-02"}',
        )
        reviews = list(self.repository._standalone_review_tasks.values())
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["status"], "completed")

    def test_article_source_queues_standalone_summary(self) -> None:
        task = queue_standalone_summary_for_source(
            self.repository, intake=dict(ARTICLE_INTAKE), now_value="2026-10-02T00:01:00+00:00"
        )
        self.assertIsNotNone(task)
        self.assertEqual(
            task["summary_task_id"],
            standalone_summary_task_id_for("article", "doc-9", ARTICLE_INTAKE["source_updated_at"]),
        )

    def test_review_rejects_contract_invalid_decision(self) -> None:
        """Review round 3, R3-8: a no_change WITHOUT its target violates the
        shared review contract and must fail the task — the old path ended
        the candidate as a bogus accepted no-op."""
        queue_standalone_summary_for_source(
            self.repository, intake=dict(self.intake), now_value="2026-10-02T00:01:00+00:00"
        )
        bad_review = {
            "decisions": [
                {
                    "candidate_id": "c1", "candidate_type": "knowledge",
                    "decision": "no_change", "confidence": 0.9,
                    "rationale": "duplicate — but the target is missing",
                    "proposed_content": "", "target_object": None, "target_version": None,
                }
            ]
        }
        result = drain_standalone_knowledge_tasks(
            self.repository,
            client=_ScriptedHermes(SUMMARY_OUTPUT, bad_review),
            weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
        )
        self.assertEqual(result, {"executed": 1, "failed": 1})
        review_rows = list(self.repository._standalone_review_tasks.values())
        self.assertEqual(review_rows[0]["status"], "failed")
        self.assertIn("review contract", review_rows[0]["error"])
        self.assertEqual(self.repository.list_weknora_promotions(), [])

    def test_newer_source_mid_review_fails_before_promotion(self) -> None:
        """Review round 4, R4-2: a source arriving WHILE the review run
        executes is caught by the post-run generation re-check — the old
        review must not complete and must enqueue ZERO promotions."""
        queue_standalone_summary_for_source(
            self.repository, intake=dict(self.intake), now_value="2026-10-02T00:01:00+00:00"
        )

        class _MidRunSourceInjection(_ScriptedHermes):
            """Runs the summary, then accepts a NEWER source version while the
            review run is in flight (between the review's start and get_run)."""

            def get_run(self, run_id):
                index = int(str(run_id).rsplit("-", 1)[-1]) - 1
                if index == 1:  # the review run
                    newer = dict(CSD_INTAKE, source_updated_at="2026-10-02T09:00:00+00:00")
                    self.repository.accept_knowledge_source(
                        {
                            "schema_version": "knowledge-source-v1",
                            **{k: v for k, v in newer.items()
                               if k not in ("intake_id", "references_payload")},
                            "references": {},
                        },
                        now_value="2026-10-02T09:00:01+00:00",
                    )
                return super().get_run(run_id)

        client = _MidRunSourceInjection(SUMMARY_OUTPUT, REVIEW_OUTPUT)
        client.repository = self.repository
        result = drain_standalone_knowledge_tasks(
            self.repository, client=client,
            weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
        )
        self.assertEqual(result, {"executed": 1, "failed": 1})
        review_rows = list(self.repository._standalone_review_tasks.values())
        self.assertEqual(review_rows[0]["status"], "failed")
        self.assertIn("advanced", review_rows[0]["error"])
        self.assertEqual(self.repository.list_weknora_promotions(), [])

    def test_run_failure_is_recorded_not_stranded(self) -> None:
        """Review round 2 R2-10: a failing run must land in status=failed —
        the old datetime.now(timezone) TypeError stranded the row running."""
        task = queue_standalone_summary_for_source(
            self.repository, intake=dict(self.intake), now_value="2026-10-02T00:01:00+00:00"
        )

        class _Boom:
            def start_run(self, **kwargs):
                raise RuntimeError("gateway down")

        result = drain_standalone_knowledge_tasks(
            self.repository, client=_Boom(),
            weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
        )
        self.assertEqual(result, {"executed": 0, "failed": 1})
        stored = self.repository.get_standalone_summary_task(task["summary_task_id"])
        self.assertEqual(stored["status"], "failed")
        self.assertIn("gateway down", stored["error"])

    def test_zero_candidates_allow_zero_decisions(self) -> None:
        """Review round 2 R2-9: an empty summary candidate set with an empty
        decision list is a legal (terminal) review — not a coverage failure."""
        queue_standalone_summary_for_source(
            self.repository, intake=dict(self.intake), now_value="2026-10-02T00:01:00+00:00"
        )
        empty_summary = {
            "problem_description": "nothing durable", "timeline": "",
            "investigation_process": "", "confirmed_facts": "",
            "root_cause_and_solution": "", "verification_results": "",
            "limitations_and_unconfirmed": "", "evidence_references": [],
            "candidates": [],
        }
        result = drain_standalone_knowledge_tasks(
            self.repository,
            client=_ScriptedHermes(empty_summary, {"decisions": []}),
            weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
        )
        self.assertEqual(result, {"executed": 2, "failed": 0})
        self.assertEqual(self.repository.list_weknora_promotions(), [])

    def test_review_rejects_diverged_summary_lineage(self) -> None:
        """Review round 2 R2-9: the review may only run against the summary
        generation queued for its own source version."""
        task = queue_standalone_summary_for_source(
            self.repository, intake=dict(self.intake), now_value="2026-10-02T00:01:00+00:00"
        )
        # First drain completes summary + review normally.
        drain_standalone_knowledge_tasks(
            self.repository,
            client=_ScriptedHermes(SUMMARY_OUTPUT, REVIEW_OUTPUT),
            weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
        )
        review_rows = list(self.repository._standalone_review_tasks.values())
        self.assertEqual(len(review_rows), 1)
        # Forge a lineage divergence that the generation check cannot shadow:
        # an unknown source_type has no newest-version to compare, so the
        # summary-row vs review-task mismatch is what must fire.
        self.repository._standalone_review_tasks[review_rows[0]["review_task_id"]].update(
            status="pending", owner_token=None, claimed_at=None, lease_expires_at=None,
            source_type="csd_issue_mistyped",
        )
        result = drain_standalone_knowledge_tasks(
            self.repository,
            client=_ScriptedHermes(SUMMARY_OUTPUT, REVIEW_OUTPUT),
            weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
        )
        self.assertEqual(result, {"executed": 0, "failed": 1})
        failed_row = self.repository.get_standalone_review_task(
            review_rows[0]["review_task_id"]
        )
        self.assertEqual(failed_row["status"], "failed")
        self.assertIn("diverged", failed_row["error"])

    def test_newer_source_version_fails_the_stale_generation(self) -> None:
        """Review round 3, R3-6: a NEWER accepted source version supersedes
        the queued generation — the stale summary must fail visibly and never
        run, while the queue mints the new generation."""
        task = queue_standalone_summary_for_source(
            self.repository, intake=dict(self.intake), now_value="2026-10-02T00:01:00+00:00"
        )
        newer = dict(CSD_INTAKE, source_updated_at="2026-10-02T08:00:00+00:00")
        self.repository.accept_knowledge_source(
            {
                "schema_version": "knowledge-source-v1",
                **{k: v for k, v in newer.items() if k not in ("intake_id", "references_payload")},
                "references": {},
            },
            now_value="2026-10-02T08:00:01+00:00",
        )
        result = drain_standalone_knowledge_tasks(
            self.repository,
            client=_ScriptedHermes(SUMMARY_OUTPUT, REVIEW_OUTPUT),
            weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
        )
        self.assertEqual(result, {"executed": 0, "failed": 1})
        stored = self.repository.get_standalone_summary_task(task["summary_task_id"])
        self.assertEqual(stored["status"], "failed")
        self.assertIn("advanced", stored["error"])
        # No review, no promotion from the stale generation.
        self.assertEqual(list(self.repository._standalone_review_tasks.values()), [])
        self.assertEqual(self.repository.list_weknora_promotions(), [])


class RedeliveryRetryTests(unittest.TestCase):
    """R25 (p2-184): redelivery-driven bounded retry for failed standalone
    Summary and Review tasks, with a FRESH Hermes run identity per attempt
    (attempt-suffixed session id + idempotency key)."""

    def setUp(self) -> None:
        self.patcher = _enable_real_mode({})
        self.addCleanup(self.patcher.stop)
        self.repository = InMemoryTicketRepository()
        self.repository.accept_knowledge_source(
            {
                "schema_version": "knowledge-source-v1",
                "source_type": CSD_INTAKE["source_type"],
                "source_id": CSD_INTAKE["source_id"],
                "source_updated_at": CSD_INTAKE["source_updated_at"],
                "payload": CSD_INTAKE["payload"],
                "references": {},
            },
            now_value="2026-10-02T00:00:01+00:00",
        )
        stored = next(
            row for row in self.repository._knowledge_source_intakes.values()
            if row["source_type"] == "csd_issue"
        )
        self.intake = dict(stored)
        self.now = "2026-10-06T00:00:00+00:00"

    def _queue(self):
        return queue_standalone_summary_for_source(
            self.repository, intake=dict(self.intake), now_value=self.now,
        )

    def _fail_running_summary(self, error="timeline must be a string or an array of strings"):
        claimed = self.repository.claim_standalone_summary_tasks(limit=1, now_value=self.now)
        assert claimed
        self.repository.fail_standalone_summary_task(
            claimed[0]["summary_task_id"], error=error,
            owner_token=claimed[0]["owner_token"],
        )
        return claimed[0]["summary_task_id"]

    def test_redelivery_revives_failed_summary_with_fresh_run_identity(self) -> None:
        task_id = self._queue()["summary_task_id"]
        self._fail_running_summary()

        revived = self._queue()
        self.assertEqual(revived["status"], "pending")
        self.assertEqual(revived["attempt_count"], 1)
        self.assertTrue(revived["summary_session_id"].endswith(":a1"))
        self.assertIn("requeued: source redelivered", str(revived["error"]))

        client = _ScriptedHermes(SUMMARY_OUTPUT, REVIEW_OUTPUT)
        result = drain_standalone_knowledge_tasks(
            self.repository, client=client,
            weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
        )
        self.assertEqual(result, {"executed": 2, "failed": 0})
        # B5: the retried run used an attempt-suffixed idempotency key, so the
        # gateway starts a fresh run instead of replaying the failed one.
        self.assertTrue(client.runs[0]["idempotency_key"].endswith(":run:a1"))
        summary = self.repository.get_standalone_summary_task(task_id)
        self.assertEqual(summary["status"], "completed")
        self.assertTrue(summary["summary_session_id"].endswith(":a1"))

    def test_first_attempt_keeps_historical_run_identity(self) -> None:
        self._queue()
        client = _ScriptedHermes(SUMMARY_OUTPUT, REVIEW_OUTPUT)
        drain_standalone_knowledge_tasks(
            self.repository, client=client,
            weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
        )
        self.assertTrue(client.runs[0]["idempotency_key"].endswith(":run"))
        self.assertFalse(client.runs[0]["idempotency_key"].endswith(":run:a0"))

    def test_redelivery_attempt_cap_keeps_failed(self) -> None:
        task_id = self._queue()["summary_task_id"]
        for _ in range(5):
            self._fail_running_summary()
            revived = self._queue()
        # 6th redelivery on a failed row at the cap: no revive.
        self._fail_running_summary()
        capped = self._queue()
        self.assertEqual(capped["status"], "failed")
        self.assertEqual(capped["attempt_count"], 5)

    def test_redelivery_is_noop_on_pending(self) -> None:
        first = self._queue()
        second = self._queue()
        self.assertEqual(first["summary_task_id"], second["summary_task_id"])
        self.assertEqual(second["status"], "pending")
        self.assertEqual(second.get("attempt_count"), 0)
        self.assertEqual(len(self.repository.list_standalone_summary_tasks()), 1)

    def test_redelivery_revives_failed_review_of_completed_summary(self) -> None:
        bad_review = {
            "decisions": [
                {
                    "candidate_id": "c1", "candidate_type": "knowledge",
                    "decision": "no_change", "confidence": 0.9,
                    "rationale": "duplicate without a target",
                    "proposed_content": "", "target_object": None, "target_version": None,
                }
            ]
        }
        self._queue()
        result = drain_standalone_knowledge_tasks(
            self.repository, client=_ScriptedHermes(SUMMARY_OUTPUT, bad_review),
            weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
        )
        self.assertEqual(result, {"executed": 1, "failed": 1})
        summary_id = self.repository.list_standalone_summary_tasks()[0]["summary_task_id"]
        review_id = f"{summary_id}:review"
        self.assertEqual(
            self.repository.get_standalone_summary_task(summary_id)["status"], "completed"
        )
        self.assertEqual(
            self.repository.get_standalone_review_task(review_id)["status"], "failed"
        )

        revived_summary = self._queue()
        self.assertEqual(revived_summary["status"], "completed")
        review = self.repository.get_standalone_review_task(review_id)
        self.assertEqual(review["status"], "pending")
        self.assertEqual(review["attempt_count"], 1)
        self.assertTrue(review["review_session_id"].endswith(":a1"))
        self.assertIn("requeued: source redelivered", str(review["error"]))


class _SearchOkKnowledge:
    """Knowledge surface that answers legally (empty search is legal)."""

    def configured(self):
        return True

    def search(self, query):
        return []


class _MemoryReviewOutput:
    """A review that proposes a MEMORY `new` write."""

    OUTPUT = {
        "decisions": [
            {
                "candidate_id": "c1",
                "candidate_type": "memory",
                "decision": "new",
                "confidence": 0.9,
                "rationale": "no similar memory exists",
                "proposed_content": "Region EU join failures were fixed upstream.",
                "kind": "fact",
                "target_object": None,
                "target_version": None,
            }
        ],
    }


class MalformedMemoryEvidenceDrainTests(unittest.TestCase):
    """R20/P1 (review round 20): a malformed memory response reaching the
    REAL standalone Summary → Review drain must surface as unavailable
    evidence — the candidate parks at human_review — never as a silent
    empty listing that lets `memory / new / queued` skip the dedup check.

    Only Hermes and the external HTTP boundary are scripted; the memory
    client is the real contract-pinned WeKnoraClient.
    """

    MALFORMED_FIRST_PAGES = [
        {"success": False, "error": "backend unavailable"},
        {},
        {"data": None},
        {"data": [None]},
    ]

    def setUp(self) -> None:
        self.patcher = _enable_real_mode({})
        self.addCleanup(self.patcher.stop)

    def _drain_with_memory_pages(self, pages, agent_memory_client="__default__"):
        import io
        import urllib.request

        from backend.services.weknora_client import WeKnoraClient

        repository = InMemoryTicketRepository()
        repository.accept_knowledge_source(
            {
                "schema_version": "knowledge-source-v1",
                "source_type": CSD_INTAKE["source_type"],
                "source_id": CSD_INTAKE["source_id"],
                "source_updated_at": CSD_INTAKE["source_updated_at"],
                "payload": CSD_INTAKE["payload"],
                "references": {},
            },
            now_value="2026-10-02T00:00:01+00:00",
        )
        stored = next(
            row for row in repository._knowledge_source_intakes.values()
            if row["source_type"] == "csd_issue"
        )
        queue_standalone_summary_for_source(
            repository, intake=dict(stored), now_value="2026-10-02T00:01:00+00:00"
        )

        memory_client = WeKnoraClient(
            base_url="http://weknora.test",
            api_token="synthetic-token",
            contract={
                "health": {"method": "GET", "path": "/health"},
                "memory_list": {
                    "method": "GET",
                    "path": "/api/v1/memory/items",
                    "query_params": {"identity": {"$": "identity"}},
                },
            },
            memory_identity="hermes-shared",
        )
        remaining = list(pages)

        class _Resp(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=None):
            payload = remaining.pop(0) if remaining else {"data": []}
            return _Resp(json.dumps(payload).encode("utf-8"))

        patcher = unittest.mock.patch.object(
            urllib.request, "urlopen", side_effect=fake_urlopen
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        hermes = _ScriptedHermes(SUMMARY_OUTPUT, _MemoryReviewOutput.OUTPUT)
        if agent_memory_client == "__default__":
            agent_memory_client = _AgentMemoryOk()
        result = drain_standalone_knowledge_tasks(
            repository,
            client=hermes,
            weknora_client=_SearchOkKnowledge(),
            memory_client=memory_client,
            agent_memory_client=agent_memory_client,
            limit=5,
        )
        # The review INPUT bundle (run 2) carries the memory_available flag
        # the Hermes Review actually saw.
        review_bundle = json.loads(hermes.runs[1]["input_text"])
        return result, repository, review_bundle

    def test_malformed_first_page_parks_at_human_review(self) -> None:
        for malformed in self.MALFORMED_FIRST_PAGES:
            with self.subTest(malformed=malformed):
                result, repository, review_bundle = self._drain_with_memory_pages([malformed])
                # The drain itself succeeds: the failure is EVIDENCE
                # unavailability, not a task error.
                self.assertEqual(result, {"executed": 2, "failed": 0})
                self.assertFalse(review_bundle["weknora"]["memory_available"])
                promotions = repository.list_weknora_promotions()
                self.assertEqual(len(promotions), 1)
                self.assertEqual(promotions[0]["candidate_type"], "memory")
                # The auto-write decision is gone: human_review, never
                # memory/new/queued.
                self.assertEqual(promotions[0]["decision"], "human_review")
                self.assertIn(
                    "[downgraded from new:", promotions[0]["candidate_payload"]["note"]
                )

    def test_malformed_mid_pagination_page_parks_at_human_review(self) -> None:
        pages = [
            {"data": [{"id": f"m{i}"} for i in range(200)]},
            {"success": False, "error": "backend unavailable"},
        ]
        result, repository, review_bundle = self._drain_with_memory_pages(pages)
        self.assertEqual(result, {"executed": 2, "failed": 0})
        self.assertFalse(review_bundle["weknora"]["memory_available"])
        promotions = repository.list_weknora_promotions()
        self.assertEqual(len(promotions), 1)
        self.assertEqual(promotions[0]["decision"], "human_review")

    def test_legal_empty_memory_still_allows_new(self) -> None:
        result, repository, review_bundle = self._drain_with_memory_pages([{"data": []}])
        self.assertEqual(result, {"executed": 2, "failed": 0})
        self.assertTrue(review_bundle["weknora"]["memory_available"])
        # Stage 3 (p2-194): the bundle carries BOTH retrieval sides; with the
        # AgentMemory surface healthy (empty hits), the writable decision
        # survives.
        self.assertTrue(review_bundle["agent_memory"]["available"])
        self.assertEqual(review_bundle["agent_memory"]["results"]["c1"], [])
        promotions = repository.list_weknora_promotions()
        self.assertEqual(len(promotions), 1)
        self.assertEqual(promotions[0]["candidate_type"], "memory")
        self.assertEqual(promotions[0]["decision"], "new")
        self.assertEqual(promotions[0]["status"], "queued")

    def test_agent_memory_unavailable_downgrades_writable_to_human_review(self) -> None:
        """Stage 3: one silent retrieval side is enough to refuse a write."""
        result, repository, review_bundle = self._drain_with_memory_pages(
            [{"data": []}], agent_memory_client=None
        )
        self.assertEqual(result, {"executed": 2, "failed": 0})
        self.assertFalse(review_bundle["agent_memory"]["available"])
        promotions = repository.list_weknora_promotions()
        self.assertEqual(len(promotions), 1)
        self.assertEqual(promotions[0]["decision"], "human_review")
        self.assertIn(
            "AgentMemory evidence unavailable",
            promotions[0]["candidate_payload"]["note"],
        )


if __name__ == "__main__":
    unittest.main()
