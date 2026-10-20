"""preproduction-prompt-sync-resilience v1: Prompt target failure classification,
redaction and bounded recovery in the ECS deploy script.

The shell tests source ``deployment/deploy_automation_ecs_release.sh`` and run
its real recovery wrapper / Prompt phase against a fake Prompt CLI, with the
real Python classifier behind ``PYTHON_BIN``.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from backend.scripts import automation_ecs_release_pipeline as pipeline
from backend.scripts import prompt_release
from backend.scripts.automation_ecs_release_pipeline import (
    PROMPT_OPERATION_ERROR_SCHEMA,
    assert_evidence_secret_free,
    classify_prompt_operation_failure,
    parse_prompt_cli_failure,
    prompt_cli_failure_detail,
    record_prompt_operation_failure,
    redact_sensitive_text,
)

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = ROOT / "deployment" / "deploy_automation_ecs_release.sh"
TARGET_DSN = "postgresql://deploy:Sup3rS3cret@db.internal:5432/app?sslmode=require"
SESSION_TOKEN = "FQoGZXIvYXdz-session-sentinel"
SECRET_MARKERS = ("Sup3rS3cret", "postgresql://", "postgres://", SESSION_TOKEN, "AKIAIOSFODNN7EXAMPLE")
ENVIRONMENT = {"PROMPT_RELEASE_TARGET_DSN": TARGET_DSN, "AWS_SESSION_TOKEN": SESSION_TOKEN}


def _stderr(error: str, exception_type: str = "OperationalError", sqlstate: str = "") -> str:
    detail: dict[str, object] = {"ok": False, "error": error, "exception_type": exception_type}
    if sqlstate:
        detail["sqlstate"] = sqlstate
    return json.dumps(detail)


TRANSIENT_STDERR = _stderr(
    f'connection failed: connection to server at "db.internal", port 5432 failed: '
    f"server closed the connection unexpectedly; dsn={TARGET_DSN} token={SESSION_TOKEN}",
    sqlstate="08006",
)
TIMEOUT_STDERR = _stderr(f"connection timeout expired while dialing {TARGET_DSN}")
PERMISSION_STDERR = _stderr(
    "permission denied for schema supportportal_preproduction", "InsufficientPrivilege", "42501"
)
SCHEMA_MISSING_STDERR = _stderr(
    'relation "supportportal_preproduction.support_prompt_releases" does not exist',
    "UndefinedTable",
    "42P01",
)


def _assert_secret_free_text(text: str, where: str) -> None:
    lowered = text.lower()
    for marker in SECRET_MARKERS:
        assert marker.lower() not in lowered, f"{marker!r} leaked into {where}"


# ---------------------------------------------------------------------------
# Redaction (C1)
# ---------------------------------------------------------------------------


def test_redaction_removes_dsn_credentials_tokens_and_environment_literals() -> None:
    raw = (
        f'connection failed: connection to server at "db.internal" (10.0.0.5), port 5432 failed: '
        f"Connection refused; dsn={TARGET_DSN} password=Sup3rS3cret token={SESSION_TOKEN} "
        "Authorization: Bearer abc.def.ghi AKIAIOSFODNN7EXAMPLE host=db.internal user=deploy"
    )
    redacted = redact_sensitive_text(raw, environment=ENVIRONMENT)
    _assert_secret_free_text(redacted, "redacted text")
    assert "abc.def.ghi" not in redacted
    assert "Connection refused" in redacted
    assert "host=db.internal" in redacted
    assert_evidence_secret_free({"redacted_message": redacted}, "artifact")


def test_redaction_removes_a_bare_dsn_password_component() -> None:
    redacted = redact_sensitive_text("FATAL: Sup3rS3cret was echoed on its own", environment=ENVIRONMENT)
    assert "Sup3rS3cret" not in redacted
    assert "[redacted]" in redacted


def test_redaction_ignores_numeric_and_short_environment_values() -> None:
    environment = {"TICKET_DB_POOL_TIMEOUT_SECONDS_TOKEN": "8", "DEPLOY_TOKEN": "on"}
    assert redact_sensitive_text("8 connections on host", environment=environment) == "8 connections on host"


def test_redaction_keeps_the_tail_within_the_limit() -> None:
    redacted = redact_sensitive_text("x" * 50 + " tail-marker", environment={}, limit=20)
    assert len(redacted) == 20
    assert redacted.endswith("tail-marker")
    assert redacted.startswith("...")


# ---------------------------------------------------------------------------
# Classification (C3, C4, C5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("operation", "detail", "expected", "retryable"),
    [
        # priority 1: exception class
        ("prompt-target-state", {"exception_type": "TimeoutError"}, "prompt_target_state_connection_timeout", True),
        ("prompt-target-state", {"exception_type": "ConnectionFailure"}, "prompt_target_state_transient_connection", True),
        ("prompt-target-state", {"exception_type": "InsufficientPrivilege"}, "prompt_target_state_permission_denied", False),
        ("prompt-target-state", {"exception_type": "UndefinedTable"}, "prompt_target_state_schema_missing", False),
        ("prompt-target-state", {"exception_type": "InvalidSchemaName"}, "prompt_target_state_schema_missing", False),
        # 08007 on the read-only readback is just a lost connection
        ("prompt-target-state", {"exception_type": "TransactionResolutionUnknown", "sqlstate": "08007"}, "prompt_target_state_transient_connection", True),
        # priority 2: SQLSTATE behind a generic class
        ("prompt-target-state", {"exception_type": "OperationalError", "sqlstate": "08006", "message": "x"}, "prompt_target_state_transient_connection", True),
        ("prompt-target-state", {"exception_type": "OperationalError", "sqlstate": "57P03", "message": "x"}, "prompt_target_state_transient_connection", True),
        ("prompt-target-state", {"exception_type": "OperationalError", "sqlstate": "28P01", "message": "x"}, "prompt_target_state_permission_denied", False),
        ("prompt-target-state", {"exception_type": "ProgrammingError", "sqlstate": "3F000", "message": "x"}, "prompt_target_state_schema_missing", False),
        # priority 3: exact libpq phrases
        ("prompt-target-state", {"exception_type": "OperationalError", "message": "connection timeout expired"}, "prompt_target_state_connection_timeout", True),
        ("prompt-target-state", {"exception_type": "OperationalError", "message": "connection failed: Connection refused"}, "prompt_target_state_transient_connection", True),
        ("prompt-target-state", {"message": 'psycopg.errors.InvalidSchemaName: schema "x" does not exist'}, "prompt_target_state_schema_missing", False),
        ("prompt-target-state", {"exception_type": "OperationalError", "message": 'FATAL:  password authentication failed for user "deploy"'}, "prompt_target_state_permission_denied", False),
        ("prompt-target-state", {"exception_type": "OperationalError", "message": 'FATAL:  role "deploy" does not exist'}, "prompt_target_state_permission_denied", False),
        # fuzzy "timeout" is never enough
        ("prompt-target-state", {"exception_type": "OperationalError", "message": "statement timeout while reading"}, "prompt_target_state_unknown", False),
        ("prompt-target-state", {"exception_type": "RuntimeError", "message": "something odd"}, "prompt_target_state_unknown", False),
        ("prompt-target-state", {}, "prompt_target_state_unknown", False),
        # ValueError is a verdict, never a connection fault
        ("prompt-target-state", {"exception_type": "ValueError", "message": "PROMPT_RELEASE_TARGET_DSN is required"}, "prompt_target_state_unknown", False),
        ("prompt-sync", {"exception_type": "ValueError", "message": "prompt release content fingerprint mismatch: pr-1", "target_readback_status": "missing"}, "prompt_sync_validation_failed", False),
        ("prompt-sync", {"exception_type": "ValueError", "message": "connection refused", "target_readback_status": "missing"}, "prompt_sync_unknown", False),
        ("prompt-sync", {"exception_type": "InsufficientPrivilege", "target_readback_status": "missing"}, "prompt_sync_permission_denied", False),
        ("prompt-sync", {"exception_type": "UndefinedTable", "target_readback_status": "missing"}, "prompt_sync_schema_missing", False),
        ("prompt-sync", {"exception_type": "TransactionResolutionUnknown", "target_readback_status": "missing"}, "prompt_sync_write_result_unknown", False),
    ],
)
def test_classification_follows_exception_then_sqlstate_then_exact_phrases(
    operation: str, detail: dict[str, str], expected: str, retryable: bool
) -> None:
    verdict = classify_prompt_operation_failure(operation, **detail)
    assert verdict["classification"] == expected
    assert verdict["retryable"] is retryable


@pytest.mark.parametrize(
    ("readback", "expected", "retryable"),
    [
        ("missing", "prompt_sync_transient_connection", True),
        ("candidate", "prompt_sync_write_result_unknown", False),
        ("active", "prompt_sync_write_result_unknown", False),
        ("failed", "prompt_sync_write_result_unknown", False),
        ("unavailable", "prompt_sync_write_result_unknown", False),
        ("", "prompt_sync_write_result_unknown", False),
    ],
)
def test_sync_lost_connection_is_replayable_only_when_readback_proves_missing(
    readback: str, expected: str, retryable: bool
) -> None:
    verdict = classify_prompt_operation_failure(
        "prompt-sync",
        exception_type="OperationalError",
        sqlstate="08006",
        message="server closed the connection unexpectedly",
        target_readback_status=readback,
    )
    assert verdict == {"classification": expected, "kind": expected.removeprefix("prompt_sync_"), "retryable": retryable}


def test_classification_rejects_an_unknown_operation() -> None:
    with pytest.raises(ValueError, match="unknown Prompt operation"):
        classify_prompt_operation_failure("prompt-activate", exception_type="OperationalError")


# ---------------------------------------------------------------------------
# stderr parsing and the structured detail line of both CLIs
# ---------------------------------------------------------------------------


def test_parse_cli_failure_prefers_the_structured_detail_line() -> None:
    detail = parse_prompt_cli_failure(
        "automation ECS release pipeline failed: connection timeout expired\n"
        '{"error": "connection timeout expired", "exception_type": "OperationalError", "ok": false}\n'
    )
    assert detail == {"exception_type": "OperationalError", "sqlstate": "", "message": "connection timeout expired"}


def test_parse_cli_failure_falls_back_to_the_traceback_tail() -> None:
    detail = parse_prompt_cli_failure(
        "Traceback (most recent call last):\n  File x, line 1\npsycopg.OperationalError: connection refused\n"
    )
    assert detail == {"exception_type": "OperationalError", "sqlstate": "", "message": "connection refused"}
    assert parse_prompt_cli_failure("") == {"exception_type": "", "sqlstate": "", "message": ""}
    assert parse_prompt_cli_failure("just text\nmore text")["message"] == "just text\nmore text"


def test_prompt_cli_failure_detail_carries_class_and_sqlstate() -> None:
    error = psycopg.errors.lookup("08006")("server closed the connection unexpectedly")
    assert prompt_cli_failure_detail(error) == {
        "ok": False,
        "error": "server closed the connection unexpectedly",
        "exception_type": "ConnectionFailure",
        "sqlstate": "08006",
    }
    assert "sqlstate" not in prompt_cli_failure_detail(psycopg.OperationalError("connection timeout expired"))


def test_pipeline_main_reports_psycopg_errors_with_a_structured_line(capsys: pytest.CaptureFixture[str]) -> None:
    error = psycopg.errors.lookup("57P03")("the database system is starting up")
    with patch.object(pipeline, "run", side_effect=error):
        assert pipeline.main(["prompt-target-state", "--schema", "s", "--release-id", "pr-1"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    assert lines[0] == "automation ECS release pipeline failed: the database system is starting up"
    assert json.loads(lines[-1]) == {
        "error": "the database system is starting up",
        "exception_type": "CannotConnectNow",
        "ok": False,
        "sqlstate": "57P03",
    }
    assert parse_prompt_cli_failure(captured.err)["sqlstate"] == "57P03"


def test_prompt_release_main_reports_class_and_sqlstate(capsys: pytest.CaptureFixture[str]) -> None:
    error = psycopg.errors.lookup("42501")("permission denied for schema supportportal_preproduction")
    with patch.object(prompt_release, "_execute", side_effect=error):
        assert prompt_release.main(["sync", "--release-id", "pr-1", "--defer-activation"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err.strip()) == {
        "error": "permission denied for schema supportportal_preproduction",
        "exception_type": "InsufficientPrivilege",
        "ok": False,
        "sqlstate": "42501",
    }


def test_sync_target_repository_pins_an_explicit_connect_timeout() -> None:
    with patch.object(prompt_release, "PostgresTicketRepository") as constructor:
        prompt_release._create_sync_target_repository(TARGET_DSN, "supportportal_preproduction")
    kwargs = constructor.call_args.kwargs
    assert kwargs["connect_timeout"] == 10
    assert kwargs["dsn"] == TARGET_DSN
    assert kwargs["migration_dsn"] == TARGET_DSN
    assert kwargs["schema"] == "supportportal_preproduction"


# ---------------------------------------------------------------------------
# Artifact record (C1, C6)
# ---------------------------------------------------------------------------


def test_record_failure_writes_a_secret_free_artifact_and_append_only_history(tmp_path: Path) -> None:
    output = tmp_path / "prompt-sync-error.json"
    first = record_prompt_operation_failure(
        operation="prompt-sync",
        stderr_text=TRANSIENT_STDERR,
        exit_code=1,
        release_id="r20261020-test",
        prompt_release_id="pr-test",
        attempt=1,
        target_readback_status="missing",
        output=output,
        environment=ENVIRONMENT,
    )
    second = record_prompt_operation_failure(
        operation="prompt-sync",
        stderr_text=TRANSIENT_STDERR,
        exit_code=1,
        release_id="r20261020-test",
        prompt_release_id="pr-test",
        attempt=2,
        target_readback_status="candidate",
        output=output,
        environment=ENVIRONMENT,
    )
    assert first["classification"] == "prompt_sync_transient_connection" and first["retryable"] is True
    assert second["classification"] == "prompt_sync_write_result_unknown" and second["retryable"] is False
    stored = json.loads(output.read_text(encoding="utf-8"))
    assert stored == second
    assert stored["schema_version"] == PROMPT_OPERATION_ERROR_SCHEMA
    assert stored["attempts"] == 2 and stored["max_attempts"] == 3 and stored["exit_code"] == 1
    assert stored["exception_type"] == "OperationalError" and stored["sqlstate"] == "08006"
    assert stored["release_id"] == "r20261020-test" and stored["prompt_release_id"] == "pr-test"
    assert stored["outcome"] == "failed" and stored["generated_at"].endswith("Z")
    assert "server closed the connection unexpectedly" in stored["redacted_message"]
    _assert_secret_free_text(output.read_text(encoding="utf-8"), "artifact")
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    history = (tmp_path / "prompt-sync-error-attempts.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["attempts"] for line in history] == [1, 2]
    _assert_secret_free_text("\n".join(history), "attempt history")
    assert_evidence_secret_free(stored, "artifact")


def test_record_failure_refuses_an_artifact_that_still_carries_a_dsn(tmp_path: Path) -> None:
    output = tmp_path / "prompt-target-state-error.json"
    with patch.object(pipeline, "redact_sensitive_text", side_effect=lambda text, **_: text):
        with pytest.raises(ValueError, match="DSN is forbidden"):
            record_prompt_operation_failure(
                operation="prompt-target-state",
                stderr_text=TRANSIENT_STDERR,
                exit_code=1,
                release_id="r20261020-test",
                prompt_release_id="pr-test",
                attempt=1,
                output=output,
                environment=ENVIRONMENT,
            )
    assert not output.exists()
    assert not (tmp_path / "prompt-target-state-error-attempts.jsonl").exists()


def test_classify_cli_reads_the_stderr_capture_and_writes_the_record(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    stderr_file = tmp_path / "attempt-1.stderr"
    stderr_file.write_text(PERMISSION_STDERR + "\n", encoding="utf-8")
    output = tmp_path / "prompt-target-state-error.json"
    with patch.dict(os.environ, ENVIRONMENT):
        code = pipeline.main(
            [
                "classify-prompt-operation-failure",
                "--operation", "prompt-target-state",
                "--stderr-file", str(stderr_file),
                "--exit-code", "1",
                "--release-id", "r20261020-test",
                "--prompt-release-id", "pr-test",
                "--attempt", "1",
                "--output", str(output),
            ]
        )
    assert code == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["classification"] == "prompt_target_state_permission_denied"
    assert verdict["retryable"] is False
    assert json.loads(output.read_text(encoding="utf-8")) == verdict
    missing = pipeline.run(
        [
            "classify-prompt-operation-failure",
            "--operation", "prompt-sync",
            "--stderr-file", str(tmp_path / "does-not-exist"),
            "--exit-code", "137",
            "--release-id", "r",
            "--prompt-release-id", "pr",
            "--attempt", "1",
            "--target-readback-status", "missing",
            "--output", str(tmp_path / "prompt-sync-error.json"),
        ]
    )
    assert missing["classification"] == "prompt_sync_unknown" and missing["redacted_message"] == ""


# ---------------------------------------------------------------------------
# Real entry: deploy_automation_ecs_release.sh recovery wrapper and Prompt phase
# ---------------------------------------------------------------------------

SHELL_PRELUDE = textwrap.dedent(
    """
    source "$1"
    STATE_DIR="$2"; TEMP_DIR="$2"; PYTHON_BIN="$3"
    RELEASE_ID="r20261020-test"; PROMPT_RELEASE_ID="pr-test"
    PROMPT_TARGET_SCHEMA="supportportal_preproduction"; REGION="us-east-1"
    MANIFEST_PATH="${STATE_DIR}/missing-manifest.json"
    sleep() { printf '%s\\n' "$1" >>"${STATE_DIR}/sleeps"; }
    fake_prompt_cli() {
      local n=$(( $(cat "${STATE_DIR}/calls" 2>/dev/null || echo 0) + 1 ))
      printf '%s\\n' "${n}" >"${STATE_DIR}/calls"
      if (( n <= FAKE_FAIL_TIMES )); then printf '%s\\n' "${FAKE_STDERR}" >&2; return 1; fi
      printf '%s\\n' "${FAKE_STDOUT}"
    }
    """
)


def _run_deploy_shell(
    tmp_path: Path, body: str, *, env: dict[str, str] | None = None, python: str | None = None
) -> tuple[subprocess.CompletedProcess[str], Path]:
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    result = subprocess.run(
        ["bash", "-c", SHELL_PRELUDE + textwrap.dedent(body), "bash", str(DEPLOY_SCRIPT), str(state_dir), python or sys.executable],
        env={
            **os.environ,
            **ENVIRONMENT,
            "FAKE_FAIL_TIMES": "0",
            "FAKE_STDERR": "",
            "FAKE_STDOUT": json.dumps({"release_id": "pr-test", "status": "candidate"}),
            **(env or {}),
        },
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, state_dir


def _calls(state_dir: Path) -> int:
    return int((state_dir / "calls").read_text(encoding="utf-8").strip())


def _sleeps(state_dir: Path) -> list[str]:
    path = state_dir / "sleeps"
    return path.read_text(encoding="utf-8").split() if path.exists() else []


def _assert_state_dir_secret_free(state_dir: Path) -> None:
    for path in state_dir.rglob("*"):
        if path.is_file():
            _assert_secret_free_text(path.read_text(encoding="utf-8", errors="replace"), str(path))


def test_shell_target_state_recovers_after_transient_failures(tmp_path: Path) -> None:
    result, state_dir = _run_deploy_shell(
        tmp_path,
        """
        run_prompt_operation_with_recovery prompt-target-state "${STATE_DIR}/prompt-current.json" fake_prompt_cli
        echo "RC=$? STATUS=${PROMPT_TARGET_STATE_STATUS} ATTEMPTS=${PROMPT_TARGET_STATE_ATTEMPTS} CLS=${PROMPT_TARGET_STATE_CLASSIFICATION} RETRY=${PROMPT_TARGET_STATE_RETRYABLE}"
        """,
        env={"FAKE_FAIL_TIMES": "2", "FAKE_STDERR": TRANSIENT_STDERR},
    )
    assert result.returncode == 0, result.stderr
    assert "RC=0 STATUS=passed ATTEMPTS=3 CLS=prompt_target_state_transient_connection RETRY=true" in result.stdout
    assert _calls(state_dir) == 3
    assert _sleeps(state_dir) == ["1", "3"]
    assert json.loads((state_dir / "prompt-current.json").read_text(encoding="utf-8")) == {
        "release_id": "pr-test",
        "status": "candidate",
    }
    artifact = json.loads((state_dir / "prompt-target-state-error.json").read_text(encoding="utf-8"))
    assert artifact["outcome"] == "recovered"
    assert artifact["attempts"] == 3
    assert artifact["classification"] == "prompt_target_state_transient_connection"
    assert artifact["retryable"] is True
    assert "server closed the connection unexpectedly" in artifact["redacted_message"]
    assert result.stdout.count("attempt 1/3 failed") == 1 and result.stdout.count("attempt 2/3 failed") == 1
    _assert_state_dir_secret_free(state_dir)
    _assert_secret_free_text(result.stdout + result.stderr, "shell output")


def test_shell_target_state_stops_after_three_transient_failures_and_reports_evidence(tmp_path: Path) -> None:
    result, state_dir = _run_deploy_shell(
        tmp_path,
        """
        rc=0; run_prompt_operation_with_recovery prompt-target-state "${STATE_DIR}/prompt-current.json" fake_prompt_cli || rc=$?
        echo "RC=${rc} STATUS=${PROMPT_TARGET_STATE_STATUS} ATTEMPTS=${PROMPT_TARGET_STATE_ATTEMPTS} CLS=${PROMPT_TARGET_STATE_CLASSIFICATION} RETRY=${PROMPT_TARGET_STATE_RETRYABLE}"
        printf '%s' "${PROMPT_SCRATCH_DIR}" >"${STATE_DIR}/scratch-path"
        set +o pipefail
        echo "RAW_WITH_SECRET=$(grep -l Sup3rS3cret "${PROMPT_SCRATCH_DIR}"/*.stderr | wc -l | tr -d ' ')"
        set -o pipefail
        write_evidence failed_before_activation
        cleanup_schema_bootstrap() { :; }; rollback_services() { :; }; write_evidence() { :; }
        true; cleanup
        """,
        env={"FAKE_FAIL_TIMES": "9", "FAKE_STDERR": TIMEOUT_STDERR},
    )
    assert result.returncode == 0, result.stderr
    assert "RC=1 STATUS=failed ATTEMPTS=3 CLS=prompt_target_state_connection_timeout RETRY=true" in result.stdout
    assert _calls(state_dir) == 3
    assert _sleeps(state_dir) == ["1", "3"]
    assert not (state_dir / "prompt-current.json").exists()
    assert not (state_dir / "prompt-current.json.tmp").exists()
    artifact = json.loads((state_dir / "prompt-target-state-error.json").read_text(encoding="utf-8"))
    assert artifact["outcome"] == "failed" and artifact["attempts"] == 3 and artifact["max_attempts"] == 3
    history = (state_dir / "prompt-target-state-error-attempts.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["attempts"] for line in history] == [1, 2, 3]
    # The raw stderr with the DSN only ever existed in the scratch directory,
    # which cleanup removed; nothing under the checkpoint carries it.
    assert "RAW_WITH_SECRET=3" in result.stdout
    scratch = Path((state_dir / "scratch-path").read_text(encoding="utf-8"))
    assert not scratch.exists()
    assert not str(scratch).startswith(str(state_dir))
    evidence = json.loads((state_dir / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "failed_before_activation"
    checks = evidence["checks"]
    assert checks["prompt_target_state"] == "failed"
    assert checks["prompt_target_state_classification"] == "prompt_target_state_connection_timeout"
    assert checks["prompt_target_state_attempts"] == 3
    assert checks["prompt_target_state_retryable"] is True
    assert checks["prompt_sync"] == "not_started"
    assert checks["prompt_sync_attempts"] == 0 and checks["prompt_sync_retryable"] is False
    assert_evidence_secret_free(evidence)
    _assert_state_dir_secret_free(state_dir)
    _assert_secret_free_text(result.stdout + result.stderr, "shell output")


@pytest.mark.parametrize(
    ("stderr", "classification"),
    [
        (PERMISSION_STDERR, "prompt_target_state_permission_denied"),
        (SCHEMA_MISSING_STDERR, "prompt_target_state_schema_missing"),
        (_stderr("prompt release not found: pr-test", "ValueError"), "prompt_target_state_unknown"),
    ],
)
def test_shell_non_transient_failures_run_exactly_once(tmp_path: Path, stderr: str, classification: str) -> None:
    result, state_dir = _run_deploy_shell(
        tmp_path,
        """
        rc=0; run_prompt_operation_with_recovery prompt-target-state "${STATE_DIR}/prompt-current.json" fake_prompt_cli || rc=$?
        echo "RC=${rc} STATUS=${PROMPT_TARGET_STATE_STATUS} ATTEMPTS=${PROMPT_TARGET_STATE_ATTEMPTS} CLS=${PROMPT_TARGET_STATE_CLASSIFICATION} RETRY=${PROMPT_TARGET_STATE_RETRYABLE}"
        """,
        env={"FAKE_FAIL_TIMES": "9", "FAKE_STDERR": stderr},
    )
    assert result.returncode == 0, result.stderr
    assert f"RC=1 STATUS=failed ATTEMPTS=1 CLS={classification} RETRY=false" in result.stdout
    assert _calls(state_dir) == 1
    assert _sleeps(state_dir) == []
    artifact = json.loads((state_dir / "prompt-target-state-error.json").read_text(encoding="utf-8"))
    assert artifact["classification"] == classification and artifact["retryable"] is False
    _assert_state_dir_secret_free(state_dir)


@pytest.mark.parametrize("readback", ["candidate", "active", "unavailable"])
def test_shell_sync_lost_connection_is_fail_closed_unless_the_release_row_is_missing(
    tmp_path: Path, readback: str
) -> None:
    result, state_dir = _run_deploy_shell(
        tmp_path,
        f"""
        prompt_sync_target_readback_status() {{ printf '%s\\n' "{readback}"; }}
        rc=0; run_prompt_operation_with_recovery prompt-sync "${{STATE_DIR}}/prompt-sync.json" fake_prompt_cli || rc=$?
        echo "RC=${{rc}} SYNC=${{PROMPT_SYNC_STATUS}} ATTEMPTS=${{PROMPT_SYNC_ATTEMPTS}} CLS=${{PROMPT_SYNC_CLASSIFICATION}} RETRY=${{PROMPT_SYNC_RETRYABLE}}"
        write_evidence failed_before_activation
        """,
        env={"FAKE_FAIL_TIMES": "9", "FAKE_STDERR": TRANSIENT_STDERR},
    )
    assert result.returncode == 0, result.stderr
    assert "RC=1 SYNC=failed ATTEMPTS=1 CLS=prompt_sync_write_result_unknown RETRY=false" in result.stdout
    assert _calls(state_dir) == 1
    assert _sleeps(state_dir) == []
    artifact = json.loads((state_dir / "prompt-sync-error.json").read_text(encoding="utf-8"))
    assert artifact["target_readback_status"] == readback
    assert artifact["classification"] == "prompt_sync_write_result_unknown"
    assert not (state_dir / "prompt-sync.json").exists()
    checks = json.loads((state_dir / "evidence.json").read_text(encoding="utf-8"))["checks"]
    assert checks["prompt_sync"] == "failed"
    assert checks["prompt_sync_classification"] == "prompt_sync_write_result_unknown"
    assert checks["prompt_sync_attempts"] == 1 and checks["prompt_sync_retryable"] is False
    _assert_state_dir_secret_free(state_dir)


def test_shell_sync_replays_only_while_readback_proves_missing_and_stops_at_three(tmp_path: Path) -> None:
    result, state_dir = _run_deploy_shell(
        tmp_path,
        """
        prompt_sync_target_readback_status() { printf 'missing\\n'; }
        rc=0; run_prompt_operation_with_recovery prompt-sync "${STATE_DIR}/prompt-sync.json" fake_prompt_cli || rc=$?
        echo "RC=${rc} SYNC=${PROMPT_SYNC_STATUS} ATTEMPTS=${PROMPT_SYNC_ATTEMPTS} CLS=${PROMPT_SYNC_CLASSIFICATION} RETRY=${PROMPT_SYNC_RETRYABLE}"
        """,
        env={"FAKE_FAIL_TIMES": "9", "FAKE_STDERR": TRANSIENT_STDERR},
    )
    assert result.returncode == 0, result.stderr
    assert "RC=1 SYNC=failed ATTEMPTS=3 CLS=prompt_sync_transient_connection RETRY=true" in result.stdout
    assert _calls(state_dir) == 3
    assert _sleeps(state_dir) == ["1", "3"]
    _assert_state_dir_secret_free(state_dir)


def test_shell_sync_recovers_when_the_release_row_is_still_missing(tmp_path: Path) -> None:
    sync_result = {"ok": True, "sync": {"status": "candidate"}, "identity": {"build_ref": "b1", "content_fingerprint": "sha256:abc"}}
    result, state_dir = _run_deploy_shell(
        tmp_path,
        """
        prompt_sync_target_readback_status() { printf 'missing\\n'; }
        run_prompt_operation_with_recovery prompt-sync "${STATE_DIR}/prompt-sync.json" fake_prompt_cli
        echo "RC=$? SYNC=${PROMPT_SYNC_STATUS} ATTEMPTS=${PROMPT_SYNC_ATTEMPTS} CLS=${PROMPT_SYNC_CLASSIFICATION}"
        """,
        env={"FAKE_FAIL_TIMES": "1", "FAKE_STDERR": TRANSIENT_STDERR, "FAKE_STDOUT": json.dumps(sync_result)},
    )
    assert result.returncode == 0, result.stderr
    # The deployable status is read from the sync result by the phase, not by the wrapper.
    assert "RC=0 SYNC=not_started ATTEMPTS=2 CLS=prompt_sync_transient_connection" in result.stdout
    assert _calls(state_dir) == 2
    assert _sleeps(state_dir) == ["1"]
    assert json.loads((state_dir / "prompt-sync.json").read_text(encoding="utf-8")) == sync_result
    artifact = json.loads((state_dir / "prompt-sync-error.json").read_text(encoding="utf-8"))
    assert artifact["outcome"] == "recovered" and artifact["attempts"] == 2
    _assert_state_dir_secret_free(state_dir)


def test_shell_classifier_failure_records_a_non_retryable_unknown_without_a_message(tmp_path: Path) -> None:
    result, state_dir = _run_deploy_shell(
        tmp_path,
        """
        PYTHON_BIN=/usr/bin/false
        rc=0; run_prompt_operation_with_recovery prompt-target-state "${STATE_DIR}/prompt-current.json" fake_prompt_cli || rc=$?
        echo "RC=${rc} STATUS=${PROMPT_TARGET_STATE_STATUS} ATTEMPTS=${PROMPT_TARGET_STATE_ATTEMPTS} CLS=${PROMPT_TARGET_STATE_CLASSIFICATION} RETRY=${PROMPT_TARGET_STATE_RETRYABLE}"
        """,
        env={"FAKE_FAIL_TIMES": "9", "FAKE_STDERR": TRANSIENT_STDERR},
    )
    assert result.returncode == 0, result.stderr
    assert "RC=1 STATUS=failed ATTEMPTS=1 CLS=prompt_target_state_unknown RETRY=false" in result.stdout
    assert _calls(state_dir) == 1
    artifact = json.loads((state_dir / "prompt-target-state-error.json").read_text(encoding="utf-8"))
    assert artifact["classification"] == "prompt_target_state_unknown"
    assert artifact["retryable"] is False
    assert artifact["redacted_message"] == "classification unavailable"
    assert artifact["schema_version"] == PROMPT_OPERATION_ERROR_SCHEMA
    _assert_state_dir_secret_free(state_dir)


def _fake_python(tmp_path: Path) -> Path:
    """A PYTHON_BIN that answers prompt-target-state itself and delegates everything else."""
    script = tmp_path / "fake-python"
    script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            if [[ "${1:-}" == "-m" && "${2:-}" == "backend.scripts.automation_ecs_release_pipeline" && "${3:-}" == "prompt-target-state" ]]; then
              n=$(( $(cat "${FAKE_TS_COUNTER}" 2>/dev/null || echo 0) + 1 ))
              printf '%s\\n' "${n}" >"${FAKE_TS_COUNTER}"
              if (( n <= ${FAKE_TS_FAIL_TIMES:-0} )); then printf '%s\\n' "${FAKE_TS_STDERR}" >&2; exit 1; fi
              printf '%s\\n' "${FAKE_TS_STDOUT}"
              exit 0
            fi
            exec "${REAL_PYTHON}" "$@"
            """
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


