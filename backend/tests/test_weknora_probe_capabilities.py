from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from backend.services.weknora_client import WeKnoraError

_PROBE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "weknora" / "probe_weknora_contract.py"
)


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("probe_weknora_contract", _PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["probe_weknora_contract"] = module
    spec.loader.exec_module(module)
    return module


class FakeProbeServer:
    """WeKnora double for the write-capability probe.

    conditional=True rejects any update whose base_version is not the current
    stored version (a proper optimistic-locking server); conditional=False
    accepts every update regardless of base_version (no server-side version
    protection).
    """

    def __init__(self, *, conditional: bool) -> None:
        self.conditional = conditional
        self.objects: dict[str, dict[str, Any]] = {}
        self.next_version = 10

    def knowledge_create(self, *, title: str, content: str, idempotency_key: str) -> dict:
        for object_id, record in self.objects.items():
            if record["idempotency_key"] == idempotency_key:
                return {"object_id": object_id, "version": record["version"]}
        object_id = "probe-1"
        self.next_version += 1
        self.objects[object_id] = {
            "title": title,
            "content": content,
            "version": str(self.next_version),
            "idempotency_key": idempotency_key,
        }
        return {"object_id": object_id, "version": str(self.next_version)}

    def knowledge_read(self, *, object_id: str) -> dict:
        record = self.objects[object_id]
        return {
            "object_id": object_id,
            "version": record["version"],
            "title": record["title"],
            "content": record["content"],
        }

    def knowledge_update(
        self, *, object_id: str, base_version: str, title: str, content: str, idempotency_key: str
    ) -> dict:
        record = self.objects[object_id]
        if self.conditional and base_version != record["version"]:
            raise WeKnoraError(
                f"stale base_version {base_version}", failure_kind="conflict"
            )
        self.next_version += 1
        record.update(title=title, content=content, version=str(self.next_version))
        return {"object_id": object_id, "version": str(self.next_version)}


def test_probe_capabilities_all_verified_when_server_rejects_stale_base() -> None:
    probe = _load_probe_module()
    caps = probe._probe_write_capabilities(FakeProbeServer(conditional=True))
    for capability in ("create", "readback", "idempotent_recreate", "conditional_update"):
        assert caps[capability]["status"] == "verified", capability
    stale = caps["stale_base_version_rejected"]
    assert stale["status"] == "verified"
    assert stale["result"]["rejected"] is True
    # The stale base must be an outdated version, never the current one.
    assert stale["result"]["stale_base_version"] != "13"  # current after two updates


def test_probe_marks_stale_acceptance_as_failed_not_verified() -> None:
    """A server without version protection must not produce 'verified'
    evidence for conditional updates."""
    probe = _load_probe_module()
    caps = probe._probe_write_capabilities(FakeProbeServer(conditional=False))
    stale = caps["stale_base_version_rejected"]
    assert stale["status"] == "failed"
    assert "ACCEPTED a stale base_version" in stale["detail"]
    assert "conditional_update=true must not be declared" in stale["detail"]
