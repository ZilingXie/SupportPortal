resource "aws_efs_file_system" "automation" {
  creation_token   = "${local.name_prefix}-token-cache"
  encrypted        = true
  performance_mode = "generalPurpose"
  throughput_mode  = "bursting"

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }
}

resource "aws_efs_mount_target" "automation" {
  for_each = local.public_subnet_ids_by_az

  file_system_id  = aws_efs_file_system.automation.id
  subnet_id       = each.value[0]
  security_groups = [aws_security_group.efs.id]
}

resource "aws_efs_access_point" "automation" {
  file_system_id = aws_efs_file_system.automation.id

  posix_user {
    gid = 1000
    uid = 1000
  }

  root_directory {
    path = "/automation-production"

    creation_info {
      owner_gid   = 1000
      owner_uid   = 1000
      permissions = "0750"
    }
  }
}
