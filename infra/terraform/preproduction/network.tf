resource "aws_security_group" "ecs" {
  name        = "supportportal-preproduction-ecs"
  description = "Preproduction Automation Fargate tasks"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "supportportal-preproduction-ecs" })
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.ecs.id
  referenced_security_group_id = var.shared_alb_security_group_id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
  description                  = "Shared ALB to Preproduction API"
}

resource "aws_vpc_security_group_ingress_rule" "hermes_from_preproduction" {
  security_group_id            = aws_security_group.ecs.id
  referenced_security_group_id = aws_security_group.ecs.id
  from_port                    = 8642
  to_port                      = 8642
  ip_protocol                  = "tcp"
  description                  = "Preproduction Account tasks to private Hermes"
}

resource "aws_vpc_security_group_ingress_rule" "efs_from_preproduction" {
  security_group_id            = var.shared_efs_security_group_id
  referenced_security_group_id = aws_security_group.ecs.id
  from_port                    = 2049
  to_port                      = 2049
  ip_protocol                  = "tcp"
  description                  = "Preproduction tasks to shared EFS mount targets"
}

resource "aws_lb_target_group" "automation" {
  name        = "supportportal-preproduction-tg"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    enabled             = true
    path                = "/automation/preproduction/health/live"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = local.tags
}

resource "aws_lb_listener_rule" "automation_https" {
  listener_arn = var.shared_https_listener_arn
  priority     = var.listener_rule_priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.automation.arn
  }

  condition {
    path_pattern {
      values = ["/automation/preproduction", "/automation/preproduction/*"]
    }
  }
}

resource "aws_service_discovery_private_dns_namespace" "preproduction" {
  name = "preproduction.supportportal.local"
  vpc  = var.vpc_id
  tags = local.tags
}

resource "aws_service_discovery_service" "hermes" {
  name = "hermes"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.preproduction.id
    routing_policy = "MULTIVALUE"
    dns_records {
      ttl  = 10
      type = "A"
    }
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  tags = merge(local.tags, { Component = "hermes-discovery" })
}
