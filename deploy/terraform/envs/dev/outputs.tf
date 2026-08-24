output "url" {
  value = module.platform.url
}

output "alb_dns_name" {
  value = module.platform.alb_dns_name
}

output "cluster_name" {
  value = module.platform.cluster_name
}

output "app_service_name" {
  value = module.platform.app_service_name
}

output "worker_service_name" {
  value = module.platform.worker_service_name
}

output "database_identifier" {
  value = module.platform.database_identifier
}

output "database_master_secret_arn" {
  value = module.platform.database_master_secret_arn
}

output "data_lake_bucket" {
  value = module.platform.data_lake_bucket
}

output "log_group_name" {
  value = module.platform.log_group_name
}

output "secret_names" {
  description = "Populate each of these before the first deploy — see docs/RUNBOOK.md."
  value       = module.platform.secret_names
}

output "ecr_repository_urls" {
  value = module.platform.ecr_repository_urls
}

output "github_deploy_role_arn" {
  value = module.platform.github_deploy_role_arn
}
