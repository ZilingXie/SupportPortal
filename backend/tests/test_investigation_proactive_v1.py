from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

from backend.services.investigation_runtime_policy import investigation_policy, public_investigation
from backend.services.automation_hermes_agent import toolsets_for_phase
from backend.services.zac_consultation import ZacSettings, read_terminal, ZacClient
from backend.services.investigation_consult_store import transition


def _turn():
    return {"direction":"investigation", "created_at":"2026-10-14T12:00:00+00:00", "work_result":{}}

def test_channel_only_policy_is_fixed_30_day_window_and_close_is_false():
    policy=investigation_policy(_turn(), {"case_task":{"direction":"investigation"}})
    assert policy["initial_search_window"]["toTs"]-policy["initial_search_window"]["fromTs"] == 30*86400
    assert policy["allow_close"] is False
    assert "supportportal_work" not in toolsets_for_phase("work", direction="investigation")
    assert "supportportal_work" in toolsets_for_phase("work", direction="investigation", allow_close=True)

def test_zac_settings_fail_closed_and_private_recovery_is_not_public():
    assert ZacSettings("https://zac", "secret", "team-agent").error() is None
    assert ZacSettings("https://zac", "secret", "other").error() == "zac_agent_model_not_configured"
    assert public_investigation({"summary":"x", "_zac_user_id":"opaque"}) == {"summary":"x"}

def test_zac_completed_requires_response_completed():
    lines=[b"event: response.completed\n", b'data: {"response":{"metadata":{"conversation_id":"c1","conversation_status":"active"},"output":[{"content":[{"type":"output_text","text":"answer"}]}]}}\n', b"\n"]
    result=read_terminal(lines, deadline=999999999999)
    assert result["state"] == "completed" and result["conversation_id"] == "c1"

def test_zac_stream_done_without_completed_is_unknown():
    result=read_terminal([b"data: [DONE]\n", b"\n"], deadline=999999999999)
    assert result["state"] == "outcome_unknown"


def test_zac_transition_is_idempotent_and_preserves_opaque_request():
    turn = {"turn_id": "t1", "status": "running", "phase": "work", "direction": "investigation", "case_revision": 2, "work_result": {}}
    binding = {"direction": "investigation", "case_task": {"direction": "investigation"}}
    case = {"case_revision": 2}
    run = {"run_id": "r1", "status": "running"}
    request = {"model": "team-agent", "input": "sanitized", "stream": True}
    work, _, record, created = transition(turn, binding, case, run, action="reserve", run_id="r1", request=request)
    assert created and record["state"] == "in_flight" and "request_body" in record
    turn["work_result"] = work
    _, _, same, created_again = transition({**turn, "work_result": work}, binding, case, run, action="reserve", run_id="r1", request=request)
    assert not created_again and same["idempotency_key"] == record["idempotency_key"]
    _, _, stale, _ = transition({**turn, "case_revision": 3, "work_result": work}, binding, {"case_revision": 2}, run, action="finish", run_id="r1", result={"state": "completed", "conversation_id": "c1"})
    assert stale["stale"] is True
