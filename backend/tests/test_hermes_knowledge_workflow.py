from __future__ import annotations

import json

import pytest

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.automation_hermes_agent import (
    KNOWLEDGE_REVIEW_TOOLSETS,
    KNOWLEDGE_SUMMARY_TOOLSETS,
)
from backend.services.hermes_agent_runtime import HermesAgentSettings
from backend.services.hermes_case_workflow import (
    apply_hermes_output,
    build_mock_output,
    create_opening_turn,
    queue_feedback_turn,
    reopen_hermes_case,
    start_hermes_case,
)
from backend.services.hermes_knowledge_workflow import (
    drain_hermes_knowledge_tasks,
    knowledge_workflow_active,
    queue_hermes_summary_for_case,
    queue_hermes_summary_for_locally_resolved_ticket,
    review_session_id_for,
    summary_task_id_for,
)
from backend.services.prompt_runtime import initialize_prompt_runtime

# The workflow resolves its manuals through the prompt runtime; services
# initialize the snapshot at startup, so the tests mirror that here (no
# PROMPT_RELEASE_ID in tests -> the code catalog snapshot).
initialize_prompt_runtime()


def _repository() -> InMemoryTicketRepository:
    repository = InMemoryTicketRepository()
    repository.initialize()
    repository.save_ticket(
        {
            "ticket_id": "123",
            "subject": "Cannot join",
            "status": "investigating",
            "messages": [],
            "created_at": "2026-09-05T08:00:00Z",
            "updated_at": "2026-09-05T08:00:00Z",
        }
    )
    from backend.services.engineer_cases import build_new_engineer_case

    engineer_case = build_new_engineer_case(
        repository.get_ticket("123"),
        engineer_case_id="123-1",
        case_sequence=1,
        title="Cannot join",
        status="investigating",
        trigger_source="account_not_automated",
        trigger_reason="technical",
        now_value="2026-09-05T08:00:00Z",
    )
    engineer_case["thread_id"] = "INV-123-1"
    repository.save_engineer_case(engineer_case)
    return repository


def _start(repository: InMemoryTicketRepository) -> dict:
    request = create_opening_turn(
        engineer_case_id="123-1",
        client_ticket_id="123",
        investigation_id="INV-123-1",
        problem_description="Customer cannot join.",
        investigation_scope="Investigate the reported join failure.",
        completion_criteria=("Identify an evidence-backed conclusion.",),
        now_value="2026-09-05T08:00:00Z",
    )
    start_hermes_case(repository, request=request)
    return request.model_dump()


def _enable_real_mode(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    monkeypatch.delenv("AGENT_MODEL_ID", raising=False)
    monkeypatch.delenv("HERMES_WEKNORA_BASE_URL", raising=False)
    monkeypatch.delenv("HERMES_WEKNORA_API_TOKEN", raising=False)


class FakeHermesAgentClient:
    """Scripted gateway: summary runs and review runs return preset outputs."""

    def __init__(self, *, summary_output: str, review_output: str, run_status: str = "completed"):
        self.settings = HermesAgentSettings(
            base_url="http://hermes.test",
            api_token="test-token",
            turn_timeout_seconds=30.0,
            poll_interval_seconds=0.0,
        )
        self.summary_output = summary_output
        self.review_output = review_output
        self.run_status = run_status
        self.runs: dict[str, dict] = {}
        self.started: list[dict] = []

    def start_run(self, **kwargs) -> dict:
        self.started.append(kwargs)
        run_id = f"run-{len(self.started)}"
        self.runs[run_id] = kwargs
        return {"run_id": run_id}

    def get_run(self, run_id: str) -> dict:
        kwargs = self.runs[run_id]
        is_review = "skills" in (kwargs.get("enabled_toolsets") or [])
        output = self.review_output if is_review else self.summary_output
        return {"status": self.run_status, "output": output, "usage": None}


class FakeWeKnora:
    def __init__(self, results: list[dict]):
        self.results = results
        self.queries: list[str] = []

    def configured(self) -> bool:
        return True

    def search(self, query: str, **kwargs) -> list[dict]:
        self.queries.append(query)
        return self.results


def _summary_output(candidate_id: str = "cand-1") -> str:
    return _summary_output_candidates([
        {
            "candidate_id": candidate_id,
            "statement": "A misconfigured region causes join failures.",
            "context": "Single-case evidence.",
            "evidence_references": ["output-1"],
        }
    ])


def _summary_output_candidates(candidates: list[dict]) -> str:
    payload = {
        "problem_description": "Customer cannot join channels.",
        "timeline": "Opened; investigated; solved.",
        "investigation_process": "Checked routing and join logs.",
        "confirmed_facts": "The join failure is reproducible.",
        "root_cause_and_solution": "Misconfigured region; corrected.",
        "verification_results": "Customer confirmed join works.",
        "limitations_and_unconfirmed": "Long-term stability is unconfirmed.",
        "evidence_references": ["output-1"],
        "candidates": candidates,
    }
    return "Summary follows.\n```json\n" + json.dumps(payload) + "\n```"


def _review_output(decisions: list[dict]) -> str:
    return "Review follows.\n```json\n" + json.dumps({"decisions": decisions}) + "\n```"


def _supplement_decision(candidate_id: str = "cand-1") -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_type": "knowledge",
        "decision": "supplement",
        "confidence": 0.8,
        "rationale": "Existing entry lacks the region condition.",
        "proposed_content": "Add: a misconfigured region causes join failures.",
        "target_object": "weknora:kb:join-failures",
        "target_version": "3",
        "source_references": ["weknora:kb:join-failures"],
    }


