from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import json
import re
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    REQUEST_BODY_ANALYZER_SCENARIO,
    profile_has_invocation_credentials,
    resolve_model_profile,
)
from backend.services.prompts.request_body_evidence import (
    build_request_body_evidence_system_prompt,
    build_request_body_evidence_user_prompt,
)


REQUEST_BODY_INSUFFICIENT_REASON = "rag_completed_with_insufficient_evidence"
_BODY_KEYWORDS = {
    "body",
    "clientrequest",
    "config",
    "json",
    "layoutconfig",
    "payload",
    "request body",
    "request_body",
}
_SCHEMA_EVIDENCE_PRIORITY = {
    "nested_schema": 0,
    "request_body_schema": 1,
    "endpoint_schema": 2,
    "request_parameters": 3,
    "payload_example": 4,
    "same_doc_neighbor": 5,
}


@dataclass(frozen=True)
class RequestBodyEvidenceQuery:
    is_request_body_or_api_config: bool
    confidence: float = 0.0
    endpoint_hints: list[str] = field(default_factory=list)
    http_methods: list[str] = field(default_factory=list)
    body_keys: list[str] = field(default_factory=list)
    nested_paths: list[str] = field(default_factory=list)
    field_value_hints: dict[str, str] = field(default_factory=dict)
    question_need: str = "unknown"
    schema_evidence_goals: list[str] = field(default_factory=list)
    analyzer_source: str = "rules"


@dataclass(frozen=True)
class RequestBodyEvidenceChunk:
    chunk_id: str
    evidence_type: str
    matched_fields: list[str]
    source_path: str | None = None
    text_excerpt: str | None = None
    similarity: float = 0.0
    original_chunk: Any = None


@dataclass(frozen=True)
class RequestBodyEvidenceResult:
    triggered: bool
    query: RequestBodyEvidenceQuery
    chunks: list[RequestBodyEvidenceChunk] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    retrieval_queries: list[dict[str, str]] = field(default_factory=list)


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _append_unique(items: list[str], value: object) -> None:
    normalized = _clean_text(value)
    if normalized and normalized not in items:
        items.append(normalized)


def _normalize_path(path: str) -> str:
    normalized = _clean_text(path)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if parsed.scheme and parsed.netloc:
        normalized = parsed.path or normalized
    if "?" in normalized:
        normalized = normalized.split("?", 1)[0]
    return normalized.rstrip(",.;)")


