variable "aws_region" {
  description = "AWS region containing the imported Automation Production resources."
  type        = string
  default     = "us-east-1"
}

variable "vpc_id" {
  description = "Existing VPC used by the imported Automation target group."
  type        = string
}

variable "ecs_cluster_name" {
  description = "Shared ECS cluster name; Terraform reads but does not own the cluster."
  type        = string
  default     = "supportportal-production"
}

variable "shared_alb_arn" {
  description = "Shared ALB ARN; Terraform does not own the ALB."
  type        = string
}

variable "shared_https_listener_arn" {
  description = "Shared HTTPS listener ARN that receives the imported Automation rule."
  type        = string
}

variable "shared_acm_certificate_arn" {
  description = "Shared ACM certificate ARN recorded as an external dependency."
  type        = string
}

variable "ecs_security_group_id" {
  description = "Shared ECS security group used by all three services."
  type        = string
}

variable "shared_log_group_name" {
  description = "Single shared CloudWatch log group referenced by live task definitions."
  type        = string
}

variable "shared_task_execution_role_name" {
  description = "Shared ECS execution role name referenced by live task definitions."
  type        = string
}

variable "shared_task_role_name" {
  description = "Shared ECS task role name referenced by live task definitions."
  type        = string
}

variable "shared_graph_efs_file_system_id" {
  description = "Shared Graph token-cache EFS id; Terraform does not own EFS resources."
  type        = string
}

variable "shared_ssm_parameter_names" {
  description = "Shared runtime and Hermes SSM parameter names verified as external inputs."
  type        = list(string)
}

variable "api_service_name" {
  type    = string
  default = "supportportal-production-api"
}

variable "route_service_name" {
  type    = string
  default = "supportportal-production-route"
}

variable "worker_service_name" {
  type    = string
  default = "supportportal-production-worker"
}

variable "api_task_definition_arn" {
  description = "Bootstrap task definition for API; release revisions are ignored after import."
  type        = string
}

variable "route_task_definition_arn" {
  description = "Bootstrap task definition for Route; release revisions are ignored after import."
  type        = string
}

variable "worker_task_definition_arn" {
  description = "Bootstrap task definition for Worker; release revisions are ignored after import."
  type        = string
}

variable "api_subnet_ids" {
  type = list(string)
}

variable "route_subnet_ids" {
  type = list(string)
}

variable "worker_subnet_ids" {
  type = list(string)
}

variable "assign_public_ip" {
  type    = bool
  default = true
}

variable "desired_count" {
  type    = number
  default = 1

  validation {
    condition     = var.desired_count >= 1 && floor(var.desired_count) == var.desired_count
    error_message = "desired_count must be a positive whole number."
  }
}

variable "automation_target_group_name" {
  type    = string
  default = "supportportal-production-tg"
}

variable "automation_listener_rule_priority" {
  type    = number
  default = 10

  validation {
    condition     = var.automation_listener_rule_priority == 10
    error_message = "The imported Automation HTTPS listener rule must retain priority 10."
  }
}
