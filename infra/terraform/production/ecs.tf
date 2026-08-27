resource "aws_ecs_cluster" "production" {
  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = var.enable_container_insights ? "enabled" : "disabled"
  }
}

resource "aws_ecs_task_definition" "api" {
  count = var.enable_services ? 1 : 0

  family                   = "${local.name_prefix}-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = var.api_image
      essential = true
      command   = ["uvicorn", "backend.automation_production_runtime:app", "--host", "0.0.0.0", "--port", "8000"]
      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]
      environment = local.base_environment
      secrets     = local.api_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "api"
        }
      }
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)\" || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    },
    {
      name      = "route"
      image     = var.route_image
      essential = true
      command   = ["uvicorn", "backend.route_service:app", "--host", "0.0.0.0", "--port", "8100"]
      environment = [
        {
          name  = "APP_BUILD_REF"
          value = var.release_id
        },
        {
          name  = "AUTOMATION_ENVIRONMENT"
          value = "production"
        },
      ]
      secrets = [
        {
          name      = "ROUTE_SERVICE_TOKEN"
          valueFrom = aws_secretsmanager_secret.runtime["route_service_token"].arn
        },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.route.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "route"
        }
      }
    },
  ])

}

resource "aws_ecs_service" "api" {
  count = var.enable_services ? 1 : 0

  name                               = "${local.name_prefix}-api"
  cluster                            = aws_ecs_cluster.production.id
  task_definition                    = aws_ecs_task_definition.api[0].arn
  desired_count                      = var.desired_count
  launch_type                        = "FARGATE"
  platform_version                   = "LATEST"
  health_check_grace_period_seconds  = 120
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = local.public_subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = var.assign_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [terraform_data.service_validation]

}

resource "aws_ecs_task_definition" "worker" {
  count = var.enable_services ? 1 : 0

  family                   = "${local.name_prefix}-worker"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = var.worker_image
      essential = true
      command   = ["python", "-m", "backend.worker"]
      environment = concat(
        local.base_environment,
        [
          {
            name  = "PROMPT_RUNTIME_SERVICE"
            value = "automation_production_worker"
          },
          {
            name  = "TICKET_DB_APPLICATION_NAME"
            value = "supportportal-automation-production-worker"
          },
          {
            name  = "BILLING_AUTOMATION_GRAPH_TOKEN_CACHE"
            value = "/app/.msgraph/billing-automation-token.json"
          },
          {
            name  = "BILLING_AUTOMATION_REPLY_RECORD_PATH"
            value = "/app/.msgraph/billing-request-replies.jsonl"
          },
        ],
      )
      secrets = local.worker_secrets
      mountPoints = [
        {
          sourceVolume  = "graph-token-cache"
          containerPath = "/app/.msgraph"
          readOnly      = false
        },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.worker.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "worker"
        }
      }
    },
  ])

  volume {
    name = "graph-token-cache"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.automation.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.automation.id
        iam             = "ENABLED"
      }
    }
  }
}

resource "aws_ecs_service" "worker" {
  count = var.enable_services ? 1 : 0

  name                               = "${local.name_prefix}-worker"
  cluster                            = aws_ecs_cluster.production.id
  task_definition                    = aws_ecs_task_definition.worker[0].arn
  desired_count                      = var.desired_count
  launch_type                        = "FARGATE"
  platform_version                   = "LATEST"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = local.public_subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = var.assign_public_ip
  }

  depends_on = [terraform_data.service_validation]
}
