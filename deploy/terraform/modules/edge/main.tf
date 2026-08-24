# The load balancer, its certificate, its DNS record, and an optional WAF.
#
# `internal = true` is the default and the recommendation. This application
# holds a read-only credential to the customer's data warehouse and shows their
# entire cost structure; putting it on the public internet should be a decision
# somebody makes on purpose, not the default a module hands them.
#
# TLS terminates here. There is no HTTP listener that serves anything: port 80
# exists only to redirect, so a bookmarked http:// link still works without ever
# carrying a session cookie in the clear.

locals {
  # Access logging is opt-in by bucket name. The bucket and its policy are the
  # caller's to create: ALB log delivery needs a bucket policy that grants the
  # regional log-delivery principal, and that belongs with whoever owns the
  # organisation's log archive, not with this module.
  enable_logs = var.access_logs_bucket != null
}

resource "aws_lb" "this" {
  name               = substr("${var.name}-alb", 0, 32)
  load_balancer_type = "application"
  internal           = var.internal
  security_groups    = [var.security_group_id]
  subnets            = var.internal ? var.private_subnet_ids : var.public_subnet_ids

  drop_invalid_header_fields = true
  enable_deletion_protection = var.deletion_protection
  # Long enough for a cold LIVE dashboard tile on an XSMALL warehouse (§22.3
  # allows 8 s p95) plus an agent's full grounded answer (20 s p95), with room
  # for the SSE stream that carries it.
  idle_timeout = var.idle_timeout_seconds

  dynamic "access_logs" {
    for_each = local.enable_logs ? [1] : []
    content {
      bucket  = var.access_logs_bucket
      prefix  = var.name
      enabled = true
    }
  }

  tags = var.tags
}

resource "aws_lb_target_group" "app" {
  name        = substr("${var.name}-app", 0, 32)
  port        = var.app_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  # /readyz, not /healthz: the load balancer should stop sending traffic to a
  # task that cannot reach Postgres or Redis, while the container supervisor
  # keeps it alive on liveness. That split is the whole point of having two
  # endpoints (§18).
  health_check {
    path                = "/readyz"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 30

  stickiness {
    type    = "lb_cookie"
    enabled = false
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = var.tags
}

resource "aws_acm_certificate" "this" {
  count = var.certificate_arn == null ? 1 : 0

  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = var.tags
}

resource "aws_route53_record" "validation" {
  for_each = var.certificate_arn == null ? {
    for option in aws_acm_certificate.this[0].domain_validation_options :
    option.domain_name => option
  } : {}

  zone_id         = var.hosted_zone_id
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  records         = [each.value.resource_record_value]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "this" {
  count = var.certificate_arn == null ? 1 : 0

  certificate_arn         = aws_acm_certificate.this[0].arn
  validation_record_fqdns = [for r in aws_route53_record.validation : r.fqdn]
}

locals {
  certificate_arn = var.certificate_arn != null ? var.certificate_arn : aws_acm_certificate_validation.this[0].certificate_arn
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = var.ssl_policy
  certificate_arn   = local.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  tags = var.tags
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  tags = var.tags
}

resource "aws_route53_record" "app" {
  count = var.hosted_zone_id == null ? 0 : 1

  zone_id = var.hosted_zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = true
  }
}

# ── WAF ─────────────────────────────────────────────────────────────────────
# Off by default for an internal load balancer (the managed rule groups mostly
# defend against internet-scale noise that never reaches a private ALB), and
# strongly recommended for an internet-facing one.
resource "aws_wafv2_web_acl" "this" {
  count = var.enable_waf ? 1 : 0

  name        = "${var.name}-waf"
  description = "${var.name} — managed baseline plus a rate limit"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 1

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-common"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "RateLimitPerIp"
    priority = 3

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name}-waf"
    sampled_requests_enabled   = true
  }

  tags = var.tags
}

resource "aws_wafv2_web_acl_association" "this" {
  count = var.enable_waf ? 1 : 0

  resource_arn = aws_lb.this.arn
  web_acl_arn  = aws_wafv2_web_acl.this[0].arn
}
