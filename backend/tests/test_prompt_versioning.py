from __future__ import annotations

import hashlib
import os
import unittest
from unittest.mock import patch

from backend.scripts import prompt_release as prompt_release_script
from backend.repositories.ticket_repository import (
    InMemoryTicketRepository,
    PostgresTicketRepository,
)
from backend.scripts.prompt_release import run as run_prompt_release
from backend.services.agent_config import build_managed_prompt_catalog
from backend.services.prompt_runtime import (
    initialize_prompt_runtime,
    load_prompt_release_snapshot,
    prompt_runtime_info,
    reset_prompt_runtime_for_tests,
    resolve_system_prompt,
)
from backend.services.prompt_versioning import PromptVersionService


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

    def _candidate_with_fraud_prompt(self, content: str) -> dict[str, object]:
        prompt_key = "account-verification-field-extractor-system"
        active = self.repository.get_managed_prompt(prompt_key)["active_version"]
        draft = self.repository.create_prompt_draft(
            prompt_key,
            content=content,
            change_note="Test deployment contract",
            based_on_version=active["version"],
            actor_id="test",
            created_at="2026-08-25T00:00:00+00:00",
        )
        self.repository.schedule_prompt_version(
            prompt_key,
            draft["version"],
            actor_id="test",
            scheduled_at="2026-08-25T00:01:00+00:00",
        )
        return self.repository.prepare_prompt_release(
            build_ref="fraud-contract-test",
            created_at="2026-08-25T00:02:00+00:00",
        )

    def test_validate_release_accepts_current_fraud_v4_contract(self) -> None:
        release = self.repository.get_active_prompt_release()

        result = PromptVersionService(self.repository).validate_release(release["release_id"])

        self.assertEqual(result["release_id"], release["release_id"])

    def test_validate_release_rejects_structurally_invalid_fraud_contract(self) -> None:
        current = next(
            item["content"]
            for item in self.catalog
            if item["prompt_key"] == "account-verification-field-extractor-system"
        )
        candidate = self._candidate_with_fraud_prompt(current.replace('"name": {', '"name_broken": {', 1))

        with self.assertRaisesRegex(RuntimeError, "Fraud Account Prompt fields mismatch"):
            PromptVersionService(self.repository).validate_release(candidate["release_id"])

    def test_validate_release_rejects_legacy_fraud_fields(self) -> None:
        current = next(
            item["content"]
            for item in self.catalog
            if item["prompt_key"] == "account-verification-field-extractor-system"
        )
        candidate = self._candidate_with_fraud_prompt(
            current.replace('"account_type": {', '"company_information": {', 1)
        )

        with self.assertRaisesRegex(RuntimeError, "Fraud Account Prompt contains legacy fields"):
            PromptVersionService(self.repository).validate_release(candidate["release_id"])

    def test_validate_release_rejects_fraud_content_that_differs_from_code(self) -> None:
        current = next(
            item["content"]
            for item in self.catalog
            if item["prompt_key"] == "account-verification-field-extractor-system"
        )
        candidate = self._candidate_with_fraud_prompt(current + "\nDeployment-only drift.")

        with self.assertRaisesRegex(RuntimeError, "does not match current code"):
            PromptVersionService(self.repository).validate_release(candidate["release_id"])

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

    def test_catalog_retirement_creates_exact_candidate_without_scheduled_versions(self) -> None:
        retired_key = self.catalog[-1]["prompt_key"]
        current_catalog = self.catalog[:-1]
        previous = self.repository.get_active_prompt_release()

        sync_result = self.repository.sync_prompt_catalog(
            current_catalog,
            actor_id="system",
            created_at="2026-07-23T03:10:00+00:00",
        )
        candidate = self.repository.prepare_prompt_release(
            build_ref="retire-one",
            created_at="2026-07-23T03:11:00+00:00",
        )

        self.assertEqual(sync_result["retired_prompt_keys"], [retired_key])
        self.assertIsNone(self.repository.get_managed_prompt(retired_key))
        self.assertTrue(candidate["created"])
        self.assertEqual(set(candidate["items"]), {item["prompt_key"] for item in current_catalog})
        self.assertNotIn(retired_key, candidate["items"])
        self.assertEqual(self.repository.get_active_prompt_release()["release_id"], previous["release_id"])
        self.assertEqual(self.repository.get_prompt_release(previous["release_id"])["items"][retired_key], 1)

        with patch("backend.services.agent_config.build_managed_prompt_catalog", return_value=current_catalog):
            snapshot = load_prompt_release_snapshot(self.repository, candidate["release_id"])
        self.assertEqual(set(snapshot.prompts), set(candidate["items"]))

    def test_failed_retirement_preserves_previous_release_and_reintroduction_is_deployable(self) -> None:
        retired = self.catalog[-1]
        current_catalog = self.catalog[:-1]
        previous = self.repository.get_active_prompt_release()
        self.repository.sync_prompt_catalog(
            current_catalog,
            actor_id="system",
            created_at="2026-07-23T03:20:00+00:00",
        )
        candidate = self.repository.prepare_prompt_release(
            build_ref="failed-retirement",
            created_at="2026-07-23T03:21:00+00:00",
        )
        self.repository.fail_prompt_release(candidate["release_id"], failure_reason="test failure")

        self.assertEqual(self.repository.get_active_prompt_release()["release_id"], previous["release_id"])
        retired_versions = self.repository._prompt_versions[retired["prompt_key"]]
        self.assertEqual(next(item for item in retired_versions if item["status"] == "active")["version"], 1)

        sync_result = self.repository.sync_prompt_catalog(
            self.catalog,
            actor_id="system",
            created_at="2026-07-23T03:22:00+00:00",
        )
        prepared = self.repository.prepare_prompt_release(
            build_ref="reintroduced-before-activation",
            created_at="2026-07-23T03:23:00+00:00",
        )
        self.assertEqual(sync_result["reactivated_prompt_keys"], [retired["prompt_key"]])
        self.assertFalse(prepared["created"])
        self.assertEqual(prepared["release_id"], previous["release_id"])

    def test_reintroduction_after_retirement_activation_schedules_historical_content(self) -> None:
        retired = self.catalog[-1]
        current_catalog = self.catalog[:-1]
        self.repository.sync_prompt_catalog(
            current_catalog,
            actor_id="system",
            created_at="2026-07-23T03:30:00+00:00",
        )
        candidate = self.repository.prepare_prompt_release(
            build_ref="retirement",
            created_at="2026-07-23T03:31:00+00:00",
        )
        self.repository.activate_prompt_release(
            candidate["release_id"],
            activated_at="2026-07-23T03:32:00+00:00",
        )
        self.assertFalse(any(item["status"] == "active" for item in self.repository._prompt_versions[retired["prompt_key"]]))

        self.repository.sync_prompt_catalog(
            self.catalog,
            actor_id="system",
            created_at="2026-07-23T03:33:00+00:00",
        )
        restored = self.repository.get_managed_prompt(retired["prompt_key"])
        self.assertIsNotNone(restored["scheduled_version"])
        self.assertEqual(restored["scheduled_version"]["content"], retired["content"])
        next_candidate = self.repository.prepare_prompt_release(
            build_ref="reintroduced",
            created_at="2026-07-23T03:34:00+00:00",
        )
        self.assertEqual(set(next_candidate["items"]), {item["prompt_key"] for item in self.catalog})

    def test_retirement_unschedules_stale_edit_before_reintroduction(self) -> None:
        retired = self.catalog[-1]
        key = retired["prompt_key"]
        draft = self.repository.create_prompt_draft(
            key,
            content="stale scheduled content",
            change_note="must not survive retirement as scheduled",
            based_on_version=1,
            actor_id="admin",
            created_at="2026-07-23T03:35:00+00:00",
        )
        self.repository.schedule_prompt_version(
            key,
            draft["version"],
            actor_id="admin",
            scheduled_at="2026-07-23T03:36:00+00:00",
        )
        self.repository.sync_prompt_catalog(
            self.catalog[:-1],
            actor_id="system",
            created_at="2026-07-23T03:37:00+00:00",
        )
        candidate = self.repository.prepare_prompt_release(
            build_ref="retirement-with-stale-edit",
            created_at="2026-07-23T03:38:00+00:00",
        )
        self.repository.activate_prompt_release(
            candidate["release_id"],
            activated_at="2026-07-23T03:39:00+00:00",
        )

        self.repository.sync_prompt_catalog(
            self.catalog,
            actor_id="system",
            created_at="2026-07-23T03:40:00+00:00",
        )

        restored = self.repository.get_managed_prompt(key)
        stale = next(item for item in restored["versions"] if item["version"] == draft["version"])
        self.assertEqual(stale["status"], "draft")
        self.assertEqual(restored["scheduled_version"]["content"], retired["content"])

    def test_retired_prompt_rejects_stale_draft_and_schedule_operations(self) -> None:
        retired = self.catalog[-1]
        key = retired["prompt_key"]
        draft = self.repository.create_prompt_draft(
            key,
            content="stale draft content",
            change_note="created before retirement",
            based_on_version=1,
            actor_id="admin",
            created_at="2026-07-23T03:41:00+00:00",
        )
        self.repository.sync_prompt_catalog(
            self.catalog[:-1],
            actor_id="system",
            created_at="2026-07-23T03:42:00+00:00",
        )

        with self.assertRaisesRegex(ValueError, "prompt not found"):
            self.repository.create_prompt_draft(
                key,
                content="new retired draft",
                change_note="must fail",
                based_on_version=1,
                actor_id="admin",
                created_at="2026-07-23T03:43:00+00:00",
            )
        with self.assertRaisesRegex(ValueError, "prompt not found"):
            self.repository.schedule_prompt_version(
                key,
                draft["version"],
                actor_id="admin",
                scheduled_at="2026-07-23T03:44:00+00:00",
            )

    def test_duplicate_catalog_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate prompt catalog key"):
            self.repository.sync_prompt_catalog(
                [self.catalog[0], self.catalog[0]],
                actor_id="system",
                created_at="2026-07-23T03:40:00+00:00",
            )

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

        with self.assertLogs("backend.services.prompt_runtime", level="WARNING") as captured:
            with patch.dict("os.environ", {"PROMPT_RELEASE_ID": release["release_id"], "PROMPT_RELEASE_REQUIRED": "true"}, clear=False):
                snapshot = initialize_prompt_runtime(self.repository, service_name="api")

        self.assertEqual(snapshot.release_id, release["release_id"])
        self.assertEqual(resolve_system_prompt("route-system", "fallback"), "candidate route")
        self.assertEqual(prompt_runtime_info()["source"], "release")
        self.assertTrue(any(f"prompt_runtime_loaded service=api release_id={release['release_id']}" in line for line in captured.output))

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

    def test_current_returns_active_release_for_shell_reconciliation(self) -> None:
        repository = InMemoryTicketRepository()
        payload = run_prompt_release(["current", "--output", "shell"], repository=repository)

        self.assertEqual(payload["release"]["status"], "active")

    def test_validate_checks_release_without_initializing_global_runtime(self) -> None:
        repository = InMemoryTicketRepository()
        current = run_prompt_release(["current", "--output", "shell"], repository=repository)
        payload = run_prompt_release(
            ["validate", "--release-id", current["release"]["release_id"]],
            repository=repository,
        )

        self.assertEqual(payload["validation"]["source"], "release")
        self.assertEqual(payload["validation"]["status"], "loaded")


class PromptReleaseSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = InMemoryTicketRepository()
        self.source.sync_prompt_catalog(
            build_managed_prompt_catalog(),
            actor_id="system",
            created_at="2026-07-23T00:00:00+00:00",
        )
        self.target = InMemoryTicketRepository()

    def _sync_via_cli(self, release_id: str) -> dict:
        return run_prompt_release(
            ["sync", "--release-id", release_id, "--target-dsn", "postgresql://sync:test@db.local/target"],
            repository=self.source,
            target_repository=self.target,
        )

    def test_sync_active_release_into_fresh_target(self) -> None:
        active = self.source.get_active_prompt_release()

        payload = self._sync_via_cli(active["release_id"])

        self.assertTrue(payload["sync"]["created"])
        self.assertEqual(payload["validation"]["status"], "loaded")
        self.assertEqual(self.target.get_active_prompt_release()["release_id"], active["release_id"])
        self.assertEqual(
            load_prompt_release_snapshot(self.target, active["release_id"]).prompts,
            load_prompt_release_snapshot(self.source, active["release_id"]).prompts,
        )

    def test_sync_does_not_run_schema_initialization_on_source_or_target(self) -> None:
        active = self.source.get_active_prompt_release()

        with patch.object(self.source, "initialize", wraps=self.source.initialize) as source_initialize, patch.object(
            self.target,
            "initialize",
            wraps=self.target.initialize,
        ) as target_initialize:
            self._sync_via_cli(active["release_id"])

        source_initialize.assert_not_called()
        target_initialize.assert_not_called()

    def test_sync_is_idempotent(self) -> None:
        active = self.source.get_active_prompt_release()
        first = self._sync_via_cli(active["release_id"])
        second = self._sync_via_cli(active["release_id"])

        self.assertTrue(first["sync"]["created"])
        self.assertFalse(second["sync"]["created"])
        self.assertEqual(second["sync"]["versions_remapped"], 0)
        self.assertEqual(second["sync"]["versions_matched"], len(active["items"]))
        self.assertTrue(second["identity"]["content_fingerprint"].startswith("sha256:"))
        self.assertEqual(self.target.get_active_prompt_release()["release_id"], active["release_id"])

    def test_sync_same_id_rejects_build_ref_mismatch_before_status_change(self) -> None:
        active = self.source.get_active_prompt_release()
        self._sync_via_cli(active["release_id"])
        self.target._prompt_releases[active["release_id"]]["build_ref"] = "different-build"

        with self.assertRaisesRegex(ValueError, "build_ref mismatch"):
            self._sync_via_cli(active["release_id"])

        self.assertEqual(
            self.target.get_prompt_release(active["release_id"])["status"],
            "active",
        )

    def test_sync_same_id_rejects_content_mismatch_before_status_change(self) -> None:
        active = self.source.get_active_prompt_release()
        self._sync_via_cli(active["release_id"])
        key = sorted(active["items"])[0]
        target_version = self.target._prompt_releases[active["release_id"]]["items"][key]
        target_row = next(
            row
            for row in self.target._prompt_versions[key]
            if row["version"] == target_version
        )
        target_row["content"] = "different target content"
        target_row["content_sha256"] = "f" * 64

        with self.assertRaisesRegex(ValueError, "content fingerprint mismatch"):
            self._sync_via_cli(active["release_id"])

        self.assertEqual(
            self.target.get_prompt_release(active["release_id"])["status"],
            "active",
        )

    def test_sync_active_release_can_defer_target_activation(self) -> None:
        active = self.source.get_active_prompt_release()
        PromptVersionService(self.target).sync_catalog()
        previous_target = self.target.get_active_prompt_release()

        deferred = run_prompt_release(
            [
                "sync",
                "--release-id",
                active["release_id"],
                "--target-dsn",
                "unused-in-test",
                "--defer-activation",
            ],
            repository=self.source,
            target_repository=self.target,
        )

        self.assertEqual(deferred["sync"]["status"], "candidate")
        self.assertEqual(
            self.target.get_active_prompt_release()["release_id"],
            previous_target["release_id"],
        )
        activated = self._sync_via_cli(active["release_id"])
        self.assertEqual(activated["sync"]["status"], "active")
        self.assertEqual(
            self.target.get_active_prompt_release()["release_id"],
            active["release_id"],
        )

    def test_sync_target_repository_uses_secret_safe_environment_inputs(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PROMPT_RELEASE_TARGET_DSN": "postgresql://target.example/db",
                "PROMPT_RELEASE_TARGET_SCHEMA": "automation_production",
                "TICKET_DB_SCHEMA": "source_schema",
            },
            clear=False,
        ), patch.object(prompt_release_script, "PostgresTicketRepository") as repository_class:
            prompt_release_script._create_sync_target_repository(None, None)

        repository_class.assert_called_once_with(
            dsn="postgresql://target.example/db",
            schema="automation_production",
            migration_dsn="postgresql://target.example/db",
            application_name="supportportal-prompt-release-sync",
        )

    def test_sync_reuses_existing_content_at_different_target_version(self) -> None:
        collision_key = "account-account-billing-router-system"
        source_content = self.source.get_managed_prompt(collision_key)["active_version"]["content"]
        divergent_catalog = [
            {**item, "content": "Target-local historical billing router prompt"}
            if item["prompt_key"] == collision_key
            else item
            for item in build_managed_prompt_catalog()
        ]
        self.target.sync_prompt_catalog(
            divergent_catalog,
            actor_id="target-system",
            created_at="2026-07-23T00:00:00+00:00",
        )
        existing = self.target.create_prompt_draft(
            collision_key,
            content=source_content,
            change_note="target already stores the source content",
            based_on_version=1,
            actor_id="target-admin",
            created_at="2026-07-23T00:30:00+00:00",
        )

        active = self.source.get_active_prompt_release()
        payload = self._sync_via_cli(active["release_id"])
        target_release = self.target.get_prompt_release(active["release_id"])
        target_prompt = self.target.get_managed_prompt(collision_key)

        self.assertEqual(payload["sync"]["versions_created"], 0)
        self.assertGreater(payload["sync"]["versions_remapped"], 0)
        self.assertEqual(target_release["items"][collision_key], existing["version"])
        self.assertEqual(len(target_prompt["versions"]), 2)
        self.assertEqual(target_prompt["active_version"]["content"], source_content)

    def test_sync_candidate_release_then_activation_harmonizes_target_status(self) -> None:
        prompt = self.source.get_managed_prompt("route-system")
        draft = self.source.create_prompt_draft(
            "route-system",
            content="Updated route prompt for sync",
            change_note="sync test",
            based_on_version=prompt["active_version"]["version"],
            actor_id="admin-1",
            created_at="2026-07-23T01:00:00+00:00",
        )
        self.source.schedule_prompt_version(
            "route-system",
            draft["version"],
            actor_id="admin-1",
            scheduled_at="2026-07-23T01:05:00+00:00",
        )
        candidate = self.source.prepare_prompt_release(
            build_ref="sync-test",
            created_at="2026-07-23T02:00:00+00:00",
        )
        self.assertTrue(candidate["created"])

        payload = self._sync_via_cli(candidate["release_id"])

        self.assertEqual(payload["sync"]["status"], "candidate")
        self.assertEqual(payload["validation"]["status"], "loaded")
        self.assertNotEqual(self.target.get_active_prompt_release()["release_id"], candidate["release_id"])
        self.assertEqual(
            load_prompt_release_snapshot(self.target, candidate["release_id"]).prompts["route-system"],
            "Updated route prompt for sync",
        )

        self.source.activate_prompt_release(candidate["release_id"], activated_at="2026-07-23T02:10:00+00:00")
        payload = self._sync_via_cli(candidate["release_id"])

        self.assertEqual(payload["sync"]["status"], "active")
        self.assertEqual(self.target.get_active_prompt_release()["release_id"], candidate["release_id"])
        self.assertEqual(self.target.get_managed_prompt("route-system")["active_version"]["version"], draft["version"])

    def test_sync_remaps_conflicting_target_version_by_content_hash(self) -> None:
        collision_key = "account-account-billing-router-system"
        divergent_catalog = [
            {**item, "content": "Target-local historical billing router prompt"}
            if item["prompt_key"] == collision_key
            else item
            for item in build_managed_prompt_catalog()
        ]
        self.target.sync_prompt_catalog(
            divergent_catalog,
            actor_id="target-system",
            created_at="2026-07-23T00:00:00+00:00",
        )
        previous_target_release = self.target.get_active_prompt_release()
        previous_target_content = self.target.get_managed_prompt(collision_key)["active_version"]["content"]

        route = self.source.get_managed_prompt("route-system")
        draft = self.source.create_prompt_draft(
            "route-system",
            content="Candidate route prompt with a cross-database collision",
            change_note="exercise target-local version mapping",
            based_on_version=route["active_version"]["version"],
            actor_id="admin-1",
            created_at="2026-07-23T02:00:00+00:00",
        )
        self.source.schedule_prompt_version(
            "route-system",
            draft["version"],
            actor_id="admin-1",
            scheduled_at="2026-07-23T02:01:00+00:00",
        )
        candidate = self.source.prepare_prompt_release(
            build_ref="collision-sync",
            created_at="2026-07-23T02:02:00+00:00",
        )

        payload = self._sync_via_cli(candidate["release_id"])
        target_candidate = self.target.get_prompt_release(candidate["release_id"])

        self.assertGreater(payload["sync"]["versions_remapped"], 0)
        self.assertNotEqual(target_candidate["items"][collision_key], candidate["items"][collision_key])
        self.assertEqual(
            load_prompt_release_snapshot(self.target, candidate["release_id"]).prompts,
            load_prompt_release_snapshot(self.source, candidate["release_id"]).prompts,
        )
        self.assertEqual(self.target.get_active_prompt_release()["release_id"], previous_target_release["release_id"])
        self.assertEqual(
            self.target.get_managed_prompt(collision_key)["active_version"]["content"],
            previous_target_content,
        )
        remapped = next(
            item
            for item in self.target.get_managed_prompt(collision_key)["versions"]
            if item["version"] == target_candidate["items"][collision_key]
        )
        self.assertEqual(remapped["status"], "draft")

        self.source.activate_prompt_release(candidate["release_id"], activated_at="2026-07-23T02:10:00+00:00")
        self._sync_via_cli(candidate["release_id"])

        self.assertEqual(self.target.get_active_prompt_release()["release_id"], candidate["release_id"])
        self.assertEqual(
            self.target.get_managed_prompt(collision_key)["active_version"]["content"],
            self.source.get_managed_prompt(collision_key)["active_version"]["content"],
        )

    def test_sync_rejects_content_hash_mismatch(self) -> None:
        active = self.source.get_active_prompt_release()
        self._sync_via_cli(active["release_id"])
        key = sorted(active["items"])[0]
        forged = {
            **active,
            "release_id": "pr-forged000000",
            "items": {key: active["items"][key]},
        }
        versions = [
            {
                "prompt_key": key,
                "version": active["items"][key],
                "content": "tampered",
                "content_sha256": "0" * 64,
                "status": "active",
            }
        ]

        with self.assertRaisesRegex(ValueError, "content hash mismatch"):
            self.target.sync_prompt_release(forged, versions)


