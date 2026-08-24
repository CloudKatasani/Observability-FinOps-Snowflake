-- V_ACCESS_GOVERNANCE_LOGIN: the contract guarantees the newest row is no more than
-- 120 minutes old — the documented latency of the
-- slowest source behind it (R7).
SELECT
  MAX(TIME_BUCKET) AS newest_row,
  TIMESTAMPDIFF(minute, MAX(TIME_BUCKET), CURRENT_TIMESTAMP()) AS age_minutes
FROM {{ ref('access_governance_login') }}
HAVING TIMESTAMPDIFF(minute, MAX(TIME_BUCKET), CURRENT_TIMESTAMP()) > 120
