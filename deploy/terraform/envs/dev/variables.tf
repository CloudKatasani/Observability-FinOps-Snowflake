variable "name" {
  description = "Deployment name; prefixes every resource."
  type        = string
  default     = "snowobs-dev"
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "image" {
  description = "All-in-one image reference. The pipeline updates the running task; this is the bootstrap value."
  type        = string
}

variable "domain_name" {
  description = "FQDN the platform is served on."
  type        = string
}

variable "hosted_zone_id" {
  description = "Route 53 zone for DNS and certificate validation. Null to manage DNS yourself."
  type        = string
  default     = null
}

variable "certificate_arn" {
  description = "Existing ACM certificate. Null issues one (needs hosted_zone_id)."
  type        = string
  default     = null
}

variable "ingress_cidrs" {
  description = "Networks allowed to reach the load balancer."
  type        = list(string)
  default     = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
}

variable "data_lake_bucket_name" {
  description = "Globally unique S3 bucket name."
  type        = string
}

variable "app_mode" {
  description = "SNOWOBS_MODE. `live` once a Snowflake connection is configured."
  type        = string
  default     = "live"
}

variable "auth_provider" {
  description = "oidc in any shared environment; local is a laptop fallback only."
  type        = string
  default     = "oidc"
}

variable "auth_issuer" {
  type    = string
  default = null
}

variable "llm_provider" {
  description = "anthropic | bedrock | cortex | none. `none` keeps the deterministic agent path."
  type        = string
  default     = "none"
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
  description = "The read-only role from deploy/terraform/snowflake (default SNOWOBS_READER)."
  type        = string
  default     = null
}

variable "snowflake_warehouse" {
  type    = string
  default = null
}

variable "github_repository" {
  description = "owner/repo the deploy role trusts."
  type        = string
}

variable "allowed_git_refs" {
  type    = list(string)
  default = ["refs/heads/main", "refs/tags/v*"]
}
