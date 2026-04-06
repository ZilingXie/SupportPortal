from __future__ import annotations

import json
import re
import shutil
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from backend.repositories.knowledge_repository import KnowledgeRepository

SITEMAP_URL = "https://docs.agora.io/en/sitemap.xml"
LLMS_URL = "https://docs.agora.io/en/llms.txt"
HTML_DOCS_HOST = "docs.agora.io"
MARKDOWN_DOCS_HOST = "docs-md.agora.io"
REPORT_FILE_NAME = "_sync_report.json"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_API_TIMEOUT_SECONDS = 90.0
USER_AGENT = "SupportPortalAgoraDocSync/1.0"
FINAL_INGESTION_STATUSES = {"completed", "failed"}


@dataclass(frozen=True)
class SyncConfig:
    output_dir: Path
    api_base_url: str | None = None
    download_workers: int = 8
    upload_workers: int = 4
    poll_interval_seconds: float = 2.0
    poll_timeout_seconds: float = 300.0
    limit: int | None = None


@dataclass(frozen=True)
class DiscoveryItem:
    discovery_url: str
    markdown_url: str
    local_path: str


@dataclass(frozen=True)
class DownloadResult:
    discovery_url: str
    markdown_url: str
    local_path: str
    status: str
    status_code: int | None = None
    size_bytes: int = 0
    error: str | None = None


@dataclass(frozen=True)
class PollResult:
    status: str | None
    ingestion: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    timed_out: bool = False
    error: str | None = None


@dataclass(frozen=True)
class UploadResult:
    local_path: str
    upload_status: str
    ingestion_id: str | None = None
    ingestion_status: str | None = None
    queued: bool | None = None
    processing_mode: str | None = None
    http_status: int | None = None
    chunk_count: int | None = None
    document_id: str | None = None
    dedupe_action: str | None = None
    error_message: str | None = None
    error: str | None = None
    report_warning_count: int = 0


class SyncHttpError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: bytes | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body or b""
        self.url = url


