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

  # AWS ALB and EFS each accept only one subnet per Availability Zone.
  public_subnet_ids = [
    for availability_zone in sort(keys(local.public_subnet_ids_by_az)) :
    local.public_subnet_ids_by_az[availability_zone][0]
  ]

  ecr_repositories = {
    api    = "${local.name_prefix}-api"
    route  = "${local.name_prefix}-route"
    worker = "${local.name_prefix}-worker"
  }

  runtime_secret_names = {
    ticket_db_dsn               = "${local.name_prefix}/ticket-db-dsn"
    migration_db_dsn            = "${local.name_prefix}/migration-db-dsn"
    n8n_request_token           = "${local.name_prefix}/n8n-request-token"
    rag_service_url             = "${local.name_prefix}/rag-service-url"
    rag_service_token           = "${local.name_prefix}/rag-service-shared-token"
    route_service_token         = "${local.name_prefix}/route-service-token"
    zendesk_basic_auth          = "${local.name_prefix}/zendesk-basic-auth"
    openai_api_key              = "${local.name_prefix}/openai-api-key"
    billing_graph_tenant_id     = "${local.name_prefix}/billing-graph-tenant-id"
    billing_graph_client_id     = "${local.name_prefix}/billing-graph-client-id"
    billing_graph_client_secret = "${local.name_prefix}/billing-graph-client-secret"
    billing_graph_username      = "${local.name_prefix}/billing-graph-username"
  }

  manifest_bucket_name = trimspace(var.manifest_bucket_name) != "" ? trimspace(var.manifest_bucket_name) : "${var.project_name}-${var.environment}-release-manifests-${data.aws_caller_identity.current.account_id}"

  certificate_arn = trimspace(var.acm_certificate_arn) != "" ? trimspace(var.acm_certificate_arn) : try(aws_acm_certificate.production[0].arn, "")

  redis_auth_token = try(random_password.redis_auth[0].result, "")

  base_environment = [
    {
      name  = "AUTOMATION_ENVIRONMENT"
      value = "production"
    },
    {
      name  = "AUTOMATION_RESOURCE_ID"
      value = "production"
    },
    {
      name  = "AUTOMATION_DB_RESOURCE_ID"
      value = "production"
    },
    {
      name  = "AUTOMATION_DB_SCHEMA"
      value = "supportportal_production"
    },
    {
      name  = "AUTOMATION_DB_TABLE"
      value = "automation_executions_production"
    },
    {
      name  = "AUTOMATION_QUEUE_NAME"
      value = "automation.production"
    },
    {
      name  = "AUTOMATION_EVENT_CHANNEL"
      value = "automation.events.production"
    },
    {
      name  = "ROUTE_SERVICE_URL"
      value = "http://127.0.0.1:8100"
    },
    {
      name  = "APP_BUILD_REF"
      value = var.release_id
    },
    {
      name  = "APP_BUILD_TIME"
      value = "terraform-foundation"
    },
  ]

  api_secrets = [
    {
      name      = "TICKET_DB_DSN"
      valueFrom = aws_secretsmanager_secret.runtime["ticket_db_dsn"].arn
    },
    {
      name      = "AUTOMATION_DB_DSN"
      valueFrom = aws_secretsmanager_secret.runtime["ticket_db_dsn"].arn
    },
    {
      name      = "n8n_request_token"
      valueFrom = aws_secretsmanager_secret.runtime["n8n_request_token"].arn
    },
    {
      name      = "RAG_SERVICE_URL"
      valueFrom = aws_secretsmanager_secret.runtime["rag_service_url"].arn
    },
    {
      name      = "RAG_SERVICE_SHARED_TOKEN"
      valueFrom = aws_secretsmanager_secret.runtime["rag_service_token"].arn
    },
    {
      name      = "ROUTE_SERVICE_TOKEN"
      valueFrom = aws_secretsmanager_secret.runtime["route_service_token"].arn
    },
    {
      name      = "zendesk_basic_auth"
      valueFrom = aws_secretsmanager_secret.runtime["zendesk_basic_auth"].arn
    },
    {
      name      = "OPENAI_API_KEY"
      valueFrom = aws_secretsmanager_secret.runtime["openai_api_key"].arn
    },
  ]

  worker_secrets = concat(
    local.api_secrets,
    var.enable_redis ? [
      {
        name      = "REDIS_URL"
        valueFrom = aws_secretsmanager_secret.redis_url[0].arn
      },
      {
        name      = "AUTOMATION_REDIS_URL"
        valueFrom = aws_secretsmanager_secret.redis_url[0].arn
      },
    ] : [],
    [
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
    ],
  )
}