class _Context:
    def __init__(self, value=None) -> None:
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False


class _PromptSyncCursor:
    def __init__(self, *, existing_release, target_rows) -> None:
        self.existing_release = existing_release
        self.target_rows = target_rows
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False

    def execute(self, statement, params=None) -> None:
        self.statements.append(str(statement))

    def fetchone(self):
        return self.existing_release

    def fetchall(self):
        return list(self.target_rows)


class _PromptSyncConnection:
    def __init__(self, cursor: _PromptSyncCursor) -> None:
        self._cursor = cursor

    def transaction(self):
        return _Context()

    def cursor(self):
        return _Context(self._cursor)


class _PromptSyncPostgresRepository(PostgresTicketRepository):
    def __init__(self, cursor: _PromptSyncCursor) -> None:
        self._schema = "test_prompt_sync"
        self._fake_connection = _PromptSyncConnection(cursor)

    def _run_with_connection_retry(self, operation_name, action):
        return action(self._fake_connection)


class PostgresPromptReleaseSyncContractTests(unittest.TestCase):
    @staticmethod
    def _payloads() -> tuple[dict, list[dict], str]:
        content = "prompt body"
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        release = {
            "release_id": "prompt-release-1",
            "build_ref": "build-1",
            "status": "active",
            "items": {"prompt-key": 1},
        }
        versions = [
            {
                "prompt_key": "prompt-key",
                "version": 1,
                "content": content,
                "content_sha256": content_hash,
            }
        ]
        return release, versions, content_hash

    @staticmethod
    def _assert_no_write(statements: list[str]) -> None:
        joined = "\n".join(statements).upper()
        assert "SQL('UPDATE " not in joined
        assert "SQL('INSERT " not in joined

    def test_same_id_build_ref_mismatch_stops_before_any_write(self) -> None:
        release, versions, content_hash = self._payloads()
        cursor = _PromptSyncCursor(
            existing_release=("different-build", "candidate"),
            target_rows=[("prompt-key", 7, content_hash)],
        )
        repository = _PromptSyncPostgresRepository(cursor)

        with self.assertRaisesRegex(ValueError, "build_ref mismatch"):
            repository.sync_prompt_release(release, versions)

        self._assert_no_write(cursor.statements)

    def test_same_id_content_mismatch_stops_before_any_write(self) -> None:
        release, versions, _ = self._payloads()
        cursor = _PromptSyncCursor(
            existing_release=("build-1", "candidate"),
            target_rows=[("prompt-key", 7, "f" * 64)],
        )
        repository = _PromptSyncPostgresRepository(cursor)

        with self.assertRaisesRegex(ValueError, "content fingerprint mismatch"):
            repository.sync_prompt_release(release, versions)

        self._assert_no_write(cursor.statements)

    def test_same_id_matching_content_allows_target_local_version_remap(self) -> None:
        release, versions, content_hash = self._payloads()
        cursor = _PromptSyncCursor(
            existing_release=("build-1", "active"),
            target_rows=[("prompt-key", 7, content_hash)],
        )
        repository = _PromptSyncPostgresRepository(cursor)

        result = repository.sync_prompt_release(release, versions)

        self.assertEqual(result["status"], "active")
        self.assertEqual(result["versions_matched"], 1)
        self.assertEqual(result["versions_remapped"], 1)
        self._assert_no_write(cursor.statements)


if __name__ == "__main__":
    unittest.main()
