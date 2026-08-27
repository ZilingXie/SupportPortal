resource "aws_secretsmanager_secret" "runtime" {
  for_each = local.runtime_secret_names

  name                    = each.value
  description             = "SupportPortal ECS Production runtime secret: ${each.key}."
  recovery_window_in_days = 7
}
