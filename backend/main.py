from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.repositories.ticket_repository import (
    InMemoryTicketRepository,
    TicketRepository,
    create_ticket_repository,
)
from backend.services.emotion_reply import generate_emotion_reply
from backend.services.event_bus import AsyncRedisEventBus
from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY, answer_with_rag
from backend.services.sentiment_classifier import SentimentResult, classify_sentiment
from backend.services.task_queue import AsyncRedisTaskQueue

BASE_DIR = Path(__file__).resolve().parent.parent
CLIENT_DIR = BASE_DIR / "client_ui"
ENGINEER_DIR = BASE_DIR / "engineer_ui"
DASHBOARD_DIR = BASE_DIR / "dashboard"

# Auto-load project environment variables from repository root.
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)

MANAGED_MODE = "managed"
TAKEOVER_MODE = "takeover"
OPEN_STATUSES = {"open", "waiting_for_engineer"}
PRIORITY_RANK = {"urgent": 4, "high": 3, "normal": 2, "low": 1}
LOGGER = logging.getLogger(__name__)
_UNAVAILABLE_MODELS: set[str] = set()


def _env_flag(name: str, default: bool = False) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _safe_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _safe_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


ASYNC_QUERY_ENABLED = _env_flag("ASYNC_QUERY_ENABLED", default=False)
OPENAI_REQUEST_TIMEOUT_SECONDS = _safe_float_env("OPENAI_REQUEST_TIMEOUT_SECONDS", 20.0)
OPENAI_MAX_RETRIES = _safe_int_env("OPENAI_MAX_RETRIES", 1)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TicketQueryRequest(BaseModel):
    ticket_id: str | None = None
    customer_id: str = Field(default="C-001")
    requester: str | None = None
    subject: str | None = None
    message: str = Field(min_length=1)


class TicketActionRequest(BaseModel):
    action: str = Field(pattern="^(processing|resolved|handoff|reopen)$")
    engineer_id: str = Field(default="eng")
    note: str | None = None


class TicketModeRequest(BaseModel):
    mode: str = Field(pattern="^(managed|takeover)$")
    engineer_id: str = Field(default="eng")


class ManagedResponseRequest(BaseModel):
    engineer_id: str = Field(default="eng")
    solution: str = Field(min_length=1, max_length=4000)


class TakeoverReplyRequest(BaseModel):
    engineer_id: str = Field(default="eng")
    message: str = Field(min_length=1, max_length=4000)


class CancelPendingRequest(BaseModel):
    customer_id: str | None = None
    message_created_at: str = Field(min_length=1, max_length=64)


