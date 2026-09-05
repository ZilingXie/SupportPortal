from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.scripts.create_hermes_drain_evidence import (
    DEFAULT_BASE_URL,
    DEFAULT_CLUSTER,
    DEFAULT_DSN_PARAMETER,
    DEFAULT_SERVICE,
    DrainEvidenceError,
    _write_atomic,
    collect_evidence,
    main,
)


TASK_DEFINITION = (
    "arn:aws:ecs:us-east-1:891612554546:task-definition/supportportal-production-hermes:3"
)
API_TASK_DEFINITION = (
    "arn:aws:ecs:us-east-1:891612554546:task-definition/supportportal-production-api:30"
)
WORKER_TASK_DEFINITION = (
    "arn:aws:ecs:us-east-1:891612554546:task-definition/supportportal-production-worker:32"
)


class _Response:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.value).encode()


def _service(task_definition: str) -> dict[str, object]:
    deployment = {
        "status": "PRIMARY",
        "rolloutState": "COMPLETED",
        "taskDefinition": task_definition,
        "desiredCount": 1,
        "runningCount": 1,
        "pendingCount": 0,
    }
    return {
        "services": [
            {
                "desiredCount": 1,
                "runningCount": 1,
                "pendingCount": 0,
                "taskDefinition": task_definition,
                "deployments": [deployment],
            }
        ]
    }


def _account_task_definition(
    role: str,
    *,
    mode: str = "disabled",
    extra_environment: tuple[str, ...] = (),
    extra_secrets: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "taskDefinition": {
            "containerDefinitions": [
                {
                    "name": role,
                    "environment": [
                        {"name": "HERMES_CASE_WORKFLOW_MODE", "value": mode},
                        *(
                            {"name": name, "value": "redacted"}
                            for name in extra_environment
                        ),
                    ],
                    "secrets": [
                        {"name": name, "valueFrom": "arn:aws:ssm:us-east-1:example"}
                        for name in extra_secrets
                    ],
                }
            ]
        }
    }


def _aws(
    *,
    api_mode: str = "disabled",
    worker_mode: str = "disabled",
    api_environment: tuple[str, ...] = (),
    api_secrets: tuple[str, ...] = (),
    worker_environment: tuple[str, ...] = (),
    worker_secrets: tuple[str, ...] = (),
) -> MagicMock:
    aws = MagicMock()
    aws.run.side_effect = [
        {"Account": "891612554546"},
        _service(TASK_DEFINITION),
        _service(API_TASK_DEFINITION),
        _account_task_definition(
            "api",
            mode=api_mode,
            extra_environment=api_environment,
            extra_secrets=api_secrets,
        ),
        _service(WORKER_TASK_DEFINITION),
        _account_task_definition(
            "worker",
            mode=worker_mode,
            extra_environment=worker_environment,
            extra_secrets=worker_secrets,
        ),
        {"Parameter": {"Value": "postgresql://runtime:secret@example.invalid/supportportal"}},
    ]
    return aws


def _connection(rows: list[tuple[str, int]]) -> MagicMock:
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchall.return_value = rows
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    return connection


def test_collects_sanitized_read_only_drain_evidence() -> None:
    aws = _aws()
    connect = MagicMock(return_value=_connection([]))
    evidence = collect_evidence(
        aws=aws,
        expected_account_id="891612554546",
        cluster=DEFAULT_CLUSTER,
        service_name=DEFAULT_SERVICE,
        base_url=DEFAULT_BASE_URL,
        dsn_parameter=DEFAULT_DSN_PARAMETER,
        opener=lambda *_args, **_kwargs: _Response(
            {"hermes_case_workflow": {"mode": "disabled"}}
        ),
        connect=connect,
        now=datetime(2026, 9, 6, tzinfo=timezone.utc),
    )
    assert evidence["turn_requests"] == {"queued": 0, "active": 0}
    assert evidence["task_definition"] == TASK_DEFINITION
    assert evidence["account_task_definitions"] == {
        "api": API_TASK_DEFINITION,
        "worker": WORKER_TASK_DEFINITION,
    }
    assert "dsn" not in json.dumps(evidence).lower()
    assert "secret" not in json.dumps(evidence).lower()
    assert connect.call_args.kwargs["options"] == "-c default_transaction_read_only=on"
    cursor = connect.return_value.cursor.return_value
    assert "supportportal_production.support_hermes_turn_requests" in (
        cursor.execute.call_args.args[0]
    )


@pytest.mark.parametrize("rows", [[("queued", 1)], [("active", 1)]])
def test_rejects_queued_or_active_turns(rows: list[tuple[str, int]]) -> None:
    with pytest.raises(DrainEvidenceError, match="not drained"):
        collect_evidence(
            aws=_aws(),
            expected_account_id="891612554546",
            cluster=DEFAULT_CLUSTER,
            service_name=DEFAULT_SERVICE,
            base_url=DEFAULT_BASE_URL,
            dsn_parameter=DEFAULT_DSN_PARAMETER,
            opener=lambda *_args, **_kwargs: _Response(
                {"hermes_case_workflow": {"mode": "disabled"}}
            ),
            connect=MagicMock(return_value=_connection(rows)),
        )


def test_rejects_non_disabled_production_before_database_read() -> None:
    aws = _aws()
    connect = MagicMock()
    with pytest.raises(DrainEvidenceError, match="not disabled"):
        collect_evidence(
            aws=aws,
            expected_account_id="891612554546",
            cluster=DEFAULT_CLUSTER,
            service_name=DEFAULT_SERVICE,
            base_url=DEFAULT_BASE_URL,
            dsn_parameter=DEFAULT_DSN_PARAMETER,
            opener=lambda *_args, **_kwargs: _Response(
                {"hermes_case_workflow": {"mode": "mock"}}
            ),
            connect=connect,
        )
    connect.assert_not_called()


