"""Standalone knowledge-source Summary/Review (governance plan WP1).

Sources without a bound Hermes Case — CSD issues and article snapshots —
still enter the SAME governance pipeline: a fresh Summary session per
(source, version) freezes the raw snapshot, a separate Review session
judges the candidates, and the review report feeds the existing WeKnora
promotion consumption bridge. There is no case lineage to gate on; the
source version IS the generation, so dedup is per (source identity,
version): a duplicate delivery reuses the task, a newer version earns a
new one.

The case-bound path (hermes_knowledge_workflow) remains the primary
pipeline; this module is its case-less sibling and deliberately reuses the
same prompts, run-execution shape, review downgrade rules, and promotion
bridge.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from backend.services.hermes_agent_runtime import (
    TERMINAL_RUN_STATUSES,
    HermesAgentClient,
    HermesAgentError,
)
from backend.services.prompt_runtime import resolve_system_prompt

LOGGER = logging.getLogger("supportportal.knowledge_standalone_workflow")

STANDALONE_SUMMARY_PROMPT_KEY = "hermes-case-summary-manual"
STANDALONE_REVIEW_PROMPT_KEY = "hermes-knowledge-review-manual"
STANDALONE_RUN_TIMEOUT_SECONDS = 900.0
STANDALONE_POLL_INTERVAL_SECONDS = 2.0
STANDALONE_BUNDLE_MAX_CHARS = 200_000
STANDALONE_LEASE_SECONDS = 900.0
# Restricted surfaces matching the case-bound roles (review P1-10): the
# Summary gets read-only case context; the Review gets the skill library
# only. No memory, publication, or case-write toolsets ever load here.
STANDALONE_SUMMARY_TOOLSETS = ["common"]
STANDALONE_REVIEW_TOOLSETS = ["skills"]

CORE_PROMPT_KEY = "hermes-support-agent-system"
CORE_PROMPT_FALLBACK_TEXT = (
    "You are the Agora support knowledge agent. The run input is one raw "
    "source snapshot. Follow the phase instructions. Every conclusion must "
    "be grounded in the snapshot content; never invent facts."
)


class StandaloneKnowledgeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def standalone_workflow_active() -> bool:
    from backend.services.hermes_knowledge_workflow import knowledge_workflow_active

    return knowledge_workflow_active()


def standalone_summary_task_id_for(source_type: str, source_id: str, source_version: str) -> str:
    return f"knowledge-source-summary:{source_type}:{source_id}:{source_version}"


def standalone_session_id_for(kind: str, source_type: str, source_id: str, source_version: str) -> str:
    return (
        "hermes-session:"
        + str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"supportportal:knowledge-standalone:{kind}:{source_type}:{source_id}:{source_version}",
        ))
    )


def queue_standalone_summary_for_source(
    repository: Any, *, intake: dict[str, Any], now_value: str | None = None,
    case_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Create-or-reuse the standalone Summary task for one accepted source.

    ``intake`` is the accepted knowledge-source row (source_type, source_id,
    source_updated_at as the version). Dedup is per (source identity,
    version): duplicate deliveries reuse the task; a newer accepted version
    is a new generation by construction (different task id).
    """
    if not standalone_workflow_active():
        return None
    from datetime import datetime, timezone

    source_type = str(intake.get("source_type") or "")
    source_id = str(intake.get("source_id") or "")
    source_version = str(intake.get("source_updated_at") or "")
    if not source_type or not source_id or not source_version:
        return None
    task_id = standalone_summary_task_id_for(source_type, source_id, source_version)
    now = now_value or datetime.now(timezone.utc).isoformat()
    payload = {
        "summary_task_id": task_id,
        "intake_id": str(intake.get("intake_id") or ""),
        "source_type": source_type,
        "source_id": source_id,
        "source_version": source_version,
        "summary_session_id": standalone_session_id_for(
            "summary", source_type, source_id, source_version
        ),
        "status": "pending",
        "idempotency_key": f"hmknow-standalone:{task_id}",
        "prompt_version": STANDALONE_SUMMARY_PROMPT_KEY,
        # Review round 3, R3-5: the native-case investigation context (binding
        # lineage, engineer turns, timeline) is frozen onto the task row so
        # the Summary sees the engineer conversation, not just the snapshot.
        "context_snapshot": case_context or None,
        "created_at": now,
        "updated_at": now,
    }
    return repository.ensure_standalone_summary_task(payload)


