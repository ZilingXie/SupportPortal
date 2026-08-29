data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [var.vpc_id]
  }

  filter {
    name   = "map-public-ip-on-launch"
    values = ["true"]
  }
}

data "aws_subnet" "selected" {
  for_each = toset(local.candidate_public_subnet_ids)
  id       = each.value
}

resource "terraform_data" "network_validation" {
  input = local.public_subnet_ids

  lifecycle {
    precondition {
      condition     = length(local.public_subnet_ids) >= 2
      error_message = "The ECS ALB requires at least two public subnets."
    }

    precondition {
      condition     = length(distinct([for subnet in data.aws_subnet.selected : subnet.availability_zone])) >= 2
      error_message = "The ECS ALB subnets must span at least two availability zones."
    }
  }
}

resource "terraform_data" "listener_validation" {
  input = var.enable_https_listener

  lifecycle {
    precondition {
      condition     = !var.enable_https_listener || local.certificate_arn != ""
      error_message = "An ACM certificate ARN or a Terraform-managed certificate is required for HTTPS."
    }
  }
}

resource "terraform_data" "service_validation" {
  input = var.enable_services

  lifecycle {
    precondition {
      condition = !var.enable_services || (
        trimspace(var.api_image) != "" &&
        trimspace(var.route_image) != "" &&
        trimspace(var.worker_image) != ""
      )
      error_message = "API, Route and Worker immutable image references are required when enable_services=true."
    }

    precondition {
      condition = !var.enable_services || alltrue([
        for image in [var.api_image, var.route_image, var.worker_image] :
        startswith(image, "${aws_ecr_repository.runtime.repository_url}@sha256:") &&
        can(regex("@sha256:[0-9a-f]{64}$", image))
      ])
      error_message = "All services must use supportportal/production ECR references pinned by sha256 digest."
    }

    precondition {
      condition = !var.enable_services || alltrue([
        trimspace(var.release_id) != "" && trimspace(var.release_id) != "unreleased",
        can(regex("^[0-9a-f]{40}$", var.git_commit)),
        trimspace(var.build_time) != "",
        trimspace(var.prompt_release_id) != "",
      ])
      error_message = "Release ID, full Git commit, build time and Prompt Release ID are required."
    }

    precondition {
      condition     = local.efs_subnet_id != ""
      error_message = "The One Zone EFS availability zone must contain a selected public subnet."
    }
  }
}