class KnowledgeApiClient:
    def __init__(self, *, base_url: str, timeout_seconds: float = DEFAULT_API_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def upload_official_document(self, file_path: Path) -> tuple[int, dict[str, Any]]:
        body, content_type = _encode_multipart_form_data(
            file_name=file_path.name,
            content=file_path.read_bytes(),
        )
        return self._request_json(
            path="/api/engineer/knowledge/official-documents",
            method="POST",
            data=body,
            headers={
                "Content-Type": content_type,
                "Accept": "application/json",
            },
        )

    def get_ingestion(self, ingestion_id: str) -> dict[str, Any]:
        _, payload = self._request_json(
            path=f"/api/engineer/knowledge/ingestions/{urllib.parse.quote(ingestion_id, safe='')}",
            method="GET",
            headers={"Accept": "application/json"},
        )
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected ingestion payload type for {ingestion_id}")
        return payload

    def get_ingestion_report(self, ingestion_id: str) -> dict[str, Any]:
        _, payload = self._request_json(
            path=f"/api/dashboard/knowledge-ingestions/{urllib.parse.quote(ingestion_id, safe='')}/report",
            method="GET",
            headers={"Accept": "application/json"},
        )
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected ingestion report payload type for {ingestion_id}")
        return payload

    def _request_json(
        self,
        *,
        path: str,
        method: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        status_code, raw_body = _request_bytes(
            f"{self.base_url}{path}",
            method=method,
            data=data,
            headers=headers,
            timeout_seconds=self.timeout_seconds,
        )
        payload = _json_loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError(f"Expected a JSON object from {path}")
        return status_code, payload


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def html_url_to_markdown_url(html_url: str) -> str:
    parsed = urllib.parse.urlparse(html_url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if host == MARKDOWN_DOCS_HOST and path.endswith(".md"):
        return urllib.parse.urlunparse(("https", MARKDOWN_DOCS_HOST, path, "", "", ""))
    if host != HTML_DOCS_HOST:
        raise ValueError(f"Expected {HTML_DOCS_HOST} URL, got: {html_url}")
    if not path:
        raise ValueError(f"Documentation URL missing path: {html_url}")

    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
    platforms = [item.strip() for item in query.get("platform", []) if item.strip()]
    if platforms:
        return f"https://{MARKDOWN_DOCS_HOST}{path}_{platforms[0]}.md"
    return f"https://{MARKDOWN_DOCS_HOST}{path}.md"


def output_relative_path_from_markdown_url(markdown_url: str) -> str:
    parsed = urllib.parse.urlparse(markdown_url)
    host = parsed.netloc.lower()
    if host != MARKDOWN_DOCS_HOST:
        raise ValueError(f"Expected {MARKDOWN_DOCS_HOST} URL, got: {markdown_url}")
    relative_path = parsed.path.lstrip("/")
    if not relative_path.endswith(".md"):
        raise ValueError(f"Markdown URL must end with .md: {markdown_url}")
    return relative_path


def extract_markdown_urls_from_llms_text(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s)>]+", text)
    extracted: list[str] = []
    seen: set[str] = set()
    for candidate in urls:
        normalized = candidate.rstrip(".,;:")
        parsed = urllib.parse.urlparse(normalized)
        if parsed.netloc.lower() != MARKDOWN_DOCS_HOST:
            continue
        if not parsed.path.endswith(".md"):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        extracted.append(normalized)
    return extracted


def extract_ingestion_id_from_upload_payload(payload: dict[str, Any]) -> str:
    ingestion = payload.get("ingestion") if isinstance(payload.get("ingestion"), dict) else {}
    candidates = [
        ingestion.get("ingestion_id"),
        payload.get("ingestion_id"),
    ]
    for candidate in candidates:
        normalized = clean_text(candidate)
        if normalized:
            return normalized
    raise ValueError("Upload response did not include an ingestion_id")


def wait_for_ingestion_completion(
    *,
    client: KnowledgeApiClient,
    ingestion_id: str,
    poll_interval_seconds: float,
    poll_timeout_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> PollResult:
    timeout = max(0.0, float(poll_timeout_seconds))
    deadline = monotonic() + timeout
    last_ingestion: dict[str, Any] | None = None
    last_status: str | None = None
    last_error: str | None = None

    while True:
        try:
            payload = client.get_ingestion(ingestion_id)
            ingestion = payload.get("ingestion") if isinstance(payload.get("ingestion"), dict) else payload
            last_ingestion = ingestion if isinstance(ingestion, dict) else {}
            last_status = clean_text(last_ingestion.get("status")).lower() or None
            last_error = None
        except Exception as exc:  # pragma: no cover - exercised via timeout path
            last_error = str(exc)

        if last_status in FINAL_INGESTION_STATUSES:
            report: dict[str, Any] | None = None
            report_error: str | None = None
            try:
                report = client.get_ingestion_report(ingestion_id)
            except Exception as exc:  # pragma: no cover - exercised in report error path
                report_error = str(exc)
            return PollResult(
                status=last_status,
                ingestion=last_ingestion,
                report=report,
                timed_out=False,
                error=report_error or last_error,
            )

        if monotonic() >= deadline:
            timeout_message = (
                last_error
                or f"Timed out waiting for ingestion {ingestion_id} after {timeout:.1f}s "
                f"(last_status={last_status or 'unknown'})"
            )
            return PollResult(
                status=last_status,
                ingestion=last_ingestion,
                report=None,
                timed_out=True,
                error=timeout_message,
            )

        sleep(max(0.0, float(poll_interval_seconds)))


def build_sync_report(
    *,
    started_at: str,
    finished_at: str,
    config: SyncConfig,
    discovery: dict[str, Any],
    download_results: list[DownloadResult],
    upload_results: list[UploadResult],
    run_error: str | None,
) -> dict[str, Any]:
    download_successes = sum(1 for item in download_results if item.status == "downloaded")
    download_failures = sum(1 for item in download_results if item.status != "downloaded")

    completed = sum(1 for item in upload_results if item.upload_status == "completed")
    upload_failures = sum(1 for item in upload_results if item.upload_status == "upload_failed")
    ingestion_failures = sum(1 for item in upload_results if item.upload_status == "ingestion_failed")
    timeouts = sum(1 for item in upload_results if item.upload_status == "timed_out")

    success = (
        run_error is None
        and discovery.get("selected_count", 0) > 0
        and download_failures == 0
        and upload_failures == 0
        and ingestion_failures == 0
        and timeouts == 0
    )

    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "api_base_url": config.api_base_url,
        "output_dir": str(config.output_dir),
        "limit": config.limit,
        "success": success,
        "run_error": run_error,
        "discovery": discovery,
        "downloads": {
            "attempted": len(download_results),
            "succeeded": download_successes,
            "failed": download_failures,
            "items": [asdict(item) for item in download_results],
        },
        "uploads": {
            "attempted": len(upload_results),
            "completed": completed,
            "upload_failed": upload_failures,
            "ingestion_failed": ingestion_failures,
            "timed_out": timeouts,
            "items": [asdict(item) for item in upload_results],
        },
    }


def write_sync_report(output_dir: Path, report: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / REPORT_FILE_NAME
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report_path


def run_sync(config: SyncConfig) -> tuple[int, dict[str, Any], Path]:
    started_at = now_iso()
    discovery_info: dict[str, Any] = {
        "attempted_sources": [],
        "selected_source": None,
        "errors": [],
        "total_discovered": 0,
        "selected_count": 0,
        "effective_limit": config.limit,
    }
    download_results: list[DownloadResult] = []
    upload_results: list[UploadResult] = []
    run_error: str | None = None

    _reset_output_dir(config.output_dir)

    try:
        discovery_items, discovery_info = discover_documents(limit=config.limit)
        if not discovery_items:
            raise RuntimeError("No Agora documentation URLs were discovered")

        download_results = download_documents(
            items=discovery_items,
            output_dir=config.output_dir,
            workers=config.download_workers,
        )
        downloaded_markdown_files = sorted(config.output_dir.rglob("*.md"))
        if not downloaded_markdown_files:
            raise RuntimeError("No Markdown documents were downloaded successfully")

        if config.api_base_url:
            client = KnowledgeApiClient(base_url=config.api_base_url)
            upload_results = upload_documents(
                markdown_files=downloaded_markdown_files,
                output_dir=config.output_dir,
                client=client,
                workers=config.upload_workers,
                poll_interval_seconds=config.poll_interval_seconds,
                poll_timeout_seconds=config.poll_timeout_seconds,
            )
        else:
            upload_results = ingest_documents_locally(
                markdown_files=downloaded_markdown_files,
                output_dir=config.output_dir,
                workers=config.upload_workers,
            )
    except Exception as exc:
        run_error = str(exc)

    finished_at = now_iso()
    report = build_sync_report(
        started_at=started_at,
        finished_at=finished_at,
        config=config,
        discovery=discovery_info,
        download_results=download_results,
        upload_results=upload_results,
        run_error=run_error,
    )
    report_path = write_sync_report(config.output_dir, report)
    exit_code = 0 if report["success"] else 1
    return exit_code, report, report_path


def ingest_documents_locally(
    *,
    markdown_files: list[Path],
    output_dir: Path,
    workers: int = 1,
) -> list[UploadResult]:
    if not markdown_files:
        return []
    from backend.repositories.knowledge_repository import create_knowledge_repository

    repository = create_knowledge_repository()
    repository.initialize()
    max_workers = max(1, int(workers))
    sync_run = repository.create_sync_run(
        source_system="agora",
        knowledge_type="official",
        status="running",
        host_name=socket.gethostname(),
        config_snapshot={
            "mode": "local_direct",
            "output_dir": str(output_dir),
            "file_count": len(markdown_files),
            "workers": max_workers,
        },
    )
    if max_workers == 1:
        results = [
            _ingest_single_document(
                file_path=file_path,
                output_dir=output_dir,
                repository=repository,
                sync_run_id=sync_run["sync_run_id"],
            )
            for file_path in markdown_files
        ]
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _ingest_single_document,
                    file_path=file_path,
                    output_dir=output_dir,
                    repository=repository,
                    sync_run_id=sync_run["sync_run_id"],
                ): file_path
                for file_path in markdown_files
            }
            results = [future.result() for future in as_completed(futures)]
    repository.update_sync_run(
        sync_run["sync_run_id"],
        status="completed" if all(item.upload_status == "completed" for item in results) else "failed",
        discovered_count=len(markdown_files),
        claimed_count=len(markdown_files),
        processed_count=sum(1 for item in results if item.upload_status == "completed"),
        failed_count=sum(1 for item in results if item.upload_status != "completed"),
        summary={
            "processed": sum(1 for item in results if item.upload_status == "completed"),
            "failed": sum(1 for item in results if item.upload_status != "completed"),
            "ingestion_ids": [item.ingestion_id for item in results if item.ingestion_id],
        },
    )
    return sorted(results, key=lambda item: item.local_path)


def discover_documents(*, limit: int | None) -> tuple[list[DiscoveryItem], dict[str, Any]]:
    attempted_sources: list[str] = []
    discovery_errors: list[str] = []

    try:
        attempted_sources.append("sitemap")
        html_urls = _discover_html_urls_from_sitemap()
        items = [
            DiscoveryItem(
                discovery_url=url,
                markdown_url=html_url_to_markdown_url(url),
                local_path=output_relative_path_from_markdown_url(html_url_to_markdown_url(url)),
            )
            for url in html_urls
        ]
        selected_source = "sitemap"
    except Exception as sitemap_exc:
        discovery_errors.append(str(sitemap_exc))
        attempted_sources.append("llms")
        try:
            markdown_urls = _discover_markdown_urls_from_llms()
        except Exception as llms_exc:
            discovery_errors.append(str(llms_exc))
            joined = " | ".join(discovery_errors)
            raise RuntimeError(f"Failed to discover Agora documentation: {joined}") from llms_exc
        items = [
            DiscoveryItem(
                discovery_url=url,
                markdown_url=url,
                local_path=output_relative_path_from_markdown_url(url),
            )
            for url in markdown_urls
        ]
        selected_source = "llms"

    unique_items = _dedupe_discovery_items(items)
    total_discovered = len(unique_items)
    if limit is not None:
        unique_items = unique_items[: max(0, int(limit))]

    discovery_info = {
        "attempted_sources": attempted_sources,
        "selected_source": selected_source,
        "errors": discovery_errors,
        "total_discovered": total_discovered,
        "selected_count": len(unique_items),
        "effective_limit": limit,
    }
    return unique_items, discovery_info


def download_documents(
    *,
    items: list[DiscoveryItem],
    output_dir: Path,
    workers: int,
) -> list[DownloadResult]:
    if not items:
        return []

    max_workers = max(1, int(workers))
    results: list[DownloadResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_download_single_document, item=item, output_dir=output_dir): item
            for item in items
        }
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: item.local_path)


