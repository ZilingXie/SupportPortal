variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "vpc_id" {
  description = "Shared VPC id (default VPC)"
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets for Fargate services (frontend/app/docreader)"
  type        = list(string)
}

variable "db_subnet_id" {
  description = "Subnet for the dedicated WeKnora data capacity instance"
  type        = string
}

variable "shared_https_listener_arn" {
  description = "Shared ALB HTTPS listener that receives the /dashboard/weknora rule"
  type        = string
}

variable "shared_alb_security_group_id" {
  description = "Security group id of the shared ALB"
  type        = string
}

variable "listener_rule_priority" {
  description = "Priority for the /dashboard/weknora listener rule"
  type        = number
  default     = 105
}

variable "db_instance_type" {
  description = "Instance type of the dedicated ParadeDB/Redis capacity instance"
  type        = string
  default     = "t3.xlarge"
}

variable "db_root_volume_gb" {
  description = "Root EBS size (gp3) of the data capacity instance; docker volumes live on it"
  type        = number
  default     = 100
}

variable "log_retention_days" {
  description = "CloudWatch log retention for /ecs/supportportal/weknora"
  type        = number
  default     = 30
}

variable "create_services" {
  description = "Create the five WeKnora ECS services (staged bootstrap: apply foundation first, register task definitions, then set true)"
  type        = bool
  default     = false
}

variable "task_definition_arns" {
  description = "Map of service key (paradedb, redis, docreader, app, frontend) to registered task definition ARN"
  type        = map(string)
  default     = {}
}

variable "desired_count" {
  description = "Desired count per WeKnora service (phase one is single-instance by design)"
  type        = number
  default     = 1
}
