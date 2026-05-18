from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Any

from backend.services.rag_evidence_summary import build_rag_evidence_summary
from backend.services.rag_request_body_evidence import REQUEST_BODY_INSUFFICIENT_REASON

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
        failure_kind: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
        self.failure_kind = str(failure_kind or "").strip() or None


@dataclass(frozen=True)
class RagTicketAnswerDetail:
    answer: str
    confidence: float
    sources: list[str]
    citations: list[dict[str, str]]
    needs_engineer_guidance: bool
    reason: str
    evidence_summary: dict[str, Any] | None = None
    packed_evidence: dict[str, Any] | None = None

    def as_answer_tuple(self) -> tuple[str, float, list[str], list[dict[str, str]], bool]:
        return (
            self.answer,
            float(self.confidence),
            list(self.sources),
            [dict(item) for item in self.citations],
            bool(self.needs_engineer_guidance),
        )


def classify_rag_service_failure_kind(error: RagServiceError) -> str | None:
    normalized = str(getattr(error, "failure_kind", "") or "").strip().lower()
    if normalized in {"timeout", "transport", "http", "cancelled"}:
        return normalized
    if error.status_code is not None:
        return "http"
    message = str(error).strip().lower()
    if "timeout" in message or "timed out" in message:
        return "timeout"
    if "request failed" in message or "not configured" in message:
        return "transport"
    return None


def with_rag_detail_diagnostics(
    detail: RagTicketAnswerDetail,
    diagnostics: dict[str, Any] | None,
) -> RagTicketAnswerDetail:
    if not diagnostics:
        return detail
    merged_evidence = dict(detail.evidence_summary or {}) if isinstance(detail.evidence_summary, dict) else {}
    existing_diagnostics = (
        dict(merged_evidence.get("diagnostics"))
        if isinstance(merged_evidence.get("diagnostics"), dict)
        else {}
    )
    for key, value in diagnostics.items():
        if value is not None:
            existing_diagnostics[str(key)] = value
    if existing_diagnostics:
        merged_evidence["diagnostics"] = existing_diagnostics
    return replace(detail, evidence_summary=merged_evidence or None)


def map_rag_payload_to_ticket_answer_detail(
    payload: dict[str, Any],
    *,
    insufficient_reply: str,
) -> RagTicketAnswerDetail:
    decision = str(payload.get("decision") or "").strip().lower()
    raw_reason = str(payload.get("reason") or "").strip()
    evidence_summary = payload.get("evidence_summary") if isinstance(payload.get("evidence_summary"), dict) else None
    packed_evidence = payload.get("packed_evidence") if isinstance(payload.get("packed_evidence"), dict) else None
    if decision == "answer":
        answer = str(payload.get("answer") or "").strip()
        if answer:
            citations = payload.get("citations") if isinstance(payload.get("citations"), list) else []
            sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
            return RagTicketAnswerDetail(
                answer=answer,
                confidence=float(payload.get("confidence") or 0.0),
                sources=[str(source) for source in sources if str(source).strip()],
                citations=[item for item in citations if isinstance(item, dict)],
                needs_engineer_guidance=False,
                reason=raw_reason or "grounded_answer",
                evidence_summary=evidence_summary,
                packed_evidence=packed_evidence,
            )
    return RagTicketAnswerDetail(
        answer=insufficient_reply,
        confidence=float(payload.get("confidence") or 0.0),
        sources=[],
        citations=[],
        needs_engineer_guidance=True,
        reason=raw_reason or "insufficient_evidence",
        evidence_summary=evidence_summary,
        packed_evidence=packed_evidence,
    )


def map_rag_payload_to_ticket_answer(
    payload: dict[str, Any],
    *,
    insufficient_reply: str,
) -> tuple[str, float, list[str], list[dict[str, str]], bool]:
    return map_rag_payload_to_ticket_answer_detail(
        payload,
        insufficient_reply=insufficient_reply,
    ).as_answer_tuple()


