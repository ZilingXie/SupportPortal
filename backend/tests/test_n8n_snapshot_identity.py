import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.n8n.validate_workflow_snapshots import validate


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS = ROOT / "docs/integrations/n8n/workflows"
HISTORICAL_COMMIT = "4ecea0431f1b84490bab4bf51220faeef815e6b2"
WORKFLOWS = ["GgDxPEWtW7ltT5BW", "MM3Z3T469Eru3Q1I", "r1HIW8UNuCabiOPn"]


def test_restored_history_is_byte_exact_and_local_candidates_are_separate():
    for workflow_id in WORKFLOWS:
        for kind, directory in (("published", "active"), ("draft", "drafts")):
            path = SNAPSHOTS / directory / f"{workflow_id}.{kind}.json"
            original = subprocess.check_output([
                "git", "show", f"{HISTORICAL_COMMIT}:{path.relative_to(ROOT)}",
            ], cwd=ROOT)
            assert path.read_bytes() == original
    assert validate(SNAPSHOTS) == (15, 4, 56)


@pytest.mark.parametrize("workflow_id", WORKFLOWS)
@pytest.mark.parametrize("kind,field", [
    ("published", "versionId"), ("published", "activeVersionId"),
    ("draft", "versionId"), ("draft", "activeVersionId"),
])
def test_validator_rejects_internal_remote_version_conflicts(tmp_path, workflow_id, kind, field):
    root = tmp_path / "snapshots"
    shutil.copytree(SNAPSHOTS, root)
    directory = "active" if kind == "published" else "drafts"
    path = root / directory / f"{workflow_id}.{kind}.json"
    snapshot = json.loads(path.read_text())
    snapshot["workflow"][field] = "different-remote-version"
    path.write_text(json.dumps(snapshot))
    with pytest.raises(ValueError, match=f"workflow.{field}"):
        validate(root)


@pytest.mark.parametrize("workflow_id", WORKFLOWS)
def test_consistent_internal_versions_are_accepted(tmp_path, workflow_id):
    root = tmp_path / "snapshots"
    shutil.copytree(SNAPSHOTS, root)
    manifest = json.loads((root / "manifest.json").read_text())
    item = next(i for i in manifest["workflows"] if i["workflowId"] == workflow_id)
    for field, kind in (("publishedFile", "published"), ("draftFile", "draft")):
        path = root / item[field]
        snapshot = json.loads(path.read_text())
        snapshot["workflow"]["versionId"] = item[f"{kind}VersionId"]
        snapshot["workflow"]["activeVersionId"] = item["publishedVersionId"]
        path.write_text(json.dumps(snapshot))
    assert validate(root) == (15, 4, 56)


@pytest.mark.parametrize("location,field", [
    ("source", "versionId"), ("source", "publishedVersionId"),
    ("workflow", "versionId"), ("workflow", "activeVersionId"),
])
def test_local_candidate_cannot_claim_published_identity(tmp_path, location, field):
    root = tmp_path / "snapshots"
    shutil.copytree(SNAPSHOTS, root)
    path = root / "candidates/GgDxPEWtW7ltT5BW.candidate.json"
    candidate = json.loads(path.read_text())
    candidate[location][field] = "claimed-published-version"
    path.write_text(json.dumps(candidate))
    with pytest.raises(ValueError):
        validate(root)


def test_local_candidate_cannot_use_historical_delivery_host(tmp_path):
    root = tmp_path / "snapshots"
    shutil.copytree(SNAPSHOTS, root)
    path = root / "candidates/GgDxPEWtW7ltT5BW.candidate.json"
    candidate = json.loads(path.read_text())
    node = next(n for n in candidate["workflow"]["nodes"] if n["name"] == "Deliver Source Snapshot")
    node["parameters"]["url"] = "https://support.stellarix.space/automation/preproduction/v1/knowledge/sources"
    path.write_text(json.dumps(candidate))
    with pytest.raises(ValueError, match="not in the source-only allowlist"):
        validate(root)


@pytest.mark.parametrize("mutation", ["base", "manifest", "source", "nodes", "unpublished"])
def test_validator_rejects_other_identity_and_content_metadata_conflicts(tmp_path, mutation):
    root = tmp_path / "snapshots"
    shutil.copytree(SNAPSHOTS, root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    item = next(i for i in manifest["workflows"] if i["workflowId"] == WORKFLOWS[0])
    if mutation == "base":
        item["candidateBaseVersionId"] = "other-base"
    elif mutation == "manifest":
        item["draftVersionId"] = "other-draft"
    elif mutation == "nodes":
        item["nodeCount"] += 1
    else:
        path = root / (item["publishedFile"] if mutation == "source" else item["candidateFile"])
        snapshot = json.loads(path.read_text())
        if mutation == "source":
            snapshot["source"]["publishedVersionId"] = "other-published"
        else:
            snapshot["restoreNotes"]["publicationStatus"] = "published"
        path.write_text(json.dumps(snapshot))
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        validate(root)
