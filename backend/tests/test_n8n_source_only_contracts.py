"""Stage-2 source-only contract tests (p2-194): execute the REAL n8n Code-node
JavaScript from the repo draft snapshots against synthetic fixtures through a
minimal n8n shim, and assert the produced snapshots validate against the
SupportPortal ``KnowledgeSourceSnapshot`` model (contract option A: no new
top-level fields; ``snapshot_hash`` travels in ``references``).

No real ticket/Jira data is replayed: every fixture is synthetic. Real n8n
execution evidence is registered as waiting-for-evidence in
docs/evidence/weknora-dualwrite-phase2/stage2-source-only.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

import pytest  # noqa: E402

from scripts.n8n.validate_workflow_snapshots import (  # noqa: E402
    DIRECT_WRITE_URL_PATTERNS,
    SOURCE_ONLY_ALLOWED_NODE_TYPES,
    SOURCE_ONLY_URL_ALLOW,
    _static_url_prefix,
)

DRAFTS = Path("docs/integrations/n8n/workflows/drafts")
SOLVED = json.loads((DRAFTS / "MM3Z3T469Eru3Q1I.draft.json").read_text())
CSD = json.loads((DRAFTS / "GgDxPEWtW7ltT5BW.draft.json").read_text())

NODE_BIN = os.environ.get("NODE_BIN", "/usr/local/bin/node")

SHIM = """
const fs = require('fs');
const crypto = require('crypto');
const input = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const named = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const $input = {
  first: () => ({ json: input }),
  all: () => [{ json: input }],
};
function $(name) { return { first: () => ({ json: named[name] }) }; }
"""


def _run_js(code: str, payload: dict, named: dict | None = None) -> dict:
    """Run one Code-node script with the shim; return {ok, items}.

    ``payload`` is the $input item; ``named`` maps node names to the json that
    ``$('Node').first().json`` returns (the n8n cross-node reference surface).
    """
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fixture,             tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as named_file:
        json.dump(payload, fixture)
        json.dump(named or {}, named_file)
        fixture_path, named_path = fixture.name, named_file.name
    program = (
        f"{SHIM}\n"
        "const __result = (function() {\n" + code + "\n})();\n"
        "process.stdout.write(JSON.stringify(__result));\n"
    )
    result = subprocess.run(
        [NODE_BIN, "-e", program, fixture_path, named_path],
        capture_output=True, text=True, timeout=30,
    )
    os.unlink(fixture_path)
    os.unlink(named_path)
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip()}
    items = json.loads(result.stdout or "[]")
    return {"ok": True, "items": items}


def _node_code(snapshot: dict, name: str) -> str:
    for node in snapshot["workflow"]["nodes"]:
        if node.get("name") == name:
            return (node.get("parameters") or {}).get("jsCode") or ""
    raise AssertionError(f"node {name!r} not found in snapshot")


# ----------------------------------------------------------- structural (C1)


@pytest.mark.parametrize("snapshot", [SOLVED, CSD], ids=["solved", "csd"])
def test_draft_nodes_and_urls_are_source_only(snapshot: dict) -> None:
    for node in snapshot["workflow"]["nodes"]:
        assert node["type"] in SOURCE_ONLY_ALLOWED_NODE_TYPES, node["name"]
        for key, value in _walk_strings(node.get("parameters") or {}):
            for label, pattern in DIRECT_WRITE_URL_PATTERNS:
                assert not pattern.search(value), f"{node['name']} {key} hits {label}"
            if key[-1:] == ("url",) or (key and key[-1] in {"url", "endpoint", "host"}):
                prefix = _static_url_prefix(value)
                if prefix:
                    assert any(p.search(prefix) for p in SOURCE_ONLY_URL_ALLOW), (
                        f"{node['name']} URL {prefix!r} not allowlisted"
                    )


def _walk_strings(value, path=()):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk_strings(v, (*path, str(k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk_strings(v, (*path, str(i)))


@pytest.mark.parametrize("snapshot", [SOLVED, CSD], ids=["solved", "csd"])
def test_draft_connections_close(snapshot: dict) -> None:
    names = {n["name"] for n in snapshot["workflow"]["nodes"]}
    for source, targets in (snapshot["workflow"].get("connections") or {}).items():
        assert source in names, source
        for groups in targets.values():
            for group in groups or []:
                for link in group or []:
                    assert (link or {}).get("node") in names, link


# ------------------------------------------------------- Solved chain (C2)


def _solved_ticket(comment_count: int, *, next_page: str | None = None,
                   page_comments: int | None = None) -> dict:
    """One synthetic comment page plus the ticket the snapshot node fetched.

    page_comments defaults to comment_count (complete); a smaller value
    simulates a truncated fetch the completeness gate must refuse."""
    n = page_comments if page_comments is not None else comment_count
    page = {
        "comments": [
            {"id": c, "body": f"synthetic comment {c}", "author_id": 100 + c}
            for c in range(1, n + 1)
        ],
        "next_page": next_page,
    }
    ticket = {
        "id": 990001,
        "status": "solved",
        "updated_at": "2026-10-10T01:02:03Z",
        "comment_count": comment_count,
    }
    return {"page": page, "ticket": ticket, "named": {"Get Ticket Snapshot": {"ticket": ticket}}}


def test_solved_completeness_passes_and_builds_contract_a_snapshot() -> None:
    from backend.automation_ecs_api import KnowledgeSourceSnapshot

    fixture = _solved_ticket(comment_count=3)
    completeness = _run_js(
        _node_code(SOLVED, "Validate Snapshot Completeness"),
        fixture["page"],
        named=fixture["named"],
    )
    assert completeness["ok"], completeness
    validated_input = completeness["items"][0]["json"]
    assert validated_input["comments"] and len(validated_input["comments"]) == 3
    assert validated_input["ticket"]["id"] == 990001

    built = _run_js(_node_code(SOLVED, "Build knowledge-source-v1"), validated_input)
    assert built["ok"], built
    snapshot_json = built["items"][0]["json"]
    model = KnowledgeSourceSnapshot.model_validate(snapshot_json)  # option A: v1 accepts it
    assert model.source_type == "zendesk_ticket"
    assert model.source_id == "990001"
    assert model.source_updated_at == "2026-10-10T01:02:03Z"
    assert len(model.payload["comments"]) == 3
    assert len(model.references["snapshot_hash"]) == 64
    # idempotency identity is exactly the server-side triple (C4): the same
    # input triple must reproduce the same snapshot fields.
    again = _run_js(_node_code(SOLVED, "Build knowledge-source-v1"), validated_input)
    assert again["items"][0]["json"]["references"]["snapshot_hash"] == \
        snapshot_json["references"]["snapshot_hash"]


def test_solved_pagination_incomplete_fails_closed() -> None:
    fixture = _solved_ticket(comment_count=5, page_comments=3)  # truncated fetch
    result = _run_js(
        _node_code(SOLVED, "Validate Snapshot Completeness"),
        fixture["page"],
        named=fixture["named"],
    )
    assert not result["ok"] and "Comment pagination incomplete" in result["error"]


def test_solved_next_page_present_fails_closed() -> None:
    fixture = _solved_ticket(
        comment_count=2, next_page="https://agoraio.zendesk.com/api/v2/next"
    )
    result = _run_js(
        _node_code(SOLVED, "Validate Snapshot Completeness"),
        fixture["page"],
        named=fixture["named"],
    )
    assert not result["ok"] and "next_page still present" in result["error"]


# ---------------------------------------------------------- CSD chain (C2)


def _csd_issue(comment_total: int, comments: int, *, omit_comment: bool = False) -> dict:
    issue = {
        "key": "CSD-990001",
        "fields": {
            "summary": "synthetic CSD issue",
            "status": {"name": "RESOLVED"},
            "resolution": {"name": "Fixed"},
            "updated": "2026-10-10T04:05:06Z",
        },
    }
    if not omit_comment:
        issue["fields"]["comment"] = {
            "comments": [
                {"id": c, "body": f"synthetic csd comment {c}"} for c in range(1, comments + 1)
            ],
            "total": comment_total,
        }
    return issue


def test_csd_complete_comment_builds_and_validates_against_v1() -> None:
    from backend.automation_ecs_api import KnowledgeSourceSnapshot

    issue = _csd_issue(comment_total=3, comments=3)
    validated = _run_js(_node_code(CSD, "Validate CSD Snapshot"), issue)
    assert validated["ok"], validated
    built = _run_js(
        _node_code(CSD, "Build csd_issue Snapshot"), validated["items"][0]["json"]
    )
    assert built["ok"], built
    snapshot_json = built["items"][0]["json"]
    model = KnowledgeSourceSnapshot.model_validate(snapshot_json)
    assert model.source_type == "csd_issue"
    assert model.source_id == "CSD-990001"
    assert model.payload["comment_count"] == 3
    assert len(model.references["snapshot_hash"]) == 64


def test_csd_missing_comment_field_fails_closed() -> None:
    issue = _csd_issue(comment_total=0, comments=0, omit_comment=True)
    result = _run_js(_node_code(CSD, "Validate CSD Snapshot"), issue)
    assert not result["ok"] and "fields.comment missing" in result["error"]


def test_csd_comment_count_short_of_total_fails_closed() -> None:
    issue = _csd_issue(comment_total=5, comments=3)
    result = _run_js(_node_code(CSD, "Validate CSD Snapshot"), issue)
    assert not result["ok"] and "total 5" in result["error"]


# ------------------------------------------------------ receipt gate (C3/C4)


@pytest.mark.parametrize(
    "status", ["accepted", "already_exists", "stale_ignored"]
)
def test_receipt_gate_accepts_three_states(status: str) -> None:
    verify = _run_js(_node_code(SOLVED, "Verify Receipt"),
                     {"status": status, "task_id": "task-123"})
    assert verify["ok"], verify
    assert verify["items"][0]["json"] == {
        "delivered": True, "status": status, "task_id": "task-123",
    }


@pytest.mark.parametrize(
    "body,label",
    [
        ({"status": "queued", "task_id": "t"}, "unexpected status"),
        ({"status": "accepted", "task_id": ""}, "empty task_id"),
        ({"status": "accepted"}, "empty task_id"),
        ({"error": "boom"}, "unexpected status"),
    ],
)
def test_receipt_gate_fails_closed_on_bad_receipts(body: dict, label: str) -> None:
    result = _run_js(_node_code(SOLVED, "Verify Receipt"), body)
    assert not result["ok"] and label in result["error"]


def test_already_exists_absorbs_replay_and_stale_absorbs_reorder() -> None:
    """C4: the receiving side already implements the three-state idempotency on
    (source_type, source_id, source_updated_at); the workflow surfaces exactly
    that behaviour through the receipt gate without any local dedup."""
    triple = ("zendesk_ticket", "990001", "2026-10-10T01:02:03Z")
    from backend.repositories.knowledge_source_repository import (
        InMemoryKnowledgeSourceRepositoryMixin,
    )

    import threading

    class _Host(InMemoryKnowledgeSourceRepositoryMixin):
        def __init__(self) -> None:
            self._assignment_lock = threading.RLock()
            self._initialize_knowledge_source_state()

    host = _Host()
    base = {
        "schema_version": "knowledge-source-v1",
        "source_type": triple[0],
        "source_id": triple[1],
        "payload": {"ticket": {"id": 990001}, "comments": []},
        "references": {"snapshot_hash": "h" * 64},
    }
    first = host.accept_knowledge_source(
        {**base, "source_updated_at": triple[2]}, now_value="2026-10-10T01:00:00Z"
    )
    assert first["receipt_status"] == "accepted" and first["task_id"]
    replay = host.accept_knowledge_source(
        {**base, "source_updated_at": triple[2]}, now_value="2026-10-10T02:00:00Z"
    )
    assert replay["receipt_status"] == "already_exists"
    assert replay.get("task_id")
    stale = host.accept_knowledge_source(
        {**base, "source_updated_at": "2026-10-09T00:00:00Z"},
        now_value="2026-10-10T03:00:00Z",
    )
    assert stale["receipt_status"] == "stale_ignored"
    # each receipt passes the workflow's Verify Receipt gate unchanged
    for receipt in (first, replay, stale):
        gate = _run_js(_node_code(SOLVED, "Verify Receipt"),
                       {"status": receipt["receipt_status"], "task_id": receipt["task_id"]})
        assert gate["ok"], gate
