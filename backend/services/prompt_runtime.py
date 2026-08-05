from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptRuntimeSnapshot:
    release_id: str
    prompts: dict[str, str]
    source: str

    def info(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "prompt_count": len(self.prompts),
            "status": "loaded",
            "source": self.source,
        }


_SNAPSHOT: PromptRuntimeSnapshot | None = None
_SNAPSHOT_LOCK = threading.Lock()


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _code_snapshot() -> PromptRuntimeSnapshot:
    from backend.services.agent_config import build_managed_prompt_catalog

    prompts = {
        item["prompt_key"]: str(item["content"])
        for item in build_managed_prompt_catalog()
    }
    digest = hashlib.sha256(
        "\n".join(f"{key}:{prompts[key]}" for key in sorted(prompts)).encode("utf-8")
    ).hexdigest()[:12]
    return PromptRuntimeSnapshot(release_id=f"code-{digest}", prompts=prompts, source="code")


def load_prompt_release_snapshot(repository: Any, release_id: str) -> PromptRuntimeSnapshot:
    normalized_release_id = str(release_id or "").strip()
    if not normalized_release_id:
        raise RuntimeError("Prompt Release ID is required")
    release = repository.get_prompt_release(normalized_release_id)
    if release is None:
        raise RuntimeError(f"Prompt Release {normalized_release_id} was not found")
    if release.get("status") not in {"candidate", "active"}:
        raise RuntimeError(f"Prompt Release {normalized_release_id} is not deployable")
    managed = {item["prompt_key"]: item for item in repository.list_managed_prompts()}
    prompts: dict[str, str] = {}
    for prompt_key, version in dict(release.get("items") or {}).items():
        prompt = managed.get(prompt_key)
        selected = next(
            (
                item
                for item in list((prompt or {}).get("versions") or [])
                if int(item.get("version") or 0) == int(version)
            ),
            None,
        )
        if selected is None:
            raise RuntimeError(f"Prompt Release {normalized_release_id} is missing {prompt_key} v{version}")
        content = str(selected.get("content") or "")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if content_hash != str(selected.get("content_sha256") or ""):
            raise RuntimeError(f"Prompt Release {normalized_release_id} hash mismatch for {prompt_key}")
        prompts[prompt_key] = content
    from backend.services.agent_config import build_managed_prompt_catalog

    expected_keys = {item["prompt_key"] for item in build_managed_prompt_catalog()}
    if set(prompts) != expected_keys:
        missing = sorted(expected_keys - set(prompts))
        extra = sorted(set(prompts) - expected_keys)
        raise RuntimeError(
            f"Prompt Release {normalized_release_id} catalog mismatch missing={missing} extra={extra}"
        )
    return PromptRuntimeSnapshot(release_id=normalized_release_id, prompts=prompts, source="release")


def initialize_prompt_runtime(repository: Any | None = None, *, service_name: str | None = None) -> PromptRuntimeSnapshot:
    global _SNAPSHOT
    with _SNAPSHOT_LOCK:
        if _SNAPSHOT is not None:
            return _SNAPSHOT
        release_id = str(os.getenv("PROMPT_RELEASE_ID") or "").strip()
        required = _env_flag("PROMPT_RELEASE_REQUIRED", default=False)
        if not release_id:
            if required:
                raise RuntimeError("PROMPT_RELEASE_ID is required")
            _SNAPSHOT = _code_snapshot()
            LOGGER.warning(
                "prompt_runtime_loaded service=%s release_id=%s prompts=%s source=code",
                str(service_name or "unknown"),
                _SNAPSHOT.release_id,
                len(_SNAPSHOT.prompts),
            )
            return _SNAPSHOT
        if repository is None:
            raise RuntimeError("prompt repository is required for a managed Prompt Release")
        _SNAPSHOT = load_prompt_release_snapshot(repository, release_id)
        LOGGER.warning(
            "prompt_runtime_loaded service=%s release_id=%s prompts=%s source=release",
            str(service_name or "unknown"),
            release_id,
            len(_SNAPSHOT.prompts),
        )
        return _SNAPSHOT


def initialize_prompt_runtime_from_environment(*, service_name: str) -> PromptRuntimeSnapshot:
    release_id = str(os.getenv("PROMPT_RELEASE_ID") or "").strip()
    if not release_id:
        return initialize_prompt_runtime(service_name=service_name)
    from backend.repositories.ticket_repository import create_ticket_repository

    repository = create_ticket_repository()
    try:
        repository.initialize()
        return initialize_prompt_runtime(repository, service_name=service_name)
    finally:
        close = getattr(repository, "close", None)
        if callable(close):
            close()


def resolve_system_prompt(prompt_key: str, fallback: str) -> str:
    snapshot = _SNAPSHOT
    if snapshot is None:
        if _env_flag("PROMPT_RELEASE_REQUIRED", default=False):
            raise RuntimeError("Prompt runtime was not initialized")
        return str(fallback)
    content = snapshot.prompts.get(str(prompt_key or "").strip())
    if content is None:
        if snapshot.source == "release":
            raise RuntimeError(f"Managed prompt {prompt_key} is missing from {snapshot.release_id}")
        return str(fallback)
    return content


def prompt_runtime_info() -> dict[str, Any]:
    snapshot = _SNAPSHOT
    return snapshot.info() if snapshot is not None else {"release_id": None, "prompt_count": 0, "status": "not_loaded", "source": None}


def reset_prompt_runtime_for_tests() -> None:
    global _SNAPSHOT
    with _SNAPSHOT_LOCK:
        _SNAPSHOT = None
