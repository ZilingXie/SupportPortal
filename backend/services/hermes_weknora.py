"""Read-only WeKnora client and the review-result adapter boundary.

WeKnora owns knowledge content, versions, and retrieval; its memory is
caller-isolated, so the Hermes Review role never touches WeKnora credentials
or implicit agent memory. SupportPortal performs the read-only similarity
search on the Review role's behalf and hands validated review decisions to
the adapter layer defined here. Nothing in this module writes knowledge: the
submission records produced by the adapter are the controlled interface a
future WeKnora writer will consume.

The transport targets the official WeKnora hybrid-search API
(`POST /api/v1/knowledge-bases/{knowledge_base_id}/hybrid-search`). Every
transport or contract failure is reported as unavailable — the workflow then
fails closed to human review instead of inventing similarity evidence.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

LOGGER = logging.getLogger("supportportal.hermes_weknora")

WEKNORA_SEARCH_PATH = "/api/v1/knowledge-bases/{knowledge_base_id}/hybrid-search"
WEKNORA_SEARCH_TOP_K = 3
WEKNORA_REQUEST_TIMEOUT_SECONDS = 15.0


class WeKnoraUnavailable(RuntimeError):
    """WeKnora is unconfigured or the read-only search failed."""


class HermesWeKnoraClient:
    """Minimal read-only WeKnora similarity-search client."""

    def __init__(
        self, *, base_url: str | None = None, api_token: str | None = None,
        knowledge_base_id: str | None = None, timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = str(base_url if base_url is not None else os.getenv("HERMES_WEKNORA_BASE_URL") or "").strip().rstrip("/")
        self.api_token = str(api_token if api_token is not None else os.getenv("HERMES_WEKNORA_API_TOKEN") or "").strip()
        self.knowledge_base_id = str(
            knowledge_base_id
            if knowledge_base_id is not None
            else os.getenv("HERMES_WEKNORA_KNOWLEDGE_BASE_ID") or ""
        ).strip()
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else float(os.getenv("HERMES_WEKNORA_TIMEOUT_SECONDS") or WEKNORA_REQUEST_TIMEOUT_SECONDS)
        )

    def configured(self) -> bool:
        return bool(self.base_url and self.api_token and self.knowledge_base_id)

    def search(self, query: str, *, top_k: int = WEKNORA_SEARCH_TOP_K) -> list[dict[str, Any]]:
        """Search similar knowledge entries; raises WeKnoraUnavailable on any failure."""
        normalized = str(query or "").strip()
        if not normalized:
            raise WeKnoraUnavailable("empty weknora query")
        if not self.configured():
            raise WeKnoraUnavailable("weknora client not configured")
        body = json.dumps({"query_text": normalized, "match_count": int(top_k)}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url
            + WEKNORA_SEARCH_PATH.format(
                knowledge_base_id=urllib.parse.quote(self.knowledge_base_id, safe="")
            ),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "supportportal-weknora/1",
                "X-API-Key": self.api_token,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise WeKnoraUnavailable(f"weknora search transport failed: {exc}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WeKnoraUnavailable("weknora search returned a non-JSON body") from exc
        results = payload.get("data") if isinstance(payload, dict) else None
        if results is None:
            return []
        if not isinstance(results, list):
            raise WeKnoraUnavailable("weknora search response missing data list")
        normalized_results: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            normalized_results.append({
                "object_id": str(item.get("knowledge_id") or item.get("id") or ""),
                "version": str(item.get("content_revision") or ""),
                "title": str(item.get("knowledge_title") or item.get("title") or ""),
                "snippet": str(item.get("content") or item.get("matched_content") or ""),
                "score": item.get("score"),
            })
        return normalized_results


def build_weknora_submissions(
    report: dict[str, Any],
    *,
    lineage_extras: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Convert a validated review report into per-decision adapter submissions.

    Every decision is handed over — including `human_review`, which stays a
    pending-human record — so the adapter layer is the complete, traceable
    boundary between SupportPortal lineage and WeKnora content.
    ``lineage_extras`` carries the run-time lineage the report itself cannot
    know (ticket id, Summary/Review session and run ids, Slack thread) so
    every submission is independently traceable.
    """
    lineage = {
        "engineer_case_id": str(report.get("engineer_case_id") or ""),
        "client_ticket_id": str(report.get("client_ticket_id") or ""),
        "episode": int(report.get("episode") or 0),
        "ledger_revision": int(report.get("ledger_revision") or 0),
        "conversation_version": int(report.get("conversation_version") or 0),
        "summary_id": str(report.get("summary_id") or ""),
        "review_id": str(report.get("review_id") or ""),
    }
    for key in (
        "investigation_id", "summary_session_id", "summary_run_id",
        "review_session_id", "review_run_id", "slack_channel_id", "slack_thread_ts",
    ):
        value = str((lineage_extras or {}).get(key) or "").strip()
        if value:
            lineage[key] = value
    submissions: list[dict[str, Any]] = []
    for decision in report.get("decisions") or []:
        if not isinstance(decision, dict):
            continue
        candidate_id = str(decision.get("candidate_id") or "")
        submissions.append({
            "submission_id": f"weknora-submission:{lineage['review_id']}:{candidate_id}",
            "candidate_id": candidate_id,
            "candidate_type": str(decision.get("candidate_type") or ""),
            "decision": str(decision.get("decision") or ""),
            "proposed_content": str(decision.get("proposed_content") or ""),
            "target_object": decision.get("target_object"),
            "target_version": decision.get("target_version"),
            "kind": str(decision.get("kind") or ""),
            "importance": decision.get("importance"),
            "rationale": str(decision.get("rationale") or ""),
            "confidence": decision.get("confidence"),
            "source_references": list(decision.get("source_references") or []),
            "report_hash": str(report.get("content_hash") or ""),
            "lineage": dict(lineage),
        })
    return submissions
