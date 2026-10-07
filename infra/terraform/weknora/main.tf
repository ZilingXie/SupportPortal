data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "aws_ssm_parameter" "ecs_optimized_al2023" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}

locals {
  environment          = "weknora"
  parameter_prefix     = "/supportportal/weknora"
  parameter_prefix_arn = "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.parameter_prefix}"
  docs_bucket_name     = "supportportal-weknora-docs-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  backup_bucket_name   = "supportportal-weknora-backup-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  ecr_repo_name        = "supportportal/weknora"
  source_bucket_name   = "supportportal-release-evidence-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  tags = {
    Project     = "supportportal"
    Environment = local.environment
    Owner       = "zac"
    System      = "weknora"
  }
}

resource "aws_ecs_cluster" "weknora" {
  name = "supportportal-weknora"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }

  tags = local.tags
}

# ---------------------------------------------------------------------------
# Dedicated EC2 capacity for ParadeDB + Redis (phase one: single instance,
# min=max=desired=1). Data lives in docker volumes on the root EBS, so ECS
# task restarts keep data; instance replacement is a restore-from-backup
# event (documented phase-one limit, see docs/deploy_weknora_standalone_ecs.md).
# ---------------------------------------------------------------------------

resource "aws_iam_role" "db_instance" {
  name = "supportportal-weknora-db-instance"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "db_instance_ecs" {
  role       = aws_iam_role.db_instance.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_role_policy_attachment" "db_instance_ssm" {
  role       = aws_iam_role.db_instance.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "db_instance" {
  name = "supportportal-weknora-db-instance"
  role = aws_iam_role.db_instance.name
  tags = local.tags
}

resource "aws_launch_template" "db_capacity" {
  name          = "supportportal-weknora-db-capacity"
  image_id      = data.aws_ssm_parameter.ecs_optimized_al2023.value
  instance_type = var.db_instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.db_instance.arn
  }

  metadata_options {
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 1
  }

  monitoring {
    enabled = true
  }

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size           = var.db_root_volume_gb
      volume_type           = "gp3"
      delete_on_termination = false
      encrypted             = true
    }
  }

  user_data = base64encode(<<-EOT
    #!/bin/bash
    echo ECS_CLUSTER=${aws_ecs_cluster.weknora.name} >> /etc/ecs/ecs.config
    echo ECS_ENGINE_TASK_CLEANUP_WAIT_DURATION=5m >> /etc/ecs/ecs.config
    echo ECS_AVAILABLE_LOGGING_DRIVERS='["json-file","awslogs"]' >> /etc/ecs/ecs.config
  EOT
  )

  tag_specifications {
    resource_type = "instance"
    tags          = local.tags
  }

  tags = local.tags
}

resource "aws_autoscaling_group" "db_capacity" {
  name                = "supportportal-weknora-db-capacity"
  vpc_zone_identifier = [var.db_subnet_id]
  min_size            = 1
  max_size            = 1
  desired_capacity    = 1
  health_check_type   = "EC2"

  launch_template {
    id      = aws_launch_template.db_capacity.id
    version = "$Latest"
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      instance_warmup        = 180
      min_healthy_percentage = 0
    }
  }

  dynamic "tag" {
    for_each = merge(
      { "AmazonECSManaged" = "true", "Name" = "supportportal-weknora-db-capacity" },
      local.tags,
    )
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = true
    }
  }
}

resource "aws_ecs_capacity_provider" "db" {
  name = "supportportal-weknora-db"

  auto_scaling_group_provider {
    auto_scaling_group_arn = aws_autoscaling_group.db_capacity.arn
    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 1
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 1
    }
    managed_termination_protection = "DISABLED"
  }

  tags = local.tags
}

resource "aws_ecs_cluster_capacity_providers" "weknora" {
  cluster_name       = aws_ecs_cluster.weknora.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT", aws_ecs_capacity_provider.db.name]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

resource "aws_ecr_repository" "weknora" {
  name                 = local.ecr_repo_name
  image_tag_mutability = "IMMUTABLE"

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.tags, { Component = "runtime-images" })
}

resource "aws_ecr_lifecycle_policy" "weknora" {
  repository = aws_ecr_repository.weknora.name
  policy = jsonencode({
    rules = [
      for index, component in ["app", "frontend", "docreader", "base"] : {
        rulePriority = index + 1
        description  = "Keep current plus two rollback builds for ${component}"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["${component}-"]
          countType     = "imageCountMoreThan"
          countNumber   = 3
        }
        action = { type = "expire" }
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "weknora" {
  name              = "/ecs/supportportal/weknora"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}
