variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "data_lake_bucket_arn" {
  description = "Bucket the application may read and write. The only bucket it may touch."
  type        = string
}

variable "metrics_namespace" {
  description = "CloudWatch namespace the task may publish to, and only that one."
  type        = string
  default     = "snowobs"
}

variable "llm_provider" {
  description = "anthropic | bedrock | cortex | none. Drives which secret and which IAM grant exist."
  type        = string
  default     = "none"

  validation {
    condition     = contains(["anthropic", "bedrock", "cortex", "none"], var.llm_provider)
    error_message = "llm_provider must match LLM__PROVIDER: anthropic, bedrock, cortex, or none."
  }
}

variable "bedrock_model_arns" {
  description = <<-EOT
    Exact model ARNs the task may invoke. Left as a required list rather than
    given a wildcard default: "bedrock:InvokeModel on *" is a standing licence
    to spend money on any model in the account.
  EOT
  type        = list(string)
  default     = []
}

variable "webhook_secret_enabled" {
  description = "Create a secret for the alert webhook URL (§14 channels)."
  type        = bool
  default     = false
}

variable "enable_ecs_exec" {
  description = "Allow `aws ecs execute-command` into running tasks. Off in production by default."
  type        = bool
  default     = false
}

variable "kms_deletion_window_days" {
  type    = number
  default = 30
}

variable "secret_recovery_window_days" {
  description = "0 deletes immediately; use 7+ in production so a fat-fingered destroy is recoverable."
  type        = number
  default     = 7
}

variable "tags" {
  type    = map(string)
  default = {}
}
