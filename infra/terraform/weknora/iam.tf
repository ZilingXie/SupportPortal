# ---------------------------------------------------------------------------
# Task execution role (image pull + secrets from /supportportal/weknora/*).
# SecureString values are created out-of-band via CLI and are never Terraform
# resources or variables.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "task_execution" {
  name = "supportportal-weknora-ecs-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "task_execution_parameters" {
  name = "supportportal-weknora-parameters"
  role = aws_iam_role.task_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter", "ssm:GetParameters"]
      Resource = "${local.parameter_prefix_arn}/*"
    }]
  })
}

# ---------------------------------------------------------------------------
# App task role: document objects in the dedicated bucket (default cred chain).
# ---------------------------------------------------------------------------

resource "aws_iam_role" "app_task" {
  name = "supportportal-weknora-app-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "app_task_docs" {
  name = "supportportal-weknora-docs-bucket"
  role = aws_iam_role.app_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DocsList"
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation", "s3:ListBucket"]
        Resource = aws_s3_bucket.docs.arn
      },
      {
        Sid    = "DocsObjects"
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = "${aws_s3_bucket.docs.arn}/*"
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Ops task role for one-off tasks (controlled migration, pg_dump backup,
# restore-into-verification-target) run via run-task.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "ops_task" {
  name = "supportportal-weknora-ops-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "ops_task_buckets" {
  name = "supportportal-weknora-ops-buckets"
  role = aws_iam_role.ops_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "BackupBucket"
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation", "s3:ListBucket"]
        Resource = aws_s3_bucket.backup.arn
      },
      {
        Sid    = "BackupObjects"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
        Resource = [
          "${aws_s3_bucket.backup.arn}/*",
          "${aws_s3_bucket.docs.arn}/*",
        ]
      },
      {
        Sid      = "DocsList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.docs.arn
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# CodeBuild: builds the three WeKnora images from a pinned source archive in
# the release-evidence bucket and pushes them to the WeKnora ECR repo.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "codebuild" {
  name = "supportportal-weknora-codebuild"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codebuild.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "codebuild" {
  name = "supportportal-weknora-build"
  role = aws_iam_role.codebuild.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid = "SourceArchive"
        # Pinned S3 source versions download via s3:GetObjectVersion.
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:GetBucketLocation",
          "s3:ListBucket",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:s3:::${local.source_bucket_name}",
          "arn:${data.aws_partition.current.partition}:s3:::${local.source_bucket_name}/weknora/*",
        ]
      },
      {
        Sid      = "EcrAuthToken"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = ["*"]
      },
      {
        Sid    = "EcrPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
          "ecr:DescribeImages",
        ]
        Resource = [aws_ecr_repository.weknora.arn]
      },
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      },
    ]
  })
}

locals {
  weknora_buildspec = <<-EOT
    version: 0.2
    env:
      variables:
        ECR_REPO: "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com/${local.ecr_repo_name}"
    phases:
      pre_build:
        commands:
          - set -e
          - COMMIT="${"$"}{WEKNORA_COMMIT:-}"
          - if [ -z "$COMMIT" ]; then echo "WEKNORA_COMMIT override is required"; exit 1; fi
          - aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com
          # The S3 source archive is auto-extracted into CODEBUILD_SRC_DIR; if the
          # raw archive is still present (no auto-extract), extract it ourselves.
          - SRC_ROOT=$CODEBUILD_SRC_DIR
          - if [ -f "$SRC_ROOT/source.tar.gz" ]; then tar -xzf "$SRC_ROOT/source.tar.gz" -C "$SRC_ROOT"; fi
          - if [ -d "$SRC_ROOT/weknora-src" ]; then SRC_ROOT="$SRC_ROOT/weknora-src"; fi
          - cd "$SRC_ROOT"
          - grep -qx "commit=$COMMIT" WEKNORA_COMMIT_INFO || { echo "source archive does not match WEKNORA_COMMIT"; cat WEKNORA_COMMIT_INFO || true; exit 1; }
          - echo "verified source provenance:" && cat WEKNORA_COMMIT_INFO
      build:
        commands:
          - docker build -f docker/Dockerfile.app --build-arg VERSION_ARG="$WEKNORA_VERSION" --build-arg COMMIT_ID_ARG="$COMMIT" --build-arg BUILD_TIME_ARG="$(date -u +%Y-%m-%dT%H:%M:%SZ)" -t "$ECR_REPO:app-$COMMIT" .
          - docker build -f frontend/Dockerfile --build-arg VITE_FRONTEND_COMMIT="$COMMIT" --build-arg VITE_BASE_URL=/dashboard/weknora/ -t "$ECR_REPO:frontend-$COMMIT" frontend/
          - docker build -f docker/Dockerfile.docreader -t "$ECR_REPO:docreader-$COMMIT" .
      post_build:
        commands:
          - docker push "$ECR_REPO:app-$COMMIT"
          - docker push "$ECR_REPO:frontend-$COMMIT"
          - docker push "$ECR_REPO:docreader-$COMMIT"
          # Base image copies so the deployment never depends on Docker Hub:
          # pin the exact upstream tags the compose file uses.
          - docker pull docker.io/paradedb/paradedb:v0.22.6-pg17
          - docker tag docker.io/paradedb/paradedb:v0.22.6-pg17 "$ECR_REPO:base-paradedb-v0.22.6-pg17"
          - docker push "$ECR_REPO:base-paradedb-v0.22.6-pg17"
          - docker pull docker.io/library/redis:7.0-alpine
          - docker tag docker.io/library/redis:7.0-alpine "$ECR_REPO:base-redis-7.0-alpine"
          - docker push "$ECR_REPO:base-redis-7.0-alpine"
          - echo "image manifest (record into release evidence)"
          - aws ecr describe-images --repository-name "${local.ecr_repo_name}" --image-ids imageTag=app-$COMMIT imageTag=frontend-$COMMIT imageTag=docreader-$COMMIT imageTag=base-paradedb-v0.22.6-pg17 imageTag=base-redis-7.0-alpine --query 'imageDetails[].{tag:imageTags[0],digest:imageDigest,pushed:imagePushedAt}' --output json
  EOT
}

resource "aws_codebuild_project" "image_build" {
  name          = "supportportal-weknora-image-build"
  description   = "Build WeKnora app/frontend/docreader images from a pinned source archive and push to ECR"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 240

  artifacts {
    type = "NO_ARTIFACTS"
  }

  source {
    type      = "S3"
    location  = "${local.source_bucket_name}/weknora/src/source.tar.gz"
    buildspec = local.weknora_buildspec
  }

  environment {
    compute_type    = "BUILD_GENERAL1_LARGE"
    image           = "aws/codebuild/standard:7.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true

    environment_variable {
      name  = "WEKNORA_VERSION"
      value = "phase1"
    }
  }

  logs_config {
    cloudwatch_logs {
      group_name  = "/codebuild/supportportal-weknora-image-build"
      stream_name = "build"
    }
  }

  tags = local.tags
}

resource "aws_cloudwatch_log_group" "codebuild" {
  name              = "/codebuild/supportportal-weknora-image-build"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}
