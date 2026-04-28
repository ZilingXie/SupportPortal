from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Any

from backend.services.client_query_intent import is_answer_first_how_to_message
from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import QUERY_EXPANSION_SCENARIO, resolve_model_profile
from backend.services.prompts.query_understanding import (
    build_query_decomposition_system_prompt,
    build_query_decomposition_user_prompt,
    build_query_rewrite_system_prompt,
    build_query_rewrite_user_prompt,
    build_self_query_system_prompt,
    build_self_query_user_prompt,
)
from backend.services.query_expansion_cache import QueryExpansionCache, build_query_expansion_cache_key
from backend.services.token_usage import build_usage_ledger_entry

if TYPE_CHECKING:
    from backend.services.rag_qa import RetrievedChunk

LOGGER = logging.getLogger(__name__)

DEFAULT_QUERY_PROFILE = "en"
DEFAULT_PROMPT_PROFILE = "default_en"
QUERY_UNDERSTANDING_VERSION = "v2"
SELF_QUERY_VERSION = "v2"
GLOSSARY_VERSION = "agora_glossary_en_v2"
GLOSSARY_HIT_LIMIT = 5
RULE_EXPANSION_LIMIT = 3
REWRITE_LIMIT = 2
PRF_EXPANSION_LIMIT = 2
DECOMPOSITION_LIMIT = 2
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
_HARD_FILTER_DOWNPUSH_SOURCES = {"rule", "rule+llm"}
_LANGUAGE_MAP = {
    "node.js": "nodejs",
    "nodejs": "nodejs",
    "javascript": "nodejs",
    "typescript": "nodejs",
    "golang": "go",
    "go": "go",
    "python3": "python",
    "python": "python",
    "java": "java",
    "c++": "cpp",
    "cpp": "cpp",
    "php": "php",
    "ios": "ios",
    "swift": "swift",
    "android": "android",
    "kotlin": "kotlin",
    "react native": "react-native",
    "flutter": "flutter",
}
_PROTOCOL_MAP = {
    "rtmp": "rtmp",
    "hls": "hls",
    "http-flv": "http-flv",
    "http flv": "http-flv",
    "webrtc": "webrtc",
}
_METHOD_NAMES = ["BuildTokenWithUidAndPrivilege", "BuildTokenWithUid"]
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "by",
    "do",
    "for",
    "how",
    "i",
    "in",
    "is",
    "it",
    "my",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "why",
    "with",
}
_GENERIC_EXPANSION_TERMS = {
    "agora",
    "channel",
    "issue",
    "problem",
    "audio",
    "video",
    "sdk",
    "documentation",
}
_LANGUAGE_CONTEXT_MARKERS = ("in", "for", "using", "with", "on", "from")
_CODE_CONTEXT_RE = re.compile(
    r"`[^`]+`|\b(api|method|callback|parameter|parameters|code|code sample|sample code|sdk method)\b",
    re.IGNORECASE,
)
_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_GLOSSARY_PATH = _ROOT / "dictionary" / "video-calling_glossary (1).md"
_DEFAULT_GLOSSARY_SNAPSHOT_PATH = _ROOT / "dictionary" / "agora_glossary_en.json"
_DEFAULT_SYMPTOM_LEXICON_PATH = _ROOT / "dictionary" / "troubleshooting_lexicon_en.json"


@dataclass(frozen=True)
class DictionaryEntry:
    source: str
    canonical_term: str
    definition_summary: str
    aliases: tuple[str, ...]
    related_terms: tuple[str, ...]
    expansion_terms: tuple[str, ...]
    metadata_hints: dict[str, Any]
    normalized_aliases: tuple[str, ...]


@dataclass(frozen=True)
class QueryProfile:
    profile_id: str
    prompt_profile: str
    glossary_version: str
    glossary_path: str
    glossary_snapshot_path: str
    symptom_lexicon_path: str
    glossary_entries: tuple[DictionaryEntry, ...]
    symptom_entries: tuple[DictionaryEntry, ...]


@dataclass(frozen=True)
class RetrievalPlan:
    semantic_query: str
    hard_filters: dict[str, str] = field(default_factory=dict)
    soft_signals: dict[str, list[str]] = field(default_factory=dict)
    rewritten_queries: list[str] = field(default_factory=list)
    decomposition_subqueries: list[str] = field(default_factory=list)
    fallback_mode: str = "none"
    rule_expansions: list[str] = field(default_factory=list)
    llm_expansions: list[str] = field(default_factory=list)
    prf_expansions: list[str] = field(default_factory=list)
    hard_filter_sources: dict[str, str] = field(default_factory=dict)
    soft_signal_sources: dict[str, list[str]] = field(default_factory=dict)
    cache_hit: bool = False
    prf_used: bool = False


