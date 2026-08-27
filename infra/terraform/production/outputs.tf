output "aws_account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "vpc_id" {
  value = var.vpc_id
}

output "public_subnet_ids" {
  value = local.public_subnet_ids
}

output "ecr_repository_urls" {
  value = { for key, repository in aws_ecr_repository.runtime : key => repository.repository_url }
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.production.name
}

output "ecs_task_execution_role_arn" {
  value = aws_iam_role.ecs_task_execution.arn
}

output "ecs_task_role_arn" {
  value = aws_iam_role.ecs_task.arn
}

output "github_release_role_arn" {
  value = aws_iam_role.github_release.arn
}

output "alb_dns_name" {
  value = aws_lb.production.dns_name
}

output "alb_zone_id" {
  value = aws_lb.production.zone_id
}

output "alb_target_group_arn" {
  value = aws_lb_target_group.api.arn
}

output "acm_certificate_arn" {
  value = local.certificate_arn
}

output "acm_dns_validation_records" {
  value = var.acm_certificate_arn != "" ? [] : [
    for option in aws_acm_certificate.production[0].domain_validation_options : {
      name  = option.resource_record_name
      type  = option.resource_record_type
      value = option.resource_record_value
    }
  ]
}

output "runtime_secret_arns" {
  value = { for key, secret in aws_secretsmanager_secret.runtime : key => secret.arn }
}

output "redis_endpoint" {
  value = var.enable_redis ? aws_elasticache_replication_group.redis[0].primary_endpoint_address : null
}

output "redis_url_secret_arn" {
  value = var.enable_redis ? aws_secretsmanager_secret.redis_url[0].arn : null
}

output "efs_file_system_id" {
  value = aws_efs_file_system.automation.id
}

output "efs_access_point_id" {
  value = aws_efs_access_point.automation.id
}

output "release_manifest_bucket" {
  value = aws_s3_bucket.release_manifest.bucket
}

output "alarm_arns" {
  value = concat(
    [aws_cloudwatch_metric_alarm.alb_5xx.arn, aws_cloudwatch_metric_alarm.target_5xx.arn],
    var.enable_services && var.enable_container_insights ? [aws_cloudwatch_metric_alarm.api_running_tasks[0].arn] : [],
  )
}

output "api_service_name" {
  value = var.enable_services ? aws_ecs_service.api[0].name : null
}

output "worker_service_name" {
  value = var.enable_services ? aws_ecs_service.worker[0].name : null
}
