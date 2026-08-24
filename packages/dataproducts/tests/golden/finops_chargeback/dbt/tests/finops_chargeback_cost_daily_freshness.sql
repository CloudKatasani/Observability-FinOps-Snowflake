-- V_FINOPS_CHARGEBACK_COST_DAILY: the contract guarantees the newest row is no more than
-- 180 minutes old — the documented latency of the
-- slowest source behind it (R7).
SELECT
  MAX(TIME_BUCKET) AS newest_row,
  TIMESTAMPDIFF(minute, MAX(TIME_BUCKET), CURRENT_TIMESTAMP()) AS age_minutes
FROM {{ ref('finops_chargeback_cost_daily') }}
HAVING TIMESTAMPDIFF(minute, MAX(TIME_BUCKET), CURRENT_TIMESTAMP()) > 180
