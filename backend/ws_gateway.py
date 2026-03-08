from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

LOGGER = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            recipients = list(self._channels.get(channel, set()))
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


app = FastAPI(title="SupportPortal WS Gateway", version="0.1.0")
hub = ConnectionHub()
redis_client: Redis | None = None
redis_listener_task: asyncio.Task[None] | None = None


def _redis_url() -> str:
    return (os.getenv("REDIS_URL") or "redis://127.0.0.1:6379/0").strip()


def _event_channel() -> str:
    return (os.getenv("EVENT_BUS_CHANNEL") or "support.events").strip()


async def _consume_events() -> None:
    global redis_client
    if redis_client is None:
        return

    channel_name = _event_channel()
    pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
    await pubsub.subscribe(channel_name)
    LOGGER.info("WS Gateway subscribed to Redis channel: %s", channel_name)

    try:
        while True:
            message = await pubsub.get_message(timeout=1.0)
            if not message:
                await asyncio.sleep(0.01)
                continue
            if message.get("type") != "message":
                continue

            data = message.get("data")
            if not isinstance(data, str):
                continue
            try:
                payload = json.loads(data)
            except Exception:
                LOGGER.warning("WS Gateway received invalid JSON payload: %s", data)
                continue
            if not isinstance(payload, dict):
                continue

            targets_raw = payload.pop("targets", ["client", "engineer", "dashboard"])
            targets = (
                [str(item).strip().lower() for item in targets_raw]
                if isinstance(targets_raw, list)
                else ["client", "engineer", "dashboard"]
            )
            for channel in targets:
                if channel not in {"client", "engineer", "dashboard"}:
                    continue
                await hub.broadcast(channel, payload)
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await pubsub.unsubscribe(channel_name)
            await pubsub.aclose()
        except Exception:
            pass


@app.on_event("startup")
async def on_startup() -> None:
    global redis_client, redis_listener_task
    redis_client = Redis.from_url(_redis_url(), decode_responses=True)
    redis_listener_task = asyncio.create_task(_consume_events())
    LOGGER.info("WS Gateway startup complete.")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global redis_client, redis_listener_task
    if redis_listener_task is not None:
        redis_listener_task.cancel()
        try:
            await redis_listener_task
        except asyncio.CancelledError:
            pass
        redis_listener_task = None
    if redis_client is not None:
        try:
            await redis_client.aclose()
        except Exception:
            pass
        redis_client = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": now_iso(), "service": "ws-gateway"}


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

