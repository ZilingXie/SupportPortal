from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.scripts.bootstrap_automation_preproduction import (
    AwsSsmClient,
    BootstrapConfig,
    GENERATED_TOKEN_SUFFIXES,
    SOURCE_COPY_SUFFIXES,
    bootstrap,
    main,
)


def test_ssm_cli_payload_uses_private_short_lived_file_not_argv() -> None:
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        payload_uri = command[command.index("--cli-input-json") + 1]
        payload_path = payload_uri.removeprefix("file://")
        observed["command"] = command
        observed["payload_path"] = payload_path
        observed["mode"] = os.stat(payload_path).st_mode & 0o777
        with open(payload_path, encoding="utf-8") as payload_file:
            observed["payload"] = json.load(payload_file)
        assert "input" not in kwargs
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    with patch(
        "backend.scripts.bootstrap_automation_preproduction.subprocess.run",
        side_effect=fake_run,
    ):
        AwsSsmClient("us-east-1").put_parameter(
            Name="/supportportal/preproduction/test",
            Value="redacted-secret-value",
            Type="SecureString",
            Overwrite=False,
        )

    assert "redacted-secret-value" not in " ".join(observed["command"])
    assert observed["mode"] == 0o600
    assert observed["payload"] == {
        "Name": "/supportportal/preproduction/test",
        "Value": "redacted-secret-value",
        "Type": "SecureString",
        "Overwrite": False,
    }
    assert not os.path.exists(str(observed["payload_path"]))


def test_ssm_cli_payload_file_is_removed_when_subprocess_fails() -> None:
    observed_path = ""

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        nonlocal observed_path
        observed_path = command[command.index("--cli-input-json") + 1].removeprefix("file://")
        assert os.path.exists(observed_path)
        raise OSError("subprocess failed")

    with (
        patch(
            "backend.scripts.bootstrap_automation_preproduction.subprocess.run",
            side_effect=fake_run,
        ),
        pytest.raises(OSError, match="subprocess failed"),
    ):
        AwsSsmClient("us-east-1").put_parameter(
            Name="/supportportal/preproduction/test",
            Value="redacted-secret-value",
            Type="SecureString",
        )

    assert observed_path
    assert not os.path.exists(observed_path)


def test_ssm_cli_payload_file_is_removed_when_serialization_fails() -> None:
    observed_path = ""

    def fake_dump(_: object, payload_file: object) -> None:
        nonlocal observed_path
        observed_path = str(payload_file.name)
        raise TypeError("serialization failed")

    with (
        patch(
            "backend.scripts.bootstrap_automation_preproduction.json.dump",
            side_effect=fake_dump,
        ),
        patch("backend.scripts.bootstrap_automation_preproduction.subprocess.run") as run,
        pytest.raises(TypeError, match="serialization failed"),
    ):
        AwsSsmClient("us-east-1").put_parameter(
            Name="/supportportal/preproduction/test",
            Value="redacted-secret-value",
            Type="SecureString",
        )

    run.assert_not_called()
    assert observed_path
    assert not os.path.exists(observed_path)


def _connection() -> tuple[MagicMock, MagicMock]:
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [
        (False, False, False),
        (True, False, False, False, True),
    ]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    connection.info.dbname = "supportportal"
    cursor.connection = connection
    connection.transaction.return_value.__enter__.return_value = None
    return connection, cursor


def _ssm() -> MagicMock:
    client = MagicMock()
    client.get_parameters_by_path.return_value = {"Parameters": []}
    client.get_parameter.return_value = {"Parameter": {"Value": "postgresql://admin:secret@db/supportportal"}}
    return client


