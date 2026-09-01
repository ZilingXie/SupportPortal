data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "${local.name_prefix}-ecs-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "ecs_task" {
  name               = "${local.name_prefix}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role" "ecs_worker_task" {
  name               = "${local.name_prefix}-ecs-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "ecs_task_secrets" {
  statement {
    sid     = "ReadRuntimeSecrets"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = concat(
      [for secret in aws_secretsmanager_secret.runtime : secret.arn],
      var.enable_redis ? [aws_secretsmanager_secret.redis_auth[0].arn, aws_secretsmanager_secret.redis_url[0].arn] : [],
    )
  }

  statement {
    sid       = "ReadRuntimeParameters"
    effect    = "Allow"
    actions   = ["ssm:GetParameters"]
    resources = values(local.runtime_parameter_arns)
  }
}

resource "aws_iam_role_policy" "ecs_task_secrets" {
  name   = "${local.name_prefix}-read-secrets"
  role   = aws_iam_role.ecs_task_execution.id
  policy = data.aws_iam_policy_document.ecs_task_secrets.json
}

data "aws_iam_policy_document" "ecs_task_efs" {
  statement {
    sid     = "MountTokenCache"
    effect  = "Allow"
    actions = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
    resources = [
      aws_efs_file_system.automation.arn,
      aws_efs_access_point.automation.arn,
    ]
  }
}

resource "aws_iam_role_policy" "ecs_task_efs" {
  name   = "${local.name_prefix}-efs-token-cache"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ecs_task_efs.json
}

resource "aws_iam_role_policy" "ecs_worker_task_efs" {
  name   = "${local.name_prefix}-efs-token-cache"
  role   = aws_iam_role.ecs_worker_task.id
  policy = data.aws_iam_policy_document.ecs_task_efs.json
}

data "aws_iam_policy_document" "ecs_task_pilot_efs" {
  count = var.enable_services ? 1 : 0

  statement {
    sid     = "MountPilotCredentials"
    effect  = "Allow"
    actions = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
    resources = [
      "arn:aws:elasticfilesystem:${var.aws_region}:${data.aws_caller_identity.current.account_id}:file-system/${var.pilot_efs_file_system_id}",
    ]

    condition {
      test     = "StringEquals"
      variable = "elasticfilesystem:AccessPointArn"
      values = [
        "arn:aws:elasticfilesystem:${var.aws_region}:${data.aws_caller_identity.current.account_id}:access-point/${var.pilot_efs_access_point_id}",
      ]
    }
  }
}

resource "aws_iam_role_policy" "ecs_task_pilot_efs" {
  count = var.enable_services ? 1 : 0

  name   = "${local.name_prefix}-efs-pilot-creds"
  role   = aws_iam_role.ecs_worker_task.id
  policy = data.aws_iam_policy_document.ecs_task_pilot_efs[0].json
}

data "aws_iam_policy_document" "github_oidc_assume" {
  statement {
    effect = "Allow"

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    actions = ["sts:AssumeRoleWithWebIdentity"]

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repository}:ref:refs/heads/${var.github_branch}",
        "repo:${var.github_repository}:environment:*",
      ]
    }
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [var.github_oidc_thumbprint]
}

resource "aws_iam_role" "github_release" {
  name               = "${local.name_prefix}-github-release"
  assume_role_policy = data.aws_iam_policy_document.github_oidc_assume.json
}

data "aws_iam_policy_document" "github_release" {
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PushReleaseImages"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.runtime.arn]
  }

  statement {
    sid    = "InspectAndUpdateEcs"
    effect = "Allow"
    actions = [
      "ecs:DescribeClusters",
      "ecs:DescribeServices",
      "ecs:DescribeTaskDefinition",
      "ecs:DescribeTasks",
      "ecs:ListTasks",
      "ecs:RegisterTaskDefinition",
      "ecs:RunTask",
      "ecs:StopTask",
      "ecs:TagResource",
      "ecs:UpdateService",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "PassEcsRoles"
    effect  = "Allow"
    actions = ["iam:PassRole"]
    resources = [
      aws_iam_role.ecs_task_execution.arn,
      aws_iam_role.ecs_task.arn,
      aws_iam_role.ecs_worker_task.arn,
    ]
  }

  statement {
    sid    = "ReadAndWriteManifest"
    effect = "Allow"
    actions = [
      "s3:GetBucketLocation",
      "s3:ListBucket",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = [
      aws_s3_bucket.release_manifest.arn,
      "${aws_s3_bucket.release_manifest.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "github_release" {
  name   = "${local.name_prefix}-github-release-policy"
  role   = aws_iam_role.github_release.id
  policy = data.aws_iam_policy_document.github_release.json
}
