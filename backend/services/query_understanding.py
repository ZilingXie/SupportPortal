from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
import re
import time
from typing import Any

DEFAULT_QUERY_PROFILE = "en"
DEFAULT_PROMPT_PROFILE = "default_en"
QUERY_UNDERSTANDING_VERSION = "v1"
SELF_QUERY_VERSION = "v1"
GLOSSARY_VERSION = "video-calling_glossary_en_v1"
GLOSSARY_HIT_LIMIT = 5
REWRITE_LIMIT = 2
DECOMPOSITION_LIMIT = 3
_ALLOWED_HARD_FILTERS = {
    "language",
    "method_name",
    "product",
    "protocol",
    "source_family",
    "doc_subtype",
}
_ALLOWED_SOFT_SIGNALS = {
    "chunk_type",
    "section_path",
    "topic",
    "use_case",
    "issue_category",
    "symptoms",
    "keywords",
    "external_service",
}
_LANGUAGE_MAP = {
    "node.js": "nodejs",
    "nodejs": "nodejs",
    "javascript": "nodejs",
    "golang": "go",
    "go": "go",
    "python3": "python",
    "python": "python",
    "java": "java",
    "c++": "cpp",
    "cpp": "cpp",
    "php": "php",
}
_PROTOCOL_MAP = {
    "rtmp": "rtmp",
    "hls": "hls",
    "http-flv": "http-flv",
    "http flv": "http-flv",
    "webrtc": "webrtc",
}
_METHOD_NAMES = ["BuildTokenWithUidAndPrivilege", "BuildTokenWithUid"]
_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_GLOSSARY_PATH = _ROOT / "dictionary" / "video-calling_glossary (1).md"


@dataclass(frozen=True)
class GlossaryEntry:
    canonical_term: str
    definition: str
    normalized_aliases: tuple[str, ...]


@dataclass(frozen=True)
class QueryProfile:
    profile_id: str
    prompt_profile: str
    glossary_version: str
    glossary_path: str
    glossary_entries: tuple[GlossaryEntry, ...]


@dataclass(frozen=True)
class RetrievalPlan:
    semantic_query: str
    hard_filters: dict[str, str] = field(default_factory=dict)
    soft_signals: dict[str, list[str]] = field(default_factory=dict)
    rewritten_queries: list[str] = field(default_factory=list)
    decomposition_subqueries: list[str] = field(default_factory=list)
    fallback_mode: str = "none"


@dataclass(frozen=True)
class QueryUnderstandingResult:
    query_profile: str
    query_understanding_version: str
    glossary_version: str
    self_query_version: str
    normalized_query: str
    canonical_terms: list[str]
    glossary_hits: list[dict[str, str]]
    retrieval_plan: RetrievalPlan
    rewritten_queries: list[str]
    decomposition_subqueries: list[str]
    fallback_mode: str
    intent_latency_ms: float = 0.0
    rewrite_latency_ms: float = 0.0

    @property
    def semantic_query(self) -> str:
        return self.retrieval_plan.semantic_query


