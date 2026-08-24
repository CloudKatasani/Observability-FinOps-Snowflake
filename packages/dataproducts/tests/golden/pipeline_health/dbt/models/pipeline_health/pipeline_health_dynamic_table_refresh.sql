-- V_PIPELINE_HEALTH_DYNAMIC_TABLE_REFRESH · Pipeline Reliability 1.0.0
-- Generated from the governed metrics pipe.dt_lag_vs_target, pipe.dt_lag_breaches, pipe.dt_refresh_failures, dq.freshness_sla_attainment, dq.sla_breach_count.
-- Edit the metric YAML, not this file: it is regenerated on every publish.
{{ config(materialized='table', on_schema_change='append_new_columns') }}

WITH product AS (
  SELECT
    (
      DATE_TRUNC('DAY', "REFRESH_START_AT")
    ) AS "TIME_BUCKET",
    QUALIFIED_NAME AS "DYNAMIC_TABLE",
    DATABASE_NAME AS "DATABASE",
    SCHEMA_NAME AS "TABLE_SCHEMA",
    CASE
      WHEN TARGET_LAG_SEC IS NULL
      THEN 'NO_TARGET'
      WHEN ACTUAL_LAG_SEC > TARGET_LAG_SEC
      THEN 'BREACHED'
      ELSE 'WITHIN_TARGET'
    END AS "SLA_STATUS",
    (
      CAST(CASE
        WHEN (
          SUM(TARGET_LAG_SEC)
        ) = 0 OR (
          SUM(TARGET_LAG_SEC)
        ) IS NULL
        THEN NULL
        ELSE CAST((
          SUM(ACTUAL_LAG_SEC)
        ) AS DECIMAL(38, 15)) / CAST((
          SUM(TARGET_LAG_SEC)
        ) AS DECIMAL(38, 15))
      END AS DECIMAL(38, 15))
    ) AS "PIPE_DT_LAG_VS_TARGET",
    SUM(
      CASE
        WHEN NOT TARGET_LAG_SEC IS NULL AND ACTUAL_LAG_SEC > TARGET_LAG_SEC
        THEN 1
        ELSE 0
      END
    ) AS "PIPE_DT_LAG_BREACHES",
    SUM(CASE WHEN REFRESH_STATE <> 'SUCCEEDED' THEN 1 ELSE 0 END) AS "PIPE_DT_REFRESH_FAILURES",
    (
      CAST(CASE
        WHEN (
          SUM(CASE WHEN NOT TARGET_LAG_SEC IS NULL THEN 1 ELSE 0 END)
        ) = 0
        OR (
          SUM(CASE WHEN NOT TARGET_LAG_SEC IS NULL THEN 1 ELSE 0 END)
        ) IS NULL
        THEN NULL
        ELSE CAST((
          SUM(
            CASE
              WHEN NOT TARGET_LAG_SEC IS NULL AND ACTUAL_LAG_SEC <= TARGET_LAG_SEC
              THEN 1
              ELSE 0
            END
          )
        ) AS DECIMAL(38, 15)) / CAST((
          SUM(CASE WHEN NOT TARGET_LAG_SEC IS NULL THEN 1 ELSE 0 END)
        ) AS DECIMAL(38, 15))
      END AS DECIMAL(38, 15))
    ) AS "DQ_FRESHNESS_SLA_ATTAINMENT",
    SUM(
      CASE
        WHEN NOT TARGET_LAG_SEC IS NULL AND ACTUAL_LAG_SEC > TARGET_LAG_SEC
        THEN 1
        ELSE 0
      END
    ) AS "DQ_SLA_BREACH_COUNT"
  FROM (
    SELECT
      "QUALIFIED_NAME" AS QUALIFIED_NAME,
      "NAME" AS DYNAMIC_TABLE_NAME,
      "DATABASE_NAME" AS DATABASE_NAME,
      "SCHEMA_NAME" AS SCHEMA_NAME,
      "STATE" AS REFRESH_STATE,
      "STATE_MESSAGE" AS STATE_MESSAGE,
      "REFRESH_ACTION" AS REFRESH_ACTION,
      "REFRESH_TRIGGER" AS REFRESH_TRIGGER,
      "TARGET_LAG_SEC" AS TARGET_LAG_SEC,
      (
        TRY_TO_TIMESTAMP_NTZ("REFRESH_START_TIME")
      ) AS REFRESH_START_AT,
      (
        TRY_TO_TIMESTAMP_NTZ("REFRESH_END_TIME")
      ) AS REFRESH_END_AT,
      (
        TRY_TO_TIMESTAMP_NTZ("DATA_TIMESTAMP")
      ) AS DATA_TIMESTAMP_AT,
      (
        DATEDIFF(
          SECOND,
          (
            TRY_TO_TIMESTAMP_NTZ("DATA_TIMESTAMP")
          ),
          (
            TRY_TO_TIMESTAMP_NTZ("REFRESH_START_TIME")
          )
        )
      ) AS ACTUAL_LAG_SEC,
      (
        DATEDIFF(
          SECOND,
          (
            TRY_TO_TIMESTAMP_NTZ("REFRESH_START_TIME")
          ),
          (
            TRY_TO_TIMESTAMP_NTZ("REFRESH_END_TIME")
          )
        )
      ) AS REFRESH_DURATION_SEC,
      (
        NULL
      ) AS ACCOUNT_NAME /* The account these rows came from (see the ACCOUNT_OF shim). */
    FROM {{ source('account_usage', 'DYNAMIC_TABLE_REFRESH_HISTORY') }}
  ) AS base
  GROUP BY
    (
      DATE_TRUNC('DAY', "REFRESH_START_AT")
    ),
    QUALIFIED_NAME,
    DATABASE_NAME,
    SCHEMA_NAME,
    CASE
      WHEN TARGET_LAG_SEC IS NULL
      THEN 'NO_TARGET'
      WHEN ACTUAL_LAG_SEC > TARGET_LAG_SEC
      THEN 'BREACHED'
      ELSE 'WITHIN_TARGET'
    END
)
SELECT
  product.*,
  CURRENT_TIMESTAMP() AS _LOADED_AT,
  'V_PIPELINE_HEALTH_DYNAMIC_TABLE_REFRESH' AS _SOURCE_VIEW,
  '{{ invocation_id }}' AS _BATCH_ID
FROM product
