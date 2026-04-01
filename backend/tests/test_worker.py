from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

if importlib.util.find_spec("psycopg") is None:
    raise unittest.SkipTest("psycopg is not installed in the local test environment")

import psycopg

if importlib.util.find_spec("redis") is None:
    redis_module = types.ModuleType("redis")
    redis_asyncio_module = types.ModuleType("redis.asyncio")

    class _FakeRedis:
        @classmethod
        def from_url(cls, *_args: object, **_kwargs: object) -> "_FakeRedis":
            return cls()

        def publish(self, *_args: object, **_kwargs: object) -> int:
            return 1

        def blpop(self, *_args: object, **_kwargs: object) -> None:
            return None

        def rpush(self, *_args: object, **_kwargs: object) -> int:
            return 1

        def close(self) -> None:
            return None

        async def aclose(self) -> None:
            return None

    redis_module.Redis = _FakeRedis
    redis_asyncio_module.Redis = _FakeRedis
    sys.modules["redis"] = redis_module
    sys.modules["redis.asyncio"] = redis_asyncio_module


def _load_worker_module():
    module_path = Path(__file__).resolve().parents[1] / "worker.py"
    spec = importlib.util.spec_from_file_location(
        "backend.tests._worker_under_test",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load backend.worker for tests")

    fake_main = types.ModuleType("backend.main")
    fake_main.build_answer = lambda *_args, **_kwargs: ("", 0.0, [], [], False)
    fake_main.resolve_support_message = lambda *_args, **_kwargs: None
    fake_main.build_client_sync_event = lambda *_args, **_kwargs: {}
    fake_main.build_engineer_followup_request = lambda *_args, **_kwargs: "follow up"
    fake_main.ensure_ticket_defaults = lambda _ticket: None
    fake_main.now_iso = lambda: "2026-03-22T00:00:00+00:00"
    fake_main.ticket_repository = Mock()

    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"backend.main": fake_main}):
        spec.loader.exec_module(module)
    return module


worker = _load_worker_module()


def _route_decision(*, action: str, scope_label: str, reason: str) -> types.SimpleNamespace:
    route_family = "agora_docs_rag" if action == "rag" else "web_company_info" if action == "web_search" else "fallback_or_refuse"
    tooling_profile = "agora_docs_only" if action == "rag" else "official_web_search" if action == "web_search" else "no_agora_docs_refusal"
    return types.SimpleNamespace(
        scope_label=scope_label,
        route=action,
        confidence=0.93,
        reason=reason,
        matched_signals=["token"] if action == "rag" else ["agora"],
        response_language="en",
        route_family=route_family,
        execution_action=action,
        tooling_profile=tooling_profile,
    )


def _build_ticket(
    *,
    ticket_id: str = "T-RETRY",
    customer_message: str = "Need help with token generation",
    message_created_at: str = "2026-03-22T00:00:00+00:00",
) -> dict[str, object]:
    return {
        "ticket_id": ticket_id,
        "customer_id": "C-123",
        "requester": "Customer",
        "subject": "Token question",
        "status": "communicating",
        "priority": "normal",
        "created_at": "2026-03-22T00:00:00+00:00",
        "updated_at": "2026-03-22T00:00:00+00:00",
        "messages": [
            {
                "role": "customer",
                "content": customer_message,
                "created_at": message_created_at,
            },
            {
                "role": "assistant",
                "content": "I am checking the knowledge base for you now.",
                "created_at": "2026-03-22T00:00:01+00:00",
            },
        ],
    }


class WorkerResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = {
            "task_type": "ticket_query",
            "ticket_id": "T-RETRY",
            "customer_message": "Need help with token generation",
            "message_created_at": "2026-03-22T00:00:00+00:00",
            "created_at": "2026-03-22T00:00:01+00:00",
        }

    def test_process_ticket_query_retries_transient_save_ticket_failure(self) -> None:
        initial_ticket = _build_ticket()
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(initial_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.side_effect = [
            psycopg.OperationalError("connection timeout expired"),
            None,
        ]
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use the Node.js token builder sample.",
            confidence=0.91,
            sources=["official/deploy-token-server.md"],
            citations=[{"source": "official/deploy-token-server.md", "label": "Deploy a token server"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="docs_match",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["token", "node.js"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:00+00:00",
        ), patch.object(
            worker,
            "TICKET_REPOSITORY_RETRY_MAX",
            1,
        ), patch.object(
            worker,
            "TICKET_REPOSITORY_RETRY_BASE_DELAY_SECONDS",
            0.05,
        ), patch.object(
            worker.time,
            "sleep",
        ) as sleep_mock:
            worker._process_ticket_query(bus, dict(self.task))

        self.assertEqual(repository.save_ticket.call_count, 2)
        saved_ticket = repository.save_ticket.call_args_list[-1].args[0]
        self.assertEqual(saved_ticket["messages"][-1]["content"], "Use the Node.js token builder sample.")
        self.assertEqual(repository.record_event.call_count, 1)
        sleep_mock.assert_any_call(0.05)

    def test_schedule_ticket_task_retry_reenqueues_retryable_db_failure(self) -> None:
        queue = Mock()
        queue.enqueue.return_value = True

        with patch.object(worker, "TICKET_TASK_RETRY_MAX", 2), patch.object(
            worker,
            "TICKET_TASK_RETRY_BASE_DELAY_SECONDS",
            0.5,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:02:00+00:00",
        ), patch.object(worker.time, "sleep") as sleep_mock:
            scheduled = worker._schedule_ticket_task_retry(
                queue,
                dict(self.task),
                psycopg.OperationalError("server closed the connection unexpectedly"),
            )

        self.assertTrue(scheduled)
        queue.enqueue.assert_called_once()
        retry_task = queue.enqueue.call_args.args[0]
        self.assertEqual(retry_task["worker_retry_count"], 1)
        self.assertEqual(retry_task["last_retry_at"], "2026-03-22T00:02:00+00:00")
        self.assertIn("server closed the connection unexpectedly", retry_task["last_error"])
        sleep_mock.assert_called_once_with(0.5)

    def test_process_ticket_query_skips_duplicate_final_response_after_requeue(self) -> None:
        initial_ticket = _build_ticket()
        refreshed_ticket = _build_ticket()
        refreshed_ticket["messages"].append(
            {
                "role": "assistant",
                "content": "Use the Node.js token builder sample.",
                "created_at": "2026-03-22T00:01:00+00:00",
                "sources": ["official/deploy-token-server.md"],
                "citations": [
                    {
                        "source": "official/deploy-token-server.md",
                        "label": "Deploy a token server",
                    }
                ],
            }
        )
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(refreshed_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()
        execution = types.SimpleNamespace(
            answer="Use the Node.js token builder sample.",
            confidence=0.91,
            sources=["official/deploy-token-server.md"],
            citations=[{"source": "official/deploy-token-server.md", "label": "Deploy a token server"}],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="docs_match",
            route_confidence=0.91,
            search_used=False,
            matched_signals=["token", "node.js"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=False,
            next_status="communicating",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task))

        repository.save_ticket.assert_not_called()

    def test_process_ticket_query_persists_route_metadata_without_calling_legacy_build_answer(self) -> None:
        initial_ticket = _build_ticket()
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(initial_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.record_event.return_value = None
        bus = Mock()

        resolution = types.SimpleNamespace(
            answer="Agora's CEO is Tony Zhao.",
            confidence=0.93,
            sources=["https://www.agora.io/en/about-agora/"],
            citations=[
                {
                    "source_url": "https://www.agora.io/en/about-agora/",
                    "heading": "About Agora",
                    "source_path": "https://www.agora.io/en/about-agora/",
                }
            ],
            needs_engineer_guidance=False,
            answer_route="web_search",
            scope_label="agora_non_technical",
            route_reason="agora_public_info",
            route_confidence=0.93,
            search_used=True,
            matched_signals=["agora", "ceo"],
            route_family="web_company_info",
            execution_action="web_search",
            tooling_profile="official_web_search",
            needs_investigating=False,
            next_status="communicating",
        )

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=resolution,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task))

        saved_ticket = repository.save_ticket.call_args.args[0]
        assistant_message = saved_ticket["messages"][-1]
        self.assertEqual(saved_ticket["status"], "communicating")
        self.assertEqual(assistant_message["answer_route"], "web_search")
        self.assertEqual(assistant_message["scope_label"], "agora_non_technical")
        self.assertTrue(assistant_message["search_used"])
        event_payload = repository.record_event.call_args.args[2]
        self.assertEqual(event_payload["status"], "communicating")
        self.assertEqual(event_payload["answer_route"], "web_search")
        self.assertEqual(event_payload["scope_label"], "agora_non_technical")
        self.assertNotIn("engineer_mode", event_payload)

    def test_process_ticket_query_post_check_rejection_starts_investigation(self) -> None:
        initial_ticket = _build_ticket()
        repository = Mock()
        repository.get_ticket.side_effect = [
            copy.deepcopy(initial_ticket),
            copy.deepcopy(initial_ticket),
        ]
        repository.list_ticket_events.return_value = []
        repository.save_ticket.return_value = None
        repository.save_investigation.return_value = None
        repository.record_event.return_value = None
        bus = Mock()

        execution = types.SimpleNamespace(
            answer="Please upgrade to SDK 4.2.2 and retry token renewal.",
            confidence=0.86,
            sources=["https://docs.agora.io/en/video-calling/token-authentication"],
            citations=[
                {
                    "chunk_id": "chunk-1",
                    "source_path": "official/token-authentication.md",
                    "heading": "Token authentication",
                    "source_url": "https://docs.agora.io/en/video-calling/token-authentication",
                }
            ],
            needs_engineer_guidance=False,
            answer_route="rag",
            scope_label="agora_technical",
            route_reason="grounded_answer",
            route_confidence=0.93,
            search_used=False,
            matched_signals=["token", "android 14"],
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
            needs_investigating=True,
            next_status="investigating",
            investigation_reason="rag_post_check_insufficient",
        )

        investigation_result = {
            "created": True,
            "public_reply": "I've opened an engineer ticket for this issue and we're investigating further. I'll reply here as soon as the engineer review is confirmed.",
            "active_investigation": {
                "id": "INV-RETRY-1",
                "state": "active",
                "trigger_reason": "rag_post_check_insufficient",
                "trigger_source": "worker_async_rag",
                "messages": [
                    {
                        "id": "INV-RETRY-1-m1",
                        "role": "engineer_ai",
                        "content": "Please confirm whether Android 14 is the only affected platform.",
                        "created_at": "2026-03-22T00:01:05+00:00",
                    }
                ],
            },
            "new_internal_messages": [],
        }

        def _start_or_refresh(ticket, **_kwargs):
            ticket["status"] = "investigating"
            ticket["active_investigation"] = copy.deepcopy(investigation_result["active_investigation"])
            return copy.deepcopy(investigation_result)

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "_orchestrate_worker_support_message",
            return_value=execution,
        ), patch.object(
            worker,
            "start_or_refresh_investigation",
            side_effect=_start_or_refresh,
        ), patch.object(
            worker,
            "build_client_sync_event",
            return_value={"event": "ticket_ai_response_ready"},
        ), patch.object(
            worker,
            "ensure_ticket_defaults",
            side_effect=lambda ticket: None,
        ), patch.object(
            worker,
            "now_iso",
            return_value="2026-03-22T00:01:05+00:00",
        ):
            worker._process_ticket_query(bus, dict(self.task))

        saved_ticket = repository.save_ticket.call_args.args[0]
        self.assertEqual(saved_ticket["status"], "investigating")
        self.assertEqual(saved_ticket["messages"][-1]["content"], investigation_result["public_reply"])
        self.assertEqual(repository.save_investigation.call_count, 1)
        first_event = repository.record_event.call_args_list[0].args[2]
        self.assertEqual(first_event["status"], "investigating")
        self.assertEqual(first_event["execution_action"], "rag")

    def test_process_ticket_message_sentiment_persists_label_and_records_event(self) -> None:
        repository = Mock()
        repository.get_ticket.return_value = copy.deepcopy(_build_ticket())
        repository.update_message_sentiment_label.return_value = True
        repository.record_event.return_value = None
        bus = Mock()
        task = {
            "task_type": "ticket_message_sentiment",
            "ticket_id": "T-RETRY",
            "customer_message": "Need help with token generation",
            "message_created_at": "2026-03-22T00:00:00+00:00",
            "created_at": "2026-03-22T00:00:01+00:00",
        }

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "classify_sentiment",
            return_value=types.SimpleNamespace(
                bucket="negative",
                raw_label="anger",
                confidence=0.91,
                provider="test",
            ),
        ), patch.object(worker, "now_iso", return_value="2026-03-22T00:03:00+00:00"), patch.object(
            worker,
            "_publish",
        ) as publish_mock:
            worker._process_ticket_message_sentiment(bus, task)

        repository.update_message_sentiment_label.assert_called_once_with(
            ticket_id="T-RETRY",
            role="customer",
            content="Need help with token generation",
            created_at="2026-03-22T00:00:00+00:00",
            sentiment_label="bad",
        )
        event_payload = repository.record_event.call_args.args[2]
        self.assertEqual(event_payload["event"], "ticket_message_sentiment_tagged")
        self.assertEqual(event_payload["sentiment_label"], "bad")
        publish_mock.assert_called_once()

    def test_process_ticket_message_sentiment_skips_when_customer_message_cannot_be_updated(self) -> None:
        repository = Mock()
        repository.get_ticket.return_value = copy.deepcopy(_build_ticket())
        repository.update_message_sentiment_label.return_value = False
        repository.record_event.return_value = None
        bus = Mock()
        task = {
            "task_type": "ticket_message_sentiment",
            "ticket_id": "T-RETRY",
            "customer_message": "Need help with token generation",
            "message_created_at": "2026-03-22T00:00:00+00:00",
            "created_at": "2026-03-22T00:00:01+00:00",
        }

        with patch.object(worker, "ticket_repository", repository), patch.object(
            worker,
            "classify_sentiment",
            return_value=types.SimpleNamespace(
                bucket="neutral",
                raw_label="neutral",
                confidence=0.51,
                provider="test",
            ),
        ), patch.object(worker, "_publish") as publish_mock:
            worker._process_ticket_message_sentiment(bus, task)

        repository.record_event.assert_not_called()
        publish_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
