from __future__ import annotations

from unittest.mock import Mock, patch

from backend.scripts import automation_ecs_bootstrap


def test_bootstrap_owns_ddl_and_closes_account_repository() -> None:
    settings = Mock(environment="production", db_schema="coordination")
    settings.provenance.return_value.schema_revision = "automation-ecs-001"
    store = Mock()
    repository = Mock()
    with patch.object(automation_ecs_bootstrap.AutomationEcsSettings, "from_env", return_value=settings), patch.object(
        automation_ecs_bootstrap, "create_automation_ecs_store", return_value=store
    ), patch.object(
        automation_ecs_bootstrap, "create_ticket_repository", return_value=repository
    ), patch.object(
        automation_ecs_bootstrap.PromptVersionService, "sync_catalog"
    ) as prompts, patch.object(
        automation_ecs_bootstrap,
        "check_account_runtime_schema",
        return_value={"ok": True, "schema": "account", "missing": []},
    ):
        result = automation_ecs_bootstrap.run(["bootstrap"])

    repository.initialize.assert_called_once_with()
    prompts.assert_called_once_with()
    store.migrate.assert_called_once_with()
    store.check_schema.assert_called_once_with()
    repository.close.assert_called_once_with()
    assert result["schema_revision"] == "automation-ecs-001"


def test_check_is_read_only() -> None:
    settings = Mock(environment="preproduction", db_schema="coordination")
    settings.provenance.return_value.schema_revision = "automation-ecs-001"
    store = Mock()
    with patch.object(automation_ecs_bootstrap.AutomationEcsSettings, "from_env", return_value=settings), patch.object(
        automation_ecs_bootstrap, "create_automation_ecs_store", return_value=store
    ), patch.object(
        automation_ecs_bootstrap, "create_ticket_repository", side_effect=AssertionError("no repository DDL")
    ), patch.object(
        automation_ecs_bootstrap,
        "check_account_runtime_schema",
        return_value={"ok": True, "schema": "account", "missing": []},
    ):
        result = automation_ecs_bootstrap.run(["check"])

    store.check_schema.assert_called_once_with()
    store.migrate.assert_not_called()
    assert result["mode"] == "check"
