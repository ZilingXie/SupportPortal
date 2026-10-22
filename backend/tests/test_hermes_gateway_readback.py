import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "deployment/hermes-case-files/gateway_readback.py"


def load():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gateway_readback_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Result:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def runner(result):
    def run(*args, **kwargs):
        return result
    return run


def test_split_health_and_toolsets_success_writes_two_markers(tmp_path):
    mod = load()
    outputs = {
        "health": Result('GATEWAY_HEALTH_JSON={"status":"ok"}\n'),
        "toolsets": Result('GATEWAY_TOOLSETS_JSON=[{"name":"skills","enabled":true,"configured":true},{"name":"supportportal_files","enabled":true,"configured":true}]\n'),
    }
    seen = []
    def run(command, **kwargs):
        seen.append(command)
        return outputs["health" if "health" in command[-1] else "toolsets"]
    result = mod.run_split_readbacks({"health": ["health"], "toolsets": ["toolsets"]}, tmp_path, run)
    assert result["health"]["status"] == "ok"
    assert len(result["toolsets"]) == 2
    assert {p.name for p in tmp_path.iterdir()} == {"health.json", "toolsets.json"}
    assert len(seen) == 2


@pytest.mark.parametrize("fault", [
    ("toolsets", 'GATEWAY_HEALTH_JSON={"status":"ok"}\n', 'GATEWAY_TOOLSETS_JSON={"name":', 0, "marker_invalid_json"),
    ("toolsets", 'GATEWAY_HEALTH_JSON={"status":"ok"}\n', "", 0, "marker_missing"),
    ("toolsets", 'GATEWAY_HEALTH_JSON={"status":"ok"}\n', 'GATEWAY_TOOLSETS_JSON={}\nGATEWAY_TOOLSETS_JSON={}\n', 0, "marker_duplicate"),
    ("toolsets", 'GATEWAY_HEALTH_JSON={"status":"ok"}\n', 'EOF\n', 0, "eof"),
    ("toolsets", 'GATEWAY_HEALTH_JSON={"status":"ok"}\n', 'failed\n', 7, "command_nonzero"),
])
def test_toolsets_faults_fail_closed_and_health_artifact_remains(tmp_path, fault):
    mod = load()
    endpoint, health, toolsets, returncode, expected = fault
    calls = iter([Result(health), Result(toolsets, returncode=returncode)])
    with pytest.raises(mod.ReadbackError, match="toolsets:" + expected):
        mod.run_split_readbacks({"health": ["health"], "toolsets": ["toolsets"]}, tmp_path,
                                lambda *a, **k: next(calls))
    assert json.loads((tmp_path / "health.json").read_text())["status"] == "passed"
    failed = json.loads((tmp_path / "toolsets.json").read_text())
    assert failed["status"] == "failed" and failed["error"] == expected


def test_health_fault_fails_before_toolsets_but_records_health(tmp_path):
    mod = load()
    calls = iter([Result('GATEWAY_HEALTH_JSON={"status":"ok"}\nEOF\n'),
                  Result('GATEWAY_TOOLSETS_JSON=[]\n')])
    with pytest.raises(mod.ReadbackError, match="health:eof"):
        mod.run_split_readbacks({"health": ["health"], "toolsets": ["toolsets"]}, tmp_path,
                                lambda *a, **k: next(calls))
    assert json.loads((tmp_path / "health.json").read_text())["error"] == "eof"
    assert json.loads((tmp_path / "toolsets.json").read_text())["status"] == "passed"


def test_timeout_is_recorded_as_failed(tmp_path):
    mod = load()
    def timeout(*args, **kwargs):
        raise __import__("subprocess").TimeoutExpired(args[0], kwargs["timeout"], output="partial")
    with pytest.raises(mod.ReadbackError, match="health:timeout"):
        mod.run_split_readbacks({"health": ["health"], "toolsets": ["toolsets"]}, tmp_path, timeout)
    value = json.loads((tmp_path / "health.json").read_text())
    assert value["timed_out"] is True and value["status"] == "failed"
