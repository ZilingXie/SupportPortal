data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  environment          = "preproduction"
  parameter_prefix     = "/supportportal/preproduction"
  parameter_prefix_arn = "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.parameter_prefix}"
  backup_bucket_name   = var.hermes_backup_bucket_name != "" ? var.hermes_backup_bucket_name : "supportportal-hermes-preproduction-backup-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  tags = {
    Project     = "supportportal"
    Environment = local.environment
    Owner       = "zac"
    System      = "automation"
  }
  account_services = {
    api = {
      name                 = "supportportal-preproduction-api"
      platform_version     = "LATEST"
      attach_load_balancer = true
    }
    route = {
      name                 = "supportportal-preproduction-route"
      platform_version     = "1.4.0"
      attach_load_balancer = false
    }
    worker = {
      name                 = "supportportal-preproduction-worker"
      platform_version     = "1.4.0"
      attach_load_balancer = false
    }
  }
}

resource "aws_ecs_cluster" "preproduction" {
  name = "supportportal-preproduction"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }

  tags = local.tags
}

resource "aws_ecr_repository" "runtime" {
  name                 = "supportportal/preproduction"
  image_tag_mutability = "IMMUTABLE"

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.tags, { Component = "runtime-images" })
}

resource "aws_ecr_lifecycle_policy" "runtime" {
  repository = aws_ecr_repository.runtime.name
  policy = jsonencode({
    rules = [
      for index, role in ["api", "route", "worker"] : {
        rulePriority = index + 1
        description  = "Keep current plus two rollback releases for ${role}"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["${role}-"]
          countType     = "imageCountMoreThan"
          countNumber   = 3
        }
        action = { type = "expire" }
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "runtime" {
  name              = "/ecs/supportportal/preproduction"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}