def upload_documents(
    *,
    markdown_files: list[Path],
    output_dir: Path,
    client: KnowledgeApiClient,
    workers: int,
    poll_interval_seconds: float,
    poll_timeout_seconds: float,
) -> list[UploadResult]:
    if not markdown_files:
        return []

    max_workers = max(1, int(workers))
    results: list[UploadResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _upload_single_document,
                file_path=file_path,
                output_dir=output_dir,
                client=client,
                poll_interval_seconds=poll_interval_seconds,
                poll_timeout_seconds=poll_timeout_seconds,
            ): file_path
            for file_path in markdown_files
        }
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: item.local_path)


def _ingest_single_document(
    *,
    file_path: Path,
    output_dir: Path,
    repository: "KnowledgeRepository",
    sync_run_id: str | None = None,
) -> UploadResult:
    from backend.services.local_source_sync import ingest_source_document, stage_source_document

    local_path = file_path.relative_to(output_dir).as_posix()
    borrow_scope = getattr(repository, "borrow_local_direct_write_connection", None)
    try:
        with (borrow_scope() if callable(borrow_scope) else nullcontext()):
            source_document = stage_source_document(
                repository,
                knowledge_type="official",
                source_system="agora",
                external_id=local_path,
                title=file_path.stem,
                source_url=None,
                published_url=None,
                content_format="markdown",
                raw_content=file_path.read_text(encoding="utf-8"),
                metadata={
                    "sync_mode": "local_direct",
                    "source_absolute_path": str(file_path),
                    "source_relative_path": local_path,
                },
            )
            result = ingest_source_document(
                repository,
                source_document,
                sync_mode="local_direct",
                sync_run_id=sync_run_id,
            )
            report = repository.get_ingestion_report(result.ingestion_id) or {}
            summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
            warning_count = len(report.get("warnings") or []) if isinstance(report.get("warnings"), list) else 0
            return UploadResult(
                local_path=local_path,
                upload_status="completed" if result.status == "completed" else "ingestion_failed",
                ingestion_id=result.ingestion_id,
                ingestion_status=result.status,
                queued=False,
                processing_mode="local_direct",
                http_status=None,
                chunk_count=result.chunk_count if result.chunk_count is not None else summary.get("chunk_count"),
                document_id=result.document_id or summary.get("document_id"),
                dedupe_action=result.dedupe_action or summary.get("dedupe_action"),
                error_message=result.error_message or summary.get("error_message"),
                error=None,
                report_warning_count=warning_count,
            )
    except Exception as exc:
        report = None
        summary = report.get("summary") if isinstance(report, dict) and isinstance(report.get("summary"), dict) else {}
        return UploadResult(
            local_path=local_path,
            upload_status="upload_failed",
            ingestion_id=None,
            ingestion_status=None,
            queued=False,
            processing_mode="local_direct",
            http_status=None,
            chunk_count=summary.get("chunk_count"),
            document_id=summary.get("document_id"),
            dedupe_action=summary.get("dedupe_action"),
            error_message=clean_text(summary.get("error_message")) or str(exc),
            error=str(exc),
            report_warning_count=len(report.get("warnings") or []) if isinstance(report, dict) and isinstance(report.get("warnings"), list) else 0,
        )


