-- V_PIPELINE_HEALTH_TASK_RUN: the contract declares the grain (TIME_BUCKET, TASK, GRAPH_ROOT, ERROR_CLASS, DATABASE, TASK_SCHEMA).
-- A duplicate key means every downstream aggregate double-counts.
SELECT
  TIME_BUCKET, TASK, GRAPH_ROOT, ERROR_CLASS, DATABASE, TASK_SCHEMA,
  COUNT(*) AS rows_at_key
FROM {{ ref('pipeline_health_task_run') }}
GROUP BY TIME_BUCKET, TASK, GRAPH_ROOT, ERROR_CLASS, DATABASE, TASK_SCHEMA
HAVING COUNT(*) > 1
