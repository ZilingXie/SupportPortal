from __future__ import annotations

import asyncio
import logging
import os
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
from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY, answer_with_rag

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


class ConnectionHub:
    def __init__(self) -> None:
        self._channels: dict[str, set[WebSocket]] = {
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


FAQ_ANSWERS = {
    "reset password": "Go to the sign-in page, click 'Forgot password', then follow the email instructions.",
    "api authentication failed": "Check API key validity, token expiration, and environment configuration first.",
    "where is config file": "The default config lives in /etc/app/config.yaml or your project root .env file.",
    "database timeout": "Verify database host/network reachability and increase connection timeout if needed.",
}

NEGATIVE_KEYWORDS = {
    "angry",
    "frustrated",
    "terrible",
    "broken",
    "crash",
    "urgent",
    "complaint",
    "slow",
}
POSITIVE_KEYWORDS = {"thanks", "great", "awesome", "good", "perfect", "resolved"}


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


def build_ai_followup(solution: str) -> str:
    clean_solution = solution.strip()
    return (
        "Thanks for waiting. I reviewed this with an engineer.\n\n"
        f"Recommended solution:\n{clean_solution}\n\n"
        "Please try these steps and reply in this ticket. I will continue to follow up until this is resolved."
    )


def _summary_fallback(ticket: dict[str, Any]) -> str:
    subject = str(ticket.get("subject", "")).strip() or "General support request"
    status = str(ticket.get("status", "open")).strip()
    priority = str(ticket.get("priority", "normal")).strip()
    mode = str(ticket.get("engineer_mode", MANAGED_MODE)).strip()

    latest_customer = ""
    latest_assistant = ""
    messages = ticket.get("messages", [])
    for message in reversed(messages):
        role = str(message.get("role", "")).strip().lower()
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        if not latest_customer and role == "customer":
            latest_customer = content
        if not latest_assistant and role == "assistant":
            latest_assistant = content
        if latest_customer and latest_assistant:
            break

    lines = [
        f"Subject: {subject}",
        f"Status: {status} | Priority: {priority} | Mode: {mode}",
    ]
    if latest_customer:
        lines.append(f"Latest customer request: {latest_customer[:220]}")
    if latest_assistant:
        lines.append(f"Latest system response: {latest_assistant[:220]}")
    if not latest_customer and not latest_assistant:
        lines.append("No conversation messages yet.")
    return "\n".join(lines)


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


def build_ticket_summary(ticket: dict[str, Any]) -> tuple[str, str]:
    fallback = _summary_fallback(ticket)
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return fallback, "fallback"

    try:
        from langchain_openai import ChatOpenAI
    except Exception:
        return fallback, "fallback"

    messages = ticket.get("messages", [])
    lines: list[str] = []
    for message in messages[-10:]:
        role = str(message.get("role", "system")).strip().upper() or "SYSTEM"
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"{role}: {content[:700]}")
    if not lines:
        return fallback, "fallback"

    prompt = (
        "Create a concise English summary for an engineer, plain text only (no markdown).\n"
        "Output 4-6 short lines and include: core issue, current progress, and next step.\n\n"
        f"Ticket ID: {ticket.get('ticket_id')}\n"
        f"Subject: {ticket.get('subject')}\n"
        f"Status: {ticket.get('status')}\n"
        f"Priority: {ticket.get('priority')}\n"
        f"Mode: {ticket.get('engineer_mode')}\n"
        "Recent messages:\n"
        + "\n".join(lines)
    )

    model_name = (os.getenv("OPENAI_CHAT_MODEL") or "gpt-4.1").strip()
    try:
        llm = ChatOpenAI(model=model_name, temperature=0, api_key=api_key)
        response = llm.invoke(
            [
                ("system", "You summarize support tickets for engineers."),
                ("user", prompt),
            ]
        )
        summary = _llm_response_to_text(response)
        if summary:
            return summary, model_name
    except Exception:
        pass
    return fallback, "fallback"


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


def detect_sentiment(message: str) -> tuple[str, float, bool]:
    lowered = message.lower()
    negative_hits = sum(word in lowered for word in NEGATIVE_KEYWORDS)
    positive_hits = sum(word in lowered for word in POSITIVE_KEYWORDS)
    score = min(0.99, 0.55 + 0.12 * negative_hits + 0.06 * positive_hits)
    if negative_hits >= 2:
        score = max(score, 0.86)
    label = "negative" if negative_hits > positive_hits else "neutral"
    is_alert = label == "negative" and score > 0.85
    return label, score, is_alert