def _discover_html_urls_from_sitemap() -> list[str]:
    _, raw_body = _request_bytes(
        SITEMAP_URL,
        method="GET",
        headers={"Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8"},
        timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
    )
    root = ET.fromstring(raw_body)
    namespace = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls: list[str] = []
    seen: set[str] = set()
    for loc in root.findall(".//ns:loc", namespace):
        value = clean_text(loc.text)
        if not value:
            continue
        parsed = urllib.parse.urlparse(value)
        if parsed.netloc.lower() != HTML_DOCS_HOST:
            continue
        if not parsed.path.startswith("/en/"):
            continue
        if value in seen:
            continue
        seen.add(value)
        urls.append(value)
    if not urls:
        raise RuntimeError("Agora sitemap did not contain any English documentation URLs")
    return urls


def _discover_markdown_urls_from_llms() -> list[str]:
    _, raw_body = _request_bytes(
        LLMS_URL,
        method="GET",
        headers={"Accept": "text/plain,*/*;q=0.8"},
        timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
    )
    markdown_urls = extract_markdown_urls_from_llms_text(raw_body.decode("utf-8", errors="replace"))
    if not markdown_urls:
        raise RuntimeError("Agora llms.txt did not contain any Markdown documentation URLs")
    return markdown_urls


