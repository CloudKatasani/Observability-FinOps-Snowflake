variable "name" {
  description = "Resource name prefix, e.g. snowobs-prod."
  type        = string
}

variable "region" {
  description = "AWS region; used to build VPC endpoint service names."
  type        = string
}

variable "cidr_block" {
  description = "VPC CIDR. A /16 leaves room for the Large sizing profile."
  type        = string
  default     = "10.60.0.0/16"

  validation {
    condition     = can(cidrsubnet(var.cidr_block, 4, 15))
    error_message = "cidr_block must be a valid CIDR no smaller than /20."
  }
}

variable "availability_zone_count" {
  description = "AZs to spread across. Two is the minimum for Multi-AZ RDS and an ALB."
  type        = number
  default     = 2

  validation {
    condition     = var.availability_zone_count >= 2 && var.availability_zone_count <= 4
    error_message = "Use between 2 and 4 availability zones."
  }
}

variable "enable_nat_gateway" {
  description = <<-EOT
    Give private subnets a route to the internet. Required to reach Snowflake
    over the public endpoint or to call the Anthropic API. Set false for a
    zero-egress deployment (Snowflake PrivateLink + Bedrock via its VPC
    endpoint); the interface endpoints then carry everything.
  EOT
  type        = bool
  default     = true
}

variable "one_nat_gateway_per_az" {
  description = "Highly available NAT (one per AZ). Costs roughly one NAT gateway per AZ."
  type        = bool
  default     = false
}

variable "enable_bedrock_endpoint" {
  description = "Create the bedrock-runtime interface endpoint. Only when LLM__PROVIDER=bedrock."
  type        = bool
  default     = false
}

variable "ingress_cidrs" {
  description = <<-EOT
    Networks allowed to reach the load balancer. Default is deliberately RFC1918
    only: a deployment holding a credential to the customer's warehouse should
    not be reachable from the internet until someone decides it should be.
  EOT
  type        = list(string)
  default     = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
}

variable "app_port" {
  description = "Container port the API listens on."
  type        = number
  default     = 8080
}

variable "enable_flow_logs" {
  description = "Log rejected flows to CloudWatch. Cheap, and the first thing asked for after an incident."
  type        = bool
  default     = true
}

variable "flow_log_retention_days" {
  type    = number
  default = 30
}

variable "kms_key_arn" {
  description = "CMK for log-group encryption."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
