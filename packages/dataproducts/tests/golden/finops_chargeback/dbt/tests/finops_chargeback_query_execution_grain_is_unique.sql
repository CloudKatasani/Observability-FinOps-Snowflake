-- V_FINOPS_CHARGEBACK_QUERY_EXECUTION: the contract declares the grain (TIME_BUCKET, TEAM, WAREHOUSE, DATABASE, ALLOCATION_METHOD).
-- A duplicate key means every downstream aggregate double-counts.
SELECT
  TIME_BUCKET, TEAM, WAREHOUSE, DATABASE, ALLOCATION_METHOD,
  COUNT(*) AS rows_at_key
FROM {{ ref('finops_chargeback_query_execution') }}
GROUP BY TIME_BUCKET, TEAM, WAREHOUSE, DATABASE, ALLOCATION_METHOD
HAVING COUNT(*) > 1
