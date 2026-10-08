import json
from pathlib import Path
import shutil
import subprocess

import pytest
from backend.services.automation_ecs_contracts import AutomationIntakeEvent


PATCH = json.loads((Path(__file__).resolve().parents[2] / "docs/plans/investigation-route-status-n8n-patch.json").read_text())


def evaluate(code, payload):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required for the actual n8n JavaScript contract")
    runner = "const payload=JSON.parse(process.argv[1]);const $=()=>({item:{json:payload}});process.stdout.write(JSON.stringify((function(){" + code + "})()));"
    return subprocess.run([node,"-e",runner,json.dumps(payload)],text=True,capture_output=True)


def test_actual_status_transformer_uses_same_snapshot_stable_identity_and_real_source():
    code = next(op["node"]["parameters"]["jsCode"] for op in PATCH["statusWorkflow"]["operations"] if op.get("node",{}).get("type") == "n8n-nodes-base.code")
    ticket = {"id":123,"status":"pending","updated_at":"2026-10-08T10:01:00Z","custom_fields":[{"id":9,"value":True}]}
    first = evaluate(code,{"ticket":ticket})
    assert first.returncode == 0, first.stderr
    payload = json.loads(first.stdout)["json"]
    event = AutomationIntakeEvent.model_validate(payload)
    assert event.ticket.id == "123" and event.ticket.status == "pending"
    assert event.ticket.updated_at.isoformat() == "2026-10-08T10:01:00+00:00"
    assert payload["occurred_at"] == payload["ticket"]["updated_at"]
    assert payload["ticket"]["custom_fields"] == {"9":True}
    assert json.loads(evaluate(code,{"ticket":ticket}).stdout)["json"]["event_id"] == payload["event_id"]
    for updated in [None,"invalid","2026-10-08T10:01:00"]:
        invalid = evaluate(code,{"ticket":{**ticket,"updated_at":updated}})
        assert invalid.returncode != 0 and "source updated_at" in invalid.stderr


def test_actual_slack_forward_preserves_authenticated_message_identity():
    expression = PATCH["slackWorkflow"]["operations"][0]["value"]
    code = "return " + expression[3:-2].strip() + ";"
    incoming = {"Input":{"team":"T-TEST","channel":"C-TEST","thread_ts":"123.45","user":"U-TEST","ts":"124.01","text":"<@U08RVQSJQF2> close the case"}}
    result = evaluate(code,incoming)
    assert result.returncode == 0, result.stderr
    body = json.loads(json.loads(result.stdout))
    assert body["source_event_id"] == "T-TEST:C-TEST:124.01"
    assert body["message_ts"] == "124.01" and body["text"] == "close the case"
    assert body["slack_user_id"] == "U-TEST" and body["team_id"] == "T-TEST"
    no_ts = evaluate(code,{"Input":{**incoming["Input"],"ts":None}})
    assert no_ts.returncode != 0 and "message ts required" in no_ts.stderr
