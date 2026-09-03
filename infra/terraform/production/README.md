# Automation Production Stable Infrastructure

This Terraform root is an import-only ownership boundary for the stable
Automation Production infrastructure. Immutable task-definition revisions and
the ECS services' `task_definition` pointers are exclusively owned by
`deployment/deploy_automation_ecs_release.sh`.

## Managed Resources

Only these existing resources are imported and managed:

- `supportportal/production` ECR repository;
- the Automation API target group;
- the shared HTTPS listener's priority `10` Automation path rule;
- API, Route, and Worker ECS service configuration, excluding each service's
  `task_definition` pointer.

The services continue to detect drift in desired count, network configuration,
load balancer attachment, platform version, deployment percentages, and circuit
breaker configuration. Only `task_definition` is ignored.

## External Shared Resources

The ECS cluster, ALB and HTTPS listener, ACM certificate, security groups,
single CloudWatch log group, SSM parameters, execution/task roles, Graph EFS,
Redis, and Hermes configuration are data sources or required inputs. This root
does not create, update, or delete them. It also contains no Pilot resources,
task definitions, Secrets Manager resources, ECR lifecycle policy, OIDC
provider, release-manifest S3 bucket, or CloudWatch alarms.

## Remote State Bootstrap

Create the encrypted, versioned S3 state bucket and DynamoDB lock table once:

```bash
cd infra/terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan -out bootstrap.tfplan
terraform apply bootstrap.tfplan
```

Then create an untracked `infra/terraform/production/backend.tf` from
`backend.tf.example` using the actual bootstrap outputs. Never commit account
details, state, plan files, or credentials.

## Import And Zero-Drift Gate

Populate an untracked `terraform.tfvars` from live readback, not from historical
defaults. Initialize the remote backend, then import each existing resource:

```bash
terraform init -reconfigure
terraform import aws_ecr_repository.runtime supportportal/production
terraform import aws_lb_target_group.automation <automation-target-group-arn>
terraform import aws_lb_listener_rule.automation_https <priority-10-rule-arn>
terraform import 'aws_ecs_service.automation["api"]' supportportal-production/supportportal-production-api
terraform import 'aws_ecs_service.automation["route"]' supportportal-production/supportportal-production-route
terraform import 'aws_ecs_service.automation["worker"]' supportportal-production/supportportal-production-worker
```

Before any Terraform apply or ECS release, require a real refresh plan:

```bash
terraform plan -detailed-exitcode -input=false -no-color
```

The only acceptable result is exit `0` with `0 to add, 0 to change, 0 to
destroy`. Exit `1` is an error; exit `2` is drift and blocks both apply and
`deployment/deploy_automation_ecs_release.sh`.

After every ECS release, the same plan must still return exit `0`. A task
definition revision change must not appear because the release script owns that
pointer and the service lifecycle ignores only `task_definition`.

No command in this root changes EC2 `/production`, n8n, Cloudflare, customer
tickets, or Prompt Release state.
