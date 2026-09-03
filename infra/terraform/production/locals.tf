locals {
  services = {
    api = {
      name                           = var.api_service_name
      task_definition                = var.api_task_definition_arn
      subnets                        = var.api_subnet_ids
      health_check_grace_period      = 120
      attach_automation_target_group = true
    }
    route = {
      name                           = var.route_service_name
      task_definition                = var.route_task_definition_arn
      subnets                        = var.route_subnet_ids
      health_check_grace_period      = 0
      attach_automation_target_group = false
    }
    worker = {
      name                           = var.worker_service_name
      task_definition                = var.worker_task_definition_arn
      subnets                        = var.worker_subnet_ids
      health_check_grace_period      = 0
      attach_automation_target_group = false
    }
  }
}
