from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from backend.services.archer_direct_client import ArcherDirectError
from backend.services.enablement_archer_executor import (
    ARCHER_SKILL_SCRIPT,
    ArcherEnablementResult,
    execute_enablement_archer,
)


APP_ID = "0123456789abcdef0123456789abcdef"
MOCK_PILOT = Path(__file__).resolve().parent / "fixtures" / "archer_mock_pilot.py"


def _project() -> dict[str, Any]:
    # live check-simple-vendor shape (probed 2026-09-02)
    return {
        "vendorId": 1322905,
        "projectName": "Mock Project",
        "appid": APP_ID,
        "companyId": 1138100,
        "projectId": "eqpDzxHNn",
        "projectType": "PAAS",
    }


def _config(status: int, region: int, load: int) -> dict[str, Any]:
    # live uap-app/6/uap elements shape
    return {
        "id": 200179442,
        "typeId": 6,
        "vendorId": 1322905,
        "appKey": APP_ID,
        "companyId": 1138100,
        "projectName": "Mock Project",
        "maxSubscribeLoad": load,
        "status": status,
        "region": region,
        "projectId": "eqpDzxHNn",
    }


class FakeArcherClient:
    """Drives the real vendored skill with live-shaped Archer responses."""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.calls: list[tuple[str, str]] = []
        initial = {
            "create": None,
            "not_found": None,
            "update": _config(0, 1, 10),
            "already_enabled": _config(1, 2, 50),
            "readback_mismatch": _config(0, 1, 10),
            "write_400": _config(0, 1, 10),
        }
        self._config: dict[str, Any] | None = initial[scenario]

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, path))
        if "check-simple-vendor" in path:
            if self.scenario == "not_found":
                # live shape: HTTP 400 "项目不存在" -> client-level translation
                return {"data": None, "message": "项目不存在"}
            return _project()
        if "search-project" in path:
            return [_project()]
        if "/uap-app/6/uap" in path and method == "GET":
            # DirectArcherClient already unwrapped the live `elements` envelope
            elements = [] if self._config is None else [dict(self._config)]
            return elements
        if path.endswith("/uap-type/6") and method in {"POST", "PUT"}:
            if self.scenario == "write_400":
                raise ArcherDirectError("archer request failed with HTTP 400")
            if self.scenario != "readback_mismatch":
                if method == "POST":
                    assert body is not None
                    self._config = dict(body)
                    self._config.setdefault("appKey", APP_ID)
                else:
                    assert body is not None
                    self._config = dict(self._config or {"appKey": APP_ID})
                    self._config.update(body)
            return {"data": {"success": True}}
        raise AssertionError(f"unexpected archer call: {method} {path}")


def test_create_missing_config_enables_and_succeeds() -> None:
    client = FakeArcherClient("create")
    result = execute_enablement_archer(APP_ID, client=client)
    assert result.outcome == "enabled"
    assert result.detail.startswith("开启结果：成功")
    assert "操作：创建" in result.detail
    # exact call sequence: check -> search -> uap GET -> POST -> readback GET
    methods = [method for method, _ in client.calls]
    assert methods == ["GET", "GET", "GET", "POST", "GET"]


def test_existing_matching_config_is_idempotent_success() -> None:
    client = FakeArcherClient("already_enabled")
    result = execute_enablement_archer(APP_ID, client=client)
    assert result.outcome == "enabled"
    assert "操作：无需更新" in result.detail
    assert [method for method, _ in client.calls] == ["GET", "GET", "GET"]


def test_mismatched_config_is_updated_then_verified() -> None:
    client = FakeArcherClient("update")
    result = execute_enablement_archer(APP_ID, client=client)
    assert result.outcome == "enabled"
    assert "操作：更新" in result.detail
    assert [method for method, _ in client.calls] == ["GET", "GET", "GET", "PUT", "GET"]


def test_project_not_found_maps_to_recovery_outcome() -> None:
    client = FakeArcherClient("not_found")
    result = execute_enablement_archer(APP_ID, client=client)
    assert result == ArcherEnablementResult("project_not_found", "查无项目")
    assert [method for method, _ in client.calls] == ["GET"]


def test_readback_mismatch_is_enable_failed() -> None:
    # POST "succeeds" but the readback still mismatches -> skill raises
    client = FakeArcherClient("readback_mismatch")
    result = execute_enablement_archer(APP_ID, client=client)
    assert result.outcome == "enable_failed"
    assert "读回不一致" in result.detail


def test_write_rejection_is_enable_failed() -> None:
    client = FakeArcherClient("write_400")
    result = execute_enablement_archer(APP_ID, client=client)
    assert result.outcome == "enable_failed"
    assert "HTTP 400" in result.detail


@pytest.mark.parametrize(
    "app_id", ["", "  ", "not-an-app-id", "0123456789abcdef", "0" * 33, "g" * 32]
)
def test_non_32_hex_app_id_is_rejected_without_any_network_call(app_id: str) -> None:
    client = FakeArcherClient("create")
    result = execute_enablement_archer(app_id, client=client)
    assert result.outcome == "appid_invalid"
    assert client.calls == []


def test_success_detail_redacts_the_app_id() -> None:
    client = FakeArcherClient("already_enabled")
    result = execute_enablement_archer(APP_ID, client=client)
    assert result.outcome == "enabled"
    assert APP_ID not in result.detail
    assert "[REDACTED_APP_ID]" in result.detail


def test_skill_error_detail_is_sanitized_and_bounded() -> None:
    class LeakyClient:
        def call(self, method: str, path: str, body: Any = None) -> Any:
            raise RuntimeError(f"token=super-secret cookie={APP_ID} " + "x" * 900)

    result = execute_enablement_archer(APP_ID, client=LeakyClient())
    assert result.outcome == "enable_failed"
    assert "super-secret" not in result.detail
    assert APP_ID not in result.detail
    assert len(result.detail) <= 500


def test_invalid_app_id_short_circuits_before_default_client() -> None:
    # no ARCHER_OAUTH_COOKIE in the test environment: a valid-shaped App ID
    # with the default client would raise credential errors, but an invalid
    # App ID never reaches any transport at all
    result = execute_enablement_archer("zzz")
    assert result.outcome == "appid_invalid"


@pytest.mark.parametrize(
    ("app_id", "scenario", "exit_code", "first_line"),
    [
        (APP_ID, "create", 0, "开启结果：成功"),
        ("not-an-app-id", "create", 2, "关键词必须为整数或 32 位字符串"),
        (APP_ID, "not_found", 3, "查无项目"),
        (APP_ID, "readback_mismatch", 1, "开启结果：失败"),
    ],
)
def test_vendored_skill_four_result_stub_smoke(
    app_id: str, scenario: str, exit_code: int, first_line: str
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        environment = os.environ.copy()
        environment.update(
            {
                "PILOT_BIN": f"{sys.executable} {MOCK_PILOT}",
                "MOCK_SCENARIO": scenario,
                "MOCK_STATE": str(root / "state.json"),
                "MOCK_LOG": str(root / "calls.jsonl"),
            }
        )
        completed = subprocess.run(
            [sys.executable, str(ARCHER_SKILL_SCRIPT), app_id],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    assert completed.returncode == exit_code
    assert completed.stdout.splitlines()[0] == first_line
