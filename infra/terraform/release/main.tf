data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  evidence_bucket_name = var.evidence_bucket_name != "" ? var.evidence_bucket_name : "supportportal-release-evidence-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  repository_arns = [
    "arn:${data.aws_partition.current.partition}:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/${var.preproduction_repository_name}",
    aws_ecr_repository.cache.arn,
  ]
}

resource "aws_ecr_repository" "cache" {
  name                 = var.cache_repository_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = false
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Project     = "supportportal"
    Environment = "release"
    Component   = "build-cache"
    Owner       = "zac"
  }
}

resource "aws_ecr_lifecycle_policy" "cache" {
  repository = aws_ecr_repository.cache.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the newest three BuildKit cache manifests"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_s3_bucket" "evidence" {
  bucket        = local.evidence_bucket_name
  force_destroy = false

  tags = {
    Project   = "supportportal"
    Component = "release-evidence"
    Owner     = "zac"
  }
}

resource "aws_s3_bucket_ownership_controls" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_public_access_block" "evidence" {
  bucket                  = aws_s3_bucket.evidence.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 90 }
  }
}

resource "aws_cloudwatch_log_group" "build" {
  name              = "/codebuild/${var.project_name}"
  retention_in_days = var.log_retention_days

  tags = {
    Project   = "supportportal"
    Component = "automation-release-build"
    Owner     = "zac"
  }
}

resource "aws_iam_role" "build" {
  name = "${var.project_name}-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codebuild.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "build" {
  name = "${var.project_name}-policy"
  role = aws_iam_role.build.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrLogin"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "PreproductionImagesAndCache"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeImages",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = local.repository_arns
      },
      {
        Sid      = "VersionedReleaseRequestRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = "${aws_s3_bucket.evidence.arn}/requests/*"
      },
      {
        Sid      = "VersionedReleaseEvidenceWrite"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.evidence.arn}/releases/*"
      },
      {
        Sid      = "EvidenceBucketVersioning"
        Effect   = "Allow"
        Action   = ["s3:GetBucketVersioning"]
        Resource = aws_s3_bucket.evidence.arn
      },
      {
        Sid      = "BuildLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.build.arn}:*"
      },
    ]
  })
}

resource "aws_codebuild_project" "release" {
  name                   = var.project_name
  description            = "Build immutable SupportPortal Automation images for Preproduction"
  service_role           = aws_iam_role.build.arn
  build_timeout          = 75
  concurrent_build_limit = 1

  artifacts { type = "NO_ARTIFACTS" }
  source {
    type      = "NO_SOURCE"
    buildspec = <<-YAML
      version: 0.2
      phases:
        install:
          commands:
            - git clone --filter=blob:none "$AUTOMATION_SOURCE_REPOSITORY_URL" /tmp/supportportal
        pre_build:
          commands:
            - cd /tmp/supportportal
            - git fetch --quiet origin main
            - git cat-file -e "$AUTOMATION_RELEASE_GIT_COMMIT^{commit}"
            - git merge-base --is-ancestor "$AUTOMATION_RELEASE_GIT_COMMIT" origin/main
            - git checkout --detach "$AUTOMATION_RELEASE_GIT_COMMIT"
            - test "$(git rev-parse HEAD)" = "$AUTOMATION_RELEASE_GIT_COMMIT"
        build:
          commands:
            - cd /tmp/supportportal
            - ./deployment/codebuild_build_automation_release.sh
    YAML
  }

  environment {
    compute_type                = "BUILD_GENERAL1_MEDIUM"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    image_pull_credentials_type = "CODEBUILD"
    privileged_mode             = true

    environment_variable {
      name  = "AUTOMATION_SOURCE_REPOSITORY_URL"
      value = var.source_repository_url
    }
    environment_variable {
      name  = "AUTOMATION_PREPRODUCTION_REPOSITORY"
      value = var.preproduction_repository_name
    }
    environment_variable {
      name  = "AUTOMATION_CODEBUILD_CACHE_REPOSITORY"
      value = aws_ecr_repository.cache.name
    }
    environment_variable {
      name  = "AUTOMATION_RELEASE_EVIDENCE_BUCKET"
      value = aws_s3_bucket.evidence.bucket
    }
    environment_variable {
      name  = "AWS_REGION"
      value = var.aws_region
    }
  }

  logs_config {
    cloudwatch_logs {
      group_name  = aws_cloudwatch_log_group.build.name
      stream_name = "release"
    }
  }

  tags = {
    Project     = "supportportal"
    Environment = "release"
    Component   = "automation-codebuild"
    Owner       = "zac"
  }
}
