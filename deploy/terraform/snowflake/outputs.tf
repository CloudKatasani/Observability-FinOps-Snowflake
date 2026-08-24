output "reader_role" {
  description = "Account role to put in SNOWFLAKE__ROLE."
  value       = snowflake_account_role.reader.name
}

output "warehouse" {
  description = "Warehouse to put in SNOWFLAKE__WAREHOUSE, or null when not created here."
  value       = var.create_warehouse ? snowflake_warehouse.app[0].name : null
}

output "granted_database_roles" {
  description = "Database roles granted, and the ACCOUNT_USAGE objects each one unlocks."
  value = {
    for name, detail in local.grantable_roles : name => detail.objects
  }
}

output "skipped_database_roles" {
  description = <<-EOT
    Organization-scoped roles not granted in this apply. The KPIs that depend on
    them render as "Unavailable — requires <object>" with the remediation grant,
    rather than as zero (R3).
  EOT
  value       = local.skipped_roles
}

output "grant_provenance" {
  description = "Where the grant list came from."
  value       = var.generated_by
}