def _download_single_document(*, item: DiscoveryItem, output_dir: Path) -> DownloadResult:
    try:
        status_code, raw_body = _request_bytes(
            item.markdown_url,
            method="GET",
            headers={"Accept": "text/markdown,*/*;q=0.8"},
            timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        )
        if not raw_body:
            return DownloadResult(
                discovery_url=item.discovery_url,
                markdown_url=item.markdown_url,
                local_path=item.local_path,
                status="failed",
                status_code=status_code,
                error="Downloaded document is empty",
            )

        destination = output_dir / item.local_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw_body)
        return DownloadResult(
            discovery_url=item.discovery_url,
            markdown_url=item.markdown_url,
            local_path=item.local_path,
            status="downloaded",
            status_code=status_code,
            size_bytes=len(raw_body),
        )
    except Exception as exc:
        status_code = exc.status_code if isinstance(exc, SyncHttpError) else None
        return DownloadResult(
            discovery_url=item.discovery_url,
            markdown_url=item.markdown_url,
            local_path=item.local_path,
            status="failed",
            status_code=status_code,
            error=str(exc),
        )


def _upload_single_document(
    *,
    file_path: Path,
    output_dir: Path,
    client: KnowledgeApiClient,
    poll_interval_seconds: float,
    poll_timeout_seconds: float,
) -> UploadResult:
    relative_path = file_path.relative_to(output_dir).as_posix()
    try:
        status_code, payload = client.upload_official_document(file_path)
        ingestion_id = extract_ingestion_id_from_upload_payload(payload)
    except Exception as exc:
        return UploadResult(
            local_path=relative_path,
            upload_status="upload_failed",
            error=str(exc),
        )

    queued_raw = payload.get("queued")
    queued = queued_raw if isinstance(queued_raw, bool) else None
    processing_mode = clean_text(payload.get("processing_mode")) or None

    poll_result = wait_for_ingestion_completion(
        client=client,
        ingestion_id=ingestion_id,
        poll_interval_seconds=poll_interval_seconds,
        poll_timeout_seconds=poll_timeout_seconds,
    )
    ingestion_payload = poll_result.ingestion if isinstance(poll_result.ingestion, dict) else {}
    report_payload = poll_result.report if isinstance(poll_result.report, dict) else {}
    summary_payload = report_payload.get("summary") if isinstance(report_payload.get("summary"), dict) else {}
    warnings_payload = report_payload.get("warnings") if isinstance(report_payload.get("warnings"), list) else []

    error_message = (
        clean_text(ingestion_payload.get("error_message"))
        or clean_text(summary_payload.get("error_message"))
        or None
    )

    upload_status = "completed"
    if poll_result.timed_out:
        upload_status = "timed_out"
    elif poll_result.status == "failed":
        upload_status = "ingestion_failed"

    return UploadResult(
        local_path=relative_path,
        upload_status=upload_status,
        ingestion_id=ingestion_id,
        ingestion_status=poll_result.status,
        queued=queued,
        processing_mode=processing_mode,
        http_status=status_code,
        chunk_count=_safe_int(summary_payload.get("chunk_count") or ingestion_payload.get("chunk_count")),
        document_id=clean_text(summary_payload.get("document_id") or ingestion_payload.get("document_id")) or None,
        dedupe_action=clean_text(summary_payload.get("dedupe_action")) or None,
        error_message=error_message,
        error=poll_result.error,
        report_warning_count=len([item for item in warnings_payload if clean_text(item)]),
    )


def _reset_output_dir(output_dir: Path) -> None:
    resolved = output_dir.resolve()
    guarded_paths = {
        Path("/").resolve(),
        Path.home().resolve(),
        Path.cwd().resolve(),
    }
    if resolved in guarded_paths:
        raise ValueError(f"Refusing to delete unsafe output directory: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


def _request_bytes(
    url: str,
    *,
    method: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float,
) -> tuple[int, bytes]:
    request_headers = {
        "User-Agent": USER_AGENT,
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        url,
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = getattr(response, "status", response.getcode())
            return int(status_code), response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        raise SyncHttpError(
            f"HTTP {exc.code} for {url}",
            status_code=int(exc.code),
            body=body,
            url=url,
        ) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise SyncHttpError(f"Request failed for {url}: {exc}", url=url) from exc


def _json_loads(raw_body: bytes) -> dict[str, Any]:
    text = raw_body.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Response was not valid JSON: {text[:200]}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Response JSON must be an object")
    return parsed


def _encode_multipart_form_data(*, file_name: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"----SupportPortalAgoraBoundary{int(time.time() * 1000)}"
    lines: list[bytes] = [
        f"--{boundary}".encode("utf-8"),
        (
            'Content-Disposition: form-data; name="file"; '
            f'filename="{file_name}"'
        ).encode("utf-8"),
        b"Content-Type: text/markdown",
        b"",
        content,
        f"--{boundary}--".encode("utf-8"),
        b"",
    ]
    return b"\r\n".join(lines), f"multipart/form-data; boundary={boundary}"


def _dedupe_discovery_items(items: list[DiscoveryItem]) -> list[DiscoveryItem]:
    deduped: list[DiscoveryItem] = []
    seen: set[str] = set()
    for item in items:
        key = f"{item.markdown_url}::{item.local_path}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None
