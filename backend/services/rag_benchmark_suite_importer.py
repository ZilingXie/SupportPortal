from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.repositories.knowledge_repository import KnowledgeRepository


REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARKS_DIR = REPO_ROOT / "benchmarks"
SUPPORTED_BENCHMARK_SUITES = (
    "agora_rag_testset_100_canonical_en",
    "agora_rag_testset_100_real_user_en",
    "agora_rag_testset_100_mixed_en",
)
DEFAULT_SOURCE_TYPES = ["official_markdown_upload", "technical_article_api"]
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CASE_SUFFIX_RE = re.compile(r"(\d+)$")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "do",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "my",
    "of",
    "on",
    "or",
    "should",
    "that",
    "the",
    "to",
    "use",
    "using",
    "what",
    "when",
    "where",
    "who",
    "why",
    "with",
    "your",
}
_SMALL_TALK_REFUSAL = (
    "This request is outside Agora technical support. The assistant should refuse and avoid forcing Agora retrieval."
)
_OFF_TOPIC_REFUSAL = (
    "This request is outside Agora technical support. The assistant should refuse and avoid forcing Agora retrieval."
)


@dataclass(frozen=True)
class _BoundChunk:
    document_id: str
    chunk_id: str
    source_path: str
    source_type: str
    heading: str
    section_path: list[str]
    language: str | None
    product: str | None
    score: float


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _tokenize(value: Any) -> list[str]:
    normalized = _clean_text(value).lower()
    return [token for token in _TOKEN_RE.findall(normalized) if token not in _STOPWORDS and len(token) > 1]


def _sentence_fragments(value: Any) -> list[str]:
    fragments = re.split(r"(?<=[.!?])\s+", _clean_text(value))
    return [fragment.strip() for fragment in fragments if fragment.strip()]