def _extract_endpoint_hints(message: str) -> list[str]:
    endpoints: list[str] = []
    for match in re.finditer(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+((?:https?://[^\s'\"`]+)|/[A-Za-z0-9_./{}:=-]+)", message, flags=re.I):
        _append_unique(endpoints, _normalize_path(match.group(1)))
    for match in re.finditer(r"\b(?:requests|axios)\.(?:get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]", message, flags=re.I):
        _append_unique(endpoints, _normalize_path(match.group(1)))
    for match in re.finditer(r"\bfetch\(\s*['\"]([^'\"]+)['\"]", message, flags=re.I):
        _append_unique(endpoints, _normalize_path(match.group(1)))
    for match in re.finditer(r"https?://[^\s'\"`]+", message):
        _append_unique(endpoints, _normalize_path(match.group(0)))
    for match in re.finditer(r"\b(?:endpoint|url)\s*[:=]\s*['\"]([^'\"]+)['\"]", message, flags=re.I):
        _append_unique(endpoints, _normalize_path(match.group(1)))
    return endpoints


def _extract_http_methods(message: str) -> list[str]:
    methods: list[str] = []
    for match in re.finditer(r"\b(GET|POST|PUT|PATCH|DELETE)\b", message, flags=re.I):
        _append_unique(methods, match.group(1).upper())
    for match in re.finditer(r"\b(?:requests|axios)\.(get|post|put|patch|delete)\(", message, flags=re.I):
        _append_unique(methods, match.group(1).upper())
    for match in re.finditer(r"\bmethod\s*:\s*['\"]?(GET|POST|PUT|PATCH|DELETE)['\"]?", message, flags=re.I):
        _append_unique(methods, match.group(1).upper())
    return methods


def _balanced_brace_snippets(text: str) -> list[str]:
    snippets: list[str] = []
    stack: list[int] = []
    for index, char in enumerate(text):
        if char == "{":
            stack.append(index)
        elif char == "}" and stack:
            start = stack.pop()
            snippet = text[start : index + 1].strip()
            if snippet:
                snippets.append(snippet)
    return snippets


def _jsonish_to_python(snippet: str) -> Any | None:
    candidates = [snippet]
    quoted_keys = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_-]*)\s*:", r'\1"\2":', snippet)
    candidates.append(quoted_keys)
    candidates.append(quoted_keys.replace("'", '"'))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            try:
                parsed = ast.literal_eval(candidate)
            except Exception:
                continue
        if isinstance(parsed, (dict, list)):
            return parsed
    return None


def _extract_structured_payloads(message: str) -> list[Any]:
    payloads: list[Any] = []
    accepted_snippets: list[str] = []
    for snippet in sorted(_balanced_brace_snippets(message), key=len, reverse=True):
        if any(snippet != accepted and snippet in accepted for accepted in accepted_snippets):
            continue
        parsed = _jsonish_to_python(snippet)
        if parsed is not None:
            payloads.append(parsed)
            accepted_snippets.append(snippet)
    return payloads


def _walk_payload(value: Any, *, prefix: str = "") -> tuple[list[str], list[str], dict[str, str]]:
    body_keys: list[str] = []
    nested_paths: list[str] = []
    field_values: dict[str, str] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = _clean_text(key)
            if not normalized_key:
                continue
            if not prefix:
                _append_unique(body_keys, normalized_key)
            path = f"{prefix}.{normalized_key}" if prefix else normalized_key
            _append_unique(nested_paths, path)
            if isinstance(child, (str, int, float, bool)) or child is None:
                field_values[path] = str(child)
            child_keys, child_paths, child_values = _walk_payload(child, prefix=path)
            for item in child_keys:
                _append_unique(body_keys, item)
            for item in child_paths:
                _append_unique(nested_paths, item)
            field_values.update(child_values)
    elif isinstance(value, list):
        list_prefix = f"{prefix}[]" if prefix else "[]"
        for child in value:
            child_keys, child_paths, child_values = _walk_payload(child, prefix=list_prefix)
            for item in child_keys:
                _append_unique(body_keys, item)
            for item in child_paths:
                _append_unique(nested_paths, item)
            field_values.update(child_values)
    return body_keys, nested_paths, field_values


def _infer_question_need(message: str) -> str:
    lowered = message.lower()
    if any(token in lowered for token in ["why", "behavior", "not work", "fail", "error"]):
        return "explain_behavior"
    if any(token in lowered for token in ["correct", "valid", "right payload", "fix"]):
        return "correct_payload"
    if any(token in lowered for token in ["what does", "meaning", "parameter", "field"]):
        return "parameter_meaning"
    return "unknown"


def _schema_goals(endpoint_hints: Iterable[str], body_keys: Iterable[str], nested_paths: Iterable[str]) -> list[str]:
    goals: list[str] = []
    for endpoint in endpoint_hints:
        _append_unique(goals, f"{endpoint} request body schema")
    for key in body_keys:
        _append_unique(goals, f"{key} request body schema")
    for path in nested_paths:
        leaf = str(path).replace("[]", "").split(".")[-1]
        if leaf:
            _append_unique(goals, f"{leaf} schema")
    return goals[:12]


def _rule_detect(message: str) -> RequestBodyEvidenceQuery:
    normalized = str(message or "")
    lowered = normalized.lower()
    endpoints = _extract_endpoint_hints(normalized)
    methods = _extract_http_methods(normalized)
    payloads = _extract_structured_payloads(normalized)
    body_keys: list[str] = []
    nested_paths: list[str] = []
    field_values: dict[str, str] = {}
    for payload in payloads:
        payload_keys, payload_paths, payload_values = _walk_payload(payload)
        for item in payload_keys:
            _append_unique(body_keys, item)
        for item in payload_paths:
            _append_unique(nested_paths, item)
        field_values.update(payload_values)
    has_body_signal = any(token in lowered for token in _BODY_KEYWORDS)
    has_client_call = bool(re.search(r"\b(curl|requests\.(?:post|put|patch)|fetch\(|axios\.(?:post|put|patch))", lowered))
    triggered = bool((payloads and (has_body_signal or methods or endpoints or has_client_call)) or (has_client_call and (endpoints or methods)))
    confidence = 0.0
    if triggered:
        confidence = min(0.98, 0.45 + (0.2 if payloads else 0.0) + (0.15 if endpoints else 0.0) + (0.1 if methods else 0.0) + (0.1 if has_client_call else 0.0))
    return RequestBodyEvidenceQuery(
        is_request_body_or_api_config=triggered,
        confidence=round(confidence, 4),
        endpoint_hints=endpoints,
        http_methods=methods,
        body_keys=body_keys,
        nested_paths=nested_paths,
        field_value_hints=field_values,
        question_need=_infer_question_need(normalized),
        schema_evidence_goals=_schema_goals(endpoints, body_keys, nested_paths),
        analyzer_source="rules",
    )


def _parse_json_object(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _merge_llm_payload(rule_query: RequestBodyEvidenceQuery, payload: dict[str, Any]) -> RequestBodyEvidenceQuery:
    if not isinstance(payload, dict):
        return rule_query
    llm_triggered = bool(payload.get("is_request_body_or_api_config"))
    triggered = bool(rule_query.is_request_body_or_api_config or llm_triggered)
    endpoint_hints = list(rule_query.endpoint_hints)
    for endpoint in (payload.get("endpoint_hints") if isinstance(payload.get("endpoint_hints"), list) else []):
        _append_unique(endpoint_hints, endpoint)
    body_keys = list(rule_query.body_keys)
    for key in (payload.get("body_keys") if isinstance(payload.get("body_keys"), list) else []):
        _append_unique(body_keys, key)
    nested_paths = list(rule_query.nested_paths)
    for path in (payload.get("nested_paths") if isinstance(payload.get("nested_paths"), list) else []):
        _append_unique(nested_paths, path)
    goals = list(rule_query.schema_evidence_goals)
    for goal in (payload.get("schema_evidence_goals") if isinstance(payload.get("schema_evidence_goals"), list) else []):
        _append_unique(goals, goal)
    raw_confidence = payload.get("confidence")
    try:
        llm_confidence = float(raw_confidence)
    except (TypeError, ValueError):
        llm_confidence = 0.0
    field_values = dict(rule_query.field_value_hints)
    raw_values = payload.get("field_value_hints")
    if isinstance(raw_values, dict):
        for key, value in raw_values.items():
            normalized_key = _clean_text(key)
            if normalized_key:
                field_values[normalized_key] = _clean_text(value)
    question_need = _clean_text(payload.get("question_need")) or rule_query.question_need
    if question_need not in {"explain_behavior", "correct_payload", "parameter_meaning", "unknown"}:
        question_need = rule_query.question_need
    return RequestBodyEvidenceQuery(
        is_request_body_or_api_config=triggered,
        confidence=round(max(rule_query.confidence, min(1.0, max(0.0, llm_confidence))), 4),
        endpoint_hints=endpoint_hints,
        http_methods=list(rule_query.http_methods),
        body_keys=body_keys,
        nested_paths=nested_paths,
        field_value_hints=field_values,
        question_need=question_need,
        schema_evidence_goals=goals or _schema_goals(endpoint_hints, body_keys, nested_paths),
        analyzer_source="llm+rules" if llm_triggered else "rules",
    )


def detect_request_body_evidence_query(message: str, *, use_llm: bool = False) -> RequestBodyEvidenceQuery:
    rule_query = _rule_detect(message)
    if not use_llm:
        return rule_query
    try:
        profile = resolve_model_profile(REQUEST_BODY_ANALYZER_SCENARIO)
        if not profile_has_invocation_credentials(profile):
            return rule_query
        response = invoke_responses_text(
            profile=profile,
            system_prompt=build_request_body_evidence_system_prompt(),
            user_prompt=build_request_body_evidence_user_prompt(
                question=message,
                rule_hints={
                    "is_request_body_or_api_config": rule_query.is_request_body_or_api_config,
                    "endpoint_hints": rule_query.endpoint_hints,
                    "http_methods": rule_query.http_methods,
                    "body_keys": rule_query.body_keys,
                    "nested_paths": rule_query.nested_paths,
                    "schema_evidence_goals": rule_query.schema_evidence_goals,
                },
            ),
        )
    except (LlmInvocationError, Exception):
        return rule_query
    parsed = _parse_json_object(str(getattr(response, "text", "") or ""))
    if not parsed:
        return rule_query
    return _merge_llm_payload(rule_query, parsed)


def _chunk_id(chunk: Any) -> str:
    if isinstance(chunk, RequestBodyEvidenceChunk):
        return chunk.chunk_id
    if isinstance(chunk, dict):
        return _clean_text(chunk.get("chunk_id") or chunk.get("id"))
    return _clean_text(getattr(chunk, "chunk_id", "") or getattr(chunk, "id", ""))


def _chunk_text(chunk: Any) -> str:
    if isinstance(chunk, RequestBodyEvidenceChunk):
        return _clean_text(chunk.text_excerpt)
    if isinstance(chunk, dict):
        return str(chunk.get("text") or chunk.get("text_excerpt") or chunk.get("content") or "")
    return str(getattr(chunk, "text", "") or getattr(chunk, "content", "") or "")


def _chunk_source_path(chunk: Any) -> str | None:
    if isinstance(chunk, RequestBodyEvidenceChunk):
        return chunk.source_path
    if isinstance(chunk, dict):
        return _clean_text(chunk.get("source_path")) or None
    return _clean_text(getattr(chunk, "source_path", "")) or None


def _chunk_similarity(chunk: Any) -> float:
    if isinstance(chunk, RequestBodyEvidenceChunk):
        return float(chunk.similarity)
    value = chunk.get("similarity") if isinstance(chunk, dict) else getattr(chunk, "similarity", 0.0)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _chunk_metadata(chunk: Any) -> dict[str, Any]:
    if isinstance(chunk, dict):
        metadata = chunk.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            chunk["metadata"] = metadata
        return metadata
    metadata = getattr(chunk, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
        try:
            setattr(chunk, "metadata", metadata)
        except Exception:
            return {}
    return metadata


def _matches_field(text: str, field: str) -> bool:
    normalized_text = text.lower()
    normalized_field = str(field or "").strip()
    if not normalized_field:
        return False
    collapsed = normalized_field.replace("[]", "").lower()
    if collapsed in normalized_text or collapsed.replace(".", " ") in normalized_text:
        return True
    leaf = collapsed.split(".")[-1]
    parent = collapsed.split(".")[-2] if "." in collapsed else ""
    return bool(leaf and leaf in normalized_text and (not parent or parent in normalized_text))


def _wrap_evidence_chunk(chunk: Any, *, evidence_type: str, query: RequestBodyEvidenceQuery) -> RequestBodyEvidenceChunk:
    text = _chunk_text(chunk)
    matched_fields = [field for field in query.nested_paths if _matches_field(text, field)]
    if not matched_fields:
        matched_fields = [field for field in query.body_keys if _matches_field(text, field)]
    if evidence_type == "schema_by_field":
        evidence_type = "nested_schema" if matched_fields else "request_body_schema"
    metadata = _chunk_metadata(chunk)
    metadata["request_body_evidence_type"] = evidence_type
    metadata["request_body_matched_fields"] = list(matched_fields)
    metadata["request_body_skill_triggered"] = True
    return RequestBodyEvidenceChunk(
        chunk_id=_chunk_id(chunk),
        evidence_type=evidence_type,
        matched_fields=matched_fields,
        source_path=_chunk_source_path(chunk),
        text_excerpt=text[:800],
        similarity=_chunk_similarity(chunk),
        original_chunk=chunk,
    )


def _evidence_search_queries(query: RequestBodyEvidenceQuery) -> list[tuple[str, str]]:
    endpoint = " ".join(query.endpoint_hints[:2])
    fields = " ".join(query.body_keys[:6])
    nested = " ".join(query.nested_paths[:8])
    goals = " ".join(query.schema_evidence_goals[:6])
    searches: list[tuple[str, str]] = []
    if nested or fields:
        searches.append(("schema_by_field", f"{endpoint} {nested or fields} request body schema".strip()))
    if endpoint:
        searches.append(("endpoint_schema", f"{' '.join(query.http_methods)} {endpoint} request body schema API reference".strip()))
        searches.append(("request_parameters", f"{' '.join(query.http_methods)} {endpoint} request parameters".strip()))
        searches.append(("payload_example", f"{' '.join(query.http_methods)} {endpoint} payload example request body".strip()))
    if goals:
        searches.append(("same_doc_neighbor", f"{endpoint} {goals} schema reference".strip()))
    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for evidence_type, search_query in searches:
        normalized = _clean_text(search_query)
        key = (evidence_type, normalized.lower())
        if normalized and key not in seen:
            seen.add(key)
            deduped.append((evidence_type, normalized))
    return deduped


def run_request_body_evidence_skill(
    query: RequestBodyEvidenceQuery,
    *,
    retrieve_chunks: Callable[[str, str], list[Any]],
    max_workers: int = 5,
    max_chunks: int = 5,
) -> RequestBodyEvidenceResult:
    if not query.is_request_body_or_api_config:
        return RequestBodyEvidenceResult(triggered=False, query=query)
    searches = _evidence_search_queries(query)
    wrapped: list[RequestBodyEvidenceChunk] = []
    retrieval_trace = [{"evidence_type": evidence_type, "query": search_query} for evidence_type, search_query in searches]
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers or 1), len(searches) or 1))) as executor:
        futures = {
            executor.submit(retrieve_chunks, search_query, evidence_type): evidence_type
            for evidence_type, search_query in searches
        }
        for future in as_completed(futures):
            evidence_type = futures[future]
            try:
                chunks = future.result()
            except Exception:
                chunks = []
            for chunk in chunks or []:
                if not _chunk_id(chunk):
                    continue
                wrapped.append(_wrap_evidence_chunk(chunk, evidence_type=evidence_type, query=query))
    deduped: dict[str, RequestBodyEvidenceChunk] = {}
    for chunk in wrapped:
        existing = deduped.get(chunk.chunk_id)
        if existing is None:
            deduped[chunk.chunk_id] = chunk
            continue
        if (
            _SCHEMA_EVIDENCE_PRIORITY.get(chunk.evidence_type, 99),
            -chunk.similarity,
        ) < (
            _SCHEMA_EVIDENCE_PRIORITY.get(existing.evidence_type, 99),
            -existing.similarity,
        ):
            deduped[chunk.chunk_id] = chunk
    ordered = sorted(
        deduped.values(),
        key=lambda chunk: (
            _SCHEMA_EVIDENCE_PRIORITY.get(chunk.evidence_type, 99),
            -float(chunk.similarity or 0.0),
            chunk.chunk_id,
        ),
    )
    selected = ordered[: max(1, int(max_chunks or 1))]
    covered_fields: set[str] = set()
    for chunk in selected:
        for field in chunk.matched_fields:
            covered_fields.add(field)
    missing = [field for field in query.nested_paths if field not in covered_fields]
    return RequestBodyEvidenceResult(
        triggered=True,
        query=query,
        chunks=selected,
        missing_evidence=missing,
        retrieval_queries=retrieval_trace,
    )