def test_preproduction_bootstrap_creates_isolated_roles_and_secret_namespace() -> None:
    connection, cursor = _connection()
    ssm = _ssm()
    with (
        patch("backend.scripts.bootstrap_automation_preproduction._ssm_client", return_value=ssm),
        patch("backend.scripts.bootstrap_automation_preproduction.psycopg.connect", return_value=connection),
    ):
        result = bootstrap(
            BootstrapConfig(
                region="us-east-1",
                source_prefix="/supportportal/production",
                target_prefix="/supportportal/preproduction",
                hermes_base_url="http://hermes.preproduction.supportportal.local:8642",
            )
        )
    assert result["production_schema_access"] is False
    assert result["parameter_count"] == len(SOURCE_COPY_SUFFIXES) + len(GENERATED_TOKEN_SUFFIXES) + 3
    names = [call.kwargs["Name"] for call in ssm.put_parameter.call_args_list]
    assert len(names) == len(set(names))
    assert all(name.startswith("/supportportal/preproduction/") for name in names)
    assert all(call.kwargs["Overwrite"] is False for call in ssm.put_parameter.call_args_list)
    copied_suffixes = {
        "hermes-openai-api-key",
        "hermes-memory-llm-api-key",
        "hermes-memory-embedding-api-key",
    }
    copied_values = {
        call.kwargs["Name"].rsplit("/", 1)[-1]: call.kwargs["Value"]
        for call in ssm.put_parameter.call_args_list
        if call.kwargs["Name"].rsplit("/", 1)[-1] in copied_suffixes
    }
    assert copied_values == {
        suffix: "postgresql://admin:secret@db/supportportal" for suffix in copied_suffixes
    }
    generated_api_key = next(
        call.kwargs["Value"]
        for call in ssm.put_parameter.call_args_list
        if call.kwargs["Name"].endswith("/hermes-api-server-key")
    )
    assert generated_api_key != "postgresql://admin:secret@db/supportportal"
    source_reads = {call.kwargs["Name"] for call in ssm.get_parameter.call_args_list}
    assert {
        f"/supportportal/production/{suffix}" for suffix in copied_suffixes
    } <= source_reads
    assert "/supportportal/production/hermes-api-server-key" not in source_reads
    rendered_sql = " ".join(str(call.args[0]) for call in cursor.execute.call_args_list)
    assert "CREATE ROLE" in rendered_sql
    assert "CREATE SCHEMA" in rendered_sql
    assert "supportportal_production" in rendered_sql
    statements = [str(call.args[0]) for call in cursor.execute.call_args_list]
    grant_index = next(
        index
        for index, value in enumerate(statements)
        if "GRANT" in value and "CURRENT_USER" in value
    )
    schema_index = next(
        index for index, value in enumerate(statements) if "CREATE SCHEMA" in value
    )
    revoke_index = next(
        index
        for index, value in enumerate(statements)
        if "REVOKE" in value and "CURRENT_USER" in value
    )
    isolation_index = next(
        index
        for index, value in enumerate(statements)
        if "supportportal_production" in value
    )
    assert grant_index < schema_index < isolation_index < revoke_index
    assert any("GRANT CREATE ON DATABASE" in value for value in statements)


def test_preproduction_bootstrap_check_only_has_no_writes() -> None:
    connection, _ = _connection()
    ssm = _ssm()
    with (
        patch("backend.scripts.bootstrap_automation_preproduction._ssm_client", return_value=ssm),
        patch("backend.scripts.bootstrap_automation_preproduction.psycopg.connect", return_value=connection),
    ):
        result = bootstrap(
            BootstrapConfig(
                region="us-east-1",
                source_prefix="/supportportal/production",
                target_prefix="/supportportal/preproduction",
                hermes_base_url="http://hermes.preproduction.supportportal.local:8642",
            ),
            check_only=True,
        )
    assert result["mode"] == "check-only"
    ssm.put_parameter.assert_not_called()
    ssm.delete_parameters.assert_not_called()


