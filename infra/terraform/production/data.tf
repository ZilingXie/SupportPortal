data "aws_ecs_cluster" "production" {
  cluster_name = var.ecs_cluster_name
}

data "aws_lb" "shared" {
  arn = var.shared_alb_arn
}

data "aws_lb_listener" "shared_https" {
  arn = var.shared_https_listener_arn
}

data "aws_security_group" "ecs" {
  id = var.ecs_security_group_id
}

data "aws_cloudwatch_log_group" "shared" {
  name = var.shared_log_group_name
}

data "aws_iam_role" "task_execution" {
  name = var.shared_task_execution_role_name
}

data "aws_iam_role" "task" {
  name = var.shared_task_role_name
}

data "aws_efs_file_system" "graph" {
  file_system_id = var.shared_graph_efs_file_system_id
}

data "aws_ssm_parameter" "runtime" {
  for_each = toset(var.shared_ssm_parameter_names)
  name     = each.value
}