def map_live_detail_payload_to_ticket_answer(payload: dict[str, Any]) -> tuple[str, float, list[str], list[dict[str, str]], bool] | None:
    detail = map_live_detail_payload_to_ticket_answer_detail(payload)
    return None if detail is None else detail.as_answer_tuple()


def _synthesize_live_detail_evidence_summary(primary: dict[str, Any]) -> dict[str, Any] | None:
    selected_contexts = primary.get("selected_contexts") if isinstance(primary.get("selected_contexts"), list) else []
    cited_chunk_ids = {
        str(chunk_id).strip()
        for chunk_id in (primary.get("cited_chunk_ids") if isinstance(primary.get("cited_chunk_ids"), list) else [])
        if str(chunk_id).strip()
    }
    query_understanding = (
        primary.get("query_understanding_meta")
        if isinstance(primary.get("query_understanding_meta"), dict)
        else {}
    )
    quality_signals = {
        "generation_mode": primary.get("generation_mode"),
        "extractive_fallback_used": primary.get("extractive_fallback_used"),
        "selected_doc_count": primary.get("selected_doc_count"),
        "query_class": primary.get("query_class"),
        "light_path_used": primary.get("light_path_used"),
        "citation_coverage_ratio": primary.get("citation_coverage_ratio"),
        "top1_similarity_score": primary.get("top1_similarity_score"),
        "avg_selected_similarity_score": primary.get("avg_selected_similarity_score"),
        "handoff_reason": primary.get("handoff_reason"),
        "needs_human": primary.get("needs_human"),
    }
    if not any(value is not None for value in quality_signals.values()) and not selected_contexts and not query_understanding:
        return None
    return build_rag_evidence_summary(
        quality_signals=quality_signals,
        selected_contexts=[item for item in selected_contexts if isinstance(item, dict)],
        cited_chunk_ids=cited_chunk_ids,
        query_understanding=query_understanding,
    )


