"""HTTP client for the Hermes native gateway `/v1/runs` agent API."""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


HERMES_SUPPORT_PROFILE_PREFIX = "p/support"

TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})


class HermesAgentError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class HermesAgentSettings:
    base_url: str
    api_token: str
    turn_timeout_seconds: float = 900.0
    poll_interval_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "HermesAgentSettings":
        return cls(
            base_url=str(os.getenv("HERMES_AGENT_BASE_URL") or "").strip().rstrip("/"),
            api_token=str(os.getenv("HERMES_AGENT_API_TOKEN") or "").strip(),
            turn_timeout_seconds=float(os.getenv("HERMES_AGENT_TURN_TIMEOUT_SECONDS") or 900.0),
            poll_interval_seconds=float(os.getenv("HERMES_AGENT_POLL_INTERVAL_SECONDS") or 2.0),
        )

    def configured(self) -> bool:
        return bool(self.base_url and self.api_token)


def _request(
    settings: HermesAgentSettings,
    *,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    headers = {
        "Authorization": f"Bearer {settings.api_token}",
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        f"{settings.base_url}{path}",
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read() or b"{}")
            return response.status, payload
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2048]
        raise HermesAgentError(
            "hermes_agent_rejected",
            f"Hermes gateway HTTP {exc.code}: {detail}",
            retryable=False,
        ) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
        raise HermesAgentError(
            "hermes_agent_outcome_unknown",
            f"Hermes gateway transport failure: {exc}",
            retryable=True,
        ) from exc


class HermesAgentClient:
    """Thin client over POST /v1/runs + GET /v1/runs/{run_id}.

    Callers must always pass an explicit ``session_id`` so a run joins the case's
    persistent logical session instead of an isolated per-run session, and must
    reuse the same ``Idempotency-Key`` with byte-identical bodies on retry.
    """

    def __init__(self, settings: HermesAgentSettings | None = None) -> None:
        self.settings = settings or HermesAgentSettings.from_env()

    def start_run(
        self,
        *,
        session_id: str,
        instructions: str,
        input_text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not self.settings.configured():
            raise HermesAgentError(
                "hermes_agent_not_configured",
                "Hermes agent gateway is not configured",
                retryable=False,
            )
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            raise HermesAgentError(
                "hermes_agent_idempotency_key_missing",
                "Idempotency-Key is required for hermes runs",
                retryable=False,
            )
        body: dict[str, Any] = {"input": input_text, "session_id": session_id}
        if instructions:
            body["instructions"] = instructions
        status, payload = _request(
            self.settings,
            method="POST",
            path="/v1/runs",
            body=body,
            idempotency_key=normalized_key,
        )
        if status != 202 or not isinstance(payload, dict) or not str(payload.get("run_id") or ""):
            raise HermesAgentError(
                "hermes_agent_receipt_invalid",
                f"Hermes gateway returned HTTP {status} without a run_id",
                retryable=False,
            )
        return payload

    def get_run(self, run_id: str) -> dict[str, Any]:
        if not self.settings.configured():
            raise HermesAgentError(
                "hermes_agent_not_configured",
                "Hermes agent gateway is not configured",
                retryable=False,
            )
        normalized = str(run_id or "").strip()
        if not normalized:
            raise HermesAgentError(
                "hermes_agent_run_id_missing",
                "run_id is required",
                retryable=False,
            )
        _, payload = _request(
            self.settings,
            method="GET",
            path=f"/v1/runs/{normalized}",
        )
        if not isinstance(payload, dict) or not str(payload.get("status") or ""):
            raise HermesAgentError(
                "hermes_agent_status_invalid",
                "Hermes gateway returned a run status without a state",
                retryable=False,
            )
        return payload

    def wait_for_run(
        self,
        run_id: str,
        *,
        timeout_seconds: float | None = None,
        sleep: Any = time.sleep,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + (
            timeout_seconds
            if timeout_seconds is not None
            else self.settings.turn_timeout_seconds
        )
        poll_interval = max(0.5, self.settings.poll_interval_seconds)
        while True:
            status = self.get_run(run_id)
            if str(status.get("status")) in TERMINAL_RUN_STATUSES:
                return status
            if time.monotonic() >= deadline:
                raise HermesAgentError(
                    "hermes_agent_turn_timeout",
                    f"Hermes run {run_id} did not reach a terminal state in time",
                    retryable=True,
                )
            sleep(poll_interval)
