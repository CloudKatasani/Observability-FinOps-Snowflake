-- V_ACCESS_GOVERNANCE_GRANT: the contract declares the grain (TIME_BUCKET, ROLE, GRANTEE, GRANTED_BY, GRANTEE_TYPE, PRIVILEGE_TIER).
-- A duplicate key means every downstream aggregate double-counts.
SELECT
  TIME_BUCKET, ROLE, GRANTEE, GRANTED_BY, GRANTEE_TYPE, PRIVILEGE_TIER,
  COUNT(*) AS rows_at_key
FROM {{ ref('access_governance_grant') }}
GROUP BY TIME_BUCKET, ROLE, GRANTEE, GRANTED_BY, GRANTEE_TYPE, PRIVILEGE_TIER
HAVING COUNT(*) > 1
