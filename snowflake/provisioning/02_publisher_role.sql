-- Observability & FinOps Platform — data-product PUBLISHER role
-- Generated 2026-08-24T04:31:47+00:00
--
-- This role can create objects in ONE database, and nothing else. It is
-- separate from the read-only role the platform connects with day to day,
-- and every statement it executes is shown to a human for approval first.

USE ROLE USERADMIN;
CREATE ROLE IF NOT EXISTS SNOWOBS_PUBLISHER
  COMMENT = 'Publishes observability data products. Write scope: OBSERVABILITY only.';

USE ROLE SYSADMIN;
CREATE DATABASE IF NOT EXISTS OBSERVABILITY
  COMMENT = 'Published observability data products';
CREATE SCHEMA IF NOT EXISTS OBSERVABILITY.PUBLISHED;
CREATE SCHEMA IF NOT EXISTS OBSERVABILITY.SEMANTIC;

GRANT USAGE ON DATABASE OBSERVABILITY TO ROLE SNOWOBS_PUBLISHER;
GRANT ALL ON SCHEMA OBSERVABILITY.PUBLISHED TO ROLE SNOWOBS_PUBLISHER;
GRANT ALL ON SCHEMA OBSERVABILITY.SEMANTIC TO ROLE SNOWOBS_PUBLISHER;
GRANT USAGE ON WAREHOUSE WH_SNOWOBS_APP TO ROLE SNOWOBS_PUBLISHER;

-- Publication also needs the reader role's SELECT access to build the
-- published views from the usage data.
GRANT ROLE SNOWOBS_READER TO ROLE SNOWOBS_PUBLISHER;

SHOW GRANTS TO ROLE SNOWOBS_PUBLISHER;
