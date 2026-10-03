"""Knowledge-governance workflow for Hermes engineer cases (Summary + Review).

Two logical roles on the existing Hermes agent gateway:

- Summary — created on the FIRST terminal transition of a case episode
  (Zendesk ``solved``, local ``resolved``, or ``closed``), runs on the case's
  original Hermes session with read-only case context, and returns a strict
  :class:`HermesSummaryPacket` parsed from the run's final JSON block.
- Review — created only after a Summary completes, runs on a NEW dedicated
  Hermes session with the ``knowledge-review`` skill and no case-write or
  memory toolsets, and returns per-candidate :class:`HermesReviewDecision`
  objects. SupportPortal validates schema, lineage, content hash, session
  identity, and restricted identifiers before handing the decisions to the
  WeKnora adapter layer; Hermes never persists knowledge and never writes to
  WeKnora.

SupportPortal remains the authority for cross-system tasks and lineage.
Reopen (a new episode) invalidates unfinished tasks in the reopen
transaction; a revision that advances while a task is in flight fails the
task at completion instead of being marked successful.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from backend.repositories.hermes_case_repository import HermesRepositoryConflict
from backend.services.automation_ecs_store import AGENT_MODEL_ENV_NAME
from backend.services.automation_hermes_agent import (
    CORE_PROMPT_FALLBACK_TEXT,
    CORE_PROMPT_KEY,
    KNOWLEDGE_REVIEW_PROMPT_KEY,
    KNOWLEDGE_REVIEW_TOOLSETS,
    KNOWLEDGE_SUMMARY_PROMPT_KEY,
    KNOWLEDGE_SUMMARY_TOOLSETS,
    workspace_key_for,
)
from backend.services.hermes_agent_runtime import (
    TERMINAL_RUN_STATUSES,
    HermesAgentClient,
    HermesAgentError,
    HermesAgentSettings,
)
from backend.services.hermes_case_workflow import (
    HermesReviewReport,
    HermesSummaryPacket,
    WeKnoraPromotionCandidate,
    _SUMMARY_TEXT_FIELDS,
    _normalize_summary_text,
    hermes_workflow_mode,
    review_report_content_hash,
    summary_packet_content_hash,
)
from backend.services.hermes_case_workflow import _weknora_candidate_hash
from backend.services.hermes_weknora import (
    HermesWeKnoraClient,
    WeKnoraUnavailable,
    build_weknora_submissions,
)
from backend.services.prompt_runtime import resolve_system_prompt

LOGGER = logging.getLogger("supportportal.hermes_knowledge_workflow")

HERMES_KNOWLEDGE_REVIEW_SKILL_VERSION = "knowledge-review-v1"
KNOWLEDGE_SUMMARY_REASONING_EFFORT = "medium"
KNOWLEDGE_REVIEW_REASONING_EFFORT = "xhigh"
KNOWLEDGE_TASK_LEASE_SECONDS = 900.0
KNOWLEDGE_BUNDLE_MAX_CHARS = 200_000

SUMMARY_CONTENT_FIELDS = (
    "problem_description", "timeline", "investigation_process", "confirmed_facts",
    "root_cause_and_solution", "verification_results", "limitations_and_unconfirmed",
    "evidence_references", "candidates",
)


def normalize_summary_output(parsed: Any) -> dict[str, Any]:
    """Apply the Summary output content contract to a parsed run payload.

    Shared by the case-bound and standalone Summary paths (review round 2,
    R2-9): every narrative field passes the documented scalar-or-string-list
    boundary and ``candidates`` is a list — a malformed payload fails the task
    visibly instead of silently entering the pipeline.
    """
    if not isinstance(parsed, dict):
        raise KnowledgeWorkflowError("output_contract_invalid", "summary output is not a JSON object")
    content: dict[str, Any] = {}
    for field in SUMMARY_CONTENT_FIELDS:
        value = parsed.get(field)
        if field in _SUMMARY_TEXT_FIELDS:
            content[field] = _normalize_summary_text(value, field=field)
        elif field == "candidates":
            if not isinstance(value, list):
                raise KnowledgeWorkflowError(
                    "output_contract_invalid", "candidates must be a list"
                )
            content[field] = value
        else:
            content[field] = value
    return content

_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)


class KnowledgeWorkflowError(RuntimeError):
    """Local knowledge-workflow failure with a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def knowledge_workflow_active() -> bool:
    """The pipeline only runs against the real Hermes case workflow + gateway."""
    if hermes_workflow_mode() != "real":
        return False
    return HermesAgentSettings.from_env().configured()


def summary_task_id_for(engineer_case_id: str, episode: int) -> str:
    return f"hermes-summary-task:{engineer_case_id}:{episode}"


def review_task_id_for(
    engineer_case_id: str, episode: int, *, generation: str = ""
) -> str:
    # The generation suffix (from the summary task id) flows through so a
    # NEW Summary generation earns a NEW independent Review — reviewing the
    # same (case, episode) with different frozen input must never reuse the
    # first generation's review task/session/idempotency key (review P1-7).
    suffix = f":{generation}" if generation else ""
    return f"hermes-review-task:{engineer_case_id}:{episode}{suffix}"


def review_session_id_for(
    engineer_case_id: str, episode: int, *, generation: str = ""
) -> str:
    suffix = f":{generation}" if generation else ""
    return (
        "hermes-session:"
        + str(uuid5(
            NAMESPACE_URL,
            f"supportportal:knowledge-review:{engineer_case_id}:{episode}{suffix}",
        ))
    )


def _summary_generation(summary_task_id: str) -> str:
    """The :g<hash> generation suffix of a summary task id ('' for the base)."""
    parts = str(summary_task_id or "").rsplit(":", 1)
    if len(parts) == 2 and parts[1].startswith("g") and len(parts[1]) > 1:
        return parts[1]
    return ""


def _pinned_agent_model() -> str | None:
    import os

    return str(os.getenv(AGENT_MODEL_ENV_NAME) or "").strip() or None


# --------------------------------------------------------------------- triggers


