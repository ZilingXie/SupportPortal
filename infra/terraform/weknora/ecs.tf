# ---------------------------------------------------------------------------
# WeKnora services. Staged bootstrap: create_services=false applies the
# foundation; task definitions are registered by
# deployment/weknora/register_weknora_initial_task_definitions.sh; the second
# apply with create_services=true creates the five services. Later task
# definition revisions and service pointers are owned by the deploy script,
# so only task_definition is ignored here.
# ---------------------------------------------------------------------------

locals {
  weknora_services = {
    paradedb = {
      name                   = "weknora-paradedb"
      launch_type            = null
      capacity_provider      = aws_ecs_capacity_provider.db.name
      platform_version       = null
      attach_load_balancer   = false
      service_registry       = aws_service_discovery_service.paradedb.arn
      subnet_style           = "db"
      assign_public_ip       = false
      health_grace           = 0
    }
    redis = {
      name                   = "weknora-redis"
      launch_type            = null
      capacity_provider      = aws_ecs_capacity_provider.db.name
      platform_version       = null
      attach_load_balancer   = false
      service_registry       = aws_service_discovery_service.redis.arn
      subnet_style           = "db"
      assign_public_ip       = false
      health_grace           = 0
    }
    docreader = {
      name                   = "weknora-docreader"
      launch_type            = "FARGATE"
      capacity_provider      = null
      platform_version       = "LATEST"
      attach_load_balancer   = false
      service_registry       = aws_service_discovery_service.docreader.arn
      subnet_style           = "public"
      assign_public_ip       = true
      health_grace           = 0
    }
    app = {
      name                   = "weknora-app"
      launch_type            = "FARGATE"
      capacity_provider      = null
      platform_version       = "LATEST"
      attach_load_balancer   = false
      service_registry       = aws_service_discovery_service.app.arn
      subnet_style           = "public"
      assign_public_ip       = true
      health_grace           = 0
    }
    frontend = {
      name                   = "weknora-frontend"
      launch_type            = "FARGATE"
      capacity_provider      = null
      platform_version       = "LATEST"
      attach_load_balancer   = true
      service_registry       = null
      subnet_style           = "public"
      assign_public_ip       = true
      health_grace           = 60
    }
  }
}

resource "aws_ecs_service" "weknora" {
  for_each = var.create_services ? local.weknora_services : {}

  name          = each.value.name
  cluster       = aws_ecs_cluster.weknora.arn
  task_definition = var.task_definition_arns[each.key]
  desired_count = var.desired_count

  dynamic "capacity_provider_strategy" {
    for_each = each.value.capacity_provider != null ? [1] : []
    content {
      capacity_provider = each.value.capacity_provider
      weight            = 1
    }
  }

  launch_type      = each.value.launch_type
  platform_version = each.value.platform_version

  availability_zone_rebalancing = each.value.launch_type == "FARGATE" ? "ENABLED" : "DISABLED"
  wait_for_steady_state         = false

  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"

  health_check_grace_period_seconds = each.value.health_grace

  # All phase-one services are single-instance by design; replace-style
  # rollouts also guarantee the app never double-runs migrations.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = each.value.subnet_style == "db" ? [var.db_subnet_id] : var.public_subnet_ids
    security_groups  = [each.value.subnet_style == "db" ? aws_security_group.data.id : aws_security_group.tasks.id]
    assign_public_ip = each.value.assign_public_ip
  }

  dynamic "load_balancer" {
    for_each = each.value.attach_load_balancer ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.frontend.arn
      container_name   = "frontend"
      container_port   = 80
    }
  }

  dynamic "service_registries" {
    for_each = each.value.service_registry != null ? [1] : []
    content {
      registry_arn = each.value.service_registry
    }
  }

  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = merge(local.tags, { Component = each.key })
}
