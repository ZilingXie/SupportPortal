resource "aws_ecr_repository" "runtime" {
  name                 = "supportportal/production"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Project     = "supportportal"
    Environment = "production"
    Owner       = "zac"
    System      = "automation"
    Name        = "supportportal/production"
  }
}
