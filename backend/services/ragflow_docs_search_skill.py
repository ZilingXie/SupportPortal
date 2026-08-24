from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import RAG_ANSWER_SCENARIO, resolve_model_profile
from backend.services.prompts.rag_answer import (
    INSUFFICIENT_EVIDENCE_REPLY,
    build_rag_answer_system_prompt,
    build_rag_answer_user_prompt,
)

DEFAULT_RAGFLOW_BASE_URL = "https://knowledge.convoai.club/kb/ticket-agent"
DEFAULT_TOP_K = 6
_TRUSTED_DOC_HOSTS = frozenset({"docs.agora.io", "api-ref.agora.io"})
_SKILL_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "ragflow-docs-search"
    / "scripts"
    / "search.py"
)


class RagflowDocsSearchError(RuntimeError):
    def __init__(self, failure_kind: str) -> None:
        super().__init__(failure_kind)
        self.failure_kind = str(failure_kind or "unknown").strip() or "unknown"


def _trusted_source_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    try:
        trusted_port = parsed.port in {None, 443}
    except ValueError:
        trusted_port = False
    if (
        parsed.scheme != "https"
        or str(parsed.hostname or "").lower() not in _TRUSTED_DOC_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or not trusted_port
    ):
        return ""
    return url


def _normalize_search_results(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise RagflowDocsSearchError("invalid_search_response")
    results: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        source_url = _trusted_source_url(item.get("source_url"))
        if not content or not source_url:
            continue
        try:
            similarity = float(item.get("similarity") or 0.0)
        except (TypeError, ValueError):
            similarity = 0.0
        results.append(
            {
                "chunk_id": f"ragflow-{len(results) + 1}",
                "content": content,
                "source_url": source_url,
                "document": str(item.get("doc") or "").strip(),
                "similarity": similarity,
            }
        )
    return results


def _context_block(results: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for item in results:
        sections.append(
            "\n".join(
                [
                    f"### chunk_id: {item['chunk_id']}",
                    f"source_url: {item['source_url']}",
                    f"similarity: {item['similarity']:.3f}",
                    "content:",
                    str(item["content"]),
                ]
            )
        )
    return "\n\n".join(sections)


def _question_with_ticket_context(
    question: str,
    ticket_context: list[dict[str, str]] | None,
) -> str:
    normalized_question = str(question or "").strip()
    context_lines: list[str] = []
    for item in list(ticket_context or [])[-6:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role and content:
            context_lines.append(f"{role}: {content}")
    if not context_lines:
        return normalized_question
    return (
        f"Latest customer question:\n{normalized_question}\n\n"
        "Recent ticket context (interpretation only, not documentation evidence):\n"
        + "\n".join(context_lines)
    )


def _render_answer(answer: str, key_steps: Any) -> str:
    normalized_answer = str(answer or "").strip()
    if not normalized_answer:
        return ""
    steps = [str(item or "").strip() for item in key_steps] if isinstance(key_steps, list) else []
    steps = [item for item in steps if item]
    if not steps:
        return normalized_answer
    return f"{normalized_answer}\n\n" + "\n".join(
        f"{index}. {step}" for index, step in enumerate(steps, start=1)
    )


class RagflowDocsSearchSkillClient:
    def __init__(self, *, script_path: Path | None = None, top_k: int = DEFAULT_TOP_K) -> None:
        self._script_path = script_path or _SKILL_SCRIPT
        self._top_k = max(1, int(top_k))

    def _search(self, question: str, *, top_k: int, timeout_seconds: float) -> list[dict[str, Any]]:
        env = dict(os.environ)
        if not str(env.get("RAGFLOW_BASE_URL") or "").strip():
            env["RAGFLOW_BASE_URL"] = DEFAULT_RAGFLOW_BASE_URL
        command = [
            sys.executable,
            str(self._script_path),
            "search",
            str(question or "").strip(),
            "--top-k",
            str(top_k),
            "--json",
            "--no-rerank",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise RagflowDocsSearchError("timeout") from exc
        except OSError as exc:
            raise RagflowDocsSearchError("execution") from exc
        if completed.returncode != 0:
            stderr = str(completed.stderr or "")
            if "RAGFLOW_API_KEY not set" in stderr or "set RAGFLOW_API_KEY" in stderr:
                failure_kind = "configuration"
            elif "HTTP 401" in stderr:
                failure_kind = "authentication"
            elif "HTTP 403" in stderr:
                failure_kind = "access"
            else:
                failure_kind = "search"
            raise RagflowDocsSearchError(failure_kind)
        stdout = str(completed.stdout or "").strip()
        if not stdout or stdout.startswith("No results for:"):
            return []
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RagflowDocsSearchError("invalid_search_response") from exc
        return _normalize_search_results(payload)

    def query(
        self,
        *,
        question: str,
        request_id: str,
        ticket_id: str | None = None,
        customer_id: str | None = None,
        ticket_context: list[dict[str, str]] | None = None,
        top_k: int | None = None,
        timeout_seconds: float = 120.0,
        **_: Any,
    ) -> dict[str, Any]:
        del request_id, ticket_id, customer_id
        normalized_question = str(question or "").strip()
        if not normalized_question:
            return {"decision": "escalate", "answer": "", "reason": "empty_question"}
        timeout = max(1.0, float(timeout_seconds))
        started_at = time.monotonic()
        results = self._search(
            normalized_question,
            top_k=max(1, int(top_k or self._top_k)),
            timeout_seconds=timeout,
        )
        if not results:
            return {"decision": "escalate", "answer": "", "reason": "insufficient_evidence"}

        remaining_seconds = timeout - (time.monotonic() - started_at)
        if remaining_seconds <= 0:
            raise RagflowDocsSearchError("timeout")
        profile = resolve_model_profile(RAG_ANSWER_SCENARIO)
        profile = replace(
            profile,
            timeout_seconds=min(profile.timeout_seconds, remaining_seconds),
            max_retries=0,
            fallback_models=(),
            fallback_profiles=(),
        )
        try:
            response = invoke_responses_text(
                profile=profile,
                system_prompt=build_rag_answer_system_prompt(
                    insufficient_reply=INSUFFICIENT_EVIDENCE_REPLY,
                ),
                user_prompt=build_rag_answer_user_prompt(
                    question=_question_with_ticket_context(normalized_question, ticket_context),
                    context_block=_context_block(results),
                    insufficient_reply=INSUFFICIENT_EVIDENCE_REPLY,
                    repair_mode=False,
                ),
            )
        except (LlmInvocationError, ValueError) as exc:
            raise RagflowDocsSearchError("generation") from exc
        try:
            payload = json.loads(str(response.text or "").strip())
        except json.JSONDecodeError as exc:
            raise RagflowDocsSearchError("invalid_generation_response") from exc
        if not isinstance(payload, dict):
            raise RagflowDocsSearchError("invalid_generation_response")
        if payload.get("insufficient_evidence") is not False:
            return {"decision": "escalate", "answer": "", "reason": "insufficient_evidence"}

        answer = _render_answer(payload.get("answer"), payload.get("key_steps"))
        citation_ids = payload.get("citations") if isinstance(payload.get("citations"), list) else []
        citation_ids = [str(item or "").strip() for item in citation_ids if str(item or "").strip()]
        results_by_id = {str(item["chunk_id"]): item for item in results}
        if not answer:
            return {"decision": "escalate", "answer": "", "reason": "empty_generated_answer"}
        if not citation_ids or any(item not in results_by_id for item in citation_ids):
            return {"decision": "escalate", "answer": "", "reason": "invalid_citations"}

        citations: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for chunk_id in citation_ids:
            source_url = str(results_by_id[chunk_id]["source_url"])
            if source_url in seen_urls:
                continue
            seen_urls.add(source_url)
            citations.append({"source_url": source_url})
        if not citations:
            return {"decision": "escalate", "answer": "", "reason": "invalid_citations"}
        return {
            "decision": "answer",
            "answer": answer,
            "citations": citations,
        }
