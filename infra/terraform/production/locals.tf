data "aws_caller_identity" "current" {}

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Component   = "ecs-foundation"
  }

  candidate_public_subnet_ids = length(var.public_subnet_ids) > 0 ? var.public_subnet_ids : data.aws_subnets.public.ids

  public_subnet_ids_by_az = {
    for subnet in data.aws_subnet.selected :
    subnet.availability_zone => subnet.id...
  }

  # The ALB accepts one subnet per Availability Zone.
  public_subnet_ids = [
    for availability_zone in sort(keys(local.public_subnet_ids_by_az)) :
    local.public_subnet_ids_by_az[availability_zone][0]
  ]

  # The cost-first EFS file system is One Zone, so only the EFS-dependent
  # Worker may run in the matching subnet. API and Route remain multi-AZ.
  efs_subnet_id = try(
    local.public_subnet_ids_by_az[var.efs_availability_zone_name][0],
    "",
  )
  efs_mount_target_subnets = local.efs_subnet_id == "" ? {} : {
    (var.efs_availability_zone_name) = local.efs_subnet_id
  }

  ecr_repository_name = "${var.project_name}/${var.environment}"

  runtime_secret_names = {
    ticket_db_dsn               = "${local.name_prefix}/ticket-db-dsn"
    migration_db_dsn            = "${local.name_prefix}/migration-db-dsn"
    automation_intake_token     = "${local.name_prefix}/automation-intake-token"
    dashboard_admin_username    = "${local.name_prefix}/dashboard-admin-username"
    dashboard_admin_password    = "${local.name_prefix}/dashboard-admin-password"
    dashboard_session_secret    = "${local.name_prefix}/dashboard-session-secret"
    n8n_request_token           = "${local.name_prefix}/n8n-request-token"
    rag_service_url             = "${local.name_prefix}/rag-service-url"
    rag_service_token           = "${local.name_prefix}/rag-service-shared-token"
    zendesk_basic_auth          = "${local.name_prefix}/zendesk-basic-auth"
    zendesk_ai_assignee_email   = "${local.name_prefix}/zendesk-ai-assignee-email"
    zendesk_fraud_assignee_id   = "${local.name_prefix}/zendesk-fraud-review-assignee-id"
    openai_api_key              = "${local.name_prefix}/openai-api-key"
    account_slack_webhook_url   = "${local.name_prefix}/account-slack-n8n-webhook-url"
    account_slack_status_url    = "${local.name_prefix}/account-slack-n8n-status-url"
    engineer_slack_access_token = "${local.name_prefix}/engineer-slack-access-token"
    engineer_slack_team_id      = "${local.name_prefix}/engineer-slack-team-id"
    engineer_slack_channel_id   = "${local.name_prefix}/engineer-slack-channel-id"
    billing_graph_tenant_id     = "${local.name_prefix}/billing-graph-tenant-id"
    billing_graph_client_id     = "${local.name_prefix}/billing-graph-client-id"
    billing_graph_client_secret = "${local.name_prefix}/billing-graph-client-secret"
    billing_graph_username      = "${local.name_prefix}/billing-graph-username"
  }

  manifest_bucket_name = trimspace(var.manifest_bucket_name) != "" ? trimspace(var.manifest_bucket_name) : "${var.project_name}-${var.environment}-release-manifests-${data.aws_caller_identity.current.account_id}"

  certificate_arn = trimspace(var.acm_certificate_arn) != "" ? trimspace(var.acm_certificate_arn) : try(aws_acm_certificate.production[0].arn, "")

  redis_auth_token = try(random_password.redis_auth[0].result, "")

  image_digests = {
    api    = try(split("@", var.api_image)[1], "unreleased")
    route  = try(split("@", var.route_image)[1], "unreleased")
    worker = try(split("@", var.worker_image)[1], "unreleased")
  }

  base_environment = [
    {
      name  = "AUTOMATION_ENVIRONMENT"
      value = var.environment
    },
    {
      name  = "AUTOMATION_BASE_PATH"
      value = "/automation/${var.environment}"
    },
    {
      name  = "AUTOMATION_DB_RESOURCE_ID"
      value = local.name_prefix
    },
    {
      name  = "AUTOMATION_DB_SCHEMA"
      value = "supportportal_production"
    },
    {
      name  = "AUTOMATION_JOB_NAMESPACE"
      value = "supportportal-production"
    },
    {
      name  = "TICKET_DB_SCHEMA"
      value = "supportportal_production"
    },
    {
      name  = "AUTOMATION_RELEASE_ID"
      value = var.release_id
    },
    {
      name  = "PROMPT_RELEASE_ID"
      value = var.prompt_release_id
    },
    {
      name  = "APP_BUILD_REF"
      value = var.git_commit
    },
    {
      name  = "APP_BUILD_TIME"
      value = var.build_time
    },
    {
      name  = "PROMPT_RELEASE_REQUIRED"
      value = "true"
    },
    {
      name  = "RUNTIME_SCHEMA_MODE"
      value = "check"
    },
  ]

  api_secrets = [
    {
      name      = "AUTOMATION_DB_DSN"
      valueFrom = aws_secretsmanager_secret.runtime["ticket_db_dsn"].arn
    },
    {
      name      = "AUTOMATION_INTAKE_SHARED_TOKEN"
      valueFrom = aws_secretsmanager_secret.runtime["automation_intake_token"].arn
    },
    {
      name      = "AUTOMATION_DASHBOARD_ADMIN_USERNAME"
      valueFrom = aws_secretsmanager_secret.runtime["dashboard_admin_username"].arn
    },
    {
      name      = "AUTOMATION_DASHBOARD_ADMIN_PASSWORD"
      valueFrom = aws_secretsmanager_secret.runtime["dashboard_admin_password"].arn
    },
    {
      name      = "AUTOMATION_DASHBOARD_SESSION_SECRET"
      valueFrom = aws_secretsmanager_secret.runtime["dashboard_session_secret"].arn
    },
  ]

  role_db_secrets = [
    {
      name      = "AUTOMATION_DB_DSN"
      valueFrom = aws_secretsmanager_secret.runtime["ticket_db_dsn"].arn
    },
    {
      name      = "TICKET_DB_DSN"
      valueFrom = aws_secretsmanager_secret.runtime["ticket_db_dsn"].arn
    },
  ]

  route_secrets = concat(local.role_db_secrets, [
    {
      name      = "OPENAI_API_KEY"
      valueFrom = aws_secretsmanager_secret.runtime["openai_api_key"].arn
    },
  ])

  worker_secrets = concat(local.role_db_secrets, [
    {
      name      = "zendesk_basic_auth"
      valueFrom = aws_secretsmanager_secret.runtime["zendesk_basic_auth"].arn
    },
    {
      name      = "OPENAI_API_KEY"
      valueFrom = aws_secretsmanager_secret.runtime["openai_api_key"].arn
    },
    {
      name      = "n8n_request_token"
      valueFrom = aws_secretsmanager_secret.runtime["n8n_request_token"].arn
    },
    {
      name      = "RAGFLOW_BASE_URL"
      valueFrom = aws_secretsmanager_secret.runtime["rag_service_url"].arn
    },
    {
      name      = "RAGFLOW_API_KEY"
      valueFrom = aws_secretsmanager_secret.runtime["rag_service_token"].arn
    },
    {
      name      = "ZENDESK_AI_ASSIGNEE_EMAIL"
      valueFrom = aws_secretsmanager_secret.runtime["zendesk_ai_assignee_email"].arn
    },
    {
      name      = "ZENDESK_FRAUD_REVIEW_ASSIGNEE_ID"
      valueFrom = aws_secretsmanager_secret.runtime["zendesk_fraud_assignee_id"].arn
    },
    {
      name      = "ACCOUNT_SLACK_N8N_WEBHOOK_URL"
      valueFrom = aws_secretsmanager_secret.runtime["account_slack_webhook_url"].arn
    },
    {
      name      = "ACCOUNT_SLACK_N8N_STATUS_URL"
      valueFrom = aws_secretsmanager_secret.runtime["account_slack_status_url"].arn
    },
    {
      name      = "ENGINEER_SLACK_ACCESS_TOKEN"
      valueFrom = aws_secretsmanager_secret.runtime["engineer_slack_access_token"].arn
    },
    {
      name      = "ENGINEER_SLACK_TEAM_ID"
      valueFrom = aws_secretsmanager_secret.runtime["engineer_slack_team_id"].arn
    },
    {
      name      = "ENGINEER_SLACK_CHANNEL_ID"
      valueFrom = aws_secretsmanager_secret.runtime["engineer_slack_channel_id"].arn
    },
    {
      name      = "BILLING_AUTOMATION_GRAPH_TENANT_ID"
      valueFrom = aws_secretsmanager_secret.runtime["billing_graph_tenant_id"].arn
    },
    {
      name      = "BILLING_AUTOMATION_GRAPH_CLIENT_ID"
      valueFrom = aws_secretsmanager_secret.runtime["billing_graph_client_id"].arn
    },
    {
      name      = "BILLING_AUTOMATION_GRAPH_CLIENT_SECRET"
      valueFrom = aws_secretsmanager_secret.runtime["billing_graph_client_secret"].arn
    },
    {
      name      = "BILLING_AUTOMATION_GRAPH_USERNAME"
      valueFrom = aws_secretsmanager_secret.runtime["billing_graph_username"].arn
    },
  ])
}