@dataclass(frozen=True)
class QueryUnderstandingResult:
    query_profile: str
    query_understanding_version: str
    glossary_version: str
    self_query_version: str
    normalized_query: str
    canonical_terms: list[str]
    glossary_hits: list[dict[str, Any]]
    dictionary_hits: list[dict[str, Any]]
    retrieval_plan: RetrievalPlan
    rewritten_queries: list[str]
    decomposition_subqueries: list[str]
    fallback_mode: str
    intent_latency_ms: float = 0.0
    rewrite_latency_ms: float = 0.0
    cache_hit: bool = False
    llm_usage_ledger: list[dict[str, Any]] = field(default_factory=list)

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


def _normalize_aliases(canonical_term: str, aliases: list[str]) -> tuple[str, ...]:
    normalized_aliases = {
        _normalize_key(canonical_term),
        _normalize_key(canonical_term.replace("-", " ")),
        _normalize_key(canonical_term.replace("(", " ").replace(")", " ")),
    }
    for alias in aliases:
        normalized_alias = _normalize_key(alias)
        if normalized_alias:
            normalized_aliases.add(normalized_alias)
            collapsed = normalized_alias.replace(" ", "")
            if collapsed:
                normalized_aliases.add(collapsed)
    return tuple(sorted(alias for alias in normalized_aliases if alias))


def _load_dictionary_entries(path: Path, *, source: str) -> tuple[DictionaryEntry, ...]:
    if not path.is_file():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.warning("Failed to load %s dictionary from %s: %s", source, path, exc)
        return ()
    items = payload if isinstance(payload, list) else []
    entries: list[DictionaryEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        canonical_term = _normalize_space(item.get("canonical_term"))
        definition_summary = _normalize_space(item.get("definition_summary"))
        aliases = _normalize_string_list(item.get("aliases"))
        related_terms = _normalize_string_list(item.get("related_terms"))
        expansion_terms = _normalize_string_list(item.get("expansion_terms"))
        metadata_hints = item.get("metadata_hints") if isinstance(item.get("metadata_hints"), dict) else {}
        if not canonical_term:
            continue
        entries.append(
            DictionaryEntry(
                source=source,
                canonical_term=canonical_term,
                definition_summary=definition_summary,
                aliases=tuple(aliases),
                related_terms=tuple(related_terms),
                expansion_terms=tuple(expansion_terms),
                metadata_hints=dict(metadata_hints),
                normalized_aliases=_normalize_aliases(canonical_term, aliases),
            )
        )
    return tuple(entries)


def _parse_markdown_glossary(markdown_text: str) -> tuple[DictionaryEntry, ...]:
    entries: list[DictionaryEntry] = []
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
        if not term:
            continue
        entries.append(
            DictionaryEntry(
                source="glossary",
                canonical_term=term,
                definition_summary=definition,
                aliases=(),
                related_terms=(),
                expansion_terms=(),
                metadata_hints={},
                normalized_aliases=_normalize_aliases(term, []),
            )
        )
    return tuple(entries)


@lru_cache(maxsize=8)
def load_query_profile(locale: str | None = None, product: str | None = None) -> QueryProfile:
    _ = locale
    _ = product
    glossary_path = _DEFAULT_GLOSSARY_PATH
    glossary_snapshot_path = _DEFAULT_GLOSSARY_SNAPSHOT_PATH
    symptom_lexicon_path = _DEFAULT_SYMPTOM_LEXICON_PATH
    glossary_entries = _load_dictionary_entries(glossary_snapshot_path, source="glossary")
    if not glossary_entries and glossary_path.is_file():
        glossary_entries = _parse_markdown_glossary(glossary_path.read_text(encoding="utf-8"))
    symptom_entries = _load_dictionary_entries(symptom_lexicon_path, source="symptom_lexicon")
    return QueryProfile(
        profile_id=DEFAULT_QUERY_PROFILE,
        prompt_profile=DEFAULT_PROMPT_PROFILE,
        glossary_version=GLOSSARY_VERSION,
        glossary_path=str(glossary_path),
        glossary_snapshot_path=str(glossary_snapshot_path),
        symptom_lexicon_path=str(symptom_lexicon_path),
        glossary_entries=glossary_entries,
        symptom_entries=symptom_entries,
    )


def _find_dictionary_hits(query: str, profile: QueryProfile) -> list[dict[str, Any]]:
    normalized_query = _normalize_key(query)
    collapsed_query = normalized_query.replace(" ", "")
    candidates = [*profile.glossary_entries, *profile.symptom_entries]
    hits: list[tuple[int, int, dict[str, Any]]] = []
    for entry in candidates:
        best_pos: int | None = None
        best_match = ""
        for alias in entry.normalized_aliases:
            if not alias:
                continue
            pos = normalized_query.find(alias)
            if pos == -1 and alias.replace(" ", ""):
                compact_pos = collapsed_query.find(alias.replace(" ", ""))
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
                0 if entry.source == "glossary" else 1,
                {
                    "source": entry.source,
                    "canonical_term": entry.canonical_term,
                    "matched_text": best_match or entry.canonical_term,
                    "definition": entry.definition_summary,
                    "definition_summary": entry.definition_summary,
                    "related_terms": list(entry.related_terms),
                    "expansion_terms": list(entry.expansion_terms),
                    "metadata_hints": dict(entry.metadata_hints),
                },
            )
        )
    ordered = [item[2] for item in sorted(hits, key=lambda item: (item[0], item[1]))]
    return ordered[:GLOSSARY_HIT_LIMIT]


