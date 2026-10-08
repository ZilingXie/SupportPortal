# ---------------------------------------------------------------------------
# Security groups. Fargate tasks (frontend/app/docreader) share weknora_tasks;
# the data instance tasks (paradedb/redis) use weknora_data and accept traffic
# only from weknora_tasks. Nothing in this root exposes 5432/6379/50051 to the
# internet.
# ---------------------------------------------------------------------------

resource "aws_security_group" "tasks" {
  name        = "supportportal-weknora-ecs"
  description = "WeKnora Fargate tasks (frontend/app/docreader)"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "supportportal-weknora-ecs" })
}

resource "aws_security_group" "data" {
  name        = "supportportal-weknora-data"
  description = "WeKnora data capacity (ParadeDB/Redis); ingress only from WeKnora tasks"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "supportportal-weknora-data" })
}

resource "aws_vpc_security_group_ingress_rule" "frontend_from_alb" {
  security_group_id            = aws_security_group.tasks.id
  referenced_security_group_id = var.shared_alb_security_group_id
  from_port                    = 80
  to_port                      = 80
  ip_protocol                  = "tcp"
  description                  = "Shared ALB to WeKnora frontend nginx"
}

resource "aws_vpc_security_group_ingress_rule" "app_from_tasks" {
  security_group_id            = aws_security_group.tasks.id
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 8080
  to_port                      = 8080
  ip_protocol                  = "tcp"
  description                  = "WeKnora frontend to WeKnora app"
}

resource "aws_vpc_security_group_ingress_rule" "docreader_from_tasks" {
  security_group_id            = aws_security_group.tasks.id
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 50051
  to_port                      = 50051
  ip_protocol                  = "tcp"
  description                  = "WeKnora app to docreader gRPC"
}

resource "aws_vpc_security_group_ingress_rule" "postgres_from_tasks" {
  security_group_id            = aws_security_group.data.id
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "WeKnora app to ParadeDB"
}

# Ops path: pg_dump/psql via SSM port-forwarding from the capacity instance
# (backup_weknora_database.sh). The paradedb task ENI shares this group, so the
# instance-to-task hop needs a self-referencing rule.
resource "aws_vpc_security_group_ingress_rule" "postgres_from_data_instance" {
  security_group_id            = aws_security_group.data.id
  referenced_security_group_id = aws_security_group.data.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "Capacity instance (SSM ops port-forward) to ParadeDB task"
}

resource "aws_vpc_security_group_ingress_rule" "redis_from_tasks" {
  security_group_id            = aws_security_group.data.id
  referenced_security_group_id = aws_security_group.tasks.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
  description                  = "WeKnora app to Redis"
}

# ---------------------------------------------------------------------------
# ALB integration: dedicated target group + listener rule for
# /dashboard/weknora only. No existing rule is modified.
# ---------------------------------------------------------------------------

resource "aws_lb_target_group" "frontend" {
  name        = "supportportal-weknora-ui-tg"
  port        = 80
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    enabled             = true
    path                = "/dashboard/weknora/"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = local.tags
}

resource "aws_lb_listener_rule" "frontend_https" {
  listener_arn = var.shared_https_listener_arn
  priority     = var.listener_rule_priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }

  condition {
    path_pattern {
      values = ["/dashboard/weknora", "/dashboard/weknora/*"]
    }
  }
}

# ---------------------------------------------------------------------------
# Private service discovery for internal addressing
# (paradedb.weknora.supportportal.local etc.).
# ---------------------------------------------------------------------------

resource "aws_service_discovery_private_dns_namespace" "weknora" {
  name        = "weknora.supportportal.local"
  description = "WeKnora standalone internal services"
  vpc         = var.vpc_id
  tags        = local.tags
}

resource "aws_service_discovery_service" "paradedb" {
  name = "paradedb"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.weknora.id
    routing_policy = "MULTIVALUE"
    dns_records {
      ttl  = 10
      type = "A"
    }
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  tags = merge(local.tags, { Component = "paradedb" })
}

resource "aws_service_discovery_service" "redis" {
  name = "redis"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.weknora.id
    routing_policy = "MULTIVALUE"
    dns_records {
      ttl  = 10
      type = "A"
    }
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  tags = merge(local.tags, { Component = "redis" })
}

resource "aws_service_discovery_service" "docreader" {
  name = "docreader"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.weknora.id
    routing_policy = "MULTIVALUE"
    dns_records {
      ttl  = 10
      type = "A"
    }
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  tags = merge(local.tags, { Component = "docreader" })
}

resource "aws_service_discovery_service" "app" {
  name = "app"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.weknora.id
    routing_policy = "MULTIVALUE"
    dns_records {
      ttl  = 10
      type = "A"
    }
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  tags = merge(local.tags, { Component = "app" })
}
