resource "aws_efs_access_point" "graph" {
  file_system_id = var.shared_graph_efs_file_system_id

  posix_user {
    gid = 1000
    uid = 1000
  }

  root_directory {
    path = "/supportportal-preproduction-graph"
    creation_info {
      owner_gid   = 1000
      owner_uid   = 1000
      permissions = "0750"
    }
  }

  tags = merge(local.tags, { Component = "graph-token-cache" })
}

locals {
  hermes_efs_roots = {
    hermes-home = {
      path        = "/supportportal-preproduction-hermes-home"
      uid         = 10000
      gid         = 10000
      permissions = "0755"
    }
    tdai-data = {
      path        = "/supportportal-preproduction-tdai-data"
      uid         = 0
      gid         = 0
      permissions = "0755"
    }
    pilot-creds = {
      path        = "/supportportal-preproduction-pilot-creds"
      uid         = 10000
      gid         = 10000
      permissions = "0700"
    }
  }
}

resource "aws_efs_access_point" "hermes" {
  for_each       = local.hermes_efs_roots
  file_system_id = var.shared_graph_efs_file_system_id

  posix_user {
    gid = each.value.gid
    uid = each.value.uid
  }

  root_directory {
    path = each.value.path
    creation_info {
      owner_gid   = each.value.gid
      owner_uid   = each.value.uid
      permissions = each.value.permissions
    }
  }

  tags = merge(local.tags, {
    Name      = "supportportal-preproduction-${each.key}"
    Component = "hermes-${each.key}"
  })
}

resource "aws_s3_bucket" "hermes_backup" {
  bucket        = local.backup_bucket_name
  force_destroy = false
  tags          = merge(local.tags, { Component = "hermes-migration-backup" })
}

resource "aws_s3_bucket_ownership_controls" "hermes_backup" {
  bucket = aws_s3_bucket.hermes_backup.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_public_access_block" "hermes_backup" {
  bucket                  = aws_s3_bucket.hermes_backup.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "hermes_backup" {
  bucket = aws_s3_bucket.hermes_backup.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "hermes_backup" {
  bucket = aws_s3_bucket.hermes_backup.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "hermes_backup" {
  bucket = aws_s3_bucket.hermes_backup.id
  rule {
    id     = "expire-migration-backups"
    status = "Enabled"
    filter {}
    expiration { days = 30 }
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
}
