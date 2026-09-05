"""Create sanitized, short-lived evidence that Production Hermes is drained."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psycopg


SCHEMA_VERSION = "supportportal-hermes-drain-v1"
DEFAULT_ACCOUNT_ID = "891612554546"
DEFAULT_REGION = "us-east-1"
DEFAULT_CLUSTER = "supportportal-production"
DEFAULT_SERVICE = "supportportal-production-hermes"
DEFAULT_API_SERVICE = "supportportal-production-api"
DEFAULT_WORKER_SERVICE = "supportportal-production-worker"
DEFAULT_BASE_URL = "https://supportcenter.stellarix.space/automation/production"
DEFAULT_DSN_PARAMETER = "/supportportal/production/automation-db-dsn"


class DrainEvidenceError(RuntimeError):
    pass


class AwsClient:
    def __init__(self, region: str) -> None:
        self.region = region

    def run(self, arguments: list[str]) -> dict[str, Any]:
        completed = subprocess.run(
            ["aws", *arguments, "--region", self.region, "--output", "json"],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise DrainEvidenceError(f"AWS read failed: {' '.join(arguments[:2])}")
        value = json.loads(completed.stdout or "{}")
        if not isinstance(value, dict):
            raise DrainEvidenceError("AWS read returned an invalid response")
        return value


def _read_release(url: str, *, opener: Callable[..., Any] = urllib.request.urlopen) -> dict[str, Any]:
    try:
        with opener(f"{url.rstrip('/')}/health/release", timeout=10) as response:
            value = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise DrainEvidenceError("Production release health read failed") from exc
    if not isinstance(value, dict):
        raise DrainEvidenceError("Production release health returned an invalid response")
    return value


def _read_dsn(aws: AwsClient, parameter_name: str) -> str:
    response = aws.run(
        ["ssm", "get-parameter", "--name", parameter_name, "--with-decryption"]
    )
    dsn = str((response.get("Parameter") or {}).get("Value") or "")
    if not dsn:
        raise DrainEvidenceError("Production database parameter is empty")
    return dsn


def _stable_service(aws: AwsClient, *, cluster: str, service_name: str) -> dict[str, Any]:
    services = aws.run(
        ["ecs", "describe-services", "--cluster", cluster, "--services", service_name]
    ).get("services") or []
    if len(services) != 1:
        raise DrainEvidenceError(f"ECS service readback is ambiguous: {service_name}")
    service = services[0]
    counts = tuple(
        int(service.get(key) or 0)
        for key in ("desiredCount", "runningCount", "pendingCount")
    )
    if counts != (1, 1, 0):
        raise DrainEvidenceError(
            f"ECS service must be stable at desired/running/pending=1/1/0: {service_name}"
        )
    if not str(service.get("taskDefinition") or ""):
        raise DrainEvidenceError(f"ECS task definition is missing: {service_name}")
    deployments = service.get("deployments") or []
    if len(deployments) != 1:
        raise DrainEvidenceError(f"ECS service rollout is not settled: {service_name}")
    deployment = deployments[0]
    if (
        deployment.get("status") != "PRIMARY"
        or deployment.get("rolloutState") != "COMPLETED"
        or deployment.get("taskDefinition") != service["taskDefinition"]
        or tuple(
            int(deployment.get(key) or 0)
            for key in ("desiredCount", "runningCount", "pendingCount")
        )
        != (1, 1, 0)
    ):
        raise DrainEvidenceError(f"ECS service rollout is not complete: {service_name}")
    return service


def _disabled_account_task(
    aws: AwsClient,
    *,
    task_definition: str,
    role: str,
) -> None:
    response = aws.run(
        ["ecs", "describe-task-definition", "--task-definition", task_definition]
    )
    containers = (response.get("taskDefinition") or {}).get("containerDefinitions") or []
    matching = [item for item in containers if item.get("name") == role]
    if len(matching) != 1:
        raise DrainEvidenceError(f"Production {role} container readback is ambiguous")
    container = matching[0]
    environment = {
        str(item.get("name") or ""): str(item.get("value") or "")
        for item in container.get("environment") or []
    }
    names = set(environment) | {
        str(item.get("name") or "") for item in container.get("secrets") or []
    }
    if environment.get("HERMES_CASE_WORKFLOW_MODE") != "disabled":
        raise DrainEvidenceError(f"Production {role} Hermes mode is not disabled")
    forbidden = {
        "ENGINEER_INVESTIGATION_REPLY_BASE_URL",
        "ENGINEER_INVESTIGATION_REPLY_API_KEY",
        "HERMES_CALLBACK_TOKEN",
    }
    if names & forbidden:
        raise DrainEvidenceError(f"Production {role} still contains Hermes runtime wiring")


def _turn_counts(
    dsn: str,
    *,
    connect: Callable[..., Any] = psycopg.connect,
) -> dict[str, int]:
    try:
        with connect(
            dsn,
            connect_timeout=10,
            options="-c default_transaction_read_only=on",
        ) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, COUNT(*) "
                "FROM supportportal_production.support_hermes_turn_requests "
                "WHERE status IN ('queued', 'active') GROUP BY status"
            )
            rows = cursor.fetchall()
    except Exception as exc:
        raise DrainEvidenceError("Production Hermes turn-count read failed") from exc
    counts = {"queued": 0, "active": 0}
    for status, count in rows:
        normalized = str(status or "")
        if normalized not in counts:
            raise DrainEvidenceError("Production Hermes turn-count response is invalid")
        counts[normalized] = int(count)
    return counts


def collect_evidence(
    *,
    aws: AwsClient,
    expected_account_id: str,
    cluster: str,
    service_name: str,
    base_url: str,
    dsn_parameter: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
    connect: Callable[..., Any] = psycopg.connect,
    now: datetime | None = None,
) -> dict[str, Any]:
    identity = aws.run(["sts", "get-caller-identity"])
    if str(identity.get("Account") or "") != expected_account_id:
        raise DrainEvidenceError("AWS account does not match the expected Production account")
    service = _stable_service(aws, cluster=cluster, service_name=service_name)
    task_definition = str(service["taskDefinition"])
    account_task_definitions: dict[str, str] = {}
    for role, account_service_name in (
        ("api", DEFAULT_API_SERVICE),
        ("worker", DEFAULT_WORKER_SERVICE),
    ):
        account_service = _stable_service(
            aws, cluster=cluster, service_name=account_service_name
        )
        account_task_definition = str(account_service["taskDefinition"])
        _disabled_account_task(
            aws,
            task_definition=account_task_definition,
            role=role,
        )
        account_task_definitions[role] = account_task_definition
    release = _read_release(base_url, opener=opener)
    mode = str((release.get("hermes_case_workflow") or {}).get("mode") or "")
    if mode != "disabled":
        raise DrainEvidenceError("Production Account Hermes mode is not disabled")
    turns = _turn_counts(_read_dsn(aws, dsn_parameter), connect=connect)
    if turns != {"queued": 0, "active": 0}:
        raise DrainEvidenceError("Production Hermes turn requests are not drained")
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": timestamp.isoformat().replace("+00:00", "Z"),
        "environment": "production",
        "cluster": cluster,
        "service": service_name,
        "task_definition": task_definition,
        "account_task_definitions": account_task_definitions,
        "hermes_case_workflow_mode": mode,
        "turn_requests": turns,
    }


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(temporary_name).unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--expected-account-id", default=DEFAULT_ACCOUNT_ID)
    parser.add_argument("--cluster", default=DEFAULT_CLUSTER)
    parser.add_argument("--service", default=DEFAULT_SERVICE)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--dsn-parameter", default=DEFAULT_DSN_PARAMETER)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.region != DEFAULT_REGION or args.expected_account_id != DEFAULT_ACCOUNT_ID:
            raise DrainEvidenceError("only the canonical Production AWS account and region are supported")
        if args.cluster != DEFAULT_CLUSTER or args.service != DEFAULT_SERVICE:
            raise DrainEvidenceError("only the canonical Production Hermes service is supported")
        if args.dsn_parameter != DEFAULT_DSN_PARAMETER:
            raise DrainEvidenceError("only the canonical Production database parameter is supported")
        if args.base_url.rstrip("/") != DEFAULT_BASE_URL:
            raise DrainEvidenceError("only the canonical Production base URL is supported")
        evidence = collect_evidence(
            aws=AwsClient(args.region),
            expected_account_id=args.expected_account_id,
            cluster=args.cluster,
            service_name=args.service,
            base_url=args.base_url,
            dsn_parameter=args.dsn_parameter,
        )
        _write_atomic(Path(args.output), evidence)
        print(
            json.dumps(
                {
                    "ok": True,
                    "output": args.output,
                    "task_definition": evidence["task_definition"],
                    "turn_requests": evidence["turn_requests"],
                },
                sort_keys=True,
            )
        )
    except DrainEvidenceError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    except Exception:
        print(
            json.dumps(
                {"ok": False, "error": "unexpected drain evidence failure"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
