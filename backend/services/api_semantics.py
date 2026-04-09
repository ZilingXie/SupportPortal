from __future__ import annotations

import re
import urllib.parse
from typing import Any

_DOCS_URL_RE = re.compile(r"https?://docs\.agora\.io/[^\s)]+", re.IGNORECASE)
_METHOD_ENDPOINT_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[\w./%-]+)", re.IGNORECASE)
_BARE_ENDPOINT_RE = re.compile(r"(/(?:dev|api|v\d+)[\w./%-]+)", re.IGNORECASE)
_PARAM_RE = re.compile(r"\b(?:uid|str_uid|time(?:_in_seconds)?|cname|ip)\b", re.IGNORECASE)
_MISMATCH_RE = re.compile(
    r"\b(?:documentation|documented|official documentation|docs|actual behavior|actual use|difference|differences|"
    r"mismatch|does not match|inconsistent|according to the documentation|according to the docs|however)\b",
    re.IGNORECASE,
)
_BEHAVIOR_RE = re.compile(
    r"\b(?:cannot be used|works correctly|omitting the|returns?\s+\{|must be a number|no rule has been created|"
    r"success|rejected)\b",
    re.IGNORECASE,
)
_NUMBERED_BLOCK_RE = re.compile(r"(?ms)^\s*(\d+)\.\s+(.*?)(?=^\s*\d+\.\s+|\Z)")
_PLATFORM_OR_SDK_RE = re.compile(
    r"\b(?:android|ios|web|flutter|react native|react-native|unity|windows|macos|linux|cpp|sdk|rest api|api version)\b",
    re.IGNORECASE,
)
_DOCS_PATH_SIGNAL_ALLOWLIST = {
    "broadcast-streaming",
    "voice-calling",
    "video-calling",
    "interactive-live-streaming",
    "channel-management-api",
    "ban-user-privileges",
    "best-practices",
    "endpoint",
    "create-rules",
    "delete-rules",
    "get-rule-list",
    "update-expiration-time",
}
_ENDPOINT_OPERATION_HINTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("post", "kicking-rule"): ("create-rules", "request parameters", "request-parameters"),
    ("get", "kicking-rule"): ("get-rule-list", "request parameters", "request-parameters"),
    ("delete", "kicking-rule"): ("delete-rules", "request parameters", "request-parameters"),
    ("put", "kicking-rule"): ("update-expiration-time", "request parameters", "request-parameters"),
    ("patch", "kicking-rule"): ("update-expiration-time", "request parameters", "request-parameters"),
}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_signal(value: Any) -> str:
    return _clean_text(value).strip(" \t\r\n.,;:!?()[]{}<>\"'")


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _clean_signal(value)
        if not clean:
            continue
        lowered = clean.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(clean)
    return deduped


def extract_docs_urls(message: str) -> list[str]:
    return _dedupe(_DOCS_URL_RE.findall(str(message or "")))


def extract_endpoint_paths(message: str) -> list[str]:
    text = str(message or "")
    endpoints = [match.group(2) for match in _METHOD_ENDPOINT_RE.finditer(text)]
    if not endpoints:
        endpoints = [match.group(1) for match in _BARE_ENDPOINT_RE.finditer(text)]
    return _dedupe(endpoints)


def extract_endpoint_calls(message: str) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    for match in _METHOD_ENDPOINT_RE.finditer(str(message or "")):
        method = _clean_signal(match.group(1)).upper()
        endpoint = _clean_signal(match.group(2))
        if method and endpoint:
            calls.append((method, endpoint))
    return calls


def extract_parameter_terms(message: str) -> list[str]:
    return _dedupe([match.group(0).lower() for match in _PARAM_RE.finditer(str(message or ""))])


def extract_platform_or_sdk(message: str) -> str | None:
    match = _PLATFORM_OR_SDK_RE.search(str(message or ""))
    if match is None:
        return None
    return _clean_text(match.group(0)).lower()


def extract_endpoint_operation_hints(message: str) -> list[str]:
    hints: list[str] = []
    for method, endpoint in extract_endpoint_calls(message):
        slug = endpoint.rstrip("/").split("/")[-1].strip().lower()
        if not slug:
            continue
        hints.extend(_ENDPOINT_OPERATION_HINTS.get((method.lower(), slug), ()))
    return _dedupe(hints)


def extract_anchor_hits(message: str) -> list[str]:
    hits: list[str] = []
    for raw_url in extract_docs_urls(message):
        parsed = urllib.parse.urlparse(raw_url)
        path_parts = [_clean_signal(part) for part in parsed.path.split("/") if _clean_signal(part)]
        for part in path_parts:
            if part.lower() in _DOCS_PATH_SIGNAL_ALLOWLIST:
                hits.append(part)
        if path_parts:
            hits.append(path_parts[-1])
        if parsed.fragment:
            hits.append(_clean_signal(parsed.fragment))
    for endpoint in extract_endpoint_paths(message):
        endpoint = endpoint.rstrip("/")
        if endpoint:
            hits.append(_clean_signal(endpoint.split("/")[-1]))
    hits.extend(extract_endpoint_operation_hints(message))
    hits.extend(extract_parameter_terms(message))
    return _dedupe(hits)


