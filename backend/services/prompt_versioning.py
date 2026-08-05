from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.services.agent_config import build_managed_prompt_catalog
from backend.services.prompt_runtime import load_prompt_release_snapshot


MAX_PROMPT_CONTENT_CHARS = 100_000
MAX_CHANGE_NOTE_CHARS = 500


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PromptVersionService:
    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def sync_catalog(self, *, actor_id: str = "system", created_at: str | None = None) -> dict[str, Any]:
        return self.repository.sync_prompt_catalog(
            build_managed_prompt_catalog(),
            actor_id=actor_id,
            created_at=created_at or _now_iso(),
        )

    def list_prompts(self) -> list[dict[str, Any]]:
        return self.repository.list_managed_prompts()

    def get_prompt(self, prompt_key: str) -> dict[str, Any]:
        prompt = self.repository.get_managed_prompt(prompt_key)
        if prompt is None:
            raise ValueError("prompt not found")
        return prompt

    def create_draft(
        self,
        prompt_key: str,
        *,
        content: str,
        change_note: str,
        based_on_version: int,
        actor_id: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        normalized_content = str(content or "").strip()
        normalized_note = str(change_note or "").strip()
        if not normalized_content:
            raise ValueError("prompt content is required")
        if len(normalized_content) > MAX_PROMPT_CONTENT_CHARS:
            raise ValueError(f"prompt content exceeds {MAX_PROMPT_CONTENT_CHARS} characters")
        if not normalized_note:
            raise ValueError("change_note is required")
        if len(normalized_note) > MAX_CHANGE_NOTE_CHARS:
            raise ValueError(f"change_note exceeds {MAX_CHANGE_NOTE_CHARS} characters")
        prompt = self.get_prompt(prompt_key)
        if not prompt.get("editable"):
            raise ValueError("prompt is read-only")
        return self.repository.create_prompt_draft(
            prompt_key,
            content=normalized_content,
            change_note=normalized_note,
            based_on_version=int(based_on_version),
            actor_id=actor_id,
            created_at=created_at or _now_iso(),
        )

    def schedule(self, prompt_key: str, version: int, *, actor_id: str, scheduled_at: str | None = None) -> dict[str, Any]:
        return self.repository.schedule_prompt_version(
            prompt_key,
            int(version),
            actor_id=actor_id,
            scheduled_at=scheduled_at or _now_iso(),
        )

    def unschedule(self, prompt_key: str, version: int) -> dict[str, Any]:
        return self.repository.unschedule_prompt_version(prompt_key, int(version))

    def restore(self, prompt_key: str, version: int, *, actor_id: str, created_at: str | None = None) -> dict[str, Any]:
        return self.repository.restore_prompt_version(
            prompt_key,
            int(version),
            actor_id=actor_id,
            created_at=created_at or _now_iso(),
        )

    def prepare_release(self, *, build_ref: str, created_at: str | None = None) -> dict[str, Any]:
        self.sync_catalog(created_at=created_at)
        return self.repository.prepare_prompt_release(
            build_ref=str(build_ref or "unknown").strip() or "unknown",
            created_at=created_at or _now_iso(),
        )

    def activate_release(self, release_id: str, *, activated_at: str | None = None) -> dict[str, Any]:
        self.validate_release(release_id)
        return self.repository.activate_prompt_release(
            release_id,
            activated_at=activated_at or _now_iso(),
        )

    def fail_release(self, release_id: str, *, failure_reason: str) -> dict[str, Any]:
        normalized_reason = str(failure_reason or "").strip()
        if not normalized_reason:
            raise ValueError("failure_reason is required")
        return self.repository.fail_prompt_release(release_id, failure_reason=normalized_reason[:1000])

    def active_release(self) -> dict[str, Any] | None:
        return self.repository.get_active_prompt_release()

    def validate_release(self, release_id: str) -> dict[str, Any]:
        snapshot = load_prompt_release_snapshot(self.repository, release_id)
        return snapshot.info()

    def release(self, release_id: str) -> dict[str, Any]:
        release = self.repository.get_prompt_release(release_id)
        if release is None:
            raise ValueError("prompt release not found")
        return release

    def list_releases(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.repository.list_prompt_releases(limit=limit)