@pytest.mark.parametrize(
    ("role", "aws"),
    [
        ("api", _aws(api_mode="mock")),
        ("worker", _aws(worker_mode="real")),
    ],
)
def test_rejects_non_disabled_account_task_definition_before_database_read(
    role: str,
    aws: MagicMock,
) -> None:
    connect = MagicMock()
    opener = MagicMock()
    with pytest.raises(DrainEvidenceError, match=rf"{role} Hermes mode is not disabled"):
        collect_evidence(
            aws=aws,
            expected_account_id="891612554546",
            cluster=DEFAULT_CLUSTER,
            service_name=DEFAULT_SERVICE,
            base_url=DEFAULT_BASE_URL,
            dsn_parameter=DEFAULT_DSN_PARAMETER,
            opener=opener,
            connect=connect,
        )
    opener.assert_not_called()
    connect.assert_not_called()


@pytest.mark.parametrize(
    ("role", "aws"),
    [
        (
            "api",
            _aws(api_environment=("ENGINEER_INVESTIGATION_REPLY_BASE_URL",)),
        ),
        (
            "worker",
            _aws(worker_secrets=("ENGINEER_INVESTIGATION_REPLY_API_KEY",)),
        ),
        ("worker", _aws(worker_environment=("HERMES_CALLBACK_TOKEN",))),
    ],
)
def test_rejects_residual_account_hermes_wiring(
    role: str,
    aws: MagicMock,
) -> None:
    with pytest.raises(DrainEvidenceError, match=rf"{role} still contains Hermes"):
        collect_evidence(
            aws=aws,
            expected_account_id="891612554546",
            cluster=DEFAULT_CLUSTER,
            service_name=DEFAULT_SERVICE,
            base_url=DEFAULT_BASE_URL,
            dsn_parameter=DEFAULT_DSN_PARAMETER,
        )


def test_rejects_unstable_service_or_wrong_account() -> None:
    wrong = _aws()
    wrong.run.side_effect = [{"Account": "000000000000"}]
    with pytest.raises(DrainEvidenceError, match="account"):
        collect_evidence(
            aws=wrong,
            expected_account_id="891612554546",
            cluster=DEFAULT_CLUSTER,
            service_name=DEFAULT_SERVICE,
            base_url=DEFAULT_BASE_URL,
            dsn_parameter=DEFAULT_DSN_PARAMETER,
        )
    unstable = _aws()
    unstable.run.side_effect = [
        {"Account": "891612554546"},
        {"services": [{"desiredCount": 1, "runningCount": 0, "pendingCount": 1}]},
    ]
    with pytest.raises(DrainEvidenceError, match="1/1/0"):
        collect_evidence(
            aws=unstable,
            expected_account_id="891612554546",
            cluster=DEFAULT_CLUSTER,
            service_name=DEFAULT_SERVICE,
            base_url=DEFAULT_BASE_URL,
            dsn_parameter=DEFAULT_DSN_PARAMETER,
        )


@pytest.mark.parametrize(
    "deployments",
    [
        [],
        [
            {
                "status": "PRIMARY",
                "rolloutState": "IN_PROGRESS",
                "taskDefinition": TASK_DEFINITION,
                "desiredCount": 1,
                "runningCount": 1,
                "pendingCount": 0,
            }
        ],
        [
            {
                "status": "PRIMARY",
                "rolloutState": "COMPLETED",
                "taskDefinition": TASK_DEFINITION,
                "desiredCount": 1,
                "runningCount": 1,
                "pendingCount": 0,
            },
            {"status": "ACTIVE"},
        ],
    ],
)
def test_rejects_unsettled_service_rollout(deployments: list[dict[str, object]]) -> None:
    aws = _aws()
    hermes = _service(TASK_DEFINITION)
    hermes["services"][0]["deployments"] = deployments
    aws.run.side_effect = [
        {"Account": "891612554546"},
        hermes,
    ]
    with pytest.raises(DrainEvidenceError, match="rollout"):
        collect_evidence(
            aws=aws,
            expected_account_id="891612554546",
            cluster=DEFAULT_CLUSTER,
            service_name=DEFAULT_SERVICE,
            base_url=DEFAULT_BASE_URL,
            dsn_parameter=DEFAULT_DSN_PARAMETER,
        )


def test_main_does_not_echo_unexpected_secret_text(capsys: pytest.CaptureFixture[str]) -> None:
    with patch(
        "backend.scripts.create_hermes_drain_evidence.collect_evidence",
        side_effect=RuntimeError("postgresql://user:password@example.invalid/database"),
    ):
        assert main(["--output", "/tmp/not-written.json"]) == 1
    error = capsys.readouterr().err
    assert "unexpected drain evidence failure" in error
    assert "password" not in error


def test_atomic_output_is_owner_only(tmp_path) -> None:
    target = tmp_path / "drain" / "evidence.json"
    _write_atomic(target, {"ok": True})
    assert json.loads(target.read_text()) == {"ok": True}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_main_rejects_noncanonical_account_before_reads(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("backend.scripts.create_hermes_drain_evidence.collect_evidence") as collect:
        assert main(["--expected-account-id", "000000000000", "--output", "/tmp/no.json"]) == 1
    collect.assert_not_called()
    assert "canonical Production AWS account" in capsys.readouterr().err
