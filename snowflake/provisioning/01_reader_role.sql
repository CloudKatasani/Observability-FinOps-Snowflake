-- Observability & FinOps Platform for Snowflake — read-only provisioning
-- Generated 2026-08-24T03:08:54+00:00
--
-- Idempotent: safe to re-run. Read-only: this script grants SELECT on
-- usage views through Snowflake's granular database roles and creates
-- one small warehouse for the platform's own queries. It grants no
-- privileges on your data, and no blanket IMPORTED PRIVILEGES.
--
-- Run as a role that can create roles and warehouses (typically
-- USERADMIN + SYSADMIN, or ACCOUNTADMIN). The platform itself never
-- runs this: a human reviews and executes it.

USE ROLE USERADMIN;
CREATE ROLE IF NOT EXISTS SNOWOBS_READER
  COMMENT = 'Read-only role for the Observability & FinOps Platform';

USE ROLE ACCOUNTADMIN;  -- required to grant SNOWFLAKE database roles

-- SNOWFLAKE.GOVERNANCE_VIEWER: 6 source(s) (access_history, masking_policies, object_dependencies, policy_references, row_access_policies…)
GRANT DATABASE ROLE SNOWFLAKE.GOVERNANCE_VIEWER TO ROLE SNOWOBS_READER;

-- SNOWFLAKE.OBJECT_VIEWER: 5 source(s) (columns, databases, schemata, tables, views)
GRANT DATABASE ROLE SNOWFLAKE.OBJECT_VIEWER TO ROLE SNOWOBS_READER;

-- SNOWFLAKE.ORGANIZATION_BILLING_VIEWER: 4 source(s) (contract_items, rate_sheet_daily, remaining_balance_daily, usage_in_currency_daily)
GRANT DATABASE ROLE SNOWFLAKE.ORGANIZATION_BILLING_VIEWER TO ROLE SNOWOBS_READER;

-- SNOWFLAKE.ORGANIZATION_USAGE_VIEWER: 3 source(s) (data_transfer_daily_history, org_warehouse_metering_history, storage_daily_history)
GRANT DATABASE ROLE SNOWFLAKE.ORGANIZATION_USAGE_VIEWER TO ROLE SNOWOBS_READER;

-- SNOWFLAKE.SECURITY_VIEWER: 6 source(s) (grants_to_roles, grants_to_users, login_history, roles, sessions…)
GRANT DATABASE ROLE SNOWFLAKE.SECURITY_VIEWER TO ROLE SNOWOBS_READER;

-- SNOWFLAKE.USAGE_VIEWER: 30 source(s) (automatic_clustering_history, copy_history, cortex_analyst_usage_history, cortex_functions_query_usage_history, cortex_functions_usage_history…)
GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER TO ROLE SNOWOBS_READER;

-- A small, resource-monitored warehouse so the platform's own cost is
-- visible and bounded. Its consumption is reported as a first-class
-- KPI (cost.platform_self_cost).
USE ROLE SYSADMIN;
CREATE WAREHOUSE IF NOT EXISTS WH_SNOWOBS_APP
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'Observability & FinOps Platform application warehouse';

USE ROLE ACCOUNTADMIN;
CREATE RESOURCE MONITOR IF NOT EXISTS RM_SNOWOBS_APP
  WITH CREDIT_QUOTA = 50
  FREQUENCY = MONTHLY
  START_TIMESTAMP = IMMEDIATELY
  -- Notify only. This monitor guards the platform's own warehouse;
  -- production warehouses are never hard-suspended (§14, §27.8).
  TRIGGERS ON 80 PERCENT DO NOTIFY
           ON 100 PERCENT DO NOTIFY;
ALTER WAREHOUSE WH_SNOWOBS_APP SET RESOURCE_MONITOR = RM_SNOWOBS_APP;

USE ROLE SECURITYADMIN;
GRANT USAGE ON WAREHOUSE WH_SNOWOBS_APP TO ROLE SNOWOBS_READER;

-- Grant the reader role to the service user and to an operator role.
GRANT ROLE SNOWOBS_READER TO ROLE SYSADMIN;
-- GRANT ROLE SNOWOBS_READER TO USER <SNOWOBS_SERVICE_USER>;

-- Verify: this should list the granted database roles.
SHOW GRANTS TO ROLE SNOWOBS_READER;
