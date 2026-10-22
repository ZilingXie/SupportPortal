# Controlled metadata readiness publication (v4)

Plan: [investigation-case-files-slack-ingress-metadata-v1](../../docs/plans/investigation-case-files-hermes-metadata-readiness.md).
This tooling does not enable business tests. Independent code acceptance precedes
merge and cloud mutation; independent deployment acceptance precedes readiness.

## Local reproduction

Use the existing immutable candidate image, without building, pulling or AWS calls:

```sh
python3 deployment/hermes-case-files/verify_metadata_efs_local.py \
  --image 891612554546.dkr.ecr.us-east-1.amazonaws.com/supportportal/hermes:hermes-20261022-11ef7856-metadata-v1 \
  --expected-image-id faec191f4f1af5a24a40010717ebd3ea12ed9b0ddf9b6117ef489d0414d1eddb \
  --output /absolute/path/to/fresh-attempt
```

The launcher starts the existing Podman VM if necessary, verifies image identity,
runs that image ID with `--pull never --network none`, and mounts scripts/fixture
read-only. Each attempt needs a fresh output directory. Inspect exit status,
`container-verification.log`, `container-verification.json`, and exactly one
`METADATA_EFS_LOCAL_PASSED` marker. A partial log or absent marker is failure.
Image source labels, local RepoDigests and deployment ECR manifest digest are
different evidence fields; do not substitute one for another.

The versioned old fixture is byte-identical to the retained TD54 image export
`/tmp/cf-rebuild/td54/deploy-ecs/case-files-skill/SKILL.md` (1937 bytes), SHA-256
`7379499fc8412b4a52cd5ad82a31f74ae580ae02c8577ef4eb2a7c16910ebc02`.
The container uses the real unmodified loader and a temporary `HERMES_HOME`;
old metadata, wrong content hash and wrong source path must fail at their named
gates. Original-input publication, repeat without inode replacement, and rollback
must succeed. This is local filesystem/loader proof, not live EFS acceptance.

## ECS execution after code acceptance

First independently read back account/region, service/task, EFS filesystem/access
point/root and ownership, current file hash/stat, neighboring configuration,
health, and absence of concurrent rollout/publication. The CLI checks ECS task
metadata; it does not replace the external EFS AP/deployment checks.

Transfer only reviewed scripts through the existing ECS Exec channel and verify
their hashes. In the verified Preproduction Hermes task, run the image Python:

```sh
python /tmp/publish_skill.py --action check-only \
  --release-id APPROVED_RELEASE_ID \
  --expected-task-arn VERIFIED_PREPRODUCTION_TASK_ARN \
  --expected-old-sha256 7379499fc8412b4a52cd5ad82a31f74ae580ae02c8577ef4eb2a7c16910ebc02 \
  --expected-new-sha256 bce133eb503042e75f67b7d512877fa4a2efccc2af75766c2fe15a00b330154a
```

Check-only is the default and creates no lock/record/backup. Use the same explicit
inputs with `--action apply` only after the image stage and all prechecks pass.
The target is fixed `/opt/data/skills/support-case-files/SKILL.md`; candidate bytes
come only from `/opt/hermes/case-files-skill/SKILL.md`. UID/GID10000 and mode0644
are preserved. Backups and records live outside the skills search tree under
`/opt/data/.supportportal-skill-releases/support-case-files/RELEASE_ID/`.
Require exit0 plus `SKILL_PUBLICATION_JSON` with the expected state/hash.

After apply, run `python /tmp/probe_metadata.py --require-description` without
local-fixture overrides. Require its `METADATA_PROBE_JSON`, actual EFS path,
served/file/bundled hashes, schema and list/direct-view isolation. Complete the
remaining Gateway/health/ALB/sidecar/Terraform and unchanged-service gates in the
plan. Record image source (`11ef7856…`) separately from merged v4 tooling source.
Do not rewrite the original immutable release or v3 failure/rollback artifacts.

## Recovery

`prepared`/`outcome_unknown` are not success. Read target bytes and operation
record with a new check-only call first. If target is new, the original apply
confirms committed without replacing it; if old, the original operation can
continue. A pending rollback must instead resume rollback. `rolled_back` cannot
be reactivated with apply. Contradictory state, third-party bytes, symlinks,
changed attributes or bad backup fail closed; do not overwrite or delete evidence.
The flock coordinates participating publishers only, not arbitrary external
writers. The executor must retain exclusive ownership of the release window.

Check-only returns `ok=false`, code `publication_unresolved`, exit1 for both
`prepared` and `outcome_unknown`, regardless of whether target bytes are old or
new. Its JSON retains state, pending_action and current hash/stat for readback;
it does not mutate the record or confirm success. Only explicit apply/rollback
may resolve that observation. Terminal state/target/intent contradictions return
`state_conflict` and exit1. Baseline, consistent committed and consistent
rolled_back states remain successful read-only checks.

On an authorized rollback, repeat the exact release/task/hash inputs with
`--action rollback`; it restores backup bytes and attributes atomically, or
confirms the old state. Then restore the preflight task definition and complete
fresh readback as specified by the plan. Unknown outcomes require readback;
ownership conflicts stop automatic recovery and require the human decision.
