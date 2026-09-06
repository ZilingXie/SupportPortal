"""Create the isolated Preproduction database identity and SSM namespace once."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import conninfo, sql


SOURCE_COPY_SUFFIXES = (
    "openai-api-key",
    "hermes-openai-api-key",
    "hermes-memory-llm-api-key",
    "hermes-memory-embedding-api-key",
    "zendesk-basic-auth",
    "rag-service-url",
    "rag-service-shared-token",
    "zendesk-ai-assignee-email",
    "zendesk-fraud-review-assignee-id",
    "account-slack-n8n-webhook-url",
    "account-slack-n8n-status-url",
    "engineer-slack-access-token",
    "billing-graph-client-secret",
    "enablement-internal-email-recipients",
    "fraud-internal-email-recipients",
    "account-suspension-internal-email-recipients",
    "archer-oauth-cookie",
)
GENERATED_TOKEN_SUFFIXES = (
    "automation-intake-shared-token",
    "dashboard-session-secret",
    "n8n-request-token",
    "hermes-api-server-key",
    "hermes-callback-token",
    "hermes-tdai-admin-key",
)


@dataclass(frozen=True)
class BootstrapConfig:
    region: str
    source_prefix: str
    target_prefix: str
    hermes_base_url: str
    schema: str = "supportportal_preproduction"
    runtime_role: str = "supportportal_preproduction_runtime"
    migration_role: str = "supportportal_preproduction_migration"


class AwsSsmClient:
    def __init__(self, region: str) -> None:
        self.region = region

    def _run(self, arguments: list[str], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        command = ["aws", "ssm", *arguments, "--region", self.region, "--output", "json"]
        payload_path: str | None = None
        try:
            if payload is not None:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    prefix="supportportal-ssm-",
                    delete=False,
                ) as payload_file:
                    payload_path = payload_file.name
                    os.chmod(payload_path, 0o600)
                    json.dump(payload, payload_file)
                command.extend(["--cli-input-json", f"file://{payload_path}"])
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
            )
        finally:
            if payload_path is not None:
                os.unlink(payload_path)
        if completed.returncode != 0:
            raise RuntimeError("AWS SSM operation failed")
        value = json.loads(completed.stdout or "{}")
        if not isinstance(value, dict):
            raise RuntimeError("AWS SSM returned an invalid response")
        return value

    def get_parameter(self, *, Name: str, WithDecryption: bool) -> dict[str, Any]:
        arguments = ["get-parameter", "--name", Name]
        if WithDecryption:
            arguments.append("--with-decryption")
        return self._run(arguments)

    def get_parameters_by_path(
        self,
        *,
        Path: str,
        NextToken: str | None = None,
    ) -> dict[str, Any]:
        arguments = ["get-parameters-by-path", "--path", Path, "--recursive"]
        if NextToken:
            arguments.extend(["--next-token", NextToken])
        return self._run(arguments)

    def put_parameter(self, **payload: Any) -> dict[str, Any]:
        return self._run(["put-parameter"], payload)

    def delete_parameters(self, *, Names: list[str]) -> dict[str, Any]:
        return self._run(["delete-parameters"], {"Names": Names})


def _ssm_client(region: str) -> AwsSsmClient:
    return AwsSsmClient(region)


def _parameter_name(prefix: str, suffix: str) -> str:
    return f"{prefix.rstrip('/')}/{suffix}"


def _read_parameter(client: Any, name: str) -> str:
    response = client.get_parameter(Name=name, WithDecryption=True)
    value = str(response["Parameter"]["Value"] or "")
    if not value:
        raise RuntimeError(f"required SSM parameter is empty: {name}")
    return value


def _existing_target_parameters(client: Any, config: BootstrapConfig) -> set[str]:
    existing: set[str] = set()
    next_token: str | None = None
    while True:
        page = client.get_parameters_by_path(
            Path=config.target_prefix,
            NextToken=next_token,
        )
        existing.update(str(item["Name"]) for item in page.get("Parameters") or [])
        next_token = str(page.get("NextToken") or "") or None
        if next_token is None:
            break
    return existing


def _make_role_dsn(source_dsn: str, *, user: str, password: str) -> str:
    values = conninfo.conninfo_to_dict(source_dsn)
    values.update({"user": user, "password": password})
    return conninfo.make_conninfo(**values)


def _assert_fresh_database(cursor: Any, config: BootstrapConfig) -> None:
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=%s), "
        "EXISTS (SELECT 1 FROM pg_roles WHERE rolname=%s), "
        "EXISTS (SELECT 1 FROM pg_roles WHERE rolname=%s)",
        (config.schema, config.runtime_role, config.migration_role),
    )
    schema_exists, runtime_exists, migration_exists = cursor.fetchone()
    if schema_exists or runtime_exists or migration_exists:
        raise RuntimeError("Preproduction schema or database roles already exist")


def _create_database_identity(
    cursor: Any,
    config: BootstrapConfig,
    *,
    runtime_password: str,
    migration_password: str,
) -> None:
    cursor.execute(
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(config.runtime_role),
            sql.Literal(runtime_password),
        )
    )
    cursor.execute(
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(config.migration_role),
            sql.Literal(migration_password),
        )
    )
    cursor.execute(
        sql.SQL("GRANT {} TO CURRENT_USER").format(
            sql.Identifier(config.migration_role),
        )
    )
    cursor.execute(
        sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
            sql.Identifier(config.schema),
            sql.Identifier(config.migration_role),
        )
    )
    cursor.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
            sql.Identifier(cursor.connection.info.dbname),
            sql.Identifier(config.runtime_role),
            sql.Identifier(config.migration_role),
        )
    )
    cursor.execute(
        sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(
            sql.Identifier(cursor.connection.info.dbname),
            sql.Identifier(config.migration_role),
        )
    )
    cursor.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(config.schema),
            sql.Identifier(config.runtime_role),
        )
    )
    cursor.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
        ).format(
            sql.Identifier(config.migration_role),
            sql.Identifier(config.schema),
            sql.Identifier(config.runtime_role),
        )
    )
    cursor.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
            "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {}"
        ).format(
            sql.Identifier(config.migration_role),
            sql.Identifier(config.schema),
            sql.Identifier(config.runtime_role),
        )
    )
    cursor.execute(
        "SELECT has_schema_privilege(%s, %s, 'USAGE'), "
        "has_schema_privilege(%s, 'supportportal_production', 'USAGE'), "
        "has_schema_privilege(%s, 'supportportal_production', 'USAGE'), "
        "has_database_privilege(%s, current_database(), 'CREATE'), "
        "has_database_privilege(%s, current_database(), 'CREATE')",
        (
            config.runtime_role,
            config.schema,
            config.runtime_role,
            config.migration_role,
            config.runtime_role,
            config.migration_role,
        ),
    )
    (
        target_access,
        runtime_production_access,
        migration_production_access,
        runtime_database_create,
        migration_database_create,
    ) = cursor.fetchone()
    if (
        not target_access
        or runtime_production_access
        or migration_production_access
        or runtime_database_create
        or not migration_database_create
    ):
        raise RuntimeError("Preproduction database role schema isolation check failed")
    cursor.execute(
        sql.SQL("REVOKE {} FROM CURRENT_USER").format(
            sql.Identifier(config.migration_role),
        )
    )


def _put_parameters(client: Any, values: dict[str, str], created: list[str]) -> None:
    for name, value in values.items():
        client.put_parameter(
            Name=name,
            Value=value,
            Type="SecureString",
            Overwrite=False,
            Tier="Standard",
        )
        created.append(name)


def bootstrap(config: BootstrapConfig, *, check_only: bool = False) -> dict[str, Any]:
    ssm = _ssm_client(config.region)
    existing_parameters = _existing_target_parameters(ssm, config)
    if existing_parameters:
        raise RuntimeError("Preproduction SSM namespace is not fresh")
    source_migration_dsn = _read_parameter(
        ssm,
        _parameter_name(config.source_prefix, "automation-db-migration-dsn"),
    )
    with psycopg.connect(source_migration_dsn) as connection, connection.cursor() as cursor:
        _assert_fresh_database(cursor, config)
        if check_only:
            return {
                "ok": True,
                "mode": "check-only",
                "schema_fresh": True,
                "roles_fresh": True,
                "ssm_namespace_fresh": True,
            }

        runtime_password = secrets.token_urlsafe(36)
        migration_password = secrets.token_urlsafe(36)
        values = {
            _parameter_name(config.target_prefix, suffix): _read_parameter(
                ssm,
                _parameter_name(config.source_prefix, suffix),
            )
            for suffix in SOURCE_COPY_SUFFIXES
        }
        values.update(
            {
                _parameter_name(config.target_prefix, suffix): (
                    f"sk-mem-{secrets.token_urlsafe(48)}"
                    if suffix == "hermes-tdai-admin-key"
                    else secrets.token_urlsafe(48)
                )
                for suffix in GENERATED_TOKEN_SUFFIXES
            }
        )
        values[_parameter_name(config.target_prefix, "hermes-base-url")] = config.hermes_base_url
        values[_parameter_name(config.target_prefix, "automation-db-dsn")] = _make_role_dsn(
            source_migration_dsn,
            user=config.runtime_role,
            password=runtime_password,
        )
        values[
            _parameter_name(config.target_prefix, "automation-db-migration-dsn")
        ] = _make_role_dsn(
            source_migration_dsn,
            user=config.migration_role,
            password=migration_password,
        )
        created_parameters: list[str] = []
        try:
            with connection.transaction():
                _create_database_identity(
                    cursor,
                    config,
                    runtime_password=runtime_password,
                    migration_password=migration_password,
                )
                _put_parameters(ssm, values, created_parameters)
        except Exception:
            for index in range(0, len(created_parameters), 10):
                ssm.delete_parameters(Names=created_parameters[index : index + 10])
            raise
    return {
        "ok": True,
        "mode": "bootstrap",
        "environment": "preproduction",
        "schema_created": True,
        "runtime_role_created": True,
        "migration_role_created": True,
        "production_schema_access": False,
        "parameter_count": len(values),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--source-prefix", default="/supportportal/production")
    parser.add_argument("--target-prefix", default="/supportportal/preproduction")
    parser.add_argument("--hermes-base-url", required=True)
    parser.add_argument("--check-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.source_prefix != "/supportportal/production":
            raise ValueError("source SSM prefix must be /supportportal/production")
        if args.target_prefix != "/supportportal/preproduction":
            raise ValueError("target SSM prefix must be /supportportal/preproduction")
        if not str(args.hermes_base_url).startswith("http://hermes.preproduction."):
            raise ValueError("Hermes base URL must be the private Preproduction service")
        result = bootstrap(
            BootstrapConfig(
                region=args.region,
                source_prefix=args.source_prefix,
                target_prefix=args.target_prefix,
                hermes_base_url=args.hermes_base_url,
            ),
            check_only=args.check_only,
        )
        print(json.dumps(result, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