def _contains_alias(text: str, alias: str) -> bool:
    normalized_text = _normalize_space(text).lower()
    normalized_alias = _normalize_space(alias).lower()
    if not normalized_text or not normalized_alias:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])"
    return re.search(pattern, normalized_text, flags=re.IGNORECASE) is not None


def _query_has_explicit_language_context(query: str) -> bool:
    normalized_query = _normalize_space(query).lower()
    if not normalized_query:
        return False
    if _mentioned_methods(query):
        return True
    if _CODE_CONTEXT_RE.search(normalized_query):
        return True
    for alias in _LANGUAGE_MAP:
        if not _contains_alias(normalized_query, alias):
            continue
        if normalized_query.startswith(alias):
            return True
        for marker in _LANGUAGE_CONTEXT_MARKERS:
            if re.search(rf"\b{re.escape(marker)}\s+{re.escape(alias)}\b", normalized_query, flags=re.IGNORECASE):
                return True
    return False


def _detect_language(query: str) -> str | None:
    lowered = _normalize_space(query).lower()
    for alias, normalized in _LANGUAGE_MAP.items():
        if _contains_alias(lowered, alias):
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


def _normalize_source_value(value: Any) -> str | None:
    text = _normalize_space(value)
    return text or None


def _normalize_source_list(value: Any) -> list[str]:
    return _normalize_string_list(value)


def validate_retrieval_plan(raw_plan: dict[str, Any]) -> RetrievalPlan:
    semantic_query = _normalize_space(raw_plan.get("semantic_query"))
    raw_hard_filters = raw_plan.get("hard_filters") if isinstance(raw_plan.get("hard_filters"), dict) else {}
    raw_soft_signals = raw_plan.get("soft_signals") if isinstance(raw_plan.get("soft_signals"), dict) else {}
    raw_hard_filter_sources = (
        raw_plan.get("hard_filter_sources") if isinstance(raw_plan.get("hard_filter_sources"), dict) else {}
    )
    raw_soft_signal_sources = (
        raw_plan.get("soft_signal_sources") if isinstance(raw_plan.get("soft_signal_sources"), dict) else {}
    )
    hard_filters: dict[str, str] = {}
    soft_signals: dict[str, list[str]] = {}
    hard_filter_sources: dict[str, str] = {}
    soft_signal_sources: dict[str, list[str]] = {}

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
            hard_filter_sources[key] = _normalize_source_value(raw_hard_filter_sources.get(key)) or "validated"

    for key, value in raw_soft_signals.items():
        if key not in _ALLOWED_SOFT_SIGNALS:
            continue
        normalized_items = _normalize_string_list(value)
        if normalized_items:
            soft_signals[key] = normalized_items
            normalized_sources = _normalize_source_list(raw_soft_signal_sources.get(key))
            soft_signal_sources[key] = normalized_sources or ["validated"]

    rule_expansions = _normalize_string_list(raw_plan.get("rule_expansions"))[:RULE_EXPANSION_LIMIT]
    llm_expansions = _normalize_string_list(
        raw_plan.get("llm_expansions") if raw_plan.get("llm_expansions") is not None else raw_plan.get("rewritten_queries")
    )[:REWRITE_LIMIT]
    prf_expansions = _normalize_string_list(raw_plan.get("prf_expansions"))[:PRF_EXPANSION_LIMIT]
    decomposition_subqueries = _normalize_string_list(raw_plan.get("decomposition_subqueries"))[:DECOMPOSITION_LIMIT]
    fallback_mode = _normalize_space(raw_plan.get("fallback_mode")) or "validated"

    return RetrievalPlan(
        semantic_query=semantic_query,
        hard_filters=hard_filters,
        soft_signals=soft_signals,
        rewritten_queries=list(llm_expansions),
        decomposition_subqueries=decomposition_subqueries,
        fallback_mode=fallback_mode,
        rule_expansions=rule_expansions,
        llm_expansions=llm_expansions,
        prf_expansions=prf_expansions,
        hard_filter_sources=hard_filter_sources,
        soft_signal_sources=soft_signal_sources,
        cache_hit=bool(raw_plan.get("cache_hit")),
        prf_used=bool(raw_plan.get("prf_used")),
    )


def _append_soft_signal(
    soft_signals: dict[str, list[str]],
    soft_signal_sources: dict[str, list[str]],
    key: str,
    values: Any,
    source: str,
) -> None:
    normalized_values = _normalize_string_list(values)
    if not normalized_values:
        return
    existing = soft_signals.setdefault(key, [])
    for value in normalized_values:
        if value not in existing:
            existing.append(value)
    sources = soft_signal_sources.setdefault(key, [])
    if source and source not in sources:
        sources.append(source)


