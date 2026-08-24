-- V_ACCESS_GOVERNANCE_USER: the contract promises at least 1 row(s) per day.
-- An empty day is a pipeline failure, not a quiet zero (R3).
WITH daily AS (
  SELECT
    CURRENT_DATE() AS usage_day,
    COUNT(*) AS rows_per_day
  FROM {{ ref('access_governance_user') }}
  GROUP BY 1
)
SELECT usage_day, rows_per_day
FROM daily
WHERE rows_per_day < 1
