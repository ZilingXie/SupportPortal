"""Normalization and revision helpers for Zendesk comment snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


MAX_COMMENT_BODY_LENGTH = 100_000
MAX_COMMENT_AUTHOR_NAME_LENGTH = 160
MAX_COMMENT_CHANNEL_LENGTH = 160


class ZendeskCommentSnapshotError(ValueError):
    """A snapshot cannot be safely applied to the Account comment projection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code or "invalid_snapshot").strip() or "invalid_snapshot"
        self.message = str(message or "Invalid Zendesk comment snapshot").strip()


@dataclass(frozen=True)
class NormalizedZendeskComment:
    zendesk_comment_id: str
    is_public: bool
    is_initial: bool
    author_id: str | None
    author_name: str | None
    author_kind: str
    body: str
    via_channel: str | None
    created_at: str

    def as_storage_payload(self) -> dict[str, Any]:
        return {
            "zendesk_comment_id": self.zendesk_comment_id,
            "is_public": self.is_public,
            "is_initial": self.is_initial,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "author_kind": self.author_kind,
            "body": self.body,
            "via_channel": self.via_channel,
            "created_at": self.created_at,
        }

    def as_public_payload(self) -> dict[str, Any]:
        return {
            "zendesk_comment_id": self.zendesk_comment_id,
            "is_public": self.is_public,
            "is_initial": self.is_initial,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "author_kind": self.author_kind,
            "body": self.body,
            "via_channel": self.via_channel,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class NormalizedZendeskSnapshot:
    source_updated_at: str
    comments: tuple[NormalizedZendeskComment, ...]
    snapshot_hash: str
    comments_revision: str


def _normalize_text(value: Any, *, field: str, max_length: int, required: bool = False) -> str:
    text = " ".join(str(value or "").split()).strip()
    if required and not text:
        raise ZendeskCommentSnapshotError("invalid_snapshot", f"{field} is required")
    if len(text) > max_length:
        raise ZendeskCommentSnapshotError(
            "invalid_snapshot",
            f"{field} must not exceed {max_length} characters",
        )
    return text


def normalize_timestamp(value: Any, *, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ZendeskCommentSnapshotError("invalid_snapshot", f"{field} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ZendeskCommentSnapshotError(
            "invalid_snapshot",
            f"{field} must be an ISO-8601 timestamp",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse_public(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"false", "0", "no", "private", "internal"}:
            return False
        if normalized in {"true", "1", "yes", "public"}:
            return True
    return bool(value)


def _author_kind(role: Any, *, is_public: bool) -> str:
    normalized = str(role or "").strip().lower()
    if normalized in {"end-user", "end_user", "customer", "requester", "user"}:
        return "customer"
    if normalized in {"agent", "staff", "admin", "support"}:
        return "agent"
    if normalized in {"system", "bot", "automation"}:
        return "system"
    # A private comment with no Zendesk role is still not a customer message.
    return "unknown"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def normalize_snapshot(payload: Any) -> NormalizedZendeskSnapshot:
    if not isinstance(payload, dict):
        raise ZendeskCommentSnapshotError("invalid_snapshot", "snapshot must be an object")
    if payload.get("snapshot_complete") is not True:
        raise ZendeskCommentSnapshotError(
            "incomplete_snapshot",
            "snapshot_complete must be true",
        )

    source_updated_at = normalize_timestamp(payload.get("source_updated_at"), field="source_updated_at")
    raw_comments = payload.get("comments")
    if not isinstance(raw_comments, list):
        raise ZendeskCommentSnapshotError("invalid_snapshot", "comments must be an array")

    normalized: list[NormalizedZendeskComment] = []
    seen_ids: set[str] = set()
    for raw_comment in raw_comments:
        if not isinstance(raw_comment, dict):
            raise ZendeskCommentSnapshotError("invalid_snapshot", "each comment must be an object")
        comment_id = _normalize_text(
            raw_comment.get("id", raw_comment.get("zendesk_comment_id")),
            field="comment id",
            max_length=128,
            required=True,
        )
        if comment_id in seen_ids:
            raise ZendeskCommentSnapshotError(
                "duplicate_comment_id",
                f"duplicate Zendesk comment id: {comment_id}",
            )
        seen_ids.add(comment_id)
        is_public = _parse_public(raw_comment.get("public", raw_comment.get("is_public")))
        author = raw_comment.get("author")
        author = author if isinstance(author, dict) else {}
        author_id = _normalize_text(
            author.get("id", raw_comment.get("author_id")),
            field="author id",
            max_length=128,
        ) or None
        author_name = _normalize_text(
            author.get("name", raw_comment.get("author_name")),
            field="author name",
            max_length=MAX_COMMENT_AUTHOR_NAME_LENGTH,
        ) or None
        author_kind = _author_kind(
            author.get("role", raw_comment.get("author_kind")),
            is_public=is_public,
        )
        body = str(raw_comment.get("body") or "").strip()
        if len(body) > MAX_COMMENT_BODY_LENGTH:
            raise ZendeskCommentSnapshotError(
                "invalid_snapshot",
                f"comment body must not exceed {MAX_COMMENT_BODY_LENGTH} characters",
            )
        raw_via = raw_comment.get("via_channel", raw_comment.get("via", ""))
        if isinstance(raw_via, dict):
            raw_via = raw_via.get("channel")
        via_channel = _normalize_text(
            raw_via,
            field="via_channel",
            max_length=MAX_COMMENT_CHANNEL_LENGTH,
        ) or None
        created_at = normalize_timestamp(raw_comment.get("created_at"), field="comment created_at")
        normalized.append(
            NormalizedZendeskComment(
                zendesk_comment_id=comment_id,
                is_public=is_public,
                is_initial=False,
                author_id=author_id,
                author_name=author_name,
                author_kind=author_kind,
                body=body,
                via_channel=via_channel,
                created_at=created_at,
            )
        )

    normalized.sort(key=lambda item: (item.created_at, item.zendesk_comment_id))
    if normalized:
        first = normalized[0]
        normalized[0] = NormalizedZendeskComment(
            **{**first.as_storage_payload(), "is_initial": True}
        )

    comment_payloads = [comment.as_storage_payload() for comment in normalized]
    snapshot_hash = _sha256(
        {"source_updated_at": source_updated_at, "comments": comment_payloads}
    )
    comments_revision = _sha256(comment_payloads)
    return NormalizedZendeskSnapshot(
        source_updated_at=source_updated_at,
        comments=tuple(normalized),
        snapshot_hash=snapshot_hash,
        comments_revision=comments_revision,
    )


def build_conversation_revision(detail_revision: Any, comments_revision: Any) -> str:
    return hashlib.sha256(
        f"{str(detail_revision or '').strip()}|{str(comments_revision or '').strip()}".encode("utf-8")
    ).hexdigest()


def comment_sync_summary(
    *,
    source_updated_at: Any = None,
    synced_at: Any = None,
    comment_count: int = 0,
    comments_revision: Any = None,
) -> dict[str, Any]:
    return {
        "source_updated_at": str(source_updated_at or "").strip() or None,
        "synced_at": str(synced_at or "").strip() or None,
        "comment_count": max(0, int(comment_count or 0)),
        "comments_revision": str(comments_revision or "").strip() or None,
    }
