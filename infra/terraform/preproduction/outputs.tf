output "bootstrap_contract" {
  value = {
    environment                  = local.environment
    cluster_name                 = aws_ecs_cluster.preproduction.name
    repository_name              = aws_ecr_repository.runtime.name
    repository_url               = aws_ecr_repository.runtime.repository_url
    execution_role_arn           = aws_iam_role.task_execution.arn
    task_role_arn                = aws_iam_role.task.arn
    hermes_task_role_arn         = aws_iam_role.hermes_task.arn
    log_group_name               = aws_cloudwatch_log_group.runtime.name
    parameter_prefix_arn         = local.parameter_prefix_arn
    graph_efs_file_system_id     = var.shared_graph_efs_file_system_id
    graph_efs_access_point_id    = aws_efs_access_point.graph.id
    hermes_efs_access_point_ids  = { for name, access_point in aws_efs_access_point.hermes : name => access_point.id }
    hermes_efs_access_point_arns = { for name, access_point in aws_efs_access_point.hermes : name => access_point.arn }
    ecs_security_group_id        = aws_security_group.ecs.id
    efs_subnet_id                = var.efs_subnet_id
    target_group_arn             = aws_lb_target_group.automation.arn
    hermes_discovery_service_arn = aws_service_discovery_service.hermes.arn
    hermes_private_base_url      = "http://hermes.preproduction.supportportal.local:8642"
    hermes_backup_bucket_name    = aws_s3_bucket.hermes_backup.bucket
  }
}

output "account_service_names" {
  value = { for role, service in aws_ecs_service.account : role => service.name }
}
