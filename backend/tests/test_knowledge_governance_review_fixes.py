"""Targeted tests for the p2-181/p2-182 acceptance remediation.

Covers the four code fixes on the current main baseline:
skill-decision boundary in the review contract, the complete Summary input
bundle (ticket history + Slack thread), per-surface WeKnora evidence with
fail-closed decision downgrade, and full lineage metadata on WeKnora writes.
"""

from __future__ import annotations

import json

import pytest

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.engineer_slack import build_engineer_case_thread_event
from backend.services.hermes_agent_runtime import HermesAgentSettings
from backend.services.hermes_case_workflow import (
    HermesReviewDecision,
    apply_hermes_output,
    build_mock_output,
    create_opening_turn,
    start_hermes_case,
)
from backend.services.hermes_knowledge_workflow import (
    _collect_weknora_evidence,
    _downgrade_decisions_without_evidence,
    build_case_close_bundle,
    drain_hermes_knowledge_tasks,
    queue_hermes_summary_for_case,
    review_session_id_for,
    summary_task_id_for,
)
from backend.services.prompt_runtime import initialize_prompt_runtime
from backend.services.weknora_client import WeKnoraClient
from backend.services.weknora_promotion_adapter import WeKnoraPromotionAdapter
from backend.repositories.weknora_promotion_repository import normalize_weknora_promotion_task

initialize_prompt_runtime()


