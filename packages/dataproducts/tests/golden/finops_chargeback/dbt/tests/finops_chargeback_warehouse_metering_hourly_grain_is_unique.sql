-- V_FINOPS_CHARGEBACK_WAREHOUSE_METERING_HOURLY: the contract declares the grain (TIME_BUCKET, WAREHOUSE).
-- A duplicate key means every downstream aggregate double-counts.
SELECT
  TIME_BUCKET, WAREHOUSE,
  COUNT(*) AS rows_at_key
FROM {{ ref('finops_chargeback_warehouse_metering_hourly') }}
GROUP BY TIME_BUCKET, WAREHOUSE
HAVING COUNT(*) > 1
