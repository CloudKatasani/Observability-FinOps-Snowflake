-- V_FINOPS_CHARGEBACK_WAREHOUSE_METERING_HOURLY · Platform Cost & Attribution 1.1.0
-- Generated from the governed metrics chargeback.reconciliation_variance.
-- Edit the metric YAML, not this file: it is regenerated on every publish.
{{ config(materialized='table', on_schema_change='append_new_columns') }}

WITH product AS (
  SELECT
    (
      DATE_TRUNC('HOUR', "METERING_HOUR")
    ) AS "TIME_BUCKET",
    WAREHOUSE_NAME AS "WAREHOUSE",
    SUM(CREDITS_COMPUTE) - SUM(CREDITS_ATTRIBUTED) AS "CHARGEBACK_RECONCILIATION_VARIANCE"
  FROM (
    SELECT
      m."WAREHOUSE_NAME" AS WAREHOUSE_NAME,
      m."WAREHOUSE_ID" AS WAREHOUSE_ID,
      (
        DATE_TRUNC('HOUR', (
          TRY_TO_TIMESTAMP_NTZ(m."START_TIME")
        ))
      ) AS METERING_HOUR,
      (
        CAST(m."CREDITS_USED_COMPUTE" AS DECIMAL(38, 9))
      ) AS CREDITS_COMPUTE,
      (
        CAST(m."CREDITS_USED_CLOUD_SERVICES" AS DECIMAL(38, 9))
      ) AS CREDITS_CLOUD_SERVICES,
      (
        CAST(COALESCE(a.CREDITS_ATTRIBUTED, 0) AS DECIMAL(38, 9))
      ) AS CREDITS_ATTRIBUTED,
      (
        CAST(m."CREDITS_USED_COMPUTE" - COALESCE(a.CREDITS_ATTRIBUTED, 0) AS DECIMAL(38, 9))
      ) AS CREDITS_IDLE,
      r.WAREHOUSE_RANK AS WAREHOUSE_RANK /* Whole-window rank by total compute, so concentration metrics can ask for */ /* "the top N warehouses" without a correlated subquery per row. */
    FROM {{ source('account_usage', 'WAREHOUSE_METERING_HISTORY') }} AS m
    LEFT JOIN (
      SELECT
        "WAREHOUSE_NAME" AS WAREHOUSE_NAME,
        (
          DATE_TRUNC('HOUR', (
            TRY_TO_TIMESTAMP_NTZ("START_TIME")
          ))
        ) AS ATTRIBUTION_HOUR,
        SUM((
          CAST("CREDITS_ATTRIBUTED_COMPUTE" AS DECIMAL(38, 9))
        )) AS CREDITS_ATTRIBUTED
      FROM {{ source('account_usage', 'QUERY_ATTRIBUTION_HISTORY') }}
      GROUP BY
        1,
        2
    ) AS a
      ON a.WAREHOUSE_NAME = m."WAREHOUSE_NAME"
      AND a.ATTRIBUTION_HOUR = (
        DATE_TRUNC('HOUR', (
          TRY_TO_TIMESTAMP_NTZ(m."START_TIME")
        ))
      )
    LEFT JOIN (
      SELECT
        "WAREHOUSE_NAME" AS WAREHOUSE_NAME,
        DENSE_RANK() OVER (ORDER BY SUM("CREDITS_USED_COMPUTE") DESC NULLS LAST) AS WAREHOUSE_RANK
      FROM {{ source('account_usage', 'WAREHOUSE_METERING_HISTORY') }}
      GROUP BY
        1
    ) AS r
      ON r.WAREHOUSE_NAME = m."WAREHOUSE_NAME"
  ) AS base
  GROUP BY
    (
      DATE_TRUNC('HOUR', "METERING_HOUR")
    ),
    WAREHOUSE_NAME
)
SELECT
  product.*,
  CURRENT_TIMESTAMP() AS _LOADED_AT,
  'V_FINOPS_CHARGEBACK_WAREHOUSE_METERING_HOURLY' AS _SOURCE_VIEW,
  '{{ invocation_id }}' AS _BATCH_ID
FROM product
