# Snowflake side of the deployment: the read-only role the platform connects
# with, built from granular database roles (R4), plus a small resource-monitored
# warehouse so the platform's own cost is bounded and visible.
#
# What this module deliberately does NOT do:
#   • create the service user or manage its key pair — key material must never
#     be a Terraform-managed attribute (§27.13);
#   • grant IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE, or any ACCOUNTADMIN /
#     SECURITYADMIN role — the variable validation rejects them (§27.3);
#   • create the publisher (write) role — that is a separate, separately
#     authorised artefact (snowflake/provisioning/02_publisher_role.sql, R8);
#   • hard-suspend anything — the resource monitor notifies only (§27.8).
#
# The grant list is generated, never hand-written: `make provisioning` derives
# it from the same source registry the application reads, so this module asks
# for exactly the privileges the code uses and no more.
#
# Apply as a role that can create account roles and warehouses and can grant
# SNOWFLAKE database roles (in practice ACCOUNTADMIN, or USERADMIN + SYSADMIN
# with the database-role grants delegated). Read the plan before applying: this
# is a change to a customer's account, and those are reviewed by a human (R8).

locals {
  # Organization-scoped roles are skipped unless the caller says the target is
  # an organization account, because granting them elsewhere is a hard failure
  # rather than a degraded KPI.
  grantable_roles = {
    for name, detail in var.database_roles : name => detail
    if var.grant_organization_roles || !detail.organization_scoped
  }

  skipped_roles = [
    for name, detail in var.database_roles : name
    if detail.organization_scoped && !var.grant_organization_roles
  ]

  # "SNOWFLAKE.USAGE_VIEWER" → "\"SNOWFLAKE\".\"USAGE_VIEWER\"", the fully
  # qualified, quoted form the provider expects for a database role.
  qualified_database_role = {
    for name, _ in local.grantable_roles :
    name => format("\"%s\".\"%s\"", split(".", name)[0], split(".", name)[1])
  }
}

resource "snowflake_account_role" "reader" {
  name    = var.reader_role_name
  comment = "Read-only role for the Observability & FinOps Platform. Grants are generated from the application's source registry."
}

# One grant per granular database role. Each carries the sources it unlocks in
# its own state, so `terraform plan` reads as a privilege review rather than an
# opaque list of role names.
resource "snowflake_grant_database_role" "reader" {
  for_each = local.grantable_roles

  database_role_name = local.qualified_database_role[each.key]
  parent_role_name   = snowflake_account_role.reader.name
}

resource "snowflake_resource_monitor" "app" {
  count = var.create_warehouse ? 1 : 0

  name            = var.resource_monitor_name
  credit_quota    = var.monthly_credit_quota
  frequency       = "MONTHLY"
  start_timestamp = "IMMEDIATELY"

  # Notify only. No suspend_trigger, and no suspend_immediate_trigger: a
  # resource monitor must never be able to stop a warehouse in a customer's
  # account (§14, §27.8).
  notify_triggers = [80, 100]
}

resource "snowflake_warehouse" "app" {
  count = var.create_warehouse ? 1 : 0

  name                = var.warehouse_name
  warehouse_size      = upper(var.warehouse_size)
  auto_suspend        = var.warehouse_auto_suspend_seconds
  auto_resume         = "true"
  initially_suspended = true
  resource_monitor    = snowflake_resource_monitor.app[0].name
  comment             = "Observability & FinOps Platform application warehouse. Its consumption is reported as cost.platform_self_cost."
}

resource "snowflake_grant_privileges_to_account_role" "warehouse_usage" {
  count = var.create_warehouse ? 1 : 0

  account_role_name = snowflake_account_role.reader.name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.app[0].name
  }
}

# The service user is created out of band (it owns key material); this only
# attaches the read-only role to it.
resource "snowflake_grant_account_role" "service_user" {
  count = var.service_user_name == null ? 0 : 1

  role_name = snowflake_account_role.reader.name
  user_name = var.service_user_name
}
