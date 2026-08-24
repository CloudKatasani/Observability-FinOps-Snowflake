-- V_ACCESS_GOVERNANCE_USER · Security & Access Governance 1.0.0
-- Generated from the governed metrics sec.dormant_users, sec.users_without_key_pair.
-- Edit the metric YAML, not this file: it is regenerated on every publish.
{{ config(materialized='table', on_schema_change='append_new_columns') }}

WITH product AS (
  SELECT
    USER_NAME AS "USER",
    COALESCE(USER_TYPE, 'UNKNOWN') AS "USER_TYPE",
    COALESCE(DEFAULT_ROLE, 'NONE') AS "DEFAULT_ROLE",
    CASE WHEN IS_DISABLED THEN 'DISABLED' ELSE 'ACTIVE' END AS "ACCOUNT_STATUS",
    CASE
      WHEN HAS_RSA_PUBLIC_KEY AND HAS_PASSWORD
      THEN 'KEY_AND_PASSWORD'
      WHEN HAS_RSA_PUBLIC_KEY
      THEN 'KEY_PAIR'
      WHEN HAS_PASSWORD
      THEN 'PASSWORD_ONLY'
      ELSE 'FEDERATED_OR_NONE'
    END AS "CREDENTIAL_TYPE",
    COUNT(
      DISTINCT CASE WHEN DAYS_SINCE_LAST_LOGIN >= 90 AND NOT IS_DISABLED THEN USER_NAME END
    ) AS "SEC_DORMANT_USERS",
    COUNT(DISTINCT CASE WHEN NOT HAS_RSA_PUBLIC_KEY AND NOT IS_DISABLED THEN USER_NAME END) AS "SEC_USERS_WITHOUT_KEY_PAIR"
  FROM (
    SELECT
      "USER_ID" AS USER_ID,
      "NAME" AS USER_NAME,
      "LOGIN_NAME" AS LOGIN_NAME,
      "DEFAULT_ROLE" AS DEFAULT_ROLE,
      "DEFAULT_WAREHOUSE" AS DEFAULT_WAREHOUSE,
      "TYPE" AS USER_TYPE,
      COALESCE("DISABLED", FALSE) AS IS_DISABLED,
      COALESCE("HAS_PASSWORD", FALSE) AS HAS_PASSWORD,
      COALESCE("HAS_RSA_PUBLIC_KEY", FALSE) AS HAS_RSA_PUBLIC_KEY,
      (
        TRY_TO_TIMESTAMP_NTZ("LAST_SUCCESS_LOGIN")
      ) AS LAST_SUCCESS_LOGIN_AT,
      (
        TRY_TO_TIMESTAMP_NTZ("CREATED_ON")
      ) AS CREATED_AT,
      (
        TRY_TO_TIMESTAMP_NTZ("DELETED_ON")
      ) AS DELETED_AT,
      MAX((
        TRY_TO_TIMESTAMP_NTZ("LAST_SUCCESS_LOGIN")
      )) OVER (PARTITION BY (
        NULL
      )) AS SNAPSHOT_AT,
      (
        CAST(CASE
          WHEN (
            86400
          ) = 0 OR (
            86400
          ) IS NULL
          THEN NULL
          ELSE CAST((
            (
              DATEDIFF(
                SECOND,
                (
                  TRY_TO_TIMESTAMP_NTZ("LAST_SUCCESS_LOGIN")
                ),
                MAX((
                  TRY_TO_TIMESTAMP_NTZ("LAST_SUCCESS_LOGIN")
                )) OVER (PARTITION BY (
                  NULL
                ))
              )
            )
          ) AS DECIMAL(38, 15)) / CAST((
            86400
          ) AS DECIMAL(38, 15))
        END AS DECIMAL(38, 15))
      ) AS DAYS_SINCE_LAST_LOGIN,
      (
        NULL
      ) AS ACCOUNT_NAME /* The account this snapshot came from (see the ACCOUNT_OF shim). */
    FROM {{ source('account_usage', 'USERS') }}
  ) AS base
  GROUP BY
    USER_NAME,
    COALESCE(USER_TYPE, 'UNKNOWN'),
    COALESCE(DEFAULT_ROLE, 'NONE'),
    CASE WHEN IS_DISABLED THEN 'DISABLED' ELSE 'ACTIVE' END,
    CASE
      WHEN HAS_RSA_PUBLIC_KEY AND HAS_PASSWORD
      THEN 'KEY_AND_PASSWORD'
      WHEN HAS_RSA_PUBLIC_KEY
      THEN 'KEY_PAIR'
      WHEN HAS_PASSWORD
      THEN 'PASSWORD_ONLY'
      ELSE 'FEDERATED_OR_NONE'
    END
)
SELECT
  product.*,
  CURRENT_TIMESTAMP() AS _LOADED_AT,
  'V_ACCESS_GOVERNANCE_USER' AS _SOURCE_VIEW,
  '{{ invocation_id }}' AS _BATCH_ID
FROM product
