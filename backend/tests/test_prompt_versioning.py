from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.scripts.prompt_release import run as run_prompt_release
from backend.services.agent_config import build_managed_prompt_catalog
from backend.services.prompt_runtime import (
    initialize_prompt_runtime,
    prompt_runtime_info,
    reset_prompt_runtime_for_tests,
    resolve_system_prompt,
)


class PromptVersioningRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.catalog = build_managed_prompt_catalog()
        self.repository.sync_prompt_catalog(
            self.catalog,
            actor_id="system",
            created_at="2026-07-23T00:00:00+00:00",
        )

    def test_catalog_seed_creates_one_complete_active_release(self) -> None:
        prompts = self.repository.list_managed_prompts()
        release = self.repository.get_active_prompt_release()

        self.assertEqual(len(prompts), len(self.catalog))
        self.assertIsNotNone(release)
        self.assertEqual(set(release["items"]), {item["prompt_key"] for item in self.catalog})
        self.assertTrue(all(item["active_version"]["version"] == 1 for item in prompts))

    def test_draft_schedule_prepare_and_activate_are_atomic(self) -> None:
        prompt = self.repository.get_managed_prompt("route-system")
        draft = self.repository.create_prompt_draft(
            "route-system",
            content="Updated route prompt",
            change_note="Improve routing",
            based_on_version=prompt["active_version"]["version"],
            actor_id="admin-1",
            created_at="2026-07-23T01:00:00+00:00",
        )
        self.repository.schedule_prompt_version(
            "route-system",
            draft["version"],
            actor_id="admin-1",
            scheduled_at="2026-07-23T01:05:00+00:00",
        )

        previous = self.repository.get_active_prompt_release()
        candidate = self.repository.prepare_prompt_release(
            build_ref="abc123",
            created_at="2026-07-23T02:00:00+00:00",
        )

        self.assertEqual(self.repository.get_active_prompt_release()["release_id"], previous["release_id"])
        self.assertEqual(candidate["items"]["route-system"], draft["version"])
        self.assertEqual(self.repository.get_managed_prompt("route-system")["active_version"]["version"], 1)

        active = self.repository.activate_prompt_release(
            candidate["release_id"],
            activated_at="2026-07-23T02:10:00+00:00",
        )

        self.assertEqual(active["status"], "active")
        self.assertEqual(self.repository.get_managed_prompt("route-system")["active_version"]["version"], draft["version"])
        self.assertEqual(self.repository.get_prompt_release(previous["release_id"])["status"], "superseded")

    def test_prepare_without_scheduled_versions_reuses_active_release(self) -> None:
        active = self.repository.get_active_prompt_release()
        prepared = self.repository.prepare_prompt_release(
            build_ref="same-code",
            created_at="2026-07-23T03:00:00+00:00",
        )

        self.assertFalse(prepared["created"])
        self.assertEqual(prepared["release_id"], active["release_id"])

    def test_restore_creates_new_draft_without_changing_active(self) -> None:
        restored = self.repository.restore_prompt_version(
            "route-system",
            1,
            actor_id="admin-1",
            created_at="2026-07-23T04:00:00+00:00",
        )

        self.assertEqual(restored["status"], "draft")
        self.assertEqual(restored["based_on_version"], 1)
        self.assertEqual(self.repository.get_managed_prompt("route-system")["active_version"]["version"], 1)

    def test_stale_active_version_rejects_draft(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "active prompt version changed"):
            self.repository.create_prompt_draft(
                "route-system",
                content="Stale draft",
                change_note="Stale",
                based_on_version=999,
                actor_id="admin-1",
                created_at="2026-07-23T05:00:00+00:00",
            )


class PromptRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_prompt_runtime_for_tests()
        self.repository = InMemoryTicketRepository()
        self.repository.sync_prompt_catalog(
            build_managed_prompt_catalog(), actor_id="system", created_at="2026-07-23T00:00:00+00:00"
        )

    def tearDown(self) -> None:
        reset_prompt_runtime_for_tests()

    def test_candidate_release_is_loaded_as_one_immutable_snapshot(self) -> None:
        prompt = self.repository.get_managed_prompt("route-system")
        draft = self.repository.create_prompt_draft(
            "route-system", content="candidate route", change_note="test",
            based_on_version=prompt["active_version"]["version"], actor_id="admin",
            created_at="2026-07-23T01:00:00+00:00",
        )
        self.repository.schedule_prompt_version(
            "route-system", draft["version"], actor_id="admin", scheduled_at="2026-07-23T01:01:00+00:00"
        )
        release = self.repository.prepare_prompt_release(build_ref="abc", created_at="2026-07-23T02:00:00+00:00")

        with patch.dict("os.environ", {"PROMPT_RELEASE_ID": release["release_id"], "PROMPT_RELEASE_REQUIRED": "true"}, clear=False):
            snapshot = initialize_prompt_runtime(self.repository, service_name="api")

        self.assertEqual(snapshot.release_id, release["release_id"])
        self.assertEqual(resolve_system_prompt("route-system", "fallback"), "candidate route")
        self.assertEqual(prompt_runtime_info()["source"], "release")

    def test_strict_mode_rejects_missing_release_id(self) -> None:
        with patch.dict("os.environ", {"PROMPT_RELEASE_ID": "", "PROMPT_RELEASE_REQUIRED": "true"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "PROMPT_RELEASE_ID is required"):
                initialize_prompt_runtime(self.repository, service_name="api")

    def test_release_hash_mismatch_is_rejected(self) -> None:
        release = self.repository.get_active_prompt_release()
        route = next(item for item in self.repository._prompt_versions["route-system"] if item["status"] == "active")
        route["content"] = "tampered"
        with patch.dict("os.environ", {"PROMPT_RELEASE_ID": release["release_id"]}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                initialize_prompt_runtime(self.repository, service_name="worker_query")


class PromptReleaseCliTests(unittest.TestCase):
    def test_prepare_shell_contract_reuses_active_release_without_scheduled_versions(self) -> None:
        repository = InMemoryTicketRepository()
        payload = run_prompt_release(["prepare", "--build-ref", "abc", "--output", "shell"], repository=repository)

        self.assertFalse(payload["release"]["created"])
        self.assertEqual(payload["release"]["status"], "active")


if __name__ == "__main__":
    unittest.main()
