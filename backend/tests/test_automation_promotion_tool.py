from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_promotion_copies_manifests_and_layers_and_verifies_identical_digests_without_building() -> None:
    script = (ROOT / "deployment/promote_automation_release.sh").read_text(encoding="utf-8")
    assert 'SOURCE_REPOSITORY="supportportal-preproduction"' in script
    assert 'TARGET_REPOSITORY="supportportal-production"' in script
    assert "batch-get-image" in script
    assert "crane copy" in script
    assert "put-image" not in script
    assert '[[ "${target_digest}" = "${expected}" ]]' in script
    assert "automation-promotion-v1" in script
    assert "docker build" not in script
    assert "buildx" not in script
    assert 'PROMOTION_RECORD="${PROMOTION_RECORD:-${manifest_dir}/promotion-record.json}"' in script
