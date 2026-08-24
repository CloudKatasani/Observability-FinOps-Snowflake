-- V_FINOPS_CHARGEBACK_QUERY_EXECUTION: the contract guarantees the newest row is no more than
-- 480 minutes old — the documented latency of the
-- slowest source behind it (R7).
SELECT
  MAX(TIME_BUCKET) AS newest_row,
  TIMESTAMPDIFF(minute, MAX(TIME_BUCKET), CURRENT_TIMESTAMP()) AS age_minutes
FROM {{ ref('finops_chargeback_query_execution') }}
HAVING TIMESTAMPDIFF(minute, MAX(TIME_BUCKET), CURRENT_TIMESTAMP()) > 480
