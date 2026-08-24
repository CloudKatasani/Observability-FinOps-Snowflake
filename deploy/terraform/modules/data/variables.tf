variable "name" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "database_security_group_id" {
  type = string
}

variable "cache_security_group_id" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

# ── Postgres ────────────────────────────────────────────────────────────────
variable "postgres_version" {
  description = "Major version is what matters; RDS applies minors automatically."
  type        = string
  default     = "16"
}

variable "db_instance_class" {
  description = "See docs/AWS_COST.md: t4g.small (Small), t4g.medium (Standard), m6g.large (Large)."
  type        = string
  default     = "db.t4g.small"
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 20
}

variable "db_max_allocated_storage_gb" {
  description = "Storage autoscaling ceiling. Set equal to allocated to disable."
  type        = number
  default     = 100
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "backup_retention_days" {
  description = "RPO for app metadata. 7 days meets the RUNBOOK's stated 24 h RPO with room."
  type        = number
  default     = 7

  validation {
    condition     = var.backup_retention_days >= 1
    error_message = "Automated backups must be on: the audit log is not re-derivable (R2)."
  }
}

variable "backup_window" {
  type    = string
  default = "02:00-03:00"
}

variable "maintenance_window" {
  type    = string
  default = "sun:03:30-sun:04:30"
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "skip_final_snapshot" {
  description = "True only for throwaway environments."
  type        = bool
  default     = false
}

variable "performance_insights_enabled" {
  type    = bool
  default = false
}

variable "apply_immediately" {
  description = "Apply changes outside the maintenance window. True in dev, false in prod."
  type        = bool
  default     = false
}

# ── Redis ───────────────────────────────────────────────────────────────────
variable "redis_version" {
  type    = string
  default = "7.1"
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "redis_replica_count" {
  description = "0 = single node (dev). 1+ enables automatic failover and Multi-AZ."
  type        = number
  default     = 0
}

variable "redis_snapshot_retention_days" {
  description = "The queue is not a system of record; snapshots are a convenience, not an RPO."
  type        = number
  default     = 1
}

# ── S3 ──────────────────────────────────────────────────────────────────────
variable "data_lake_bucket_name" {
  description = "Globally unique bucket name. Include the account or environment."
  type        = string
}

variable "upload_retention_days" {
  description = "TTL on raw uploaded extracts under uploads/ (§17)."
  type        = number
  default     = 30
}

variable "tags" {
  type    = map(string)
  default = {}
}
