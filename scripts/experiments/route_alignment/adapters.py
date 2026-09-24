"""Explicit adapters for fixtures, read-only PostgreSQL snapshots, and HTTP candidates."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Callable
from urllib.parse import urlparse

from .core import CandidateResult, CaseSnapshot, build_snapshot, normalize_classification
from .dataset import candidate_state


DEFAULT_CANDIDATE_HTTP_TIMEOUT_SECONDS = 75.0
_CONTROLLED_HTTP_ERRORS = frozenset(
    {
        "authentication_error",
        "rate_limited",
        "input_too_large",
        "model_identity_unverified",
        "model_invocation_failed",
        "incomplete_output",
        "empty_model_output",
        "invalid_model_json",
        "invalid_model_classification",
        "normalization_error",
        "provider_http_error",
        "provider_timeout",
        "gateway_busy",
        "http_error",
        "provider_invocation_failed",
    }
)
_DIAGNOSTIC_KEYS = frozenset(
    {
        "wrapper_http_status", "gateway_http_status", "provider_http_status", "response_status", "incomplete_reason", "message_status",
        "requested_model", "actual_model", "input_tokens", "output_tokens", "reasoning_tokens",
        "text_length", "max_output_tokens", "reasoning_effort", "config_version",
        "normalization_code", "implementation_commit", "schema_version", "prompt_version",
        "hermes_route_manual_version", "hermes_route_manual_hash", "normalizer_version",
        "normalizer_policy_version", "normalizer_confidence_threshold", "gateway_implementation_commit",
        "provider_attempt_count", "actual_model_verified",
    }
)
_PROVENANCE_KEYS = frozenset(
    {
        "provider", "reasoning_effort", "hermes_route_manual_version", "hermes_route_manual_hash",
        "normalizer_version", "normalizer_policy_version", "normalizer_confidence_threshold",
        "schema_version", "implementation_commit", "wrapper_version", "actual_model_verified",
        "max_output_tokens", "config_version", "gateway_implementation_commit", "provider_attempt_count",
    }
)


def _http_error_code(error: urllib.error.HTTPError) -> tuple[str, dict[str, Any]]:
    diagnostics: dict[str, Any] = {"wrapper_http_status": error.code}
    if error.code in {401, 403}:
        error.close()
        return "authentication_error", diagnostics
    if error.code == 429:
        error.close()
        return "rate_limited", diagnostics
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (AttributeError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    finally:
        error.close()
    controlled = payload.get("error") if isinstance(payload, Mapping) else None
    if isinstance(controlled, str) and controlled in _CONTROLLED_HTTP_ERRORS:
        diagnostics_payload = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), Mapping) else {}
        diagnostics.update({
            str(key): value for key, value in diagnostics_payload.items() if str(key) in _DIAGNOSTIC_KEYS
        })
        return controlled, diagnostics
    return "http_error", diagnostics


def load_fixture_snapshots(path: str) -> list[CaseSnapshot]:
    snapshots: list[CaseSnapshot] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            snapshots.append(build_snapshot(row, alias=str(row.get("case_alias") or f"case-{line_number:03d}")))
    return snapshots


def _stratified_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Round-robin deterministic groups so a recent single class cannot fill the sample."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        classification = row.get("route_classification") if isinstance(row.get("route_classification"), Mapping) else {}
        key = (
            str(classification.get("primary_label") or "baseline_missing"),
            str(classification.get("secondary_label") or "baseline_missing"),
            str(classification.get("route_target") or "baseline_missing"),
        )
        groups.setdefault(key, []).append(row)
    selected: list[dict[str, Any]] = []
    ordered_groups = [groups[key] for key in sorted(groups)]
    while len(selected) < limit and ordered_groups:
        next_groups: list[list[dict[str, Any]]] = []
        for group in ordered_groups:
            if group and len(selected) < limit:
                selected.append(group.pop(0))
            if group:
                next_groups.append(group)
        ordered_groups = next_groups
    return selected


def fetch_production_snapshots(*, dsn: str, schema: str = "supportportal_production", limit: int = 100) -> list[CaseSnapshot]:
    """Read a frozen sample under a read-only transaction; never writes to DB."""
    if limit <= 0 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    try:
        import psycopg
        from psycopg import sql
    except ModuleNotFoundError as exc:  # pragma: no cover - EC2 dependency boundary
        raise RuntimeError("psycopg is required only for the live Production snapshot command") from exc

    candidate_limit = min(max(limit * 10, limit), 5000)
    query = sql.SQL(
        """
        SELECT c.client_ticket_id AS ticket_id, c.route_classification,
               c.updated_at::text AS case_updated_at, c.title AS subject,
               c.question AS question, t.status, t.product,
               c.processing_profile,
               ccs.comments_revision,
               COALESCE(
                   json_agg(
                       json_build_object(
                           'role', CASE WHEN cc.author_kind IN ('agent', 'staff') THEN 'assistant' ELSE 'user' END,
                           'content', cc.body,
                           'created_at', cc.created_at::text
                       ) ORDER BY cc.created_at, cc.zendesk_comment_id
                   ) FILTER (WHERE cc.zendesk_comment_id IS NOT NULL AND cc.is_public IS TRUE),
                   '[]'::json
               ) AS comments
        FROM {}.support_account_cases c
        JOIN {}.support_tickets t ON t.ticket_id = c.client_ticket_id
        LEFT JOIN {}.support_account_case_comments cc ON cc.client_ticket_id = c.client_ticket_id
        LEFT JOIN {}.support_account_case_comment_sync_state ccs ON ccs.client_ticket_id = c.client_ticket_id
        WHERE c.processing_profile = 'production'
        GROUP BY c.client_ticket_id, c.route_classification, c.updated_at, c.title, c.question, t.status, t.product, c.processing_profile, ccs.comments_revision
        ORDER BY c.updated_at DESC, c.client_ticket_id
        LIMIT %s
        """
    ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))
    rows: list[dict[str, Any]] = []
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on -c statement_timeout=30000") as conn:
        with conn.transaction(force_rollback=True):
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute(query, (candidate_limit,))
                rows = [dict(row) for row in cur.fetchall()]
                for row in rows:
                    question = row.get("question", "")
                    comments = row.get("comments") or []
                    row["question"] = question
                    row["messages"] = comments
                    comments_revision = str(row.pop("comments_revision") or "").strip()
                    case_updated_at = str(row.pop("case_updated_at") or "")
                    row["case_revision"] = comments_revision or case_updated_at
                    row["case_revision_source"] = "comments_revision" if comments_revision else "case_updated_at"
                    row["metadata"] = {
                        "processing_profile": row.pop("processing_profile", None),
                        "status": row.pop("status", None),
                        "product": row.pop("product", None),
                        "baseline_input_alignment": "unknown",
                    }
    selected = _stratified_rows(rows, limit)
    return [build_snapshot(row, alias=f"prod-{index:03d}") for index, row in enumerate(selected, 1)]


def fixture_candidate(name: str, mapping: Mapping[str, Mapping[str, Any]]) -> Callable[[CaseSnapshot], CandidateResult]:
    def invoke(snapshot: CaseSnapshot) -> CandidateResult:
        value = mapping.get(snapshot.alias)
        if value is None:
            return CandidateResult(candidate=name, status="error", error="fixture_missing", error_code="fixture_missing")
        normalized = normalize_classification(value)
        if not any(normalized.get(field) is not None for field in ("intent_class", "agora_route", "conversation_action")):
            return CandidateResult(candidate=name, status="error", raw=dict(value), error="missing_classification", error_code="missing_classification", model_version="fixture")
        return CandidateResult(candidate=name, status="ok", raw=dict(value), normalized=normalized, model_version="fixture", prompt_version="fixture", requested_model="fixture", returned_model="fixture")
    return invoke


def http_candidate(
    name: str,
    endpoint: str,
    *,
    timeout: float = DEFAULT_CANDIDATE_HTTP_TIMEOUT_SECONDS,
    headers: Mapping[str, str] | None = None,
) -> Callable[[CaseSnapshot], CandidateResult]:
    """Call a dedicated route-alignment case-snapshot endpoint.

    This is intentionally incompatible with Hermes' existing ``classify_route``
    tool, which only normalizes a supplied classification object.
    """
    parsed_endpoint = urlparse(endpoint)
    host = (parsed_endpoint.hostname or "").lower()
    path = parsed_endpoint.path.lower()
    local_http = parsed_endpoint.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}
    allowed_hosts = {item.strip().lower() for item in os.getenv("ROUTE_EXPERIMENT_ALLOWED_HOSTS", "").split(",") if item.strip()}
    if parsed_endpoint.scheme != "https" and not local_http:
        raise ValueError("candidate endpoint must use https (or localhost http for tests)")
    if not parsed_endpoint.path or "route-alignment" not in path:
        raise ValueError("candidate endpoint path must include route-alignment")
    if any(segment in path for segment in ("/account", "/intake", "/cases", "/automation/production", "/automation/preproduction")):
        raise ValueError("ordinary business endpoints are not allowed")
    if allowed_hosts and host not in allowed_hosts:
        raise ValueError("candidate endpoint host is not allowlisted")

    gateway_endpoint = path.endswith("/v1/route-alignment/responses")

    def invoke(snapshot: CaseSnapshot) -> CandidateResult:
        state = candidate_state(snapshot)
        if gateway_endpoint:
            from .hermes_classifier import (
                HERMES_DEFAULT_MAX_OUTPUT_TOKENS,
                HermesExperimentError,
                build_experiment_profile,
                classify_case_snapshot,
                _classification_schema,
                _experiment_provenance,
                _experiment_system_prompt,
            )
            from backend.services.llm_factory import LlmTextResult
            try:
                max_output_tokens = int(os.getenv("HERMES_ROUTE_ALIGNMENT_MAX_OUTPUT_TOKENS", str(HERMES_DEFAULT_MAX_OUTPUT_TOKENS)))
            except ValueError:
                max_output_tokens = HERMES_DEFAULT_MAX_OUTPUT_TOKENS
            reasoning_effort = os.getenv("HERMES_ROUTE_ALIGNMENT_REASONING_EFFORT", "medium").strip()
            payload = {
                "contract": "hermes-route-inference-v1",
                "input": json.dumps(state, ensure_ascii=False, sort_keys=True),
                "instructions": _experiment_system_prompt(),
                "reasoning": {"effort": reasoning_effort},
                "max_output_tokens": max_output_tokens,
                "text": {"format": {"type": "json_schema", "name": "hermes_route_experiment_classification", "strict": True, "schema": _classification_schema()}},
                "store": False,
            }
        else:
            payload = {
                "contract": "route-alignment-v1",
                "case_snapshot": {
                    "case_alias": snapshot.alias,
                    "case_revision": snapshot.case_revision,
                    **state,
                },
            }
        def gateway_invoke(*, profile: Any, system_prompt: str, user_prompt: str, extra_payload: dict[str, Any] | None = None) -> LlmTextResult:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", **dict(headers or {})},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    wrapper_status = int(getattr(response, "status", 200))
                    parsed = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                try:
                    body = json.loads(exc.read().decode("utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    body = {}
                finally:
                    exc.close()
                raw_diagnostics = body.get("diagnostics") if isinstance(body, Mapping) else None
                diagnostics = dict(raw_diagnostics) if isinstance(raw_diagnostics, Mapping) else {}
                diagnostics["gateway_http_status"] = exc.code
                diagnostics.setdefault("provider_attempt_count", 0)
                if exc.code in {401, 403}:
                    code = "authentication_error"
                elif exc.code == 429:
                    code = "rate_limited"
                elif exc.code == 409:
                    code = "gateway_busy"
                else:
                    candidate_error = body.get("error") if isinstance(body, Mapping) else None
                    code = candidate_error if isinstance(candidate_error, str) and candidate_error in _CONTROLLED_HTTP_ERRORS else "http_error"
                raise HermesExperimentError(code, diagnostics=diagnostics) from exc
            except TimeoutError as exc:
                raise HermesExperimentError("provider_timeout", diagnostics={"gateway_http_status": None}) from exc
            if not isinstance(parsed, Mapping) or parsed.get("contract") != "hermes-route-inference-v1":
                raise HermesExperimentError("invalid_gateway_response", diagnostics={"gateway_http_status": wrapper_status})
            diagnostics = dict(parsed.get("diagnostics") or {}) if isinstance(parsed.get("diagnostics"), Mapping) else {}
            diagnostics["gateway_http_status"] = wrapper_status
            classification = parsed.get("classification")
            if not isinstance(classification, Mapping):
                raise HermesExperimentError("missing_classification", diagnostics=diagnostics)
            usage = parsed.get("usage") if isinstance(parsed.get("usage"), Mapping) else {}
            raw_payload = {
                "status": diagnostics.get("response_status"),
                "incomplete_details": {"reason": diagnostics.get("incomplete_reason")},
                "model": parsed.get("actual_model"),
                "output_text": json.dumps(classification, ensure_ascii=False),
                "gateway_diagnostics": diagnostics,
            }
            return LlmTextResult(
                text=raw_payload["output_text"],
                model_name=str(parsed.get("actual_model") or ""),
                prompt_tokens=int(usage.get("input_tokens") or 0),
                completion_tokens=int(usage.get("output_tokens") or 0),
                reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
                raw_payload=raw_payload,
                provider_name=str(diagnostics.get("provider") or "openai"),
            )

        started = time.monotonic()
        if gateway_endpoint:
            profile = build_experiment_profile(
                api_key="gateway-transport",
                base_url="http://127.0.0.1",
                model=os.getenv("HERMES_ROUTE_ALIGNMENT_MODEL", "gateway-fixed"),
                reasoning_effort=reasoning_effort,
            )
            try:
                snapshot_payload = {"case_alias": snapshot.alias, "case_revision": snapshot.case_revision, **state}
                result = classify_case_snapshot(snapshot_payload, profile=profile, max_output_tokens=max_output_tokens, invoke=gateway_invoke)
                if result.get("actual_model_verified") is not True or not result.get("actual_model"):
                    raise HermesExperimentError("model_identity_unverified", diagnostics=dict(result.get("diagnostics") or {}))
                diagnostics = dict(result.get("diagnostics") or {})
                diagnostics["gateway_http_status"] = diagnostics.get("gateway_http_status")
                metadata = {key: result.get(key) for key in _PROVENANCE_KEYS if result.get(key) is not None}
                for key in _PROVENANCE_KEYS:
                    if key in diagnostics:
                        metadata[key] = diagnostics[key]
                metadata["diagnostics"] = diagnostics
                return CandidateResult(
                    candidate=name, status="ok", raw=dict(result["classification"]), normalized=dict(result["normalized_classification"]),
                    latency_ms=round((time.monotonic() - started) * 1000, 2), model_version=diagnostics.get("actual_model") or result.get("actual_model"),
                    prompt_version=result.get("prompt_version"), requested_model=diagnostics.get("requested_model") or result.get("requested_model"),
                    returned_model=result.get("returned_model"), call_count=1, usage=dict(result.get("usage") or {}), metadata=metadata,
                )
            except HermesExperimentError as exc:
                diagnostics = {
                    **_experiment_provenance(profile, max_output_tokens),
                    **dict(exc.diagnostics),
                    "provider_attempt_count": dict(exc.diagnostics).get("provider_attempt_count", 0),
                }
                metadata = {key: diagnostics.get(key) for key in _PROVENANCE_KEYS if diagnostics.get(key) is not None}
                metadata["diagnostics"] = diagnostics
                usage = {
                    key: diagnostics[key]
                    for key in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_input_tokens")
                    if diagnostics.get(key) is not None
                }
                return CandidateResult(
                    candidate=name, status="error", error=exc.code, error_code=exc.code,
                    latency_ms=round((time.monotonic() - started) * 1000, 2),
                    model_version=diagnostics.get("actual_model") or diagnostics.get("requested_model"),
                    requested_model=diagnostics.get("requested_model"), returned_model=diagnostics.get("actual_model"),
                    prompt_version=diagnostics.get("prompt_version"),
                    usage=usage,
                    call_count=int(diagnostics.get("provider_attempt_count") or 0), metadata=metadata,
                )
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", **dict(headers or {})},
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                wrapper_http_status = int(getattr(response, "status", 200))
                parsed = json.loads(response.read().decode("utf-8"))
            if not isinstance(parsed, Mapping):
                raise ValueError("response_not_object")
            expected_contract = "hermes-route-inference-v1" if gateway_endpoint else "route-alignment-v1"
            if parsed.get("contract") != expected_contract:
                raise ValueError("invalid_contract")
            if not gateway_endpoint and (parsed.get("case_alias") != snapshot.alias or parsed.get("case_revision") != snapshot.case_revision):
                raise ValueError("snapshot_identity_mismatch")
            raw_value = parsed.get("normalized_classification") or parsed.get("classification")
            if not isinstance(raw_value, Mapping):
                raise ValueError("missing_classification")
            raw = dict(raw_value)
            normalized = normalize_classification(raw)
            if not any(normalized.get(field) is not None for field in ("intent_class", "agora_route", "conversation_action")):
                raise ValueError("missing_classification")
            requested_model = str(parsed.get("requested_model") or parsed.get("model_version") or "unknown")
            raw_returned_model = parsed.get("returned_model")
            returned_model = raw_returned_model.strip() if isinstance(raw_returned_model, str) else None
            usage = dict(parsed.get("usage") or {}) if isinstance(parsed.get("usage"), Mapping) else {}
            metadata = {key: parsed.get(key) for key in _PROVENANCE_KEYS if key in parsed}
            diagnostics = parsed.get("diagnostics") if isinstance(parsed.get("diagnostics"), Mapping) else {}
            metadata["diagnostics"] = {
                ("gateway_http_status" if gateway_endpoint else "wrapper_http_status"): wrapper_http_status,
                **dict(diagnostics),
            }
            for key in _PROVENANCE_KEYS:
                if key in metadata["diagnostics"]:
                    metadata[key] = metadata["diagnostics"][key]
            metadata["max_output_tokens"] = parsed.get("max_output_tokens")
            metadata["config_version"] = parsed.get("config_version")
            if gateway_endpoint:
                from .hermes_classifier import (
                    HERMES_ROUTE_EXPERIMENT_PROMPT_VERSION,
                    HERMES_ROUTE_EXPERIMENT_SCHEMA_VERSION,
                    HERMES_ROUTE_CLASSIFICATION_VERSION,
                    HERMES_ROUTE_MANUAL_VERSION,
                    build_hermes_route_manual,
                )
                from .provenance import source_code_commit
                import hashlib
                metadata.update({
                    "implementation_commit": source_code_commit(__file__),
                    "schema_version": HERMES_ROUTE_EXPERIMENT_SCHEMA_VERSION,
                    "prompt_version": HERMES_ROUTE_EXPERIMENT_PROMPT_VERSION,
                    "normalizer_version": HERMES_ROUTE_CLASSIFICATION_VERSION,
                    "hermes_route_manual_version": HERMES_ROUTE_MANUAL_VERSION,
                    "hermes_route_manual_hash": hashlib.sha256(build_hermes_route_manual().encode("utf-8")).hexdigest(),
                    "reasoning_effort": os.getenv("HERMES_ROUTE_ALIGNMENT_REASONING_EFFORT", "medium").strip(),
                    "max_output_tokens": max_output_tokens,
                })
                metadata["diagnostics"].update({key: value for key, value in metadata.items() if key in _DIAGNOSTIC_KEYS})
            if parsed.get("actual_model_verified") is not True or not returned_model:
                return CandidateResult(
                    candidate=name,
                    status="error",
                    error="model_identity_unverified",
                    error_code="model_identity_unverified",
                    latency_ms=round((time.monotonic() - started) * 1000, 2),
                    model_version=str(parsed.get("model_version") or "unknown"),
                    prompt_version=str(parsed.get("prompt_version") or "unknown"),
                    requested_model=requested_model,
                    returned_model=None,
                    call_count=1,
                    usage=usage,
                    metadata=metadata,
                )
            return CandidateResult(
                candidate=name,
                status="ok",
                raw=raw,
                normalized=normalized,
                latency_ms=round((time.monotonic() - started) * 1000, 2),
                model_version=str(parsed.get("model_version") or "unknown"),
                prompt_version=str(parsed.get("prompt_version") or "unknown"),
                requested_model=requested_model,
                returned_model=returned_model,
                call_count=1,
                usage=usage,
                metadata=metadata,
            )
        except urllib.error.HTTPError as exc:
            error_code, diagnostics = _http_error_code(exc)
            if gateway_endpoint and "wrapper_http_status" in diagnostics:
                diagnostics["gateway_http_status"] = diagnostics.pop("wrapper_http_status")
            requested_model = diagnostics.get("requested_model")
            actual_model = diagnostics.get("actual_model")
            usage = {
                key: diagnostics[key]
                for key in ("input_tokens", "output_tokens", "reasoning_tokens")
                if diagnostics.get(key) is not None
            }
            metadata = {
                key: diagnostics.get(key)
                for key in _PROVENANCE_KEYS
                if key in diagnostics
            }
            metadata["diagnostics"] = diagnostics
            return CandidateResult(
                candidate=name,
                status="error",
                error=error_code,
                error_code=error_code,
                latency_ms=round((time.monotonic() - started) * 1000, 2),
                model_version=(
                    str(actual_model) if isinstance(actual_model, str) and actual_model
                    else str(requested_model) if isinstance(requested_model, str) and requested_model
                    else None
                ),
                prompt_version=(
                    str(diagnostics["prompt_version"]) if diagnostics.get("prompt_version") else None
                ),
                requested_model=(
                    str(requested_model) if isinstance(requested_model, str) and requested_model else None
                ),
                returned_model=(
                    str(actual_model) if isinstance(actual_model, str) and actual_model else None
                ),
                call_count=1,
                usage=usage,
                metadata=metadata,
            )
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            error_code = str(exc)[:80] or type(exc).__name__
            return CandidateResult(candidate=name, status="error", error=type(exc).__name__ + ":" + str(exc)[:200], error_code=error_code, latency_ms=round((time.monotonic() - started) * 1000, 2), call_count=1)
    return invoke


def gateway_capabilities(endpoint: str, *, headers: Mapping[str, str] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    """Read and validate the dedicated gateway capability contract."""
    capabilities_url = endpoint.rsplit("/v1/route-alignment/responses", 1)[0] + "/v1/route-alignment/capabilities"
    request = urllib.request.Request(capabilities_url, headers=dict(headers or {}), method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping) or payload.get("contract") != "hermes-route-inference-v1":
        raise ValueError("invalid_route_alignment_capabilities")
    required = {"single_attempt": True, "fallback": False, "tools": False, "session_persistence": False, "response_store": False, "structured_output": True}
    if any(payload.get(key) != value for key, value in required.items()):
        raise ValueError("route_alignment_capabilities_not_isolated")
    if not payload.get("gateway_implementation_commit") or payload.get("gateway_implementation_commit") == "unverified" or not payload.get("model"):
        raise ValueError("route_alignment_capabilities_incomplete")
    return dict(payload)
