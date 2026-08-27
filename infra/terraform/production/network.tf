resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb"
  description = "Public ALB for SupportPortal ECS Production."
  vpc_id      = var.vpc_id
  egress      = []

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "ecs" {
  name        = "${local.name_prefix}-ecs"
  description = "ECS Production task network access."
  vpc_id      = var.vpc_id
  egress      = []

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "redis" {
  count       = var.enable_redis ? 1 : 0
  name        = "${local.name_prefix}-redis"
  description = "Private Redis access for ECS Production tasks."
  vpc_id      = var.vpc_id
  egress      = []

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "efs" {
  name        = "${local.name_prefix}-efs"
  description = "EFS access for ECS Production token cache."
  vpc_id      = var.vpc_id
  egress      = []

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "Public HTTP for redirect or bootstrap validation."
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "Public HTTPS ALB ingress."
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_all" {
  security_group_id = aws_security_group.alb.id
  description       = "ALB can reach ECS targets."
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "ecs_from_alb" {
  security_group_id            = aws_security_group.ecs.id
  referenced_security_group_id = aws_security_group.alb.id
  description                  = "Only the ALB can reach the API container."
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "ecs_all" {
  security_group_id = aws_security_group.ecs.id
  description       = "ECS tasks need ECR, RDS, Redis and remote service egress."
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "redis_from_ecs" {
  count                        = var.enable_redis ? 1 : 0
  security_group_id            = aws_security_group.redis[0].id
  referenced_security_group_id = aws_security_group.ecs.id
  description                  = "Only ECS Production tasks can reach Redis."
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "efs_from_ecs" {
  security_group_id            = aws_security_group.efs.id
  referenced_security_group_id = aws_security_group.ecs.id
  description                  = "Only ECS Production tasks can mount the EFS token cache."
  from_port                    = 2049
  to_port                      = 2049
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "efs_all" {
  security_group_id = aws_security_group.efs.id
  description       = "EFS mount targets permit response traffic."
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_ecs" {
  count                        = var.rds_security_group_id != "" ? 1 : 0
  security_group_id            = var.rds_security_group_id
  referenced_security_group_id = aws_security_group.ecs.id
  description                  = "Allow ECS Production tasks to use the existing RDS instance."
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}