def test_preproduction_bootstrap_fails_closed_when_target_is_not_fresh() -> None:
    ssm = _ssm()
    ssm.get_parameters_by_path.return_value = {
        "Parameters": [{"Name": "/supportportal/preproduction/openai-api-key"}]
    }
    with patch("backend.scripts.bootstrap_automation_preproduction._ssm_client", return_value=ssm):
        with pytest.raises(RuntimeError, match="SSM namespace is not fresh"):
            bootstrap(
                BootstrapConfig(
                    region="us-east-1",
                    source_prefix="/supportportal/production",
                    target_prefix="/supportportal/preproduction",
                    hermes_base_url="http://hermes.preproduction.supportportal.local:8642",
                )
            )


def test_preproduction_bootstrap_removes_partial_ssm_writes_on_failure() -> None:
    connection, _ = _connection()
    ssm = _ssm()
    ssm.put_parameter.side_effect = [None, None, RuntimeError("write failed")]
    with (
        patch("backend.scripts.bootstrap_automation_preproduction._ssm_client", return_value=ssm),
        patch("backend.scripts.bootstrap_automation_preproduction.psycopg.connect", return_value=connection),
    ):
        with pytest.raises(RuntimeError, match="write failed"):
            bootstrap(
                BootstrapConfig(
                    region="us-east-1",
                    source_prefix="/supportportal/production",
                    target_prefix="/supportportal/preproduction",
                    hermes_base_url="http://hermes.preproduction.supportportal.local:8642",
                )
            )
    deleted = ssm.delete_parameters.call_args.kwargs["Names"]
    assert len(deleted) == 2
    assert all(name.startswith("/supportportal/preproduction/") for name in deleted)


def test_preproduction_bootstrap_role_membership_is_inside_database_transaction() -> None:
    connection, cursor = _connection()
    ssm = _ssm()
    with (
        patch("backend.scripts.bootstrap_automation_preproduction._ssm_client", return_value=ssm),
        patch("backend.scripts.bootstrap_automation_preproduction.psycopg.connect", return_value=connection),
    ):
        bootstrap(
            BootstrapConfig(
                region="us-east-1",
                source_prefix="/supportportal/production",
                target_prefix="/supportportal/preproduction",
                hermes_base_url="http://hermes.preproduction.supportportal.local:8642",
            )
        )
    connection.transaction.assert_called_once_with()
    statements = [str(call.args[0]) for call in cursor.execute.call_args_list]
    assert any("GRANT" in value and "CURRENT_USER" in value for value in statements)
    assert any("REVOKE" in value and "CURRENT_USER" in value for value in statements)


@pytest.mark.parametrize(
    "role_access",
    [
        (True, True, False, False, True),
        (True, False, True, False, True),
        (True, False, False, True, True),
        (True, False, False, False, False),
    ],
)
def test_preproduction_bootstrap_rejects_production_access_for_either_role(
    role_access: tuple[bool, bool, bool, bool, bool],
) -> None:
    connection, cursor = _connection()
    cursor.fetchone.side_effect = [(False, False, False), role_access]
    ssm = _ssm()
    with (
        patch("backend.scripts.bootstrap_automation_preproduction._ssm_client", return_value=ssm),
        patch("backend.scripts.bootstrap_automation_preproduction.psycopg.connect", return_value=connection),
    ):
        with pytest.raises(RuntimeError, match="database role schema isolation"):
            bootstrap(
                BootstrapConfig(
                    region="us-east-1",
                    source_prefix="/supportportal/production",
                    target_prefix="/supportportal/preproduction",
                    hermes_base_url="http://hermes.preproduction.supportportal.local:8642",
                )
            )
    ssm.put_parameter.assert_not_called()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            ["--source-prefix", "/wrong", "--hermes-base-url", "http://hermes.preproduction.supportportal.local:8642"],
            "source SSM prefix",
        ),
        (
            ["--target-prefix", "/wrong", "--hermes-base-url", "http://hermes.preproduction.supportportal.local:8642"],
            "target SSM prefix",
        ),
    ],
)
def test_preproduction_bootstrap_cli_rejects_noncanonical_namespaces(
    arguments: list[str],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(arguments) == 1
    assert message in capsys.readouterr().err
