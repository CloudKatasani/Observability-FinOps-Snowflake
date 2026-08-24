-- V_PIPELINE_HEALTH_TASK_RUN · Pipeline Reliability 1.0.0
-- Generated from the governed metrics pipe.task_success_rate, pipe.task_failures, pipe.root_failures, pipe.skipped_downstream, pipe.task_duration_p95.
-- Edit the metric YAML, not this file: it is regenerated on every publish.
{{ config(materialized='table', on_schema_change='append_new_columns') }}

WITH product AS (
  SELECT
    (
      DATE_TRUNC('DAY', "SCHEDULED_AT")
    ) AS "TIME_BUCKET",
    TASK_NAME AS "TASK",
    COALESCE(GRAPH_ROOT_TASK_ID, TASK_NAME) AS "GRAPH_ROOT",
    COALESCE(ERROR_CODE, 'NONE') AS "ERROR_CLASS",
    DATABASE_NAME AS "DATABASE",
    SCHEMA_NAME AS "TASK_SCHEMA",
    (
      CAST(CASE
        WHEN (
          COUNT(*)
        ) = 0 OR (
          COUNT(*)
        ) IS NULL
        THEN NULL
        ELSE CAST((
          SUM(CASE WHEN STATE = 'SUCCEEDED' THEN 1 ELSE 0 END)
        ) AS DECIMAL(38, 15)) / CAST((
          COUNT(*)
        ) AS DECIMAL(38, 15))
      END AS DECIMAL(38, 15))
    ) AS "PIPE_TASK_SUCCESS_RATE",
    SUM(CASE WHEN STATE = 'FAILED' THEN 1 ELSE 0 END) AS "PIPE_TASK_FAILURES",
    SUM(CASE WHEN STATE = 'FAILED' AND TASK_NAME = GRAPH_ROOT_TASK_ID THEN 1 ELSE 0 END) AS "PIPE_ROOT_FAILURES",
    SUM(CASE WHEN STATE = 'SKIPPED' THEN 1 ELSE 0 END) AS "PIPE_SKIPPED_DOWNSTREAM",
    MAX(CASE WHEN DURATION_PERCENT_RANK <= 0.95 THEN DURATION_SEC END) AS "PIPE_TASK_DURATION_P95"
  FROM (
    SELECT
      "NAME" AS TASK_NAME,
      "DATABASE_NAME" AS DATABASE_NAME,
      "SCHEMA_NAME" AS SCHEMA_NAME,
      "STATE" AS STATE,
      "ERROR_CODE" AS ERROR_CODE,
      "QUERY_ID" AS QUERY_ID,
      "GRAPH_ROOT_TASK_ID" AS GRAPH_ROOT_TASK_ID,
      "GRAPH_RUN_GROUP_ID" AS GRAPH_RUN_GROUP_ID,
      "RUN_ID" AS RUN_ID,
      (
        TRY_TO_TIMESTAMP_NTZ("SCHEDULED_TIME")
      ) AS SCHEDULED_AT,
      (
        TRY_TO_TIMESTAMP_NTZ("QUERY_START_TIME")
      ) AS STARTED_AT,
      (
        TRY_TO_TIMESTAMP_NTZ("COMPLETED_TIME")
      ) AS COMPLETED_AT,
      (
        DATEDIFF(
          SECOND,
          (
            TRY_TO_TIMESTAMP_NTZ("SCHEDULED_TIME")
          ),
          (
            TRY_TO_TIMESTAMP_NTZ("COMPLETED_TIME")
          )
        )
      ) AS DURATION_SEC,
      PERCENT_RANK() OVER (
        PARTITION BY "NAME", (
          NULL
        )
        ORDER BY (
          DATEDIFF(
            SECOND,
            (
              TRY_TO_TIMESTAMP_NTZ("SCHEDULED_TIME")
            ),
            (
              TRY_TO_TIMESTAMP_NTZ("COMPLETED_TIME")
            )
          )
        )
      ) AS DURATION_PERCENT_RANK,
      SUM(CASE WHEN "STATE" = 'FAILED' THEN 1 ELSE 0 END) OVER (PARTITION BY "NAME", (
        NULL
      )) AS TASK_FAILURE_COUNT,
      MAX(
        CASE
          WHEN "STATE" = 'SUCCEEDED'
          THEN (
            TRY_TO_TIMESTAMP_NTZ("COMPLETED_TIME")
          )
        END
      ) OVER (PARTITION BY "NAME", (
        NULL
      )) AS LAST_SUCCESS_AT,
      MAX((
        TRY_TO_TIMESTAMP_NTZ("SCHEDULED_TIME")
      )) OVER (PARTITION BY (
        NULL
      )) AS OBSERVED_THROUGH_AT,
      (
        DATEDIFF(
          SECOND,
          MAX(
            CASE
              WHEN "STATE" = 'SUCCEEDED'
              THEN (
                TRY_TO_TIMESTAMP_NTZ("COMPLETED_TIME")
              )
            END
          ) OVER (PARTITION BY "NAME", (
            NULL
          )),
          MAX((
            TRY_TO_TIMESTAMP_NTZ("SCHEDULED_TIME")
          )) OVER (PARTITION BY (
            NULL
          ))
        )
      ) AS SECONDS_SINCE_LAST_SUCCESS,
      (
        NULL
      ) AS ACCOUNT_NAME /* The account these rows came from (see the ACCOUNT_OF shim). */
    FROM {{ source('account_usage', 'TASK_HISTORY') }}
  ) AS base
  GROUP BY
    (
      DATE_TRUNC('DAY', "SCHEDULED_AT")
    ),
    TASK_NAME,
    COALESCE(GRAPH_ROOT_TASK_ID, TASK_NAME),
    COALESCE(ERROR_CODE, 'NONE'),
    DATABASE_NAME,
    SCHEMA_NAME
)
SELECT
  product.*,
  CURRENT_TIMESTAMP() AS _LOADED_AT,
  'V_PIPELINE_HEALTH_TASK_RUN' AS _SOURCE_VIEW,
  '{{ invocation_id }}' AS _BATCH_ID
FROM product
