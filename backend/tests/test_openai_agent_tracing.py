from __future__ import annotations

import types
import unittest
from unittest.mock import patch


class _FakeSpan:
    def __init__(self, kind: str, payload: dict[str, object], sink: list[dict[str, object]]) -> None:
        self.kind = kind
        self.payload = payload
        self.sink = sink

    def __enter__(self):
        self.sink.append({"kind": self.kind, "phase": "enter", "payload": dict(self.payload)})
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.sink.append({"kind": self.kind, "phase": "exit", "payload": dict(self.payload)})
        return False


class _FakeTrace(_FakeSpan):
    @property
    def trace_id(self) -> str | None:
        value = self.payload.get("trace_id")
        return str(value) if value is not None else None


class OpenAiAgentTracingTests(unittest.TestCase):
    def _fake_sdk(self, sink: list[dict[str, object]]) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            trace=lambda workflow_name, trace_id=None, group_id=None, metadata=None: _FakeTrace(
                "trace",
                {
                    "workflow_name": workflow_name,
                    "trace_id": trace_id,
                    "group_id": group_id,
                    "metadata": dict(metadata or {}),
                },
                sink,
            ),
            function_span=lambda name, input=None, output=None: _FakeSpan(
                "function",
                {"name": name, "input": input, "output": output},
                sink,
            ),
            generation_span=lambda input=None, output=None, model=None, model_config=None, usage=None: _FakeSpan(
                "generation",
                {
                    "input": list(input or []),
                    "output": list(output or []),
                    "model": model,
                    "model_config": dict(model_config or {}),
                    "usage": dict(usage or {}),
                },
                sink,
            ),
            guardrail_span=lambda name, triggered=False: _FakeSpan(
                "guardrail",
                {"name": name, "triggered": triggered},
                sink,
            ),
            custom_span=lambda name, data=None: _FakeSpan(
                "custom",
                {"name": name, "data": dict(data or {})},
                sink,
            ),
        )

    def test_start_review_trace_exposes_current_ref_and_records_spans(self) -> None:
        from backend.services import openai_agent_tracing as tracing

        events: list[dict[str, object]] = []
        fake_uuid = types.SimpleNamespace(hex="1234567890abcdef1234567890abcdef")

        with patch("backend.services.openai_agent_tracing._load_agents_sdk", return_value=self._fake_sdk(events)), patch(
            "backend.services.openai_agent_tracing.uuid4",
            return_value=fake_uuid,
        ):
            with tracing.start_review_trace(
                run_id="run-123",
                ticket_id="TK-TRACE-1",
                message_id="2026-04-04T00:00:00+00:00",
                product="audio_video_calling",
                mode="grounded_postcheck",
                route_reason="grounded_answer",
            ) as trace_context:
                trace_ref = trace_context.as_trace_ref()
                self.assertEqual(trace_ref["trace_id"], "trace_1234567890abcdef1234567890abcdef")
                self.assertEqual(trace_ref["group_id"], "run-123")
                self.assertEqual(trace_ref["workflow_name"], "supportportal.review_agent.grounded_postcheck")
                self.assertEqual(trace_ref["mode"], "grounded_postcheck")
                self.assertEqual(tracing.current_trace_ref(), trace_ref)
                with trace_context.function_span("review_agent.grounded_postcheck", input="review payload"):
                    pass
                tracing.record_generation_span(
                    system_prompt="system",
                    user_prompt="user",
                    response_text="approved",
                    model_name="gpt-5.4",
                    reasoning_effort="medium",
                    temperature=0.0,
                    prompt_tokens=12,
                    completion_tokens=4,
                )
                tracing.record_guardrail_span(
                    name="troubleshooting_intake.output_contract",
                    triggered=False,
                    data={"status": "ok"},
                )
                tracing.record_custom_span(
                    name="review_agent.outcome",
                    data={"decision": "approve_answer"},
                )

        self.assertIsNone(tracing.current_trace_ref())
        trace_event = next(item for item in events if item["kind"] == "trace" and item["phase"] == "enter")
        self.assertEqual(trace_event["payload"]["metadata"]["ticket_id"], "TK-TRACE-1")
        self.assertEqual(trace_event["payload"]["metadata"]["message_id"], "2026-04-04T00:00:00+00:00")
        self.assertEqual(trace_event["payload"]["metadata"]["product"], "audio_video_calling")
        self.assertEqual(trace_event["payload"]["metadata"]["route_reason"], "grounded_answer")
        generation_event = next(item for item in events if item["kind"] == "generation" and item["phase"] == "enter")
        self.assertEqual(generation_event["payload"]["model"], "gpt-5.4")
        self.assertEqual(generation_event["payload"]["usage"]["input_tokens"], 12)
        self.assertEqual(generation_event["payload"]["output"][0]["content"][0]["text"], "approved")
        guardrail_event = next(item for item in events if item["kind"] == "guardrail" and item["phase"] == "enter")
        self.assertFalse(guardrail_event["payload"]["triggered"])
        custom_event = next(item for item in events if item["kind"] == "custom" and item["phase"] == "enter")
        self.assertEqual(custom_event["payload"]["name"], "review_agent.outcome")

    def test_start_review_trace_without_sdk_is_noop(self) -> None:
        from backend.services import openai_agent_tracing as tracing

        with patch("backend.services.openai_agent_tracing._load_agents_sdk", return_value=None):
            with tracing.start_review_trace(
                run_id="run-456",
                ticket_id="TK-TRACE-2",
                message_id="2026-04-04T01:00:00+00:00",
                product="audio_video_calling",
                mode="rag_insufficient_evidence",
                route_reason="rag_unavailable",
            ) as trace_context:
                self.assertIsNone(trace_context.as_trace_ref())
                self.assertIsNone(tracing.current_trace_ref())
                with trace_context.function_span("review_agent.rag_insufficient_evidence"):
                    pass
                tracing.record_generation_span(
                    system_prompt="system",
                    user_prompt="user",
                    response_text="fallback",
                    model_name="gpt-5.4-mini",
                    reasoning_effort="low",
                    temperature=0.1,
                    prompt_tokens=3,
                    completion_tokens=2,
                )
                tracing.record_guardrail_span(
                    name="troubleshooting_intake.output_contract",
                    triggered=True,
                    data={"status": "invalid_json"},
                )
                tracing.record_custom_span(
                    name="review_agent.outcome",
                    data={"decision": "clarify_customer_for_intake"},
                )

        self.assertIsNone(tracing.current_trace_ref())
