variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "supportportal-automation-release"
}

variable "source_repository_url" {
  type    = string
  default = "https://github.com/ZilingXie/SupportPortal.git"
}

variable "preproduction_repository_name" {
  type    = string
  default = "supportportal/preproduction"

  validation {
    condition     = var.preproduction_repository_name == "supportportal/preproduction"
    error_message = "CodeBuild is restricted to the Preproduction release repository."
  }
}

variable "cache_repository_name" {
  type    = string
  default = "supportportal/build-cache"
}

variable "evidence_bucket_name" {
  type    = string
  default = ""
}

variable "log_retention_days" {
  type    = number
  default = 30
}
