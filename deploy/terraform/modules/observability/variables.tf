variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

variable "log_retention_days" {
  description = <<-EOT
    Application logs. These carry trace ids, actor ids, and the compiled SQL of
    every guarded statement — they are an audit trail, so retention is a
    compliance decision, not a cost one.
  EOT
  type        = number
  default     = 90
}

variable "alb_arn_suffix" {
  type = string
}

variable "target_group_arn_suffix" {
  type = string
}

variable "cluster_name" {
  type = string
}

variable "app_service_name" {
  type = string
}

variable "worker_service_name" {
  type = string
}

variable "database_identifier" {
  type = string
}

variable "alarm_topic_arn" {
  description = "SNS topic for alarm and OK notifications. Null creates alarms that page nobody."
  type        = string
  default     = null
}

variable "error_count_threshold" {
  description = "5xx responses in a 5-minute window before alarming."
  type        = number
  default     = 10
}

variable "latency_p95_threshold_seconds" {
  description = "§22.3: a warm dashboard tile is 300 ms p95; this allows for cold tiles and agent turns."
  type        = number
  default     = 3
}

variable "database_free_storage_threshold_bytes" {
  description = "Default 4 GiB, which is 20% of the Small profile's 20 GB volume."
  type        = number
  default     = 4294967296
}

variable "tags" {
  type    = map(string)
  default = {}
}
