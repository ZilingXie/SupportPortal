from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

LOGGER = logging.getLogger(__name__)


def _safe_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


class RagServiceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def map_rag_payload_to_ticket_answer(
    payload: dict[str, Any],
    *,
    insufficient_reply: str,
) -> tuple[str, float, list[str], list[dict[str, str]], bool]:
    decision = str(payload.get("decision") or "").strip().lower()
    if decision == "answer":
        answer = str(payload.get("answer") or "").strip()
        if not answer:
            return insufficient_reply, float(payload.get("confidence") or 0.0), [], [], True
        citations = payload.get("citations") if isinstance(payload.get("citations"), list) else []
        sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
        return (
            answer,
            float(payload.get("confidence") or 0.0),
            [str(source) for source in sources if str(source).strip()],
            [item for item in citations if isinstance(item, dict)],
            False,
        )
    return insufficient_reply, float(payload.get("confidence") or 0.0), [], [], True


def _base_url() -> str:
    return (os.getenv("RAG_SERVICE_URL") or "").strip().rstrip("/")


def _shared_token() -> str:
    return (os.getenv("RAG_SERVICE_SHARED_TOKEN") or "").strip()


def _timeout_seconds() -> float:
    return _safe_float_env("RAG_SERVICE_TIMEOUT_SECONDS", 45.0)


def _json_loads(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"message": text}


def _encode_multipart_form_data(
    *,
    fields: dict[str, str] | None = None,
    files: list[dict[str, Any]] | None = None,
) -> tuple[bytes, str]:
    boundary = "----SupportPortalRagBoundary7MA4YWxkTrZu0gW"
    lines: list[bytes] = []

    for name, value in (fields or {}).items():
        lines.extend(
            [
                f"--{boundary}".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"'.encode("utf-8"),
                b"",
                str(value).encode("utf-8"),
            ]
        )

    for item in files or []:
        field_name = str(item.get("field_name") or "file")
        file_name = str(item.get("file_name") or "upload.bin")
        content_type = str(item.get("content_type") or "application/octet-stream")
        content = item.get("content")
        file_bytes = content if isinstance(content, bytes) else bytes(content or b"")
        lines.extend(
            [
                f"--{boundary}".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{file_name}"'
                ).encode("utf-8"),
                f"Content-Type: {content_type}".encode("utf-8"),
                b"",
                file_bytes,
            ]
        )

    lines.append(f"--{boundary}--".encode("utf-8"))
    lines.append(b"")
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


class RagServiceClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        shared_token: str | None = None,
    ) -> None:
        self._base_url = (base_url or _base_url()).strip().rstrip("/")
        self._timeout_seconds = timeout_seconds if timeout_seconds is not None else _timeout_seconds()
        self._shared_token = (shared_token or _shared_token()).strip()

    def is_configured(self) -> bool:
        return bool(self._base_url)

    def _build_url(self, path: str, query: dict[str, Any] | None = None) -> str:
        base = self._base_url.rstrip("/")
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{base}{normalized_path}"
        if query:
            filtered = {key: value for key, value in query.items() if value is not None}
            if filtered:
                url = f"{url}?{urllib.parse.urlencode(filtered)}"
        return url

    def _headers(self, *, content_type: str | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        if self._shared_token:
            headers["Authorization"] = f"Bearer {self._shared_token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
        query: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured():
            raise RagServiceError("RAG service is not configured")

        timeout = timeout_seconds if timeout_seconds is not None else self._timeout_seconds
        body: bytes | None = raw_body
        request_content_type = content_type
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            request_content_type = "application/json"

        request = urllib.request.Request(
            url=self._build_url(path, query=query),
            data=body,
            headers=self._headers(content_type=request_content_type),
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = _json_loads(response.read())
                return payload if isinstance(payload, dict) else {"payload": payload}
        except urllib.error.HTTPError as exc:
            payload = _json_loads(exc.read())
            raise RagServiceError(
                f"RAG service returned HTTP {exc.code}",
                status_code=int(exc.code),
                payload=payload,
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise RagServiceError("RAG service request failed") from exc

    def query(
        self,
        *,
        question: str,
        request_id: str,
        ticket_id: str | None,
        customer_id: str | None,
        top_k: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "question": question,
            "request_id": request_id,
            "ticket_id": ticket_id,
            "customer_id": customer_id,
        }
        if top_k is not None:
            payload["top_k"] = int(top_k)
        return self._request("POST", "/internal/rag/query", json_body=payload)

    def upload_official_document(self, *, file_name: str, content: bytes) -> dict[str, Any]:
        body, content_type = _encode_multipart_form_data(
            files=[
                {
                    "field_name": "file",
                    "file_name": file_name,
                    "content_type": "text/markdown",
                    "content": content,
                }
            ]
        )
        return self._request(
            "POST",
            "/internal/knowledge/official-documents",
            raw_body=body,
            content_type=content_type,
        )

    def upload_article(
        self,
        *,
        title: str,
        content: str,
        source_url: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/internal/knowledge/articles",
            json_body={
                "title": title,
                "content": content,
                "source_url": source_url,
            },
        )

    def list_ingestions(
        self,
        *,
        limit: int,
        status: str | None = None,
        knowledge_type: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/internal/knowledge/ingestions",
            query={
                "limit": limit,
                "status": status,
                "knowledge_type": knowledge_type,
            },
        )

    def get_ingestion(self, ingestion_id: str) -> dict[str, Any]:
        quoted = urllib.parse.quote(str(ingestion_id or "").strip(), safe="")
        return self._request("GET", f"/internal/knowledge/ingestions/{quoted}")

    def get_ingestion_report(self, ingestion_id: str) -> dict[str, Any]:
        quoted = urllib.parse.quote(str(ingestion_id or "").strip(), safe="")
        return self._request("GET", f"/internal/knowledge/ingestions/{quoted}/report")

    def knowledge_metrics(self) -> dict[str, Any]:
        return self._request("GET", "/internal/knowledge/metrics")

    def health(self, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        return self._request("GET", "/health", timeout_seconds=timeout_seconds)

    def probe_health(self) -> dict[str, Any]:
        if not self.is_configured():
            return {
                "status": "disabled",
                "service": "rag-api",
                "knowledge_storage": "disabled",
            }
        try:
            payload = self.health(timeout_seconds=min(self._timeout_seconds, 2.0))
        except RagServiceError as exc:
            return {
                "status": "unreachable",
                "service": "rag-api",
                "knowledge_storage": "unreachable",
                "detail": str(exc),
            }
        payload = dict(payload)
        payload.setdefault("status", "ok")
        payload.setdefault("service", "rag-api")
        return payload


async def async_to_thread(method: Any, *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(method, *args, **kwargs)
