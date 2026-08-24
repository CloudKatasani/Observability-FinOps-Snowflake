variable "generated_by" {
  description = <<-EOT
    Provenance string written by scripts/gen_snowflake_grants.py. Surfaced as an
    output so an auditor reading `terraform show` can see where the grant list
    came from without opening the repository.
  EOT
  type        = string
}

variable "reader_role_name" {
  description = "Account role the platform connects with. Read-only by construction (R4)."
  type        = string
  default     = "SNOWOBS_READER"
}

variable "database_roles" {
  description = <<-EOT
    Granular SNOWFLAKE database roles the platform needs, keyed by role name.
    GENERATED — do not edit. Written to grants.auto.tfvars.json by
    `make provisioning` from the source registry, so a newly registered
    ACCOUNT_USAGE view brings its own grant with it and this module can never
    ask for more privilege than the application actually reads.

    `organization_scoped` marks roles that only exist in an organization
    account; applying them against a member account fails, so they are gated by
    var.grant_organization_roles.
  EOT
  type = map(object({
    sources             = list(string)
    objects             = list(string)
    organization_scoped = bool
  }))

  validation {
    # §27.3 / R4. The generator audits its SQL output; this is the same rule
    # enforced on the Terraform input, so a hand-edited tfvars file cannot
    # smuggle a blanket grant past review.
    condition = alltrue([
      for name in keys(var.database_roles) :
      !can(regex("(?i)imported privileges|accountadmin|securityadmin", name))
    ])
    error_message = "Blanket grants are forbidden (R4/§27.3): only granular SNOWFLAKE database roles."
  }
}

variable "grant_organization_roles" {
  description = <<-EOT
    Grant the ORGANIZATION_* database roles. Only set this true when applying
    against an organization account (or a member account with ORGADMIN
    enabled); elsewhere the grant does not exist and apply fails. When false,
    the affected KPIs degrade with a remediation hint rather than breaking (R3).
  EOT
  type        = bool
  default     = false
}

variable "service_user_name" {
  description = <<-EOT
    Existing Snowflake user the platform authenticates as. The user itself is
    NOT managed here: creating a service user means handling its key material,
    and key material must never be a Terraform-managed attribute (§27.13).
    Create the user and register its public key out of band, then name it here.
    Leave null to skip the grant and wire the user up by hand.
  EOT
  type        = string
  default     = null
}

variable "create_warehouse" {
  description = "Create the small, resource-monitored warehouse the platform queries with."
  type        = bool
  default     = true
}

variable "warehouse_name" {
  type    = string
  default = "WH_SNOWOBS_APP"
}

variable "warehouse_size" {
  description = "XSMALL is sufficient: the platform aggregates usage views, it does not scan data."
  type        = string
  default     = "XSMALL"

  validation {
    condition     = contains(["XSMALL", "SMALL", "MEDIUM"], upper(var.warehouse_size))
    error_message = "The platform's own warehouse is not a data warehouse; keep it XSMALL–MEDIUM."
  }
}

variable "warehouse_auto_suspend_seconds" {
  type    = number
  default = 60
}

variable "monthly_credit_quota" {
  description = "Resource-monitor quota for the platform's own consumption, in credits."
  type        = number
  default     = 50
}

variable "resource_monitor_name" {
  type    = string
  default = "RM_SNOWOBS_APP"
}
