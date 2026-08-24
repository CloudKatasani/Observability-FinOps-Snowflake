output "alb_arn" {
  value = aws_lb.this.arn
}

output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "alb_arn_suffix" {
  description = "For CloudWatch alarm dimensions."
  value       = aws_lb.this.arn_suffix
}

output "target_group_arn" {
  value = aws_lb_target_group.app.arn
}

output "target_group_arn_suffix" {
  value = aws_lb_target_group.app.arn_suffix
}

output "target_group_label" {
  description = "ALBRequestCountPerTarget resource label for the compute module's scaling policy."
  value       = "${aws_lb.this.arn_suffix}/${aws_lb_target_group.app.arn_suffix}"
}

output "https_listener_arn" {
  description = "Depend on this from the ECS service so registration waits for the listener."
  value       = aws_lb_listener.https.arn
}

output "url" {
  value = "https://${var.domain_name}"
}
