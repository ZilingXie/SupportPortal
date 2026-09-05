from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.scripts.bootstrap_automation_preproduction import (
    BootstrapConfig,
    GENERATED_TOKEN_SUFFIXES,
    SOURCE_COPY_SUFFIXES,
    bootstrap,
    main,
)


def _connection() -> tuple[MagicMock, MagicMock]:
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [
        (False, False, False),
        (True, False, False),
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
    rendered_sql = " ".join(str(call.args[0]) for call in cursor.execute.call_args_list)
    assert "CREATE ROLE" in rendered_sql
    assert "CREATE SCHEMA" in rendered_sql
    assert "supportportal_production" in rendered_sql


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


@pytest.mark.parametrize("role_access", [(True, True, False), (True, False, True)])
def test_preproduction_bootstrap_rejects_production_access_for_either_role(
    role_access: tuple[bool, bool, bool],
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
