from datetime import datetime, timezone
import json
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock

from backend.services.investigation_runtime_policy import investigation_policy, public_investigation
from backend.services.automation_hermes_agent import toolsets_for_phase
from backend.services.zac_consultation import ZacSettings, TeamAgentSettings, read_terminal, ZacClient, TeamAgentClient
from backend.services.investigation_consult_store import transition


def _turn():
    return {"direction":"investigation", "created_at":"2026-10-14T12:00:00+00:00", "work_result":{}}

def test_investigation_toolset_contract_is_explicit_and_automation_is_separate():
    assert toolsets_for_phase("work", direction="investigation") == [
        "supportportal_investigation", "common", "memory", "skills"
    ]
    assert "supportportal_work" not in toolsets_for_phase("work", direction="investigation")
    assert toolsets_for_phase("work", direction="automation") == ["supportportal_work"]
    assert toolsets_for_phase("work", direction="investigation", allow_close=True) == [
        "supportportal_investigation", "common", "memory", "skills", "supportportal_work"
    ]

def test_channel_only_policy_is_fixed_30_day_window_and_close_is_false():
    policy=investigation_policy(_turn(), {"case_task":{"direction":"investigation"}})
    assert policy["initial_search_window"]["toTs"]-policy["initial_search_window"]["fromTs"] == 30*86400
    assert policy["allow_close"] is False
    assert "supportportal_work" not in toolsets_for_phase("work", direction="investigation")
    assert "supportportal_work" in toolsets_for_phase("work", direction="investigation", allow_close=True)


def test_customer_says_today_adds_bounded_service_default_window():
    turn = {
        **_turn(),
        "created_at": "2026-10-15T16:30:00+00:00",  # 2026-10-16 00:30 Asia/Shanghai
        "input_snapshot": {
            "conversation": [
                {"author": {"role": "agent"}, "body": "earlier"},
                {"author": {"role": "customer"}, "body": "这是今天的 session，channel name=zilingtest"},
            ]
        },
    }
    policy = investigation_policy(turn, {"case_task": {"direction": "investigation"}})
    window = policy["relative_date_window"]
    assert window["window_basis"] == "relative_customer_date"
    assert window["timezone"] == "Asia/Shanghai"
    assert window["timezone_source"] == "service_default"
    assert window["fromTs"] == int(datetime.fromisoformat("2026-10-15T16:00:00+00:00").timestamp())
    assert window["toTs"] == int(datetime.fromisoformat("2026-10-15T16:30:00+00:00").timestamp())


def test_customer_says_today_in_english_adds_targeted_window():
    turn = {
        **_turn(),
        "input_snapshot": {
            "conversation": [
                {"author": {"role": "customer"}, "body": "I mean today's session"},
            ]
        },
    }
    policy = investigation_policy(turn, {"case_task": {"direction": "investigation"}})
    assert policy["relative_date_window"]["query_priority"] == "targeted_current_turn"

def test_initial_ticket_description_today_adds_window_without_copying_description():
    turn = {
        **_turn(),
        "created_at": "2026-10-15T16:30:00+00:00",
        "input_snapshot": {
            "current_event": {"event_type": "ticket.created", "trigger_comment_id": None},
            "ticket": {"description": "Please inspect today's session"},
            "conversation": [],
        },
    }
    policy = investigation_policy(turn, {"case_task": {"direction": "investigation"}})
    assert policy["relative_date_window"]["window_source"] == "initial_ticket_description"

def test_initial_ticket_description_chinese_today_is_bounded():
    turn = {
        **_turn(),
        "created_at": "2026-10-15T16:30:00+00:00",
        "input_snapshot": {
            "current_event": {"event_type": "ticket.created", "trigger_comment_id": None},
            "ticket": {"description": "请检查今天早上的 session"},
            "conversation": [],
        },
    }
    policy = investigation_policy(turn, {"case_task": {"direction": "investigation"}})
    window = policy["relative_date_window"]
    assert window["fromTs"] == int(datetime.fromisoformat("2026-10-15T16:00:00+00:00").timestamp())
    assert window["toTs"] == int(datetime.fromisoformat("2026-10-15T16:30:00+00:00").timestamp())

def test_initial_description_does_not_override_customer_comment_or_agent_history():
    base = {
        **_turn(),
        "input_snapshot": {
            "current_event": {"event_type": "ticket.created", "trigger_comment_id": None},
            "ticket": {"description": "today's session"},
            "conversation": [{"author": {"role": "customer"}, "body": "channel only"}],
        },
    }
    assert "relative_date_window" not in investigation_policy(base, {"case_task": {"direction": "investigation"}})
    agent_only = {**base, "input_snapshot": {**base["input_snapshot"], "current_event": {"event_type": "ticket.updated", "trigger_comment_id": "c1"}, "conversation": [{"author": {"role": "agent"}, "body": "today"}]}}
    assert "relative_date_window" not in investigation_policy(agent_only, {"case_task": {"direction": "investigation"}})


def test_previous_today_in_history_does_not_trigger_current_turn_window():
    turn = {
        **_turn(),
        "input_snapshot": {
            "conversation": [
                {"author": {"role": "customer"}, "body": "今天的 session"},
                {"author": {"role": "agent"}, "body": "请提供更多信息"},
                {"author": {"role": "customer"}, "body": "channel name=zilingtest"},
            ]
        },
    }
    policy = investigation_policy(turn, {"case_task": {"direction": "investigation"}})
    assert "relative_date_window" not in policy

