output "cluster_name" {
  value = aws_ecs_cluster.weknora.name
}

output "cluster_arn" {
  value = aws_ecs_cluster.weknora.arn
}

output "ecr_repository_url" {
  value = aws_ecr_repository.weknora.repository_url
}

output "log_group" {
  value = aws_cloudwatch_log_group.weknora.name
}

output "codebuild_project" {
  value = aws_codebuild_project.image_build.name
}

output "docs_bucket" {
  value = aws_s3_bucket.docs.id
}

output "backup_bucket" {
  value = aws_s3_bucket.backup.id
}

output "frontend_target_group_arn" {
  value = aws_lb_target_group.frontend.arn
}

output "listener_rule_arn" {
  value = aws_lb_listener_rule.frontend_https.arn
}

output "cloudmap_namespace" {
  value = aws_service_discovery_private_dns_namespace.weknora.name
}

output "tasks_security_group_id" {
  value = aws_security_group.tasks.id
}

output "data_security_group_id" {
  value = aws_security_group.data.id
}

output "db_capacity_asg_name" {
  value = aws_autoscaling_group.db_capacity.name
}