class ConnectionHub:
    def __init__(self) -> None:
        self._channels: dict[str, set[WebSocket]] = {
            "client": set(),
            "engineer": set(),
            "dashboard": set(),
        }
        self._lock = asyncio.Lock()

    async def connect(self, channel: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._channels[channel].add(websocket)

    async def disconnect(self, channel: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._channels[channel].discard(websocket)

    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            recipients = list(self._channels[channel])
        stale: list[WebSocket] = []
        for websocket in recipients:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        if stale:
            async with self._lock:
                for websocket in stale:
                    self._channels[channel].discard(websocket)


app = FastAPI(title="AI Ticket POC", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if CLIENT_DIR.exists():
    app.mount("/client", StaticFiles(directory=CLIENT_DIR, html=True), name="client-ui")
if ENGINEER_DIR.exists():
    app.mount("/engineer", StaticFiles(directory=ENGINEER_DIR, html=True), name="engineer-ui")
if DASHBOARD_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard-ui")


ticket_repository: TicketRepository = create_ticket_repository()
hub = ConnectionHub()
event_bus = AsyncRedisEventBus()
task_queue = AsyncRedisTaskQueue()


FAQ_ANSWERS = {
    "reset password": "Go to the sign-in page, click 'Forgot password', then follow the email instructions.",
    "api authentication failed": "Check API key validity, token expiration, and environment configuration first.",
    "where is config file": "The default config lives in /etc/app/config.yaml or your project root .env file.",
    "database timeout": "Verify database host/network reachability and increase connection timeout if needed.",
}


def derive_subject(message: str) -> str:
    normalized = " ".join(str(message).split())
    if not normalized:
        return "General support request"
    return normalized[:100]


def priority_sort_value(priority: str | None) -> int:
    if not priority:
        return PRIORITY_RANK["normal"]
    return PRIORITY_RANK.get(str(priority).lower(), PRIORITY_RANK["normal"])


def latest_customer_message(ticket: dict[str, Any]) -> str:
    messages = ticket.get("messages", [])
    for message in reversed(messages):
        if message.get("role") == "customer":
            return str(message.get("content", "")).strip()
    return ""


def ensure_ticket_defaults(ticket: dict[str, Any]) -> None:
    created_at = ticket.get("created_at") or now_iso()
    ticket["created_at"] = created_at
    ticket.setdefault("updated_at", created_at)
    ticket.setdefault("priority", "normal")
    ticket.setdefault("status", "open")
    ticket.setdefault("messages", [])
    ticket.setdefault("subject", "General support request")
    ticket.setdefault("requester", ticket.get("customer_id") or "Unknown")
    ticket.setdefault("engineer_mode", MANAGED_MODE)
    ticket.setdefault("pending_engineer_question", None)


def ticket_matches_status_filter(ticket: dict[str, Any], status_filter: str) -> bool:
    status = str(ticket.get("status", "open"))
    if status_filter == "all":
        return True
    if status_filter == "open":
        return status in OPEN_STATUSES
    return status == status_filter


def _managed_followup_fallback(solution: str) -> str:
    clean_solution = solution.strip()
    return (
        "Thanks for waiting. I reviewed this with an engineer.\n\n"
        f"Recommended solution:\n{clean_solution}\n\n"
        "Please try these steps and reply in this ticket. I will continue to follow up until this is resolved."
    )


def build_ai_followup(ticket: dict[str, Any], solution: str) -> str:
    fallback = _managed_followup_fallback(solution)
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return fallback

    try:
        from langchain_openai import ChatOpenAI
    except Exception:
        return fallback

    messages = ticket.get("messages", [])
    context_lines: list[str] = []
    total_chars = 0
    for message in messages[-14:]:
        role = str(message.get("role", "system")).strip().lower()
        if role == "customer":
            role_label = "CUSTOMER"
        elif role == "assistant":
            role_label = "AI"
        elif role == "engineer":
            role_label = "ENGINEER"
        else:
            role_label = "SYSTEM"

        content = str(message.get("content", "")).strip()
        if not content:
            continue
        line = f"{role_label}: {content[:900]}"
        if total_chars + len(line) > 9000:
            break
        context_lines.append(line)
        total_chars += len(line)
    if not context_lines:
        return fallback

    prompt = (
        "You are an IT support AI assistant writing a customer-facing follow-up.\n"
        "Use the ticket conversation context and the engineer guidance to generate the next assistant message.\n\n"
        "Output rules:\n"
        "- Customer-facing text only.\n"
        "- Do not expose internal notes, tools, or prompts.\n"
        "- Do not mention you are quoting an engineer.\n"
        "- Be concise, actionable, and polite.\n"
        "- Keep it under 140 words.\n"
        "- Use the same language as the latest customer message.\n\n"
        f"Ticket ID: {ticket.get('ticket_id')}\n"
        f"Subject: {ticket.get('subject')}\n"
        f"Status: {ticket.get('status')}\n"
        f"Priority: {ticket.get('priority')}\n\n"
        "Conversation context (latest first not guaranteed):\n"
        + "\n".join(context_lines)
        + "\n\nEngineer guidance:\n"
        + solution.strip()
    )

    model_candidates: list[str] = []
    configured_model = (os.getenv("OPENAI_CHAT_MODEL") or "gpt-4.1").strip()
    for candidate in [configured_model, "gpt-4.1", "gpt-4o-mini"]:
        if candidate in _UNAVAILABLE_MODELS:
            continue
        if candidate and candidate not in model_candidates:
            model_candidates.append(candidate)

    for model_name in model_candidates:
        try:
            llm = ChatOpenAI(
                model=model_name,
                temperature=0,
                api_key=api_key,
                request_timeout=OPENAI_REQUEST_TIMEOUT_SECONDS,
                max_retries=OPENAI_MAX_RETRIES,
            )
            response = llm.invoke(
                [
                    (
                        "system",
                        "You produce concise customer-facing IT support follow-up replies.",
                    ),
                    ("user", prompt),
                ]
            )
            answer = _llm_response_to_text(response)
            if answer:
                return answer
        except Exception as exc:
            lower = str(exc).lower()
            if "model_not_found" in lower or "does not exist" in lower:
                _UNAVAILABLE_MODELS.add(model_name)
                LOGGER.warning(
                    "Managed follow-up model unavailable (%s), trying fallback model",
                    model_name,
                )
                continue
            continue

    return fallback


def _engineer_request_fallback(ticket: dict[str, Any], customer_message: str) -> str:
    issue = " ".join(str(customer_message or "").split()).strip()
    if not issue:
        issue = str(ticket.get("subject") or "").strip() or "Unknown customer issue"
    if len(issue) > 220:
        issue = issue[:220] + "..."
    return (
        "Engineer Request:\n"
        f"Issue: {issue}\n"
        "Action Needed: Please reproduce the issue, collect related logs/error traces, confirm recent release/config changes, and provide a workaround plus ETA."
    )


def _normalize_engineer_request_text(text: str, ticket: dict[str, Any], customer_message: str) -> str:
    content = str(text or "").strip()
    if not content:
        return _engineer_request_fallback(ticket, customer_message)

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    issue_parts: list[str] = []
    action_parts: list[str] = []
    current_section: str | None = None

    for line in lines:
        lowered = line.lower()
        if lowered.startswith("engineer request"):
            current_section = None
            continue
        if lowered.startswith("issue:"):
            current_section = "issue"
            issue_line = line.split(":", 1)[1].strip()
            if issue_line:
                issue_parts.append(issue_line)
            continue
        if lowered.startswith("action needed:"):
            current_section = "action"
            action_line = line.split(":", 1)[1].strip()
            if action_line:
                action_parts.append(action_line)
            continue

        # Support wrapped lines without repeating the "Issue:" / "Action Needed:" prefix.
        if current_section == "issue":
            issue_parts.append(line)
        elif current_section == "action":
            action_parts.append(line)

    issue_value = " ".join(issue_parts).strip()
    action_value = " ".join(action_parts).strip()

    if not issue_value or not action_value:
        return _engineer_request_fallback(ticket, customer_message)

    return (
        "Engineer Request:\n"
        f"Issue: {issue_value}\n"
        f"Action Needed: {action_value}"
    )


def build_engineer_followup_request(ticket: dict[str, Any], customer_message: str) -> str:
    fallback = _engineer_request_fallback(ticket, customer_message)
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return fallback

    try:
        from langchain_openai import ChatOpenAI
    except Exception:
        return fallback

    messages = ticket.get("messages", [])
    context_lines: list[str] = []
    total_chars = 0
    for message in messages:
        role = str(message.get("role", "system")).strip().upper() or "SYSTEM"
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        line = f"{role}: {content[:700]}"
        if total_chars + len(line) > 7500:
            break
        context_lines.append(line)
        total_chars += len(line)
    if not context_lines:
        return fallback

    prompt = (
        "You are assisting support escalation handoff.\n"
        "Based on the full ticket context, create a concise engineer request.\n"
        "Output plain text only, exactly 3 lines in this exact format:\n"
        "Engineer Request:\n"
        "Issue: <one concise sentence>\n"
        "Action Needed: <one concise sentence describing what engineer should do or provide>\n\n"
        f"Ticket ID: {ticket.get('ticket_id')}\n"
        f"Subject: {ticket.get('subject')}\n"
        f"Status: {ticket.get('status')}\n"
        f"Priority: {ticket.get('priority')}\n"
        "Recent messages:\n"
        + "\n".join(context_lines)
    )

    model_candidates: list[str] = []
    configured_model = (os.getenv("OPENAI_CHAT_MODEL") or "gpt-4.1").strip()
    for candidate in [configured_model, "gpt-4.1", "gpt-4o-mini"]:
        if candidate and candidate not in model_candidates:
            model_candidates.append(candidate)

    for model_name in model_candidates:
        try:
            llm = ChatOpenAI(model=model_name, temperature=0, api_key=api_key)
            response = llm.invoke(
                [
                    ("system", "You generate concise support escalation requests for engineers."),
                    ("user", prompt),
                ]
            )
            normalized = _normalize_engineer_request_text(
                _llm_response_to_text(response), ticket, customer_message
            )
            if normalized:
                return normalized
        except Exception:
            continue
    return fallback


def _summary_fallback(ticket: dict[str, Any]) -> tuple[str, str]:
    subject = str(ticket.get("subject", "")).strip() or "General support request"
    status = str(ticket.get("status", "open")).strip().lower()
    priority = str(ticket.get("priority", "normal")).strip().lower()
    mode = str(ticket.get("engineer_mode", MANAGED_MODE)).strip().lower()
    pending_question = str(ticket.get("pending_engineer_question", "")).strip()

    latest_customer = ""
    latest_assistant = ""
    messages = ticket.get("messages", [])
    for message in reversed(messages):
        role = str(message.get("role", "")).strip().lower()
        content = " ".join(str(message.get("content", "")).split()).strip()
        if not content:
            continue
        if not latest_customer and role == "customer":
            latest_customer = content
        if not latest_assistant and role == "assistant":
            latest_assistant = content
        if latest_customer and latest_assistant:
            break

    summary_parts = [
        f"Ticket subject is '{subject}' with status {status}, priority {priority}, and mode {mode}."
    ]
    if latest_customer:
        summary_parts.append(f"Latest customer request: {latest_customer[:260]}")
    if latest_assistant:
        summary_parts.append(f"Latest AI response: {latest_assistant[:260]}")
    if pending_question:
        summary_parts.append(f"Pending engineer request: {pending_question[:260]}")
    if not latest_customer and not latest_assistant and not pending_question:
        summary_parts.append("No conversation history is available yet.")
    summary = " ".join(summary_parts).strip()

    if status == "resolved":
        next_action = (
            "Confirm resolution details with the customer and close the ticket if no additional issue remains."
        )
    elif status == "waiting_for_engineer":
        next_action = (
            "Investigate the unresolved gap, gather required logs or reproduction details, and send a concrete reply."
        )
    else:
        next_action = (
            "Continue troubleshooting based on the latest customer message, then provide the next actionable step."
        )

    if mode == TAKEOVER_MODE:
        next_action += " Keep the response in human takeover mode."

    return summary, next_action


def _extract_json_dict(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    candidates = [cleaned]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return None


def _normalize_summary_fields(
    payload: dict[str, Any] | None, fallback_summary: str, fallback_next_action: str
) -> tuple[str, str]:
    def _to_text(value: Any, *, multiline: bool = False) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            if not items:
                return ""
            if multiline:
                return "\n".join([f"{index + 1}. {item}" for index, item in enumerate(items)])
            return " ".join(items)
        if value is None:
            return ""
        text = str(value).strip()
        return text

    summary = ""
    next_action = ""
    if isinstance(payload, dict):
        summary = _to_text(payload.get("summary", ""), multiline=False)
        next_action = _to_text(
            payload.get("next_action_needed")
            or payload.get("next_action")
            or payload.get("nextActionNeeded")
            or "",
            multiline=True,
        )

    if not summary:
        summary = fallback_summary
    if not next_action:
        next_action = fallback_next_action

    normalized_summary = " ".join(summary.split())
    next_action_lines = [
        " ".join(line.split()).strip()
        for line in str(next_action).splitlines()
        if " ".join(line.split()).strip()
    ]
    if next_action_lines:
        normalized_next_action = "\n".join(next_action_lines)
    else:
        normalized_next_action = " ".join(str(next_action).split())
    return normalized_summary[:1500], normalized_next_action[:900]


def _llm_response_to_text(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")).strip())
            else:
                parts.append(str(item).strip())
        return "\n".join([part for part in parts if part]).strip()
    return str(content).strip()


def build_ticket_summary(ticket: dict[str, Any]) -> tuple[str, str, str]:
    fallback_summary, fallback_next_action = _summary_fallback(ticket)
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return fallback_summary, fallback_next_action, "fallback"

    try:
        from langchain_openai import ChatOpenAI
    except Exception:
        return fallback_summary, fallback_next_action, "fallback"

    messages = ticket.get("messages", [])
    lines: list[str] = []
    for message in messages[-14:]:
        role = str(message.get("role", "system")).strip().upper() or "SYSTEM"
        content = " ".join(str(message.get("content", "")).split()).strip()
        if not content:
            continue
        lines.append(f"{role}: {content[:900]}")
    if not lines:
        return fallback_summary, fallback_next_action, "fallback"

    ticket_id = str(ticket.get("ticket_id", "")).strip()
    subject = str(ticket.get("subject", "")).strip()
    status = str(ticket.get("status", "")).strip()
    priority = str(ticket.get("priority", "")).strip()
    mode = str(ticket.get("engineer_mode", MANAGED_MODE)).strip()
    requester = str(ticket.get("requester") or ticket.get("customer_id") or "").strip()
    pending_question = str(ticket.get("pending_engineer_question", "")).strip()

    prompt = (
        "Return a JSON object with exactly two keys: summary and next_action_needed.\n"
        "Requirements:\n"
        '- summary: 2-4 concise sentences describing current issue, current progress, and blocker if any.\n'
        "- next_action_needed: 1-3 concrete actions for the engineer to execute next.\n"
        "- Use plain English text values.\n"
        "- Do not use markdown, headings, or extra keys.\n\n"
        f"Ticket ID: {ticket_id}\n"
        f"Subject: {subject}\n"
        f"Requester: {requester}\n"
        f"Status: {status}\n"
        f"Priority: {priority}\n"
        f"Mode: {mode}\n"
        f"Pending engineer question: {pending_question or 'None'}\n"
        "Recent messages:\n"
        + "\n".join(lines)
    )

    model_candidates: list[str] = []
    configured_model = (os.getenv("OPENAI_CHAT_MODEL") or "gpt-4.1").strip()
    for candidate in [configured_model, "gpt-4.1", "gpt-4o-mini"]:
        if candidate and candidate not in model_candidates:
            model_candidates.append(candidate)

    for model_name in model_candidates:
        try:
            llm = ChatOpenAI(model=model_name, temperature=0, api_key=api_key)
            response = llm.invoke(
                [
                    (
                        "system",
                        "You summarize support tickets for engineers and output strict JSON with summary and next_action_needed.",
                    ),
                    ("user", prompt),
                ]
            )
            raw_output = _llm_response_to_text(response)
            parsed = _extract_json_dict(raw_output)
            summary, next_action = _normalize_summary_fields(
                parsed, fallback_summary, fallback_next_action
            )
            if summary and next_action:
                return summary, next_action, model_name
        except Exception:
            continue

    return fallback_summary, fallback_next_action, "fallback"


def build_answer(message: str) -> tuple[str, float, list[str], list[dict[str, str]], bool]:
    rag_answer = answer_with_rag(message)
    if rag_answer is not None:
        is_rag_insufficient = rag_answer.answer.strip() == INSUFFICIENT_EVIDENCE_REPLY
        return (
            rag_answer.answer,
            rag_answer.confidence,
            rag_answer.sources,
            rag_answer.citations,
            is_rag_insufficient,
        )

    lowered = message.lower()
    for keyword, answer in FAQ_ANSWERS.items():
        if keyword in lowered:
            return answer, 0.92, ["faq"], [], False
    fallback = (
        "I could not find an exact FAQ match. I created a ticket and linked related knowledge "
        "articles for engineer follow-up."
    )
    return fallback, 0.68, ["kb:simulated"], [], True


def detect_sentiment(message: str) -> SentimentResult:
    return classify_sentiment(message)


def build_emotion_context(ticket: dict[str, Any], limit: int = 6, max_chars: int = 240) -> list[dict[str, str]]:
    messages = ticket.get("messages", [])
    context: list[dict[str, str]] = []
    for item in messages[-max(1, int(limit)) :]:
        role = str(item.get("role", "system")).strip().lower() or "system"
        content = " ".join(str(item.get("content", "")).split()).strip()
        if not content:
            continue
        if len(content) > max_chars:
            content = content[:max_chars] + "..."
        context.append({"role": role, "content": content})
    return context


def compose_emotion_and_answer(emotion_reply: str, answer: str) -> str:
    emotional = str(emotion_reply or "").strip()
    technical = str(answer or "").strip()
    if emotional and technical:
        return f"{emotional}\n\n{technical}"
    return emotional or technical


def compute_priority(is_alert: bool) -> str:
    return "high" if is_alert else "normal"


async def dispatch_event(channels: list[str], payload: dict[str, Any]) -> None:
    normalized_channels: list[str] = []
    for channel in channels:
        value = str(channel or "").strip().lower()
        if value and value not in normalized_channels:
            normalized_channels.append(value)

    for channel in normalized_channels:
        await hub.broadcast(channel, payload)

    if normalized_channels:
        bus_payload = dict(payload)
        bus_payload["targets"] = normalized_channels
        await event_bus.publish(bus_payload)


def build_query_task(ticket_id: str, customer_message: str, message_created_at: str) -> dict[str, str]:
    return {
        "task_type": "ticket_query",
        "ticket_id": ticket_id,
        "customer_message": customer_message,
        "message_created_at": message_created_at,
        "created_at": now_iso(),
    }


def build_client_sync_event(ticket: dict[str, Any], event_name: str, message: str | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event": event_name,
        "ticket_id": str(ticket.get("ticket_id") or ""),
        "customer_id": str(ticket.get("customer_id") or ""),
        "status": str(ticket.get("status") or "open"),
        "engineer_mode": str(ticket.get("engineer_mode") or MANAGED_MODE),
        "updated_at": str(ticket.get("updated_at") or now_iso()),
        "created_at": now_iso(),
    }
    if message:
        event["message"] = message
    return event


def build_engineer_request_records(ticket_id: str) -> list[dict[str, Any]]:
    rows = ticket_repository.list_ticket_events(ticket_id=ticket_id, limit=200)
    records: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        event_type = str(row.get("event_type") or payload.get("event") or "").strip().lower()
        created_at = str(payload.get("created_at") or row.get("created_at") or now_iso())
        engineer_id = str(payload.get("engineer_id") or "").strip()
        detail = str(payload.get("message") or "").strip()

        status = ""
        if event_type in {"ticket_takeover_reply", "ticket_direct_reply"}:
            status = "engineer replied"
            if not detail:
                detail = "Engineer sent a direct reply to the customer."
        elif event_type == "ticket_guidance_applied":
            status = "received answer"
            if not detail:
                detail = "Engineer provided guidance for AI response."
        elif event_type == "ticket_mode_changed":
            target_mode = str(payload.get("engineer_mode") or payload.get("new_mode") or "").strip().lower()
            if target_mode == TAKEOVER_MODE:
                status = "engineer takeover"
                if not detail:
                    detail = "Engineer switched this case to Human Takeover mode."

        if not status:
            continue

        records.append(
            {
                "id": f"{ticket_id}-{event_type}-{index}",
                "status": status,
                "detail": detail,
                "engineer_id": engineer_id,
                "created_at": created_at,
                "event_type": event_type,
            }
        )
    return records


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/client")


@app.get("/login")
def login_entry() -> RedirectResponse:
    return RedirectResponse(url="/engineer")


@app.post("/api/v1/auth/logout")
def logout() -> dict[str, Any]:
    return {"ok": True, "logged_out_at": now_iso()}


@app.on_event("startup")
def startup_event() -> None:
    global ticket_repository
    try:
        ticket_repository.initialize()
        LOGGER.info("Ticket repository initialized: %s", ticket_repository.storage_mode())
    except Exception as exc:
        LOGGER.warning(
            "Ticket repository initialization failed. Fallback to memory mode. error=%s",
            exc,
        )
        fallback = InMemoryTicketRepository()
        fallback.initialize()
        ticket_repository = fallback
        LOGGER.warning("Ticket repository switched to memory mode.")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await event_bus.close()
    await task_queue.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "time": now_iso(),
        "ticket_storage": ticket_repository.storage_mode(),
        "async_query_enabled": "true" if ASYNC_QUERY_ENABLED else "false",
    }


@app.post("/api/tickets/query")
async def create_or_update_ticket(request: TicketQueryRequest) -> dict[str, Any]:
    ticket_id = request.ticket_id or f"T-{uuid4().hex[:6].upper()}"
    existing_ticket = ticket_repository.get_ticket(ticket_id)
    is_new_ticket = existing_ticket is None
    sentiment = detect_sentiment(request.message)
    is_alert = sentiment.bucket == "negative"
    priority = compute_priority(is_alert)

    ticket = existing_ticket or {
        "ticket_id": ticket_id,
        "customer_id": request.customer_id,
        "status": "open",
        "created_at": now_iso(),
        "messages": [],
        "engineer_mode": MANAGED_MODE,
    }
    ensure_ticket_defaults(ticket)
    initial_message_count = len(ticket.get("messages", []))

    ticket["customer_id"] = request.customer_id
    ticket["requester"] = (
        request.requester.strip()
        if request.requester and request.requester.strip()
        else ticket.get("requester") or request.customer_id
    )
    existing_subject = str(ticket.get("subject") or "").strip()
    if request.subject and request.subject.strip():
        ticket["subject"] = request.subject.strip()
    elif is_new_ticket or not existing_subject or existing_subject == "General support request":
        ticket["subject"] = derive_subject(request.message)

    if ticket.get("status") == "resolved":
        ticket["status"] = "open"

    timestamp = now_iso()
    customer_message = request.message.strip()
    ticket["messages"].append(
        {
            "role": "customer",
            "content": customer_message,
            "created_at": timestamp,
        }
    )
    emotion_reply = generate_emotion_reply(
        sentiment_bucket=sentiment.bucket,
        raw_label=sentiment.raw_label,
        sentiment_confidence=sentiment.confidence,
        customer_message=customer_message,
        ticket_context=build_emotion_context(ticket),
    )
    LOGGER.info(
        "sentiment_decision sentiment_bucket=%s sentiment_raw_label=%s sentiment_confidence=%.3f emotion_reply_source=%s",
        sentiment.bucket,
        sentiment.raw_label,
        sentiment.confidence,
        emotion_reply.source,
    )

    answer = emotion_reply.text
    confidence = 0.0
    sources: list[str] = []
    citations: list[dict[str, str]] = []
    needs_engineer_guidance = False
    needs_engineer_input = False
    ai_replied = True
    task_enqueued = False

    if ticket.get("engineer_mode") == TAKEOVER_MODE:
        answer = compose_emotion_and_answer(
            emotion_reply.text,
            "I have forwarded your question to the assigned engineer. The engineer will reply to you shortly.",
        )
        confidence = 0.0
        sources = []
        citations = []
        ai_replied = True
        ticket["status"] = "open"
        ticket["pending_engineer_question"] = (
            "Customer sent a new message in takeover mode. Please contact the customer directly."
        )
        needs_engineer_input = True
    elif is_alert:
        answer = emotion_reply.text
        confidence = 0.0
        ticket["status"] = "waiting_for_engineer"
        ticket["pending_engineer_question"] = _engineer_request_fallback(ticket, customer_message)
        needs_engineer_input = True
    elif ASYNC_QUERY_ENABLED:
        answer = emotion_reply.text
        confidence = 0.0
        ai_replied = True
        ticket["status"] = "open"
        ticket["pending_engineer_question"] = None
        task_enqueued = await task_queue.enqueue(
            build_query_task(
                ticket_id=ticket_id,
                customer_message=customer_message,
                message_created_at=timestamp,
            )
        )
        if not task_enqueued:
            technical_answer, confidence, sources, citations, needs_engineer_guidance = build_answer(
                customer_message
            )
            answer = compose_emotion_and_answer(emotion_reply.text, technical_answer)
    else:
        technical_answer, confidence, sources, citations, needs_engineer_guidance = build_answer(
            customer_message
        )
        answer = compose_emotion_and_answer(emotion_reply.text, technical_answer)

    if not needs_engineer_input and needs_engineer_guidance:
        engineer_request = build_engineer_followup_request(ticket, customer_message)
        answer = compose_emotion_and_answer(
            emotion_reply.text,
            (
                "I could not find enough reliable information in the knowledge sources for this issue. "
                "I have contacted an engineer to continue investigation and will follow up shortly."
            ),
        )
        confidence = min(confidence, 0.55)
        sources = []
        citations = []
        ticket["status"] = "waiting_for_engineer"
        ticket["pending_engineer_question"] = engineer_request
        needs_engineer_input = True
    elif not needs_engineer_input:
        ticket["status"] = "open"
        if not task_enqueued:
            ticket["pending_engineer_question"] = None

    if ai_replied and str(answer).strip():
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": answer,
            "created_at": now_iso(),
        }
        if sources:
            assistant_message["sources"] = sources
        if citations:
            assistant_message["citations"] = citations
        ticket["messages"].append(assistant_message)

    ticket["priority"] = priority
    ticket["updated_at"] = now_iso()
    new_messages = ticket.get("messages", [])[initial_message_count:]
    ticket_repository.save_ticket(ticket, new_messages=new_messages)

    event = {
        "event": "ticket_created" if is_new_ticket else "ticket_updated",
        "ticket_id": ticket_id,
        "priority": priority,
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
        "message": customer_message,
        "created_at": now_iso(),
    }
    ticket_repository.record_event(ticket_id, event["event"], event)
    await dispatch_event(["engineer", "dashboard"], event)
    await dispatch_event(
        ["client"],
        build_client_sync_event(ticket, event["event"], customer_message[:200]),
    )

    if task_enqueued:
        processing_event = {
            "event": "ticket_ai_processing",
            "ticket_id": ticket_id,
            "status": ticket["status"],
            "priority": priority,
            "engineer_mode": ticket["engineer_mode"],
            "message": "AI is processing this request asynchronously.",
            "created_at": now_iso(),
        }
        ticket_repository.record_event(ticket_id, processing_event["event"], processing_event)
        await dispatch_event(["engineer", "dashboard"], processing_event)
        await dispatch_event(
            ["client"],
            build_client_sync_event(ticket, processing_event["event"]),
        )

    if needs_engineer_input:
        attention_event = {
            "event": "engineer_attention_required",
            "ticket_id": ticket_id,
            "priority": priority,
            "status": ticket["status"],
            "engineer_mode": ticket["engineer_mode"],
            "message": ticket.get("pending_engineer_question") or "Engineer attention required",
            "created_at": now_iso(),
        }
        ticket_repository.record_event(ticket_id, attention_event["event"], attention_event)
        await dispatch_event(["engineer", "dashboard"], attention_event)
        await dispatch_event(
            ["client"],
            build_client_sync_event(ticket, attention_event["event"]),
        )

    if is_alert:
        alert_event = {
            "event": "sentiment_alert",
            "ticket_id": ticket_id,
            "priority": "high",
            "message": f"Customer frustration detected ({sentiment.raw_label})",
            "created_at": now_iso(),
        }
        ticket_repository.record_event(ticket_id, alert_event["event"], alert_event)
        await dispatch_event(["engineer", "dashboard"], alert_event)

    return {
        "ticket_id": ticket_id,
        "answer": answer,
        "confidence": round(confidence, 2),
        "sources": sources,
        "citations": citations,
        "sentiment": {
            "label": sentiment.bucket,
            "raw_label": sentiment.raw_label,
            "score": round(sentiment.confidence, 2),
            "is_alert": is_alert,
            "provider": sentiment.provider,
            "intent": emotion_reply.intent,
        },
        "priority": priority,
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
        "ai_replied": ai_replied,
        "needs_engineer_input": needs_engineer_input,
        "queued_for_ai": task_enqueued,
        "queued_message_created_at": timestamp if task_enqueued else None,
        "eta_minutes": 5 if priority == "high" else 15,
    }


@app.get("/api/engineer/tickets")
def list_tickets(
    status: str = Query(default="open", pattern="^(open|all|resolved|waiting_for_engineer)$"),
) -> dict[str, Any]:
    all_tickets = ticket_repository.list_tickets(include_messages=True)
    filtered_tickets: list[dict[str, Any]] = []
    for ticket in all_tickets:
        ensure_ticket_defaults(ticket)
        if ticket_matches_status_filter(ticket, status):
            filtered_tickets.append(ticket)

    tickets = sorted(
        filtered_tickets,
        key=lambda item: (
            priority_sort_value(item.get("priority")),
            item.get("updated_at", item.get("created_at", "")),
        ),
        reverse=True,
    )
    return {"tickets": tickets, "status_filter": status}


@app.get("/api/engineer/tickets/{ticket_id}")
def get_ticket_detail(ticket_id: str) -> dict[str, Any]:
    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ensure_ticket_defaults(ticket)
    ticket["engineer_request_records"] = build_engineer_request_records(ticket_id)
    return {"ticket": ticket}


@app.get("/api/engineer/tickets/{ticket_id}/summary")
def get_ticket_summary(ticket_id: str) -> dict[str, Any]:
    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ensure_ticket_defaults(ticket)
    summary, next_action_needed, model = build_ticket_summary(ticket)
    return {
        "ticket_id": ticket_id,
        "summary": summary,
        "next_action_needed": next_action_needed,
        "model": model,
        "generated_at": now_iso(),
    }


@app.post("/api/tickets/{ticket_id}/action")
async def update_ticket(ticket_id: str, request: TicketActionRequest) -> dict[str, Any]:
    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    status_map = {
        "processing": "open",
        "reopen": "open",
        "resolved": "resolved",
        "handoff": "waiting_for_engineer",
    }

    ensure_ticket_defaults(ticket)
    initial_message_count = len(ticket.get("messages", []))
    ticket["status"] = status_map[request.action]
    if request.action == "handoff":
        ticket["engineer_mode"] = MANAGED_MODE
        ticket["pending_engineer_question"] = latest_customer_message(ticket)
    elif request.action == "resolved":
        ticket["pending_engineer_question"] = None

    ticket["updated_at"] = now_iso()
    ticket["last_engineer_action"] = {
        "action": request.action,
        "engineer_id": request.engineer_id,
        "note": request.note,
        "created_at": now_iso(),
    }
    new_messages = ticket.get("messages", [])[initial_message_count:]
    ticket_repository.save_ticket(ticket, new_messages=new_messages)

    payload = {
        "event": "ticket_updated",
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
        "engineer_id": request.engineer_id,
        "created_at": now_iso(),
    }
    ticket_repository.record_event(ticket_id, payload["event"], payload)
    await dispatch_event(["engineer", "dashboard"], payload)
    await dispatch_event(["client"], build_client_sync_event(ticket, payload["event"]))

    return {
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
        "updated_at": ticket["updated_at"],
    }


@app.post("/api/tickets/{ticket_id}/cancel-pending")
async def cancel_pending_ticket_query(ticket_id: str, request: CancelPendingRequest) -> dict[str, Any]:
    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ensure_ticket_defaults(ticket)
    customer_id = str(ticket.get("customer_id") or "").strip()
    request_customer_id = str(request.customer_id or "").strip()
    if request_customer_id and customer_id and request_customer_id != customer_id:
        raise HTTPException(status_code=403, detail="Ticket customer mismatch")

    message_created_at = request.message_created_at.strip()
    if not message_created_at:
        raise HTTPException(status_code=400, detail="message_created_at is required")

    ticket["updated_at"] = now_iso()
    ticket_repository.save_ticket(ticket, new_messages=[])

    payload = {
        "event": "ticket_ai_generation_stopped",
        "ticket_id": ticket_id,
        "customer_id": customer_id,
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
        "message_created_at": message_created_at,
        "message": "AI generation stopped by customer.",
        "created_at": now_iso(),
    }
    ticket_repository.record_event(ticket_id, payload["event"], payload)
    await dispatch_event(["engineer", "dashboard"], payload)

    client_payload = build_client_sync_event(ticket, payload["event"], payload["message"])
    client_payload["message_created_at"] = message_created_at
    await dispatch_event(["client"], client_payload)

    return {
        "ticket_id": ticket_id,
        "canceled": True,
        "message_created_at": message_created_at,
        "updated_at": ticket["updated_at"],
    }


@app.post("/api/engineer/tickets/{ticket_id}/mode")
async def update_ticket_mode(ticket_id: str, request: TicketModeRequest) -> dict[str, Any]:
    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ensure_ticket_defaults(ticket)
    initial_message_count = len(ticket.get("messages", []))
    previous_mode = ticket.get("engineer_mode", MANAGED_MODE)

    ticket["engineer_mode"] = request.mode
    if request.mode == TAKEOVER_MODE:
        if ticket.get("status") == "waiting_for_engineer":
            ticket["status"] = "open"
        # Clear stale pending engineer requests when a ticket enters takeover mode.
        ticket["pending_engineer_question"] = None

    ticket["updated_at"] = now_iso()
    ticket["last_engineer_action"] = {
        "action": "mode_switch",
        "previous_mode": previous_mode,
        "new_mode": request.mode,
        "engineer_id": request.engineer_id,
        "created_at": now_iso(),
    }
    new_messages = ticket.get("messages", [])[initial_message_count:]
    ticket_repository.save_ticket(ticket, new_messages=new_messages)

    payload = {
        "event": "ticket_mode_changed",
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "engineer_mode": request.mode,
        "engineer_id": request.engineer_id,
        "message": (
            "Engineer switched this case to Human Takeover mode."
            if request.mode == TAKEOVER_MODE
            else "Engineer switched this case to AI Managing mode."
        ),
        "created_at": now_iso(),
    }
    ticket_repository.record_event(ticket_id, payload["event"], payload)
    await dispatch_event(["engineer", "dashboard"], payload)
    await dispatch_event(["client"], build_client_sync_event(ticket, payload["event"]))

    return {
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "engineer_mode": request.mode,
        "updated_at": ticket["updated_at"],
    }


@app.post("/api/engineer/tickets/{ticket_id}/managed-response")
async def submit_managed_response(ticket_id: str, request: ManagedResponseRequest) -> dict[str, Any]:
    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ensure_ticket_defaults(ticket)
    initial_message_count = len(ticket.get("messages", []))
    if ticket.get("engineer_mode") != MANAGED_MODE:
        raise HTTPException(status_code=400, detail="Ticket is in takeover mode")

    solution = request.solution.strip()
    if not solution:
        raise HTTPException(status_code=400, detail="Solution cannot be empty")

    ai_followup = await asyncio.to_thread(build_ai_followup, ticket, solution)
    ticket["messages"].append(
        {
            "role": "assistant",
            "content": ai_followup,
            "created_at": now_iso(),
        }
    )

    ticket["status"] = "open"
    ticket["pending_engineer_question"] = None
    ticket["updated_at"] = now_iso()
    ticket["last_engineer_action"] = {
        "action": "managed_guidance",
        "engineer_id": request.engineer_id,
        "note": solution,
        "created_at": now_iso(),
    }
    new_messages = ticket.get("messages", [])[initial_message_count:]
    ticket_repository.save_ticket(ticket, new_messages=new_messages)

    payload = {
        "event": "ticket_guidance_applied",
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
        "engineer_id": request.engineer_id,
        "message": solution[:200],
        "created_at": now_iso(),
    }
    ticket_repository.record_event(ticket_id, payload["event"], payload)
    await dispatch_event(["engineer", "dashboard"], payload)
    await dispatch_event(["client"], build_client_sync_event(ticket, payload["event"]))

    return {
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
        "ai_followup": ai_followup,
        "updated_at": ticket["updated_at"],
    }


@app.post("/api/engineer/tickets/{ticket_id}/takeover-reply")
async def submit_takeover_reply(ticket_id: str, request: TakeoverReplyRequest) -> dict[str, Any]:
    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ensure_ticket_defaults(ticket)
    initial_message_count = len(ticket.get("messages", []))
    current_mode = str(ticket.get("engineer_mode") or MANAGED_MODE).strip().lower()

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    ticket["messages"].append(
        {
            "role": "engineer",
            "content": message,
            "created_at": now_iso(),
        }
    )

    ticket["status"] = "open"
    ticket["pending_engineer_question"] = None
    ticket["updated_at"] = now_iso()
    action_name = "takeover_reply" if current_mode == TAKEOVER_MODE else "direct_reply"
    event_name = "ticket_takeover_reply" if current_mode == TAKEOVER_MODE else "ticket_direct_reply"
    ticket["last_engineer_action"] = {
        "action": action_name,
        "engineer_id": request.engineer_id,
        "note": message,
        "created_at": now_iso(),
    }
    new_messages = ticket.get("messages", [])[initial_message_count:]
    ticket_repository.save_ticket(ticket, new_messages=new_messages)

    payload = {
        "event": event_name,
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
        "engineer_id": request.engineer_id,
        "message": message[:200],
        "created_at": now_iso(),
    }
    ticket_repository.record_event(ticket_id, payload["event"], payload)
    await dispatch_event(["engineer", "dashboard"], payload)
    await dispatch_event(["client"], build_client_sync_event(ticket, payload["event"], message[:200]))

    return {
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
        "sent_message": message,
        "updated_at": ticket["updated_at"],
    }


@app.get("/api/dashboard/metrics")
def dashboard_metrics() -> dict[str, Any]:
    tickets = ticket_repository.list_tickets(include_messages=False)
    total = len(tickets)
    resolved = sum(ticket.get("status") == "resolved" for ticket in tickets)
    alerts = sum(ticket.get("priority") == "high" for ticket in tickets)
    resolution_rate = round((resolved / total) * 100, 1) if total else 0.0
    return {
        "today_ticket_count": total,
        "resolution_rate": resolution_rate,
        "sentiment_alert_count": alerts,
    }


@app.get("/api/dashboard/events")
def dashboard_events(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    rows = ticket_repository.list_events(limit=limit)
    events: list[dict[str, Any]] = []
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        event_type = str(row.get("event_type") or payload.get("event") or "ticket_updated")
        ticket_id = row.get("ticket_id") or payload.get("ticket_id")
        normalized = {
            "event": payload.get("event") or event_type,
            "ticket_id": str(ticket_id) if ticket_id is not None else "-",
            "message": payload.get("message"),
            "status": payload.get("status"),
            "priority": payload.get("priority"),
            "engineer_mode": payload.get("engineer_mode"),
            "created_at": payload.get("created_at") or row.get("created_at") or now_iso(),
        }
        events.append(normalized)
    return {"events": events}


@app.websocket("/ws/client")
async def client_ws(websocket: WebSocket) -> None:
    await hub.connect("client", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect("client", websocket)


@app.websocket("/ws/engineer")
async def engineer_ws(websocket: WebSocket) -> None:
    await hub.connect("engineer", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect("engineer", websocket)


@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket) -> None:
    await hub.connect("dashboard", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect("dashboard", websocket)
