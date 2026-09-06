variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "vpc_id" {
  description = "Existing VPC shared with Production."
  type        = string
}

variable "public_subnet_ids" {
  description = "Existing public subnets used by the Preproduction Fargate tasks."
  type        = list(string)
}

variable "efs_subnet_id" {
  description = "Public subnet in the availability zone that has the shared EFS mount target."
  type        = string
}

variable "shared_https_listener_arn" {
  description = "Existing HTTPS listener on the shared ALB."
  type        = string
}

variable "shared_alb_security_group_id" {
  description = "Security group attached to the shared ALB."
  type        = string
}

variable "shared_graph_efs_file_system_id" {
  description = "Existing EFS file system; Preproduction gets an isolated Graph access point."
  type        = string
}

variable "shared_efs_security_group_id" {
  description = "Security group protecting the shared EFS mount targets."
  type        = string
}

variable "shared_rds_security_group_id" {
  description = "Security group protecting the shared PostgreSQL instance."
  type        = string
}

variable "hermes_efs_access_point_arns" {
  description = "Existing Hermes access point ARNs authorized only for the migrated Preproduction writer."
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.hermes_efs_access_point_arns) == 3
    error_message = "Hermes migration requires the three existing state access point ARNs."
  }
}

variable "account_task_definition_arns" {
  description = "Initial canonical API, Route, and Worker revisions registered by the bootstrap command."
  type        = map(string)
  default     = {}

  validation {
    condition = !var.create_account_services || (
      length(var.account_task_definition_arns) == 3 &&
      alltrue([for role in ["api", "route", "worker"] : contains(keys(var.account_task_definition_arns), role)])
    )
    error_message = "Creating Account services requires api, route, and worker task definition ARNs."
  }
}

variable "create_account_services" {
  description = "False for foundation bootstrap; true only after initial task definitions are registered."
  type        = bool
  default     = false
}

variable "desired_count" {
  type    = number
  default = 1

  validation {
    condition     = var.desired_count == 1
    error_message = "The initial Preproduction acceptance environment requires exactly one task per service."
  }
}

variable "listener_rule_priority" {
  type    = number
  default = 20
}

variable "hermes_backup_bucket_name" {
  type    = string
  default = ""
}

variable "log_retention_days" {
  type    = number
  default = 30
}
