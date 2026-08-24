# Every variable is documented, and every default is the safe choice rather
# than the convenient one: private load balancer, no ad-hoc SQL, showback
# rather than chargeback, deletion protection on, no LLM provider.

variable "name" {
  description = "Deployment name; prefixes every resource. e.g. snowobs-prod."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,24}$", var.name))
    error_message = "name must be 3–25 lowercase alphanumerics or hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "dev | staging | prod. Tag value only; behaviour comes from the explicit variables."
  type        = string
}

variable "region" {
  type = string
}

variable "aws_partition" {
  description = "aws | aws-us-gov | aws-cn. Only change this in a non-commercial partition."
  type        = string
  default     = "aws"
}

variable "image" {
  description = "All-in-one image reference, tagged or digest-pinned. Never `latest`."
  type        = string

  validation {
    condition     = !endswith(var.image, ":latest")
    error_message = "Pin a version or a digest: `latest` makes a rollback impossible to describe."
  }
}

# ── network ─────────────────────────────────────────────────────────────────
variable "cidr_block" {
  type    = string
  default = "10.60.0.0/16"
}

variable "availability_zone_count" {
  type    = number
  default = 2
}

variable "enable_nat_gateway" {
  type    = bool
  default = true
}

variable "one_nat_gateway_per_az" {
  type    = bool
  default = false
}

variable "ingress_cidrs" {
  description = "Networks allowed to reach the load balancer. RFC1918 by default."
  type        = list(string)
  default     = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
}

variable "enable_flow_logs" {
  type    = bool
  default = true
}

variable "app_port" {
  type    = number
  default = 8080
}

# ── edge ────────────────────────────────────────────────────────────────────
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

variable "internal_load_balancer" {
  type    = bool
  default = true
}

variable "alb_idle_timeout_seconds" {
  type    = number
  default = 120
}

variable "alb_access_logs_bucket" {
  type    = string
  default = null
}

variable "enable_waf" {
  type    = bool
  default = false
}

# ── data ────────────────────────────────────────────────────────────────────
variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 20
}

variable "db_max_allocated_storage_gb" {
  type    = number
  default = 100
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "backup_retention_days" {
  type    = number
  default = 7
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "redis_replica_count" {
  type    = number
  default = 0
}

variable "data_lake_bucket_name" {
  description = "Globally unique S3 bucket name for uploads, landed Parquet, and exports."
  type        = string
}

variable "upload_retention_days" {
  type    = number
  default = 30
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "skip_final_snapshot" {
  type    = bool
  default = false
}

variable "apply_immediately" {
  type    = bool
  default = false
}

# ── compute ─────────────────────────────────────────────────────────────────
variable "cpu_architecture" {
  type    = string
  default = "ARM64"
}

variable "app_cpu" {
  type    = number
  default = 512
}

variable "app_memory" {
  type    = number
  default = 1024
}

variable "app_desired_count" {
  type    = number
  default = 1
}

variable "app_min_count" {
  type    = number
  default = 1
}

variable "app_max_count" {
  type    = number
  default = 4
}

variable "worker_cpu" {
  type    = number
  default = 512
}

variable "worker_memory" {
  type    = number
  default = 1024
}

variable "worker_desired_count" {
  type    = number
  default = 1
}

variable "worker_min_count" {
  type    = number
  default = 1
}

variable "worker_max_count" {
  type    = number
  default = 4
}

variable "container_insights" {
  type    = bool
  default = true
}

variable "enable_ecs_exec" {
  type    = bool
  default = false
}

variable "metrics_namespace" {
  type    = string
  default = "snowobs"
}

# ── application configuration (§21) ─────────────────────────────────────────
variable "app_mode" {
  type    = string
  default = "live"
}

variable "tenancy" {
  type    = string
  default = "single"
}

variable "auth_provider" {
  type    = string
  default = "oidc"
}

variable "auth_issuer" {
  type    = string
  default = null
}

variable "auth_client_id" {
  type    = string
  default = null
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
  type    = list(string)
  default = []
}

variable "finops_mode" {
  type    = string
  default = "showback"
}

variable "reconcile_tolerance_pct" {
  type    = number
  default = 0.5
}

variable "guardrails_max_rows" {
  type    = number
  default = 50000
}

variable "allow_adhoc_sql" {
  type    = bool
  default = false
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
  default = null
}

variable "snowflake_warehouse" {
  type    = string
  default = null
}

variable "snowflake_statement_timeout_s" {
  type    = number
  default = 300
}

variable "webhook_secret_enabled" {
  type    = bool
  default = false
}

variable "secret_recovery_window_days" {
  type    = number
  default = 7
}

variable "extra_environment" {
  type    = map(string)
  default = {}
}

# ── observability ───────────────────────────────────────────────────────────
variable "log_retention_days" {
  type    = number
  default = 90
}

variable "alarm_topic_arn" {
  type    = string
  default = null
}

variable "latency_p95_threshold_seconds" {
  type    = number
  default = 3
}

# ── CI ──────────────────────────────────────────────────────────────────────
variable "create_ci_resources" {
  description = "ECR and the GitHub OIDC deploy role. Create these once, usually in dev."
  type        = bool
  default     = false
}

variable "ecr_repository_names" {
  type    = list(string)
  default = ["snowobs"]
}

variable "github_repository" {
  type    = string
  default = "example/snowobs"
}

variable "allowed_git_refs" {
  type    = list(string)
  default = ["refs/heads/main", "refs/tags/v*"]
}

variable "create_oidc_provider" {
  type    = bool
  default = true
}

variable "existing_oidc_provider_arn" {
  type    = string
  default = null
}

variable "tags" {
  type    = map(string)
  default = {}
}
