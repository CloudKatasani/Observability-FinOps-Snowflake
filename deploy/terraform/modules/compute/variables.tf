variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "image" {
  description = <<-EOT
    Fully qualified image reference for the all-in-one image, e.g.
    <account>.dkr.ecr.<region>.amazonaws.com/snowobs:1.4.0. Pin a tag or a
    digest — `latest` makes a rollback impossible to describe.
  EOT
  type        = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "app_security_group_id" {
  type = string
}

variable "target_group_arn" {
  type = string
}

variable "alb_target_group_label" {
  description = <<-EOT
    "<alb-suffix>/<target-group-suffix>", from the edge module. Enables
    request-count-per-target scaling. Null disables that policy.
  EOT
  type        = string
  default     = null
}

variable "execution_role_arn" {
  type = string
}

variable "task_role_arn" {
  type = string
}

variable "log_group_name" {
  type = string
}

variable "secret_arns" {
  description = "Secret ARNs from the security module, keyed by purpose. Values, never."
  type        = map(string)
}

variable "secret_names" {
  description = "Secret names from the security module — used for reference-style settings."
  type        = map(string)
}

# ── sizing ──────────────────────────────────────────────────────────────────
variable "cpu_architecture" {
  description = "ARM64 is roughly 20% cheaper per vCPU-hour on Fargate and the image builds for it."
  type        = string
  default     = "ARM64"

  validation {
    condition     = contains(["ARM64", "X86_64"], var.cpu_architecture)
    error_message = "cpu_architecture must be ARM64 or X86_64."
  }
}

variable "app_cpu" {
  description = "Fargate CPU units: 512 = 0.5 vCPU."
  type        = number
  default     = 512
}

variable "app_memory" {
  type    = number
  default = 1024
}

variable "app_port" {
  type    = number
  default = 8080
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

variable "app_target_cpu_percent" {
  type    = number
  default = 60
}

variable "app_target_requests_per_task" {
  type    = number
  default = 400
}

variable "health_check_grace_period_seconds" {
  type    = number
  default = 90
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

variable "worker_target_cpu_percent" {
  type    = number
  default = 60
}

variable "enable_queue_depth_scaling" {
  description = <<-EOT
    Scale the worker on queue depth instead of CPU. Requires the application to
    publish a `queue_depth` metric into var.metrics_namespace; without it the
    policy sits in INSUFFICIENT_DATA and never scales. Leave false until that
    metric exists.
  EOT
  type        = bool
  default     = false
}

variable "worker_target_queue_depth" {
  description = "Queued jobs per worker task to hold when queue-depth scaling is enabled."
  type        = number
  default     = 20
}

variable "metrics_namespace" {
  type    = string
  default = "snowobs"
}

variable "container_insights" {
  type    = bool
  default = true
}

variable "enable_ecs_exec" {
  type    = bool
  default = false
}

# ── application configuration (§21) ─────────────────────────────────────────
variable "app_mode" {
  description = "SNOWOBS_MODE. AWS deployments run `live`; `offline` is the laptop/assessment shape."
  type        = string
  default     = "live"

  validation {
    condition     = contains(["live", "offline", "auto"], var.app_mode)
    error_message = "app_mode must be live, offline, or auto."
  }
}

variable "tenancy" {
  type    = string
  default = "single"

  validation {
    condition     = contains(["single", "multi"], var.tenancy)
    error_message = "tenancy must be single or multi."
  }
}

variable "redis_url" {
  type = string
}

variable "data_lake_bucket" {
  type = string
}

variable "auth_provider" {
  type    = string
  default = "oidc"

  validation {
    condition     = contains(["oidc", "local"], var.auth_provider)
    error_message = "auth_provider must be oidc or local."
  }
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

  validation {
    condition     = contains(["anthropic", "bedrock", "cortex", "none"], var.llm_provider)
    error_message = "llm_provider must be anthropic, bedrock, cortex, or none."
  }
}

variable "llm_model_strong" {
  type    = string
  default = null
}

variable "llm_model_fast" {
  type    = string
  default = null
}

variable "finops_mode" {
  type    = string
  default = "showback"

  validation {
    condition     = contains(["showback", "chargeback"], var.finops_mode)
    error_message = "finops_mode must be showback or chargeback."
  }
}

variable "reconcile_tolerance_pct" {
  description = "R6. Raising this above 0.5 means publishing figures that do not reconcile."
  type        = number
  default     = 0.5
}

variable "guardrails_max_rows" {
  type    = number
  default = 50000
}

variable "allow_adhoc_sql" {
  description = "R9. Ad-hoc SQL still passes the guard; this decides whether the surface exists at all."
  type        = bool
  default     = false
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

variable "snowflake_query_tag_prefix" {
  type    = string
  default = "SNOWOBS"
}

variable "snowflake_statement_timeout_s" {
  type    = number
  default = 300
}

variable "extra_environment" {
  description = "Escape hatch for settings added after this module was written. Never secrets."
  type        = map(string)
  default     = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