def summary_input_fingerprint(
    binding: dict[str, Any], *, source_versions: list[tuple[str, str, str]] | None = None
) -> str:
    """Deterministic fingerprint of the Summary's frozen input generation.

    Covers the case lineage the Summary would consume (episode, ledger
    revision, conversation version) plus every accepted knowledge-source
    version linked to the case: a duplicate notification or an unchanged
    ``solved -> closed`` transition reproduces the same fingerprint (task
    reuse), while late-arriving or updated substantive material changes it
    and earns a new Summary generation.
    """
    import hashlib

    parts = [
        f"episode={int(binding.get('episode') or 0)}",
        f"ledger={int(binding.get('current_ledger_revision') or 0)}",
        f"conversation={int(binding.get('conversation_version') or 0)}",
    ]
    for source_type, source_id, source_version in sorted(source_versions or []):
        parts.append(f"src={source_type}:{source_id}:{source_version}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _linked_source_versions(repository: Any, engineer_case_id: str) -> list[tuple[str, str, str]]:
    """Accepted knowledge-source versions linked to this case (best effort).

    A repository without the linkage surface simply contributes no source
    parts; lineage revisions still fingerprint the case-side input.
    """
    linker = getattr(repository, "list_knowledge_sources_for_case", None)
    if not callable(linker):
        return []
    try:
        rows = linker(engineer_case_id) or []
    except Exception:  # noqa: BLE001 - fingerprint input is best-effort additive
        return []
    versions: list[tuple[str, str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        versions.append((
            str(row.get("source_type") or ""),
            str(row.get("source_id") or ""),
            str(row.get("source_updated_at") or row.get("source_updated_at_epoch") or ""),
        ))
    return versions


def queue_hermes_summary_for_case(
    repository: Any, *, engineer_case_id: str, trigger: str,
    now_value: str | None = None,
) -> dict[str, Any] | None:
    """Create-or-reuse the episode's Summary task; returns None when inactive.

    Idempotency is per (engineer_case_id, episode, input_fingerprint):
    duplicate or out-of-order terminal events with unchanged input reuse the
    original task row; updated substantive material (a newer accepted source
    version, an advanced ledger) produces a new Summary generation with a
    versioned task id while the earlier generation keeps its own record.
    """
    if hermes_workflow_mode() != "real":
        return None
    binding = repository.get_hermes_case_binding(engineer_case_id)
    if not isinstance(binding, dict):
        return None
    now = now_value or _now_iso()
    episode = int(binding["episode"])
    fingerprint = summary_input_fingerprint(
        binding, source_versions=_linked_source_versions(repository, engineer_case_id)
    )
    latest = repository.latest_hermes_summary_task_for_case_episode(engineer_case_id, episode)
    if isinstance(latest, dict) and str(latest.get("input_fingerprint") or "") == fingerprint:
        # Identical frozen input (duplicate notification or solved->closed
        # with unchanged content): reuse the existing generation.
        return latest
    task_id = summary_task_id_for(engineer_case_id, episode)
    if isinstance(latest, dict):
        # Changed input after an earlier generation: version the new task so
        # both generations stay traceable; ensure() still collapses replays
        # of THIS fingerprint.
        task_id = f"{task_id}:g{str(fingerprint)[:12]}"
    payload = {
        "summary_task_id": task_id,
        "engineer_case_id": engineer_case_id,
        "client_ticket_id": str(binding["client_ticket_id"]),
        "investigation_id": str(binding["investigation_id"]),
        "episode": episode,
        "ledger_revision": int(binding["current_ledger_revision"]),
        "conversation_version": int(binding["conversation_version"]),
        "hermes_session_id": str(binding.get("hermes_session_id") or ""),
        "trigger": trigger,
        "idempotency_key": f"hmknow:{task_id}",
        "prompt_version": KNOWLEDGE_SUMMARY_PROMPT_KEY,
        "agent_model": _pinned_agent_model(),
        "reasoning_effort": KNOWLEDGE_SUMMARY_REASONING_EFFORT,
        "input_fingerprint": fingerprint,
        "created_at": now,
    }
    return repository.ensure_hermes_summary_task(payload)


def queue_hermes_summary_for_locally_resolved_ticket(
    repository: Any, *, client_ticket_id: str, now_value: str | None = None,
) -> dict[str, Any] | None:
    """Best-effort Summary trigger for the local ``resolved`` transition.

    The n8n Zendesk ``solved`` sync remains the primary trigger; this path
    covers locally resolved tickets whose sync event never arrives. Engineer
    cases that are already closed locally are still found via the ticket's
    full case list.
    """
    ticket = repository.get_ticket(client_ticket_id)
    if not isinstance(ticket, dict) or str(ticket.get("status") or "") != "resolved":
        return None
    cases = repository.list_ticket_engineer_cases(
        client_ticket_id, include_client_messages=False
    )
    for case in sorted(
        (case for case in cases if isinstance(case, dict)),
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    ):
        engineer_case_id = str(case.get("engineer_case_id") or "").strip()
        if not engineer_case_id:
            continue
        if isinstance(repository.get_hermes_case_binding(engineer_case_id), dict):
            return queue_hermes_summary_for_case(
                repository, engineer_case_id=engineer_case_id,
                trigger="local_resolved", now_value=now_value,
            )
    return None


# ---------------------------------------------------------------------- bundles


KNOWLEDGE_SLACK_HISTORY_LIMIT = 500


def _case_slack_thread(repository: Any, case_id: str) -> dict[str, Any]:
    """Complete engineer Slack thread history for the Summary input contract.

    The thread binding is the authoritative Slack lineage source — the
    engineer-case payload does not carry Slack ids. A missing binding means
    the case never had a thread (an empty projection is correct); any read
    failure or a per-case history overflow FAILS the summary visibly
    (`slack_history_unavailable` / `slack_history_truncated`) instead of
    summarizing without the investigation context.
    """
    try:
        binding = repository.get_engineer_slack_thread_binding(case_id, active_only=False)
        listing = repository.list_engineer_slack_events_for_case(
            case_id, limit=KNOWLEDGE_SLACK_HISTORY_LIMIT
        )
    except KnowledgeWorkflowError:
        raise
    except Exception as exc:  # noqa: BLE001 - partial Slack context must not silently pass
        raise KnowledgeWorkflowError(
            "slack_history_unavailable",
            f"engineer Slack history could not be read for {case_id}: {exc}",
        ) from exc
    if not isinstance(binding, dict):
        return {"slack_channel_id": "", "slack_thread_ts": "", "events": []}
    events = listing.get("events") if isinstance(listing, dict) else None
    if not isinstance(events, list):
        raise KnowledgeWorkflowError(
            "slack_history_unavailable",
            f"engineer Slack history listing is malformed for {case_id}",
        )
    if bool(listing.get("truncated")):
        raise KnowledgeWorkflowError(
            "slack_history_truncated",
            f"engineer Slack history for {case_id} exceeds "
            f"{KNOWLEDGE_SLACK_HISTORY_LIMIT} events; refusing a partial summary",
        )
    projection = [
        {
            "event_id": str(event.get("event_id") or ""),
            "event_type": str(event.get("event_type") or ""),
            "status": str(event.get("status") or ""),
            "payload": event.get("payload") or {},
            "created_at": str(event.get("created_at") or ""),
        }
        for event in events
        if isinstance(event, dict)
    ]
    return {
        "slack_channel_id": str(binding.get("slack_channel_id") or ""),
        "slack_thread_ts": str(binding.get("slack_thread_ts") or ""),
        "events": projection,
    }


def build_case_close_bundle(repository: Any, task: dict[str, Any]) -> dict[str, Any]:
    case_id = str(task["engineer_case_id"])
    binding = repository.get_hermes_case_binding(case_id) or {}
    ledger = repository.get_hermes_case_ledger(case_id) or {}
    engineer_case = repository.get_engineer_case(case_id, include_client_messages=True) or {}
    ticket = repository.get_ticket(str(task["client_ticket_id"])) or {}
    current_output_id = str(binding.get("current_output_id") or "")
    current_output = repository.get_hermes_output(current_output_id) if current_output_id else None
    slack_thread = _case_slack_thread(repository, case_id)
    # Linked source material is part of the Summary's frozen input (review
    # round 1, P1-4): the same latest-version set that feeds the input
    # fingerprint is delivered verbatim to the agent. A read failure here
    # fails the Summary visibly rather than summarizing without material.
    knowledge_sources = repository.list_knowledge_sources_for_case(case_id)
    source_projection = [
        {
            "intake_id": str(row.get("intake_id") or ""),
            "source_type": str(row.get("source_type") or ""),
            "source_id": str(row.get("source_id") or ""),
            "source_updated_at": str(row.get("source_updated_at") or ""),
            "task_id": str(row.get("task_id") or ""),
            "payload": row.get("payload") or {},
            "references": row.get("references") or {},
        }
        for row in knowledge_sources
        if isinstance(row, dict)
    ]
    return {
        "schema": "hermes-case-close-bundle-v2",
        "lineage": {
            "engineer_case_id": case_id,
            "client_ticket_id": str(task["client_ticket_id"]),
            "investigation_id": str(task["investigation_id"]),
            "episode": int(task["episode"]),
            "ledger_revision": int(task["ledger_revision"]),
            "conversation_version": int(task["conversation_version"]),
            "hermes_session_id": str(task["hermes_session_id"]),
            "zendesk_ticket_id": str(ticket.get("ticket_id") or task["client_ticket_id"]),
            # Slack lineage comes from the thread binding, the authoritative
            # source; the engineer-case payload does not carry these ids.
            "slack_channel_id": slack_thread["slack_channel_id"],
            "slack_thread_ts": slack_thread["slack_thread_ts"],
        },
        "ticket": {
            "ticket_id": str(ticket.get("ticket_id") or ""),
            "subject": str(ticket.get("subject") or ""),
            "status": str(ticket.get("status") or ""),
            # Full ticket message history (customer comments, AI replies,
            # Zendesk-linked external ids) — the summary input contract
            # requires the complete conversation, not just the ledger.
            "messages": ticket.get("messages") or [],
        },
        "ledger": ledger,
        "engineer_case": {
            "subject": str(engineer_case.get("subject") or ""),
            "status": str(engineer_case.get("status") or ""),
            "messages": engineer_case.get("messages") or [],
        },
        "slack_thread": slack_thread,
        "knowledge_sources": source_projection,
        "current_investigation_output": current_output,
        "authority_events": repository.list_hermes_authority_events(case_id),
    }


def _render_bundle(bundle: dict[str, Any]) -> str:
    rendered = json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if len(rendered) > KNOWLEDGE_BUNDLE_MAX_CHARS:
        raise KnowledgeWorkflowError(
            "knowledge_bundle_too_large",
            f"bundle of {len(rendered)} chars exceeds {KNOWLEDGE_BUNDLE_MAX_CHARS}",
        )
    return rendered


def _binding_lineage_matches(binding: dict[str, Any], task: dict[str, Any]) -> bool:
    return (
        int(binding["episode"]) == int(task["episode"])
        and int(binding["current_ledger_revision"]) == int(task["ledger_revision"])
        and int(binding["conversation_version"]) == int(task["conversation_version"])
    )


def _require_current_lineage(repository: Any, task: dict[str, Any]) -> dict[str, Any]:
    binding = repository.get_hermes_case_binding(str(task["engineer_case_id"]))
    if not isinstance(binding, dict):
        raise KnowledgeWorkflowError("unknown_hermes_case", "Hermes case binding is missing")
    if not _binding_lineage_matches(binding, task):
        raise KnowledgeWorkflowError(
            "stale_case_lineage",
            "case revision advanced while the knowledge task was pending",
        )
    # Frozen-input generation check (review round 2, R2-7): the task's
    # fingerprint covers the linked source versions too, so a source that
    # advanced between queueing and execution (or mid-run before the final
    # re-check) must fail visibly instead of silently summarizing newer
    # material under an older generation's identity. The queue side mints the
    # new generation; this row fails and stays inspectable.
    expected_fingerprint = str(task.get("input_fingerprint") or "").strip()
    if expected_fingerprint:
        current_fingerprint = summary_input_fingerprint(
            binding,
            source_versions=_linked_source_versions(repository, str(task["engineer_case_id"])),
        )
        if current_fingerprint != expected_fingerprint:
            raise KnowledgeWorkflowError(
                "source_input_diverged",
                "linked knowledge sources advanced beyond the task's frozen input fingerprint",
            )
    return binding


# ------------------------------------------------------------------ run helpers


def _summary_instructions() -> str:
    core = resolve_system_prompt(CORE_PROMPT_KEY, CORE_PROMPT_FALLBACK_TEXT)
    manual = resolve_system_prompt(KNOWLEDGE_SUMMARY_PROMPT_KEY, "")
    if not manual:
        raise KnowledgeWorkflowError("prompt_unresolved", "summary manual prompt is missing")
    return core + f"\n\n--- PHASE MANUAL ({KNOWLEDGE_SUMMARY_PROMPT_KEY}) ---\n{manual}"


def _review_instructions() -> str:
    manual = resolve_system_prompt(KNOWLEDGE_REVIEW_PROMPT_KEY, "")
    if not manual:
        raise KnowledgeWorkflowError("prompt_unresolved", "knowledge review manual prompt is missing")
    return manual


def _extract_run_json(output: Any) -> dict[str, Any]:
    text = str(output or "").strip()
    blocks = _JSON_BLOCK_RE.findall(text)
    if not blocks and text.startswith("{"):
        blocks = [text]
    for block in reversed(blocks):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise KnowledgeWorkflowError(
        "output_contract_invalid", "run output contains no JSON object"
    )


def _execute_knowledge_run(
    client: HermesAgentClient,
    *,
    run_id: str | None,
    session_id: str,
    instructions: str,
    input_text: str,
    idempotency_key: str,
    workspace_key: str,
    enabled_toolsets: list[str],
    model: str | None,
    reasoning_effort: str | None,
    timeout_seconds: float | None,
    poll_interval_seconds: float | None,
    sleeper: Any,
) -> dict[str, Any]:
    """Start (idempotently) and await one knowledge run; returns the terminal status."""
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        started = client.start_run(
            session_id=session_id,
            instructions=instructions,
            input_text=input_text,
            idempotency_key=idempotency_key,
            workspace_key=workspace_key,
            enabled_toolsets=enabled_toolsets,
            model=model,
            model_options=(
                {"reasoning_effort": reasoning_effort}
                if model and reasoning_effort
                else None
            ),
        )
        normalized_run_id = str(started["run_id"])
    deadline = time.monotonic() + (
        timeout_seconds
        if timeout_seconds is not None
        else client.settings.turn_timeout_seconds
    )
    interval = max(
        0.5,
        poll_interval_seconds
        if poll_interval_seconds is not None
        else client.settings.poll_interval_seconds,
    )
    while True:
        status = client.get_run(normalized_run_id)
        if str(status.get("status")) in TERMINAL_RUN_STATUSES:
            status["run_id"] = normalized_run_id
            return status
        if time.monotonic() >= deadline:
            raise HermesAgentError(
                "hermes_agent_turn_timeout",
                f"Hermes run {normalized_run_id} did not settle within budget",
                retryable=True,
            )
        sleeper(interval)


def _fail_task_safely(fail_call: Any, *, error_code: str, error_message: str) -> None:
    """Fail a claimed task; a conflict means it was invalidated or stolen mid-run."""
    try:
        fail_call(error_code=error_code, error_message=error_message)
    except HermesRepositoryConflict:
        LOGGER.info("knowledge task left running state before failure could be recorded")


# ------------------------------------------------------------------ Summary run


def run_hermes_summary_task(
    repository: Any,
    client: HermesAgentClient,
    *,
    task: dict[str, Any],
    sleeper: Any = time.sleep,
    poll_interval_seconds: float | None = None,
    timeout_seconds: float | None = None,
    now_value: str | None = None,
) -> dict[str, Any]:
    """Execute one claimed Summary task to a terminal task state."""
    summary_task_id = str(task["summary_task_id"])
    owner_token = str(task["owner_token"])
    now = now_value or _now_iso()

    def _fail(error_code: str, error_message: str) -> dict[str, Any]:
        _fail_task_safely(
            lambda **kwargs: repository.fail_hermes_summary_task(
                summary_task_id, owner_token=owner_token, failed_at=now, **kwargs
            ),
            error_code=error_code,
            error_message=error_message,
        )
        return {"summary_task_id": summary_task_id, "status": "failed", "error_code": error_code}

    try:
        _require_current_lineage(repository, task)
        instructions = _summary_instructions()
        input_text = _render_bundle(build_case_close_bundle(repository, task))
        status = _execute_knowledge_run(
            client,
            run_id=str(task.get("run_id") or "") or None,
            session_id=str(task["hermes_session_id"]),
            instructions=instructions,
            input_text=input_text,
            idempotency_key=str(task["idempotency_key"]),
            workspace_key=workspace_key_for("engineer", str(task["engineer_case_id"])),
            enabled_toolsets=list(KNOWLEDGE_SUMMARY_TOOLSETS),
            model=str(task.get("agent_model") or "") or None,
            reasoning_effort=str(task.get("reasoning_effort") or "") or None,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            sleeper=sleeper,
        )
        if str(task.get("run_id") or "") != str(status.get("run_id") or ""):
            repository.record_hermes_summary_run(
                summary_task_id, run_id=str(status["run_id"]), now_value=now
            )
        if str(status.get("status")) != "completed":
            return _fail(
                f"hermes_run_{status.get('status')}",
                f"summary run ended with {status.get('status')}",
            )
        parsed = _extract_run_json(status.get("output"))
        content = normalize_summary_output(parsed)
        packet_payload = {
            **content,
            "schema_version": "v1",
            "summary_id": f"hermes-summary:{task['engineer_case_id']}:{task['episode']}",
            "engineer_case_id": str(task["engineer_case_id"]),
            "client_ticket_id": str(task["client_ticket_id"]),
            "investigation_id": str(task["investigation_id"]),
            "episode": int(task["episode"]),
            "ledger_revision": int(task["ledger_revision"]),
            "conversation_version": int(task["conversation_version"]),
            "hermes_session_id": str(task["hermes_session_id"]),
            "trigger": str(task["trigger"]),
            "content_hash": "",
            "created_at": now,
        }
        packet_payload["content_hash"] = summary_packet_content_hash(packet_payload)
        packet = HermesSummaryPacket.model_validate(packet_payload)
        _require_current_lineage(repository, task)
        generation = _summary_generation(summary_task_id)
        review_payload = {
            "review_task_id": review_task_id_for(
                str(task["engineer_case_id"]), int(task["episode"]), generation=generation
            ),
            "summary_task_id": summary_task_id,
            "engineer_case_id": str(task["engineer_case_id"]),
            "client_ticket_id": str(task["client_ticket_id"]),
            "investigation_id": str(task["investigation_id"]),
            "episode": int(task["episode"]),
            "ledger_revision": int(task["ledger_revision"]),
            "conversation_version": int(task["conversation_version"]),
            # Review round 3, R3-6: the review carries its generation's frozen
            # fingerprint so the same source-divergence check covers it.
            "input_fingerprint": str(task.get("input_fingerprint") or ""),
            "review_session_id": review_session_id_for(
                str(task["engineer_case_id"]), int(task["episode"]), generation=generation
            ),
            "idempotency_key": (
                "hmknow:"
                + review_task_id_for(
                    str(task["engineer_case_id"]), int(task["episode"]), generation=generation
                )
            ),
            "prompt_version": KNOWLEDGE_REVIEW_PROMPT_KEY,
            "skill_version": HERMES_KNOWLEDGE_REVIEW_SKILL_VERSION,
            "agent_model": str(task.get("agent_model") or "") or None,
            "reasoning_effort": KNOWLEDGE_REVIEW_REASONING_EFFORT,
            "created_at": now,
        }
        try:
            repository.complete_hermes_summary_task(
                summary_task_id,
                owner_token=owner_token,
                packet=packet.model_dump(mode="json"),
                review_task=review_payload,
                completed_at=now,
            )
        except HermesRepositoryConflict:
            LOGGER.info(
                "summary task left running state before completion could be recorded "
                "summary_task_id=%s", summary_task_id,
            )
            return {"summary_task_id": summary_task_id, "status": "superseded_during_run"}
        LOGGER.info(
            "hermes_summary_completed summary_task_id=%s run_id=%s candidates=%s",
            summary_task_id, status.get("run_id"), len(packet.candidates),
        )
        return {"summary_task_id": summary_task_id, "status": "completed"}
    except HermesAgentError as exc:
        return _fail(str(exc.code), str(exc))
    except KnowledgeWorkflowError as exc:
        return _fail(exc.code, str(exc))
    except (ValueError, TypeError) as exc:
        return _fail("output_contract_invalid", str(exc))


# ------------------------------------------------------------------- Review run


WRITABLE_REVIEW_DECISIONS = frozenset({"new", "merge", "supplement", "replace"})


def _collect_weknora_evidence(
    weknora_client: HermesWeKnoraClient | None,
    packet: dict[str, Any],
    *,
    memory_client: Any = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], bool, bool]:
    """Per-candidate similarity evidence from BOTH WeKnora surfaces.

    ``knowledge`` results come from the read-only review client; ``memory``
    results come from the contract-pinned client's caller-isolated memory
    query (shared Hermes identity). Each surface reports its own
    availability; a candidate's writable decision is only trustworthy when
    every surface that could hold a duplicate answered.
    """
    knowledge_results: dict[str, list[dict[str, Any]]] = {}
    memory_results: dict[str, list[dict[str, Any]]] = {}
    candidates = packet.get("candidates") or []
    knowledge_ok = True
    if weknora_client is None or not weknora_client.configured():
        knowledge_ok = False
        for candidate in candidates:
            knowledge_results[str(candidate.get("candidate_id") or "")] = []
    else:
        read_full = getattr(weknora_client, "read", None)
        for candidate in candidates:
            candidate_id = str(candidate.get("candidate_id") or "")
            try:
                hits = weknora_client.search(str(candidate.get("statement") or ""))
                # Review round 2, R2-6: a snippet alone cannot ground a
                # supplement/merge/replace decision — every hit carries the
                # target's FULL body and its content version (the manual
                # metadata revision, not a chunk revision). A read failure
                # marks the surface unavailable so writable decisions fail
                # closed to human review.
                if callable(read_full):
                    enriched: list[dict[str, Any]] = []
                    for hit in hits:
                        entry = dict(hit)
                        full = read_full(str(hit.get("object_id") or ""))
                        entry["full_content"] = str(full.get("content") or "")
                        entry["content_version"] = str(full.get("content_version") or "")
                        entry["target_lineage"] = full.get("lineage") or {}
                        enriched.append(entry)
                    knowledge_results[candidate_id] = enriched
                else:
                    knowledge_results[candidate_id] = list(hits)
            except WeKnoraUnavailable as exc:
                LOGGER.warning(
                    "weknora_search_unavailable candidate_id=%s error=%s", candidate_id, exc
                )
                knowledge_results[candidate_id] = []
                knowledge_ok = False
    memory_ok = True
    # The current contract-pinned client exposes the official memory API as
    # ``memory_list`` (a list endpoint, not a search); older clients and test
    # fakes may still expose ``memory_query``. Either surface counts.
    memory_list = getattr(memory_client, "memory_list", None) if memory_client is not None else None
    memory_query = getattr(memory_client, "memory_query", None) if memory_client is not None else None
    memory_read = memory_list if callable(memory_list) else (
        memory_query if callable(memory_query) else None
    )
    memory_configured = (
        memory_read is not None
        and bool(getattr(memory_client, "is_configured", lambda: True)())
        and bool(getattr(memory_client, "has_memory_identity", lambda: True)())
    )
    if not memory_configured:
        memory_ok = False
        for candidate in candidates:
            memory_results[str(candidate.get("candidate_id") or "")] = []
    else:
        for candidate in candidates:
            candidate_id = str(candidate.get("candidate_id") or "")
            try:
                if memory_read is memory_list:
                    # One listing covers every candidate (list endpoint).
                    # Prefer the full pagination walk so evidence cannot miss
                    # a duplicate on a later page (a partial listing would
                    # let a `new` decision create a duplicate object).
                    if candidate_id == str((candidates[0] or {}).get("candidate_id") or ""):
                        list_all = getattr(memory_client, "memory_list_all", None)
                        listed = (
                            list(list_all()) if callable(list_all) else list(memory_list())
                        )
                        for other in candidates:
                            memory_results[str(other.get("candidate_id") or "")] = list(listed)
                else:
                    memory_results[candidate_id] = list(
                        memory_query(query=str(candidate.get("statement") or ""))
                    )
            except Exception as exc:  # noqa: BLE001 - any memory failure is unavailable evidence
                LOGGER.warning(
                    "weknora_memory_unavailable candidate_id=%s error=%s", candidate_id, exc
                )
                memory_results[candidate_id] = []
                memory_ok = False
    return knowledge_results, memory_results, knowledge_ok, memory_ok


def _downgrade_decisions_without_evidence(
    decisions: list[Any],
    *,
    knowledge_available: bool,
    memory_available: bool,
) -> tuple[list[Any], list[str]]:
    """Fail closed: a writable decision survives only when every surface that
    could hold a duplicate answered (knowledge candidates need the knowledge
    surface; memory candidates need both), and skill write proposals always
    become ``human_review`` — the original decision is preserved in the
    rationale and nothing is silently dropped or auto-written."""
    adjusted: list[Any] = []
    downgraded: list[str] = []
    for item in decisions:
        if not isinstance(item, dict):
            adjusted.append(item)
            continue
        decision = str(item.get("decision") or "")
        candidate_type = str(item.get("candidate_type") or "")
        skill_write = candidate_type == "skill" and decision in WRITABLE_REVIEW_DECISIONS
        evidence_ok = (
            knowledge_available
            if candidate_type == "knowledge"
            else (knowledge_available and memory_available)
        )
        unverified_write = decision in WRITABLE_REVIEW_DECISIONS and not evidence_ok
        if not skill_write and not unverified_write:
            adjusted.append(item)
            continue
        degraded = dict(item)
        degraded["decision"] = "human_review"
        reason = (
            "skill candidates are human-maintained and never auto-written"
            if skill_write
            else "WeKnora evidence unavailable; refusing an unverified write"
        )
        degraded["rationale"] = f"[downgraded from {decision}: {reason}] {item.get('rationale') or ''}".strip()
        degraded.pop("target_object", None)
        degraded.pop("target_version", None)
        adjusted.append(degraded)
        downgraded.append(str(item.get("candidate_id") or ""))
    return adjusted, downgraded


# ------------------------------------------------- consumption bridge (p2-182)


def _slack_thread_lineage(repository: Any, engineer_case_id: str) -> tuple[str | None, str | None]:
    binding = repository.get_engineer_slack_thread_binding(
        engineer_case_id, active_only=False
    )
    if not isinstance(binding, dict):
        return None, None
    return (
        str(binding.get("slack_channel_id") or "") or None,
        str(binding.get("slack_thread_ts") or "") or None,
    )


def build_weknora_promotions_from_review_report(
    report: dict[str, Any],
    *,
    summary_task: dict[str, Any],
    review_task: dict[str, Any],
    review_run_id: str | None,
    packet: dict[str, Any],
    slack_channel_id: str | None = None,
    slack_thread_ts: str | None = None,
) -> list[dict[str, Any]]:
    """Consumption bridge: one WeKnora promotion task per review decision.

    knowledge/memory decisions pass through unchanged (payloads validated
    against the adapter's candidate contract). Every skill decision is routed
    to an explicit human-review (or no-op) promotion record — skills are
    human-maintained and never written — with the original decision preserved
    in the payload for audit. Source identity is per-candidate and
    content-addressed by the report hash, so re-completing the same report
    enqueues idempotently while a changed report yields new rows.
    """
    statements = {
        str(item.get("candidate_id") or ""): str(item.get("statement") or "")
        for item in (packet.get("candidates") or [])
        if isinstance(item, dict)
    }
    lineage = {
        "engineer_case_id": str(report.get("engineer_case_id") or ""),
        "client_ticket_id": str(report.get("client_ticket_id") or ""),
        "investigation_id": str(report.get("investigation_id") or ""),
        "summary_session_id": str(summary_task.get("hermes_session_id") or ""),
        "summary_run_id": str(summary_task.get("run_id") or ""),
        "review_session_id": str(review_task.get("review_session_id") or ""),
        "review_run_id": str(review_run_id or ""),
        "slack_channel_id": str(slack_channel_id or ""),
        "slack_thread_ts": str(slack_thread_ts or ""),
        "source_type": "hermes_knowledge_review",
        "source_version": str(report.get("content_hash") or ""),
        # Review round 3, R3-6: promotions remember the frozen-input
        # generation they were produced from, so a human decision can be
        # refused when the case's inputs have moved on.
        "input_fingerprint": str(review_task.get("input_fingerprint") or ""),
    }
    tasks: list[dict[str, Any]] = []
    for decision in report.get("decisions") or []:
        if not isinstance(decision, dict):
            continue
        candidate_id = str(decision.get("candidate_id") or "")
        original_type = str(decision.get("candidate_type") or "")
        original_decision = str(decision.get("decision") or "")
        statement = statements.get(candidate_id, "")
        title = statement.splitlines()[0].strip()[:80] if statement.strip() else ""
        payload = {
            "schema_version": "v1",
            "candidate_type": original_type,
            "decision": original_decision,
            "title": title,
            "content": (
                "" if original_decision == "merge"
                else str(decision.get("proposed_content") or "")
            ),
            "merged_content": (
                str(decision.get("proposed_content") or "")
                if original_decision == "merge" else ""
            ),
            "target_object_id": str(decision.get("target_object") or ""),
            "base_version": str(decision.get("target_version") or ""),
            "kind": str(decision.get("kind") or ""),
            "importance": decision.get("importance"),
            "note": str(decision.get("rationale") or ""),
        }
        if original_type == "skill":
            routed_decision = "no_change" if original_decision == "no_change" else "human_review"
            payload["skill_proposal"] = True
        else:
            routed_decision = original_decision
            WeKnoraPromotionCandidate.model_validate(payload)
        tasks.append({
            **lineage,
            "source_id": f"{report.get('review_id')}:{candidate_id}",
            "candidate_type": original_type,
            "decision": routed_decision,
            "content_hash": _weknora_candidate_hash(payload),
            "candidate_payload": payload,
        })
    return tasks


def run_hermes_review_task(
    repository: Any,
    client: HermesAgentClient,
    *,
    task: dict[str, Any],
    weknora_client: HermesWeKnoraClient | None = None,
    memory_client: Any = None,
    sleeper: Any = time.sleep,
    poll_interval_seconds: float | None = None,
    timeout_seconds: float | None = None,
    now_value: str | None = None,
) -> dict[str, Any]:
    """Execute one claimed Review task to a terminal task state."""
    review_task_id = str(task["review_task_id"])
    owner_token = str(task["owner_token"])
    now = now_value or _now_iso()

    def _fail(error_code: str, error_message: str) -> dict[str, Any]:
        _fail_task_safely(
            lambda **kwargs: repository.fail_hermes_review_task(
                review_task_id, owner_token=owner_token, failed_at=now, **kwargs
            ),
            error_code=error_code,
            error_message=error_message,
        )
        return {"review_task_id": review_task_id, "status": "failed", "error_code": error_code}

    summary_task = repository.get_hermes_summary_task(str(task["summary_task_id"]))
    packet = (summary_task or {}).get("packet") if isinstance(summary_task, dict) else None
    if not isinstance(packet, dict):
        return _fail("summary_packet_missing", "completed summary packet is required")

    try:
        _require_current_lineage(repository, task)
        instructions = _review_instructions()
        weknora_results, memory_results, knowledge_ok, memory_ok = _collect_weknora_evidence(
            weknora_client, packet, memory_client=memory_client
        )
        try:
            repository.record_hermes_review_weknora_context(
                review_task_id,
                weknora_available=knowledge_ok and memory_ok,
                weknora_query=(
                    "; ".join(
                        str(item.get("statement") or "")
                        for item in (packet.get("candidates") or [])
                    )
                    or None
                ),
                now_value=now,
            )
        except HermesRepositoryConflict:
            return {"review_task_id": review_task_id, "status": "superseded_during_run"}
        bundle = {
            "schema": "hermes-knowledge-review-bundle-v1",
            "lineage": {
                "engineer_case_id": str(task["engineer_case_id"]),
                "client_ticket_id": str(task["client_ticket_id"]),
                "investigation_id": str(task["investigation_id"]),
                "episode": int(task["episode"]),
                "ledger_revision": int(task["ledger_revision"]),
                "conversation_version": int(task["conversation_version"]),
                "summary_id": str(packet.get("summary_id") or ""),
                "review_session_id": str(task["review_session_id"]),
                "skill_version": HERMES_KNOWLEDGE_REVIEW_SKILL_VERSION,
            },
            "summary_packet": packet,
            "weknora": {
                # Per-surface availability: a writable decision is only
                # trustworthy when every surface that could hold a duplicate
                # answered; the bundle exposes both flags explicitly.
                "available": knowledge_ok,
                "memory_available": memory_ok,
                "results": weknora_results,
                "memory_results": memory_results,
            },
        }
        status = _execute_knowledge_run(
            client,
            run_id=str(task.get("run_id") or "") or None,
            session_id=str(task["review_session_id"]),
            instructions=instructions,
            input_text=_render_bundle(bundle),
            idempotency_key=str(task["idempotency_key"]),
            workspace_key=workspace_key_for("knowledge-review", str(task["engineer_case_id"])),
            enabled_toolsets=list(KNOWLEDGE_REVIEW_TOOLSETS),
            model=str(task.get("agent_model") or "") or None,
            reasoning_effort=str(task.get("reasoning_effort") or "") or None,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            sleeper=sleeper,
        )
        if str(task.get("run_id") or "") != str(status.get("run_id") or ""):
            repository.record_hermes_review_run(
                review_task_id, run_id=str(status["run_id"]), now_value=now
            )
        if str(status.get("status")) != "completed":
            return _fail(
                f"hermes_run_{status.get('status')}",
                f"review run ended with {status.get('status')}",
            )
        parsed = _extract_run_json(status.get("output"))
        decisions = parsed.get("decisions")
        if not isinstance(decisions, list):
            return _fail("output_contract_invalid", "run output decisions is not a list")
        packet_candidate_ids = {
            str(item.get("candidate_id") or "")
            for item in (packet.get("candidates") or [])
        }
        decision_ids = {
            str(item.get("candidate_id") or "") for item in decisions if isinstance(item, dict)
        }
        if decision_ids != packet_candidate_ids or len(decisions) != len(decision_ids):
            return _fail(
                "review_coverage_invalid",
                "review decisions must cover each summary candidate exactly once",
            )
        decisions, downgraded_ids = _downgrade_decisions_without_evidence(
            decisions,
            knowledge_available=knowledge_ok,
            memory_available=memory_ok,
        )
        if downgraded_ids:
            LOGGER.warning(
                "review_decisions_downgraded review_task_id=%s knowledge_available=%s "
                "memory_available=%s candidate_ids=%s",
                review_task_id, knowledge_ok, memory_ok, downgraded_ids,
            )
        report_payload = {
            "schema_version": "v1",
            "review_id": f"hermes-review:{task['engineer_case_id']}:{task['episode']}",
            "summary_id": str(packet.get("summary_id") or ""),
            "engineer_case_id": str(task["engineer_case_id"]),
            "client_ticket_id": str(task["client_ticket_id"]),
            "investigation_id": str(task["investigation_id"]),
            "episode": int(task["episode"]),
            "ledger_revision": int(task["ledger_revision"]),
            "conversation_version": int(task["conversation_version"]),
            "review_session_id": str(task["review_session_id"]),
            "weknora_available": knowledge_ok,
            "memory_available": memory_ok,
            "decisions": decisions,
            "downgraded_candidate_ids": downgraded_ids,
            "content_hash": "",
            "created_at": now,
        }
        report_payload["content_hash"] = review_report_content_hash(report_payload)
        report = HermesReviewReport.model_validate(report_payload)
        _require_current_lineage(repository, task)
        slack_channel_id, slack_thread_ts = _slack_thread_lineage(
            repository, str(task["engineer_case_id"])
        )
        report_payload_json = report.model_dump(mode="json")
        promotions = build_weknora_promotions_from_review_report(
            report_payload_json,
            summary_task=summary_task,
            review_task=task,
            review_run_id=str(status.get("run_id") or ""),
            packet=packet,
            slack_channel_id=slack_channel_id,
            slack_thread_ts=slack_thread_ts,
        )
        submissions = build_weknora_submissions(
            report_payload_json,
            lineage_extras={
                "investigation_id": str(task["investigation_id"]),
                "summary_session_id": str(summary_task.get("hermes_session_id") or ""),
                "summary_run_id": str(summary_task.get("run_id") or ""),
                "review_session_id": str(task["review_session_id"]),
                "review_run_id": str(status.get("run_id") or ""),
                "slack_channel_id": slack_channel_id or "",
                "slack_thread_ts": slack_thread_ts or "",
            },
        )
        try:
            repository.complete_hermes_review_task(
                review_task_id,
                owner_token=owner_token,
                report=report_payload_json,
                weknora_adapter_status="recorded",
                weknora_submissions=submissions,
                weknora_promotions=promotions,
                completed_at=now,
            )
        except HermesRepositoryConflict:
            LOGGER.info(
                "review task left running state before completion could be recorded "
                "review_task_id=%s", review_task_id,
            )
            return {"review_task_id": review_task_id, "status": "superseded_during_run"}
        LOGGER.info(
            "hermes_review_completed review_task_id=%s run_id=%s decisions=%s "
            "knowledge_available=%s memory_available=%s downgraded=%s weknora_promotions=%s",
            review_task_id, status.get("run_id"), len(report.decisions),
            knowledge_ok, memory_ok, len(report.downgraded_candidate_ids), len(promotions),
        )
        return {"review_task_id": review_task_id, "status": "completed"}
    except HermesAgentError as exc:
        return _fail(str(exc.code), str(exc))
    except KnowledgeWorkflowError as exc:
        return _fail(exc.code, str(exc))
    except (ValueError, TypeError) as exc:
        return _fail("output_contract_invalid", str(exc))


# ----------------------------------------------------------------------- drain


def _claimable(task: dict[str, Any], now_iso: str) -> bool:
    status = str(task.get("status") or "")
    if status == "pending":
        return True
    if status == "running":
        return str(task.get("lease_expires_at") or "") <= now_iso
    return False


def drain_hermes_knowledge_tasks(
    repository: Any,
    *,
    client: HermesAgentClient | None = None,
    weknora_client: HermesWeKnoraClient | None = None,
    memory_client: Any = None,
    limit: int = 5,
    now_value: str | None = None,
    sleeper: Any = time.sleep,
) -> int:
    """Claim and run pending summary then review tasks; returns tasks processed."""
    if not knowledge_workflow_active():
        return 0
    hermes_client = client or HermesAgentClient()
    weknora = weknora_client if weknora_client is not None else HermesWeKnoraClient()
    processed = 0
    now = now_value or _now_iso()
    from datetime import datetime, timedelta, timezone

    now_dt = datetime.now(timezone.utc) if now_value is None else datetime.fromisoformat(now_value)
    lease_expires = (now_dt + timedelta(seconds=KNOWLEDGE_TASK_LEASE_SECONDS)).isoformat()

    for task in repository.list_hermes_summary_tasks():
        if processed >= limit or not _claimable(task, now):
            continue
        claimed = repository.claim_hermes_summary_task(
            str(task["summary_task_id"]),
            owner_token=f"knowledge-worker:{id(hermes_client)}",
            claimed_at=now,
            lease_expires_at=lease_expires,
        )
        if not claimed:
            continue
        try:
            run_hermes_summary_task(
                repository, hermes_client, task=claimed, sleeper=sleeper, now_value=now
            )
        except Exception:  # noqa: BLE001 - one broken task must not stop the drain
            LOGGER.warning(
                "hermes_summary_task_crashed summary_task_id=%s",
                task["summary_task_id"], exc_info=True,
            )
        processed += 1

    for task in repository.list_hermes_review_tasks():
        if processed >= limit or not _claimable(task, now):
            continue
        claimed = repository.claim_hermes_review_task(
            str(task["review_task_id"]),
            owner_token=f"knowledge-worker:{id(hermes_client)}",
            claimed_at=now,
            lease_expires_at=lease_expires,
        )
        if not claimed:
            continue
        try:
            run_hermes_review_task(
                repository, hermes_client, task=claimed,
                weknora_client=weknora, memory_client=memory_client,
                sleeper=sleeper, now_value=now,
            )
        except Exception:  # noqa: BLE001
            LOGGER.warning(
                "hermes_review_task_crashed review_task_id=%s",
                task["review_task_id"], exc_info=True,
            )
        processed += 1
    return processed
