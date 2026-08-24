-- V_ACCESS_GOVERNANCE_USER: the contract declares the grain (USER, USER_TYPE, DEFAULT_ROLE, ACCOUNT_STATUS, CREDENTIAL_TYPE).
-- A duplicate key means every downstream aggregate double-counts.
SELECT
  USER, USER_TYPE, DEFAULT_ROLE, ACCOUNT_STATUS, CREDENTIAL_TYPE,
  COUNT(*) AS rows_at_key
FROM {{ ref('access_governance_user') }}
GROUP BY USER, USER_TYPE, DEFAULT_ROLE, ACCOUNT_STATUS, CREDENTIAL_TYPE
HAVING COUNT(*) > 1
