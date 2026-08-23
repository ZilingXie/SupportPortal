"""Legacy /account intake body compatibility for the automation /v1/cases endpoints.

The n8n Zendesk intake workflows POST the historical five-field body
(``title``, ``question``, ``customer_email``, ``source``, ``customer_name``,
form-encoded). This module parses both that body shape and the native JSON
contract into ``AutomationExecutionRequest`` so those workflows can switch to
the split environments without changing their request body, mirroring the
derivation the old intake performed internally: Zendesk ticket id from the
``source`` link, idempotent ``request_id`` per ticket, and ``AC-{ticket_id}``
case ids.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import ValidationError

from backend.services.automation_contracts import AutomationExecutionRequest

_ZENDESK_TICKET_PATH_RE = re.compile(r"^/(?:agent/tickets/(\d+)|api/v2/tickets/(\d+)\.json)$")
_ZENDESK_SOURCE_LINK_KEYS = ("Link", "link", "url", "source_url", "source")


def _source_link(value: Any) -> str:
    if isinstance(value, dict):
        for key in _ZENDESK_SOURCE_LINK_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""
    if isinstance(value, str):
        return value.strip()
    return ""


def zendesk_ticket_id_from_source(value: Any) -> str:
    link = _source_link(value)
    if not link:
        return ""
    parsed = urllib.parse.urlparse(link if "://" in link else f"https://{link}")
    host = (parsed.hostname or "").lower()
    if host != "zendesk.com" and not host.endswith(".zendesk.com"):
        return ""
    match = _ZENDESK_TICKET_PATH_RE.match(parsed.path or "")
    if not match:
        return ""
    return match.group(1) or match.group(2) or ""


def normalize_legacy_intake_payload(raw: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw)
    title = str(payload.pop("title", "") or "").strip()
    source = payload.pop("source", None)
    if not str(payload.get("subject") or "").strip() and title:
        payload["subject"] = title
    zendesk_ticket_id = str(payload.get("zendesk_ticket_id") or "").strip()
    if not zendesk_ticket_id:
        derived_ticket_id = zendesk_ticket_id_from_source(source)
        if derived_ticket_id:
            payload["zendesk_ticket_id"] = derived_ticket_id
            zendesk_ticket_id = derived_ticket_id
    if not str(payload.get("request_id") or "").strip():
        payload["request_id"] = f"n8n-zd-{zendesk_ticket_id}" if zendesk_ticket_id else f"n8n-{uuid4().hex}"
    if not str(payload.get("case_id") or "").strip():
        payload["case_id"] = f"AC-{zendesk_ticket_id}" if zendesk_ticket_id else f"AC-{uuid4().hex[:6].upper()}"
    return payload


async def parse_automation_execution_request(http_request: Request) -> AutomationExecutionRequest:
    content_type = (http_request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    body = (await http_request.body()).decode("utf-8", errors="replace")
    if content_type == "application/x-www-form-urlencoded":
        raw: Any = dict(urllib.parse.parse_qsl(body, keep_blank_values=True))
    else:
        try:
            raw = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"invalid request body: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="request body must be a JSON object or form body")
    try:
        return AutomationExecutionRequest(**normalize_legacy_intake_payload(raw))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc
