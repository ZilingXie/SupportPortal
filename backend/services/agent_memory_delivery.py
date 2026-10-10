"""AgentMemory Wiki delivery client and adapter (WeKnora dual-write phase 2).

SupportPortal writes AgentMemory through the SAME public Wiki API the n8n
chains used (verified contract, see docs/deploy_hermes_investigator_ecs.md
"n8n 导入文章四步 API"):

- POST {base}/api/v1/knowledge/wiki/create     {"team_id","name"} -> data.wiki_id
- POST {base}/api/v1/knowledge/wiki/raw/write  {"team_id","wiki_id","files":[{"filename","content"}]}
- POST {base}/api/v1/knowledge/wiki/ingest     {"wiki_id"}        (async LLM build)
- POST {base}/api/v1/knowledge/wiki/get        {"wiki_id"}        (readback)
- GET  {base}/health                                              (availability probe)

Headers: X-Tdai-Service-Id, X-Tdai-User-Key, Content-Type: application/json.

The Wiki API has no idempotency key and no team-wide wiki listing, so the
adapter's failure model is stricter than WeKnora's:

- content-addressed filename (sp-<content_hash>.md) makes raw/write, ingest
  and readback replayable within a known wiki_id;
- the wiki_id is captured durably the moment create answers (before any
  follow-up call can time out);
- a create whose outcome is unknown (timeout, unparseable receipt) and that
  left no wiki_id can NEVER be blindly retried — there is no list API to
  reconcile a possibly-created wiki, so the delivery escalates to
  human_review instead of risking a duplicate wiki.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

AGENT_MEMORY_WIKI_BASE_URL_ENV = "AGENT_MEMORY_WIKI_BASE_URL"
AGENT_MEMORY_WIKI_USER_KEY_ENV = "AGENT_MEMORY_WIKI_USER_KEY"
AGENT_MEMORY_WIKI_SERVICE_ID_ENV = "AGENT_MEMORY_WIKI_SERVICE_ID"
AGENT_MEMORY_WIKI_TEAM_ID_ENV = "AGENT_MEMORY_WIKI_TEAM_ID"
AGENT_MEMORY_WIKI_TIMEOUT_ENV = "AGENT_MEMORY_WIKI_TIMEOUT_SECONDS"

DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
# The raw/write endpoint enforces 512KB per file (verified contract).
MAX_FILE_BYTES = 512 * 1024

_RETRYABLE_FAILURE_KINDS = frozenset({"timeout", "transport"})


class AgentMemoryWikiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failure_kind: str,
        status_code: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind
        self.status_code = status_code
        self.payload = payload

    @property
    def retryable(self) -> bool:
        if self.failure_kind in _RETRYABLE_FAILURE_KINDS:
            return True
        return self.status_code is not None and self.status_code >= 500


class AgentMemoryWikiClient:
    """Thin contract-pinned client for the AgentMemory Wiki write API."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        user_key: str | None = None,
        service_id: str | None = None,
        team_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._base_url = str(
            base_url if base_url is not None else os.getenv(AGENT_MEMORY_WIKI_BASE_URL_ENV) or ""
        ).strip().rstrip("/")
        self._user_key = str(
            user_key if user_key is not None else os.getenv(AGENT_MEMORY_WIKI_USER_KEY_ENV) or ""
        ).strip()
        self._service_id = str(
            service_id
            if service_id is not None
            else os.getenv(AGENT_MEMORY_WIKI_SERVICE_ID_ENV) or "default"
        ).strip()
        self._team_id = str(
            team_id if team_id is not None else os.getenv(AGENT_MEMORY_WIKI_TEAM_ID_ENV) or ""
        ).strip()
        try:
            self._timeout = float(
                timeout_seconds
                if timeout_seconds is not None
                else os.getenv(AGENT_MEMORY_WIKI_TIMEOUT_ENV) or DEFAULT_REQUEST_TIMEOUT_SECONDS
            )
        except (TypeError, ValueError):
            self._timeout = DEFAULT_REQUEST_TIMEOUT_SECONDS

    def configured(self) -> bool:
        return bool(self._base_url and self._user_key and self._team_id)

    # ---------------------------------------------------------------- request

    def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8") if json_body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("X-Tdai-Service-Id", self._service_id)
        request.add_header("X-Tdai-User-Key", self._user_key)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - the status already classifies
                pass
            kind = "auth" if exc.code in (401, 403) else (
                "not_found" if exc.code == 404 else "http"
            )
            raise AgentMemoryWikiError(
                f"AgentMemory Wiki API {path} failed: HTTP {exc.code}",
                failure_kind=kind,
                status_code=exc.code,
                payload=_safe_json(body),
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
                raise AgentMemoryWikiError(
                    f"AgentMemory Wiki API {path} timed out",
                    failure_kind="timeout",
                ) from exc
            raise AgentMemoryWikiError(
                f"AgentMemory Wiki API {path} transport failure: {exc}",
                failure_kind="transport",
            ) from exc
        except TimeoutError as exc:
            raise AgentMemoryWikiError(
                f"AgentMemory Wiki API {path} timed out",
                failure_kind="timeout",
            ) from exc
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise AgentMemoryWikiError(
                f"AgentMemory Wiki API {path} returned an unparseable body",
                failure_kind="invalid_response",
                payload={"body": body[:500]},
            ) from exc
        if not isinstance(parsed, dict):
            raise AgentMemoryWikiError(
                f"AgentMemory Wiki API {path} returned a non-object body",
                failure_kind="invalid_response",
                payload={"body": body[:500]},
            )
        # The panel-shaped APIs answer {"success": false, ...} with HTTP 200;
        # a falsy success flag is an explicit failure, never an empty success.
        if parsed.get("success") is False:
            raise AgentMemoryWikiError(
                f"AgentMemory Wiki API {path} answered success=false",
                failure_kind="http",
                payload=parsed,
            )
        return parsed

    # --------------------------------------------------------------- surface

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def wiki_create(self, *, name: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/knowledge/wiki/create",
            json_body={"team_id": self._team_id, "name": str(name or "")},
        )

    def wiki_raw_write(self, *, wiki_id: str, filename: str, content: str) -> dict[str, Any]:
        encoded = str(content or "").encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise AgentMemoryWikiError(
                f"candidate body exceeds the Wiki raw/write per-file limit ({MAX_FILE_BYTES} bytes)",
                failure_kind="payload_too_large",
                payload={"filename": filename, "bytes": len(encoded)},
            )
        return self._request(
            "POST",
            "/api/v1/knowledge/wiki/raw/write",
            json_body={
                "team_id": self._team_id,
                "wiki_id": str(wiki_id or ""),
                "files": [{"filename": str(filename or ""), "content": str(content or "")}],
            },
        )

    def wiki_ingest(self, *, wiki_id: str) -> dict[str, Any]:
        return self._request(
            "POST", "/api/v1/knowledge/wiki/ingest", json_body={"wiki_id": str(wiki_id or "")}
        )

    def wiki_get(self, *, wiki_id: str) -> dict[str, Any]:
        return self._request(
            "POST", "/api/v1/knowledge/wiki/get", json_body={"wiki_id": str(wiki_id or "")}
        )

    # -- read surface (dual-write phase 2, stage 3) --------------------------
    # Verified against the live preproduction panel API (2026-10-10):
    #   POST /api/v1/knowledge/wiki/list   {"team_id", "limit", "offset"}
    #        -> data {items: [{wiki_id, name, status, version, page_count, ...}],
    #                 total}
    #   POST /api/v1/knowledge/wiki/search {"wiki_id", "query", "top_k"?}
    #        -> data {count, results: [{title, snippet, score, path, type, hop}]}
    # The API has no global knowledge search; the fan-out below (list ready
    # wikis, search each) is the only verified way to answer "could this
    # knowledge already exist in AgentMemory".

    WIKI_LIST_PAGE_SIZE = 100
    DEFAULT_EVIDENCE_WIKI_CAP = 100
    DEFAULT_SEARCH_TOP_K = 5

    def wiki_list(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """One page of the team's wiki inventory as ``{"items": [...], "total": n}``."""
        payload = self._request(
            "POST",
            "/api/v1/knowledge/wiki/list",
            json_body={
                "team_id": self._team_id,
                "limit": int(limit),
                "offset": int(offset),
            },
        )
        data = _data(payload)
        items = data.get("items") if isinstance(data.get("items"), list) else None
        total = data.get("total")
        if items is None or not isinstance(total, int):
            raise AgentMemoryWikiError(
                "AgentMemory wiki list response is missing items/total",
                failure_kind="invalid_response",
                payload=payload,
            )
        return {"items": items, "total": total}

    def wiki_search(self, *, wiki_id: str, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        """Per-wiki knowledge search (verified contract; hits carry title,
        snippet, score and path)."""
        body: dict[str, Any] = {"wiki_id": str(wiki_id or ""), "query": str(query or "")}
        if top_k is not None:
            body["top_k"] = int(top_k)
        payload = self._request("POST", "/api/v1/knowledge/wiki/search", json_body=body)
        data = _data(payload)
        results = data.get("results") if isinstance(data.get("results"), list) else None
        if results is None:
            raise AgentMemoryWikiError(
                "AgentMemory wiki search response is missing results",
                failure_kind="invalid_response",
                payload=payload,
            )
        return results

    def search_knowledge(
        self, query: str, *, wiki_cap: int | None = None, top_k: int | None = None
    ) -> dict[str, Any]:
        """Fan-out knowledge search across the team's READY wikis.

        Returns ``{"wiki_count", "searched", "hits": [...]}`` where every hit
        carries its wiki identity.  Raises AgentMemoryWikiError on any list or
        search failure — a partial sweep could miss a duplicate, so callers
        treat the whole surface as unavailable (fail-closed).
        """
        cap = int(wiki_cap if wiki_cap is not None else self.DEFAULT_EVIDENCE_WIKI_CAP)
        limit = self.WIKI_LIST_PAGE_SIZE
        wikis: list[dict[str, Any]] = []
        offset = 0
        while offset < cap:
            page = self.wiki_list(limit=min(limit, cap - offset), offset=offset)
            wikis.extend(page["items"])
            if len(wikis) >= page["total"]:
                break
            offset = len(wikis)
        ready = [
            item for item in wikis
            if str(item.get("status") or "") == "ready" and str(item.get("wiki_id") or "")
        ]
        hits: list[dict[str, Any]] = []
        for item in ready:
            results = self.wiki_search(
                wiki_id=str(item["wiki_id"]), query=query,
                top_k=top_k if top_k is not None else self.DEFAULT_SEARCH_TOP_K,
            )
            for result in results:
                if not isinstance(result, dict):
                    continue
                hits.append({
                    "wiki_id": str(item.get("wiki_id") or ""),
                    "wiki_name": str(item.get("name") or ""),
                    "wiki_version": str(item.get("version") or ""),
                    "title": str(result.get("title") or ""),
                    "snippet": str(result.get("snippet") or "")[:500],
                    "score": result.get("score"),
                    "path": str(result.get("path") or ""),
                })
        return {"wiki_count": len(wikis), "searched": len(ready), "hits": hits}


def _safe_json(body: str) -> Any:
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError:
        return {"body": body[:500]}


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else {}


def agent_memory_wiki_name(payload: dict[str, Any], *, content_hash: str) -> str:
    """Deterministic wiki name for a `new` candidate.

    The title plus the content hash prefix keeps the wiki recognizable in the
    panel while making an accidental same-name duplicate from a DIFFERENT
    candidate effectively impossible.
    """
    title = str(payload.get("title") or "").strip()
    head = (title.splitlines()[0].strip()[:60]) if title else "SupportPortal knowledge"
    return f"{head} [sp-{str(content_hash or '')[:12]}]"


def agent_memory_filename(content_hash: str) -> str:
    return f"sp-{str(content_hash or '').strip()}.md"


# Prefix of an AgentMemory target-state fingerprint (review B3): the Wiki API
# has no version field, so a targeted write anchors on a deterministic hash of
# the wiki/get state the human confirmed.  The first unconfirmed attempt
# surfaces the current fingerprint; the write only executes when the human
# re-approves with THAT fingerprint and the target has not changed since.
AM_BASE_FINGERPRINT_PREFIX = "amfp:"


def agent_memory_state_fingerprint(wiki_get_data: dict[str, Any]) -> str:
    canonical = json.dumps(wiki_get_data or {}, sort_keys=True, ensure_ascii=False, default=str)
    return AM_BASE_FINGERPRINT_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _content_fingerprint(content: str) -> str:
    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class AgentMemoryDeliveryOutcome:
    status: str  # accepted | failed | outcome_unknown | human_review
    failure_code: str | None = None
    failure_detail: str | None = None
    external_object_id: str | None = None
    external_version: str | None = None
    receipt: dict[str, Any] | None = None
    readback: dict[str, Any] | None = None
    create_attempted: bool = False


class AgentMemoryDeliveryAdapter:
    """Execute one candidate's AgentMemory delivery with the agreed failure model."""

    def __init__(self, client: AgentMemoryWikiClient) -> None:
        self._client = client

    def execute(
        self,
        *,
        promotion: dict[str, Any],
        delivery: dict[str, Any],
        on_object_id: Callable[[str], None] | None = None,
    ) -> AgentMemoryDeliveryOutcome:
        payload = promotion.get("candidate_payload") if isinstance(promotion.get("candidate_payload"), dict) else {}
        decision = str(payload.get("decision") or "")
        candidate_type = str(payload.get("candidate_type") or promotion.get("candidate_type") or "")
        if candidate_type == "skill":
            return AgentMemoryDeliveryOutcome(
                status="failed",
                failure_code="skill_not_writable",
                failure_detail="skill candidates are human-maintained and never written to AgentMemory",
            )
        content = str(payload.get("content") or payload.get("merged_content") or "")
        if not content.strip():
            return AgentMemoryDeliveryOutcome(
                status="failed",
                failure_code="empty_candidate_content",
                failure_detail="the candidate carries no writable body",
            )
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            # BEFORE any external call: a create that succeeds would leave an
            # empty wiki behind when the write is then refused.
            return AgentMemoryDeliveryOutcome(
                status="failed",
                failure_code="agent_memory_wiki_raw_write_payload_too_large",
                failure_detail=(
                    f"candidate body exceeds the Wiki raw/write per-file limit ({MAX_FILE_BYTES} bytes)"
                ),
            )
        content_hash = str(promotion.get("content_hash") or "")
        filename = agent_memory_filename(content_hash)
        wiki_id = str(delivery.get("external_object_id") or "").strip()
        prior_receipt = delivery.get("operation_receipt")
        prior_receipt = prior_receipt if isinstance(prior_receipt, dict) else {}
        create_attempted = bool(prior_receipt.get("wiki_create_attempted"))

        if decision == "new":
            return self._execute_new(
                payload=payload,
                content=content,
                content_hash=content_hash,
                filename=filename,
                wiki_id=wiki_id,
                create_attempted=create_attempted,
                on_object_id=on_object_id,
            )
        if decision in {"supplement", "replace", "merge"}:
            return self._execute_targeted(
                payload=payload,
                content=content,
                filename=filename,
            )
        return AgentMemoryDeliveryOutcome(
            status="failed",
            failure_code="unsupported_decision",
            failure_detail=f"decision {decision!r} has no AgentMemory delivery path",
        )

    # ------------------------------------------------------------------ new

    def _execute_new(
        self,
        *,
        payload: dict[str, Any],
        content: str,
        content_hash: str,
        filename: str,
        wiki_id: str,
        create_attempted: bool,
        on_object_id: Callable[[str], None] | None,
    ) -> AgentMemoryDeliveryOutcome:
        if wiki_id:
            return self._reconcile_known_wiki(
                wiki_id=wiki_id, filename=filename, content=content
            )
        if create_attempted:
            # A previous create attempt left no wiki_id. The Wiki API has no
            # team-wide listing, so a possibly-created wiki cannot be found
            # again — a blind second create risks a duplicate wiki. This is
            # exactly the plan's "写入超时且结果未知" human-review case.
            return AgentMemoryDeliveryOutcome(
                status="human_review",
                failure_code="wiki_create_outcome_unknown",
                failure_detail=(
                    "a previous wiki create attempt produced no wiki_id and the Wiki API "
                    "offers no listing to reconcile it; refusing a blind re-create"
                ),
                create_attempted=True,
            )
        try:
            created = self._client.wiki_create(
                name=agent_memory_wiki_name(payload, content_hash=content_hash)
            )
        except AgentMemoryWikiError as exc:
            return self._map_error(exc, stage="wiki_create", create_attempted=True)
        new_id = str(_data(created).get("wiki_id") or created.get("wiki_id") or "").strip()
        if not new_id:
            # The create may have landed but the receipt cannot prove it.
            return AgentMemoryDeliveryOutcome(
                status="outcome_unknown",
                failure_code="wiki_create_invalid_receipt",
                failure_detail="wiki create answered without a wiki_id",
                receipt={"wiki_create": created, "wiki_create_attempted": True},
                create_attempted=True,
            )
        if on_object_id is not None:
            # Durable capture BEFORE any follow-up call can time out.
            on_object_id(new_id)
        return self._write_files(
            wiki_id=new_id,
            filename=filename,
            content=content,
            receipt={"wiki_create": created, "wiki_id": new_id, "wiki_create_attempted": True},
        )

    # -------------------------------------------------------------- targeted

    def _execute_targeted(
        self, *, payload: dict[str, Any], content: str, filename: str
    ) -> AgentMemoryDeliveryOutcome:
        target = str(payload.get("target_object_id") or "").strip()
        if not target:
            return AgentMemoryDeliveryOutcome(
                status="failed",
                failure_code="targeted_delivery_without_target",
                failure_detail="supplement/replace/merge require the target wiki_id",
            )
        shared_base = str(payload.get("base_version") or "").strip()
        confirmed_base = str(payload.get("agent_memory_base_version") or "").strip()
        if not shared_base and not confirmed_base:
            return AgentMemoryDeliveryOutcome(
                status="failed",
                failure_code="targeted_delivery_without_base_version",
                failure_detail="supplement/replace/merge require the base version the review used",
            )
        try:
            existing = self._client.wiki_get(wiki_id=target)
        except AgentMemoryWikiError as exc:
            if exc.failure_kind == "not_found":
                return AgentMemoryDeliveryOutcome(
                    status="human_review",
                    failure_code="target_wiki_missing",
                    failure_detail=f"the target wiki {target!r} no longer exists",
                )
            return self._map_error(exc, stage="wiki_get_target")
        current = agent_memory_state_fingerprint(_data(existing))
        if not confirmed_base.startswith(AM_BASE_FINGERPRINT_PREFIX):
            # First touch: the Wiki API exposes no version, so this attempt
            # refuses to write and surfaces the CURRENT fingerprint — the
            # human re-approves with it as agent_memory_base_version, and
            # only an unchanged target may then be written.
            return AgentMemoryDeliveryOutcome(
                status="human_review",
                failure_code="agent_memory_target_base_unconfirmed",
                failure_detail=(
                    "confirm the AgentMemory target state and re-approve with "
                    f"agent_memory_base_version={current} (target {target!r})"
                ),
                receipt={"wiki_id": target, "current_base_fingerprint": current, "target_read": existing},
            )
        if confirmed_base != current:
            return AgentMemoryDeliveryOutcome(
                status="human_review",
                failure_code="agent_memory_target_version_conflict",
                failure_detail=(
                    f"target {target!r} changed since the confirmed base "
                    f"(confirmed {confirmed_base}, current {current}); refusing to overwrite"
                ),
                receipt={"wiki_id": target, "confirmed_base": confirmed_base, "current_base_fingerprint": current},
            )
        return self._write_files(
            wiki_id=target,
            filename=filename,
            content=content,
            receipt={
                "wiki_id": target,
                "target_read": existing,
                "confirmed_base": confirmed_base,
                "base_version": str(payload.get("base_version") or ""),
            },
        )

    # ---------------------------------------------------------------- shared

    def _reconcile_known_wiki(
        self, *, wiki_id: str, filename: str, content: str
    ) -> AgentMemoryDeliveryOutcome:
        """Readback-first reconciliation for a retry with a known wiki_id."""
        try:
            current = self._client.wiki_get(wiki_id=wiki_id)
        except AgentMemoryWikiError as exc:
            if exc.failure_kind == "not_found":
                return AgentMemoryDeliveryOutcome(
                    status="human_review",
                    failure_code="wiki_missing_after_create",
                    failure_detail=f"the created wiki {wiki_id!r} disappeared before completion",
                )
            return self._map_error(exc, stage="wiki_get_reconcile")
        data = _data(current)
        files = data.get("files") if isinstance(data.get("files"), list) else None
        if files is not None:
            names = {
                str(item.get("filename") or item.get("name") or "")
                for item in files
                if isinstance(item, dict)
            }
            if filename in names:
                # The write already landed (idempotent replay of a retry).
                return self._accepted(
                    wiki_id=wiki_id,
                    content=content,
                    receipt={"reconciled": True, "wiki_get": current},
                    readback={"wiki_get": current, "file_proof": filename},
                )
        return self._write_files(
            wiki_id=wiki_id,
            filename=filename,
            content=content,
            receipt={"reconciled": True, "wiki_get": current, "wiki_create_attempted": True},
        )

    def _write_files(
        self, *, wiki_id: str, filename: str, content: str, receipt: dict[str, Any]
    ) -> AgentMemoryDeliveryOutcome:
        try:
            write = self._client.wiki_raw_write(wiki_id=wiki_id, filename=filename, content=content)
        except AgentMemoryWikiError as exc:
            return self._map_error(
                exc, stage="wiki_raw_write", wiki_id=wiki_id,
                receipt={**receipt, "wiki_create_attempted": True},
            )
        try:
            ingest = self._client.wiki_ingest(wiki_id=wiki_id)
        except AgentMemoryWikiError as exc:
            # The file IS stored; the async build just could not be started
            # or answered unclearly — outcome_unknown, reconcile by readback.
            return self._map_error(
                exc, stage="wiki_ingest", wiki_id=wiki_id,
                receipt={**receipt, "raw_write": write, "wiki_create_attempted": True},
            )
        try:
            readback = self._client.wiki_get(wiki_id=wiki_id)
        except AgentMemoryWikiError as exc:
            return self._map_error(
                exc, stage="wiki_get_readback", wiki_id=wiki_id,
                receipt={**receipt, "raw_write": write, "ingest": ingest, "wiki_create_attempted": True},
            )
        data = _data(readback)
        files = data.get("files") if isinstance(data.get("files"), list) else None
        if files is not None:
            names = {
                str(item.get("filename") or item.get("name") or "")
                for item in files
                if isinstance(item, dict)
            }
            if filename not in names:
                return AgentMemoryDeliveryOutcome(
                    status="outcome_unknown",
                    failure_code="agent_memory_readback_unproven",
                    failure_detail="wiki readback does not list the written file",
                    external_object_id=wiki_id,
                    receipt={**receipt, "raw_write": write, "ingest": ingest, "wiki_create_attempted": True},
                    readback={"wiki_get": readback},
                )
        return self._accepted(
            wiki_id=wiki_id,
            content=content,
            receipt={**receipt, "raw_write": write, "ingest": ingest, "wiki_create_attempted": True},
            readback={"wiki_get": readback, "file_proof": filename if files is not None else "status_only"},
        )

    def _accepted(
        self, *, wiki_id: str, content: str, receipt: dict[str, Any], readback: dict[str, Any]
    ) -> AgentMemoryDeliveryOutcome:
        return AgentMemoryDeliveryOutcome(
            status="accepted",
            external_object_id=wiki_id,
            external_version=f"file:{_content_fingerprint(content)}",
            receipt=receipt,
            readback=readback,
        )

    def _map_error(
        self,
        exc: AgentMemoryWikiError,
        *,
        stage: str,
        wiki_id: str | None = None,
        receipt: dict[str, Any] | None = None,
        create_attempted: bool = False,
    ) -> AgentMemoryDeliveryOutcome:
        detail = f"{stage}: {exc}"
        base_receipt: dict[str, Any] = dict(receipt or {})
        if exc.payload is not None:
            base_receipt[f"{stage}_error"] = exc.payload
        if create_attempted:
            base_receipt["wiki_create_attempted"] = True
        if exc.failure_kind == "auth":
            return AgentMemoryDeliveryOutcome(
                status="failed",
                failure_code="agent_memory_auth_rejected",
                failure_detail=detail,
                external_object_id=wiki_id,
                receipt=base_receipt,
                create_attempted=create_attempted,
            )
        if exc.retryable or exc.failure_kind == "invalid_response":
            return AgentMemoryDeliveryOutcome(
                status="outcome_unknown",
                failure_code=f"agent_memory_{stage}_{exc.failure_kind}",
                failure_detail=detail,
                external_object_id=wiki_id,
                receipt=base_receipt,
                create_attempted=create_attempted,
            )
        return AgentMemoryDeliveryOutcome(
            status="failed",
            failure_code=f"agent_memory_{stage}_{exc.failure_kind}",
            failure_detail=detail,
            external_object_id=wiki_id,
            receipt=base_receipt,
            create_attempted=create_attempted,
        )


__all__ = [
    "AGENT_MEMORY_WIKI_BASE_URL_ENV",
    "AGENT_MEMORY_WIKI_USER_KEY_ENV",
    "AGENT_MEMORY_WIKI_TEAM_ID_ENV",
    "AgentMemoryDeliveryAdapter",
    "AgentMemoryDeliveryOutcome",
    "AgentMemoryWikiClient",
    "AgentMemoryWikiError",
]