def build_anchor_variant(message: str) -> str | None:
    endpoint_operation_hints = extract_endpoint_operation_hints(message)
    parameter_terms = extract_parameter_terms(message)
    if endpoint_operation_hints and parameter_terms:
        focused_hits: list[str] = []
        focused_hits.extend(endpoint_operation_hints)
        focused_hits.extend(parameter_terms)
        endpoint_paths = extract_endpoint_paths(message)
        if endpoint_paths:
            focused_hits.append(_clean_signal(endpoint_paths[0].rstrip("/").split("/")[-1]))
        for raw_url in extract_docs_urls(message):
            parsed = urllib.parse.urlparse(raw_url)
            path_parts = [_clean_signal(part) for part in parsed.path.split("/") if _clean_signal(part)]
            for part in path_parts:
                lowered = part.lower()
                if lowered in {"broadcast-streaming", "voice-calling", "video-calling", "interactive-live-streaming"}:
                    focused_hits.append(part)
                    break
        focused_variant = " ".join(_dedupe(focused_hits))
        if focused_variant:
            return focused_variant
    hits = extract_anchor_hits(message)
    if not hits:
        return None
    return " ".join(_dedupe(hits))


def _build_shared_api_semantics_context(message: str) -> str:
    text = str(message or "")
    parts: list[str] = []
    endpoint_calls = extract_endpoint_calls(text)
    endpoints = extract_endpoint_paths(text)
    docs_urls = extract_docs_urls(text)
    anchor_hits = extract_anchor_hits(text)
    if endpoint_calls:
        method, endpoint = endpoint_calls[0]
        parts.append(f"{method} {endpoint}")
    elif endpoints:
        parts.append(f"endpoint {endpoints[0]}")
    if docs_urls:
        parts.append(f"docs {docs_urls[0]}")
    shared_terms = [hit for hit in anchor_hits if hit.lower() not in {"uid", "time", "str_uid"}][:4]
    if shared_terms:
        parts.append(" ".join(shared_terms))
    lowered = text.lower()
    if "disband" in lowered and "channel" in lowered:
        parts.append("disband channel")
    return _clean_text(" ".join(parts))


def extract_numbered_subqueries(message: str, *, max_items: int = 3) -> list[str]:
    text = str(message or "")
    matches = list(_NUMBERED_BLOCK_RE.finditer(text))
    if not matches:
        return []
    prefix = _build_shared_api_semantics_context(text) or _clean_text(text[: matches[0].start()])
    subqueries: list[str] = []
    for match in matches[: max(1, int(max_items or 1))]:
        body = _clean_text(match.group(2))
        if not body:
            continue
        combined = f"{prefix}\n{body}" if prefix else body
        subqueries.append(combined)
    return _dedupe(subqueries)


def query_class_from_rag_result(rag_result: dict[str, Any] | None) -> str | None:
    if not isinstance(rag_result, dict):
        return None
    evidence_summary = rag_result.get("evidence_summary")
    if isinstance(evidence_summary, dict):
        diagnostics = evidence_summary.get("diagnostics")
        if isinstance(diagnostics, dict):
            retrieval_snapshot = diagnostics.get("retrieval_plan_snapshot")
            if isinstance(retrieval_snapshot, dict):
                query_class = _clean_text(retrieval_snapshot.get("query_class")).lower()
                if query_class:
                    return query_class
    return None


def is_api_semantics_mismatch_message(message: str) -> bool:
    text = str(message or "")
    lowered = text.lower()
    docs_urls = extract_docs_urls(text)
    endpoints = extract_endpoint_paths(text)
    params = extract_parameter_terms(text)
    numbered_blocks = extract_numbered_subqueries(text, max_items=3)
    has_mismatch_language = bool(_MISMATCH_RE.search(text))
    has_behavior_signal = bool(_BEHAVIOR_RE.search(text))
    if docs_urls and endpoints and params and (has_mismatch_language or has_behavior_signal):
        return True
    if docs_urls and numbered_blocks and params and "actual behavior" in lowered:
        return True
    return False


def is_api_semantics_mismatch_context(
    *,
    message: str,
    rag_result: dict[str, Any] | None = None,
) -> bool:
    query_class = query_class_from_rag_result(rag_result)
    if query_class == "api_semantics_mismatch":
        return True
    return is_api_semantics_mismatch_message(message)


def build_api_semantics_clarification(
    message: str,
    *,
    rag_result: dict[str, Any] | None = None,
) -> tuple[dict[str, str], list[str], str]:
    known_information: dict[str, str] = {}
    docs_urls = extract_docs_urls(message)
    endpoints = extract_endpoint_paths(message)
    platform_or_sdk = extract_platform_or_sdk(message)
    if docs_urls:
        known_information["docs_page_or_api_version"] = docs_urls[0]
    elif endpoints:
        known_information["docs_page_or_api_version"] = endpoints[0]
    if platform_or_sdk:
        known_information["platform_or_sdk"] = platform_or_sdk

    missing_information: list[str] = []
    if not known_information.get("platform_or_sdk"):
        missing_information.append("platform_or_sdk")
    if not known_information.get("docs_page_or_api_version"):
        missing_information.append("docs_page_or_api_version")

    if missing_information:
        reply = (
            "I can help verify the documentation and API semantics here. "
            "Please confirm the affected platform or SDK (for example Android, iOS, Web, Flutter, or the REST API version) "
            "and the exact docs page or API version you are comparing."
            if len(missing_information) > 1
            else "I can help verify the documentation and API semantics here. "
            "Please confirm the affected platform or SDK (for example Android, iOS, Web, Flutter, or the REST API version)."
        )
        return known_information, missing_information, reply

    reason = _clean_text((rag_result or {}).get("reason")) or "api_semantics_clarification"
    reply = (
        "I can help verify the documentation and API semantics here. "
        f"I still need to confirm the exact SDK family or API version for this behavior before I give a final answer. ({reason})"
    )
    return known_information, [], reply
