terraform {
  required_version = ">= 1.6"

  required_providers {
    snowflake = {
      source = "snowflakedb/snowflake"
      # Pinned to the v2 line, in which account roles are `snowflake_account_role`
      # and database-role grants are `snowflake_grant_database_role`. Read the
      # provider's upgrade guide before widening this constraint: the resource
      # names in this file changed at the v0.9x → v1 boundary.
      version = "~> 2.0"
    }
  }
}

# Credentials come from the provider's own environment variables
# (SNOWFLAKE_ORGANIZATION_NAME, SNOWFLAKE_ACCOUNT_NAME, SNOWFLAKE_USER,
# SNOWFLAKE_PRIVATE_KEY, SNOWFLAKE_ROLE) or a ~/.snowflake/config profile.
# Nothing about the connection is declared here, so no credential can reach
# Terraform state or a variables file by accident (§27.13).
provider "snowflake" {}
