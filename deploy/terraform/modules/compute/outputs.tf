output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "cluster_arn" {
  value = aws_ecs_cluster.this.arn
}

output "app_service_name" {
  description = "For `aws ecs update-service --force-new-deployment` in the RUNBOOK."
  value       = aws_ecs_service.app.name
}

output "worker_service_name" {
  value = aws_ecs_service.worker.name
}

output "app_task_definition_family" {
  value = aws_ecs_task_definition.app.family
}

output "worker_task_definition_family" {
  value = aws_ecs_task_definition.worker.family
}