def _repository() -> InMemoryTicketRepository:
    repository = InMemoryTicketRepository()
    repository.initialize()
    repository.save_ticket(
        {
            "ticket_id": "123",
            "subject": "Cannot join",
            "status": "investigating",
            "messages": [
                {
                    "role": "customer",
                    "content": "Customer cannot join channels after region switch.",
                    "created_at": "2026-09-05T08:01:00Z",
                    "external_id": "zd-comment-1",
                },
                {
                    "role": "assistant",
                    "content": "We reproduced the join failure and corrected the region.",
                    "created_at": "2026-09-05T08:30:00Z",
                },
            ],
            "created_at": "2026-09-05T08:00:00Z",
            "updated_at": "2026-09-05T08:30:00Z",
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
    repository.save_engineer_case(
        engineer_case,
        slack_events=[
            build_engineer_case_thread_event(
                event_id="engineer-slack:123-1:open",
                event_type="engineer_case_opened",
                engineer_case_id="123-1",
                message_text="Engineer case opened for the join failure.",
            )
        ],
    )
    # Deliver the opening event so the authoritative thread binding exists
    # (InMemory derives the binding from the delivered opening event).
    repository.claim_engineer_slack_event(
        event_id="engineer-slack:123-1:open", claimed_at="2026-09-05T08:00:05Z",
    )
    repository.complete_engineer_slack_event(
        event_id="engineer-slack:123-1:open",
        status="delivered",
        failure_code=None,
        completed_at="2026-09-05T08:00:10Z",
        slack_channel_id="C123",
        slack_message_ts="1693900810.000100",
        slack_thread_ts="1693900800.001",
    )
    request = create_opening_turn(
        engineer_case_id="123-1",
        client_ticket_id="123",
        investigation_id="INV-123-1",
        problem_description="Customer cannot join.",
        investigation_scope="Investigate the join failure.",
        completion_criteria=("Identify an evidence-backed conclusion.",),
        now_value="2026-09-05T08:00:00Z",
    )
    start_hermes_case(repository, request=request)
    apply_hermes_output(repository, build_mock_output(request.model_dump(mode="json")))
    return repository


def _enable_real_mode(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    monkeypatch.delenv("AGENT_MODEL_ID", raising=False)
    monkeypatch.delenv("HERMES_WEKNORA_BASE_URL", raising=False)
    monkeypatch.delenv("HERMES_WEKNORA_API_TOKEN", raising=False)


class FakeHermesAgentClient:
    def __init__(self, *, summary_output: str, review_output: str) -> None:
        self.settings = HermesAgentSettings(
            base_url="http://hermes.test", api_token="test-token",
            turn_timeout_seconds=30.0, poll_interval_seconds=0.0,
        )
        self.summary_output = summary_output
        self.review_output = review_output
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
        return {"status": "completed", "output": output, "usage": None}


class FakeKnowledgeWeKnora:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def configured(self) -> bool:
        return True

    def search(self, query: str, **kwargs) -> list[dict]:
        self.queries.append(query)
        return [
            {
                "object_id": "weknora:kb:join-failures", "version": "3",
                "title": "Join failures", "snippet": "Existing entry.", "score": 0.9,
            }
        ]


class FakeMemoryWeKnora:
    """Mirrors the contract-pinned client's official memory API (list)."""

    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.list_calls = 0

    def is_configured(self) -> bool:
        return self.ok

    def has_memory_identity(self) -> bool:
        return self.ok

    def memory_list(self, *, top_k: int | None = None) -> list[dict]:
        self.list_calls += 1
        if not self.ok:
            raise RuntimeError("memory surface down")
        return [{"object_id": "mem-1", "version": "1", "content": "existing memory"}]


def _summary_output(candidate_id: str = "cand-1") -> str:
    payload = {
        "problem_description": "Customer cannot join channels.",
        "timeline": "Opened; investigated; solved.",
        "investigation_process": "Checked routing and join logs.",
        "confirmed_facts": "The join failure is reproducible.",
        "root_cause_and_solution": "Misconfigured region; corrected.",
        "verification_results": "Customer confirmed join works.",
        "limitations_and_unconfirmed": "Long-term stability is unconfirmed.",
        "evidence_references": ["output-1"],
        "candidates": [
            {
                "candidate_id": candidate_id,
                "statement": "A misconfigured region causes join failures.",
                "context": "Single-case evidence.",
                "evidence_references": ["output-1"],
            }
        ],
    }
    return "Summary follows.\n```json\n" + json.dumps(payload) + "\n```"


def _review_output(decisions: list[dict]) -> str:
    return "Review follows.\n```json\n" + json.dumps({"decisions": decisions}) + "\n```"


def _decision(candidate_type: str, decision: str, **overrides) -> dict:
    payload = {
        "candidate_id": "cand-1",
        "candidate_type": candidate_type,
        "decision": decision,
        "confidence": 0.8,
        "rationale": "Grounded in the provided evidence.",
        "proposed_content": "Complete writable content.",
        "target_object": None,
        "target_version": None,
        "source_references": ["output-1"],
    }
    if decision in {"no_change", "merge", "supplement", "replace"}:
        payload["target_object"] = "weknora:kb:join-failures"
        payload["target_version"] = "3"
    payload.update(overrides)
    return payload


# ------------------------------------------------------------- skill boundary


class TestSkillDecisionBoundary:
    def test_skill_write_decisions_are_contract_invalid(self) -> None:
        for decision in ("new", "merge", "supplement", "replace"):
            with pytest.raises(ValueError, match="skill candidates only allow"):
                HermesReviewDecision.model_validate(_decision("skill", decision))

    def test_skill_allows_no_change_and_human_review_without_target(self) -> None:
        for decision in ("no_change", "human_review"):
            validated = HermesReviewDecision.model_validate(
                _decision("skill", decision, target_object=None, target_version=None)
            )
            assert validated.decision == decision
            assert validated.target_object is None

    def test_skill_decisions_never_claim_a_weknora_target(self) -> None:
        with pytest.raises(ValueError, match="must not claim a WeKnora target"):
            HermesReviewDecision.model_validate(
                _decision("skill", "no_change", target_object="weknora:kb:join-failures",
                          target_version="3")
            )


# ------------------------------------------------------- evidence downgrade


class TestSearchEvidenceContract:
    """Review round 2, R2-6: error responses never read as empty matches, and
    hits carry the target's full body + content version via read()."""

    def _packet(self) -> dict:
        return {"candidates": [{"candidate_id": "cand-1", "statement": "join failures"}]}

    def test_error_search_response_marks_surface_unavailable(self) -> None:
        from backend.services.hermes_weknora import HermesWeKnoraClient, WeKnoraUnavailable

        client = HermesWeKnoraClient()
        client.search = lambda query, **kwargs: (_ for _ in ()).throw(
            WeKnoraUnavailable("weknora search response missing data list")
        )
        # The client itself must classify {"success": false} (no data list)
        # as unavailable rather than an empty match.
        with pytest.raises(WeKnoraUnavailable):
            import json as _json
            from unittest.mock import patch

            with patch.object(HermesWeKnoraClient, "configured", return_value=True), patch(
                "urllib.request.urlopen"
            ) as fake_open:
                fake_open.return_value.__enter__.return_value.read.return_value = (
                    _json.dumps({"success": False, "error": "boom"}).encode("utf-8")
                )
                HermesWeKnoraClient(
                    base_url="https://weknora.test", api_token="k",
                    knowledge_base_id="kb",
                ).search("join failures")

    def test_unavailable_surface_downgrades_writable_decisions(self) -> None:
        from backend.services.hermes_weknora import WeKnoraUnavailable

        class _BrokenSearch:
            def configured(self) -> bool:
                return True

            def search(self, query: str, **kwargs) -> list[dict]:
                raise WeKnoraUnavailable("weknora search response missing data list")

        knowledge_results, _memory, knowledge_ok, _mok = _collect_weknora_evidence(
            _BrokenSearch(), self._packet(), memory_client=None
        )
        assert knowledge_ok is False
        assert knowledge_results == {"cand-1": []}
        adjusted, downgraded = _downgrade_decisions_without_evidence(
            [_decision("knowledge", "new")], knowledge_available=False, memory_available=False
        )
        assert downgraded == ["cand-1"]
        assert adjusted[0]["decision"] == "human_review"

    def test_hits_carry_full_body_and_content_version(self) -> None:
        class _ReadableSearch:
            def configured(self) -> bool:
                return True

            def search(self, query: str, **kwargs) -> list[dict]:
                return [
                    {"object_id": "weknora:kb:join-failures", "title": "Join failures",
                     "snippet": "Existing entry.", "score": 0.9}
                ]

            def read(self, object_id: str) -> dict:
                assert object_id == "weknora:kb:join-failures"
                return {
                    "object_id": object_id, "title": "Join failures",
                    "content": "The complete stored body of the target entry.",
                    "content_version": "3",
                    "lineage": {"engineer_case_id": "123-1"},
                }

        knowledge_results, _memory, knowledge_ok, _mok = _collect_weknora_evidence(
            _ReadableSearch(), self._packet(), memory_client=None
        )
        assert knowledge_ok is True
        hit = knowledge_results["cand-1"][0]
        assert hit["full_content"] == "The complete stored body of the target entry."
        assert hit["content_version"] == "3"
        assert hit["target_lineage"] == {"engineer_case_id": "123-1"}

    def test_read_failure_fails_the_surface_closed(self) -> None:
        from backend.services.hermes_weknora import WeKnoraUnavailable

        class _UnreadableTarget:
            def configured(self) -> bool:
                return True

            def search(self, query: str, **kwargs) -> list[dict]:
                return [{"object_id": "weknora:kb:gone", "snippet": "partial", "score": 0.9}]

            def read(self, object_id: str) -> dict:
                raise WeKnoraUnavailable(f"weknora object {object_id} vanished")

        knowledge_results, _memory, knowledge_ok, _mok = _collect_weknora_evidence(
            _UnreadableTarget(), self._packet(), memory_client=None
        )
        assert knowledge_ok is False
        assert knowledge_results == {"cand-1": []}


class TestEvidenceDowngrade:
    def test_knowledge_write_downgraded_when_knowledge_unavailable(self) -> None:
        adjusted, downgraded = _downgrade_decisions_without_evidence(
            [_decision("knowledge", "supplement")],
            knowledge_available=False, memory_available=True,
        )
        assert adjusted[0]["decision"] == "human_review"
        assert "downgraded from supplement" in adjusted[0]["rationale"]
        assert adjusted[0].get("target_object") is None
        assert downgraded == ["cand-1"]

    def test_knowledge_write_survives_when_knowledge_available(self) -> None:
        adjusted, downgraded = _downgrade_decisions_without_evidence(
            [_decision("knowledge", "supplement")],
            knowledge_available=True, memory_available=False,
        )
        assert adjusted[0]["decision"] == "supplement"
        assert adjusted[0]["target_object"] == "weknora:kb:join-failures"
        assert downgraded == []

    def test_memory_write_requires_both_surfaces(self) -> None:
        adjusted, downgraded = _downgrade_decisions_without_evidence(
            [_decision("memory", "new")],
            knowledge_available=True, memory_available=False,
        )
        assert adjusted[0]["decision"] == "human_review"
        assert downgraded == ["cand-1"]

    def test_skill_write_always_downgraded_even_with_full_evidence(self) -> None:
        adjusted, downgraded = _downgrade_decisions_without_evidence(
            [_decision("skill", "new")],
            knowledge_available=True, memory_available=True,
        )
        assert adjusted[0]["decision"] == "human_review"
        assert "human-maintained" in adjusted[0]["rationale"]
        assert downgraded == ["cand-1"]

    def test_no_change_and_human_review_pass_through(self) -> None:
        adjusted, downgraded = _downgrade_decisions_without_evidence(
            [_decision("knowledge", "no_change"), _decision("knowledge", "human_review", target_object=None, target_version=None)],
            knowledge_available=False, memory_available=False,
        )
        assert [item["decision"] for item in adjusted] == ["no_change", "human_review"]
        assert downgraded == []


# ------------------------------------------------------------- summary bundle


class TestCompleteSummaryBundle:
    def test_bundle_contains_ticket_history_and_slack_thread(self) -> None:
        repository = _repository()
        task = repository.get_hermes_summary_task  # noqa: B018 - readability
        binding = repository.get_hermes_case_binding("123-1")
        bundle = build_case_close_bundle(
            repository,
            {
                "engineer_case_id": "123-1",
                "client_ticket_id": "123",
                "investigation_id": "INV-123-1",
                "episode": int(binding["episode"]),
                "ledger_revision": int(binding["current_ledger_revision"]),
                "conversation_version": int(binding["conversation_version"]),
                "hermes_session_id": str(binding.get("hermes_session_id") or ""),
            },
        )
        assert bundle["ticket"]["messages"][0]["external_id"] == "zd-comment-1"
        assert any(
            "region switch" in str(message.get("content") or "")
            for message in bundle["ticket"]["messages"]
        )
        assert bundle["slack_thread"]["events"], "slack thread events are missing"
        assert bundle["slack_thread"]["events"][0]["event_type"] == "engineer_case_opened"
        assert bundle["engineer_case"]["messages"] is not None
        assert bundle["authority_events"] is not None

    def test_summary_run_input_includes_full_history(self, monkeypatch) -> None:
        _enable_real_mode(monkeypatch)
        repository = _repository()
        queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")
        client = FakeHermesAgentClient(
            summary_output=_summary_output(),
            review_output=_review_output([_decision("knowledge", "human_review", target_object=None, target_version=None)]),
        )
        drain_hermes_knowledge_tasks(
            repository, client=client, weknora_client=FakeKnowledgeWeKnora(),
            memory_client=FakeMemoryWeKnora(), limit=5, sleeper=lambda _: None,
        )
        summary_input = client.started[0]["input_text"]
        assert "zd-comment-1" in summary_input
        assert "Customer cannot join channels after region switch." in summary_input
        assert "Engineer case opened for the join failure." in summary_input


# ----------------------------------------------- end-to-end downgrade behavior


class TestReviewDowngradeEndToEnd:
    def test_unavailable_evidence_downgrades_writes_to_human_review(self, monkeypatch) -> None:
        _enable_real_mode(monkeypatch)
        repository = _repository()
        queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")
        client = FakeHermesAgentClient(
            summary_output=_summary_output(),
            review_output=_review_output([_decision("knowledge", "new")]),
        )
        # No weknora_client and no memory surface: both unavailable.
        processed = drain_hermes_knowledge_tasks(
            repository, client=client, weknora_client=None,
            memory_client=FakeMemoryWeKnora(ok=False), limit=5, sleeper=lambda _: None,
        )
        assert processed == 2
        review_task = repository.list_hermes_review_tasks()[0]
        assert review_task["status"] == "completed"
        report = review_task["report"]
        assert report["weknora_available"] is False
        assert report["memory_available"] is False
        assert report["decisions"][0]["decision"] == "human_review"
        assert "downgraded from new" in report["decisions"][0]["rationale"]
        assert report["downgraded_candidate_ids"] == ["cand-1"]
        assert review_task["weknora_submissions"][0]["decision"] == "human_review"

    def test_memory_candidate_downgraded_when_memory_surface_missing(self, monkeypatch) -> None:
        _enable_real_mode(monkeypatch)
        repository = _repository()
        queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")
        client = FakeHermesAgentClient(
            summary_output=_summary_output(),
            review_output=_review_output([_decision("memory", "new")]),
        )
        drain_hermes_knowledge_tasks(
            repository, client=client, weknora_client=FakeKnowledgeWeKnora(),
            memory_client=None, limit=5, sleeper=lambda _: None,
        )
        report = repository.list_hermes_review_tasks()[0]["report"]
        assert report["weknora_available"] is True
        assert report["memory_available"] is False
        assert report["decisions"][0]["decision"] == "human_review"
        assert report["downgraded_candidate_ids"] == ["cand-1"]

    def test_skill_write_from_review_becomes_human_review(self, monkeypatch) -> None:
        _enable_real_mode(monkeypatch)
        repository = _repository()
        queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")
        client = FakeHermesAgentClient(
            summary_output=_summary_output(),
            review_output=_review_output([_decision("skill", "new")]),
        )
        drain_hermes_knowledge_tasks(
            repository, client=client, weknora_client=FakeKnowledgeWeKnora(),
            memory_client=FakeMemoryWeKnora(), limit=5, sleeper=lambda _: None,
        )
        report = repository.list_hermes_review_tasks()[0]["report"]
        assert report["weknora_available"] is True
        assert report["decisions"][0]["decision"] == "human_review"
        assert "skill candidates are human-maintained" in report["decisions"][0]["rationale"]
        assert report["downgraded_candidate_ids"] == ["cand-1"]

    def test_memory_evidence_is_queried_and_included_in_bundle(self, monkeypatch) -> None:
        _enable_real_mode(monkeypatch)
        repository = _repository()
        queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")
        memory = FakeMemoryWeKnora()
        client = FakeHermesAgentClient(
            summary_output=_summary_output(),
            review_output=_review_output([_decision("knowledge", "supplement")]),
        )
        drain_hermes_knowledge_tasks(
            repository, client=client, weknora_client=FakeKnowledgeWeKnora(),
            memory_client=memory, limit=5, sleeper=lambda _: None,
        )
        assert memory.list_calls, "memory surface was never queried"
        review_input = client.started[1]["input_text"]
        assert "memory_results" in review_input
        report = repository.list_hermes_review_tasks()[0]["report"]
        assert report["memory_available"] is True
        assert report["decisions"][0]["decision"] == "supplement"


# ------------------------------------------------------- Slack history contract


class _SlackBrokenRepository(InMemoryTicketRepository):
    """Reads for the per-case Slack listing blow up (storage failure)."""

    def list_engineer_slack_events_for_case(self, engineer_case_id, *, limit: int = 500):
        raise RuntimeError("slack store unavailable")


class TestSlackHistoryContract:
    def test_bundle_lineage_slack_ids_come_from_binding(self) -> None:
        repository = _repository()
        binding = repository.get_hermes_case_binding("123-1")
        bundle = build_case_close_bundle(
            repository,
            {
                "engineer_case_id": "123-1",
                "client_ticket_id": "123",
                "investigation_id": "INV-123-1",
                "episode": int(binding["episode"]),
                "ledger_revision": int(binding["current_ledger_revision"]),
                "conversation_version": int(binding["conversation_version"]),
                "hermes_session_id": str(binding.get("hermes_session_id") or ""),
            },
        )
        # The engineer-case payload carries no Slack ids; the delivered thread
        # binding is the authoritative source.
        assert bundle["lineage"]["slack_channel_id"] == "C123"
        assert bundle["lineage"]["slack_thread_ts"] == "1693900800.001"
        assert bundle["slack_thread"]["slack_channel_id"] == "C123"
        assert bundle["slack_thread"]["events"][0]["event_type"] == "engineer_case_opened"

    def test_slack_read_failure_fails_summary_visibly(self, monkeypatch) -> None:
        _enable_real_mode(monkeypatch)
        repository = _SlackBrokenRepository()
        # Move the standard fixture's state into the broken repository.
        source = _repository()
        repository.__dict__.update(source.__dict__)
        queue_hermes_summary_for_case(repository, engineer_case_id="123-1", trigger="solved")
        client = FakeHermesAgentClient(summary_output=_summary_output(), review_output="")
        drain_hermes_knowledge_tasks(
            repository, client=client, weknora_client=None, limit=5, sleeper=lambda _: None,
        )
        task = repository.get_hermes_summary_task(summary_task_id_for("123-1", 1))
        assert task["status"] == "failed"
        assert task["error_code"] == "slack_history_unavailable"
        assert repository.list_hermes_review_tasks() == []

    def test_per_case_history_overflow_fails_instead_of_truncating(self, monkeypatch) -> None:
        import backend.services.hermes_knowledge_workflow as workflow

        monkeypatch.setattr(workflow, "KNOWLEDGE_SLACK_HISTORY_LIMIT", 2)
        repository = _repository()
        record = repository.get_engineer_case("123-1", include_client_messages=False) or {}
        repository.save_engineer_case(
            dict(record),
            slack_events=[
                build_engineer_case_thread_event(
                    event_id="engineer-slack:123-1:note-1",
                    event_type="engineer_note",
                    engineer_case_id="123-1",
                    message_text="Second event.",
                ),
                build_engineer_case_thread_event(
                    event_id="engineer-slack:123-1:note-2",
                    event_type="engineer_note",
                    engineer_case_id="123-1",
                    message_text="Third event.",
                ),
            ],
        )
        binding = repository.get_hermes_case_binding("123-1")
        with pytest.raises(workflow.KnowledgeWorkflowError) as excinfo:
            build_case_close_bundle(
                repository,
                {
                    "engineer_case_id": "123-1",
                    "client_ticket_id": "123",
                    "investigation_id": "INV-123-1",
                    "episode": int(binding["episode"]),
                    "ledger_revision": int(binding["current_ledger_revision"]),
                    "conversation_version": int(binding["conversation_version"]),
                    "hermes_session_id": str(binding.get("hermes_session_id") or ""),
                },
            )
        assert excinfo.value.code == "slack_history_truncated"

    def test_per_case_query_is_not_truncated_by_other_cases(self) -> None:
        repository = _repository()
        # Flood a DIFFERENT case far past any global cap: the per-case query
        # must still see this case's own events in full.
        repository.save_engineer_case(
            {
                "engineer_case_id": "999-1",
                "client_ticket_id": "999",
                "case_sequence": 1,
                "title": "Flood",
                "status": "investigating",
                "trigger_source": "account_not_automated",
                "trigger_reason": "technical",
                "thread_id": "INV-999-1",
                "investigation_state": "active",
                "opened_at": "2026-09-05T08:00:00Z",
                "updated_at": "2026-09-05T08:00:00Z",
                "messages": [],
            },
            slack_events=[
                build_engineer_case_thread_event(
                    event_id=f"engineer-slack:999-1:event-{index}",
                    event_type="engineer_note",
                    engineer_case_id="999-1",
                    message_text=f"noise {index}",
                )
                for index in range(700)
            ],
        )
        listing = repository.list_engineer_slack_events_for_case("123-1", limit=500)
        assert listing["truncated"] is False
        assert [event["event_id"] for event in listing["events"]] == [
            "engineer-slack:123-1:open"
        ]
        other_listing = repository.list_engineer_slack_events_for_case("999-1", limit=500)
        assert other_listing["truncated"] is True
        assert len(other_listing["events"]) == 500


# ------------------------------------------------------- WeKnora write lineage


class RecordingWeKnoraClient:
    """Fake honoring the proven-readback contract: reads return exactly what
    the last write stored, so successful writes verify as accepted. Declares
    conditional-update support like a probed official contract would."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.written: dict[str, dict] = {}

    def has_memory_identity(self) -> bool:
        return True

    def supports_conditional_update(self, candidate_type: str) -> bool:
        return True

    def knowledge_read(self, *, object_id: str) -> dict:
        self.calls.append(("knowledge_read", {"object_id": object_id}))
        if object_id in self.written:
            stored = self.written[object_id]
            return {
                "object_id": object_id,
                "version": stored["version"],
                "title": stored["title"],
                "content": stored["content"],
            }
        return {"object_id": object_id, "version": "5", "title": "Existing",
                "content": "existing content"}

    def knowledge_create(self, **kwargs) -> dict:
        self.calls.append(("knowledge_create", dict(kwargs)))
        object_id = "doc-new-1"
        self.written[object_id] = {
            "version": "1", "title": kwargs["title"], "content": kwargs["content"],
        }
        return {"object_id": object_id, "version": "1", "receipt": {"ok": True}}

    def knowledge_update(self, **kwargs) -> dict:
        self.calls.append(("knowledge_update", dict(kwargs)))
        object_id = str(kwargs["object_id"])
        stored = self.written.get(object_id) or {"version": "5"}
        version = str(int(stored["version"]) + 1)
        self.written[object_id] = {
            "version": version, "title": kwargs["title"], "content": kwargs["content"],
        }
        return {"object_id": object_id, "version": version, "receipt": {"ok": True}}


LINEAGE_TASK = {
    "promotion_id": "weknora:hermes_case_promotion:123-1:1:3:knowledge",
    "engineer_case_id": "123-1",
    "client_ticket_id": "123",
    "investigation_id": "INV-123-1",
    "summary_session_id": "hermes-session:summary",
    "summary_run_id": "run-summary-1",
    "review_session_id": "hermes-session:review",
    "review_run_id": "run-review-1",
    "slack_channel_id": "C123",
    "slack_thread_ts": "1693900800.001",
    "source_type": "hermes_case_promotion",
    "source_id": "123-1:1",
    "source_version": "3",
    "content_hash": "a" * 64,
    "candidate_type": "knowledge",
    "decision": "new",
    "candidate_payload": {
        "schema_version": "v1",
        "candidate_type": "knowledge",
        "decision": "new",
        "title": "Join failures",
        "content": "A misconfigured region causes join failures.",
    },
}


class TestWeKnoraWriteLineageMetadata:
    @staticmethod
    def _write_call(client: RecordingWeKnoraClient, name: str) -> dict:
        matches = [kwargs for call_name, kwargs in client.calls if call_name == name]
        assert matches, f"no {name} call was issued"
        return matches[-1]

    def test_adapter_sends_full_lineage_metadata_on_create(self) -> None:
        client = RecordingWeKnoraClient()
        task = normalize_weknora_promotion_task(
            LINEAGE_TASK, now_value="2026-09-30T10:00:00+00:00",
        )
        outcome = WeKnoraPromotionAdapter(client).execute(task)
        assert outcome.status == "accepted"
        kwargs = self._write_call(client, "knowledge_create")
        metadata = kwargs["metadata"]
        assert metadata["engineer_case_id"] == "123-1"
        assert metadata["client_ticket_id"] == "123"
        assert metadata["slack_channel_id"] == "C123"
        assert metadata["slack_thread_ts"] == "1693900800.001"
        assert metadata["summary_session_id"] == "hermes-session:summary"
        assert metadata["review_run_id"] == "run-review-1"
        assert metadata["source_version"] == "3"
        assert metadata["promotion_id"].startswith("weknora:hermes_case_promotion")

    def test_adapter_sends_lineage_metadata_on_update(self) -> None:
        client = RecordingWeKnoraClient()
        task = normalize_weknora_promotion_task(
            {
                **LINEAGE_TASK,
                "decision": "replace",
                "candidate_payload": {
                    "schema_version": "v1",
                    "candidate_type": "knowledge",
                    "decision": "replace",
                    "content": "Corrected full content.",
                    "target_object_id": "doc-1",
                    "base_version": "5",
                },
            },
            now_value="2026-09-30T10:00:00+00:00",
        )
        outcome = WeKnoraPromotionAdapter(client).execute(task)
        assert outcome.status == "accepted"
        kwargs = self._write_call(client, "knowledge_update")
        assert kwargs["metadata"]["engineer_case_id"] == "123-1"
        assert kwargs["metadata"]["decision"] == "replace"


class TestClientSendsMetadataOnUpdates:
    CONTRACT = {
        "health": {"method": "GET", "path": "/health"},
        "knowledge_update": {"method": "POST", "path": "/api/kb/update"},
        "memory_create": {"method": "POST", "path": "/api/memory/create"},
        "memory_update": {"method": "POST", "path": "/api/memory/update"},
    }

    def test_update_bodies_carry_lineage_metadata(self) -> None:
        import io
        from unittest.mock import patch

        client = WeKnoraClient(
            base_url="http://weknora.test", api_token="synthetic-token",
            contract=self.CONTRACT, knowledge_base_id="kb-1",
            memory_identity="hermes-shared",
        )
        captured: dict[str, dict] = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            payload = json.dumps({"object_id": "doc-1", "version": "6"}).encode("utf-8")

            class _Resp(io.BytesIO):
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return _Resp(payload)

        metadata = {"engineer_case_id": "123-1", "client_ticket_id": "123"}
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.knowledge_update(
                object_id="doc-1", base_version="5", title="T", content="C",
                idempotency_key="k", metadata=metadata,
            )
        assert captured["body"]["metadata"] == metadata
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.memory_update(
                object_id="mem-1", base_version="1", content="C",
                idempotency_key="k", metadata=metadata,
            )
        assert captured["body"]["metadata"] == metadata
        assert captured["body"]["user_id"] == "hermes-shared"
