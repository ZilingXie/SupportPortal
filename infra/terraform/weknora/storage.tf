# WeKnora document objects. Accessed by the app task role via the AWS default
# credential chain; never public, no existing bucket is referenced.

resource "aws_s3_bucket" "docs" {
  bucket        = local.docs_bucket_name
  force_destroy = false
  tags          = merge(local.tags, { Component = "weknora-docs" })
}

resource "aws_s3_bucket_ownership_controls" "docs" {
  bucket = aws_s3_bucket.docs.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_public_access_block" "docs" {
  bucket                  = aws_s3_bucket.docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Backups (pg_dump of the WeKnora database, phase-one acceptance restores from
# here into an independent verification target).

resource "aws_s3_bucket" "backup" {
  bucket        = local.backup_bucket_name
  force_destroy = false
  tags          = merge(local.tags, { Component = "weknora-backup" })
}

resource "aws_s3_bucket_ownership_controls" "backup" {
  bucket = aws_s3_bucket.backup.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_public_access_block" "backup" {
  bucket                  = aws_s3_bucket.backup.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backup" {
  bucket = aws_s3_bucket.backup.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "backup" {
  bucket = aws_s3_bucket.backup.id
  rule {
    id     = "expire-old-backups"
    status = "Enabled"
    filter {}
    expiration { days = 90 }
  }
}