def _memory_new_decision(candidate_id: str = "cand-2") -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_type": "memory",
        "decision": "new",
        "confidence": 0.7,
        "rationale": "No existing memory covers this case pattern.",
        "proposed_content": "Case pattern: region misconfiguration join failure.",
        "target_object": None,
        "target_version": None,
        "kind": "semantic",
        "importance": 3,
        "source_references": ["output-1"],
    }


def _memory_new_decision_without_kind(candidate_id: str = "cand-2") -> dict:
    decision = _memory_new_decision(candidate_id)
    decision["kind"] = ""
    return decision


def _skill_decision(candidate_id: str = "cand-3", decision: str = "human_review") -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_type": "skill",
        "decision": decision,
        "confidence": 0.3,
        "rationale": "Proposes a skill edit; skills are human-maintained.",
        "proposed_content": "Add a region-misconfiguration step to the join skill.",
        "target_object": None if decision == "human_review" else "skill:join",
        "target_version": None if decision == "human_review" else "2",
        "source_references": [],
    }


def _human_review_decision(candidate_id: str = "cand-1") -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_type": "knowledge",
        "decision": "human_review",
        "confidence": 0.2,
        "rationale": "Similarity search unavailable; evidence insufficient.",
        "proposed_content": "",
        "target_object": None,
        "target_version": None,
        "source_references": [],
    }


# ------------------------------------------------------------------- triggers


def test_first_terminal_transition_creates_one_task_and_repeats_reuse_it(monkeypatch) -> None:
    _enable_real_mode(monkeypatch)
    repository = _repository()
    _start(repository)

    first = queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")
    second = queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="closed")

    assert first["status"] == "pending"
    assert first["trigger"] == "solved"
    assert second["summary_task_id"] == first["summary_task_id"]
    assert second["trigger"] == "solved"
    assert len(repository.list_hermes_summary_tasks()) == 1


