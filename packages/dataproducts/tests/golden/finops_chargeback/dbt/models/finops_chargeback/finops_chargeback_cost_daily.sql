-- V_FINOPS_CHARGEBACK_COST_DAILY · Platform Cost & Attribution 1.1.0
-- Generated from the governed metrics chargeback.metered_credits, cost.billed_credits.
-- Edit the metric YAML, not this file: it is regenerated on every publish.
{{ config(materialized='table', on_schema_change='append_new_columns') }}

WITH product AS (
  SELECT
    (
      DATE_TRUNC('DAY', "USAGE_DAY")
    ) AS "TIME_BUCKET",
    SERVICE_TYPE AS "SERVICE_TYPE",
    SUM(CREDITS_USED) AS "CHARGEBACK_METERED_CREDITS",
    SUM(CREDITS_BILLED) AS "COST_BILLED_CREDITS"
  FROM (
    SELECT
      "SERVICE_TYPE" AS SERVICE_TYPE,
      CAST("USAGE_DATE" AS DATE) AS USAGE_DAY,
      (
        CAST("CREDITS_USED_COMPUTE" AS DECIMAL(38, 9))
      ) AS CREDITS_COMPUTE,
      (
        CAST("CREDITS_USED_CLOUD_SERVICES" AS DECIMAL(38, 9))
      ) AS CREDITS_CLOUD_SERVICES_RAW,
      (
        CAST("CREDITS_ADJUSTMENT_CLOUD_SERVICES" AS DECIMAL(38, 9))
      ) AS CREDITS_CLOUD_SERVICES_ADJUSTMENT,
      (
        CAST("CREDITS_USED_CLOUD_SERVICES" + "CREDITS_ADJUSTMENT_CLOUD_SERVICES" AS DECIMAL(38, 9))
      ) AS CREDITS_CLOUD_SERVICES_BILLED,
      (
        CAST("CREDITS_USED" AS DECIMAL(38, 9))
      ) AS CREDITS_USED,
      (
        CAST("CREDITS_BILLED" AS DECIMAL(38, 9))
      ) AS CREDITS_BILLED,
      (
        NULL
      ) AS ACCOUNT_NAME /* The account these rows came from (see the ACCOUNT_OF shim). */
    FROM {{ source('account_usage', 'METERING_DAILY_HISTORY') }}
  ) AS base
  GROUP BY
    (
      DATE_TRUNC('DAY', "USAGE_DAY")
    ),
    SERVICE_TYPE
)
SELECT
  product.*,
  CURRENT_TIMESTAMP() AS _LOADED_AT,
  'V_FINOPS_CHARGEBACK_COST_DAILY' AS _SOURCE_VIEW,
  '{{ invocation_id }}' AS _BATCH_ID
FROM product
