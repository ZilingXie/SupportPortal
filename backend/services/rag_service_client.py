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


def map_live_detail_payload_to_ticket_answer(payload: dict[str, Any]) -> tuple[str, float, list[str], list[dict[str, str]], bool] | None:
    primary = payload.get("primary") if isinstance(payload, dict) else None
    if not isinstance(primary, dict):
        return None
    answer = str(primary.get("answer") or "").strip()
    if bool(primary.get("needs_human")) or not answer:
        return None
    try:
        confidence = float(primary.get("confidence_score") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    citations = primary.get("answer_citations") if isinstance(primary.get("answer_citations"), list) else []
    sources = primary.get("answer_sources") if isinstance(primary.get("answer_sources"), list) else []
    return (
        answer,
        confidence,
        [str(source) for source in sources if str(source).strip()],
        [item for item in citations if isinstance(item, dict)],
        False,
    )


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

    def _request_text(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        if not self.is_configured():
            raise RagServiceError("RAG service is not configured")

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

    def query_answer_with_recovery(
        self,
        *,
        question: str,
        request_id: str,
        ticket_id: str | None,
        customer_id: str | None,
        insufficient_reply: str,
        top_k: int | None = None,
        recovery_attempts: int = 3,
        recovery_delay_seconds: float = 0.5,
    ) -> tuple[str, float, list[str], list[dict[str, str]], bool]:
        try:
            payload = self.query(
                question=question,
                request_id=request_id,
                ticket_id=ticket_id,
                customer_id=customer_id,
                top_k=top_k,
            )
        except RagServiceError as exc:
            recovered = self._recover_ticket_answer(
                request_id=request_id,
                recovery_attempts=recovery_attempts,
                recovery_delay_seconds=recovery_delay_seconds,
            )
            if recovered is not None:
                LOGGER.warning(
                    "Recovered RAG answer from live detail after query failure request_id=%s error=%s",
                    request_id,
                    exc,
                )
                return recovered
            raise
        return map_rag_payload_to_ticket_answer(payload, insufficient_reply=insufficient_reply)

    def _recover_ticket_answer(
        self,
        *,
        request_id: str,
        recovery_attempts: int,
        recovery_delay_seconds: float,
    ) -> tuple[str, float, list[str], list[dict[str, str]], bool] | None:
        try:
            attempts = max(1, int(recovery_attempts))
        except (TypeError, ValueError):
            attempts = 1
        try:
            delay_seconds = max(0.0, float(recovery_delay_seconds))
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

            recovered = map_live_detail_payload_to_ticket_answer(payload)
            if recovered is not None:
                return recovered
            if attempt < attempts and delay_seconds > 0:
                time.sleep(delay_seconds)
        return None

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