def _original_chunk(item: Any) -> Any:
    return item.original_chunk if isinstance(item, RequestBodyEvidenceChunk) else item


def _request_body_evidence_type(item: Any) -> str:
    if isinstance(item, RequestBodyEvidenceChunk):
        return item.evidence_type
    metadata = item.get("metadata") if isinstance(item, dict) else getattr(item, "metadata", None)
    if isinstance(metadata, dict):
        return _clean_text(metadata.get("request_body_evidence_type"))
    return ""


def _is_schema_evidence(item: Any) -> bool:
    return _request_body_evidence_type(item) in {"nested_schema", "request_body_schema", "endpoint_schema", "request_parameters"}


def _is_low_value_overview(item: Any) -> bool:
    text = f"{_chunk_source_path(item) or ''} {_chunk_text(item)}".lower()
    return any(token in text for token in ["release note", "release-notes", "product overview", "/overview"])


def merge_request_body_evidence_chunks(
    *,
    primary_chunks: list[Any],
    supplement_chunks: list[Any],
    max_chunks: int,
    reserved_schema_slots: int = 3,
) -> list[Any]:
    limit = max(1, int(max_chunks or 1))
    schema_supplements = [item for item in supplement_chunks if _is_schema_evidence(item)]
    other_supplements = [item for item in supplement_chunks if item not in schema_supplements]
    normal_primary = [item for item in primary_chunks if not _is_low_value_overview(item)]
    overview_primary = [item for item in primary_chunks if _is_low_value_overview(item)]
    ordered = [
        *schema_supplements[: max(0, min(int(reserved_schema_slots or 0), limit))],
        *normal_primary,
        *schema_supplements[max(0, min(int(reserved_schema_slots or 0), limit)) :],
        *other_supplements,
        *overview_primary,
    ]
    merged: list[Any] = []
    seen: set[str] = set()
    for item in ordered:
        chunk = _original_chunk(item)
        dedupe_key = _chunk_id(chunk) or f"{_chunk_source_path(chunk) or ''}:{_chunk_text(chunk)[:120]}"
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        merged.append(chunk)
        if len(merged) >= limit:
            break
    return merged
