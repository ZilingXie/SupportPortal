# Automation Release Infrastructure

This root owns only the CodeBuild release builder, its least-privilege IAM role,
the mutable BuildKit cache repository, the encrypted/versioned evidence bucket,
and the build log group. It has no ECS deployment resources or Production ECR
write permissions.

Copy `backend.tf.example` to the ignored `backend.tf`, initialize Terraform
1.9.8, review the full plan, and apply only when the plan contains additions for
this root and no changes to existing Production resources.
