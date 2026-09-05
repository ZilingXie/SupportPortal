resource "aws_ecs_service" "account" {
  for_each = var.create_account_services ? local.account_services : {}

  name             = each.value.name
  cluster          = aws_ecs_cluster.preproduction.arn
  task_definition  = var.account_task_definition_arns[each.key]
  desired_count    = var.desired_count
  launch_type      = "FARGATE"
  platform_version = each.value.platform_version

  availability_zone_rebalancing = "ENABLED"
  wait_for_steady_state         = false

  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"

  health_check_grace_period_seconds  = each.key == "api" ? 60 : 0
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.public_subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }

  dynamic "load_balancer" {
    for_each = each.value.attach_load_balancer ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.automation.arn
      container_name   = "api"
      container_port   = 8000
    }
  }

  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = merge(local.tags, { Component = each.key })
}