PHASE_BODY = """
    RESUME=1
    printf '%s' '{"identity":{"build_ref":"b1","content_fingerprint":"sha256:abc"}}' >"${STATE_DIR}/source-prompt.json"
    run_schema_bootstrap() { echo bootstrap >>"${STATE_DIR}/phase-calls"; }
    verify_aws_mutation_ready() { echo mutation_ready >>"${STATE_DIR}/phase-calls"; }
    run_release_prompt_cli() {
      echo "cli $*" >>"${STATE_DIR}/phase-calls"
      if [[ "$1" == "sync" ]]; then printf '%s\\n' "${FAKE_SYNC_STDOUT}"; else printf '%s\\n' '{"ok": true, "validation": {}}'; fi
    }
    run_prompt_release_phase
    echo "SYNC=${PROMPT_SYNC_STATUS} ACTIVATION=${PROMPT_ACTIVATION_STATUS} TARGET_STATE=${PROMPT_TARGET_STATE_STATUS} TS_ATTEMPTS=${PROMPT_TARGET_STATE_ATTEMPTS}"
    """


def test_shell_resume_with_an_active_target_skips_sync_and_activation(tmp_path: Path) -> None:
    result, state_dir = _run_deploy_shell(
        tmp_path,
        PHASE_BODY,
        env={
            "REAL_PYTHON": sys.executable,
            "FAKE_TS_COUNTER": str(tmp_path / "ts-calls"),
            "FAKE_TS_STDOUT": json.dumps({"release_id": "pr-test", "status": "active"}),
            "FAKE_SYNC_STDOUT": "",
        },
        python=str(_fake_python(tmp_path)),
    )
    assert result.returncode == 0, result.stderr
    assert "SYNC=active ACTIVATION=active TARGET_STATE=passed TS_ATTEMPTS=1" in result.stdout
    assert "sync and activation will not be repeated" in result.stdout
    calls = (state_dir / "phase-calls").read_text(encoding="utf-8").splitlines()
    assert calls == ["bootstrap", "cli validate --release-id pr-test"]
    assert (tmp_path / "ts-calls").read_text(encoding="utf-8").strip() == "1"
    assert json.loads((state_dir / "prompt-current.json").read_text(encoding="utf-8"))["status"] == "active"
    assert json.loads((state_dir / "prompt-sync.json").read_text(encoding="utf-8")) == {
        "sync": {"status": "active"},
        "identity": {"build_ref": "b1", "content_fingerprint": "sha256:abc"},
    }
    assert not (state_dir / "prompt-target-state-error.json").exists()
    phases = [json.loads(line) for line in (state_dir / "phase-attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(phase["phase"], phase["status"]) for phase in phases] == [("prompt_schema", "passed")]


def test_shell_resume_recovers_the_state_readback_then_syncs_a_candidate(tmp_path: Path) -> None:
    sync_result = {"ok": True, "sync": {"status": "candidate"}, "identity": {"build_ref": "b1", "content_fingerprint": "sha256:abc"}}
    result, state_dir = _run_deploy_shell(
        tmp_path,
        PHASE_BODY,
        env={
            "REAL_PYTHON": sys.executable,
            "FAKE_TS_COUNTER": str(tmp_path / "ts-calls"),
            "FAKE_TS_FAIL_TIMES": "1",
            "FAKE_TS_STDERR": TIMEOUT_STDERR,
            "FAKE_TS_STDOUT": json.dumps({"release_id": "pr-test", "status": "candidate"}),
            "FAKE_SYNC_STDOUT": json.dumps(sync_result),
        },
        python=str(_fake_python(tmp_path)),
    )
    assert result.returncode == 0, result.stderr
    assert "SYNC=candidate ACTIVATION=not_started TARGET_STATE=passed TS_ATTEMPTS=2" in result.stdout
    assert "prompt-target-state attempt 1/3 failed: prompt_target_state_connection_timeout" in result.stdout
    calls = (state_dir / "phase-calls").read_text(encoding="utf-8").splitlines()
    assert calls == [
        "bootstrap",
        "mutation_ready",
        "cli sync --release-id pr-test --defer-activation",
        "cli validate --release-id pr-test",
    ]
    assert (tmp_path / "ts-calls").read_text(encoding="utf-8").strip() == "2"
    assert _sleeps(state_dir) == ["1"]
    assert json.loads((state_dir / "prompt-sync.json").read_text(encoding="utf-8")) == sync_result
    artifact = json.loads((state_dir / "prompt-target-state-error.json").read_text(encoding="utf-8"))
    assert artifact["outcome"] == "recovered" and artifact["attempts"] == 2
    assert artifact["classification"] == "prompt_target_state_connection_timeout"
    _assert_state_dir_secret_free(state_dir)
    _assert_secret_free_text(result.stdout + result.stderr, "shell output")


def test_shell_resume_stops_when_the_state_readback_is_denied(tmp_path: Path) -> None:
    result, state_dir = _run_deploy_shell(
        tmp_path,
        PHASE_BODY,
        env={
            "REAL_PYTHON": sys.executable,
            "FAKE_TS_COUNTER": str(tmp_path / "ts-calls"),
            "FAKE_TS_FAIL_TIMES": "9",
            "FAKE_TS_STDERR": PERMISSION_STDERR,
            "FAKE_TS_STDOUT": "",
            "FAKE_SYNC_STDOUT": "",
        },
        python=str(_fake_python(tmp_path)),
    )
    assert result.returncode != 0
    assert "Target Prompt Release state readback failed (prompt_target_state_permission_denied)" in result.stderr
    assert (tmp_path / "ts-calls").read_text(encoding="utf-8").strip() == "1"
    assert not (state_dir / "phase-calls").exists()
    assert not (state_dir / "prompt-sync.json").exists()
    _assert_state_dir_secret_free(state_dir)


def test_deploy_script_no_longer_discards_prompt_target_stderr() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert '>"${TEMP_DIR}/prompt-current.json" 2>/dev/null' not in script
    assert '>"${TEMP_DIR}/prompt-sync.json" 2>/dev/null' not in script
    assert "run_prompt_operation_with_recovery prompt-target-state" in script
    assert "run_prompt_operation_with_recovery prompt-sync" in script
    assert "PROMPT_OPERATION_MAX_ATTEMPTS=3" in script
    assert "PROMPT_OPERATION_RETRY_BACKOFF_SECONDS=(1 3)" in script
    cleanup = script[script.index("cleanup() {") : script.index("run_terraform_zero_plan() {")]
    assert 'rm -rf -- "${PROMPT_SCRATCH_DIR}"' in cleanup
    # The heartbeat guard from PR #1054 stays intact: the redacted field is named differently.
    assert "last_error" not in script
    assert "redacted_message" in script
