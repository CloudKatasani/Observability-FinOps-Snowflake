output "database_endpoint" {
  value = aws_db_instance.this.address
}

output "database_port" {
  value = aws_db_instance.this.port
}

output "database_name" {
  value = aws_db_instance.this.db_name
}

output "database_username" {
  value = aws_db_instance.this.username
}

output "database_master_secret_arn" {
  description = <<-EOT
    ARN of the RDS-managed master password secret. Terraform never sees the
    value; the task definition injects it by ARN and RDS rotates it.
  EOT
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "database_identifier" {
  description = "For the RUNBOOK's snapshot and restore procedures."
  value       = aws_db_instance.this.identifier
}

output "redis_primary_endpoint" {
  value = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "redis_url" {
  description = "REDIS_URL for the task definition. rediss:// — in-transit encryption is on."
  value       = "rediss://${aws_elasticache_replication_group.this.primary_endpoint_address}:6379/0"
}

output "data_lake_bucket" {
  value = aws_s3_bucket.lake.bucket
}

output "data_lake_bucket_arn" {
  value = aws_s3_bucket.lake.arn
}
