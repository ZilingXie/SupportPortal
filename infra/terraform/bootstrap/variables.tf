variable "aws_region" {
  description = "AWS region for the Terraform state bucket."
  type        = string
  default     = "us-east-1"
}

variable "state_bucket_name" {
  description = "Globally unique S3 bucket name for encrypted Terraform state."
  type        = string
}

variable "lock_table_name" {
  description = "DynamoDB lock table name."
  type        = string
  default     = "supportportal-terraform-locks"
}
