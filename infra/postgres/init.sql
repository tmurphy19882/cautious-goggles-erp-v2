-- infra/postgres/init.sql — run on first container start.
-- Creates the `erp` user and the `erp_db` database. The `POSTGRES_DB`
-- env var already creates erp_db owned by the default user, so this
-- file mostly just sets up roles and the optional `unleash` db.

-- The default `POSTGRES_USER` (set to `erp` in docker-compose) already
-- owns the db. Just grant the necessary perms.

GRANT ALL PRIVILEGES ON DATABASE erp_db TO erp;
ALTER USER erp CREATEDB;

-- Unleash uses a separate schema-less db.
CREATE DATABASE unleash;
GRANT ALL PRIVILEGES ON DATABASE unleash TO erp;
