-- V_ACCESS_GOVERNANCE_LOGIN · Security & Access Governance 1.0.0
-- Generated from the governed metrics sec.failed_logins, sec.failed_login_rate, sec.single_factor_logins, sec.distinct_client_ips.
-- Edit the metric YAML, not this file: it is regenerated on every publish.
{{ config(materialized='table', on_schema_change='append_new_columns') }}

WITH product AS (
  SELECT
    (
      DATE_TRUNC('DAY', "EVENT_AT")
    ) AS "TIME_BUCKET",
    USER_NAME AS "USER",
    COALESCE(CLIENT_TYPE, 'UNKNOWN') AS "CLIENT_TYPE",
    COALESCE(FIRST_AUTH_FACTOR, 'UNKNOWN') AS "FIRST_FACTOR",
    COALESCE(ERROR_CODE, 'NONE') AS "ERROR_CLASS",
    COALESCE(CLIENT_IP, 'UNKNOWN') AS "CLIENT_IP",
    SUM(CASE WHEN IS_SUCCESS = 'NO' THEN 1 ELSE 0 END) AS "SEC_FAILED_LOGINS",
    (
      CAST(CASE
        WHEN (
          COUNT(*)
        ) = 0 OR (
          COUNT(*)
        ) IS NULL
        THEN NULL
        ELSE CAST((
          SUM(CASE WHEN IS_SUCCESS = 'NO' THEN 1 ELSE 0 END)
        ) AS DECIMAL(38, 15)) / CAST((
          COUNT(*)
        ) AS DECIMAL(38, 15))
      END AS DECIMAL(38, 15))
    ) AS "SEC_FAILED_LOGIN_RATE",
    SUM(CASE WHEN IS_SUCCESS = 'YES' AND SECOND_AUTH_FACTOR IS NULL THEN 1 ELSE 0 END) AS "SEC_SINGLE_FACTOR_LOGINS",
    COUNT(DISTINCT CLIENT_IP) AS "SEC_DISTINCT_CLIENT_IPS"
  FROM (
    SELECT
      "EVENT_ID" AS EVENT_ID,
      (
        TRY_TO_TIMESTAMP_NTZ("EVENT_TIMESTAMP")
      ) AS EVENT_AT,
      "EVENT_TYPE" AS EVENT_TYPE,
      "USER_NAME" AS USER_NAME,
      "CLIENT_IP" AS CLIENT_IP,
      "REPORTED_CLIENT_TYPE" AS CLIENT_TYPE,
      "REPORTED_CLIENT_VERSION" AS CLIENT_VERSION,
      "FIRST_AUTHENTICATION_FACTOR" AS FIRST_AUTH_FACTOR,
      "SECOND_AUTHENTICATION_FACTOR" AS SECOND_AUTH_FACTOR,
      "IS_SUCCESS" AS IS_SUCCESS,
      "ERROR_CODE" AS ERROR_CODE,
      "ERROR_MESSAGE" AS ERROR_MESSAGE
    FROM {{ source('account_usage', 'LOGIN_HISTORY') }}
  ) AS base
  GROUP BY
    (
      DATE_TRUNC('DAY', "EVENT_AT")
    ),
    USER_NAME,
    COALESCE(CLIENT_TYPE, 'UNKNOWN'),
    COALESCE(FIRST_AUTH_FACTOR, 'UNKNOWN'),
    COALESCE(ERROR_CODE, 'NONE'),
    COALESCE(CLIENT_IP, 'UNKNOWN')
)
SELECT
  product.*,
  CURRENT_TIMESTAMP() AS _LOADED_AT,
  'V_ACCESS_GOVERNANCE_LOGIN' AS _SOURCE_VIEW,
  '{{ invocation_id }}' AS _BATCH_ID
FROM product
