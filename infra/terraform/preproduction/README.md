# Automation Preproduction Infrastructure

This root owns the stable Preproduction Automation infrastructure: cluster,
immutable ECR repository, target group and `/automation/preproduction` listener
rule, security group, log group, isolated Account and Hermes task roles, shared
execution role, isolated Graph EFS access point, private Hermes service discovery, migration-backup bucket, and
the API/Route/Worker services. Shared VPC, ALB, RDS, ACM, and EFS are inputs.
SecureString values are never Terraform resources or variables.

Bootstrap is intentionally staged without `terraform -target`:

1. Create ignored `backend.tf` and `terraform.tfvars` from live AWS readback.
2. Keep `create_account_services=false`; review and apply an add-only foundation plan.
3. Build the fixed SupportPortal commit with CodeBuild.
4. Register canonical initial task definitions with
   `deployment/register_automation_ecs_initial_task_definitions.sh`.
5. Put its generated task-definition ARN map in the ignored tfvars, set
   `create_account_services=true`, and apply the second add-only plan.
6. Run the formal Preproduction deploy command, then require a zero-drift plan.

The release deploy script exclusively owns later task-definition revisions and
service pointers. Terraform ignores only `task_definition`; network, count,
load balancer, deployment, and tagging drift remain visible.
