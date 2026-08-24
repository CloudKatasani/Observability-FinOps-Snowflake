-- V_ACCESS_GOVERNANCE_LOGIN: the contract declares the grain (TIME_BUCKET, USER, CLIENT_TYPE, FIRST_FACTOR, ERROR_CLASS, CLIENT_IP).
-- A duplicate key means every downstream aggregate double-counts.
SELECT
  TIME_BUCKET, USER, CLIENT_TYPE, FIRST_FACTOR, ERROR_CLASS, CLIENT_IP,
  COUNT(*) AS rows_at_key
FROM {{ ref('access_governance_login') }}
GROUP BY TIME_BUCKET, USER, CLIENT_TYPE, FIRST_FACTOR, ERROR_CLASS, CLIENT_IP
HAVING COUNT(*) > 1
