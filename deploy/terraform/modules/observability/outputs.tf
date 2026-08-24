output "log_group_name" {
  value = aws_cloudwatch_log_group.app.name
}

output "log_group_arn" {
  value = aws_cloudwatch_log_group.app.arn
}

output "dashboard_name" {
  value = aws_cloudwatch_dashboard.this.dashboard_name
}

output "alarm_names" {
  description = "Every alarm this module creates, for the on-call triage table in docs/RUNBOOK.md."
  value = [
    aws_cloudwatch_metric_alarm.unhealthy_hosts.alarm_name,
    aws_cloudwatch_metric_alarm.target_5xx.alarm_name,
    aws_cloudwatch_metric_alarm.latency.alarm_name,
    aws_cloudwatch_metric_alarm.app_cpu.alarm_name,
    aws_cloudwatch_metric_alarm.worker_stopped.alarm_name,
    aws_cloudwatch_metric_alarm.database_cpu.alarm_name,
    aws_cloudwatch_metric_alarm.database_storage.alarm_name,
  ]
}
