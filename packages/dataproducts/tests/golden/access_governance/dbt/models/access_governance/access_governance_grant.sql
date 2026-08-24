-- V_ACCESS_GOVERNANCE_GRANT · Security & Access Governance 1.0.0
-- Generated from the governed metrics sec.privileged_grants, sec.new_grants, sec.disabled_but_granted_users.
-- Edit the metric YAML, not this file: it is regenerated on every publish.
{{ config(materialized='table', on_schema_change='append_new_columns') }}

WITH product AS (
  SELECT
    (
      DATE_TRUNC('DAY', "GRANTED_AT")
    ) AS "TIME_BUCKET",
    ROLE_NAME AS "ROLE",
    GRANTEE_NAME AS "GRANTEE",
    COALESCE(GRANTED_BY, 'UNKNOWN') AS "GRANTED_BY",
    COALESCE(GRANTEE_TYPE, 'UNKNOWN') AS "GRANTEE_TYPE",
    CASE
      WHEN ROLE_NAME IN ('ACCOUNTADMIN', 'SECURITYADMIN', 'ORGADMIN')
      THEN 'PRIVILEGED'
      ELSE 'STANDARD'
    END AS "PRIVILEGE_TIER",
    SUM(
      CASE
        WHEN ROLE_NAME IN ('ACCOUNTADMIN', 'SECURITYADMIN', 'ORGADMIN') AND REVOKED_AT IS NULL
        THEN 1
        ELSE 0
      END
    ) AS "SEC_PRIVILEGED_GRANTS",
    COUNT(*) AS "SEC_NEW_GRANTS",
    COUNT(DISTINCT CASE WHEN GRANTEE_DISABLED AND REVOKED_AT IS NULL THEN GRANTEE_NAME END) AS "SEC_DISABLED_BUT_GRANTED_USERS"
  FROM (
    SELECT
      g."ROLE" AS ROLE_NAME,
      g."GRANTED_TO" AS GRANTED_TO,
      g."GRANTEE_NAME" AS GRANTEE_NAME,
      g."GRANTED_BY" AS GRANTED_BY,
      (
        TRY_TO_TIMESTAMP_NTZ(g."CREATED_ON")
      ) AS GRANTED_AT,
      (
        TRY_TO_TIMESTAMP_NTZ(g."DELETED_ON")
      ) AS REVOKED_AT,
      COALESCE(u."DISABLED", FALSE) AS GRANTEE_DISABLED,
      u."TYPE" AS GRANTEE_TYPE,
      (
        TRY_TO_TIMESTAMP_NTZ(u."LAST_SUCCESS_LOGIN")
      ) AS GRANTEE_LAST_LOGIN_AT,
      (
        NULL
      ) AS ACCOUNT_NAME /* The account these rows came from (see the ACCOUNT_OF shim). */
    FROM {{ source('account_usage', 'GRANTS_TO_USERS') }} AS g
    LEFT JOIN {{ source('account_usage', 'USERS') }} AS u
      ON u."NAME" = g."GRANTEE_NAME"
      AND COALESCE((
        NULL
      ), '') = COALESCE((
        NULL
      ), '')
  ) AS base
  GROUP BY
    (
      DATE_TRUNC('DAY', "GRANTED_AT")
    ),
    ROLE_NAME,
    GRANTEE_NAME,
    COALESCE(GRANTED_BY, 'UNKNOWN'),
    COALESCE(GRANTEE_TYPE, 'UNKNOWN'),
    CASE
      WHEN ROLE_NAME IN ('ACCOUNTADMIN', 'SECURITYADMIN', 'ORGADMIN')
      THEN 'PRIVILEGED'
      ELSE 'STANDARD'
    END
)
SELECT
  product.*,
  CURRENT_TIMESTAMP() AS _LOADED_AT,
  'V_ACCESS_GOVERNANCE_GRANT' AS _SOURCE_VIEW,
  '{{ invocation_id }}' AS _BATCH_ID
FROM product
