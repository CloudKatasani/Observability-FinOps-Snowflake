output "url" {
  description = "Where the platform is served."
  value       = module.edge.url
}

output "alb_dns_name" {
  description = "Point your own DNS here when hosted_zone_id is not managed by this stack."
  value       = module.edge.alb_dns_name
}

output "cluster_name" {
  value = module.compute.cluster_name
}

output "app_service_name" {
  value = module.compute.app_service_name
}

output "worker_service_name" {
  value = module.compute.worker_service_name
}

output "app_task_definition_family" {
  value = module.compute.app_task_definition_family
}

output "worker_task_definition_family" {
  value = module.compute.worker_task_definition_family
}

output "database_identifier" {
  value = module.data.database_identifier
}

output "database_endpoint" {
  value = module.data.database_endpoint
}

output "database_master_secret_arn" {
  description = "RDS-managed master password. Read it to compose the app's DATABASE_URL secret."
  value       = module.data.database_master_secret_arn
}

output "data_lake_bucket" {
  value = module.data.data_lake_bucket
}

output "log_group_name" {
  value = module.observability.log_group_name
}

output "alarm_names" {
  value = module.observability.alarm_names
}

output "secret_names" {
  description = <<-EOT
    Secret containers created for this deployment. Populate each one with
    `aws secretsmanager put-secret-value` — Terraform creates them empty on
    purpose, so no secret value is ever in state (§27.13).
  EOT
  value       = module.security.secret_names
}

output "kms_key_arn" {
  value = module.security.kms_key_arn
}

output "ecr_repository_urls" {
  value = var.create_ci_resources ? module.ci[0].repository_urls : {}
}

output "github_deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN variable in the GitHub repository."
  value       = var.create_ci_resources ? module.ci[0].deploy_role_arn : null
}
