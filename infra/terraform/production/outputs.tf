output "ecr_repository_url" {
  value = aws_ecr_repository.runtime.repository_url
}

output "automation_target_group_arn" {
  value = aws_lb_target_group.automation.arn
}

output "automation_listener_rule_arn" {
  value = aws_lb_listener_rule.automation_https.arn
}

output "automation_service_names" {
  value = { for role, service in aws_ecs_service.automation : role => service.name }
}

output "shared_dependency_readback" {
  value = {
    cluster_arn         = data.aws_ecs_cluster.production.arn
    alb_arn             = data.aws_lb.shared.arn
    https_listener_arn  = data.aws_lb_listener.shared_https.arn
    acm_certificate_arn = var.shared_acm_certificate_arn
    security_group_id   = data.aws_security_group.ecs.id
    log_group_arn       = data.aws_cloudwatch_log_group.shared.arn
    execution_role_arn  = data.aws_iam_role.task_execution.arn
    task_role_arn       = data.aws_iam_role.task.arn
    graph_efs_arn       = data.aws_efs_file_system.graph.arn
    ssm_parameter_arns  = { for name, parameter in data.aws_ssm_parameter.runtime : name => parameter.arn }
  }
}
