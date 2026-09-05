output "codebuild_project_name" {
  value = aws_codebuild_project.release.name
}

output "cache_repository_url" {
  value = aws_ecr_repository.cache.repository_url
}

output "evidence_bucket_name" {
  value = aws_s3_bucket.evidence.bucket
}

output "build_role_arn" {
  value = aws_iam_role.build.arn
}
