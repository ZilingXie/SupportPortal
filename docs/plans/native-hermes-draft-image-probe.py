"""Released-image probe: memory store and mocked external boundaries only."""
import json
import os
from unittest.mock import patch

from backend.services.automation_ecs_contracts import AutomationIntakeEvent, ExecutionStatus, JobKind
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_store import InMemoryAutomationEcsStore
from backend.services.hermes_case_task import CaseTask
from backend.services.automation_hermes_agent import HermesAgentTurnProcessor
from backend.services.automation_hermes_tools import (
    HermesToolError, continue_hermes_investigation,
    tool_save_investigation_progress, tool_save_reply_draft,
)


BUILD_REF = os.environ.get("APP_BUILD_REF")
checks = []


def scenario(exhaust):
    isolated = {
        "AUTOMATION_ENVIRONMENT": "preproduction", "AUTOMATION_DB_SCHEMA": "preproduction_probe",
        "AUTOMATION_DB_RESOURCE_ID": "memory", "AUTOMATION_JOB_NAMESPACE": "preproduction_probe",
        "AUTOMATION_INTAKE_SHARED_TOKEN": "isolated-probe",
        "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1", "AUTOMATION_RELEASE_ID": "probe",
        "AUTOMATION_IMAGE_DIGEST": "sha256:" + "a" * 64,
        "APP_BUILD_REF": BUILD_REF or "probe", "PROMPT_RELEASE_ID": "probe",
        "AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED": "0",
    }
    with patch.dict(os.environ, isolated, clear=True):
        settings = AutomationEcsSettings.from_env("worker")
        store = InMemoryAutomationEcsStore(settings)
        store.migrate()
        event = AutomationIntakeEvent.model_validate({
            "schema_version": "automation-intake-v1", "event_id": "probe-created",
            "event_type": "ticket.created", "occurred_at": "2026-10-13T00:00:00Z",
            "ticket": {"id": "99999999", "status": "open", "subject": "Synthetic investigation",
                       "description": "Synthetic investigation", "requester": {"name": "Customer", "email": "probe@example.invalid"}},
        })
        store.accept_intake(event, settings.provenance())
        route = store.claim_job(JobKind.ROUTE, worker_id="probe", lease_seconds=300)
        store.hand_off_to_hermes_agent(route, case_task=CaseTask(
            direction="investigation", route="investigation", source_event_id=event.event_id,
            route_family="rag_product_support", route_target="rag", execution_action="rag",
            reason_code="technical_request", confidence=.95,
            prompt_release_id="probe", prompt_snapshot={
                "hermes-work-manual": "Synthetic investigation only.",
                "hermes-persona-manual": "Prepare a synthetic draft only.",
                "hermes-reply-contract": "Synthetic review only.",
            },
        ).model_dump(mode="json"))
        job = store.claim_job(JobKind.AGENT_TURN, worker_id="probe", lease_seconds=300)
        source_id = job.payload["turn_id"]
        responses = []

        class Client:
            submissions = 0

            def start_run(self, **kwargs):
                self.submissions += 1
                key = kwargs["idempotency_key"]
                turn_id = key.split(":")[1]
                if key.endswith(":work"):
                    tool_save_investigation_progress(store, None, turn_id=turn_id,
                        summary="Call reference unavailable.", evidence=[], blockers=["Call reference unavailable"],
                        next_steps=["Request channel name and time window"])
                else:
                    assert "rewrite once in this same run" in kwargs["instructions"]
                    assert '"max_complete_chars": 1200' in kwargs["input_text"]
                    for text in ["x" * 1200, "x" * 1200 if exhaust else "Please share the channel name."]:
                        try:
                            responses.append(tool_save_reply_draft(store, None, turn_id=turn_id, content=text))
                        except HermesToolError as exc:
                            responses.append(exc.details)
                    if exhaust:
                        try:
                            tool_save_reply_draft(store, None, turn_id=turn_id, content="A valid short draft.")
                            raise AssertionError("third candidate was accepted")
                        except HermesToolError as exc:
                            assert exc.code == "draft_rewrite_exhausted"
                return {"run_id": "probe-run-" + str(self.submissions)}

            def get_run(self, run_id):
                return {"run_id": run_id, "status": "completed", "output": "ok",
                        "usage": {"input_tokens": 1, "output_tokens": 1}}

        model = Client()
        processor = HermesAgentTurnProcessor(store, client=model, repository=None,
            environment="preproduction", poll_interval_seconds=.01)
        with patch("backend.services.engineer_slack.notify_hermes_investigation_result"), \
             patch("backend.services.engineer_slack.notify_hermes_draft_pending"), \
             patch("backend.services.engineer_slack.notify_hermes_draft_blocked") as notice, \
             patch("backend.services.automation_hermes_tools.run_engineer_guardrail_final",
                   return_value={"decision": "pass", "blockers": []}):
            parked = processor.process(job)
            assert parked["status"] == "awaiting_investigation_review"
            store.complete_processing(job, outcome=parked, status=ExecutionStatus.COMPLETED)
            reply = continue_hermes_investigation(store, "99999999", base_event={})
            job = store.claim_job(JobKind.AGENT_TURN, worker_id="probe", lease_seconds=300)
            result = processor.process(job)
            turn = store.get_hermes_turn(reply["turn_id"])
            state = turn["work_result"]["draft_save"]
            assert state["attempts"] == 2 and state["run_id"] == "probe-run-2"
            assert turn["work_result"]["source_turn_id"] == source_id
            assert turn["direction"] == "investigation"
            assert responses[0]["code"] == "reply_too_long" and responses[0]["rewrite_allowed"]
            drafts = store.get_hermes_case_review("99999999")["drafts"]
            if exhaust:
                assert not drafts and result["error_code"] == "reply_too_long"
                assert state["last_error"]["run_id"] == "probe-run-2"
                assert notice.call_args.kwargs["reason"] == "reply_too_long"
            else:
                assert len(drafts) == 1 and drafts[0]["status"] == "awaiting_approval"
                assert "channel name" in drafts[0]["content"]
                assert result["publication"]["status"] == "awaiting_approval"
            assert all(not e.get("deliveries") for e in store.list_case_executions("99999999"))
            assert model.submissions == 2  # one Work, one Persona
    checks.append("exhaustion_exact_reason_zero_drafts" if exhaust else "prepare_one_rewrite_awaiting_approval")


scenario(False)
scenario(True)
print(json.dumps({"status": "passed", "checks": checks, "business_writes": 0,
                  "build_ref": BUILD_REF, "store": "isolated_memory", "model": "mocked"}))
