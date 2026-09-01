from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

from backend.services.enablement_archer_executor import (
    ARCHER_SKILL_SCRIPT,
    ArcherEnablementResult,
    execute_enablement_archer,
)


APP_ID = "0123456789abcdef0123456789abcdef"
MOCK_PILOT = Path(__file__).resolve().parent / "fixtures" / "archer_mock_pilot.py"


def _stub(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "stub.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return script


@pytest.mark.parametrize(
    ("exit_code", "first_line", "outcome"),
    [
        (0, "开启结果：成功", "enabled"),
        (2, "关键词必须为整数或 32 位字符串", "appid_invalid"),
        (3, "查无项目", "project_not_found"),
        (1, "开启结果：失败", "enable_failed"),
    ],
)
def test_maps_only_the_four_frozen_outcomes(
    tmp_path: Path, exit_code: int, first_line: str, outcome: str
) -> None:
    script = _stub(
        tmp_path,
        f"""
        import sys
        print({first_line!r})
        raise SystemExit({exit_code})
        """,
    )
    assert execute_enablement_archer(APP_ID, script_path=script).outcome == outcome


@pytest.mark.parametrize(
    ("exit_code", "first_line"),
    [(0, "查无项目"), (2, "开启结果：成功"), (3, "开启结果：失败")],
)
def test_exit_code_and_first_line_must_both_match(
    tmp_path: Path, exit_code: int, first_line: str
) -> None:
    script = _stub(
        tmp_path,
        f"""
        import sys
        print({first_line!r})
        raise SystemExit({exit_code})
        """,
    )
    assert execute_enablement_archer(APP_ID, script_path=script).outcome == "enable_failed"


def test_success_marker_must_be_the_literal_first_line(tmp_path: Path) -> None:
    script = _stub(
        tmp_path,
        """
        print()
        print("开启结果：成功")
        """,
    )
    assert execute_enablement_archer(APP_ID, script_path=script).outcome == "enable_failed"


def test_missing_script_is_enable_failed(tmp_path: Path) -> None:
    result = execute_enablement_archer(APP_ID, script_path=tmp_path / "missing.py")
    assert result.outcome == "enable_failed"
    assert result.detail


def test_environment_explicitly_supplies_pilot_and_config_home(tmp_path: Path) -> None:
    script = _stub(
        tmp_path,
        """
        import os
        assert os.environ["PILOT_BIN"] == "/custom/pilot"
        assert os.environ["XDG_CONFIG_HOME"] == "/custom/config"
        print("开启结果：成功")
        """,
    )
    result = execute_enablement_archer(
        APP_ID,
        script_path=script,
        pilot_bin="/custom/pilot",
        xdg_config_home="/custom/config",
    )
    assert result.outcome == "enabled"


def test_detail_removes_app_ids_secrets_controls_and_is_bounded(tmp_path: Path) -> None:
    script = _stub(
        tmp_path,
        f"""
        import sys
        print("开启结果：失败")
        print('AppID：{APP_ID} token=top-secret cookie:session-value\\x01 ' +
              '\"authorization\": \"Bearer bearer-secret\" password=two word secret;' + "x" * 1000)
        raise SystemExit(1)
        """,
    )
    result = execute_enablement_archer(APP_ID, script_path=script)
    assert result.outcome == "enable_failed"
    assert APP_ID not in result.detail
    assert "top-secret" not in result.detail
    assert "session-value" not in result.detail
    assert "bearer-secret" not in result.detail
    assert "two word secret" not in result.detail
    assert "\x01" not in result.detail
    assert len(result.detail) <= 500


def test_timeout_terminates_skill_and_pilot_child_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("CHILD_PID_FILE", str(pid_file))
    script = _stub(
        tmp_path,
        """
        import os
        import subprocess
        import sys
        import time

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        with open(os.environ["CHILD_PID_FILE"], "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
        time.sleep(60)
        """,
    )
    result = execute_enablement_archer(APP_ID, script_path=script, timeout_seconds=0.2)
    assert result == ArcherEnablementResult("enable_failed", "Archer Skill timed out")
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    for _ in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(child_pid, signal.SIGKILL)
        pytest.fail("Pilot child process survived the executor timeout")


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
