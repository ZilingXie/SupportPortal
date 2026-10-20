"""Sectioned Investigation Slack delivery with per-shard ledger receipts (p2-207).

The Investigation result is no longer squeezed into one 2000-character
message: Summary / Evidence / Blockers / Next steps are each posted as their
own Slack message in the case's bound thread, in a fixed order, with every
business truncation removed. Oversized sections split into ordered shards at
paragraph/list-item/line boundaries with code fences closed and reopened —
no characters are dropped and nothing is marked with an ellipsis. The review
action button (Prepare draft / Approve & send) is a SEPARATE final message,
sent only after every body message has a confirmed receipt.

Delivery accounting reuses the existing native-notification ledger
(enqueue/claim/finish on the idempotency store): each shard gets a stable
key (ticket/turn/section/index) so a retry of the SAME frozen message list
skips confirmed shards, continues from explicit refusals, and stops on
outcome_unknown without ever resending an ambiguous POST. The action button
never appears while any body shard is unconfirmed.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from backend.services.engineer_slack import (
    HERMES_ADHOC_RESULT_EVENT_TYPE,
    HERMES_INVESTIGATION_RESULT_EVENT_TYPE,
    EngineerSlackDeliveryError,
    engineer_slack_configured,
    post_engineer_slack_event,
)

__all__ = [
    "SECTION_ORDER",
    "SECTION_LABELS",
    "SectionMessage",
    "build_investigation_section_messages",
    "deliver_investigation_sections",
    "render_evidence_item",
]

LOGGER = logging.getLogger(__name__)

SECTION_ORDER = ("summary", "evidence", "blockers", "next_steps")
SECTION_LABELS = {
    "summary": "Summary",
    "evidence": "Evidence",
    "blockers": "Blockers",
    "next_steps": "Next steps",
}
# Slack mrkdwn block text tops out at 3000 characters; leave headroom for the
# per-message header line. This is a TECHNICAL capacity, not a business cap.
_SHARD_MAX_CHARS = 2800
_CODE_FENCE = "```"

# Evidence keys rendered with semantic labels, in display order. Keys are
# matched case-insensitively so server-side naming drift still renders.
_EVIDENCE_KEY_LABELS = (
    ("fact", "fact"),
    ("finding", "finding"),
    ("query", "query"),
    ("result", "result"),
    ("results", "results"),
    ("event_refs", "event refs"),
    ("events", "events"),
    ("limitation", "limitation"),
    ("limitations", "limitations"),
    ("coverage", "coverage"),
    ("error", "error"),
    ("reference", "reference"),
    ("source", "source"),
    ("detail", "detail"),
    ("summary", "summary"),
)
_SCALAR_TYPES = (str, int, float, bool)


def _source_truncated(value: Any) -> bool:
    """True when a string carries an explicit upstream truncation marker."""
    if not isinstance(value, str):
        return False
    return "[truncated" in value or value.rstrip().endswith("…")


def _render_scalar(value: Any) -> str:
    if isinstance(value, _SCALAR_TYPES):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:  # noqa: BLE001 - non-serializable structures stay visible
        return str(value)


def render_evidence_item(item: Any) -> str:
    """Render ONE evidence entry fully and diagnostically.

    Known semantic keys render as labeled lines (fact / query / result /
    event refs / limitation / coverage / error / reference / source /
    detail); nested structures render as formatted JSON. Unknown keys are
    preserved as labeled JSON lines rather than dropped, so an entry NEVER
    degrades to just its source field. A string that itself carries an
    upstream truncation marker gets an explicit note — the bridge never
    silently pretends the data was complete."""
    if isinstance(item, _SCALAR_TYPES):
        return str(item)
    if not isinstance(item, dict):
        return _render_scalar(item)
    lines: list[str] = []
    handled: set[str] = set()
    lowered = {str(key).strip().lower(): key for key in item.keys()}
    for semantic, label in _EVIDENCE_KEY_LABELS:
        original = lowered.get(semantic)
        if original is None or original in handled:
            continue
        handled.add(original)
        value = item[original]
        if isinstance(value, list) and value and all(isinstance(entry, dict) for entry in value):
            # A list of structured records (e.g. event refs) renders one
            # line per record so API names, success flags and error codes
            # stay individually readable.
            for entry in value:
                rendered_entry = render_evidence_item(entry)
                if rendered_entry.strip():
                    lines.append(f"{label}: {rendered_entry.replace(chr(10), ' | ')}")
            continue
        rendered = value if isinstance(value, str) else _render_scalar(value)
        if isinstance(rendered, str) and rendered.strip():
            lines.append(f"{label}: {rendered.strip()}")
            if _source_truncated(rendered):
                lines.append(f"{label}_note: upstream data was already truncated — shown as received")
    for key in item.keys():
        if key in handled:
            continue
        value = item[key]
        rendered = value if isinstance(value, str) else _render_scalar(value)
        if isinstance(rendered, str) and rendered.strip():
            lines.append(f"{key}: {rendered.strip()}")
    return "\n".join(line for line in lines if line.strip())


def _render_items(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [render_evidence_item(item) for item in raw if str(render_evidence_item(item) or "").strip()]


def _code_fence_open(text: str) -> bool:
    return text.count(_CODE_FENCE) % 2 == 1


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """Split one section's text into ordered shards.

    Preference order: blank-line paragraph boundaries, then single lines,
    and only as a last resort hard character cuts inside one oversized
    line. Code fences are tracked so a shard never ends inside an open
    fence: it is closed at the shard end and reopened at the next shard's
    start. No characters are dropped."""
    if len(text) <= max_chars:
        return [text]
    # Build the shard groups: paragraphs first; an oversized paragraph falls
    # back to its lines; an oversized line is hard-wrapped at the budget.
    shard_units: list[list[str]] = [[]]
    for paragraph in text.split("\n\n"):
        pieces: list[str] = []
        if len(paragraph) <= max_chars:
            pieces = [paragraph]
        else:
            for line in paragraph.split("\n"):
                if len(line) <= max_chars:
                    pieces.append(line)
                    continue
                pieces.extend(line[offset : offset + max_chars] for offset in range(0, len(line), max_chars))
        for piece in pieces:
            candidate = shard_units[-1] + [piece]
            joined = "\n".join(candidate)
            if shard_units[-1] and len(joined) + 1 > max_chars:
                shard_units.append([piece])
            else:
                shard_units[-1] = candidate
    shards = ["\n".join(group) for group in shard_units if group]
    # Fence continuity: close any shard that ends inside an open fence and
    # reopen it at the start of the next shard.
    result: list[str] = []
    reopen = False
    for shard in shards:
        if reopen:
            shard = _CODE_FENCE + "\n" + shard
        if _code_fence_open(shard):
            shard = shard + "\n" + _CODE_FENCE
            reopen = True
        else:
            reopen = False
        result.append(shard)
    return result


@dataclass(frozen=True)
class SectionMessage:
    section: str
    index: int  # 1-based within this section
    total: int
    text: str


def build_investigation_section_messages(
    *,
    header: str,
    record: dict[str, Any],
    extra_lines: list[str] | None = None,
) -> list[SectionMessage]:
    """Build the frozen message list for one investigation record.

    Fixed order Summary → Evidence → Blockers → Next steps; empty sections
    are skipped entirely. Every shard carries the header, section label and
    its own (i/n) index; nothing is business-truncated."""
    record = record if isinstance(record, dict) else {}
    messages: list[SectionMessage] = []
    for section in SECTION_ORDER:
        if section == "evidence":
            items = _render_items(record.get("evidence"))
            body = "\n\n".join(f"- {item}" for item in items) if items else ""
        elif section in {"blockers", "next_steps"}:
            items = _render_items(record.get(section))
            body = "\n".join(f"- {item}" for item in items) if items else ""
        else:
            body = str(record.get("summary") or "").strip()
        if not body:
            continue
        shards = _split_long_text(body, _SHARD_MAX_CHARS - 200)
        for index, shard in enumerate(shards, start=1):
            label = SECTION_LABELS[section]
            title = f"*{header} — {label}*" if len(shards) == 1 else f"*{header} — {label} ({index}/{len(shards)})*"
            messages.append(
                SectionMessage(section=section, index=index, total=len(shards), text=f"{title}\n{shard}")
            )
    for line in extra_lines or []:
        messages.append(SectionMessage(section="info", index=1, total=1, text=f"*{header}*\n{line}"))
    return messages


def deliver_investigation_sections(
    repository: Any,
    *,
    namespace: str,
    thread_ts: str,
    channel_id: str,
    ticket_id: str,
    turn_id: str,
    investigation: dict[str, Any] | None,
    environment: str,
    action: str | None = None,
    event_type: str = HERMES_INVESTIGATION_RESULT_EVENT_TYPE,
    header: str,
    claim_token: str,
    before_external: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Ledger-backed delivery of the sectioned investigation messages.

    Shards are identified by stable keys; a retry of the same frozen list
    skips confirmed shards. outcome_unknown stops the whole list — the
    action button is never sent while a body shard is unconfirmed."""
    if not engineer_slack_configured():
        return {"status": "skipped_not_configured"}
    fence = before_external or (lambda: None)
    scope = f"native-hermes-notification:{namespace}"
    record = investigation if isinstance(investigation, dict) else {}
    messages = build_investigation_section_messages(header=header, record=record)
    confirmed_shards: list[str] = []
    for message in messages:
        key = f"investigation:{ticket_id}:{turn_id}:{message.section}:{message.index}"
        row = repository.enqueue_native_notification(
            scope=scope,
            key=key,
            created_at=_now(),
            payload={
                "kind": "investigation_section",
                "section": message.section,
                "index": message.index,
                "total": message.total,
                "message_text": message.text,
                "ticket_id": ticket_id,
                "turn_id": turn_id,
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "event_type": event_type,
                "environment": environment,
            },
        )
        state = str(row.get("state") or "")
        if state == "completed":
            confirmed_shards.append(key)
            continue
        if state in {"sending", "outcome_unknown"}:
            return {
                "status": "outcome_unknown",
                "reason": "submission_requires_message_identity_readback",
                "section": message.section,
                "stopped_key": key,
                "confirmed": confirmed_shards,
                "action_sent": False,
            }
        # pending / retryable failed: claim, deliver, finish.
        fence()
        claimed = repository.claim_native_notification(scope=scope, key=key, claim_token=claim_token, updated_at=_now())
        if claimed is None:
            return {"status": "not_claimed", "stopped_key": key, "confirmed": confirmed_shards, "action_sent": False}
        payload = claimed["response_payload"]
        submitted = False
        try:
            event = {
                "event_id": f"{scope}:{key}",
                "event_type": payload["event_type"],
                "message_text": payload["message_text"],
                "environment": payload.get("environment"),
                "zendesk_ticket_id": payload.get("ticket_id"),
                "turn_id": payload.get("turn_id"),
            }
            fence()
            submitted = True
            result = post_engineer_slack_event(event, thread_ts=payload["thread_ts"])
            if (
                result.get("status") != "delivered"
                or result.get("slack_channel_id") != payload["channel_id"]
                or not result.get("slack_message_ts")
            ):
                raise EngineerSlackDeliveryError("native_slack_receipt_invalid", outcome_unknown=True)
            saved = repository.finish_native_notification(
                scope=scope, key=key, claim_token=claim_token, state="completed", result=result, updated_at=_now()
            )
            if not saved:
                raise EngineerSlackDeliveryError("native_slack_receipt_unpersisted", outcome_unknown=True)
            confirmed_shards.append(key)
        except Exception as exc:
            unknown = bool(submitted and (not isinstance(exc, EngineerSlackDeliveryError) or exc.outcome_unknown))
            state = "outcome_unknown" if unknown else "failed"
            failure = {
                "status": state,
                "failure_code": getattr(exc, "code", type(exc).__name__),
                "section": message.section,
            }
            try:
                repository.finish_native_notification(
                    scope=scope, key=key, claim_token=claim_token, state=state, result=failure, updated_at=_now()
                )
            except Exception:  # noqa: BLE001 - never reset a possibly accepted POST
                failure["status"] = "outcome_unknown"
                failure["failure_code"] = "notification_outcome_unpersisted"
            return {
                **failure,
                "stopped_key": key,
                "confirmed": confirmed_shards,
                "action_sent": False,
            }
    # All body shards confirmed — only now may the review action message go.
    if action:
        key = f"investigation:{ticket_id}:{turn_id}:action:{action}"
        row = repository.enqueue_native_notification(
            scope=scope,
            key=key,
            created_at=_now(),
            payload={
                "kind": "investigation_section",
                "section": "action",
                "index": 1,
                "total": 1,
                "message_text": f"*{header}*\nAll sections delivered. Review the draft in the messages above.",
                "ticket_id": ticket_id,
                "turn_id": turn_id,
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "event_type": event_type,
                "environment": environment,
                "action": action,
            },
        )
        state = str(row.get("state") or "")
        if state in {"sending", "outcome_unknown"}:
            return {
                "status": "outcome_unknown",
                "section": "action",
                "stopped_key": key,
                "confirmed": confirmed_shards,
                "action_sent": False,
            }
        if state != "completed":
            fence()
            claimed = repository.claim_native_notification(scope=scope, key=key, claim_token=claim_token, updated_at=_now())
            if claimed is None:
                return {"status": "not_claimed", "stopped_key": key, "confirmed": confirmed_shards, "action_sent": False}
            payload = claimed["response_payload"]
            submitted = False
            try:
                event = {
                    "event_id": f"{scope}:{key}",
                    "event_type": payload["event_type"],
                    "message_text": payload["message_text"],
                    "action": action,
                    "environment": payload.get("environment"),
                    "zendesk_ticket_id": payload.get("ticket_id"),
                    "turn_id": payload.get("turn_id"),
                }
                fence()
                submitted = True
                result = post_engineer_slack_event(event, thread_ts=payload["thread_ts"])
                if result.get("status") != "delivered" or not result.get("slack_message_ts"):
                    raise EngineerSlackDeliveryError("native_slack_receipt_invalid", outcome_unknown=True)
                repository.finish_native_notification(
                    scope=scope, key=key, claim_token=claim_token, state="completed", result=result, updated_at=_now()
                )
            except Exception as exc:
                unknown = bool(submitted and (not isinstance(exc, EngineerSlackDeliveryError) or exc.outcome_unknown))
                state = "outcome_unknown" if unknown else "failed"
                failure = {
                    "status": state,
                    "failure_code": getattr(exc, "code", type(exc).__name__),
                    "section": "action",
                }
                try:
                    repository.finish_native_notification(
                        scope=scope, key=key, claim_token=claim_token, state=state, result=failure, updated_at=_now()
                    )
                except Exception:  # noqa: BLE001
                    failure["status"] = "outcome_unknown"
                return {**failure, "stopped_key": key, "confirmed": confirmed_shards, "action_sent": False}
    return {
        "status": "confirmed",
        "shards": len(messages),
        "confirmed": confirmed_shards,
        "action_sent": bool(action),
    }


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
