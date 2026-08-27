# Terraform State Bootstrap

This root creates an encrypted, versioned S3 bucket and a pay-per-request
DynamoDB lock table. Apply it once with local state, then configure the S3
backend in `production` before managing the ECS foundation.

Do not commit `terraform.tfvars`. The state bucket name must be globally
unique and should be chosen for the intended AWS account.
