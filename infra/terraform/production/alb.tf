resource "aws_lb_target_group" "automation" {
  name        = var.automation_target_group_name
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    enabled             = true
    path                = "/automation/production/health/live"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = {
    Project = "supportportal"
    Owner   = "zac"
  }
}

resource "aws_lb_listener_rule" "automation_https" {
  listener_arn = data.aws_lb_listener.shared_https.arn
  priority     = var.automation_listener_rule_priority

  action {
    type = "forward"

    forward {
      target_group {
        arn    = aws_lb_target_group.automation.arn
        weight = 1
      }

      stickiness {
        enabled  = false
        duration = 3600
      }
    }
  }

  condition {
    path_pattern {
      values = ["/automation/production", "/automation/production/*"]
    }
  }
}