def build_standalone_bundle(
    intake: dict[str, Any], *, case_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze the raw snapshot (and, for native cases, the investigation
    context) into the Summary input bundle."""
    bundle = {
        "schema": "knowledge-source-bundle-v1",
        "source": {
            "source_type": str(intake.get("source_type") or ""),
            "source_id": str(intake.get("source_id") or ""),
            "source_updated_at": str(intake.get("source_updated_at") or ""),
            "references": dict(intake.get("references_payload") or intake.get("references") or {}),
        },
        "payload": dict(intake.get("payload") or {}),
    }
    if isinstance(case_snapshot, dict) and case_snapshot:
        # Review round 3, R3-5: the native case's engineer conversation and
        # lineage travel with the frozen input.
        bundle["case_context"] = case_snapshot
    return bundle


def _instructions(prompt_key: str, *, core: bool) -> str:
    manual = resolve_system_prompt(prompt_key, "")
    if not manual:
        raise StandaloneKnowledgeError("prompt_unresolved", f"{prompt_key} is missing")
    text = ""
    if core:
        text = resolve_system_prompt(CORE_PROMPT_KEY, CORE_PROMPT_FALLBACK_TEXT) + "\n\n"
    return f"{text}--- PHASE MANUAL ({prompt_key}) ---\n{manual}"


def _extract_run_json(output: Any) -> dict[str, Any]:
    import re

    text = str(output or "").strip()
    blocks = re.findall(r"```json\s*(.*?)\s*```", text, flags=re.DOTALL)
    if not blocks and text.startswith("{"):
        blocks = [text]
    for block in reversed(blocks):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise StandaloneKnowledgeError(
        "output_contract_invalid", "run output contains no JSON object"
    )


def _run_session(
    client: HermesAgentClient,
    *,
    session_id: str,
    instructions: str,
    input_text: str,
    idempotency_key: str,
    workspace_key: str,
    toolsets: list[str] | None = None,
) -> dict[str, Any]:
    started = client.start_run(
        session_id=session_id,
        instructions=instructions,
        input_text=input_text,
        idempotency_key=idempotency_key,
        workspace_key=workspace_key,
        enabled_toolsets=toolsets,
    )
    run_id = str(started.get("run_id") or "")
    deadline = time.monotonic() + STANDALONE_RUN_TIMEOUT_SECONDS
    while True:
        status = client.get_run(run_id)
        run_status = str(status.get("status"))
        if run_status in TERMINAL_RUN_STATUSES:
            if run_status == "completed":
                return {"run_id": run_id, "output": status.get("output")}
            raise StandaloneKnowledgeError(
                "run_failed", f"standalone run ended {run_status}"
            )
        if time.monotonic() >= deadline:
            raise StandaloneKnowledgeError("run_timeout", "standalone run did not settle")
        time.sleep(STANDALONE_POLL_INTERVAL_SECONDS)


def _require_standalone_generation(repository: Any, task: dict[str, Any]) -> None:
    """Review round 3, R3-6: a standalone task may only execute while its
    frozen source version is still the newest ACCEPTED generation — a newer
    version means a fresh task exists (or will be queued) for that material
    and this generation must fail visibly instead."""
    latest = repository.latest_knowledge_source_version(
        str(task.get("source_type") or ""), str(task.get("source_id") or "")
    )
    if latest and latest != str(task.get("source_version") or ""):
        raise StandaloneKnowledgeError(
            "source_input_diverged",
            f"source advanced to {latest} while this task froze {task.get('source_version')}",
        )


def run_standalone_summary_task(
    repository: Any,
    task: dict[str, Any],
    *,
    client: HermesAgentClient,
    intake: dict[str, Any] | None = None,
) -> dict[str, Any]:
    owner_token = str(task.get("owner_token") or "")
    """Execute one standalone Summary; persist the packet + review task.

    ``intake`` overrides the stored row (tests and the drain loop may pass
    the frozen snapshot directly); without it the stored intake row must
    exist."""
    if intake is None:
        intake = repository.get_knowledge_source(str(task.get("intake_id") or ""))
    if not isinstance(intake, dict):
        raise StandaloneKnowledgeError("intake_missing", "source intake row disappeared")
    _require_standalone_generation(repository, task)
    bundle = build_standalone_bundle(
        intake, case_snapshot=task.get("context_snapshot")
        if isinstance(task.get("context_snapshot"), dict) else None,
    )
    rendered = json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if len(rendered) > STANDALONE_BUNDLE_MAX_CHARS:
        raise StandaloneKnowledgeError("bundle_too_large", "source snapshot exceeds budget")
    outcome = _run_session(
        client,
        session_id=str(task["summary_session_id"]),
        instructions=_instructions(STANDALONE_SUMMARY_PROMPT_KEY, core=True),
        input_text=rendered,
        idempotency_key=f"{task['idempotency_key']}:run",
        workspace_key=f"supportportal_knowledge_standalone_{task['source_id']}".lower()[:128],
        toolsets=STANDALONE_SUMMARY_TOOLSETS,
    )
    # Same output content contract as the case-bound Summary (review round 2,
    # R2-9): a malformed payload fails the task instead of entering the
    # pipeline as-is.
    from backend.services.hermes_knowledge_workflow import normalize_summary_output

    packet = {
        **normalize_summary_output(_extract_run_json(outcome.get("output"))),
        "schema_version": "v1",
        "summary_id": str(task["summary_task_id"]),
    }
    return repository.complete_standalone_summary_task(
        str(task["summary_task_id"]),
        packet=packet,
        run_id=str(outcome.get("run_id") or ""),
        owner_token=owner_token,
        review_task={
            "review_task_id": f"{task['summary_task_id']}:review",
            "summary_task_id": str(task["summary_task_id"]),
            "source_type": str(task["source_type"]),
            "source_id": str(task["source_id"]),
            "source_version": str(task["source_version"]),
            "review_session_id": standalone_session_id_for(
                "review", str(task["source_type"]), str(task["source_id"]), str(task["source_version"])
            ),
            "status": "pending",
            "idempotency_key": f"hmknow-standalone:{task['summary_task_id']}:review",
            "prompt_version": STANDALONE_REVIEW_PROMPT_KEY,
            "created_at": task.get("updated_at") or task.get("created_at"),
        },
    )


def run_standalone_review_task(
    repository: Any,
    task: dict[str, Any],
    *,
    client: HermesAgentClient,
    weknora_client: Any = None,
    memory_client: Any = None,
) -> dict[str, Any]:
    owner_token = str(task.get("owner_token") or "")
    """Execute one standalone Review; feed the standard promotion bridge."""
    from backend.services.hermes_knowledge_workflow import (
        _collect_weknora_evidence,
        _downgrade_decisions_without_evidence,
        build_weknora_promotions_from_review_report,
    )
    from backend.services.hermes_weknora import HermesWeKnoraClient

    summary = repository.get_standalone_summary_task(str(task["summary_task_id"]))
    if not isinstance(summary, dict) or not isinstance(summary.get("packet"), dict):
        raise StandaloneKnowledgeError("summary_packet_missing", "review without a summary packet")
    # Server-side lineage validation (review round 2, R2-9): the review may
    # only consume the summary generation queued for THIS source version.
    if str(summary.get("status") or "") != "completed":
        raise StandaloneKnowledgeError("summary_not_completed", "review on an unfinished summary")
    for lineage_field in ("source_type", "source_id", "source_version"):
        if str(summary.get(lineage_field) or "") != str(task.get(lineage_field) or ""):
            raise StandaloneKnowledgeError(
                "standalone_lineage_mismatch",
                f"{lineage_field} diverged between the summary row and the review task",
            )
    _require_standalone_generation(repository, task)
    packet = dict(summary["packet"])
    candidates = packet.get("candidates") or []
    knowledge_client = weknora_client
    if knowledge_client is None:
        try:
            knowledge_client = HermesWeKnoraClient()
        except Exception:  # noqa: BLE001 - unconfigured evidence surface
            knowledge_client = None
    knowledge_results, memory_results, knowledge_ok, memory_ok = _collect_weknora_evidence(
        knowledge_client, packet, memory_client=memory_client
    )
    bundle = {
        "schema": "knowledge-review-bundle-v1",
        "source": {
            "source_type": str(task["source_type"]),
            "source_id": str(task["source_id"]),
            "source_version": str(task["source_version"]),
        },
        "summary_packet": packet,
        "weknora": {
            "knowledge": {k: v for k, v in knowledge_results.items()},
            "memory": {k: v for k, v in memory_results.items()},
            "knowledge_available": knowledge_ok,
            "memory_available": memory_ok,
        },
    }
    outcome = _run_session(
        client,
        session_id=str(task["review_session_id"]),
        instructions=_instructions(STANDALONE_REVIEW_PROMPT_KEY, core=False),
        input_text=json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        idempotency_key=f"{task['idempotency_key']}:run",
        workspace_key=f"supportportal_knowledge_standalone_review_{task['source_id']}".lower()[:128],
        toolsets=STANDALONE_REVIEW_TOOLSETS,
    )
    report = _extract_run_json(outcome.get("output"))
    decisions = report.get("decisions")
    # Structure is mandatory; an EMPTY decision list is legal only when the
    # summary itself had zero candidates (empty-vs-empty, review round 2
    # R2-9) — the coverage check below rejects empty-vs-nonempty.
    if not isinstance(decisions, list):
        raise StandaloneKnowledgeError("review_coverage_invalid", "report decisions must be a list")
    # Candidate coverage: every summary candidate decided exactly once, no
    # invented candidates (same contract as the case-bound review).
    expected_ids = {
        str(item.get("candidate_id") or "")
        for item in candidates if isinstance(item, dict)
    }
    seen_ids: list[str] = []
    for decision in decisions:
        if isinstance(decision, dict):
            seen_ids.append(str(decision.get("candidate_id") or ""))
    if sorted(seen_ids) != sorted(expected_ids):
        raise StandaloneKnowledgeError(
            "review_coverage_invalid",
            f"decisions {sorted(seen_ids)} do not cover candidates {sorted(expected_ids)} exactly once",
        )
    adjusted, _downgraded = _downgrade_decisions_without_evidence(
        decisions, knowledge_available=knowledge_ok, memory_available=memory_ok
    )
    # Same per-decision output contract as the case-bound review (review
    # round 3, R3-8): a structurally invalid decision (for example a
    # no_change without the target it decided not to change) must fail the
    # task instead of ending the candidate as a bogus `accepted`.
    from backend.services.hermes_case_workflow import HermesReviewDecision

    for decision in adjusted:
        try:
            HermesReviewDecision.model_validate(decision)
        except Exception as exc:  # noqa: BLE001 - surface the contract violation
            raise StandaloneKnowledgeError(
                "review_output_invalid",
                f"decision {decision.get('candidate_id')!r} violates the review contract: {exc}",
            ) from exc
    report["decisions"] = adjusted
    # Review round 4, R4-2: re-check the generation AFTER the Hermes run — a
    # source that advanced mid-run must not complete an old-generation review
    # (the pre-run check alone leaves the window open).
    _require_standalone_generation(repository, task)
    promotions = build_weknora_promotions_from_review_report(
        report,
        # The bridge reads hermes_session_id/run_id off the summary task;
        # alias the standalone session field into that name.
        summary_task={**summary, "hermes_session_id": summary.get("summary_session_id")},
        review_task=dict(task),
        review_run_id=str(outcome.get("run_id") or ""),
        packet=packet,
    )
    # Native-case lineage (review round 3, R3-5): the Slack thread binding
    # captured at queue time reaches the promotion rows.
    context = summary.get("context_snapshot")
    context = context if isinstance(context, dict) else {}
    native_binding = context.get("binding")
    native_binding = native_binding if isinstance(native_binding, dict) else {}
    if native_binding:
        for promotion in promotions:
            promotion["slack_channel_id"] = str(native_binding.get("slack_channel_id") or "") or None
            promotion["slack_thread_ts"] = str(native_binding.get("slack_thread_ts") or "") or None
    # Rewrite the case-flavored lineage to standalone lineage before enqueue.
    for promotion in promotions:
        promotion["source_type"] = "knowledge_source_review"
        promotion["source_id"] = (
            f"{task['summary_task_id']}:{promotion.get('candidate_id')}"
        )
        promotion["source_version"] = str(
            summary.get("packet_hash") or task["source_version"]
        )
        # Review round 3, R3-6: standalone promotions carry the source
        # version they were produced from, so decisions can be generation-
        # checked against the newest accepted intake.
        promotion["input_fingerprint"] = str(task["source_version"])
    return repository.complete_standalone_review_task(
        str(task["review_task_id"]),
        report=report,
        run_id=str(outcome.get("run_id") or ""),
        promotions=promotions,
        owner_token=owner_token,
    )


def _task_intake(repository: Any, task: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort frozen intake for a claimed task (None = read the row)."""
    intake_id = str(task.get("intake_id") or "")
    if not intake_id:
        return None
    try:
        row = repository.get_knowledge_source(intake_id)
    except Exception:  # noqa: BLE001 - fall back to the row read in the runner
        return None
    return row if isinstance(row, dict) else None


def drain_standalone_knowledge_tasks(
    repository: Any,
    *,
    client: HermesAgentClient,
    weknora_client: Any = None,
    memory_client: Any = None,
    limit: int = 5,
) -> dict[str, int]:
    """Claim and run pending/lease-expired standalone tasks (worker entry)."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    executed = 0
    failed = 0
    for task in repository.claim_standalone_summary_tasks(limit=limit, now_value=now):
        try:
            run_standalone_summary_task(repository, task, client=client, intake=_task_intake(repository, task))
            executed += 1
        except Exception as exc:  # noqa: BLE001 - record and continue
            failed += 1
            LOGGER.exception(
                "standalone_summary_failed task=%s", task.get("summary_task_id")
            )
            try:
                repository.fail_standalone_summary_task(
                    str(task["summary_task_id"]), error=str(exc)[:500],
                    owner_token=str(task.get("owner_token") or ""),
                )
            except Exception:
                pass
    for task in repository.claim_standalone_review_tasks(limit=limit, now_value=now):
        try:
            run_standalone_review_task(
                repository, task, client=client,
                weknora_client=weknora_client, memory_client=memory_client,
            )
            executed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            LOGGER.exception(
                "standalone_review_failed task=%s", task.get("review_task_id")
            )
            try:
                repository.fail_standalone_review_task(
                    str(task["review_task_id"]), error=str(exc)[:500],
                    owner_token=str(task.get("owner_token") or ""),
                )
            except Exception:
                pass
    return {"executed": executed, "failed": failed}
