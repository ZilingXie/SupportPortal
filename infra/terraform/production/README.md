# ECS Production Foundation

This root provisions the cost-first foundation for the SupportPortal ECS
Production endpoint:

```text
supportcenter.stellarix.space/automation/production/* -> ALB -> ECS Fargate
support.stellarix.space/production/*                  -> existing EC2 backup
```

It deliberately does not manage the Cloudflare DNS record. ACM DNS validation
records are exposed as Terraform output so the DNS owner can add them without
putting Cloudflare credentials in Terraform state.

## Scope

The default apply creates the platform resources only:

- one immutable, scan-on-push `supportportal/production` ECR repository; API,
  Route and Worker remain separate images identified by role tag and digest;
- ECS cluster and Fargate roles;
- public ALB, target group and `/automation/production*` listener rule;
- CloudWatch log groups;
- production runtime Secret Manager names without secret values;
- one cost-first TLS Redis node and its generated AUTH token;
- encrypted One Zone EFS access point mounted by the Worker for the mutable
  Graph token cache;
- a Worker-only mount of the existing Pilot credential EFS access point, with
  a dedicated Worker task role with the existing Graph EFS permission plus a
  separate Pilot Access Point-scoped ClientMount/ClientWrite policy;
- versioned, private S3 release-manifest bucket;
- GitHub Actions OIDC provider and restricted release role.

`enable_services` defaults to `false`. Do not enable it until the Account
parity Release Manifest and immutable release digests are available and the
Secrets Manager values have been populated. The optional services are three
independent Fargate tasks: the Intake API runs
`backend.automation_ecs_api:create_app --factory`, the Route Worker runs
`backend.automation_ecs_route_worker`, and the Automation Worker runs
`backend.automation_ecs_worker`. All task definitions require `X86_64` and
images in `supportportal/production` pinned as `repository@sha256:digest`.

The stack uses the existing VPC `vpc-0125f57b2ec2f0423` and discovers public
subnets when `public_subnet_ids` is empty. It assigns public IPs to Fargate
tasks because this test VPC has no NAT Gateway. The API security group only
accepts traffic from the ALB security group; the Worker is not attached to
the ALB. ALB and target 5xx alarms are created without notification actions
because no alert destination has been configured. The optional ECS
running-task alarm is created only when `enable_container_insights=true`.

The cost-first EFS file system is One Zone in `efs_availability_zone_name`
(`us-east-1b` by default). Only the EFS-dependent Worker is pinned to that
Availability Zone; API, Route and ALB retain the multi-AZ subnet set. The
selected public subnet list must include the configured EFS Availability Zone.

The ALB listener matches `/automation/production*` and forwards the original
URI unchanged. The Intake API exposes the prefixed contract directly. Both
the ALB and container health check use
`/automation/production/health/live`; readiness remains a separate check that
also requires matching Route/Worker heartbeats and release provenance.

## Bootstrap

Terraform itself is not managed by this root. Use a separate encrypted remote
state bucket before applying the production root. A small bootstrap root is
provided in `../bootstrap` for that purpose.

```bash
cd infra/terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

After the bucket is created, copy `backend.tf.example` to an untracked
`backend.tf`, replace the placeholders, and configure the S3 backend before
the first production apply. Do not commit `terraform.tfvars` or a backend
config containing account-specific state details.

## Foundation apply sequence

1. Authenticate to the intended AWS account and select `us-east-1`.
2. Copy `terraform.tfvars.example` to an untracked `terraform.tfvars` and
   verify the VPC, RDS security group and subnet selection.
3. Run `terraform init`, `terraform fmt -check` and `terraform validate`.
4. Apply with `enable_services=false`.
5. Add the output ACM validation CNAME in Cloudflare and wait for the
   certificate status to become `ISSUED`.
6. Re-apply with `enable_https_listener=true`.
7. Populate the runtime secrets out of band. Never put their values in Git,
   Terraform variables, task definitions, Release Manifests or logs. Long-lived
   tasks receive only the runtime DSN; the migration DSN remains bootstrap-only.
8. Verify the One Zone EFS mount, Worker subnet and token-cache path during the
   first task startup; the EFS filesystem is not shared with the EC2 backup.
9. Only after the Production Release Manifest exists, set the three immutable
   digest references plus `release_id`, `git_commit`, `build_time` and
   `prompt_release_id`. Enable Zendesk side effects only for the controlled
   Production Case test, then set `enable_services=true`.

Before enabling services, set `pilot_efs_file_system_id` and
`pilot_efs_access_point_id` to the existing Pilot credential store. The Pilot
mount is independent from the Terraform-managed Graph token cache and is never
attached to the API or Route task definitions.

This root is currently a verifiable configuration source only. Review and
import the manually created `supportportal/production` repository into state
before a future apply; do not apply a state transition that would delete
existing role repositories or release images.

No command in this directory changes `support.stellarix.space`, the existing
EC2 deployment, n8n workflows, or any customer-facing Case.
