"""WeKnora HTTP client for the SupportPortal knowledge/memory promotion adapter.

The concrete WeKnora API version, authentication scheme, and field names are
NOT hardcoded: they are pinned by the Preproduction contract probe and supplied
through configuration (``WEKNORA_API_CONTRACT_JSON`` and friends).  Every
operation fails closed with ``failure_kind="not_configured"`` until the pinned
contract is present, so an unconfigured deployment never issues a request.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

LOGGER = logging.getLogger(__name__)

_PATH_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")

WEKNORA_OPERATIONS = (
    "health",
    "knowledge_search",
    "knowledge_read",
    "knowledge_create",
    "knowledge_update",
    "knowledge_versions",
    "memory_list",
    "memory_create",
    "memory_update",
    "memory_confirm",
    "memory_reject",
)

_RETRYABLE_FAILURE_KINDS = frozenset({"timeout", "transport"})


class WeKnoraError(RuntimeError):
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
        if self.failure_kind == "http" and self.status_code is not None and self.status_code >= 500:
            return True
        return False


def _safe_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def load_weknora_contract(raw: str | None) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WeKnoraError(
            "WEKNORA_API_CONTRACT_JSON is not valid JSON", failure_kind="not_configured"
        ) from exc
    if not isinstance(payload, dict):
        raise WeKnoraError(
            "WEKNORA_API_CONTRACT_JSON must be a JSON object", failure_kind="not_configured"
        )
    return payload


def _extract(payload: Any, path: str) -> Any:
    node = payload
    for part in str(path or "").split("."):
        if not part:
            continue
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


class WeKnoraClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_token: str | None = None,
        contract: dict[str, Any] | str | None = None,
        timeout_seconds: float | None = None,
        knowledge_base_id: str | None = None,
        memory_identity: str | None = None,
        tenant_id: str | None = None,
        auth_header_name: str | None = None,
        auth_scheme: str | None = None,
    ) -> None:
        self._base_url = (base_url if base_url is not None else os.getenv("WEKNORA_BASE_URL", "")).strip().rstrip("/")
        self._api_token = (
            api_token if api_token is not None else os.getenv("WEKNORA_API_TOKEN", "")
        ).strip()
        if isinstance(contract, dict):
            self._contract: dict[str, Any] = dict(contract)
        else:
            self._contract = load_weknora_contract(
                contract if contract is not None else os.getenv("WEKNORA_API_CONTRACT_JSON")
            )
        self._timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else _safe_float_env("WEKNORA_TIMEOUT_SECONDS", 30.0)
        )
        self._knowledge_base_id = (
            knowledge_base_id if knowledge_base_id is not None else os.getenv("WEKNORA_KNOWLEDGE_BASE_ID", "")
        ).strip()
        self._memory_identity = (
            memory_identity if memory_identity is not None else os.getenv("WEKNORA_MEMORY_IDENTITY", "")
        ).strip()
        self._tenant_id = (tenant_id if tenant_id is not None else os.getenv("WEKNORA_TENANT_ID", "")).strip()
        self._auth_header_name = (
            auth_header_name if auth_header_name is not None else os.getenv("WEKNORA_AUTH_HEADER_NAME", "Authorization")
        ).strip() or "Authorization"
        self._auth_scheme = (
            auth_scheme if auth_scheme is not None else os.getenv("WEKNORA_AUTH_SCHEME", "Bearer")
        ).strip()

    # -- configuration -----------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self._base_url and self._api_token and self._operation("health") is not None)

    def has_memory_identity(self) -> bool:
        return bool(self._memory_identity)

    def memory_identity(self) -> str:
        return self._memory_identity

    def knowledge_base_id(self) -> str:
        return self._knowledge_base_id

    def _operation(self, name: str) -> dict[str, Any] | None:
        entry = self._contract.get(name)
        if not isinstance(entry, dict):
            return None
        path = str(entry.get("path") or "").strip()
        if not path:
            return None
        return entry

    def _field(self, key: str, default: str) -> str:
        value = str(self._contract.get(key) or "").strip()
        return value or default

    # -- transport ---------------------------------------------------------

    def _build_url(self, path: str, query: dict[str, Any] | None = None) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{self._base_url}{normalized_path}"
        if query:
            filtered = {key: value for key, value in query.items() if value is not None}
            if filtered:
                url = f"{url}?{urllib.parse.urlencode(filtered)}"
        return url

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_token:
            scheme = self._auth_scheme
            headers[self._auth_header_name] = f"{scheme} {self._api_token}".strip()
        return headers

    def _render_path(self, path: str, semantic: dict[str, Any] | None) -> str:
        """Substitute ``{placeholder}`` tokens in a pinned contract path.

        REST APIs address objects through the URL (``/knowledge/{object_id}``);
        placeholders resolve from the semantic request values, URL-quoted.  A
        placeholder without a value fails closed instead of being sent
        literally.
        """
        rendered = str(path)
        for token in set(_PATH_PLACEHOLDER.findall(rendered)):
            if semantic is None or str(token) not in semantic:
                raise WeKnoraError(
                    f"WeKnora path placeholder {{{token}}} has no request value",
                    failure_kind="not_configured",
                )
            rendered = rendered.replace(
                "{" + str(token) + "}",
                urllib.parse.quote(str(semantic[str(token)]), safe=""),
            )
        return rendered

    def _render_template(
        self, template: dict[str, Any], semantic: dict[str, Any], *, operation: str, kind: str
    ) -> dict[str, Any]:
        """Render a request-field template against the semantic inputs.

        Template values are either literals or ``{"$": "semantic_name"}``.
        A referenced semantic field that is absent, None, or empty fails
        closed: the pinned official contract defines that field as required,
        so sending without it (or inventing a value) is never acceptable.
        """

        def rendered_value(spec: Any, field: str) -> Any:
            if isinstance(spec, dict) and set(spec) == {"$"}:
                name = str(spec["$"])
                value = semantic.get(name)
                if value is None or (isinstance(value, str) and not value.strip()) or value == {}:
                    raise WeKnoraError(
                        f"WeKnora {operation} {kind} field '{field}' requires semantic "
                        f"input '{name}', which is missing or empty",
                        failure_kind="not_configured",
                    )
                return copy.deepcopy(value)
            return copy.deepcopy(spec)

        return {str(field): rendered_value(spec, str(field)) for field, spec in template.items()}

    def _request(
        self,
        operation: str,
        *,
        json_body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        entry = self._operation(operation)
        if entry is None or not self._base_url or not self._api_token:
            raise WeKnoraError(
                f"WeKnora operation '{operation}' is not configured", failure_kind="not_configured"
            )
        method = str(entry.get("method") or "GET").strip().upper()
        headers = self._headers()
        semantic = dict(json_body or {})
        if self._tenant_id:
            semantic.setdefault("tenant_id", self._tenant_id)

        body_template = entry.get("body") if isinstance(entry.get("body"), dict) else None
        query_template = entry.get("query_params") if isinstance(entry.get("query_params"), dict) else None
        rendered_query = dict(query or {})
        if query_template is not None:
            rendered_query.update(
                self._render_template(query_template, semantic, operation=operation, kind="query")
            )
        rendered_query = {k: v for k, v in rendered_query.items() if v is not None}

        body: bytes | None = None
        if body_template is not None:
            # The pinned contract fully owns the wire shape: only templated
            # fields are sent (no idempotency key, identity, or metadata leaks
            # into an API that does not define them).
            payload = self._render_template(body_template, semantic, operation=operation, kind="body")
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif method != "GET" and json_body is not None:
            payload = dict(json_body)
            tenant_field = self._field("tenant_field", "tenant_id")
            if self._tenant_id and tenant_field not in payload:
                payload[tenant_field] = self._tenant_id
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url=self._build_url(
                self._render_path(str(entry.get("path")), semantic), query=rendered_query
            ),
            data=body,
            headers=headers,
            method=method,
        )
        timeout = timeout_seconds if timeout_seconds is not None else self._timeout_seconds
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = _json_loads(response.read())
        except urllib.error.HTTPError as exc:
            raw_payload = _json_loads(exc.read())
            code = int(exc.code)
            if code in (401, 403):
                failure_kind = "auth"
            elif code == 404:
                failure_kind = "not_found"
            elif code == 409 or code == 412:
                failure_kind = "conflict"
            else:
                failure_kind = "http"
            raise WeKnoraError(
                f"WeKnora returned HTTP {code} for {operation}",
                failure_kind=failure_kind,
                status_code=code,
                payload=raw_payload,
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            failure_kind = "timeout" if isinstance(exc, (TimeoutError, socket.timeout)) else "transport"
            raise WeKnoraError(
                f"WeKnora request failed for {operation}", failure_kind=failure_kind
            ) from exc
        if not isinstance(payload, dict):
            payload = {"payload": payload}
        return payload

    # -- knowledge operations ----------------------------------------------

    def _require_knowledge_base(self) -> str:
        if not self._knowledge_base_id:
            raise WeKnoraError(
                "WeKnora knowledge base id is not configured", failure_kind="not_configured"
            )
        return self._knowledge_base_id

    def knowledge_search(self, *, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        payload = self._request(
            "knowledge_search",
            json_body={"query": str(query), "knowledge_base_id": self._require_knowledge_base(), "top_k": top_k},
        )
        results = _extract(payload, self._field("results_key", "results"))
        if not isinstance(results, list):
            raise WeKnoraError(
                "WeKnora search response is missing the results list",
                failure_kind="invalid_response",
                payload=payload,
            )
        return [item for item in results if isinstance(item, dict)]

    def knowledge_read(self, *, object_id: str) -> dict[str, Any]:
        normalized_id = str(object_id or "").strip()
        if not normalized_id:
            raise WeKnoraError("object_id is required", failure_kind="invalid_response")
        payload = self._request(
            "knowledge_read",
            json_body={"object_id": normalized_id, "knowledge_base_id": self._require_knowledge_base()},
        )
        return self._normalize_read_receipt(payload, object_id=normalized_id)

    def knowledge_versions(self, *, object_id: str) -> dict[str, Any]:
        normalized_id = str(object_id or "").strip()
        if not normalized_id:
            raise WeKnoraError("object_id is required", failure_kind="invalid_response")
        return self._request(
            "knowledge_versions",
            json_body={"object_id": normalized_id, "knowledge_base_id": self._require_knowledge_base()},
        )

    def knowledge_create(
        self, *, title: str, content: str, idempotency_key: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = self._request(
            "knowledge_create",
            json_body={
                "title": str(title or "").strip(),
                "content": str(content or ""),
                self._field("idempotency_key_field", "idempotency_key"): str(idempotency_key or "").strip(),
                "knowledge_base_id": self._require_knowledge_base(),
                "metadata": metadata or {},
            },
        )
        return self._normalize_write_receipt(payload, operation="knowledge_create")

    def knowledge_update(
        self,
        *,
        object_id: str,
        base_version: str,
        title: str,
        content: str,
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._request(
            "knowledge_update",
            json_body={
                "object_id": str(object_id or "").strip(),
                self._field("base_version_field", "base_version"): str(base_version or "").strip(),
                "title": str(title or "").strip(),
                "content": str(content or ""),
                self._field("idempotency_key_field", "idempotency_key"): str(idempotency_key or "").strip(),
                "knowledge_base_id": self._require_knowledge_base(),
                # Lineage metadata: on template contracts the pinned body owns
                # the wire shape and only templated fields are sent (same rule
                # as knowledge_create); legacy contracts carry it like create.
                "metadata": metadata or {},
            },
        )
        return self._normalize_write_receipt(payload, operation="knowledge_update")

    # -- memory operations --------------------------------------------------

    def _memory_semantics(self, body: dict[str, Any]) -> dict[str, Any]:
        """Semantic inputs for a memory operation.

        The identity is provided as the ``identity`` semantic (and, for legacy
        non-template contracts, injected under the pinned identity field).  A
        body template decides which of these actually reach the wire, so an
        official API that does not define ``user_id`` never receives it.
        """
        if not self._memory_identity:
            raise WeKnoraError(
                "WeKnora shared memory identity is not configured", failure_kind="not_configured"
            )
        payload = dict(body)
        payload["identity"] = self._memory_identity
        return payload

    def supports_conditional_update(self, candidate_type: str) -> bool:
        """Whether the pinned contract proves base_version-conditional updates.

        Defaults to False: an update operation must explicitly declare
        ``"conditional_update": true`` (probe evidence) before the adapter
        will perform version-protected targeted writes.
        """
        operation = "knowledge_update" if candidate_type == "knowledge" else "memory_update"
        entry = self._operation(operation)
        return bool(isinstance(entry, dict) and entry.get("conditional_update") is True)

    def memory_list(self, *, top_k: int | None = None) -> list[dict[str, Any]]:
        """List memory items (the official memory API is a list endpoint)."""
        payload = self._request(
            "memory_list",
            json_body=self._memory_semantics({"top_k": top_k}),
        )
        results = _extract(payload, self._field("results_key", "results"))
        if not isinstance(results, list):
            raise WeKnoraError(
                "WeKnora memory list response is missing the results list",
                failure_kind="invalid_response",
                payload=payload,
            )
        return [item for item in results if isinstance(item, dict)]

    def memory_create(
        self,
        *,
        content: str,
        idempotency_key: str,
        kind: str = "",
        importance: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        semantics = self._memory_semantics(
            {
                "content": str(content or ""),
                "idempotency_key": str(idempotency_key or "").strip(),
                "kind": str(kind or ""),
                "importance": importance,
                "metadata": metadata or {},
            }
        )
        if importance is not None:
            semantics["importance"] = int(importance)
        payload = self._request("memory_create", json_body=self._legacy_memory_body(semantics))
        return self._normalize_write_receipt(payload, operation="memory_create")

    def memory_update(
        self,
        *,
        object_id: str,
        base_version: str,
        content: str,
        idempotency_key: str,
        kind: str = "",
        importance: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        semantics = self._memory_semantics(
            {
                "object_id": str(object_id or "").strip(),
                "base_version": str(base_version or "").strip(),
                "content": str(content or ""),
                "idempotency_key": str(idempotency_key or "").strip(),
                "kind": str(kind or ""),
                "importance": importance,
                # Lineage metadata semantic: a pinned body template decides
                # whether it reaches the wire (official APIs that do not
                # define it never receive it); legacy bodies carry it like
                # memory_create.
                "metadata": metadata or {},
            }
        )
        if importance is not None:
            semantics["importance"] = int(importance)
        payload = self._request("memory_update", json_body=self._legacy_memory_body(semantics))
        return self._normalize_write_receipt(payload, operation="memory_update")

    def _legacy_memory_body(self, semantics: dict[str, Any]) -> dict[str, Any]:
        """Legacy (template-less) memory body for custom pinned APIs."""
        payload = dict(semantics)
        identity_field = self._field("identity_field", "user_id")
        payload[identity_field] = self._memory_identity
        payload[self._field("idempotency_key_field", "idempotency_key")] = semantics.get(
            "idempotency_key", ""
        )
        payload[self._field("base_version_field", "base_version")] = semantics.get(
            "base_version", ""
        )
        for optional in ("kind", "importance", "metadata", "object_id", "top_k"):
            if optional in payload and (payload[optional] is None or payload[optional] == ""):
                del payload[optional]
        return payload

    def memory_confirm(self, *, object_id: str) -> dict[str, Any]:
        return self._request(
            "memory_confirm", json_body=self._memory_semantics({"object_id": str(object_id or "").strip()})
        )

    def memory_reject(self, *, object_id: str) -> dict[str, Any]:
        return self._request(
            "memory_reject", json_body=self._memory_semantics({"object_id": str(object_id or "").strip()})
        )

    # -- receipts -----------------------------------------------------------

    def _extract_object_id(self, payload: dict[str, Any]) -> str:
        """Configured key first, then the common aliases (``object_id``, ``id``).

        The official memory API answers with ``id``; normalizing here lets one
        contract drive both shapes without guessing in the adapter.
        """
        for path in (self._field("object_id_key", "object_id"), "object_id", "id"):
            value = str(_extract(payload, path) or "").strip()
            if value:
                return value
        return ""

    def _normalize_write_receipt(self, payload: dict[str, Any], *, operation: str) -> dict[str, Any]:
        object_id = self._extract_object_id(payload)
        version = str(_extract(payload, self._field("version_key", "version")) or "").strip()
        if not object_id:
            raise WeKnoraError(
                f"WeKnora {operation} receipt is missing object id",
                failure_kind="invalid_response",
                payload=payload,
            )
        return {"object_id": object_id, "version": version or None, "receipt": payload}

    def _normalize_read_receipt(self, payload: dict[str, Any], *, object_id: str) -> dict[str, Any]:
        content = _extract(payload, self._field("content_key", "content"))
        title = _extract(payload, self._field("title_key", "title"))
        version = str(_extract(payload, self._field("version_key", "version")) or "").strip()
        if content is None:
            raise WeKnoraError(
                "WeKnora read response is missing content",
                failure_kind="invalid_response",
                payload=payload,
            )
        return {
            "object_id": self._extract_object_id(payload) or object_id,
            "version": version or None,
            "title": str(title or ""),
            "content": str(content),
            "payload": payload,
        }

    # -- probe --------------------------------------------------------------

    def probe(self) -> dict[str, Any]:
        """Read-only connectivity/contract report used by the contract probe."""
        report: dict[str, Any] = {
            "base_url": self._base_url,
            "configured": self.is_configured(),
            "operations": {name: self._operation(name) is not None for name in WEKNORA_OPERATIONS},
        }
        if not self._base_url:
            report["health"] = {"status": "not_configured"}
            return report
        health_entry = self._operation("health")
        if health_entry is None:
            report["health"] = {"status": "health_operation_not_pinned"}
            return report
        try:
            payload = self._request("health", timeout_seconds=min(self._timeout_seconds, 5.0))
        except WeKnoraError as exc:
            report["health"] = {"status": exc.failure_kind, "detail": str(exc), "status_code": exc.status_code}
            return report
        report["health"] = {"status": "ok", "payload": payload}
        return report


def _json_loads(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"message": text}


def weknora_promotion_enabled() -> bool:
    """Master gate: the WeKnora promotion path only runs when explicitly enabled."""
    if str(os.getenv("WEKNORA_PROMOTION_ENABLED") or "").strip().lower() not in {"1", "true", "yes"}:
        return False
    return WeKnoraClient().is_configured()
