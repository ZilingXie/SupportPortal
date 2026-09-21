---
name: supportportal-production-hotfix
description: "Run a narrowly scoped, fail-closed SupportPortal Production emergency hotfix from an explicitly reviewed commit, preserving immutable release, Terraform, Prompt, ECS health, rollback, and evidence gates. Use when an urgent Production fix must ship without mixing unrelated main changes."
---

# SupportPortal Production Hotfix

Use this skill only for an explicitly authorized urgent Production hotfix. It is a release workflow guard, not a shortcut around the normal deployment controls.

## Goal and boundary

The release must contain the reviewed hotfix commit and no unrelated changes from `main`. The safe meaning of “only deploy the hotfix” is:

- build the complete application tree at the reviewed hotfix SHA into the normal three immutable `linux/amd64` role images;
- prove that every path between the pinned Production baseline and the hotfix is in an explicit allowlist;
- promote or reuse only the exact manifest, Prompt candidate, and release checkpoint bound to that identity tuple;
- let the existing pipeline perform Terraform remote-state locking, Prompt reconciliation, ECS rollout, health/heartbeat, CloudWatch, rollback, and evidence checks.

Never patch files into a running container, copy a local state file, use `terraform -lock=false`, manually update one ECS service, rebuild a Production-only image, or infer role scope from a filename. Shared imports can make a small source diff affect API, Route, and Worker; unless a separately verified role-scoped pipeline exists, preserve the normal three-role provenance tuple.

This skill changes developer tooling only. It does not authorize a deployment or any customer-facing replay. Production authorization, AWS permissions, and business-side acceptance remain separate decisions.

## Required inputs

Collect and record the following before any mutating command:

| Input | Required contract |
| --- | --- |
| Production baseline | Full 40-character SHA currently running in Production, independently read back from the release/health evidence. |
| Hotfix SHA | Full 40-character SHA explicitly reviewed and authorized by the operator. |
| Scope | One or more `--allow` paths passed to `scripts/verify_hotfix_scope.sh`; exact paths and directory prefixes are supported. |
| Prompt identity | Existing Prompt candidate ID plus its build ref and complete content fingerprint. A code-only hotfix must prove this fingerprint is unchanged. |
| Release identity | Existing release ID/checkpoint, or a new release ID created by the formal pipeline. Never overwrite an immutable release. |
| Environment | `us-east-1`, account `891612554546`, and the canonical Production ECS/health endpoints. |

The identity tuple is `(baseline_sha, hotfix_sha, prompt_release_id, prompt_build_ref, prompt_content_fingerprint, release_id, role_digests)`. Any mismatch stops the workflow and requires reconciliation; do not silently substitute `main`, another Prompt candidate, or another release.

## Ordered workflow

1. **Freeze and inspect.** Work from a clean detached worktree at the exact hotfix SHA. Confirm the baseline is the live Production lineage and the hotfix descends from it. Run the scope verifier before CodeBuild:

   ```bash
   bash .codex/skills/supportportal-production-hotfix/scripts/verify_hotfix_scope.sh \
     --repo /absolute/path/to/hotfix-worktree \
     --baseline <production-baseline-full-sha> \
     --hotfix <reviewed-hotfix-full-sha> \
     --allow backend/services/account_verification_automation.py \
     --allow backend/tests/test_account_verification_automation.py
   ```

   Treat the printed changed-path/status list as release evidence. A path outside the allowlist, dirty worktree, non-descendant SHA, abbreviated SHA, or HEAD mismatch is a hard stop.

2. **Run targeted checks.** Execute the hotfix unit/regression tests and the repository-required static checks from the hotfix worktree. Do not claim business acceptance from local tests; real customer traffic and external side effects require a separately controlled acceptance.

3. **Prepare the formal release.** Set `AUTOMATION_RELEASE_HOTFIX_AUTHORIZED=<hotfix_sha>` and pass `--hotfix-baseline <baseline_sha>` at every applicable gate. For the pipeline, use `--codebuild-direct-production` only when the user has explicitly authorized an urgent Production hotfix and the normal Preproduction ECS step is intentionally skipped. Pass the clean hotfix release worktree through `--prompt-code-root` for Prompt validation. The existing tooling still requires the full hotfix tree and builds all three immutable role artifacts; the scope verifier is the additional guard against unrelated changes.

4. **Reuse before creating.** If the existing release manifest, Prompt candidate, promotion record, and checkpoint match the identity tuple and their remote artifacts are immutable, use the formal `--resume` path. Do not rebuild, republish, delete ECR images, or create a second candidate merely because the previous attempt stopped at a read-only preflight. If any identity or artifact fingerprint differs, stop and create a new release through the formal pipeline.

5. **Run Production preflight.** The operator identity must be able to read the Production Terraform state and acquire the DynamoDB lock. Minimum remote-state permissions are `s3:HeadObject` and `s3:GetObject` on `arn:aws:s3:::supportportal-terraform-state-891612554546-us-east-1/supportportal/ecs-production/terraform.tfstate`, plus `dynamodb:GetItem`, `dynamodb:PutItem`, and `dynamodb:DeleteItem` on `arn:aws:dynamodb:us-east-1:891612554546:table/supportportal-terraform-locks`. Do not bypass these with local state or `-lock=false`. A 403 is a deployment blocker, not a reason to weaken the preflight.

6. **Deploy through the existing gate.** Keep Prompt fingerprint/content unchanged for a code-only fix. Preserve the existing order: Terraform zero drift, Prompt candidate validation/sync, Route and Worker rollout, fresh heartbeat/provenance, API rollout, public health, CloudWatch, EC2 backup, and only then Prompt activation/readback. The canonical ECS health base is `https://supportcenter.stellarix.space/automation/production/health`; the legacy `support.stellarix.space` surface returning `410` is not evidence for ECS Production.

7. **Resume or rollback conservatively.** On interruption, resume only from the same release-scoped checkpoint after revalidating all bound inputs and live ECS revisions/digests. Before Prompt activation, formal rollback restores updated services in reverse order. Once activation has started or its result is unknown, stop and reconcile the target Prompt state; do not blindly retry activation or roll back a healthy new stack.

8. **Read back and record.** Verify the exact release/commit/digests/Prompt fingerprint, all three services at `1/1/0` with completed deployments, fresh Route/Worker heartbeat, public `live/release/ready`, zero unexpected CloudWatch errors, Terraform zero drift, and rollback status. Preserve secret-free evidence and update the versioned `docs/release_notes.json` required for every authorized Production ECS deployment. A release is not complete while AWS preflight is blocked or any required readback is unverified.

## Explicit non-goals

- Do not merge the hotfix branch back into `main` as part of the emergency release. `main` remains the source of truth; a later full `main` release supersedes the temporary hotfix lineage.
- Do not change Prompt content, schema, n8n workflows, secrets, Terraform ownership, or unrelated ECS services unless separately approved and covered by their own gates.
- Do not send a real ticket, email, Slack message, or customer reply as deployment proof. Keep functional acceptance separate from infrastructure verification.

See [release-contract.md](references/release-contract.md) for the compact identity and gate contract. The scope checker is intentionally standalone and has no AWS or database side effects.
