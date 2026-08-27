resource "random_password" "redis_auth" {
  count = var.enable_redis ? 1 : 0

  length  = 32
  special = false
}

resource "aws_elasticache_subnet_group" "redis" {
  count      = var.enable_redis ? 1 : 0
  name       = "${local.name_prefix}-redis"
  subnet_ids = local.public_subnet_ids
}

resource "aws_elasticache_replication_group" "redis" {
  count = var.enable_redis ? 1 : 0

  replication_group_id       = "${local.name_prefix}-redis"
  description                = "Cost-first single-node Redis for SupportPortal ECS Production."
  engine                     = "redis"
  engine_version             = var.redis_engine_version
  node_type                  = var.redis_node_type
  port                       = 6379
  num_cache_clusters         = 1
  automatic_failover_enabled = false
  multi_az_enabled           = false
  auth_token                 = local.redis_auth_token
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true
  subnet_group_name          = aws_elasticache_subnet_group.redis[0].name
  security_group_ids         = [aws_security_group.redis[0].id]
}

resource "aws_secretsmanager_secret" "redis_auth" {
  count                   = var.enable_redis ? 1 : 0
  name                    = "${local.name_prefix}/redis-auth-token"
  description             = "Redis AUTH token for ECS Production."
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "redis_auth" {
  count         = var.enable_redis ? 1 : 0
  secret_id     = aws_secretsmanager_secret.redis_auth[0].id
  secret_string = local.redis_auth_token
}

resource "aws_secretsmanager_secret" "redis_url" {
  count                   = var.enable_redis ? 1 : 0
  name                    = "${local.name_prefix}/redis-url"
  description             = "TLS Redis URL for ECS Production."
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "redis_url" {
  count     = var.enable_redis ? 1 : 0
  secret_id = aws_secretsmanager_secret.redis_url[0].id

  secret_string = "rediss://:${local.redis_auth_token}@${aws_elasticache_replication_group.redis[0].primary_endpoint_address}:${aws_elasticache_replication_group.redis[0].port}/0"
}