def _set_hard_filter(
    hard_filters: dict[str, str],
    hard_filter_sources: dict[str, str],
    key: str,
    value: Any,
    source: str,
) -> None:
    validated = validate_retrieval_plan({"hard_filters": {key: value}}).hard_filters.get(key)
    if not validated:
        return
    existing = hard_filters.get(key)
    if existing and existing == validated:
        if hard_filter_sources.get(key) == "rule" and source == "llm_only":
            hard_filter_sources[key] = "rule+llm"
        return
    if existing and existing != validated:
        return
    hard_filters[key] = validated
    hard_filter_sources[key] = source


def _seed_retrieval_plan(query: str, dictionary_hits: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_query = _normalize_space(query)
    lowered = normalized_query.lower()
    hard_filters: dict[str, Any] = {}
    hard_filter_sources: dict[str, str] = {}
    soft_signals: dict[str, list[str]] = {}
    soft_signal_sources: dict[str, list[str]] = {}

    language = _detect_language(normalized_query)
    if language:
        _set_hard_filter(hard_filters, hard_filter_sources, "language", language, "rule")

    mentioned_methods = _mentioned_methods(normalized_query)
    if len(mentioned_methods) == 1:
        _set_hard_filter(hard_filters, hard_filter_sources, "method_name", mentioned_methods[0], "rule")

    protocol = _detect_protocol(normalized_query)
    if protocol:
        _set_hard_filter(hard_filters, hard_filter_sources, "protocol", protocol, "rule")

    if re.search(r"\b(troubleshoot|troubleshooting|debug|diagnose|fix|issue|error|failing|failed|black screen|no audio)\b", lowered):
        _set_hard_filter(hard_filters, hard_filter_sources, "doc_subtype", "troubleshooting_case", "rule")
        _append_soft_signal(soft_signals, soft_signal_sources, "chunk_type", ["troubleshooting_procedure"], "rule")

    if "compare" in lowered or "difference" in lowered or " vs " in lowered or " versus " in lowered:
        _append_soft_signal(soft_signals, soft_signal_sources, "chunk_type", ["decision_logic"], "rule")

    canonical_terms: list[str] = []
    for hit in dictionary_hits:
        canonical_term = _normalize_space(hit.get("canonical_term"))
        if canonical_term and canonical_term not in canonical_terms:
            canonical_terms.append(canonical_term)
        metadata_hints = hit.get("metadata_hints") if isinstance(hit.get("metadata_hints"), dict) else {}
        hard_hint_map = metadata_hints.get("hard_filters") if isinstance(metadata_hints.get("hard_filters"), dict) else {}
        soft_hint_map = metadata_hints.get("soft_signals") if isinstance(metadata_hints.get("soft_signals"), dict) else {}
        for key, value in hard_hint_map.items():
            if key in _ALLOWED_HARD_FILTERS:
                _set_hard_filter(hard_filters, hard_filter_sources, key, value, "rule")
        for key, value in soft_hint_map.items():
            if key in _ALLOWED_SOFT_SIGNALS:
                _append_soft_signal(soft_signals, soft_signal_sources, key, value, "rule")

    if canonical_terms:
        _append_soft_signal(soft_signals, soft_signal_sources, "keywords", canonical_terms[:GLOSSARY_HIT_LIMIT], "rule")

    return {
        "semantic_query": normalized_query,
        "hard_filters": hard_filters,
        "soft_signals": soft_signals,
        "hard_filter_sources": hard_filter_sources,
        "soft_signal_sources": soft_signal_sources,
    }


def _parse_llm_json_payload(raw_text: str) -> dict[str, Any]:
    text = _normalize_space(raw_text)
    if not text:
        return {}
    for candidate in [text, text[text.find("{") : text.rfind("}") + 1] if "{" in text and "}" in text else ""]:
        candidate_text = _normalize_space(candidate)
        if not candidate_text:
            continue
        try:
            parsed = json.loads(candidate_text)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _query_expansion_enabled() -> bool:
    raw = (os.getenv("RAG_QUERY_EXPANSION_ENABLED") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _should_attempt_decomposition(query: str) -> bool:
    lowered = _normalize_space(query).lower()
    return any(
        marker in lowered
        for marker in ["compare", "difference", " vs ", " versus ", " and ", "which one", "both ", "between "]
    )


def _is_simple_lexical_query(query: str) -> bool:
    normalized = _normalize_space(query)
    if not normalized:
        return False
    if re.search(r"\b\d{3,5}\b", normalized.lower()):
        return False
    keywords = [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_.-]{1,}", normalized.lower())
        if token not in _STOPWORDS
    ]
    return len(normalized.split()) <= 6 and len(keywords) <= 4


def _invoke_query_expansion_llm(
    *,
    stage: str,
    system_prompt: str,
    user_prompt: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    profile = resolve_model_profile(QUERY_EXPANSION_SCENARIO)
    result = invoke_responses_text(
        profile=profile,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    usage_entry = build_usage_ledger_entry(
        provider=result.provider_name,
        model=result.model_name,
        stage=stage,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        input_tokens=result.prompt_tokens,
        output_tokens=result.completion_tokens,
    )
    return _parse_llm_json_payload(result.text), usage_entry


def _query_expansion_prompt_model_version(*, query_policy: str | None = None) -> str:
    profile = resolve_model_profile(QUERY_EXPANSION_SCENARIO)
    reasoning = profile.reasoning_effort or "none"
    policy_suffix = str(query_policy or "").strip().lower() or "default"
    fallback_suffix = (
        ",".join(f"{item.provider}:{item.model}" for item in profile.fallback_profiles) or "none"
    )
    return f"{profile.provider}:{profile.model}:{reasoning}:fallback={fallback_suffix}:{SELF_QUERY_VERSION}:{policy_suffix}"


def _load_cached_llm_outputs(
    *,
    normalized_query: str,
    profile: QueryProfile,
    query_policy: str | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    if not _query_expansion_enabled():
        return None, False
    cache = QueryExpansionCache()
    cache_key = build_query_expansion_cache_key(
        normalized_query=normalized_query,
        query_profile=profile.profile_id,
        query_understanding_version=QUERY_UNDERSTANDING_VERSION,
        glossary_version=profile.glossary_version,
        prompt_model_version=_query_expansion_prompt_model_version(query_policy=query_policy),
    )
    payload = cache.get_json(cache_key)
    cache.close()
    return payload, payload is not None


def _store_cached_llm_outputs(
    *,
    normalized_query: str,
    profile: QueryProfile,
    payload: dict[str, Any],
    query_policy: str | None = None,
) -> None:
    if not payload or not _query_expansion_enabled():
        return
    cache = QueryExpansionCache()
    cache_key = build_query_expansion_cache_key(
        normalized_query=normalized_query,
        query_profile=profile.profile_id,
        query_understanding_version=QUERY_UNDERSTANDING_VERSION,
        glossary_version=profile.glossary_version,
        prompt_model_version=_query_expansion_prompt_model_version(query_policy=query_policy),
    )
    cache.set_json(cache_key, payload)
    cache.close()


def _merge_llm_plan(seed_plan: dict[str, Any], llm_plan: RetrievalPlan) -> RetrievalPlan:
    hard_filters = dict(seed_plan.get("hard_filters") or {})
    hard_filter_sources = dict(seed_plan.get("hard_filter_sources") or {})
    soft_signals = {key: list(values) for key, values in dict(seed_plan.get("soft_signals") or {}).items()}
    soft_signal_sources = {
        key: list(values) for key, values in dict(seed_plan.get("soft_signal_sources") or {}).items()
    }

    for key, value in llm_plan.hard_filters.items():
        existing = hard_filters.get(key)
        if existing is None:
            hard_filters[key] = value
            hard_filter_sources[key] = "llm_only"
        elif existing == value and hard_filter_sources.get(key) == "rule":
            hard_filter_sources[key] = "rule+llm"

    for key, values in llm_plan.soft_signals.items():
        _append_soft_signal(soft_signals, soft_signal_sources, key, values, "llm")

    return RetrievalPlan(
        semantic_query=llm_plan.semantic_query or _normalize_space(seed_plan.get("semantic_query")),
        hard_filters=hard_filters,
        soft_signals=soft_signals,
        rewritten_queries=list(llm_plan.rewritten_queries),
        decomposition_subqueries=list(llm_plan.decomposition_subqueries),
        fallback_mode=llm_plan.fallback_mode,
        rule_expansions=[],
        llm_expansions=[],
        prf_expansions=[],
        hard_filter_sources=hard_filter_sources,
        soft_signal_sources=soft_signal_sources,
        cache_hit=llm_plan.cache_hit,
        prf_used=False,
    )


def _query_keywords(query: str, canonical_terms: list[str]) -> set[str]:
    keywords = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", _normalize_space(query).lower())
        if token not in _STOPWORDS
    }
    for term in canonical_terms:
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", term.lower()):
            if token not in _STOPWORDS:
                keywords.add(token)
    return keywords


def _filter_expansion_candidates(
    candidates: list[str],
    *,
    query: str,
    canonical_terms: list[str],
    limit: int,
) -> list[str]:
    existing_lower = _normalize_space(query).lower()
    canonical_lower = {term.lower() for term in canonical_terms}
    query_keywords = _query_keywords(query, canonical_terms)
    filtered: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _normalize_space(candidate)
        lowered = normalized.lower()
        if not normalized or lowered in seen or lowered in existing_lower:
            continue
        tokens = [token for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", lowered) if token not in _STOPWORDS]
        if not tokens:
            continue
        if len(tokens) == 1 and tokens[0] in _GENERIC_EXPANSION_TERMS:
            continue
        if not (set(tokens) & query_keywords or any(term in lowered for term in canonical_lower)):
            continue
        seen.add(lowered)
        filtered.append(normalized)
        if len(filtered) >= limit:
            break
    return filtered


def _build_rule_expansions(
    query: str,
    dictionary_hits: list[dict[str, Any]],
    canonical_terms: list[str],
) -> list[str]:
    candidates: list[str] = []
    for hit in dictionary_hits:
        candidates.extend(_normalize_string_list(hit.get("expansion_terms")))
        candidates.extend(_normalize_string_list(hit.get("related_terms")))
    return _filter_expansion_candidates(
        candidates,
        query=query,
        canonical_terms=canonical_terms,
        limit=RULE_EXPANSION_LIMIT,
    )


def _build_llm_expansions(
    query: str,
    canonical_terms: list[str],
    raw_expansions: list[str],
) -> list[str]:
    return _filter_expansion_candidates(
        raw_expansions,
        query=query,
        canonical_terms=canonical_terms,
        limit=REWRITE_LIMIT,
    )


def _build_heuristic_rewrites(
    query: str,
    *,
    canonical_terms: list[str],
    plan: RetrievalPlan,
    query_policy: str | None = None,
) -> list[str]:
    candidates: list[str] = []
    semantic_query = _normalize_space(plan.semantic_query) or _normalize_space(query)
    if semantic_query and plan.hard_filters.get("language"):
        candidates.append(f"{plan.hard_filters['language']} {semantic_query}")
    mentioned_methods = _mentioned_methods(query)
    lowered = semantic_query.lower()
    if len(mentioned_methods) >= 2 and any(marker in lowered for marker in ["compare", "difference", " vs ", " versus "]):
        candidates.append(f"{mentioned_methods[0]} vs {mentioned_methods[1]} comparison")
    if canonical_terms:
        suffix_terms = [term for term in canonical_terms if term.lower() not in semantic_query.lower()]
        if suffix_terms:
            candidates.append(_normalize_space(f"{semantic_query} {' '.join(suffix_terms[:2])}"))
    if str(query_policy or "").strip().lower() == "client_accuracy_first" and is_answer_first_how_to_message(query):
        lowered = semantic_query.lower()
        if any(marker in lowered for marker in ["join channel", "join a channel", "join the channel"]):
            candidates.append(_normalize_space(f"{semantic_query} quickstart"))
            candidates.append(_normalize_space(f"{semantic_query} token uid"))
    return _filter_expansion_candidates(
        candidates,
        query=query,
        canonical_terms=canonical_terms,
        limit=REWRITE_LIMIT,
    )


def _build_decomposition_subqueries(
    query: str,
    raw_subqueries: list[str],
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for subquery in raw_subqueries:
        text = _normalize_space(subquery)
        lowered = text.lower()
        if not text or lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(text)
        if len(normalized) >= DECOMPOSITION_LIMIT:
            break
    if normalized:
        return normalized
    lowered = _normalize_space(query).lower()
    methods = _mentioned_methods(query)
    if len(methods) >= 2 and any(marker in lowered for marker in ["compare", "difference", " vs ", " versus "]):
        return [f"{methods[0]} usage", f"{methods[1]} usage"][:DECOMPOSITION_LIMIT]
    return []


def downpush_hard_filters(plan: RetrievalPlan, *, query_policy: str | None = None) -> dict[str, str]:
    resolved_policy = str(query_policy or "").strip().lower()
    downpushed: dict[str, str] = {}
    for key, value in plan.hard_filters.items():
        if plan.hard_filter_sources.get(key) not in _HARD_FILTER_DOWNPUSH_SOURCES:
            continue
        if (
            key == "language"
            and resolved_policy == "client_accuracy_first"
            and not _query_has_explicit_language_context(plan.semantic_query)
            and not plan.hard_filters.get("method_name")
            and not plan.hard_filters.get("protocol")
        ):
            continue
        downpushed[key] = value
    return downpushed


def _extract_candidate_phrases_from_chunk(chunk: RetrievedChunk) -> list[tuple[str, float]]:
    metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    candidates: list[tuple[str, float]] = []
    for key, weight in [("keywords", 1.6), ("symptoms", 1.4), ("topic", 0.8), ("use_case", 0.7)]:
        raw_value = metadata.get(key)
        values = _normalize_string_list(raw_value)
        for value in values:
            candidates.append((value.replace("_", " ").replace("-", " "), weight))
    for heading in [chunk.h1, chunk.h2]:
        text = _normalize_space(heading)
        if text:
            candidates.append((text, 0.6))
    return candidates


def build_prf_expansions(
    query: str,
    chunks: list[RetrievedChunk],
    *,
    canonical_terms: list[str],
    existing_expansions: list[str],
) -> list[str]:
    query_keywords = _query_keywords(query, canonical_terms)
    existing_terms = {
        _normalize_space(query).lower(),
        *{_normalize_space(term).lower() for term in canonical_terms},
        *{_normalize_space(term).lower() for term in existing_expansions},
    }
    phrase_scores: dict[str, float] = {}
    phrase_frequency: dict[str, int] = {}
    for rank, chunk in enumerate(chunks[:5], start=1):
        for phrase, base_weight in _extract_candidate_phrases_from_chunk(chunk):
            normalized = _normalize_space(phrase)
            lowered = normalized.lower()
            if not normalized or lowered in existing_terms:
                continue
            tokens = [token for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", lowered) if token not in _STOPWORDS]
            if not tokens:
                continue
            if set(tokens).issubset(query_keywords):
                continue
            if len(tokens) == 1:
                if tokens[0] in _GENERIC_EXPANSION_TERMS:
                    continue
                continue
            if len(tokens) > 5:
                continue
            phrase_frequency[lowered] = phrase_frequency.get(lowered, 0) + 1
            phrase_scores[lowered] = phrase_scores.get(lowered, 0.0) + (
                base_weight + max(0.0, 1.0 - ((rank - 1) * 0.1))
            )
    ordered = sorted(
        phrase_scores.items(),
        key=lambda item: (phrase_frequency.get(item[0], 0), item[1], len(item[0])),
        reverse=True,
    )
    results: list[str] = []
    for lowered, _ in ordered:
        if phrase_frequency.get(lowered, 0) < 2 and " " not in lowered:
            continue
        results.append(lowered)
        if len(results) >= PRF_EXPANSION_LIMIT:
            break
    return results


def understand_rag_query(
    query: str,
    *,
    locale: str | None = None,
    product: str | None = None,
    query_policy: str | None = None,
) -> QueryUnderstandingResult:
    started_at = time.perf_counter()
    profile = load_query_profile(locale=locale, product=product)
    normalized_query = _normalize_space(query)
    dictionary_hits = _find_dictionary_hits(normalized_query, profile)
    glossary_hits = [dict(item) for item in dictionary_hits if item.get("source") == "glossary"]
    canonical_terms: list[str] = []
    for item in dictionary_hits:
        canonical_term = _normalize_space(item.get("canonical_term"))
        if canonical_term and canonical_term not in canonical_terms:
            canonical_terms.append(canonical_term)

    seed_plan = _seed_retrieval_plan(normalized_query, dictionary_hits)
    intent_latency_ms = round((time.perf_counter() - started_at) * 1000, 2)

    if _is_simple_lexical_query(normalized_query):
        rule_expansions = _build_rule_expansions(normalized_query, dictionary_hits, canonical_terms)
        finalized_plan = RetrievalPlan(
            semantic_query=seed_plan.get("semantic_query") or normalized_query,
            hard_filters=dict(seed_plan.get("hard_filters") or {}),
            soft_signals=dict(seed_plan.get("soft_signals") or {}),
            rewritten_queries=[],
            decomposition_subqueries=[],
            fallback_mode="light_path",
            rule_expansions=list(rule_expansions),
            llm_expansions=[],
            prf_expansions=[],
            hard_filter_sources=dict(seed_plan.get("hard_filter_sources") or {}),
            soft_signal_sources=dict(seed_plan.get("soft_signal_sources") or {}),
            cache_hit=False,
            prf_used=False,
        )
        return QueryUnderstandingResult(
            query_profile=profile.profile_id,
            query_understanding_version=QUERY_UNDERSTANDING_VERSION,
            glossary_version=profile.glossary_version,
            self_query_version=SELF_QUERY_VERSION,
            normalized_query=normalized_query,
            canonical_terms=canonical_terms,
            glossary_hits=glossary_hits,
            dictionary_hits=dictionary_hits,
            retrieval_plan=finalized_plan,
            rewritten_queries=[],
            decomposition_subqueries=[],
            fallback_mode="light_path",
            intent_latency_ms=intent_latency_ms,
            rewrite_latency_ms=0.0,
            cache_hit=False,
            llm_usage_ledger=[],
        )

    rewrite_started_at = time.perf_counter()
    fallback_mode = "none"
    cache_payload, cache_hit = _load_cached_llm_outputs(
        normalized_query=normalized_query,
        profile=profile,
        query_policy=query_policy,
    )
    llm_self_query_raw: dict[str, Any] = {}
    llm_rewrite_raw: dict[str, Any] = {}
    llm_decomposition_raw: dict[str, Any] = {}
    llm_usage_ledger: list[dict[str, Any]] = []
    if cache_payload:
        llm_self_query_raw = dict(cache_payload.get("self_query") or {})
        llm_rewrite_raw = dict(cache_payload.get("rewrite") or {})
        llm_decomposition_raw = dict(cache_payload.get("decomposition") or {})
    elif _query_expansion_enabled():
        try:
            llm_self_query_raw, usage_entry = _invoke_query_expansion_llm(
                stage="query_self_query",
                system_prompt=build_self_query_system_prompt(),
                user_prompt=build_self_query_user_prompt(query=normalized_query, glossary_hits=dictionary_hits),
            )
            if usage_entry:
                llm_usage_ledger.append(usage_entry)
        except (LlmInvocationError, ValueError) as exc:
            if "missing_api_key" not in str(exc):
                LOGGER.warning("Query self-query planning failed: %s", exc)
                fallback_mode = "llm_self_query_error"
        try:
            llm_rewrite_raw, usage_entry = _invoke_query_expansion_llm(
                stage="query_rewrite",
                system_prompt=build_query_rewrite_system_prompt(query_policy=query_policy),
                user_prompt=build_query_rewrite_user_prompt(
                    query=normalized_query,
                    canonical_terms=canonical_terms,
                    glossary_hits=dictionary_hits,
                    retrieval_plan_summary={
                        "semantic_query": llm_self_query_raw.get("semantic_query") or seed_plan.get("semantic_query"),
                        "hard_filters": llm_self_query_raw.get("hard_filters") or seed_plan.get("hard_filters"),
                        "soft_signals": llm_self_query_raw.get("soft_signals") or seed_plan.get("soft_signals"),
                    },
                    query_policy=query_policy,
                ),
            )
            if usage_entry:
                llm_usage_ledger.append(usage_entry)
        except (LlmInvocationError, ValueError) as exc:
            if "missing_api_key" not in str(exc):
                LOGGER.warning("Query rewrite failed: %s", exc)
                fallback_mode = fallback_mode if fallback_mode != "none" else "llm_rewrite_error"
        if _should_attempt_decomposition(normalized_query):
            try:
                llm_decomposition_raw, usage_entry = _invoke_query_expansion_llm(
                    stage="query_decomposition",
                    system_prompt=build_query_decomposition_system_prompt(),
                    user_prompt=build_query_decomposition_user_prompt(
                        query=normalized_query,
                        retrieval_plan_summary={
                            "semantic_query": llm_self_query_raw.get("semantic_query") or seed_plan.get("semantic_query"),
                            "hard_filters": llm_self_query_raw.get("hard_filters") or seed_plan.get("hard_filters"),
                            "soft_signals": llm_self_query_raw.get("soft_signals") or seed_plan.get("soft_signals"),
                        },
                    ),
                )
                if usage_entry:
                    llm_usage_ledger.append(usage_entry)
            except (LlmInvocationError, ValueError) as exc:
                if "missing_api_key" not in str(exc):
                    LOGGER.warning("Query decomposition failed: %s", exc)
                    fallback_mode = fallback_mode if fallback_mode != "none" else "llm_decomposition_error"
        if llm_self_query_raw or llm_rewrite_raw or llm_decomposition_raw:
            _store_cached_llm_outputs(
                normalized_query=normalized_query,
                profile=profile,
                payload={
                    "self_query": llm_self_query_raw,
                    "rewrite": llm_rewrite_raw,
                    "decomposition": llm_decomposition_raw,
                },
                query_policy=query_policy,
            )

    llm_plan = validate_retrieval_plan(
        {
            **llm_self_query_raw,
            "rewritten_queries": llm_rewrite_raw.get("rewritten_queries"),
            "decomposition_subqueries": llm_decomposition_raw.get("decomposition_subqueries"),
            "cache_hit": cache_hit,
            "fallback_mode": "none" if fallback_mode == "none" else fallback_mode,
        }
    )
    merged_plan = _merge_llm_plan(seed_plan, llm_plan)
    rule_expansions = _build_rule_expansions(normalized_query, dictionary_hits, canonical_terms)
    llm_expansions = _build_llm_expansions(
        normalized_query,
        canonical_terms,
        _normalize_string_list(llm_rewrite_raw.get("rewritten_queries")),
    )
    if not llm_expansions:
        llm_expansions = _build_heuristic_rewrites(
            normalized_query,
            canonical_terms=canonical_terms,
            plan=merged_plan,
            query_policy=query_policy,
        )
    decomposition_subqueries = _build_decomposition_subqueries(
        normalized_query,
        _normalize_string_list(llm_decomposition_raw.get("decomposition_subqueries")),
    )
    rewrite_latency_ms = round((time.perf_counter() - rewrite_started_at) * 1000, 2)

    finalized_plan = RetrievalPlan(
        semantic_query=merged_plan.semantic_query or normalized_query,
        hard_filters=dict(merged_plan.hard_filters),
        soft_signals=dict(merged_plan.soft_signals),
        rewritten_queries=list(llm_expansions),
        decomposition_subqueries=list(decomposition_subqueries),
        fallback_mode="none" if fallback_mode == "none" else fallback_mode,
        rule_expansions=list(rule_expansions),
        llm_expansions=list(llm_expansions),
        prf_expansions=[],
        hard_filter_sources=dict(merged_plan.hard_filter_sources),
        soft_signal_sources=dict(merged_plan.soft_signal_sources),
        cache_hit=cache_hit,
        prf_used=False,
    )
    return QueryUnderstandingResult(
        query_profile=profile.profile_id,
        query_understanding_version=QUERY_UNDERSTANDING_VERSION,
        glossary_version=profile.glossary_version,
        self_query_version=SELF_QUERY_VERSION,
        normalized_query=normalized_query,
        canonical_terms=canonical_terms,
        glossary_hits=glossary_hits,
        dictionary_hits=dictionary_hits,
        retrieval_plan=finalized_plan,
        rewritten_queries=list(llm_expansions),
        decomposition_subqueries=list(decomposition_subqueries),
        fallback_mode=finalized_plan.fallback_mode,
        intent_latency_ms=intent_latency_ms,
        rewrite_latency_ms=rewrite_latency_ms,
        cache_hit=cache_hit,
        llm_usage_ledger=llm_usage_ledger,
    )