def compute_priority(is_alert: bool) -> str:
    return "high" if is_alert else "normal"


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/client")


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


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "time": now_iso(),
        "ticket_storage": ticket_repository.storage_mode(),
    }


@app.post("/api/tickets/query")
async def create_or_update_ticket(request: TicketQueryRequest) -> dict[str, Any]:
    ticket_id = request.ticket_id or f"T-{uuid4().hex[:6].upper()}"
    existing_ticket = ticket_repository.get_ticket(ticket_id)
    is_new_ticket = existing_ticket is None
    label, score, is_alert = detect_sentiment(request.message)
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

    answer, confidence, sources, citations, needs_engineer_guidance = build_answer(customer_message)
    needs_engineer_input = False

    if ticket.get("engineer_mode") == TAKEOVER_MODE:
        answer = "This ticket is in takeover mode. An engineer will contact you directly."
        confidence = 0.0
        sources = []
        citations = []
        ticket["status"] = "open"
        ticket["pending_engineer_question"] = (
            "Customer sent a new message in takeover mode. Please contact the customer directly."
        )
        needs_engineer_input = True
    elif needs_engineer_guidance:
        answer = (
            "I could not find enough reliable information in the knowledge sources. "
            "I have asked an engineer for guidance and will follow up here shortly."
        )
        confidence = min(confidence, 0.55)
        sources = []
        citations = []
        ticket["status"] = "waiting_for_engineer"
        ticket["pending_engineer_question"] = customer_message
        needs_engineer_input = True
    else:
        ticket["status"] = "open"
        ticket["pending_engineer_question"] = None

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
    await hub.broadcast("engineer", event)
    await hub.broadcast("dashboard", event)

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
        await hub.broadcast("engineer", attention_event)
        await hub.broadcast("dashboard", attention_event)

    if is_alert:
        alert_event = {
            "event": "sentiment_alert",
            "ticket_id": ticket_id,
            "priority": "high",
            "message": "Customer frustration detected",
            "created_at": now_iso(),
        }
        ticket_repository.record_event(ticket_id, alert_event["event"], alert_event)
        await hub.broadcast("engineer", alert_event)
        await hub.broadcast("dashboard", alert_event)

    return {
        "ticket_id": ticket_id,
        "answer": answer,
        "confidence": round(confidence, 2),
        "sources": sources,
        "citations": citations,
        "sentiment": {
            "label": label,
            "score": round(score, 2),
            "is_alert": is_alert,
        },
        "priority": priority,
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
        "needs_engineer_input": needs_engineer_input,
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
    return {"ticket": ticket}


@app.get("/api/engineer/tickets/{ticket_id}/summary")
def get_ticket_summary(ticket_id: str) -> dict[str, Any]:
    ticket = ticket_repository.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ensure_ticket_defaults(ticket)
    summary, model = build_ticket_summary(ticket)
    return {
        "ticket_id": ticket_id,
        "summary": summary,
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
    await hub.broadcast("engineer", payload)
    await hub.broadcast("dashboard", payload)

    return {
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
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
    if request.mode == TAKEOVER_MODE and ticket.get("status") == "waiting_for_engineer":
        ticket["status"] = "open"
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
        "created_at": now_iso(),
    }
    ticket_repository.record_event(ticket_id, payload["event"], payload)
    await hub.broadcast("engineer", payload)
    await hub.broadcast("dashboard", payload)

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

    timestamp = now_iso()
    ticket["messages"].append(
        {
            "role": "engineer",
            "content": solution,
            "created_at": timestamp,
        }
    )

    ai_followup = build_ai_followup(solution)
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
        "created_at": now_iso(),
    }
    ticket_repository.record_event(ticket_id, payload["event"], payload)
    await hub.broadcast("engineer", payload)
    await hub.broadcast("dashboard", payload)

    return {
        "ticket_id": ticket_id,
        "status": ticket["status"],
        "engineer_mode": ticket["engineer_mode"],
        "ai_followup": ai_followup,
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
