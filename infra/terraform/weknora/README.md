# WeKnora Standalone Infrastructure (Phase One)

This root owns the stable WeKnora standalone infrastructure: dedicated ECS
cluster `supportportal-weknora` (Fargate + a single-instance EC2 capacity
provider for ParadeDB/Redis), immutable ECR repository `supportportal/weknora`,
the `/dashboard/weknora` listener rule and frontend target group, WeKnora
security groups, log group, private CloudMap namespace
`weknora.supportportal.local`, docs/backup buckets, IAM roles (execution, app
task, ops task, CodeBuild), and the dedicated image-build CodeBuild project.
Shared VPC and ALB HTTPS listener are inputs; no existing resource in this
account is modified or replaced. SecureString values are never Terraform
resources or variables.

Bootstrap is staged without `terraform -target`:

1. Create ignored `backend.tf` from `backend.tf.example` and `terraform.tfvars`
   from live AWS readback (see `docs/deploy_weknora_standalone_ecs.md` for the
   current baseline).
2. Apply the foundation plan with `create_services=false` (add-only; verify no
   existing service, listener rule, or storage resource appears in the plan).
3. Build images from the pinned source archive with the CodeBuild project.
4. Register the initial task definitions with
   `deployment/weknora/register_weknora_initial_task_definitions.sh`.
5. Put its generated task-definition ARN map in the ignored tfvars, set
   `create_services=true`, and apply the second add-only plan.
6. Deploy in dependency order (data services first, frontend last), then
   require a zero-drift plan.

The release deploy script owns later task-definition revisions and service
pointers. Terraform ignores only `task_definition`.