def _content_hash(payload: list[dict[str, Any]]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()[:12]


def suite_benchmark_version(suite_name: str, suite_payload: list[dict[str, Any]]) -> str:
    normalized_name = _clean_text(suite_name)
    if not normalized_name:
        raise ValueError("suite_name is required")
    return f"{normalized_name}_{_content_hash(suite_payload)}"


def _suite_path(suite_name: str) -> Path:
    normalized_name = _clean_text(suite_name)
    if normalized_name not in SUPPORTED_BENCHMARK_SUITES:
        raise ValueError(f"Unsupported benchmark suite: {suite_name}")
    return BENCHMARKS_DIR / f"{normalized_name}.json"


def load_suite_payload(suite_name: str) -> list[dict[str, Any]]:
    path = _suite_path(suite_name)
    if not path.exists():
        raise FileNotFoundError(f"Benchmark suite not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Benchmark suite must be a JSON array: {path}")
    items = [item for item in payload if isinstance(item, dict)]
    if not items:
        raise ValueError(f"Benchmark suite is empty: {path}")
    return items


def _numeric_suffix(case_id: Any) -> str:
    match = _CASE_SUFFIX_RE.search(_clean_text(case_id))
    if not match:
        raise ValueError(f"Benchmark case id must end with a numeric suffix: {case_id}")
    return match.group(1)


def _split_key_points(reference_answer: str) -> list[str]:
    parts = _sentence_fragments(reference_answer)
    if not parts:
        return []
    return parts[:3]


def _prepare_source_chunks(source_chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for chunk in source_chunks:
        if not isinstance(chunk, dict):
            continue
        search_parts = [
            chunk.get("source_path"),
            chunk.get("heading"),
            chunk.get("product"),
            (chunk.get("metadata") or {}).get("title") if isinstance(chunk.get("metadata"), dict) else None,
            chunk.get("text"),
        ]
        search_text = " ".join(_clean_text(part) for part in search_parts if _clean_text(part)).strip()
        prepared.append(
            {
                **chunk,
                "_search_text": search_text,
                "_search_tokens": set(_tokenize(search_text)),
                "_heading_tokens": set(_tokenize(chunk.get("heading"))),
            }
        )
    return prepared


def _bind_chunk(question: str, reference_answer: str, prepared_chunks: list[dict[str, Any]]) -> _BoundChunk | None:
    query_tokens = set(_tokenize(f"{question} {reference_answer}"))
    if not query_tokens or not prepared_chunks:
        return None
    answer_fragments = [fragment.lower() for fragment in _sentence_fragments(reference_answer)[:2] if len(fragment) >= 16]
    best: _BoundChunk | None = None
    best_score = -1.0
    for chunk in prepared_chunks:
        search_tokens = chunk.get("_search_tokens") or set()
        heading_tokens = chunk.get("_heading_tokens") or set()
        overlap = len(query_tokens & search_tokens)
        heading_overlap = len(query_tokens & heading_tokens)
        if overlap <= 0 and heading_overlap <= 0 and answer_fragments:
            if not any(fragment in str(chunk.get("_search_text") or "").lower() for fragment in answer_fragments):
                continue
        score = 0.0
        score += (overlap / max(1, len(query_tokens))) * 5.0
        score += (heading_overlap / max(1, len(query_tokens))) * 2.0
        lowered_search = str(chunk.get("_search_text") or "").lower()
        for fragment in answer_fragments:
            if fragment in lowered_search:
                score += 2.5
        if score <= best_score:
            continue
        best_score = score
        best = _BoundChunk(
            document_id=_clean_text(chunk.get("document_id")),
            chunk_id=_clean_text(chunk.get("chunk_id")),
            source_path=_clean_text(chunk.get("source_path")),
            source_type=_clean_text(chunk.get("source_type")) or "official_markdown_upload",
            heading=_clean_text(chunk.get("heading")),
            section_path=[_clean_text(item) for item in list(chunk.get("section_path") or []) if _clean_text(item)],
            language=_clean_text(chunk.get("language")) or "en",
            product=_clean_text(chunk.get("product")),
            score=round(score, 4),
        )
    if best is not None:
        return best
    fallback = prepared_chunks[0]
    return _BoundChunk(
        document_id=_clean_text(fallback.get("document_id")),
        chunk_id=_clean_text(fallback.get("chunk_id")),
        source_path=_clean_text(fallback.get("source_path")),
        source_type=_clean_text(fallback.get("source_type")) or "official_markdown_upload",
        heading=_clean_text(fallback.get("heading")),
        section_path=[_clean_text(item) for item in list(fallback.get("section_path") or []) if _clean_text(item)],
        language=_clean_text(fallback.get("language")) or "en",
        product=_clean_text(fallback.get("product")),
        score=0.0,
    )


def _infer_query_type(question: str, *, default_value: str = "faq") -> str:
    lowered = _clean_text(question).lower()
    if any(term in lowered for term in ("error", "failed", "doesn't work", "not work", "issue", "problem")):
        return "troubleshooting"
    if any(term in lowered for term in ("should", "when should", "which", "choose", "better")):
        return "decision"
    if any(term in lowered for term in ("configure", "set up", "setup", "enable", "disable")):
        return "configuration"
    return default_value


def _difficulty_for_question_type(question_type: str) -> str:
    normalized = _clean_text(question_type).lower()
    if normalized in {"scenario"}:
        return "medium"
    if normalized in {"trap", "decision"}:
        return "advanced"
    return "basic"


def _synthetic_external_binding(case_id: str, *, source_path: str, scope_label: str) -> _BoundChunk:
    suffix = _numeric_suffix(case_id)
    heading = scope_label.replace("_", " ").strip() or "external benchmark"
    return _BoundChunk(
        document_id="external-benchmark-placeholder",
        chunk_id=f"external-benchmark-{suffix}",
        source_path=source_path,
        source_type="external_benchmark",
        heading=heading,
        section_path=[heading],
        language="en",
        product="Agora",
        score=1.0,
    )


def _build_item(
    *,
    suite_name: str,
    raw_case: dict[str, Any],
    question: str,
    reference_answer: str,
    query_type: str,
    difficulty: str,
    bound_chunk: _BoundChunk,
    expected_route: str,
    expected_scope_label: str,
    retrieval_metrics_enabled: bool,
    citation_metrics_enabled: bool,
    original_case_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    heading_path = list(bound_chunk.section_path) or ([bound_chunk.heading] if bound_chunk.heading else [])
    clean_reference = _clean_text(reference_answer)
    item_metadata = {
        "suite_name": suite_name,
        "original_case_id": original_case_id,
        "expected_route": expected_route,
        "expected_scope_label": expected_scope_label,
        "retrieval_metrics_enabled": retrieval_metrics_enabled,
        "citation_metrics_enabled": citation_metrics_enabled,
        "route_aware": True,
        "evidence_binding_score": bound_chunk.score,
    }
    if isinstance(metadata, dict):
        item_metadata.update({key: value for key, value in metadata.items() if value is not None})
    return {
        "dataset_item_id": original_case_id,
        "document_id": bound_chunk.document_id,
        "chunk_id": bound_chunk.chunk_id,
        "source_path": bound_chunk.source_path,
        "source_type": bound_chunk.source_type,
        "query_type": query_type,
        "difficulty": difficulty,
        "language": bound_chunk.language or "en",
        "product": bound_chunk.product,
        "question": question,
        "reference_answer": clean_reference,
        "answer_key_points": _split_key_points(clean_reference) or [clean_reference],
        "expected_document_ids": [bound_chunk.document_id],
        "expected_heading_paths": heading_path,
        "expected_evidence_refs": [
            {
                "chunk_id": bound_chunk.chunk_id,
                "doc_id": bound_chunk.document_id,
                "heading": bound_chunk.heading,
            }
        ],
        "expected_citation_targets": [],
        "item_status": "gold",
        "dataset_quality_score": 1.0,
        "judge_disagreement_flag": False,
        "ambiguity_flag": False,
        "answer_leakage_flag": False,
        "citation_bindable_flag": True,
        "logic_eval_applicable": True,
        "sampling_reasons": [],
        "judge_votes": [],
        "metadata": item_metadata,
        "promoted_at": None,
    }


def _build_canonical_binding_map(
    canonical_payload: list[dict[str, Any]],
    prepared_chunks: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}
    for raw_case in canonical_payload:
        case_id = _clean_text(raw_case.get("id"))
        suffix = _numeric_suffix(case_id)
        question = _clean_text(raw_case.get("question"))
        reference_answer = _clean_text(raw_case.get("answer"))
        if not question or not reference_answer:
            raise ValueError(f"Canonical case is missing question or answer: {case_id}")
        bound_chunk = _bind_chunk(question, reference_answer, prepared_chunks)
        if bound_chunk is None:
            raise ValueError(f"Unable to bind canonical benchmark case to a source chunk: {case_id}")
        bindings[suffix] = {
            "question": question,
            "reference_answer": reference_answer,
            "bound_chunk": bound_chunk,
            "category": _clean_text(raw_case.get("category")),
            "query_type": _infer_query_type(question),
        }
    return bindings


def build_suite_dataset_items(
    *,
    suite_name: str,
    suite_payload: list[dict[str, Any]],
    source_chunks: list[dict[str, Any]],
    canonical_payload: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    normalized_suite_name = _clean_text(suite_name)
    if normalized_suite_name not in SUPPORTED_BENCHMARK_SUITES:
        raise ValueError(f"Unsupported benchmark suite: {suite_name}")
    prepared_chunks = _prepare_source_chunks(source_chunks)
    canonical_bindings = _build_canonical_binding_map(canonical_payload or [], prepared_chunks) if canonical_payload else {}
    items: list[dict[str, Any]] = []

    if normalized_suite_name == "agora_rag_testset_100_canonical_en":
        for raw_case in suite_payload:
            case_id = _clean_text(raw_case.get("id"))
            question = _clean_text(raw_case.get("question"))
            reference_answer = _clean_text(raw_case.get("answer"))
            if not case_id or not question or not reference_answer:
                raise ValueError("Canonical suite items require id, question, and answer")
            bound_chunk = _bind_chunk(question, reference_answer, prepared_chunks)
            if bound_chunk is None:
                raise ValueError(f"Unable to bind canonical benchmark case to a source chunk: {case_id}")
            items.append(
                _build_item(
                    suite_name=normalized_suite_name,
                    raw_case=raw_case,
                    question=question,
                    reference_answer=reference_answer,
                    query_type=_infer_query_type(question),
                    difficulty="basic",
                    bound_chunk=bound_chunk,
                    expected_route="rag",
                    expected_scope_label="agora_technical",
                    retrieval_metrics_enabled=True,
                    citation_metrics_enabled=True,
                    original_case_id=case_id,
                    metadata={
                        "set": _clean_text(raw_case.get("set")),
                        "category": _clean_text(raw_case.get("category")),
                    },
                )
            )
        return items

    if normalized_suite_name == "agora_rag_testset_100_real_user_en":
        if not canonical_bindings:
            raise ValueError("Real-user suite import requires canonical_payload")
        for raw_case in suite_payload:
            case_id = _clean_text(raw_case.get("id"))
            question = _clean_text(raw_case.get("question"))
            if not case_id or not question:
                raise ValueError("Real-user suite items require id and question")
            canonical = canonical_bindings.get(_numeric_suffix(case_id))
            if canonical is None:
                raise ValueError(f"Real-user case does not match canonical benchmark suffix: {case_id}")
            bound_chunk = canonical["bound_chunk"]
            items.append(
                _build_item(
                    suite_name=normalized_suite_name,
                    raw_case=raw_case,
                    question=question,
                    reference_answer=canonical["reference_answer"],
                    query_type=canonical["query_type"],
                    difficulty="basic",
                    bound_chunk=bound_chunk,
                    expected_route="rag",
                    expected_scope_label="agora_technical",
                    retrieval_metrics_enabled=True,
                    citation_metrics_enabled=True,
                    original_case_id=case_id,
                    metadata={
                        "set": _clean_text(raw_case.get("set")),
                        "category": canonical.get("category"),
                        "canonical_case_id": f"agora-canonical-{_numeric_suffix(case_id)}",
                    },
                )
            )
        return items

    for raw_case in suite_payload:
        case_id = _clean_text(raw_case.get("id"))
        question = _clean_text(raw_case.get("question"))
        question_type = _clean_text(raw_case.get("question_type")).lower()
        category = _clean_text(raw_case.get("category"))
        if not case_id or not question or not question_type:
            raise ValueError("Mixed suite items require id, question, and question_type")

        if question_type in {"fact", "scenario", "trap"}:
            reference_answer = _clean_text(raw_case.get("expected_answer")) or _clean_text(raw_case.get("expected_behavior"))
            bound_chunk = _bind_chunk(question, reference_answer, prepared_chunks)
            if bound_chunk is None:
                raise ValueError(f"Unable to bind mixed benchmark case to a source chunk: {case_id}")
            items.append(
                _build_item(
                    suite_name=normalized_suite_name,
                    raw_case=raw_case,
                    question=question,
                    reference_answer=reference_answer,
                    query_type=question_type,
                    difficulty=_difficulty_for_question_type(question_type),
                    bound_chunk=bound_chunk,
                    expected_route="rag",
                    expected_scope_label="agora_technical",
                    retrieval_metrics_enabled=True,
                    citation_metrics_enabled=True,
                    original_case_id=case_id,
                    metadata={"category": category, "question_type": question_type},
                )
            )
            continue

        if question_type == "small_talk":
            reference_answer = _SMALL_TALK_REFUSAL
            expected_route = "refuse"
            expected_scope_label = "small_talk"
            citation_metrics_enabled = False
        elif question_type == "off_topic":
            reference_answer = _OFF_TOPIC_REFUSAL
            expected_route = "refuse"
            expected_scope_label = "non_agora"
            citation_metrics_enabled = False
        elif question_type == "agora_nontechnical":
            reference_answer = _clean_text(raw_case.get("expected_answer")) or _clean_text(raw_case.get("expected_behavior"))
            expected_route = "web_search"
            expected_scope_label = "agora_non_technical"
            citation_metrics_enabled = True
        else:
            raise ValueError(f"Unsupported mixed question_type: {question_type}")

        items.append(
            _build_item(
                suite_name=normalized_suite_name,
                raw_case=raw_case,
                question=question,
                reference_answer=reference_answer,
                query_type=question_type,
                difficulty=_difficulty_for_question_type(question_type),
                bound_chunk=_synthetic_external_binding(
                    case_id,
                    source_path=f"benchmarks/{normalized_suite_name}.json",
                    scope_label=expected_scope_label,
                ),
                expected_route=expected_route,
                expected_scope_label=expected_scope_label,
                retrieval_metrics_enabled=False,
                citation_metrics_enabled=citation_metrics_enabled,
                original_case_id=case_id,
                metadata={"category": category, "question_type": question_type},
            )
        )
    return items


def import_benchmark_suite(
    repository: "KnowledgeRepository",
    *,
    suite_name: str,
    question_language: str = "en",
    initialize_repository: bool = True,
) -> dict[str, Any]:
    if initialize_repository:
        repository.initialize()
    normalized_suite_name = _clean_text(suite_name)
    suite_payload = load_suite_payload(normalized_suite_name)
    canonical_payload = load_suite_payload("agora_rag_testset_100_canonical_en") if normalized_suite_name == "agora_rag_testset_100_real_user_en" else []
    source_chunks = repository.list_dataset_generation_source_chunks(
        source_types=DEFAULT_SOURCE_TYPES,
        question_language=question_language,
    )
    items = build_suite_dataset_items(
        suite_name=normalized_suite_name,
        suite_payload=suite_payload,
        source_chunks=source_chunks,
        canonical_payload=canonical_payload,
    )
    return repository.upsert_imported_benchmark_dataset(
        dataset_name=normalized_suite_name,
        benchmark_version=suite_benchmark_version(normalized_suite_name, suite_payload),
        question_language=question_language,
        items=items,
    )
