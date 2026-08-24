variable "name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "security_group_id" {
  type = string
}

variable "app_port" {
  type    = number
  default = 8080
}

variable "internal" {
  description = <<-EOT
    Private load balancer (default). Set false only after deciding, on purpose,
    that a console showing the customer's whole cost structure should be
    reachable from the internet — and then turn the WAF on.
  EOT
  type        = bool
  default     = true
}

variable "domain_name" {
  description = "FQDN the platform is served on, e.g. snowobs.internal.example.com."
  type        = string
}

variable "hosted_zone_id" {
  description = "Route 53 zone for the record and for DNS certificate validation. Null to manage DNS elsewhere."
  type        = string
  default     = null
}

variable "certificate_arn" {
  description = "Existing ACM certificate. Null issues one, which requires hosted_zone_id."
  type        = string
  default     = null

  validation {
    condition     = var.certificate_arn == null || can(regex("^arn:aws[a-z-]*:acm:", var.certificate_arn))
    error_message = "certificate_arn must be an ACM certificate ARN."
  }
}

variable "ssl_policy" {
  description = "TLS 1.2 minimum. TLS13 policies are preferred where the client population allows."
  type        = string
  default     = "ELBSecurityPolicy-TLS13-1-2-2021-06"
}

variable "idle_timeout_seconds" {
  description = "Must exceed the agent's p95 grounded answer plus SSE stream time (§22.3)."
  type        = number
  default     = 120
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "access_logs_bucket" {
  description = "Existing bucket for ALB access logs, with a log-delivery bucket policy already on it."
  type        = string
  default     = null
}

variable "enable_waf" {
  type    = bool
  default = false
}

variable "waf_rate_limit" {
  description = "Requests per 5 minutes per IP before blocking."
  type        = number
  default     = 2000
}

variable "tags" {
  type    = map(string)
  default = {}
}