def test_summary_task_is_not_created_outside_real_mode(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_CASE_WORKFLOW_MODE", raising=False)
    repository = _repository()
    _start(repository)

    assert queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved") is None
    assert repository.list_hermes_summary_tasks() == []
    assert knowledge_workflow_active() is False


def test_locally_resolved_ticket_queues_summary_only_when_resolved(monkeypatch) -> None:
    _enable_real_mode(monkeypatch)
    repository = _repository()
    _start(repository)

    assert (
        queue_hermes_summary_for_locally_resolved_ticket(repository, client_ticket_id="123")
        is None
    )

    ticket = repository.get_ticket("123")
    ticket["status"] = "resolved"
    repository.save_ticket(ticket)
    task = queue_hermes_summary_for_locally_resolved_ticket(repository, client_ticket_id="123")
    assert task is not None
    assert task["trigger"] == "local_resolved"


# --------------------------------------------------------------- happy path


def test_summary_then_review_run_on_separate_sessions_with_readonly_toolsets(monkeypatch) -> None:
    _enable_real_mode(monkeypatch)
    repository = _repository()
    _start(repository)
    queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")

    weknora = FakeWeKnora(
        [{"object_id": "weknora:kb:join-failures", "version": "3", "title": "Join failures",
          "snippet": "Existing entry.", "score": 0.9}]
    )
    client = FakeHermesAgentClient(
        summary_output=_summary_output(), review_output=_review_output([_supplement_decision()])
    )
    processed = drain_hermes_knowledge_tasks(
        repository, client=client, weknora_client=weknora, limit=5, sleeper=lambda _: None
    )
    assert processed == 2

    summary_task = repository.get_hermes_summary_task(summary_task_id_for("123-1", 1))
    review_task = repository.get_hermes_review_task_for_summary(summary_task["summary_task_id"])
    assert summary_task["status"] == "completed"
    assert summary_task["packet"]["candidates"][0]["candidate_id"] == "cand-1"
    assert summary_task["packet_hash"] == summary_task["packet"]["content_hash"]
    assert review_task["status"] == "completed"
    assert review_task["report"]["weknora_available"] is True
    assert review_task["report"]["decisions"][0]["decision"] == "supplement"
    assert review_task["report"]["decisions"][0]["target_object"] == "weknora:kb:join-failures"
    assert review_task["weknora_adapter_status"] == "recorded"
    assert review_task["weknora_submissions"][0]["submission_id"] == (
        f"weknora-submission:{review_task['report']['review_id']}:cand-1"
    )

    # Consumption bridge: the completed review atomically enqueued one WeKnora
    # promotion task per decision, with the full cross-system lineage.
    binding_session = repository.get_hermes_case_binding("123-1")["hermes_session_id"]
    promotions = repository.list_weknora_promotions()
    assert len(promotions) == 1
    promotion = promotions[0]
    assert promotion["source_type"] == "hermes_knowledge_review"
    assert promotion["source_id"] == f"{review_task['report']['review_id']}:cand-1"
    assert promotion["source_version"] == review_task["report"]["content_hash"]
    assert promotion["candidate_type"] == "knowledge"
    assert promotion["decision"] == "supplement"
    assert promotion["status"] == "queued"
    assert promotion["client_ticket_id"] == "123"
    assert promotion["summary_session_id"] == binding_session
    assert promotion["summary_run_id"] == "run-1"
    assert promotion["review_session_id"] == review_session_id_for("123-1", 1)
    assert promotion["review_run_id"] == "run-2"
    assert promotion["candidate_payload"]["target_object_id"] == "weknora:kb:join-failures"
    assert promotion["candidate_payload"]["base_version"] == "3"
    assert promotion["candidate_payload"]["title"] == (
        "A misconfigured region causes join failures."
    )
    submission_lineage = review_task["weknora_submissions"][0]["lineage"]
    assert submission_lineage["client_ticket_id"] == "123"
    assert submission_lineage["summary_session_id"] == binding_session
    assert submission_lineage["review_session_id"] == review_session_id_for("123-1", 1)
    assert submission_lineage["review_run_id"] == "run-2"

    assert client.started[0]["session_id"] == binding_session
    assert client.started[0]["enabled_toolsets"] == list(KNOWLEDGE_SUMMARY_TOOLSETS)
    assert client.started[1]["session_id"] == review_session_id_for("123-1", 1)
    assert client.started[1]["session_id"] != binding_session
    assert client.started[1]["enabled_toolsets"] == list(KNOWLEDGE_REVIEW_TOOLSETS)
    assert client.started[1]["idempotency_key"] == review_task["idempotency_key"]
    assert weknora.queries == ["A misconfigured region causes join failures."]


class FakeWeKnoraWriteClient:
    """Write-side store fake for the WeKnoraPromotionAdapter.

    Writes land in the store and reads serve the stored state, so the
    adapter's content/version readback proof is exercised for real.
    """

    def __init__(self, *, memory_identity: bool = True) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.memory_identity = memory_identity
        self.objects: dict[str, dict] = {
            "weknora:kb:join-failures": {
                "object_id": "weknora:kb:join-failures",
                "version": "3",
                "title": "Join failures",
                "content": "Existing entry.",
            }
        }
        self.next_version = 100

    def has_memory_identity(self) -> bool:
        return self.memory_identity

    def supports_conditional_update(self, candidate_type: str) -> bool:
        return True

    def knowledge_read(self, *, object_id: str) -> dict:
        self.calls.append(("knowledge_read", {"object_id": object_id}))
        return dict(self.objects[object_id])

    def knowledge_create(self, *, title: str, content: str, idempotency_key: str) -> dict:
        self.calls.append(("knowledge_create", {"title": title, "key": idempotency_key}))
        object_id = f"kb-new-{len(self.objects)}"
        self.objects[object_id] = {
            "object_id": object_id, "version": "1", "title": title, "content": content,
        }
        return {"object_id": object_id, "version": "1", "receipt": {"ok": True}}

    def knowledge_update(self, *, object_id: str, base_version: str, title: str, content: str, idempotency_key: str) -> dict:
        self.calls.append((
            "knowledge_update",
            {"object_id": object_id, "base_version": base_version, "key": idempotency_key},
        ))
        row = self.objects[object_id]
        row["version"] = str(int(row["version"]) + 1)
        row["content"] = content
        if title:
            row["title"] = title
        return {"object_id": object_id, "version": row["version"], "receipt": {"ok": True}}

    def memory_list(self) -> list[dict]:
        self.calls.append(("memory_list", {}))
        return [
            dict(row) for row in self.objects.values()
            if row["object_id"].startswith("mem-")
        ]

    def memory_create(self, *, content: str, idempotency_key: str, kind: str = "", importance=None) -> dict:
        self.calls.append(("memory_create", {"key": idempotency_key, "kind": kind, "importance": importance}))
        object_id = f"mem-new-{len(self.objects)}"
        self.objects[object_id] = {
            "object_id": object_id, "version": "1", "title": "", "content": content,
        }
        return {"object_id": object_id, "version": "1", "receipt": {"ok": True}}

    def memory_update(self, *, object_id: str, base_version: str, content: str, idempotency_key: str, kind: str = "", importance=None) -> dict:
        self.calls.append(("memory_update", {"object_id": object_id, "key": idempotency_key}))
        row = self.objects[object_id]
        row["version"] = str(int(row["version"]) + 1)
        row["content"] = content
        return {"object_id": object_id, "version": row["version"], "receipt": {"ok": True}}


def _drain_weknora_promotions_like_worker(repository: Any, adapter: Any) -> int:
    """The worker's claim -> execute -> complete loop, verbatim semantics."""
    processed = 0
    for promotion in repository.list_weknora_promotions():
        if promotion["status"] not in {"queued", "active"}:
            continue
        claimed = repository.claim_weknora_promotion(
            promotion["promotion_id"], owner_token="weknora-promotion-worker:test",
            claimed_at="2026-09-05T09:30:00Z", lease_expires_at="2026-09-05T09:32:00Z",
        )
        if not claimed:
            continue
        outcome = adapter.execute(claimed)
        repository.complete_weknora_promotion(
            claimed["promotion_id"], owner_token="weknora-promotion-worker:test",
            status=outcome.status,
            weknora_object_id=outcome.weknora_object_id,
            weknora_version=outcome.weknora_version,
            receipt=outcome.receipt,
            failure_code=outcome.failure_code,
            failure_detail=outcome.failure_detail,
            completed_at="2026-09-05T09:30:01Z",
        )
        processed += 1
    return processed


def test_completed_review_feeds_the_weknora_promotion_worker_end_to_end(monkeypatch) -> None:
    from backend.services.weknora_promotion_adapter import WeKnoraPromotionAdapter

    _enable_real_mode(monkeypatch)
    repository = _repository()
    _start(repository)
    queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")

    client = FakeHermesAgentClient(
        summary_output=_summary_output_candidates([
            {
                "candidate_id": "cand-1",
                "statement": "A misconfigured region causes join failures.",
                "context": "Single-case evidence.",
                "evidence_references": ["output-1"],
            },
            {
                "candidate_id": "cand-2",
                "statement": "Case pattern worth remembering for this account.",
                "context": "",
                "evidence_references": ["output-1"],
            },
            {
                "candidate_id": "cand-3",
                "statement": "Join troubleshooting skill should add a region check.",
                "context": "",
                "evidence_references": [],
            },
        ]),
        review_output=_review_output([
            _supplement_decision("cand-1"),
            _memory_new_decision("cand-2"),
            _skill_decision("cand-3"),
        ]),
    )
    processed = drain_hermes_knowledge_tasks(
        repository, client=client, weknora_client=None, limit=5, sleeper=lambda _: None
    )
    assert processed == 2

    promotions = {
        row["source_id"].rsplit(":", 1)[-1]: row
        for row in repository.list_weknora_promotions()
    }
    assert sorted(promotions) == ["cand-1", "cand-2", "cand-3"]
    assert promotions["cand-1"]["decision"] == "supplement"
    assert promotions["cand-2"]["decision"] == "new"
    assert promotions["cand-3"]["candidate_type"] == "skill"
    assert promotions["cand-3"]["decision"] == "human_review"
    assert promotions["cand-3"]["candidate_payload"]["skill_proposal"] is True
    assert promotions["cand-3"]["candidate_payload"]["decision"] == "human_review"

    write_client = FakeWeKnoraWriteClient()
    adapter = WeKnoraPromotionAdapter(write_client)
    assert _drain_weknora_promotions_like_worker(repository, adapter) == 3
    statuses = {
        row["source_id"].rsplit(":", 1)[-1]: row["status"]
        for row in repository.list_weknora_promotions()
    }
    assert statuses == {"cand-1": "accepted", "cand-2": "accepted", "cand-3": "human_review"}
    operations = [name for name, _ in write_client.calls]
    assert "knowledge_update" in operations
    assert "memory_create" in operations
    update_call = next(kwargs for name, kwargs in write_client.calls if name == "knowledge_update")
    assert update_call["object_id"] == "weknora:kb:join-failures"
    assert update_call["base_version"] == "3"
    # Review -> bridge -> Adapter memory field chain: the review's kind and
    # importance classification survive into the memory write call.
    memory_call = next(kwargs for name, kwargs in write_client.calls if name == "memory_create")
    assert memory_call["kind"] == "semantic"
    assert memory_call["importance"] == 3

    # Idempotency: nothing left to claim, and re-enqueueing the same report
    # from the completed review does not duplicate rows.
    assert _drain_weknora_promotions_like_worker(repository, adapter) == 0
    assert len(repository.list_weknora_promotions()) == 3


def test_bridge_routes_any_skill_write_intent_to_human_review_only() -> None:
    from backend.services.hermes_knowledge_workflow import (
        build_weknora_promotions_from_review_report,
    )
    from backend.repositories.weknora_promotion_repository import (
        normalize_weknora_promotion_task,
    )

    report = {
        "review_id": "hermes-review:case-1:1",
        "engineer_case_id": "case-1",
        "client_ticket_id": "ticket-1",
        "investigation_id": "INV-1",
        "content_hash": "a" * 64,
        "decisions": [_skill_decision("cand-9", decision="new")],
    }
    packet = {"candidates": [{"candidate_id": "cand-9", "statement": "Skill edit proposal."}]}
    summary_task = {"hermes_session_id": "hermes-session:case-1", "run_id": "run-1"}
    review_task = {"review_session_id": "hermes-session:kr", "run_id": "run-2"}
    tasks = build_weknora_promotions_from_review_report(
        report, summary_task=summary_task, review_task=review_task,
        review_run_id="run-2", packet=packet,
    )
    assert len(tasks) == 1
    assert tasks[0]["candidate_type"] == "skill"
    assert tasks[0]["decision"] == "human_review"
    assert tasks[0]["candidate_payload"]["decision"] == "new"
    assert tasks[0]["candidate_payload"]["skill_proposal"] is True
    normalized = normalize_weknora_promotion_task(tasks[0], now_value="2026-09-30T00:00:00Z")
    assert normalized["status"] == "queued"

    # A direct skill write-intent enqueue is rejected at the repository gate.
    with pytest.raises(ValueError, match="human_review"):
        normalize_weknora_promotion_task(
            {**tasks[0], "decision": "new"}, now_value="2026-09-30T00:00:00Z"
        )


def test_memory_decision_without_kind_fails_the_review_contract() -> None:
    """Review-acceptance round 3: an unclassified memory decision cannot
    pass the review contract, so it never reaches the write chain."""
    from backend.services.hermes_case_workflow import HermesReviewDecision

    with pytest.raises(ValueError, match="memory kind"):
        HermesReviewDecision.model_validate(_memory_new_decision_without_kind())
    # With the classification the decision validates and the bridge forwards it.
    decision = HermesReviewDecision.model_validate(_memory_new_decision())
    assert decision.kind == "semantic"
    assert decision.importance == 3


def test_weknora_unavailable_completes_with_human_review_fail_closed(monkeypatch) -> None:
    _enable_real_mode(monkeypatch)
    repository = _repository()
    _start(repository)
    queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")

    client = FakeHermesAgentClient(
        summary_output=_summary_output(), review_output=_review_output([_human_review_decision()])
    )
    processed = drain_hermes_knowledge_tasks(
        repository, client=client, weknora_client=None, limit=5, sleeper=lambda _: None
    )
    assert processed == 2

    review_task = repository.list_hermes_review_tasks()[0]
    assert review_task["status"] == "completed"
    assert review_task["weknora_available"] is False
    assert review_task["report"]["weknora_available"] is False
    assert review_task["report"]["decisions"][0]["decision"] == "human_review"


# ----------------------------------------------------------------- failures


def test_summary_run_failure_never_creates_a_review_task(monkeypatch) -> None:
    _enable_real_mode(monkeypatch)
    repository = _repository()
    _start(repository)
    queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")

    client = FakeHermesAgentClient(
        summary_output=_summary_output(),
        review_output=_review_output([_supplement_decision()]),
        run_status="failed",
    )
    drain_hermes_knowledge_tasks(repository, client=client, weknora_client=None, limit=5,
                                 sleeper=lambda _: None)

    summary_task = repository.list_hermes_summary_tasks()[0]
    assert summary_task["status"] == "failed"
    assert summary_task["error_code"] == "hermes_run_failed"
    assert repository.list_hermes_review_tasks() == []


def test_summary_output_contract_violation_fails_without_success(monkeypatch) -> None:
    _enable_real_mode(monkeypatch)
    repository = _repository()
    _start(repository)
    queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")

    broken = {
        "problem_description": "Customer cannot join channels.",
        "investigation_process": "Checked routing and join logs.",
    }
    client = FakeHermesAgentClient(
        summary_output=json.dumps(broken),
        review_output=_review_output([_supplement_decision()]),
    )
    drain_hermes_knowledge_tasks(repository, client=client, weknora_client=None, limit=5,
                                 sleeper=lambda _: None)

    summary_task = repository.list_hermes_summary_tasks()[0]
    assert summary_task["status"] == "failed"
    assert summary_task["error_code"] == "output_contract_invalid"
    assert repository.list_hermes_review_tasks() == []


def test_review_coverage_mismatch_fails_the_review_task(monkeypatch) -> None:
    _enable_real_mode(monkeypatch)
    repository = _repository()
    _start(repository)
    queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")

    client = FakeHermesAgentClient(
        summary_output=_summary_output(),
        review_output=_review_output([_supplement_decision(candidate_id="ghost-cand")]),
    )
    drain_hermes_knowledge_tasks(repository, client=client, weknora_client=None, limit=5,
                                 sleeper=lambda _: None)

    summary_task = repository.list_hermes_summary_tasks()[0]
    review_task = repository.list_hermes_review_tasks()[0]
    assert summary_task["status"] == "completed"
    assert review_task["status"] == "failed"
    assert review_task["error_code"] == "review_coverage_invalid"


def test_revision_conflict_during_pending_summary_fails_not_succeeds(monkeypatch) -> None:
    _enable_real_mode(monkeypatch)
    repository = _repository()
    _start(repository)
    queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")

    feedback = queue_feedback_turn(
        repository, engineer_case_id="123-1", input_text="please double check",
        now_value="2026-09-05T08:02:00Z",
    )
    claimed = repository.claim_hermes_turn(
        request_id=feedback.request_id,
        owner_token="worker-1",
        claimed_at="2026-09-05T08:02:30Z",
        lease_expires_at="2026-09-05T08:03:30Z",
    )
    apply_hermes_output(repository, build_mock_output(claimed, now_value="2026-09-05T08:02:31Z"))

    client = FakeHermesAgentClient(
        summary_output=_summary_output(), review_output=_review_output([_supplement_decision()])
    )
    drain_hermes_knowledge_tasks(repository, client=client, weknora_client=None, limit=5,
                                 sleeper=lambda _: None)

    summary_task = repository.list_hermes_summary_tasks()[0]
    assert summary_task["status"] == "failed"
    assert summary_task["error_code"] == "stale_case_lineage"
    assert repository.list_hermes_review_tasks() == []


# --------------------------------------------------------------- invalidation


def test_reopen_invalidates_open_tasks_and_next_episode_gets_a_new_task(monkeypatch) -> None:
    _enable_real_mode(monkeypatch)
    repository = _repository()
    _start(repository)
    first = queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")

    reopen_hermes_case(
        repository, engineer_case_id="123-1", input_text="Customer reproduced the issue.",
        now_value="2026-09-05T08:03:00Z",
    )

    invalidated = repository.get_hermes_summary_task(first["summary_task_id"])
    assert invalidated["status"] == "invalidated"

    second = queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")
    assert second["summary_task_id"] == summary_task_id_for("123-1", 2)
    assert second["status"] == "pending"
    assert repository.get_hermes_summary_task(first["summary_task_id"])["status"] == "invalidated"
    assert repository.claim_hermes_summary_task(
        first["summary_task_id"], owner_token="w", claimed_at="2026-09-05T08:04:00Z",
        lease_expires_at="2026-09-05T08:05:00Z",
    ) is None
