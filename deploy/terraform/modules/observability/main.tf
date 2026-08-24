# Log groups, alarms, and a dashboard for the platform itself.
#
# A tool that preaches observability has to be observable (§18). What is here
# reflects what the application genuinely emits today: structured JSON logs to
# CloudWatch, plus the infrastructure-level signals ECS, the ALB, and RDS
# publish on their own. Application-level metrics (query latency by engine,
# cache hit rate, agent turn latency, LLM token cost) need the OpenTelemetry
# and Prometheus wiring described in §18, which is not yet implemented — see
# docs/ASSUMPTIONS.md. No alarm here is defined on a metric nobody publishes,
# because an alarm stuck in INSUFFICIENT_DATA teaches an on-call engineer to
# ignore the dashboard.

resource "aws_cloudwatch_log_group" "app" {
  name              = "/aws/ecs/${var.name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn

  tags = var.tags
}

locals {
  alarm_actions = var.alarm_topic_arn == null ? [] : [var.alarm_topic_arn]
}

# ── availability ────────────────────────────────────────────────────────────
resource "aws_cloudwatch_metric_alarm" "unhealthy_hosts" {
  alarm_name        = "${var.name}-unhealthy-targets"
  alarm_description = "One or more app tasks are failing /readyz. Runbook: docs/RUNBOOK.md#the-app-is-down"

  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
  tags          = var.tags
}

resource "aws_cloudwatch_metric_alarm" "target_5xx" {
  alarm_name        = "${var.name}-app-5xx"
  alarm_description = "The application is returning server errors. Runbook: docs/RUNBOOK.md#the-app-is-erroring"

  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.error_count_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }

  alarm_actions = local.alarm_actions
  tags          = var.tags
}

resource "aws_cloudwatch_metric_alarm" "latency" {
  alarm_name        = "${var.name}-app-latency-p95"
  alarm_description = "p95 response time above the §22.3 target. Runbook: docs/RUNBOOK.md#dashboards-are-slow"

  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.latency_p95_threshold_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }

  alarm_actions = local.alarm_actions
  tags          = var.tags
}

# ── capacity ────────────────────────────────────────────────────────────────
resource "aws_cloudwatch_metric_alarm" "app_cpu" {
  alarm_name        = "${var.name}-app-cpu-high"
  alarm_description = "App service pinned at its scaling ceiling. Runbook: docs/RUNBOOK.md#dashboards-are-slow"

  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 85
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = var.cluster_name
    ServiceName = var.app_service_name
  }

  alarm_actions = local.alarm_actions
  tags          = var.tags
}

resource "aws_cloudwatch_metric_alarm" "worker_stopped" {
  alarm_name        = "${var.name}-worker-not-running"
  alarm_description = "No worker task is running: scheduled alert-rule evaluation will not happen, so a breaching condition goes unnoticed rather than unpaged. Runbook: docs/RUNBOOK.md#the-worker-is-not-running"

  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  statistic           = "SampleCount"
  period              = 300
  evaluation_periods  = 2
  threshold           = 0
  comparison_operator = "LessThanOrEqualToThreshold"
  # Missing data here means the service is reporting nothing at all, which is
  # exactly the condition being alarmed on.
  treat_missing_data = "breaching"

  dimensions = {
    ClusterName = var.cluster_name
    ServiceName = var.worker_service_name
  }

  alarm_actions = local.alarm_actions
  tags          = var.tags
}

# ── data tier ───────────────────────────────────────────────────────────────
resource "aws_cloudwatch_metric_alarm" "database_cpu" {
  alarm_name        = "${var.name}-rds-cpu-high"
  alarm_description = "RDS CPU sustained high. Runbook: docs/RUNBOOK.md#the-metadata-database-is-struggling"

  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = var.database_identifier
  }

  alarm_actions = local.alarm_actions
  tags          = var.tags
}

resource "aws_cloudwatch_metric_alarm" "database_storage" {
  alarm_name        = "${var.name}-rds-storage-low"
  alarm_description = "RDS free storage below threshold. Runbook: docs/RUNBOOK.md#the-metadata-database-is-struggling"

  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.database_free_storage_threshold_bytes
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = var.database_identifier
  }

  alarm_actions = local.alarm_actions
  tags          = var.tags
}

# ── dashboard ───────────────────────────────────────────────────────────────
resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = var.name

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title  = "Requests and errors"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", var.alb_arn_suffix, { stat = "Sum" }],
            [".", "HTTPCode_Target_5XX_Count", ".", ".", { stat = "Sum" }],
            [".", "HTTPCode_Target_4XX_Count", ".", ".", { stat = "Sum" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title  = "Response time (p50 / p95 / p99)"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, { stat = "p50" }],
            ["...", { stat = "p95" }],
            ["...", { stat = "p99" }],
          ]
          annotations = {
            horizontal = [{
              label = "§22.3 warm-tile target"
              value = var.latency_p95_threshold_seconds
            }]
          }
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title  = "ECS utilisation"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", var.cluster_name, "ServiceName", var.app_service_name],
            [".", "MemoryUtilization", ".", ".", ".", "."],
            [".", "CPUUtilization", ".", ".", "ServiceName", var.worker_service_name],
            [".", "MemoryUtilization", ".", ".", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title  = "Metadata database"
          region = var.region
          view   = "timeSeries"
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.database_identifier],
            [".", "DatabaseConnections", ".", "."],
            [".", "FreeableMemory", ".", "."],
          ]
        }
      },
      {
        type = "log", x = 0, y = 12, width = 24, height = 6
        properties = {
          title  = "Recent errors"
          region = var.region
          query  = "SOURCE '${aws_cloudwatch_log_group.app.name}' | fields @timestamp, event, error, trace_id | filter level = 'error' | sort @timestamp desc | limit 50"
          view   = "table"
        }
      },
    ]
  })
}