def _normalize_space(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_key(value: Any) -> str:
    lowered = _normalize_space(value).lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(lowered.split()).strip()


def _slugify(value: Any) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", _normalize_space(value).lower())).strip("-")


def _parse_glossary_entries(markdown_text: str) -> tuple[GlossaryEntry, ...]:
    entries: list[GlossaryEntry] = []
    lines = markdown_text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line.startswith("### "):
            index += 1
            continue
        term = _normalize_space(line[4:])
        index += 1
        definition_lines: list[str] = []
        while index < len(lines):
            current = lines[index].rstrip()
            stripped = current.strip()
            if stripped.startswith("### ") or stripped.startswith("## "):
                break
            if stripped:
                definition_lines.append(stripped)
            index += 1
        definition = _normalize_space(" ".join(definition_lines))
        if not term or not definition:
            continue
        normalized_aliases = {
            _normalize_key(term),
            _normalize_key(term.replace("-", " ")),
            _normalize_key(term.replace("(", " ").replace(")", " ")),
        }
        collapsed = _normalize_key(term).replace(" ", "")
        if collapsed:
            normalized_aliases.add(collapsed)
        entries.append(
            GlossaryEntry(
                canonical_term=term,
                definition=definition,
                normalized_aliases=tuple(sorted(alias for alias in normalized_aliases if alias)),
            )
        )
    return tuple(entries)


@lru_cache(maxsize=8)
def load_query_profile(locale: str | None = None, product: str | None = None) -> QueryProfile:
    _ = locale
    _ = product
    glossary_path = _DEFAULT_GLOSSARY_PATH
    entries: tuple[GlossaryEntry, ...] = ()
    if glossary_path.is_file():
        entries = _parse_glossary_entries(glossary_path.read_text(encoding="utf-8"))
    return QueryProfile(
        profile_id=DEFAULT_QUERY_PROFILE,
        prompt_profile=DEFAULT_PROMPT_PROFILE,
        glossary_version=GLOSSARY_VERSION,
        glossary_path=str(glossary_path),
        glossary_entries=entries,
    )


def _find_glossary_hits(query: str, profile: QueryProfile) -> list[dict[str, str]]:
    raw_query = _normalize_space(query)
    normalized_query = _normalize_key(raw_query)
    collapsed_query = normalized_query.replace(" ", "")
    hits: list[tuple[int, int, dict[str, str]]] = []
    for entry in profile.glossary_entries:
        best_pos: int | None = None
        best_match = ""
        for alias in entry.normalized_aliases:
            if not alias:
                continue
            pos = normalized_query.find(alias)
            if pos == -1 and alias.replace(" ", ""):
                compact_alias = alias.replace(" ", "")
                compact_pos = collapsed_query.find(compact_alias)
                if compact_pos != -1:
                    pos = compact_pos
            if pos == -1:
                continue
            if best_pos is None or pos < best_pos:
                best_pos = pos
                best_match = alias
        if best_pos is None:
            continue
        hits.append(
            (
                best_pos,
                -len(entry.canonical_term),
                {
                    "canonical_term": entry.canonical_term,
                    "matched_text": best_match or entry.canonical_term,
                    "definition": entry.definition,
                },
            )
        )
    ordered = [item[2] for item in sorted(hits, key=lambda item: (item[0], item[1]))]
    return ordered[:GLOSSARY_HIT_LIMIT]


def _detect_language(query: str) -> str | None:
    lowered = _normalize_space(query).lower()
    for alias, normalized in _LANGUAGE_MAP.items():
        if alias in lowered:
            return normalized
    return None


def _detect_protocol(query: str) -> str | None:
    lowered = _normalize_space(query).lower()
    for alias, normalized in _PROTOCOL_MAP.items():
        if alias in lowered:
            return normalized
    return None


def _mentioned_methods(query: str) -> list[str]:
    matches: list[tuple[int, str]] = []
    for method_name in _METHOD_NAMES:
        for match in re.finditer(rf"\b{re.escape(method_name)}\b", query, flags=re.IGNORECASE):
            matches.append((match.start(), method_name))
    seen: set[str] = set()
    ordered: list[str] = []
    for _, method_name in sorted(matches, key=lambda item: item[0]):
        if method_name in seen:
            continue
        seen.add(method_name)
        ordered.append(method_name)
    return ordered


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = []
    normalized: list[str] = []
    for item in items:
        text = _normalize_space(item)
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _validate_language(value: Any) -> str | None:
    lowered = _normalize_space(value).lower()
    return _LANGUAGE_MAP.get(lowered)


def _validate_protocol(value: Any) -> str | None:
    lowered = _normalize_space(value).lower()
    return _PROTOCOL_MAP.get(lowered)


def _validate_doc_subtype(value: Any) -> str | None:
    normalized = _slugify(value)
    return normalized if normalized in {"troubleshooting-case", "troubleshooting_case"} else None


def _validate_method_name(value: Any) -> str | None:
    text = _normalize_space(value)
    if not text or text.isdigit():
        return None
    return text


def _validate_text_filter(value: Any) -> str | None:
    text = _slugify(value)
    if not text or text.isdigit():
        return None
    return text


def validate_retrieval_plan(raw_plan: dict[str, Any]) -> RetrievalPlan:
    semantic_query = _normalize_space(raw_plan.get("semantic_query"))
    raw_hard_filters = raw_plan.get("hard_filters") if isinstance(raw_plan.get("hard_filters"), dict) else {}
    raw_soft_signals = raw_plan.get("soft_signals") if isinstance(raw_plan.get("soft_signals"), dict) else {}
    hard_filters: dict[str, str] = {}
    soft_signals: dict[str, list[str]] = {}

    for key, value in raw_hard_filters.items():
        if key not in _ALLOWED_HARD_FILTERS:
            continue
        normalized: str | None
        if key == "language":
            normalized = _validate_language(value)
        elif key == "protocol":
            normalized = _validate_protocol(value)
        elif key == "doc_subtype":
            normalized = _validate_doc_subtype(value)
            if normalized == "troubleshooting-case":
                normalized = "troubleshooting_case"
        elif key == "method_name":
            normalized = _validate_method_name(value)
        else:
            normalized = _validate_text_filter(value)
        if normalized:
            hard_filters[key] = normalized

    for key, value in raw_soft_signals.items():
        if key not in _ALLOWED_SOFT_SIGNALS:
            continue
        normalized_items = _normalize_string_list(value)
        if normalized_items:
            soft_signals[key] = normalized_items

    rewritten_queries = _normalize_string_list(raw_plan.get("rewritten_queries"))
    decomposition_subqueries = _normalize_string_list(raw_plan.get("decomposition_subqueries"))
    fallback_mode = _normalize_space(raw_plan.get("fallback_mode")) or "validated"

    return RetrievalPlan(
        semantic_query=semantic_query,
        hard_filters=hard_filters,
        soft_signals=soft_signals,
        rewritten_queries=rewritten_queries[:REWRITE_LIMIT],
        decomposition_subqueries=decomposition_subqueries[:DECOMPOSITION_LIMIT],
        fallback_mode=fallback_mode,
    )


def _heuristic_retrieval_plan(query: str, canonical_terms: list[str]) -> dict[str, Any]:
    normalized_query = _normalize_space(query)
    lowered = normalized_query.lower()
    hard_filters: dict[str, Any] = {}
    soft_signals: dict[str, Any] = {}

    language = _detect_language(normalized_query)
    if language:
        hard_filters["language"] = language

    mentioned_methods = _mentioned_methods(normalized_query)
    if len(mentioned_methods) == 1:
        hard_filters["method_name"] = mentioned_methods[0]

    protocol = _detect_protocol(normalized_query)
    if protocol:
        hard_filters["protocol"] = protocol

    if re.search(r"\b(troubleshoot|troubleshooting|debug|diagnose|fix|issue|error|failing|failed|black screen|no audio)\b", lowered):
        hard_filters["doc_subtype"] = "troubleshooting_case"
        soft_signals["chunk_type"] = ["troubleshooting_procedure"]
    if "compare" in lowered or "difference" in lowered or " vs " in lowered or " versus " in lowered:
        soft_signals.setdefault("chunk_type", []).append("decision_logic")
    if any("token" in term.lower() for term in canonical_terms) or "token" in lowered:
        soft_signals.setdefault("topic", []).append("authentication")
    if canonical_terms:
        soft_signals["keywords"] = canonical_terms[:GLOSSARY_HIT_LIMIT]
    if "wildcard" in lowered:
        soft_signals.setdefault("use_case", []).append("wildcard_tokens")
    if "jitter" in lowered:
        soft_signals.setdefault("keywords", []).append("jitter")

    return {
        "semantic_query": normalized_query,
        "hard_filters": hard_filters,
        "soft_signals": soft_signals,
    }


def _build_rewritten_queries(query: str, canonical_terms: list[str], plan: RetrievalPlan) -> list[str]:
    candidates: list[str] = []
    semantic_query = _normalize_space(plan.semantic_query) or _normalize_space(query)
    if semantic_query:
        suffix_terms = [term for term in canonical_terms if term.lower() not in semantic_query.lower()]
        if suffix_terms:
            candidates.append(_normalize_space(f"{semantic_query} {' '.join(suffix_terms[:2])}"))
    if plan.hard_filters.get("language") and semantic_query:
        candidates.append(_normalize_space(f"{plan.hard_filters['language']} {semantic_query}"))
    deduped: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped and candidate != _normalize_space(query):
            deduped.append(candidate)
    return deduped[:REWRITE_LIMIT]


def _build_decomposition_subqueries(query: str, plan: RetrievalPlan) -> list[str]:
    lowered = _normalize_space(query).lower()
    mentioned_methods = _mentioned_methods(query)
    subqueries: list[str] = []
    if len(mentioned_methods) >= 2 and any(marker in lowered for marker in ["compare", "difference", " vs ", " versus "]):
        language_prefix = f"{plan.hard_filters['language']} " if plan.hard_filters.get("language") else ""
        for method_name in mentioned_methods[:2]:
            subqueries.append(_normalize_space(f"{language_prefix}{method_name} usage"))
        subqueries.append(_normalize_space(f"{' vs '.join(mentioned_methods[:2])} comparison"))
    return subqueries[:DECOMPOSITION_LIMIT]


def understand_rag_query(
    query: str,
    *,
    locale: str | None = None,
    product: str | None = None,
) -> QueryUnderstandingResult:
    started_at = time.perf_counter()
    profile = load_query_profile(locale=locale, product=product)
    normalized_query = _normalize_space(query)
    glossary_hits = _find_glossary_hits(normalized_query, profile)
    canonical_terms = [item["canonical_term"] for item in glossary_hits]
    raw_plan = _heuristic_retrieval_plan(normalized_query, canonical_terms)
    intent_latency_ms = round((time.perf_counter() - started_at) * 1000, 2)

    rewrite_started_at = time.perf_counter()
    plan = validate_retrieval_plan(raw_plan)
    rewritten_queries = _build_rewritten_queries(normalized_query, canonical_terms, plan)
    decomposition_subqueries = _build_decomposition_subqueries(normalized_query, plan)
    rewrite_latency_ms = round((time.perf_counter() - rewrite_started_at) * 1000, 2)

    finalized_plan = RetrievalPlan(
        semantic_query=plan.semantic_query or normalized_query,
        hard_filters=dict(plan.hard_filters),
        soft_signals=dict(plan.soft_signals),
        rewritten_queries=list(rewritten_queries),
        decomposition_subqueries=list(decomposition_subqueries),
        fallback_mode="none",
    )
    return QueryUnderstandingResult(
        query_profile=profile.profile_id,
        query_understanding_version=QUERY_UNDERSTANDING_VERSION,
        glossary_version=profile.glossary_version,
        self_query_version=SELF_QUERY_VERSION,
        normalized_query=normalized_query,
        canonical_terms=canonical_terms,
        glossary_hits=glossary_hits,
        retrieval_plan=finalized_plan,
        rewritten_queries=list(rewritten_queries),
        decomposition_subqueries=list(decomposition_subqueries),
        fallback_mode="none",
        intent_latency_ms=intent_latency_ms,
        rewrite_latency_ms=rewrite_latency_ms,
    )
