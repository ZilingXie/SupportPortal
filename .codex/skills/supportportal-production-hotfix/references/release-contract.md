# Production Hotfix Release Contract

This contract is the minimum evidence bundle for a SupportPortal emergency Production hotfix.

## Identity

```text
baseline_sha               = exact live Production Git SHA
hotfix_sha                 = exact reviewed hotfix Git SHA
prompt_release_id          = existing candidate/active release ID
prompt_build_ref           = Prompt build identity
prompt_content_fingerprint = complete content fingerprint (normally unchanged)
release_id                 = immutable ECS release ID
api_digest                 = immutable API image digest
route_digest               = immutable Route image digest
worker_digest              = immutable Worker image digest
```

All values are compared as a tuple. A partial match is not a safe reuse decision.

## Source gate

The hotfix SHA must be a descendant of `baseline_sha`, the release worktree must be clean and detached at that SHA, and `verify_hotfix_scope.sh` must show that every changed path in `baseline_sha..hotfix_sha` matches an explicitly supplied `--allow` path or directory prefix. The scope list is an allowlist, not a description: an omitted path fails closed.

## Artifact gate

The formal pipeline receives `--hotfix-baseline <baseline_sha>` and the environment variable `AUTOMATION_RELEASE_HOTFIX_AUTHORIZED=<hotfix_sha>`. It must produce or reuse one immutable manifest and one matching publish/promotion record. The release manifest, remote ECR digests, Prompt fingerprint, and checkpoint must agree. A local tarball or copied state is not a release artifact.

## Infrastructure gate

The formal Production deploy performs a real Terraform refresh/plan against the locked remote state and requires zero drift. The operator needs S3 state read and DynamoDB lock permissions. No `-lock=false`, local state, `terraform apply`, manual task-definition edits, or direct ECS service mutation is permitted by this contract.

## Runtime gate

The normal deploy gate remains intact: Route/Worker before API, service rollout convergence, digest/provenance, fresh heartbeat, public `live/release/ready`, CloudWatch error window, EC2 backup, Prompt validation, and activation/readback. The three role images are provenance-consistent even when the source diff is small.

## Recovery gate

`--resume` is valid only for the same immutable release and checkpoint after live identity revalidation. Pre-activation failures use formal reverse-order rollback. Activation uncertainty becomes `reconciliation_required`; it is never hidden by retrying or by reporting an incomplete rollback as successful.

## Evidence gate

Evidence must be secret-free and include source scope output, commit/release/Prompt/digest identity, Terraform result, health/heartbeat/CloudWatch/backup result, activation state, and rollback status. A blocked permission check or missing readback means Production remains unchanged or its state is explicitly unknown; report that boundary rather than claiming deployment success.
