-- V_PIPELINE_HEALTH_DYNAMIC_TABLE_REFRESH: the contract promises at least 1 row(s) per day.
-- An empty day is a pipeline failure, not a quiet zero (R3).
WITH daily AS (
  SELECT
    CAST(TIME_BUCKET AS DATE) AS usage_day,
    COUNT(*) AS rows_per_day
  FROM {{ ref('pipeline_health_dynamic_table_refresh') }}
  GROUP BY 1
)
SELECT usage_day, rows_per_day
FROM daily
WHERE rows_per_day < 1