def map_live_detail_payload_to_ticket_answer_detail(payload: dict[str, Any]) -> RagTicketAnswerDetail | None:
    primary = payload.get("primary") if isinstance(payload, dict) else None
    if not isinstance(primary, dict):
        return None
    answer = str(primary.get("answer") or "").strip()
    needs_human = bool(primary.get("needs_human"))
    raw_handoff_reason = str(primary.get("handoff_reason") or "").strip().lower()
    raw_generation_mode = str(primary.get("generation_mode") or "").strip().lower()
    try:
        confidence = float(primary.get("confidence_score") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    citations = primary.get("answer_citations") if isinstance(primary.get("answer_citations"), list) else []
    sources = primary.get("answer_sources") if isinstance(primary.get("answer_sources"), list) else []
    evidence_summary = (
        primary.get("evidence_summary")
        if isinstance(primary.get("evidence_summary"), dict)
        else payload.get("evidence_summary")
        if isinstance(payload.get("evidence_summary"), dict)
        else _synthesize_live_detail_evidence_summary(primary)
    )
    packed_evidence = (
        primary.get("packed_evidence")
        if isinstance(primary.get("packed_evidence"), dict)
        else payload.get("packed_evidence")
        if isinstance(payload.get("packed_evidence"), dict)
        else None
    )
    if needs_human or not answer:
        if raw_handoff_reason == "insufficient_evidence" or raw_generation_mode == "insufficient_evidence":
            return RagTicketAnswerDetail(
                answer=answer
                or "RAG completed but could not verify a customer-safe grounded answer from the available schema evidence.",
                confidence=confidence,
                sources=[],
                citations=[],
                needs_engineer_guidance=True,
                reason=REQUEST_BODY_INSUFFICIENT_REASON,
                evidence_summary=evidence_summary,
                packed_evidence=packed_evidence,
            )
        return None
    return RagTicketAnswerDetail(
        answer=answer,
        confidence=confidence,
        sources=[str(source) for source in sources if str(source).strip()],
        citations=[item for item in citations if isinstance(item, dict)],
        needs_engineer_guidance=False,
        reason="grounded_answer" if any(isinstance(item, dict) for item in citations) else "recovered_live_detail",
        evidence_summary=evidence_summary,
        packed_evidence=packed_evidence,
    )


def _base_url() -> str:
    return (os.getenv("RAG_SERVICE_URL") or "").strip().rstrip("/")


def _shared_token() -> str:
    return (os.getenv("RAG_SERVICE_SHARED_TOKEN") or "").strip()


def _timeout_seconds() -> float:
    return _safe_float_env("CLIENT_RAG_SERVICE_TIMEOUT_SECONDS", 180.0)


def _recovery_window_seconds() -> float:
    return _safe_float_env("CLIENT_RAG_RECOVERY_WINDOW_SECONDS", 90.0)


def _recovery_poll_interval_seconds() -> float:
    return _safe_float_env("CLIENT_RAG_RECOVERY_POLL_INTERVAL_SECONDS", 2.0)


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
            raise RagServiceError("RAG service is not configured", failure_kind="transport")

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
            failure_kind = "cancelled" if int(exc.code) == 409 and str(payload.get("reason") or "").strip() == "cancelled_by_route_flip" else "http"
            raise RagServiceError(
                f"RAG service returned HTTP {exc.code}",
                status_code=int(exc.code),
                payload=payload,
                failure_kind=failure_kind,
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            failure_kind = "timeout" if isinstance(exc, (TimeoutError, socket.timeout)) else "transport"
            raise RagServiceError("RAG service request failed", failure_kind=failure_kind) from exc

    def _request_text(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        if not self.is_configured():
            raise RagServiceError("RAG service is not configured", failure_kind="transport")

        timeout = timeout_seconds if timeout_seconds is not None else self._timeout_seconds
        request = urllib.request.Request(
            url=self._build_url(path, query=query),
            headers=self._headers(),
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            payload = _json_loads(exc.read())
            failure_kind = "cancelled" if int(exc.code) == 409 and str(payload.get("reason") or "").strip() == "cancelled_by_route_flip" else "http"
            raise RagServiceError(
                f"RAG service returned HTTP {exc.code}",
                status_code=int(exc.code),
                payload=payload,
                failure_kind=failure_kind,
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            failure_kind = "timeout" if isinstance(exc, (TimeoutError, socket.timeout)) else "transport"
            raise RagServiceError("RAG service request failed", failure_kind=failure_kind) from exc

    def query(
        self,
        *,
        question: str,
        request_id: str,
        ticket_id: str | None,
        customer_id: str | None,
        requester: str | None = None,
        ticket_context: list[dict[str, str]] | None = None,
        product: str | None = None,
        query_policy: str | None = None,
        top_k: int | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "question": question,
            "request_id": request_id,
            "ticket_id": ticket_id,
            "customer_id": customer_id,
        }
        if str(requester or "").strip():
            payload["requester"] = str(requester).strip()
        if str(product or "").strip():
            payload["product"] = str(product).strip()
        if str(query_policy or "").strip():
            payload["query_policy"] = str(query_policy).strip()
        if ticket_context is not None:
            payload["ticket_context"] = [
                {
                    "role": str(item.get("role") or "").strip(),
                    "content": str(item.get("content") or "").strip(),
                }
                for item in list(ticket_context or [])[-6:]
                if isinstance(item, dict)
                and str(item.get("role") or "").strip()
                and str(item.get("content") or "").strip()
            ]
        if top_k is not None:
            payload["top_k"] = int(top_k)
        return self._request("POST", "/internal/rag/query", json_body=payload, timeout_seconds=timeout_seconds)

    def cancel_request(self, request_id: str) -> dict[str, Any]:
        normalized_request_id = urllib.parse.quote(str(request_id or "").strip(), safe="")
        if not normalized_request_id:
            raise RagServiceError("request_id is required")
        return self._request(
            "POST",
            f"/internal/rag/requests/{normalized_request_id}/cancel",
        )

    def query_answer_with_recovery(
        self,
        *,
        question: str,
        request_id: str,
        ticket_id: str | None,
        customer_id: str | None,
        requester: str | None = None,
        ticket_context: list[dict[str, str]] | None = None,
        product: str | None = None,
        query_policy: str | None = None,
        insufficient_reply: str,
        top_k: int | None = None,
        timeout_seconds: float | None = None,
        recovery_attempts: int | None = None,
        recovery_delay_seconds: float | None = None,
        recovery_window_seconds: float | None = None,
        recovery_poll_interval_seconds: float | None = None,
    ) -> tuple[str, float, list[str], list[dict[str, str]], bool]:
        detail = self.query_answer_with_recovery_detail(
            question=question,
            request_id=request_id,
            ticket_id=ticket_id,
            customer_id=customer_id,
            requester=requester,
            ticket_context=ticket_context,
            query_policy=query_policy,
            insufficient_reply=insufficient_reply,
            top_k=top_k,
            timeout_seconds=timeout_seconds,
            recovery_attempts=recovery_attempts,
            recovery_delay_seconds=recovery_delay_seconds,
            recovery_window_seconds=recovery_window_seconds,
            recovery_poll_interval_seconds=recovery_poll_interval_seconds,
            **({"product": product} if str(product or "").strip() else {}),
        )
        return detail.as_answer_tuple()

    def query_answer_with_recovery_detail(
        self,
        *,
        question: str,
        request_id: str,
        ticket_id: str | None,
        customer_id: str | None,
        requester: str | None = None,
        ticket_context: list[dict[str, str]] | None = None,
        product: str | None = None,
        query_policy: str | None = None,
        insufficient_reply: str,
        top_k: int | None = None,
        timeout_seconds: float | None = None,
        recovery_attempts: int | None = None,
        recovery_delay_seconds: float | None = None,
        recovery_window_seconds: float | None = None,
        recovery_poll_interval_seconds: float | None = None,
    ) -> RagTicketAnswerDetail:
        try:
            query_kwargs: dict[str, Any] = {
                "question": question,
                "request_id": request_id,
                "ticket_id": ticket_id,
                "customer_id": customer_id,
                "requester": requester,
                "ticket_context": ticket_context,
                "top_k": top_k,
                "timeout_seconds": timeout_seconds,
            }
            if str(product or "").strip():
                query_kwargs["product"] = str(product).strip()
            if str(query_policy or "").strip():
                query_kwargs["query_policy"] = str(query_policy).strip()
            payload = self.query(
                **query_kwargs,
            )
        except RagServiceError as exc:
            payload = exc.payload if isinstance(exc.payload, dict) else {}
            if exc.status_code == 409 and str(payload.get("reason") or "").strip() == "cancelled_by_route_flip":
                raise
            recovery_started_at = time.monotonic()
            recovered = self._recover_ticket_answer_detail(
                request_id=request_id,
                recovery_attempts=recovery_attempts,
                recovery_delay_seconds=recovery_delay_seconds,
                recovery_window_seconds=recovery_window_seconds,
                recovery_poll_interval_seconds=recovery_poll_interval_seconds,
            )
            if recovered is not None:
                recovered = with_rag_detail_diagnostics(
                    recovered,
                    {
                        "rag_recovered_from_live_detail": True,
                        "rag_failure_kind": classify_rag_service_failure_kind(exc),
                    },
                )
                LOGGER.warning(
                    "Recovered RAG answer from live detail after query failure request_id=%s "
                    "recovery_source=live_detail recovery_elapsed_ms=%.1f error=%s",
                    request_id,
                    (time.monotonic() - recovery_started_at) * 1000,
                    exc,
                )
                return recovered
            raise
        return map_rag_payload_to_ticket_answer_detail(payload, insufficient_reply=insufficient_reply)

    def _recover_ticket_answer(
        self,
        *,
        request_id: str,
        recovery_attempts: int,
        recovery_delay_seconds: float,
    ) -> tuple[str, float, list[str], list[dict[str, str]], bool] | None:
        detail = self._recover_ticket_answer_detail(
            request_id=request_id,
            recovery_attempts=recovery_attempts,
            recovery_delay_seconds=recovery_delay_seconds,
        )
        return None if detail is None else detail.as_answer_tuple()

    def _recover_ticket_answer_detail(
        self,
        *,
        request_id: str,
        recovery_attempts: int | None,
        recovery_delay_seconds: float | None,
        recovery_window_seconds: float | None = None,
        recovery_poll_interval_seconds: float | None = None,
    ) -> RagTicketAnswerDetail | None:
        if recovery_attempts is None and recovery_delay_seconds is None:
            return self._recover_ticket_answer_detail_until_deadline(
                request_id=request_id,
                recovery_window_seconds=recovery_window_seconds,
                recovery_poll_interval_seconds=recovery_poll_interval_seconds,
            )
        return self._recover_ticket_answer_detail_with_attempts(
            request_id=request_id,
            recovery_attempts=recovery_attempts,
            recovery_delay_seconds=recovery_delay_seconds,
        )

    def _recover_ticket_answer_detail_with_attempts(
        self,
        *,
        request_id: str,
        recovery_attempts: int | None,
        recovery_delay_seconds: float | None,
    ) -> RagTicketAnswerDetail | None:
        try:
            attempts = max(1, int(recovery_attempts if recovery_attempts is not None else 3))
        except (TypeError, ValueError):
            attempts = 1
        try:
            delay_seconds = max(0.0, float(recovery_delay_seconds if recovery_delay_seconds is not None else 0.5))
        except (TypeError, ValueError):
            delay_seconds = 0.0

        for attempt in range(1, attempts + 1):
            try:
                payload = self.rag_dashboard_live_case_detail(request_id)
            except RagServiceError as exc:
                should_retry = attempt < attempts and (
                    exc.status_code is None
                    or exc.status_code == 404
                    or exc.status_code >= 500
                )
                if should_retry and delay_seconds > 0:
                    time.sleep(delay_seconds)
                if should_retry:
                    continue
                return None

            recovered = map_live_detail_payload_to_ticket_answer_detail(payload)
            if recovered is not None:
                return recovered
            if attempt < attempts and delay_seconds > 0:
                time.sleep(delay_seconds)
        return None

    def _recover_ticket_answer_detail_until_deadline(
        self,
        *,
        request_id: str,
        recovery_window_seconds: float | None = None,
        recovery_poll_interval_seconds: float | None = None,
    ) -> RagTicketAnswerDetail | None:
        deadline = time.monotonic() + max(
            0.0,
            float(recovery_window_seconds if recovery_window_seconds is not None else _recovery_window_seconds()),
        )
        poll_interval_seconds = max(
            0.0,
            float(
                recovery_poll_interval_seconds
                if recovery_poll_interval_seconds is not None
                else _recovery_poll_interval_seconds()
            ),
        )

        while True:
            try:
                payload = self.rag_dashboard_live_case_detail(request_id)
            except RagServiceError as exc:
                should_retry = (
                    exc.status_code is None
                    or exc.status_code == 404
                    or exc.status_code >= 500
                )
                now = time.monotonic()
                if not should_retry:
                    return None
                if now >= deadline:
                    return self._recover_ticket_answer_detail_final_probe(request_id=request_id)
                time.sleep(min(poll_interval_seconds, max(0.0, deadline - now)))
                continue

            recovered = map_live_detail_payload_to_ticket_answer_detail(payload)
            if recovered is not None:
                return recovered

            now = time.monotonic()
            if now >= deadline:
                return self._recover_ticket_answer_detail_final_probe(request_id=request_id)
            time.sleep(min(poll_interval_seconds, max(0.0, deadline - now)))
        return None

    def _recover_ticket_answer_detail_final_probe(
        self,
        *,
        request_id: str,
    ) -> RagTicketAnswerDetail | None:
        try:
            payload = self.rag_dashboard_live_case_detail(request_id)
        except RagServiceError:
            return None
        return map_live_detail_payload_to_ticket_answer_detail(payload)

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

    def get_ticket_family_token_summary(
        self,
        *,
        ticket_id: str,
        client_ticket_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_ticket_id = str(ticket_id or "").strip()
        if not normalized_ticket_id:
            raise RagServiceError("ticket_id is required")
        query = {"client_ticket_id": str(client_ticket_id or "").strip() or None}
        quoted = urllib.parse.quote(normalized_ticket_id, safe="")
        return self._request("GET", f"/internal/rag/ticket-families/{quoted}/token-usage", query=query)

    def rag_dashboard_page(
        self,
        page: str,
        *,
        range_value: str = "7d",
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        quoted = urllib.parse.quote(str(page or "").strip(), safe="")
        payload = dict(filters or {})
        payload["range"] = range_value
        return self._request("GET", f"/internal/dashboard/rag/{quoted}", query=payload)

    def rag_dashboard_benchmark_case_detail(
        self,
        eval_run_id: str,
        test_case_id: str,
        *,
        baseline_eval_run_id: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/internal/dashboard/rag/cases/benchmark-detail",
            query={
                "eval_run_id": eval_run_id,
                "test_case_id": test_case_id,
                "baseline_eval_run_id": baseline_eval_run_id,
            },
        )

    def rag_dashboard_live_case_detail(self, request_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/internal/dashboard/rag/cases/live-detail",
            query={"request_id": request_id},
        )

    def update_review_sample(
        self,
        sample_id: str,
        *,
        review_status: str | None = None,
        retrieval_ok: bool | None = None,
        answer_ok: bool | None = None,
        citation_ok: bool | None = None,
        logic_ok: bool | None = None,
        hallucination_present: bool | None = None,
        dataset_decision: str | None = None,
        corrected_reference_answer: str | None = None,
        corrected_citation_targets: list[dict[str, Any]] | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        quoted = urllib.parse.quote(str(sample_id or "").strip(), safe="")
        payload = {
            "review_status": review_status,
            "retrieval_ok": retrieval_ok,
            "answer_ok": answer_ok,
            "citation_ok": citation_ok,
            "logic_ok": logic_ok,
            "hallucination_present": hallucination_present,
            "dataset_decision": dataset_decision,
            "corrected_reference_answer": corrected_reference_answer,
            "corrected_citation_targets": corrected_citation_targets,
            "note": note,
        }
        return self._request(
            "POST",
            f"/internal/dashboard/rag/review-samples/{quoted}",
            json_body=payload,
        )

    def create_dataset_generation_run(
        self,
        *,
        dataset_name: str,
        source_types: list[str],
        question_language: str = "en",
    ) -> dict[str, Any]:
        payload = {
            "dataset_name": dataset_name,
            "source_types": source_types,
            "question_language": question_language,
        }
        return self._request(
            "POST",
            "/internal/dashboard/rag/datasets/generation-runs",
            json_body=payload,
        )

    def sync_local_benchmarks(self) -> dict[str, Any]:
        return self._request(
            "POST",
            "/internal/dashboard/rag/benchmarks/local-sync",
        )

    def create_local_benchmark_session_run(
        self,
        *,
        session_name: str | None = None,
        top_k: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "session_name": session_name,
            "top_k": top_k,
        }
        return self._request(
            "POST",
            "/internal/dashboard/rag/benchmarks/sessions/local-run",
            json_body=payload,
        )

    def create_dataset_benchmark_run(
        self,
        dataset_id: str,
        *,
        experiment_id: str | None = None,
        top_k: int | None = None,
        tier: str = "gold",
    ) -> dict[str, Any]:
        quoted = urllib.parse.quote(str(dataset_id or "").strip(), safe="")
        payload = {
            "experiment_id": experiment_id,
            "top_k": top_k,
            "tier": tier,
        }
        return self._request(
            "POST",
            f"/internal/dashboard/rag/datasets/{quoted}/benchmark-runs",
            json_body=payload,
        )

    def export_dataset_snapshot(self, dataset_id: str, *, tier: str = "gold") -> str:
        quoted = urllib.parse.quote(str(dataset_id or "").strip(), safe="")
        return self._request_text(
            "GET",
            f"/internal/dashboard/rag/datasets/{quoted}/export",
            query={"tier": tier},
        )

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
