"""HTTP client for the AgentRelay server (agent-collab v0.6, p2-163).

Used by the ECS worker to dispatch Enablement auto Relay Tasks and to consume
execution results posted back by the Mac-side skill.  Wire contract:
``docs/operations/agentrelay-http-contract.md`` (bound from the live server
manifest plus the public agent-relay-mcp client source).  Transport follows
the ``hermes_runtime`` pattern: Bearer auth, explicit idempotency keys, and
failure classification into retryable "outcome unknown" versus terminal
"rejected" so callers never blindly repeat a write whose outcome is unknown.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

DEFAULT_AGENTRELAY_BASE_URL = "https://server.stellarix.space/agentrelay/api"
AGENTRELAY_PROTOCOL_VERSION = "agent-collab-v0.6"
DEFAULT_AGENTRELAY_TARGET_AGENT = "zac-agent"
DEFAULT_AGENTRELAY_TASK_TTL_SECONDS = 14 * 24 * 3600


class AgentRelayError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class AgentRelayConfig:
    base_url: str
    agent_id: str
    username: str
    token: str
    target_agent_id: str
    timeout_seconds: float
    task_ttl_seconds: int


def agentrelay_config() -> AgentRelayConfig | None:
    base_url = str(os.getenv("AGENTRELAY_BASE_URL") or "").strip().rstrip("/")
    agent_id = str(os.getenv("AGENTRELAY_AGENT_ID") or "").strip()
    username = str(os.getenv("AGENTRELAY_USERNAME") or "").strip()
    token = str(os.getenv("AGENTRELAY_TOKEN") or "").strip()
    if not base_url or not agent_id or not username or not token:
        return None
    try:
        timeout_seconds = float(os.getenv("AGENTRELAY_TIMEOUT_SECONDS") or "15.0")
    except ValueError:
        timeout_seconds = 15.0
    try:
        task_ttl_seconds = int(os.getenv("AGENTRELAY_TASK_TTL_SECONDS") or "")
    except ValueError:
        task_ttl_seconds = DEFAULT_AGENTRELAY_TASK_TTL_SECONDS
    return AgentRelayConfig(
        base_url=base_url,
        agent_id=agent_id,
        username=username,
        token=token,
        target_agent_id=(
            str(os.getenv("AGENTRELAY_TARGET_AGENT_ID") or "").strip()
            or DEFAULT_AGENTRELAY_TARGET_AGENT
        ),
        timeout_seconds=timeout_seconds,
        task_ttl_seconds=task_ttl_seconds,
    )


def agentrelay_configured() -> bool:
    return agentrelay_config() is not None


class AgentRelayClient:
    """Thin request/reply wrapper; every call is one HTTP round trip."""

    def __init__(self, config: AgentRelayConfig | None = None):
        self._config = config or agentrelay_config()
        if self._config is None:
            raise AgentRelayError(
                "agentrelay_not_configured",
                "AgentRelay transport is not configured",
                retryable=False,
            )

    @property
    def config(self) -> AgentRelayConfig:
        return self._config  # type: ignore[return-value]

    # -- Transport --------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = (
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        )
        request = urllib.request.Request(
            f"{self._config.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self._config.token}",
                "X-AgentRelay-Agent-Id": self._config.agent_id,
                "X-AgentRelay-Username": self._config.username,
                "X-AgentRelay-Envelope": "v0.3",
                **({"Content-Type": "application/json"} if body else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout_seconds) as response:
                raw = response.read() or b"{}"
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise AgentRelayError(
                        "agentrelay_invalid_response",
                        "AgentRelay returned a non-object response",
                        retryable=False,
                    )
                return parsed
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2048]
            retryable = exc.code >= 500 or exc.code == 429
            raise AgentRelayError(
                "agentrelay_rejected",
                f"AgentRelay HTTP {exc.code}: {detail}",
                retryable=retryable,
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
            raise AgentRelayError(
                "agentrelay_outcome_unknown",
                f"AgentRelay outcome unknown: {exc}",
                retryable=True,
            ) from exc
        except json.JSONDecodeError as exc:
            raise AgentRelayError(
                "agentrelay_invalid_response",
                f"AgentRelay returned non-JSON: {exc}",
                retryable=False,
            ) from exc

    # -- Task lifecycle ---------------------------------------------------

    def create_task(
        self,
        *,
        idempotency_key: str,
        done_criteria: str,
        subject: str,
        text: str,
        task_expires_at_epoch: int,
    ) -> dict[str, Any]:
        """Create one Relay Task; safe to replay with the same idempotency key."""
        payload = {
            "protocol_version": AGENTRELAY_PROTOCOL_VERSION,
            "idempotency_key": str(idempotency_key),
            "requester_agent_id": self._config.agent_id,
            "target_agent_id": self._config.target_agent_id,
            "done_criteria": str(done_criteria),
            "max_turns": 1,
            "task_expires_at": int(task_expires_at_epoch),
            "message": {
                "subject": str(subject),
                "parts": [{"kind": "text", "text": str(text)}],
            },
        }
        response = self._request("POST", "/tasks", payload=payload)
        task = response.get("task")
        if not isinstance(task, dict) or not str(task.get("task_id") or "").strip():
            raise AgentRelayError(
                "agentrelay_create_receipt_invalid",
                "AgentRelay create response is missing task.task_id",
                retryable=False,
            )
        return response

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/tasks/{_path_segment(task_id)}")

    def complete_task(
        self,
        task_id: str,
        *,
        message_id: str,
        turn_sequence: int,
        expected_task_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = {
            "actor_agent_id": self._config.agent_id,
            "message_id": str(message_id),
            "turn_sequence": int(turn_sequence),
            "expected_task_version": int(expected_task_version),
            "idempotency_key": str(idempotency_key),
            "completed_against_message_id": str(message_id),
        }
        return self._request(
            "POST", f"/tasks/{_path_segment(task_id)}/complete", payload=payload
        )

    # -- Headless listener (recovery pull) --------------------------------

    def register_listener(self, listener_instance_id: str) -> int:
        response = self._request(
            "POST",
            f"/workers/{_path_segment(self._config.agent_id)}"
            f"/readiness/register?protocol_version={AGENTRELAY_PROTOCOL_VERSION}",
            payload={
                "listener_instance_id": str(listener_instance_id),
                "client_version": "supportportal-p2-163",
                "workspace_version": "2",
                "transport": "http",
            },
        )
        readiness = response.get("readiness") or (response.get("data") or {}).get("readiness")
        epoch = int((readiness or {}).get("readiness_epoch") or 0)
        if epoch <= 0:
            raise AgentRelayError(
                "agentrelay_readiness_invalid",
                "AgentRelay readiness registration is missing readiness_epoch",
                retryable=False,
            )
        return epoch

    def publish_readiness(
        self, listener_instance_id: str, readiness_epoch: int, *, ready: bool = True
    ) -> None:
        self._request(
            "POST",
            f"/workers/{_path_segment(self._config.agent_id)}"
            f"/readiness?protocol_version={AGENTRELAY_PROTOCOL_VERSION}",
            payload={
                "listener_instance_id": str(listener_instance_id),
                "readiness_epoch": int(readiness_epoch),
                "ready": bool(ready),
            },
        )

    def pull_event(self, listener_instance_id: str, readiness_epoch: int) -> dict[str, Any] | None:
        """Recovery pull: at most one event per request; repeat until empty."""
        query = (
            f"listener_instance_id={_query_value(listener_instance_id)}"
            f"&readiness_epoch={int(readiness_epoch)}"
            f"&protocol_version={AGENTRELAY_PROTOCOL_VERSION}"
        )
        response = self._request(
            "GET", f"/workers/{_path_segment(self._config.agent_id)}/events?{query}"
        )
        events = response.get("events")
        if not isinstance(events, list):
            events = (response.get("data") or {}).get("events") or []
        if not events:
            return None
        return events[0] if isinstance(events[0], dict) else None

    def ack_event(
        self,
        event: dict[str, Any],
        *,
        listener_instance_id: str,
        readiness_epoch: int,
        turn_sequence: int | None = None,
        expected_task_version: int | None = None,
    ) -> None:
        """ACK one pulled event.

        Message events (``message_id`` present) use the message-ack form whose
        fencing values (``turn_sequence`` / ``expected_task_version``) MUST be
        taken from a fresh ``GET /tasks/{id}`` — the server rejects stale
        versions and the ack itself advances the task version.  Notification
        events use the lighter ``/events/{event_id}/ack`` form.
        """
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            raise AgentRelayError(
                "agentrelay_event_invalid",
                "AgentRelay event is missing event_id required for ack",
                retryable=False,
            )
        message_id = str(event.get("message_id") or "").strip()
        task_id = str(event.get("task_id") or "").strip()
        if message_id and task_id:
            if turn_sequence is None or expected_task_version is None:
                raise AgentRelayError(
                    "agentrelay_event_invalid",
                    "Message-event ack requires fresh turn_sequence and "
                    "expected_task_version from GET /tasks/{id}",
                    retryable=False,
                )
            self._request(
                "POST",
                f"/workers/{_path_segment(self._config.agent_id)}"
                f"/messages/{_path_segment(message_id)}/ack",
                payload={
                    "task_id": task_id,
                    "event_id": event_id,
                    "message_id": message_id,
                    "turn_sequence": int(turn_sequence),
                    "expected_task_version": int(expected_task_version),
                    "listener_instance_id": str(listener_instance_id),
                    "readiness_epoch": int(readiness_epoch),
                    "idempotency_key": f"ack:{event_id}",
                },
            )
            return
        # Notification-class event: no message binding, light ack form.
        self._request(
            "POST",
            f"/workers/{_path_segment(self._config.agent_id)}"
            f"/events/{_path_segment(event_id)}/ack",
            payload={
                "idempotency_key": f"ack:{event_id}",
                "listener_instance_id": str(listener_instance_id),
                "readiness_epoch": int(readiness_epoch),
            },
        )


def new_listener_instance_id() -> str:
    return f"supportportal-worker-{uuid.uuid4().hex[:12]}"


def _path_segment(value: str) -> str:
    return str(value or "").strip().replace("/", "%2F")


def _query_value(value: str) -> str:
    return str(value or "").strip().replace("/", "%2F")
