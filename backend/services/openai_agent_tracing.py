from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
import importlib
import logging
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

LOGGER = logging.getLogger(__name__)

_CURRENT_TRACE_REF: ContextVar[dict[str, str] | None] = ContextVar("openai_agent_tracing_trace_ref", default=None)
_AGENTS_SDK: SimpleNamespace | None = None
_AGENTS_SDK_ATTEMPTED = False


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _new_trace_id() -> str:
    return f"trace_{uuid4().hex}"


def _load_agents_sdk() -> SimpleNamespace | None:
    global _AGENTS_SDK, _AGENTS_SDK_ATTEMPTED
    if _AGENTS_SDK_ATTEMPTED:
        return _AGENTS_SDK
    _AGENTS_SDK_ATTEMPTED = True

    namespace_candidates: list[Any] = []
    try:
        agents_module = importlib.import_module("agents")
    except ModuleNotFoundError:
        _AGENTS_SDK = None
        return None

    namespace_candidates.append(agents_module)
    tracing_module = getattr(agents_module, "tracing", None)
    if tracing_module is not None:
        namespace_candidates.append(tracing_module)
    try:
        namespace_candidates.append(importlib.import_module("agents.tracing.create"))
    except ModuleNotFoundError:
        pass

    resolved: dict[str, Any] = {}
    for name in ("trace", "function_span", "generation_span", "guardrail_span", "custom_span"):
        candidate = next(
            (
                getattr(namespace, name)
                for namespace in namespace_candidates
                if getattr(namespace, name, None) is not None
            ),
            None,
        )
        if candidate is None:
            LOGGER.warning("OpenAI Agents SDK tracing helper %s is unavailable; tracing layer disabled.", name)
            _AGENTS_SDK = None
            return None
        resolved[name] = candidate
    _AGENTS_SDK = SimpleNamespace(**resolved)
    return _AGENTS_SDK


def current_trace_ref() -> dict[str, str] | None:
    current = _CURRENT_TRACE_REF.get()
    return dict(current) if isinstance(current, dict) else None


@contextmanager
def _noop_span():
    yield


@dataclass
class ReviewTraceContext:
    sdk: SimpleNamespace | None
    trace_context: Any | None
    trace_ref: dict[str, str] | None
    _token: Token | None = None

    def __enter__(self) -> "ReviewTraceContext":
        if self.trace_context is not None:
            self.trace_context.__enter__()
            trace_id = _clean_text(getattr(self.trace_context, "trace_id", None))
            if trace_id and isinstance(self.trace_ref, dict):
                self.trace_ref["trace_id"] = trace_id
        if isinstance(self.trace_ref, dict):
            self._token = _CURRENT_TRACE_REF.set(dict(self.trace_ref))
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._token is not None:
            _CURRENT_TRACE_REF.reset(self._token)
            self._token = None
        if self.trace_context is not None:
            return bool(self.trace_context.__exit__(exc_type, exc, tb))
        return False

    def as_trace_ref(self) -> dict[str, str] | None:
        return dict(self.trace_ref) if isinstance(self.trace_ref, dict) else None

    def function_span(
        self,
        name: str,
        *,
        input: str | None = None,
        output: str | None = None,
    ):
        if self.sdk is None or not isinstance(self.trace_ref, dict):
            return _noop_span()
        try:
            return self.sdk.function_span(name=name, input=input, output=output)
        except Exception:
            LOGGER.exception("Failed to create OpenAI function span %s.", name)
            return _noop_span()

    def record_custom_span(self, name: str, data: dict[str, Any] | None = None) -> None:
        record_custom_span(name=name, data=data)


def start_review_trace(
    *,
    run_id: str,
    ticket_id: str | None,
    message_id: str | None,
    product: str | None,
    mode: str,
    route_reason: str | None,
) -> ReviewTraceContext:
    sdk = _load_agents_sdk()
    if sdk is None:
        return ReviewTraceContext(sdk=None, trace_context=None, trace_ref=None)

    normalized_mode = _clean_text(mode)
    workflow_name = f"supportportal.review_agent.{normalized_mode or 'unknown'}"
    trace_ref = {
        "trace_id": _new_trace_id(),
        "group_id": _clean_text(run_id),
        "workflow_name": workflow_name,
        "mode": normalized_mode,
    }
    metadata = {
        "run_id": _clean_text(run_id) or None,
        "ticket_id": _clean_text(ticket_id) or None,
        "message_id": _clean_text(message_id) or None,
        "product": _clean_text(product) or None,
        "review_mode": normalized_mode or None,
        "route_reason": _clean_text(route_reason) or None,
    }
    metadata = {key: value for key, value in metadata.items() if value is not None}
    try:
        trace_context = sdk.trace(
            workflow_name,
            trace_id=trace_ref["trace_id"],
            group_id=trace_ref["group_id"] or None,
            metadata=metadata,
        )
    except Exception:
        LOGGER.exception("Failed to start OpenAI review trace for mode=%s.", normalized_mode or "unknown")
        return ReviewTraceContext(sdk=None, trace_context=None, trace_ref=None)
    return ReviewTraceContext(sdk=sdk, trace_context=trace_context, trace_ref=trace_ref)


def record_generation_span(
    *,
    system_prompt: str,
    user_prompt: str,
    response_text: str,
    model_name: str,
    reasoning_effort: str | None,
    temperature: float | None,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    if current_trace_ref() is None:
        return
    sdk = _load_agents_sdk()
    if sdk is None:
        return
    model_config = {
        "reasoning_effort": _clean_text(reasoning_effort) or None,
        "temperature": temperature,
    }
    model_config = {key: value for key, value in model_config.items() if value is not None}
    usage = {
        "input_tokens": int(prompt_tokens or 0),
        "output_tokens": int(completion_tokens or 0),
    }
    total_tokens = usage["input_tokens"] + usage["output_tokens"]
    if total_tokens:
        usage["total_tokens"] = total_tokens
    try:
        with sdk.generation_span(
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": str(system_prompt or "")}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": str(user_prompt or "")}],
                },
            ],
            output=[
                {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": str(response_text or "")}],
                }
            ],
            model=_clean_text(model_name) or None,
            model_config=model_config or None,
            usage=usage,
        ):
            pass
    except Exception:
        LOGGER.exception("Failed to record OpenAI generation span for model=%s.", _clean_text(model_name) or "unknown")


def record_guardrail_span(
    *,
    name: str,
    triggered: bool,
    data: dict[str, Any] | None = None,
) -> None:
    del data
    if current_trace_ref() is None:
        return
    sdk = _load_agents_sdk()
    if sdk is None:
        return
    try:
        with sdk.guardrail_span(name=_clean_text(name) or "guardrail", triggered=bool(triggered)):
            pass
    except Exception:
        LOGGER.exception("Failed to record OpenAI guardrail span %s.", _clean_text(name) or "guardrail")


def record_custom_span(
    *,
    name: str,
    data: dict[str, Any] | None = None,
) -> None:
    if current_trace_ref() is None:
        return
    sdk = _load_agents_sdk()
    if sdk is None:
        return
    try:
        with sdk.custom_span(name=_clean_text(name) or "custom", data=dict(data or {})):
            pass
    except Exception:
        LOGGER.exception("Failed to record OpenAI custom span %s.", _clean_text(name) or "custom")
