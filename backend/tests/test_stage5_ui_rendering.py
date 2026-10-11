"""Stage-5 UI rendering assertions (p2-196, review B7).

Drives the REAL app.js render functions in Node against REAL API payloads
(produced by the same seeded repository + injected anomalies as the backend
contract tests) and asserts the anomaly reasons reach the DOM string.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from backend.tests.test_stage5_knowledge_review import (  # noqa: E402
    _client_and_store,
    _login,
    _patch_repository,
    _promotion_id,
    _seed_repository,
    _summary_task_id,
)

NODE_BIN = os.environ.get("NODE_BIN", "/usr/local/bin/node")

RENDER_HARNESS = r'''
const fs = require("fs");
const uiSource = fs.readFileSync("ui/automation-ecs-production/app.js", "utf-8");
// Extract only the pure functions the detail renderer needs (no DOM access).
const functionNames = [
  "escapeHtml", "formatLabel", "statusTone", "statusLabel", "statusMarkup",
  "knowledgeQualityTone", "summaryAnomalyReason", "knowledgeStateRow", "renderMaybeCode",
  "knowledgeDetailSection", "knowledgeSlackLink", "knowledgeAuditLine",
  "deliverySummaryBadges", "renderKnowledgeDetail",
];
const extracted = [];
for (const name of functionNames) {
  const patterns = [
    new RegExp("function " + name + "\\([^)]*\\)\\s*\\{", "g"),
  ];
  let found = false;
  for (const pattern of patterns) {
    const match = pattern.exec(uiSource);
    if (match) {
      const start = match.index;
      let depth = 0, i = start;
      for (; i < uiSource.length; i++) {
        if (uiSource[i] === "{") depth++;
        else if (uiSource[i] === "}") { depth--; if (depth === 0) { i++; break; } }
      }
      extracted.push(uiSource.slice(start, i));
      found = true;
      break;
    }
  }
  if (!found) throw new Error("function not found in app.js: " + name);
}
const state = { knowledgeDetail: null, knowledgeDetailLoading: false };
const suite = {
  renderKnowledgeDetailWith(detail) {
    state.knowledgeDetail = detail;
    return renderKnowledgeDetail();
  },
};
eval(extracted.join("\n"));
const payload = JSON.parse(fs.readFileSync(process.env.DETAIL_FIXTURE, "utf-8"));
const html = suite.renderKnowledgeDetailWith(payload);
process.stdout.write(html);
'''


def _render_detail_in_node(detail: dict) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fixture:
        json.dump(detail, fixture)
        fixture_path = fixture.name
    env = dict(os.environ, DETAIL_FIXTURE=fixture_path)
    result = subprocess.run(
        [NODE_BIN, "-e", RENDER_HARNESS],
        capture_output=True, text=True, timeout=30, cwd=os.getcwd(), env=env,
    )
    os.unlink(fixture_path)
    if result.returncode != 0:
        raise AssertionError(f"render harness failed: {result.stderr[:800]}")
    return result.stdout


def _detail_for(monkeypatch, mutate) -> dict:
    client, _store = _client_and_store()
    repository = _seed_repository()
    promotion_id = _promotion_id(repository)
    _patch_repository(client, monkeypatch, repository)
    _login(client)
    mutate(repository)
    response = client.get(
        f"/automation/production/dashboard/api/knowledge/promotions/{promotion_id}"
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_failed_summary_reason_reaches_the_dom(monkeypatch) -> None:
    """B7: failed + error="boom" renders BOTH the failed state and the boom
    reason (not a bare 'failed' label), with the danger tone."""
    def mutate(repository):
        repository._standalone_summary_tasks[_summary_task_id()]["status"] = "failed"
        repository._standalone_summary_tasks[_summary_task_id()]["error"] = "boom"

    detail = _detail_for(monkeypatch, mutate)
    assert detail["summary"]["state"] == "failed"
    assert detail["summary"]["failure_reason"] == "boom"
    html = _render_detail_in_node(detail)
    assert "Summary" in html
    assert "failed" in html
    assert "boom" in html, "the failure reason must be visible in the page"
    assert 'tone-danger' in html, "the failed state uses the danger tone"


def test_malformed_summary_reason_reaches_the_dom(monkeypatch) -> None:
    def mutate(repository):
        repository._standalone_summary_tasks[_summary_task_id()]["packet"] = "not-a-dict"

    detail = _detail_for(monkeypatch, mutate)
    assert detail["summary"]["state"] == "malformed"
    assert detail["summary"]["malformed_reason"] == "summary_packet_missing_or_not_object"
    html = _render_detail_in_node(detail)
    assert "malformed" in html
    assert "summary_packet_missing_or_not_object" in html


def test_healthy_detail_still_renders(monkeypatch) -> None:
    detail = _detail_for(monkeypatch, lambda repository: None)
    html = _render_detail_in_node(detail)
    assert "Summary packet" in html
    assert "Review report" in html
    assert "boom" not in html
