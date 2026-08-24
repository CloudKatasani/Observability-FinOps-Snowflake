-- V_PIPELINE_HEALTH_DYNAMIC_TABLE_REFRESH: the contract declares the grain (TIME_BUCKET, DYNAMIC_TABLE, DATABASE, TABLE_SCHEMA, SLA_STATUS).
-- A duplicate key means every downstream aggregate double-counts.
SELECT
  TIME_BUCKET, DYNAMIC_TABLE, DATABASE, TABLE_SCHEMA, SLA_STATUS,
  COUNT(*) AS rows_at_key
FROM {{ ref('pipeline_health_dynamic_table_refresh') }}
GROUP BY TIME_BUCKET, DYNAMIC_TABLE, DATABASE, TABLE_SCHEMA, SLA_STATUS
HAVING COUNT(*) > 1
