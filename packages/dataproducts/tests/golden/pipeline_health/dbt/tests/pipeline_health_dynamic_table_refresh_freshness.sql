-- V_PIPELINE_HEALTH_DYNAMIC_TABLE_REFRESH: the contract guarantees the newest row is no more than
-- 180 minutes old — the documented latency of the
-- slowest source behind it (R7).
SELECT
  MAX(TIME_BUCKET) AS newest_row,
  TIMESTAMPDIFF(minute, MAX(TIME_BUCKET), CURRENT_TIMESTAMP()) AS age_minutes
FROM {{ ref('pipeline_health_dynamic_table_refresh') }}
HAVING TIMESTAMPDIFF(minute, MAX(TIME_BUCKET), CURRENT_TIMESTAMP()) > 180