def test_historical_customer_today_does_not_trigger_without_current_comment():
    turn = {
        **_turn(),
        "input_snapshot": {
            "current_event": {"event_type": "ticket.updated", "trigger_comment_id": "new-customer"},
            "conversation": [{"id": "old-customer", "author": {"role": "customer"}, "body": "today"}],
        },
    }
    assert "relative_date_window" not in investigation_policy(turn, {"case_task": {"direction": "investigation"}})


def test_zendesk_snapshot_end_user_role_triggers_today_but_agent_flag_does_not():
    turn = {
        **_turn(),
        "input_snapshot": {
            "schema": "hermes-case-snapshot-v2",
            "conversation": [
                {"author": {"role": "end-user", "is_agent": False}, "body": "是今天的 session"},
            ],
        },
    }
    policy = investigation_policy(turn, {"case_task": {"direction": "investigation"}})
    assert policy["relative_date_window"]["window_basis"] == "relative_customer_date"

    agent_turn = {
        **turn,
        "input_snapshot": {
            **turn["input_snapshot"],
            "conversation": [
                {"author": {"role": "end-user", "is_agent": True}, "body": "是今天的 session"},
            ],
        },
    }
    agent_policy = investigation_policy(agent_turn, {"case_task": {"direction": "investigation"}})
    assert "relative_date_window" not in agent_policy

def test_zac_settings_fail_closed_and_private_recovery_is_not_public():
    assert ZacSettings("https://zac", "secret", "team-agent").error() is None
    assert ZacSettings("https://zac", "secret", "other").error() == "zac_agent_model_not_configured"
    assert public_investigation({"summary":"x", "_zac_user_id":"opaque"}) == {"summary":"x"}

def test_team_agent_settings_and_endpoint_normalization():
    assert TeamAgentSettings("https://hermes.example/v1", "secret", "team-agent").error() is None
    assert TeamAgentSettings("http://hermes.preproduction.supportportal.local:8642", "secret", "team-agent").error() is None
    assert TeamAgentSettings("https://hermes.example/v1?x=1", "secret", "team-agent").error() == "zac_agent_not_configured"
    assert TeamAgentSettings("http://hermes.example", "secret", "team-agent").error() == "zac_agent_not_configured"
    assert TeamAgentSettings("https://user:pass@hermes.example", "secret", "team-agent").error() == "zac_agent_not_configured"

def test_zac_completed_requires_response_completed():
    lines=[b"event: response.completed\n", b'data: {"response":{"metadata":{"conversation_id":"c1","conversation_status":"active"},"output":[{"content":[{"type":"output_text","text":"answer"}]}]}}\n', b"\n"]
    result=read_terminal(lines, deadline=999999999999)
    assert result["state"] == "completed" and result["conversation_id"] == "c1"

def test_zac_stream_done_without_completed_is_unknown():
    result=read_terminal([b"data: [DONE]\n", b"\n"], deadline=999999999999)
    assert result["state"] == "outcome_unknown"


def test_zac_client_sends_bound_request_contract_and_reads_completed_sse():
    seen = []

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __iter__(self):
            return iter([
                b"event: response.completed\n",
                b'data: {"response":{"metadata":{"conversation_id":"c1"},"output":[]}}\n',
                b"\n",
            ])

    def opener(request, timeout):
        seen.append((request, timeout))
        return Response()

    client = ZacClient(ZacSettings("https://zac.example", "token", "team-agent"), opener=opener, clock=lambda: 0)
    result = client.consult({
        "request_body": '{"model":"team-agent","input":"sanitized","stream":true}',
        "user_id": "user-opaque",
        "idempotency_key": "idem-1",
    })
    assert result["state"] == "completed"
    request, _ = seen[0]
    assert request.full_url == "https://zac.example/v1/responses"
    assert request.get_header("Authorization") == "Bearer token"
    assert request.get_header("X-api-user-id") == "user-opaque"
    assert request.get_header("Idempotency-key") == "idem-1"
    assert json.loads(request.data) == {"model": "team-agent", "input": "sanitized", "stream": True}


def test_zac_client_retries_transport_with_same_key_and_body_then_unknown():
    seen = []

    def opener(request, timeout):
        seen.append((request.full_url, request.get_header("Idempotency-key"), request.data))
        raise TimeoutError("断流")

    clock_values = iter([0, 1, 2, 3, 4])
    client = ZacClient(ZacSettings("https://zac.example", "token", "team-agent"), opener=opener, clock=lambda: next(clock_values, 4))
    result = client.consult({
        "request_body": '{"model":"team-agent","input":"sanitized","stream":true}',
        "user_id": "user-opaque",
        "idempotency_key": "idem-1",
    })
    assert result["state"] == "outcome_unknown" and result["attempts"] == 2
    assert len(seen) == 2 and seen[0] == seen[1]


def test_zac_client_http_500_is_structured_failed_result():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 500, "upstream", {}, None)

    client = ZacClient(ZacSettings("https://zac.example", "token", "team-agent"), opener=opener, clock=lambda: 0)
    result = client.consult({
        "request_body": '{"model":"team-agent","input":"sanitized","stream":true}',
        "user_id": "user-opaque",
        "idempotency_key": "idem-500",
    })
    assert result == {"state": "failed", "code": "zac_http_500", "attempts": 1}
    assert result["state"] != "outcome_unknown"
    assert "completed" not in result and "handoff_status" not in result


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
