variable "aws_region" {
  description = "AWS region for the ECS Production foundation."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Lowercase project name used in resource names."
  type        = string
  default     = "supportportal"
}

variable "environment" {
  description = "Environment managed by this stack. Stage 3 only supports production."
  type        = string
  default     = "production"

  validation {
    condition     = var.environment == "production"
    error_message = "This Terraform root is only for the ECS Production foundation."
  }
}

variable "vpc_id" {
  description = "Existing SupportPortal VPC."
  type        = string
  default     = "vpc-0125f57b2ec2f0423"
}

variable "public_subnet_ids" {
  description = "At least two public subnet IDs. Empty discovers public subnets in the VPC."
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.public_subnet_ids) == 0 || length(var.public_subnet_ids) >= 2
    error_message = "Provide no subnet IDs for discovery, or at least two public subnet IDs."
  }
}

variable "efs_availability_zone_name" {
  description = "Availability Zone for the cost-first One Zone EFS token cache and its Worker tasks."
  type        = string
  default     = "us-east-1b"

  validation {
    condition     = trimspace(var.efs_availability_zone_name) != ""
    error_message = "efs_availability_zone_name must not be empty."
  }
}

variable "rds_security_group_id" {
  description = "Existing RDS security group to which ECS access is added."
  type        = string
  default     = "sg-0e9c3bd50e371fbf4"
}

variable "domain_name" {
  description = "Dedicated ECS Production hostname."
  type        = string
  default     = "supportcenter.stellarix.space"
}

variable "acm_certificate_arn" {
  description = "Existing issued ACM certificate ARN. Empty creates a DNS-validated certificate and outputs its records."
  type        = string
  default     = ""
}

variable "enable_https_listener" {
  description = "Create the HTTPS listener after the ACM certificate is issued."
  type        = bool
  default     = false
}

variable "enable_services" {
  description = "Create ECS API, Route and Worker services. Keep false until the approved release digests are available."
  type        = bool
  default     = false
}

variable "api_image" {
  description = "Immutable API image reference in supportportal/production, pinned by ECR digest."
  type        = string
  default     = ""
}

variable "route_image" {
  description = "Immutable Route image reference in supportportal/production, pinned by ECR digest."
  type        = string
  default     = ""
}

variable "worker_image" {
  description = "Immutable Worker image reference in supportportal/production, pinned by ECR digest."
  type        = string
  default     = ""
}

variable "release_id" {
  description = "Release manifest ID injected into task environment."
  type        = string
  default     = "unreleased"
}

variable "git_commit" {
  description = "Full Git commit captured by the Release Manifest."
  type        = string
  default     = ""
}

variable "build_time" {
  description = "UTC OCI build time captured by the Release Manifest."
  type        = string
  default     = ""
}

variable "prompt_release_id" {
  description = "Active Prompt Release ID captured by the Release Manifest."
  type        = string
  default     = ""
}

variable "zendesk_side_effects_enabled" {
  description = "Enable real Zendesk writes for controlled Production Case testing and cutover."
  type        = bool
  default     = false
}

variable "api_cpu" {
  description = "API Fargate CPU units."
  type        = string
  default     = "512"
}

variable "api_memory" {
  description = "API Fargate memory in MiB."
  type        = string
  default     = "1024"
}

variable "route_cpu" {
  description = "Route Worker Fargate CPU units."
  type        = string
  default     = "256"
}

variable "route_memory" {
  description = "Route Worker Fargate memory in MiB."
  type        = string
  default     = "512"
}

variable "worker_cpu" {
  description = "Worker Fargate CPU units."
  type        = string
  default     = "512"
}

variable "worker_memory" {
  description = "Worker Fargate memory in MiB."
  type        = string
  default     = "1024"
}

variable "desired_count" {
  description = "Initial desired count for API, Route and Worker services."
  type        = number
  default     = 1

  validation {
    condition     = var.desired_count >= 1 && floor(var.desired_count) == var.desired_count
    error_message = "desired_count must be a positive whole number."
  }
}

variable "assign_public_ip" {
  description = "Assign public IPs to Fargate tasks for egress in the current no-NAT VPC."
  type        = bool
  default     = true
}

variable "enable_redis" {
  description = "Create the single-node ElastiCache Redis foundation."
  type        = bool
  default     = true
}

variable "redis_node_type" {
  description = "Cost-first ElastiCache node type."
  type        = string
  default     = "cache.t3.micro"
}

variable "redis_engine_version" {
  description = "Redis OSS engine version."
  type        = string
  default     = "7.1"
}

variable "log_retention_days" {
  description = "CloudWatch log retention for ECS runtime logs."
  type        = number
  default     = 30
}

variable "enable_container_insights" {
  description = "Enable ECS Container Insights. Disabled by default for cost control."
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "GitHub repository allowed to assume the release role."
  type        = string
  default     = "ZilingXie/SupportPortal"
}

variable "github_branch" {
  description = "Git branch allowed to assume the release role."
  type        = string
  default     = "main"
}

variable "github_oidc_thumbprint" {
  description = "Thumbprint for token.actions.githubusercontent.com."
  type        = string
  default     = "6938fd4d98bab03faadb97b34396831e3780aea1"
}

variable "manifest_bucket_name" {
  description = "Optional globally unique S3 bucket name for release manifests. Empty derives one from the AWS account ID."
  type        = string
  default     = ""
}
