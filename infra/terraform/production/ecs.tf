resource "aws_ecs_service" "automation" {
  for_each = local.services

  name             = each.value.name
  cluster          = data.aws_ecs_cluster.production.arn
  task_definition  = each.value.task_definition
  desired_count    = var.desired_count
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  health_check_grace_period_seconds  = each.value.health_check_grace_period
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = each.value.subnets
    security_groups  = [data.aws_security_group.ecs.id]
    assign_public_ip = var.assign_public_ip
  }

  dynamic "load_balancer" {
    for_each = each.value.attach_automation_target_group ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.automation.arn
      container_name   = "api"
      container_port   = 8000
    }
  }

  lifecycle {
    # Immutable release revisions are exclusively owned by
    # deployment/deploy_automation_ecs_release.sh.
    ignore_changes = [task_definition]
  }
}
