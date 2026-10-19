"""Restricted Zac Responses/SSE boundary. No credentials or recovery payload in output."""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from dataclasses import dataclass
from typing import Any

_INTERNAL_TEAM_AGENT_HOST = "hermes.preproduction.supportportal.local"


@dataclass(frozen=True)
class TeamAgentSettings:
    base_url: str
    token: str
    model: str

    @classmethod
    def from_env(cls) -> "TeamAgentSettings":
        return cls(str(os.getenv("TEAM_AGENT_BASE_URL") or os.getenv("ZAC_AGENT_BASE_URL") or "").rstrip("/"),
                   str(os.getenv("TEAM_AGENT_REST_TOKEN") or os.getenv("ZAC_AGENT_REST_TOKEN") or ""),
                   str(os.getenv("TEAM_AGENT_MODEL") or os.getenv("ZAC_AGENT_MODEL") or ""))

    def error(self) -> str | None:
        parsed = urlsplit(self.base_url)
        if (
            not (
                parsed.scheme == "https"
                or (parsed.scheme == "http" and parsed.hostname == _INTERNAL_TEAM_AGENT_HOST)
            )
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not self.token
        ):
            return "zac_agent_not_configured"
        if self.model != "team-agent":
            return "zac_agent_model_not_configured"
        return None

ZacSettings = TeamAgentSettings


def public_result(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in ("state", "code", "answer", "conversation_id", "conversation_status", "handoff_status", "selected_agent", "selection_basis", "limitations", "requested_evidence") if key in record}


def read_terminal(response: Any, *, deadline: float, clock=time.monotonic) -> dict[str, Any]:
    event = ""
    data: list[str] = []
    size = 0
    for raw in response:
        if clock() >= deadline:
            return {"state": "outcome_unknown", "code": "zac_budget_exhausted"}
        size += len(raw)
        if size > 256_000:
            return {"state": "outcome_unknown", "code": "zac_response_limit"}
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            text = "\n".join(data)
            if text == "[DONE]":
                break
            payload = json.loads(text)
            kind = event or payload.get("type")
            if kind == "response.failed":
                return {"state": "failed", "code": "zac_response_failed"}
            if kind == "response.completed":
                result = payload.get("response") or {}
                metadata = result.get("metadata") or {}
                answer = "\n".join(str(content.get("text") or "") for item in result.get("output") or [] for content in item.get("content") or [] if content.get("type") == "output_text")
                return {
                    "state": "completed", "answer": answer,
                    "conversation_id": metadata.get("conversation_id"),
                    "conversation_status": metadata.get("conversation_status", "unknown"),
                    **{key: metadata.get(key) for key in ("handoff_status", "selected_agent", "selection_basis", "limitations", "requested_evidence")},
                }
            event, data = "", []
    return {"state": "outcome_unknown", "code": "zac_stream_incomplete"}


class TeamAgentClient:
    def __init__(self, settings: TeamAgentSettings | None = None, *, opener=urllib.request.urlopen, clock=time.monotonic):
        self.settings = settings or TeamAgentSettings.from_env()
        self.opener, self.clock = opener, clock

    def consult(self, record: dict[str, Any]) -> dict[str, Any]:
        deadline = self.clock() + 100
        for attempt in (1, 2):
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            base_url = self.settings.base_url.rstrip("/")
            if base_url.endswith("/v1"):
                base_url = base_url[:-3].rstrip("/")
            request = urllib.request.Request(base_url + "/v1/responses",
                data=record["request_body"].encode(), method="POST", headers={
                    "Authorization": "Bearer " + self.settings.token,
                    "Content-Type": "application/json", "Accept": "text/event-stream",
                    "X-API-User-Id": record["user_id"], "Idempotency-Key": record["idempotency_key"],
                })
            try:
                with self.opener(request, timeout=min(45, remaining)) as response:
                    status = int(getattr(response, "status", 200) or 0)
                    if status != 200:
                        result = {"state": "failed", "code": f"zac_http_{status}"}
                    else:
                        result = read_terminal(response, deadline=deadline, clock=self.clock)
            except urllib.error.HTTPError as exc:
                # Never surface an upstream body which can echo secrets/input.
                result = {"state": "failed", "code": f"zac_http_{exc.code}"}
            except (urllib.error.URLError, socket.timeout, TimeoutError, OSError, ValueError):
                result = {"state": "outcome_unknown", "code": "zac_transport_unknown"}
            if result["state"] != "outcome_unknown":
                return {**result, "attempts": attempt}
        return {"state": "outcome_unknown", "code": "zac_outcome_unknown", "attempts": attempt}

ZacClient = TeamAgentClient
