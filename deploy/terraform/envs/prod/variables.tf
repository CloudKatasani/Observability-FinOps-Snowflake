variable "name" {
  type    = string
  default = "snowobs-prod"
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "image" {
  description = "All-in-one image, digest-pinned for production."
  type        = string
}

variable "domain_name" {
  type = string
}

variable "hosted_zone_id" {
  type    = string
  default = null
}

variable "certificate_arn" {
  type    = string
  default = null
}

variable "ingress_cidrs" {
  type    = list(string)
  default = ["10.0.0.0/8"]
}

variable "internal_load_balancer" {
  description = "Keep true unless someone has decided, on purpose, to expose this to the internet."
  type        = bool
  default     = true
}

variable "alb_access_logs_bucket" {
  type    = string
  default = null
}

variable "enable_nat_gateway" {
  description = "False for a zero-egress deployment: Snowflake PrivateLink plus the VPC endpoints."
  type        = bool
  default     = true
}

variable "data_lake_bucket_name" {
  type = string
}

variable "upload_retention_days" {
  type    = number
  default = 30
}

# ── sizing: Standard by default; the Large profile's values are in AWS_COST.md
variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 50
}

variable "db_max_allocated_storage_gb" {
  type    = number
  default = 500
}

variable "backup_retention_days" {
  type    = number
  default = 14
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}

variable "app_cpu" {
  type    = number
  default = 1024
}

variable "app_memory" {
  type    = number
  default = 2048
}

variable "app_min_count" {
  type    = number
  default = 2
}

variable "app_max_count" {
  type    = number
  default = 8
}

variable "worker_cpu" {
  type    = number
  default = 1024
}

variable "worker_memory" {
  type    = number
  default = 2048
}

variable "worker_min_count" {
  type    = number
  default = 1
}

variable "worker_max_count" {
  type    = number
  default = 6
}

variable "log_retention_days" {
  description = "Logs carry the audit trail of every guarded statement; this is a compliance decision."
  type        = number
  default     = 365
}

variable "alarm_topic_arn" {
  description = "SNS topic the CloudWatch alarms notify. Without it the alarms page nobody."
  type        = string
  default     = null
}

# ── application configuration ───────────────────────────────────────────────
variable "tenancy" {
  type    = string
  default = "single"
}

variable "auth_issuer" {
  type = string
}

variable "auth_client_id" {
  type    = string
  default = null
}

variable "finops_mode" {
  description = "showback until the allocation has reconciled cleanly for a few closes (R6)."
  type        = string
  default     = "showback"
}

variable "llm_provider" {
  type    = string
  default = "none"
}

variable "llm_model_strong" {
  type    = string
  default = null
}

variable "llm_model_fast" {
  type    = string
  default = null
}

variable "bedrock_model_arns" {
  description = "Exact model ARNs the task may invoke when llm_provider = bedrock."
  type        = list(string)
  default     = []
}

variable "snowflake_account" {
  type    = string
  default = null
}

variable "snowflake_user" {
  type    = string
  default = null
}

variable "snowflake_role" {
  type    = string
  default = "SNOWOBS_READER"
}

variable "snowflake_warehouse" {
  type    = string
  default = "WH_SNOWOBS_APP"
}

variable "webhook_secret_enabled" {
  type    = bool
  default = false
}
