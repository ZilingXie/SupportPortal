data "aws_iam_policy_document" "manifest_bucket_tls" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.release_manifest.arn,
      "${aws_s3_bucket.release_manifest.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket" "release_manifest" {
  bucket        = local.manifest_bucket_name
  force_destroy = false
}

resource "aws_s3_bucket_ownership_controls" "release_manifest" {
  bucket = aws_s3_bucket.release_manifest.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "release_manifest" {
  bucket = aws_s3_bucket.release_manifest.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "release_manifest" {
  bucket = aws_s3_bucket.release_manifest.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "release_manifest" {
  bucket = aws_s3_bucket.release_manifest.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "release_manifest" {
  bucket = aws_s3_bucket.release_manifest.id

  rule {
    id     = "expire-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

resource "aws_s3_bucket_policy" "release_manifest" {
  bucket = aws_s3_bucket.release_manifest.id
  policy = data.aws_iam_policy_document.manifest_bucket_tls.json
}
