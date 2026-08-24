-- V_FINOPS_CHARGEBACK_COST_DAILY: the contract declares the grain (TIME_BUCKET, SERVICE_TYPE).
-- A duplicate key means every downstream aggregate double-counts.
SELECT
  TIME_BUCKET, SERVICE_TYPE,
  COUNT(*) AS rows_at_key
FROM {{ ref('finops_chargeback_cost_daily') }}
GROUP BY TIME_BUCKET, SERVICE_TYPE
HAVING COUNT(*) > 1
